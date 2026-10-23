"""Sequence reading and validation for BeastXPy.

Reads FASTA / NEXUS alignments (via Biopython), validates alphabet and
equal-length properties, and produces :class:`Alignment` objects with
appropriate BEAST 1 data types (``nucleotide`` / ``amino acid``).

Gate-0 concerns handled here:

* files are decoded as UTF-8 (BOM tolerated), never with the host locale, so
  taxon names match the UTF-8 YAML on Windows;
* NEXUS match characters (``.``) are expanded against the first sequence
  before Biopython parses the matrix;
* auto data-type detection reports ambiguity instead of silently typing
  ACGT-only peptide data as nucleotide;
* zero-length and single-sequence alignments are rejected;
* tip dates are parsed and validated exactly once by
  :func:`normalize_tip_dates`, which :func:`parse_tip_dates` also uses
.
"""

from __future__ import annotations

import io
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    DATE_DIRECTIONS,
    DATE_UNITS,
    DATE_UNITS_1_10_4,
    DEFAULT_DATE_DIRECTION,
    DEFAULT_DATE_UNITS,
    Alignment,
    DataType,
    Sequence,
    Taxon,
)

NUCLEOTIDE_CHARS = frozenset("ACGTURYSWKMBDHVN-?Xacgturyswkmbdhvn")
AMINOACID_CHARS = frozenset("ACDEFGHIKLMNPQRSTVWYBXZJUO*-?acdefghiklmnpqrstvwybxzjuo")
_PURE_NUCLEOTIDE_CHARS = frozenset("ACGTU")
_GAP_CHARS = frozenset("-?.")


class AlignmentError(ValueError):
    """Raised when an alignment cannot be read or validated."""


# ---------------------------------------------------------------------------
# Text I/O
# ---------------------------------------------------------------------------


def read_text(path: Path) -> str:
    """Read ``path`` as UTF-8 (BOM tolerated) with an actionable error.

    BeastXPy never falls back to the platform locale codec: on a GBK Windows
    console that silently produces mojibake taxon names which then fail to
    match calibration/tip-date entries.
    """
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AlignmentError(
            f"{path} is not valid UTF-8 (byte offset {exc.start}); BeastXPy reads "
            "alignment files as UTF-8. Re-save the file as UTF-8 (for example "
            "with `iconv -f GBK -t UTF-8`) or remove non-ASCII taxon names."
        ) from exc
    if "\ufffd" in text:
        raise AlignmentError(
            f"{path} contains U+FFFD replacement characters; the file is not "
            "clean UTF-8 text (possible mojibake). Re-save it as UTF-8."
        )
    return text


# ---------------------------------------------------------------------------
# NEXUS match character '.'
# ---------------------------------------------------------------------------

_NEXUS_COMMENT_RE = re.compile(r"\[[^\]]*\]")
_NEXUS_TOKEN_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"|(\S+)")
_NEXUS_MATRIX_RE = re.compile(r"(?is)\bmatrix\b(.*?);")
_NEXUS_INTERLEAVE_RE = re.compile(r"(?i)\binterleave\s*=\s*(yes|true)")


def _nexus_tokens(line: str) -> List[str]:
    tokens = []
    for quoted, quoted2, plain in _NEXUS_TOKEN_RE.findall(line):
        tokens.append(quoted if quoted else (quoted2 if quoted2 else plain))
    return tokens


def _quote_nexus_label(name: str) -> str:
    if re.search(r"[\s'\"]", name):
        return "'" + name.replace("'", "''") + "'"
    return name


def expand_nexus_match_chars(text: str, path: Path) -> str:
    """Expand NEXUS ``.`` (same state as the first sequence) in-place.

    Returns ``text`` unchanged when the matrix contains no match character.
    The rewritten matrix is non-interleaved, one sequence per line.
    """
    match = _NEXUS_MATRIX_RE.search(text)
    if match is None:
        return text
    body = _NEXUS_COMMENT_RE.sub("", match.group(1))

    entries: List[Tuple[str, str]] = []
    current: Optional[str] = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        tokens = _nexus_tokens(line)
        if not tokens:
            continue
        if len(tokens) == 1:
            if current is None:
                raise AlignmentError(
                    f"{path}: NEXUS matrix line {line!r} has no taxon name and "
                    "no preceding taxon"
                )
            entries.append((current, tokens[0]))
        else:
            name = tokens[0]
            entries.append((name, "".join(tokens[1:])))
            current = name
    if not entries:
        return text

    order: List[str] = []
    chunks: Dict[str, List[str]] = {}
    for name, chunk in entries:
        if name not in chunks:
            chunks[name] = []
            order.append(name)
        chunks[name].append(chunk)
    joined = {name: "".join(parts).upper() for name, parts in chunks.items()}
    if "." not in "".join(joined.values()):
        return text

    lengths = {len(seq) for seq in joined.values()}
    if len(lengths) > 1:
        raise AlignmentError(
            f"{path}: NEXUS matrix with match characters is ragged "
            f"(lengths {sorted(lengths)}); cannot resolve '.'"
        )
    first_name = order[0]
    first = joined[first_name]
    if "." in first:
        raise AlignmentError(
            f"{path}: the first NEXUS sequence {first_name!r} contains the match "
            "character '.'; there is no reference state to copy"
        )
    expanded: Dict[str, str] = {first_name: first}
    for name in order[1:]:
        chars = list(joined[name])
        for index, char in enumerate(chars):
            if char == ".":
                chars[index] = first[index]
        expanded[name] = "".join(chars)

    prefix = text[: match.start(1)]
    # The rebuilt matrix is not interleaved; make the FORMAT statement agree.
    prefix = _NEXUS_INTERLEAVE_RE.sub("interleave=no", prefix)
    new_body = "\n" + "".join(
        f"    {_quote_nexus_label(name)} {expanded[name]}\n" for name in order
    )
    return prefix + new_body + text[match.end(1) :]


# ---------------------------------------------------------------------------
# Data-type detection
# ---------------------------------------------------------------------------


def detect_data_type(observed: set) -> Tuple[DataType, bool]:
    """Deterministically resolve an auto-detected data type.

    Returns ``(type, ambiguous)``.  The rule is conservative:

    * residues outside the nucleotide alphabet -> amino acid;
    * residues inside ``{A,C,G,T,U}`` only -> nucleotide but *ambiguous*
      (a peptide alignment over ACGT is indistinguishable; Gate 0 warns);
    * IUPAC ambiguity codes that are also amino-acid letters -> nucleotide
      with an ambiguity warning;
    * characters outside both alphabets -> AlignmentError.
    """
    residues = {char.upper() for char in observed if char not in _GAP_CHARS}
    if not residues:
        # All-gap alignment: no evidence at all; treat as ambiguous nucleotide
        # unless the caller declares a type.
        return DataType.NUCLEOTIDE, True
    if residues <= _PURE_NUCLEOTIDE_CHARS:
        return DataType.NUCLEOTIDE, True
    if residues <= NUCLEOTIDE_CHARS and residues <= AMINOACID_CHARS:
        return DataType.NUCLEOTIDE, True
    if residues <= AMINOACID_CHARS:
        return DataType.AMINOACID, False
    illegal = sorted(residues - NUCLEOTIDE_CHARS - AMINOACID_CHARS)
    raise AlignmentError(
        f"sequences contain character(s) that belong to neither the nucleotide "
        f"nor the amino-acid alphabet: {illegal}"
    )


def data_type_allowed_chars(data_type: DataType) -> frozenset:
    """Return the permitted sequence characters for ``data_type``."""
    return NUCLEOTIDE_CHARS if data_type is DataType.NUCLEOTIDE else AMINOACID_CHARS


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class SequenceReader:
    """Read FASTA/NEXUS files into validated Alignment objects."""

    @classmethod
    def read(
        cls,
        path: str,
        format: str = "auto",
        data_type: str = "auto",
        alignment_id: Optional[str] = None,
    ) -> Alignment:
        """Read an alignment file.

        Args:
            path: Path to the sequence file.
            format: ``auto`` (infer from extension), ``fasta`` or ``nexus``.
            data_type: ``auto`` or any spelling accepted by
                :meth:`DataType.from_config`.  A declared type that
                contradicts the observed characters is an error.
            alignment_id: Identifier for the Alignment (defaults to the file
                stem).

        Returns:
            A validated Alignment.

        Raises:
            FileNotFoundError: The file does not exist.
            AlignmentError: The file cannot be parsed or validated.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Alignment file not found: {path}")

        fmt = format
        if fmt == "auto":
            suffix = file_path.suffix.lower()
            if suffix in (".fa", ".fasta", ".fas", ".fna"):
                fmt = "fasta"
            elif suffix in (".nex", ".nexus"):
                fmt = "nexus"
            else:
                fmt = "fasta"
        if fmt not in ("fasta", "nexus"):
            raise AlignmentError(
                f"Unknown alignment format {format!r} for {path}; "
                "use 'auto', 'fasta' or 'nexus'."
            )

        text = read_text(file_path)
        try:
            if fmt == "nexus":
                text = expand_nexus_match_chars(text, file_path)
                records = cls._read_nexus(text, file_path)
            else:
                records = cls._read_fasta(text, file_path)
        except AlignmentError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise AlignmentError(f"Could not read {path}: {exc}") from exc

        if not records:
            raise AlignmentError(f"No sequences found in {path}")

        alignment_id = alignment_id or file_path.stem
        observed = set("".join(seq for _, seq in records))
        declared = str(data_type).strip().lower() if data_type is not None else "auto"
        if declared == "auto":
            try:
                resolved_type, ambiguous = detect_data_type(observed)
            except AlignmentError as exc:
                raise AlignmentError(f"{path}: {exc}") from exc
            source = "auto"
        else:
            try:
                resolved_type = DataType.from_config(data_type)
            except ValueError as exc:
                raise AlignmentError(f"{path}: {exc}") from exc
            ambiguous = False
            source = "declared"

        allowed = data_type_allowed_chars(resolved_type)
        illegal = sorted(
            char
            for char in {c.upper() for c in observed}
            if char not in allowed and char not in _GAP_CHARS
        )
        if illegal:
            raise AlignmentError(
                f"Alignment '{path}' declares data_type '{resolved_type.value}' but "
                f"contains character(s) not in the {resolved_type.value} alphabet: "
                f"{illegal}"
            )

        sequences = [Sequence(taxon=name, sequence=seq) for name, seq in records]

        lengths = {len(s.sequence) for s in sequences}
        if len(lengths) > 1:
            raise AlignmentError(
                f"Alignment '{path}' is ragged: sequence lengths are {sorted(lengths)}."
            )
        if not sequences:
            raise AlignmentError(f"No sequences found in {path}")
        if len(sequences) < 2:
            raise AlignmentError(
                f"Alignment '{path}' has a single sequence; at least two taxa are "
                "required to define a BEAST tree prior"
            )
        if lengths == {0}:
            raise AlignmentError(
                f"Alignment '{path}' has zero sites; BEAST cannot build a "
                "treeLikelihood from an empty alignment"
            )

        return Alignment(
            id=alignment_id,
            data_type=resolved_type,
            sequences=sequences,
            source_file=str(file_path),
            data_type_source=source,
            data_type_ambiguous=ambiguous,
        )

    # -- Format-specific readers ---------------------------------------------
    @staticmethod
    def _read_fasta(text: str, path: Path) -> List[tuple]:
        """Read FASTA text into a list of (name, sequence) pairs."""
        from Bio import SeqIO

        records: List[tuple] = []
        for record in SeqIO.parse(io.StringIO(text), "fasta"):
            records.append((str(record.id), str(record.seq).upper()))
        return records

    @staticmethod
    def _read_nexus(text: str, path: Path) -> List[tuple]:
        """Read NEXUS text into a list of (name, sequence) pairs."""
        from Bio import SeqIO

        records: List[tuple] = []
        try:
            for record in SeqIO.parse(io.StringIO(text), "nexus"):
                records.append((str(record.id), str(record.seq).upper()))
        except AlignmentError:
            raise
        except Exception as exc:
            raise AlignmentError(
                f"Could not parse NEXUS file {path}: {exc}. Check the DIMENSIONS "
                "and MATRIX blocks; BeastXPy expands the NEXUS match character "
                "'.' but not every dialect."
            ) from exc
        return records


def validate_alignment(alignment: Alignment) -> List[str]:
    """Run extra validation over an Alignment; return a list of findings.

    Checks taxon-name uniqueness, zero-length/single-sequence alignments,
    alphabet legality, XML-illegal characters and ragged sequences.
    """
    findings: List[str] = []
    names = alignment.taxa_names
    if len(names) < 2:
        findings.append(
            f"Alignment '{alignment.id}' has {len(names)} sequence(s); at least "
            "two taxa are required"
        )
    if any(not name for name in names):
        findings.append(f"Alignment '{alignment.id}' contains an empty taxon name")
    if len(set(names)) != len(names):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        findings.append(f"Alignment '{alignment.id}' has duplicate taxon names: {duplicates}")

    lengths = set(alignment.site_lengths)
    if len(lengths) > 1:
        findings.append(
            f"Alignment '{alignment.id}' is ragged: sequence lengths are {sorted(lengths)}"
        )
    elif lengths == {0}:
        findings.append(f"Alignment '{alignment.id}' has zero sites")

    allowed = data_type_allowed_chars(alignment.data_type)
    illegal = sorted(
        {
            char
            for seq in alignment.sequences
            for char in seq.sequence.upper()
            if char not in allowed and char not in _GAP_CHARS
        }
    )
    if illegal:
        findings.append(
            f"Alignment '{alignment.id}' contains character(s) outside the "
            f"{alignment.data_type.value} alphabet: {illegal}"
        )

    from .utils import valid_xml_charset_check

    for name in names:
        problem = valid_xml_charset_check(name)
        if problem:
            findings.append(problem)
    return findings


# ---------------------------------------------------------------------------
# Tip dates
# ---------------------------------------------------------------------------


def allowed_date_units(beast_version: Optional[str]) -> frozenset:
    """Return the tip-date units accepted for ``beast_version``.

    ``weeks`` is not accepted by either BEAST version: 1.10.4 does not know
    the token, and BEAST X 10.5.0 recognises it in ``XMLUnits`` but its
    ``TimeScale.unitString`` throws ``IllegalArgumentException`` while
    ``TaxaParser`` prints the parsed dates, so any document using it fails to
    instantiate.  ``both`` must also stay inside the 1.10.4 intersection.
    """
    return DATE_UNITS_1_10_4


def normalize_tip_dates(
    raw: Any,
    known_taxa: Optional[set] = None,
    beast_version: Optional[str] = "10.5.0",
) -> Dict[str, Dict[str, Any]]:
    """Parse and validate a tip-date mapping exactly once.

    Returns ``{taxon: {"value": float, "direction": str, "units": str}}``.
    Units are always present in the result (never dropped) and every value is
    validated.  Raises :class:`AlignmentError` on any problem so the
    API and Gate 0 share one implementation.
    """
    from .utils import parse_number

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AlignmentError("tip_dates must be a mapping of taxon -> date")
    units_allowed = allowed_date_units(beast_version)
    result: Dict[str, Dict[str, Any]] = {}
    for taxon, entry in raw.items():
        if not isinstance(taxon, str) or not taxon:
            raise AlignmentError(f"tip_dates: taxon name must be a non-empty string, got {taxon!r}")
        if known_taxa is not None and taxon not in known_taxa:
            raise AlignmentError(f"tip_dates: unknown taxon {taxon!r}")
        if isinstance(entry, dict):
            unknown = set(entry) - {"value", "direction", "units"}
            if unknown:
                raise AlignmentError(
                    f"tip_dates.{taxon}: unknown key(s): {sorted(unknown)} "
                    "(valid: value, direction, units)"
                )
            value = entry.get("value")
            direction = entry.get("direction", DEFAULT_DATE_DIRECTION)
            units = entry.get("units", DEFAULT_DATE_UNITS)
        elif isinstance(entry, (int, float)) and not isinstance(entry, bool):
            value = entry
            direction = DEFAULT_DATE_DIRECTION
            units = DEFAULT_DATE_UNITS
        else:
            raise AlignmentError(
                f"tip_dates.{taxon}: must be a number or a mapping with 'value' " f"(got {entry!r})"
            )
        if value is None:
            raise AlignmentError(f"tip_dates.{taxon}: 'value' is required")
        try:
            numeric = parse_number(value, f"tip_dates.{taxon}.value")
        except ValueError as exc:
            raise AlignmentError(str(exc)) from exc
        if not math.isfinite(numeric):
            raise AlignmentError(f"tip_dates.{taxon}.value must be finite")
        if not isinstance(direction, str) or direction not in DATE_DIRECTIONS:
            raise AlignmentError(
                f"tip_dates.{taxon}.direction: invalid {direction!r} "
                f"(valid: {', '.join(sorted(DATE_DIRECTIONS))})"
            )
        if not isinstance(units, str) or units not in DATE_UNITS:
            raise AlignmentError(
                f"tip_dates.{taxon}.units: unknown unit {units!r} "
                f"(valid: {', '.join(sorted(DATE_UNITS))})"
            )
        if units not in units_allowed:
            if units == "weeks":
                raise AlignmentError(
                    f"tip_dates.{taxon}.units: 'weeks' cannot be used: BEAST X "
                    "10.5.0 accepts the token but TimeScale.unitString() throws "
                    "IllegalArgumentException while parsing <taxa>, and BEAST "
                    "1.10.4 does not know it.  Express the date in days "
                    "(1 week = 7 days) instead."
                )
            raise AlignmentError(
                f"tip_dates.{taxon}.units: {units!r} is not supported by "
                f"BEAST {beast_version or '1.10.4'} (use one of: "
                f"{', '.join(sorted(units_allowed))})"
            )
        result[taxon] = {
            "value": numeric,
            "direction": direction,
            "units": units,
        }
    return result


def parse_tip_dates(alignment: Alignment, dates: dict) -> List[Taxon]:
    """Map taxon names to dated Taxon objects.

    This is now a thin builder over :func:`normalize_tip_dates`, so the YAML
    path and the API path cannot diverge.
    """
    normalized = normalize_tip_dates(dates, known_taxa=None)
    taxa: List[Taxon] = []
    for seq in alignment.sequences:
        entry = normalized.get(seq.taxon)
        if entry is None:
            taxa.append(Taxon(id=seq.taxon))
        else:
            taxa.append(
                Taxon(
                    id=seq.taxon,
                    date=float(entry["value"]),
                    date_direction=entry["direction"],
                    date_units=entry["units"],
                )
            )
    return taxa


# ---------------------------------------------------------------------------
# Read-only data-check inspectors (F2 ``check-data``; append-only additions)
# ---------------------------------------------------------------------------
#
# The functions below never raise for bad *data*: they report what they see so
# ``beastxpy.datacheck`` can turn each observation into a finding with a
# location and a fix.  They complement (never replace) the Gate-0 validation
# above: Gate 0 rejects a broken alignment outright, while these inspectors
# describe every problem per taxon/site.

#: Spellings of a declared ``data_type`` that mean codon / protein-coding data.
#: BEAST 1 XML cannot express a codon data type (Gate 0 rejects it), but
#: ``check-data`` still inspects such an alignment for codon-level problems.
CODON_DATA_TYPE_SPELLINGS = frozenset(
    {"codon", "codons", "codon data", "protein-coding", "protein_coding", "cds"}
)

#: Characters that count as fully specified (non-ambiguous, non-gap) states.
DEFINITE_NUCLEOTIDE_CHARS = frozenset("ACGTU")
DEFINITE_AMINOACID_CHARS = frozenset("ACDEFGHIKLMNPQRSTVWYUO*")


def is_codon_data_type(value: Any) -> bool:
    """Whether a declared ``data_type`` value denotes codon/protein-coding data."""
    if not isinstance(value, str):
        return False
    return " ".join(value.strip().lower().split()) in CODON_DATA_TYPE_SPELLINGS


def definite_chars(data_type: DataType) -> frozenset:
    """Characters counted as definite states for ``data_type``.

    Anything else (gaps ``-``, unknowns ``?``/``N``/``X``, IUPAC ambiguity
    codes) is treated as ambiguous by :func:`ambiguity_fractions`.
    """
    return (
        DEFINITE_NUCLEOTIDE_CHARS if data_type is DataType.NUCLEOTIDE else DEFINITE_AMINOACID_CHARS
    )


def duplicate_taxa(alignment: Alignment) -> List[str]:
    """Sorted taxon names occurring more than once in ``alignment``."""
    counts: Dict[str, int] = {}
    for seq in alignment.sequences:
        counts[seq.taxon] = counts.get(seq.taxon, 0) + 1
    return sorted(name for name, count in counts.items() if count > 1)


def empty_sequence_taxa(alignment: Alignment) -> List[str]:
    """Taxon names whose sequence is zero-length or whitespace-only (file order)."""
    return [seq.taxon for seq in alignment.sequences if not str(seq.sequence or "").strip()]


def duplicate_sequence_groups(alignment: Alignment) -> List[List[str]]:
    """Groups of taxa sharing an identical, non-empty sequence.

    Empty/whitespace-only sequences are excluded (they are reported by
    :func:`empty_sequence_taxa` instead).  Groups come in first-occurrence
    order; taxa inside a group keep file order.  Only groups with 2+ taxa.
    """
    groups: Dict[str, List[str]] = {}
    order: List[str] = []
    for seq in alignment.sequences:
        text = str(seq.sequence or "").strip()
        if not text:
            continue
        key = text.upper()
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(seq.taxon)
    return [groups[key] for key in order if len(groups[key]) > 1]


def illegal_characters_by_taxon(alignment: Alignment) -> Dict[str, Dict[str, Any]]:
    """Map taxon -> ``{"chars": [...], "sites": [...]}`` for illegal characters.

    A character is illegal when it is outside the alignment's declared data-type
    alphabet and is not a gap character (``-?.``); comparison is
    case-insensitive.  ``sites`` lists the first five 1-based positions.
    """
    allowed = data_type_allowed_chars(alignment.data_type)
    result: Dict[str, Dict[str, Any]] = {}
    for seq in alignment.sequences:
        text = str(seq.sequence or "")
        chars = set()
        sites: List[int] = []
        for index, char in enumerate(text):
            if char.upper() not in allowed and char not in _GAP_CHARS:
                chars.add(char)
                if len(sites) < 5:
                    sites.append(index + 1)
        if chars:
            result[seq.taxon] = {"chars": sorted(chars), "sites": sites}
    return result


def ambiguity_fractions(alignment: Alignment) -> Dict[str, float]:
    """Per-taxon fraction of sites that are not definite states.

    A site counts as ambiguous when its character is not in
    :func:`definite_chars` for the alignment's data type (this includes gaps,
    ``N``/``?``/``X`` and IUPAC ambiguity codes).  The denominator is the
    taxon's own sequence length; a zero-length sequence reports 0.0 (it is
    reported by :func:`empty_sequence_taxa` instead).
    """
    definite = definite_chars(alignment.data_type)
    result: Dict[str, float] = {}
    for seq in alignment.sequences:
        text = str(seq.sequence or "")
        if not text:
            result[seq.taxon] = 0.0
            continue
        ambiguous = sum(1 for char in text if char.upper() not in definite)
        result[seq.taxon] = ambiguous / len(text)
    return result


def codon_length_defect(alignment: Alignment) -> Optional[int]:
    """``n_sites % 3`` for a uniform-length alignment; None when not applicable.

    Returns None for ragged, empty or site-less alignments (other checks report
    those) and 0 when every sequence length is a multiple of three.
    """
    lengths = {len(str(seq.sequence or "")) for seq in alignment.sequences}
    if len(lengths) != 1:
        return None
    length = lengths.pop()
    if length == 0:
        return None
    return length % 3


def internal_stop_positions(sequence: str) -> List[int]:
    """1-based codon positions of internal stop codons in ``sequence``.

    The final codon is never "internal" (a terminal stop is a normal CDS
    artefact).  Ambiguity is resolved rather than guessed at: a codon whose
    every possible resolution is a stop (``TAR``, ``TRA``) translates to ``*``
    and is reported, while a codon that is genuinely ambiguous (``NNN``,
    ``TAN``, ``TGR``) translates to ``X`` and is not.  A trailing partial codon
    (length not a multiple of three) is ignored here; see
    :func:`codon_length_defect`.
    """
    from Bio.Seq import Seq

    text = str(sequence or "").upper()
    n_codons = len(text) // 3
    if n_codons <= 1:
        return []
    cache: Dict[str, str] = {}
    stops: List[int] = []
    for index in range(n_codons - 1):
        codon = text[index * 3 : index * 3 + 3]
        residue = cache.get(codon)
        if residue is None:
            try:
                residue = str(Seq(codon).translate(table="Standard"))
            except Exception:  # pragma: no cover - defensive
                residue = "X"
            cache[codon] = residue
        if residue == "*":
            stops.append(index + 1)
    return stops


def stop_codon_taxa(alignment: Alignment) -> Dict[str, List[int]]:
    """Map taxon -> internal stop-codon positions (only taxa that have one)."""
    result: Dict[str, List[int]] = {}
    for seq in alignment.sequences:
        stops = internal_stop_positions(str(seq.sequence or ""))
        if stops:
            result[seq.taxon] = stops
    return result


def read_alignment_lenient(
    path: Any,
    data_type: Any = "auto",
    alignment_id: Optional[str] = None,
) -> Alignment:
    """Read an alignment for data checking without rejecting bad data.

    Unlike :meth:`SequenceReader.read`, this never raises for duplicate taxa,
    empty/ragged sequences, illegal characters or an unknown declared
    ``data_type`` (including ``codon``): the returned :class:`Alignment`
    carries whatever the file contains so the inspectors above can report
    per-taxon findings.  Sequences are upper-cased exactly like the strict
    reader.

    Raises:
        FileNotFoundError: the file does not exist.
        AlignmentError: the file cannot be decoded or parsed at all.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Alignment file not found: {path}")
    fmt = "nexus" if file_path.suffix.lower() in (".nex", ".nexus") else "fasta"
    text = read_text(file_path)
    if fmt == "nexus":
        try:
            text = expand_nexus_match_chars(text, file_path)
        except AlignmentError:
            pass  # keep the raw text; the NEXUS reader below may still work
        records = SequenceReader._read_nexus(text, file_path)
    else:
        records = SequenceReader._read_fasta(text, file_path)
    if not records:
        raise AlignmentError(f"No sequences found in {path}")

    declared = str(data_type).strip() if data_type is not None else "auto"
    resolved: Optional[DataType] = None
    source = "auto"
    if declared.lower() != "auto" and not is_codon_data_type(declared):
        try:
            resolved = DataType.from_config(declared)
            source = "declared"
        except ValueError:
            resolved = None
    if resolved is None:
        observed = {char for _, seq in records for char in seq}
        try:
            resolved, _ambiguous = detect_data_type(observed)
        except AlignmentError:
            # Characters outside both alphabets: keep nucleotide so the
            # illegal-character inspector can report them per taxon.
            resolved = DataType.NUCLEOTIDE
    return Alignment(
        id=str(alignment_id or file_path.stem),
        data_type=resolved,
        sequences=[Sequence(taxon=name, sequence=seq) for name, seq in records],
        source_file=str(file_path),
        data_type_source=source,
        data_type_ambiguous=False,
    )
