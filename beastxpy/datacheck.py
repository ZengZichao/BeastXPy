"""Pre-flight data and calibration checks for BeastXPy configs (F2).

This module powers ``beastxpy check-data`` and the ``generate``/``run``
pre-check hook.  It inspects the *data and calibration semantics* of a parsed
:class:`~beastxpy.models.BEASTConfig` (or, when Gate 0 rejected the config, a
lenient reconstruction of it) and produces findings that always carry a
severity, a precise location and an actionable fix.

What this module does NOT do: it never generates XML, never runs BEAST, never
re-implements Gate 0 structural validation and never claims anything about
model adequacy or convergence.

Severity policy (fixed by the command specification; errors are never
downgraded to warnings): see :data:`FINDING_SEVERITIES`.  Every finding is
built through :func:`_finding`, which takes the severity from that table, so a
check cannot accidentally soften one.

Ambiguity threshold: ``high-ambiguity`` uses
:data:`DEFAULT_AMBIGUITY_THRESHOLD` = 0.10 (10% of a taxon's sites are gaps,
unknowns or IUPAC ambiguity codes).  10% is a common alignment-QC cutoff: a
taxon above it contributes little usable signal and often indicates a
poorly-aligned or contaminated sequence, while ordinary gapped alignments
(like the bundled primates example, <= 0.6%) stay well below it.  The value is
adjustable via ``--ambiguity-threshold`` and the ``ambiguity_threshold``
keyword; the comparison is strict (``fraction > threshold``).

Time-axis semantics for ``root-height-conflict``: BEAST 1 measures tmrca
statistics on the tree's time axis, which is the tip-date axis when tip dates
exist.  A root calibration therefore conflicts with the tip dates when even
its upper plausible age (97.5% quantile) does not exceed the tip-date span
(max - min over uniformly-directed, uniformly-united dates), and it conflicts
with a clade calibration when its upper plausible age is below the clade's
lower plausible age (a nested clade older than the root is impossible).
Configurations without a root calibration produce no finding: one is never
invented.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import yaml

from . import _contracts as C
from .calibration import (
    calibration_age_interval,
    empty_taxa_calibrations,
    missing_calibration_taxa,
)
from .models import (
    Alignment,
    BEASTConfig,
    CalibrationPoint,
    ClockModelConfig,
    ControlConfig,
    DistributionConfig,
    Partition,
    SiteModelConfig,
)
from .partition import (
    overlapping_window_pairs,
    resolve_partition_window,
    taxon_set_mismatches,
    uncovered_site_ranges,
)
from .sequence import (
    ambiguity_fractions,
    codon_length_defect,
    duplicate_sequence_groups,
    duplicate_taxa,
    empty_sequence_taxa,
    illegal_characters_by_taxon,
    is_codon_data_type,
    read_alignment_lenient,
    stop_codon_taxa,
)

#: Per-taxon ambiguous/gap fraction above which ``high-ambiguity`` is reported.
#: See the module docstring for the rationale; the comparison is strict (>).
DEFAULT_AMBIGUITY_THRESHOLD = 0.10

#: Substitution-model keys implying protein-coding (codon) data.  Codon models
#: are hard-rejected at Gate 0 (no BEAST 1 shim), but ``check-data`` still runs
#: codon inspectors when such a model or a codon ``data_type`` is declared.
CODON_SITE_MODELS = frozenset({"gy94", "mg94", "gy94_codon", "codon"})

#: Newick/NEXUS metacharacters.  A taxon label containing one of them (or any
#: whitespace) cannot survive BEAST's translation step: the tree log is
#: rewritten with the unquoted label and re-parses as a truncated or malformed
#: tree, silently dropping the taxon.
NEWICK_DELIMITERS = frozenset({"(", ")", ",", ":", ";", "[", "]", '"', "'", "\\"})

#: finding id -> severity, fixed by the command specification.  Errors are
#: never downgraded to warnings; :func:`_finding` is the only builder and it
#: always reads the severity from this table.
FINDING_SEVERITIES: Dict[str, str] = {
    "duplicate-taxon": C.SEVERITY_ERROR,
    "empty-taxon": C.SEVERITY_ERROR,
    "duplicate-sequence": C.SEVERITY_WARNING,
    "taxon-name-format": C.SEVERITY_WARNING,
    "illegal-character": C.SEVERITY_ERROR,
    "high-ambiguity": C.SEVERITY_WARNING,
    "partition-overlap": C.SEVERITY_ERROR,
    "partition-gap": C.SEVERITY_WARNING,
    "partition-out-of-range": C.SEVERITY_ERROR,
    "partition-taxon-mismatch": C.SEVERITY_ERROR,
    "calibration-taxon-missing": C.SEVERITY_ERROR,
    "calibration-empty-taxa": C.SEVERITY_ERROR,
    "tip-date-format": C.SEVERITY_ERROR,
    "tip-date-order": C.SEVERITY_WARNING,
    "root-height-conflict": C.SEVERITY_ERROR,
    "codon-length": C.SEVERITY_ERROR,
    "codon-stop": C.SEVERITY_WARNING,
    "empirical-tree-taxa": C.SEVERITY_ERROR,
    # Beyond the specification table: a Gate-0 rejection of the config itself
    # and an unexpected crash inside one check.  Both are always errors.
    "config-parse-error": C.SEVERITY_ERROR,
    "internal-error": C.SEVERITY_ERROR,
}

#: Category label per finding id (grouping only; carries no severity meaning).
FINDING_CATEGORIES: Dict[str, str] = {
    "duplicate-taxon": "sequence",
    "empty-taxon": "sequence",
    "duplicate-sequence": "sequence",
    "taxon-name-format": "sequence",
    "illegal-character": "sequence",
    "high-ambiguity": "sequence",
    "codon-length": "sequence",
    "codon-stop": "sequence",
    "partition-overlap": "partition",
    "partition-gap": "partition",
    "partition-out-of-range": "partition",
    "partition-taxon-mismatch": "partition",
    "calibration-taxon-missing": "calibration",
    "calibration-empty-taxa": "calibration",
    "root-height-conflict": "calibration",
    "tip-date-format": "tip-date",
    "tip-date-order": "tip-date",
    "empirical-tree-taxa": "tree",
    "config-parse-error": "config",
    "internal-error": "config",
}

#: Marker attribute set on leniently-read alignments whose declared data_type
#: was codon-like (BEASTConfig/Alignment have no codon DataType of their own).
_CODON_MARKER = "bxp_codon"


def _finding(
    finding_id: str,
    message: str,
    *,
    fix: str,
    location: Optional[Dict[str, Any]] = None,
    value: Any = None,
    threshold: Any = None,
) -> Dict[str, Any]:
    """Build one finding; the severity always comes from the fixed table."""
    return C.finding(
        finding_id,
        FINDING_SEVERITIES[finding_id],
        message,
        category=FINDING_CATEGORIES.get(finding_id),
        location=location,
        fix=fix,
        value=value,
        threshold=threshold,
    )


def _validate_threshold(ambiguity_threshold: Any) -> float:
    """Validate the ambiguity threshold; raise ContractError when unusable."""
    try:
        threshold = float(ambiguity_threshold)
    except (TypeError, ValueError):
        raise C.ContractError(
            f"ambiguity threshold must be a number, got {ambiguity_threshold!r}",
            fix=f"Pass a fraction between 0 and 1 (default {DEFAULT_AMBIGUITY_THRESHOLD}).",
        ) from None
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise C.ContractError(
            f"ambiguity threshold must be a fraction in [0, 1], got {threshold!r}",
            fix=f"Pass a value between 0 and 1 (default {DEFAULT_AMBIGUITY_THRESHOLD}).",
        )
    return threshold


def _loc(config_path: Optional[Any], **extra: Any) -> Dict[str, Any]:
    """Location dict: the config file plus whatever the finding pins down."""
    location: Dict[str, Any] = {}
    if config_path is not None:
        location["config"] = C.display_path(config_path)
    location.update({key: val for key, val in extra.items() if val is not None})
    return location


def _alignments(config: BEASTConfig) -> List[Alignment]:
    """Distinct alignments of a config, complete (un-windowed) copies first."""
    seen: Dict[str, Alignment] = {}
    for alignment in list(getattr(config, "all_alignments", None) or []):
        seen.setdefault(alignment.id, alignment)
    for partition in config.partitions:
        seen.setdefault(partition.alignment.id, partition.alignment)
    return list(seen.values())


def _alignment_taxa(config: BEASTConfig) -> Set[str]:
    """Union of taxon names across all alignments."""
    taxa: Set[str] = set()
    for alignment in _alignments(config):
        taxa.update(alignment.taxa_names)
    return taxa


def _codon_alignment_ids(config: BEASTConfig) -> Set[str]:
    """Alignments that must be inspected as codon/protein-coding data."""
    ids = {a.id for a in _alignments(config) if getattr(a, _CODON_MARKER, False)}
    for partition in config.partitions:
        model_type = str((partition.site_model.substitution_model or {}).get("type", ""))
        if model_type.strip().lower() in CODON_SITE_MODELS:
            ids.add(partition.alignment.id)
    return ids


# ---------------------------------------------------------------------------
# Checks.  Uniform signature so collect_findings can wrap each one.
# ---------------------------------------------------------------------------


def _check_sequences(
    config: BEASTConfig,
    *,
    config_path: Optional[Any] = None,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Sequence-level findings for every distinct alignment."""
    findings: List[Dict[str, Any]] = []
    codon_ids = _codon_alignment_ids(config)
    for alignment in _alignments(config):
        base = _loc(
            config_path,
            alignment=alignment.id,
            file=(C.display_path(alignment.source_file) if alignment.source_file else None),
        )
        names = alignment.taxa_names
        for taxon in duplicate_taxa(alignment):
            findings.append(
                _finding(
                    "duplicate-taxon",
                    f"taxon {taxon!r} occurs {names.count(taxon)} times in alignment "
                    f"{alignment.id!r}; BEAST needs one sequence per taxon",
                    fix=(
                        f"Open {alignment.source_file or 'the alignment file'} and "
                        f"keep a single record for {taxon!r} (delete the redundant "
                        "copy or rename it if it is a distinct specimen)."
                    ),
                    location={**base, "taxon": taxon},
                )
            )
        for taxon in empty_sequence_taxa(alignment):
            findings.append(
                _finding(
                    "empty-taxon",
                    f"taxon {taxon!r} has a zero-length or whitespace-only sequence "
                    f"in alignment {alignment.id!r}",
                    fix=(
                        f"Fill in the sequence for {taxon!r} in "
                        f"{alignment.source_file or 'the alignment file'} or remove "
                        "the taxon (and every calibration/tip date naming it)."
                    ),
                    location={**base, "taxon": taxon},
                )
            )
        for taxon, info in illegal_characters_by_taxon(alignment).items():
            chars = ", ".join(repr(char) for char in info["chars"])
            sites = ", ".join(str(site) for site in info["sites"])
            findings.append(
                _finding(
                    "illegal-character",
                    f"taxon {taxon!r} contains character(s) {chars} outside the "
                    f"{alignment.data_type.value} alphabet of alignment "
                    f"{alignment.id!r} (first at site(s) {sites})",
                    fix=(
                        f"Correct or mask those positions in "
                        f"{alignment.source_file or 'the alignment file'}, or set "
                        "data_type to the alphabet the data really uses."
                    ),
                    location={**base, "taxon": taxon, "sites": info["sites"]},
                    value={"chars": info["chars"]},
                )
            )
        for group in duplicate_sequence_groups(alignment):
            findings.append(
                _finding(
                    "duplicate-sequence",
                    f"{len(group)} taxa in alignment {alignment.id!r} share an "
                    f"identical sequence: {', '.join(repr(t) for t in group)}",
                    fix=(
                        "Verify these are genuine biological replicates; identical "
                        "sequences can also mean contamination or a copy/paste "
                        "labelling error. Remove or rename the redundant records "
                        "if they are not."
                    ),
                    location={**base, "taxa": list(group)},
                )
            )
        unsafe = sorted(
            {
                name
                for name in names
                if any(char.isspace() or char in NEWICK_DELIMITERS for char in name)
            }
        )
        if unsafe:
            findings.append(
                _finding(
                    "taxon-name-format",
                    f"taxon name(s) {', '.join(repr(name) for name in unsafe)} in "
                    f"alignment {alignment.id!r} contain whitespace or Newick/NEXUS "
                    "delimiters; BEAST's tree-log translator can truncate or "
                    "mis-parse such labels",
                    fix=(
                        "Rename the taxon(s) using letters, digits, '_' or '-' "
                        "(for example replace spaces with underscores), and "
                        "update every calibration and tip date that names them."
                    ),
                    location={**base, "taxa": unsafe},
                )
            )
        for taxon, fraction in ambiguity_fractions(alignment).items():
            if fraction > ambiguity_threshold:
                findings.append(
                    _finding(
                        "high-ambiguity",
                        f"taxon {taxon!r} in alignment {alignment.id!r} is "
                        f"{fraction:.1%} ambiguous/missing (gaps, N/?/X and IUPAC "
                        f"ambiguity codes), above the {ambiguity_threshold:.0%} "
                        "threshold",
                        fix=(
                            f"Re-examine the sequence for {taxon!r}: trim the "
                            "poorly-aligned regions, re-sequence it, or exclude "
                            "the taxon if it adds no signal."
                        ),
                        location={**base, "taxon": taxon},
                        value=round(fraction, 4),
                        threshold=ambiguity_threshold,
                    )
                )
        if alignment.id in codon_ids:
            defect = codon_length_defect(alignment)
            if defect:
                findings.append(
                    _finding(
                        "codon-length",
                        f"alignment {alignment.id!r} is declared codon/protein-"
                        f"coding but its length is not a multiple of 3 "
                        f"(length % 3 == {defect})",
                        fix=(
                            "Trim or pad the alignment to a whole number of "
                            "codons (check for a partial terminal codon or a "
                            "frame-shifting indel)."
                        ),
                        location={**base},
                        value=defect,
                    )
                )
            for taxon, stops in stop_codon_taxa(alignment).items():
                shown = ", ".join(str(pos) for pos in stops[:5])
                findings.append(
                    _finding(
                        "codon-stop",
                        f"taxon {taxon!r} in codon alignment {alignment.id!r} has "
                        f"{len(stops)} internal stop codon(s) (codon {shown}"
                        f"{'...' if len(stops) > 5 else ''})",
                        fix=(
                            f"Check the reading frame of {taxon!r} (internal stops "
                            "usually mean a frameshift or a pseudogene); fix the "
                            "sequence or exclude the taxon."
                        ),
                        location={**base, "taxon": taxon, "codons": stops[:5]},
                        value=len(stops),
                    )
                )
    return findings


def _check_partitions(
    config: BEASTConfig,
    *,
    config_path: Optional[Any] = None,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Partition window findings (overlap, gaps, range, taxon-set mismatch)."""
    findings: List[Dict[str, Any]] = []
    if not config.partitions:
        return findings
    by_alignment: Dict[str, List[Partition]] = {}
    for partition in config.partitions:
        by_alignment.setdefault(partition.alignment.id, []).append(partition)
    for alignment_id, parts in by_alignment.items():
        alignment = parts[0].alignment
        try:
            n_sites = alignment.n_sites
        except ValueError:
            # Ragged alignment: the sequence checks (and Gate 0) report it;
            # site windows are meaningless without one shared site count.
            continue
        base = _loc(
            config_path,
            alignment=alignment_id,
            file=(C.display_path(alignment.source_file) if alignment.source_file else None),
        )
        windows: List[Tuple[str, int, int]] = []
        invalid = False
        for partition in parts:
            window, issue = resolve_partition_window(
                partition.alignment,
                partition.alignment.from_pos,
                partition.alignment.to_pos,
            )
            if window is None:
                invalid = True
                findings.append(
                    _finding(
                        "partition-out-of-range",
                        f"partition {partition.id!r}: {issue}",
                        fix=(
                            f"Set from/to inside 1..{n_sites} (1-based inclusive) "
                            f"for partition {partition.id!r}, matching the sites "
                            "this partition should model."
                        ),
                        location={
                            **base,
                            "partition": partition.id,
                            "from": partition.alignment.from_pos,
                            "to": partition.alignment.to_pos,
                            "n_sites": n_sites,
                        },
                    )
                )
                continue
            windows.append((partition.id, window[0], window[1]))
        for (id_a, start_a, end_a), (id_b, start_b, end_b) in overlapping_window_pairs(windows):
            findings.append(
                _finding(
                    "partition-overlap",
                    f"partitions {id_a!r} [{start_a}-{end_a}] and {id_b!r} "
                    f"[{start_b}-{end_b}] both claim site(s) "
                    f"{max(start_a, start_b)}-{min(end_a, end_b)} of alignment "
                    f"{alignment_id!r}",
                    fix=(
                        "Make the windows disjoint: adjacent partitions must use "
                        f"from = previous to + 1 (e.g. 1-{end_a} and "
                        f"{end_a + 1}-...)."
                    ),
                    location={
                        **base,
                        "partitions": [id_a, id_b],
                        "sites": [max(start_a, start_b), min(end_a, end_b)],
                    },
                )
            )
        if windows and not invalid:
            for start, end in uncovered_site_ranges([(s, e) for _, s, e in windows], n_sites):
                findings.append(
                    _finding(
                        "partition-gap",
                        f"site(s) {start}-{end} of alignment {alignment_id!r} "
                        f"({end - start + 1} of {n_sites} sites) are covered by no "
                        "partition and will be silently ignored by BEAST",
                        fix=(
                            "Extend an existing partition window over these sites "
                            "or add a partition for them; if excluding them is "
                            "intentional, note it in the analysis description."
                        ),
                        location={**base, "sites": [start, end], "n_sites": n_sites},
                    )
                )
    for ref_id, pid, missing, extra in taxon_set_mismatches(config.partitions):
        findings.append(
            _finding(
                "partition-taxon-mismatch",
                f"partition {pid!r} disagrees with partition {ref_id!r} on the "
                f"taxon set (missing {missing}, extra {extra}); all partitions "
                "share one linked tree",
                fix=(
                    "Use alignments with the same taxon set for every partition "
                    "(add the missing taxa or remove the extra ones from the "
                    "alignment files)."
                ),
                location=_loc(
                    config_path,
                    partitions=[ref_id, pid],
                    missing=missing,
                    extra=extra,
                ),
            )
        )
    return findings


def _check_calibrations(
    config: BEASTConfig,
    *,
    config_path: Optional[Any] = None,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Calibration taxon findings."""
    findings: List[Dict[str, Any]] = []
    known = _alignment_taxa(config)
    for name, missing in missing_calibration_taxa(config.calibrations, known):
        findings.append(
            _finding(
                "calibration-taxon-missing",
                f"calibration {name!r} names taxon/taxa {missing} that no " "alignment contains",
                fix=(
                    f"Correct the spelling in the calibration's taxa list, or add "
                    f"{'/'.join(missing)} to the alignment; the names must match "
                    "the FASTA/NEXUS headers exactly (case-sensitive)."
                ),
                location=_loc(config_path, calibration=name, taxa=missing),
            )
        )
    for name in empty_taxa_calibrations(config.calibrations):
        findings.append(
            _finding(
                "calibration-empty-taxa",
                f"calibration {name!r} has an empty taxa list; a clade constraint "
                "needs at least one taxon (use 'taxa: null' for a root "
                "calibration)",
                fix=(
                    f"List the clade's taxa under {name!r}, or set 'taxa: null' "
                    "if this is meant to calibrate the root."
                ),
                location=_loc(config_path, calibration=name),
            )
        )
    return findings


def _normalized_tip_entries(
    config: BEASTConfig,
) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str]]]:
    """Normalize config.tip_dates (raw or already normalized).

    Returns ``(entries, problems)`` where ``entries`` maps taxon ->
    ``{"value", "direction", "units"}`` and ``problems`` lists ``(taxon,
    message)`` for entries that do not parse.
    """
    from .sequence import AlignmentError, normalize_tip_dates

    beast_version = getattr(getattr(config, "control", None), "beast_version", None)
    entries: Dict[str, Dict[str, Any]] = {}
    problems: List[Tuple[str, str]] = []
    raw = getattr(config, "tip_dates", None) or {}
    if not isinstance(raw, dict):
        return entries, [("*", f"tip_dates must be a mapping, got {type(raw).__name__}")]
    for taxon, entry in raw.items():
        try:
            normalized = normalize_tip_dates(
                {taxon: entry}, known_taxa=None, beast_version=beast_version
            )
        except (AlignmentError, ValueError, TypeError) as exc:
            problems.append((str(taxon), str(exc)))
            continue
        value = normalized.get(taxon)
        if value is None:  # pragma: no cover - normalize returns every taxon
            problems.append((str(taxon), "tip date did not normalize"))
            continue
        entries[str(taxon)] = value
    return entries, problems


def _check_tip_dates(
    config: BEASTConfig,
    *,
    config_path: Optional[Any] = None,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Tip-date findings: format errors and direction/ordering warnings."""
    findings: List[Dict[str, Any]] = []
    entries, problems = _normalized_tip_entries(config)
    for taxon, message in problems:
        findings.append(
            _finding(
                "tip-date-format",
                f"tip date for {taxon!r} is not usable: {message}",
                fix=(
                    "Give every dated taxon a finite number (or "
                    "{value: <number>, direction: forwards|backwards, units: "
                    "years|months|days})."
                ),
                location=_loc(config_path, taxon=taxon),
            )
        )
    if not entries:
        return findings
    for taxon, entry in entries.items():
        if entry["value"] < 0:
            findings.append(
                _finding(
                    "tip-date-order",
                    f"tip date for {taxon!r} is negative ({entry['value']:g}) with "
                    f"direction {entry['direction']!r}: backwards dates are ages "
                    "before the present and forwards dates count from the "
                    "origin, so a negative value places this sample outside the "
                    "time axis",
                    fix=(
                        f"Check the sign and the direction for {taxon!r}; if the "
                        "value is an age before the present use a non-negative "
                        "number with direction: backwards."
                    ),
                    location=_loc(config_path, taxon=taxon),
                    value=entry["value"],
                )
            )
    # Mixed directions are legal in BEAST 1 (per-taxon ``direction``
    # attribute; e.g. 0.0-forwards combined with backwards ages), so mixing is
    # not a finding by itself -- but the span-based root-height check needs one
    # common axis and is skipped for mixed dates (see _tip_date_span).
    values = [entry["value"] for entry in entries.values()]
    if len(entries) >= 2 and max(values) == min(values):
        findings.append(
            _finding(
                "tip-date-order",
                f"all {len(entries)} tip dates are identical ({values[0]:g}); the "
                "data carry no temporal signal, so tip dating cannot estimate a "
                "rate",
                fix=(
                    "Enter the real (distinct) sampling dates, or drop tip_dates "
                    "and rely on calibrations for the time axis."
                ),
                location=_loc(config_path),
                value=values[0],
            )
        )
    return findings


def _tip_date_span(
    entries: Dict[str, Dict[str, Any]],
) -> Optional[float]:
    """Tip-date span (max - min) when it is comparable; None otherwise.

    Requires at least two dated taxa with one shared direction and one shared
    unit; mixed axes cannot be compared and yield None (no finding is invented).
    """
    if len(entries) < 2:
        return None
    directions = {entry["direction"] for entry in entries.values()}
    units = {entry["units"] for entry in entries.values()}
    if len(directions) != 1 or len(units) != 1:
        return None
    values = [entry["value"] for entry in entries.values()]
    return max(values) - min(values)


def _check_root_height(
    config: BEASTConfig,
    *,
    config_path: Optional[Any] = None,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Root-calibration conflicts against tip dates and clade calibrations."""
    findings: List[Dict[str, Any]] = []
    root = getattr(config, "root_calibration", None)
    if root is None:
        return findings  # never invent a root calibration
    root_low, root_high = calibration_age_interval(root)
    if root_high is None:
        return findings  # unbounded root: nothing can be disproved
    entries, _problems = _normalized_tip_entries(config)
    span = _tip_date_span(entries)
    if span is not None and root_high <= span:
        findings.append(
            _finding(
                "root-height-conflict",
                f"root calibration {root.name!r} implies an age of at most "
                f"{root_high:g} (97.5% quantile), which does not exceed the "
                f"tip-date span of {span:g} on the same time axis; the tree "
                "cannot be both",
                fix=(
                    "Raise the root calibration (larger offset/mean) or fix the "
                    "tip dates/units; the root must be older than the spread of "
                    "the sampled tips."
                ),
                location=_loc(config_path, calibration=root.name, against="tip_dates"),
                value={"root_age_max": root_high, "tip_date_span": span},
            )
        )
    for cal in config.calibrations:
        if cal is root or cal.is_root:
            continue
        clade_low, _clade_high = calibration_age_interval(cal)
        if clade_low is None or root_high >= clade_low:
            continue
        findings.append(
            _finding(
                "root-height-conflict",
                f"root calibration {root.name!r} implies an age of at most "
                f"{root_high:g}, but calibration {cal.name!r} on a nested clade "
                f"starts at {clade_low:g}; a clade cannot be older than the root",
                fix=(
                    f"Make the root prior older than {cal.name!r} (or make "
                    f"{cal.name!r} younger); nested clade ages must stay below "
                    "the root age."
                ),
                location=_loc(config_path, calibration=root.name, against=cal.name),
                value={"root_age_max": root_high, "clade_age_min": clade_low},
            )
        )
    return findings


def _check_empirical_tree(
    config: BEASTConfig,
    *,
    config_path: Optional[Any] = None,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Empirical (Newick) starting-tree taxa vs alignment taxa."""
    control = getattr(config, "control", None)
    if control is None or getattr(control, "initial_tree", None) != "newick":
        return []  # no empirical tree configured: nothing to compare
    newick = getattr(control, "newick", None)
    if not newick or not str(newick).strip():
        return []  # Gate 0 rejects 'newick' without a tree string
    from .treetools import TreeToolsError, _parse_newick

    base = _loc(config_path, tree="control.newick")
    try:
        _root, tips = _parse_newick(str(newick), {})
    except TreeToolsError as exc:
        return [
            _finding(
                "empirical-tree-taxa",
                f"the empirical tree in control.newick could not be parsed: {exc}",
                fix=(
                    "Provide a balanced Newick string ending in ';' whose tip "
                    "labels are the alignment taxon names."
                ),
                location=base,
            )
        ]
    tree_taxa = frozenset(str(tip) for tip in tips)
    alignment_taxa = frozenset(_alignment_taxa(config))
    if not alignment_taxa:
        return []  # no alignment taxa to compare (Gate 0 reports the alignment)
    missing = sorted(alignment_taxa - tree_taxa)
    extra = sorted(tree_taxa - alignment_taxa)
    if not missing and not extra:
        return []
    parts = []
    if missing:
        parts.append(f"missing from the tree: {missing}")
    if extra:
        parts.append(f"in the tree but not in any alignment: {extra}")
    return [
        _finding(
            "empirical-tree-taxa",
            "empirical tree taxa do not match the alignment taxa (" + "; ".join(parts) + ")",
            fix=(
                "Rename the tree tips to the exact alignment taxon names (or fix "
                "the alignment) so both sets are identical; BEAST matches tips "
                "to taxa by name."
            ),
            location={**base, "missing_from_tree": missing, "not_in_alignment": extra},
            value={"missing_from_tree": missing, "not_in_alignment": extra},
        )
    ]


#: The checks run by :func:`collect_findings`, in report order.
CHECKS: Tuple[Tuple[str, Callable[..., List[Dict[str, Any]]]], ...] = (
    ("sequences", _check_sequences),
    ("partitions", _check_partitions),
    ("calibrations", _check_calibrations),
    ("tip-dates", _check_tip_dates),
    ("root-height", _check_root_height),
    ("empirical-tree", _check_empirical_tree),
)


def collect_findings(
    config: BEASTConfig,
    *,
    config_path: Optional[Any] = None,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
    checks: Optional[Sequence[Tuple[str, Callable[..., List[Dict[str, Any]]]]]] = None,
) -> List[Dict[str, Any]]:
    """Run every check against ``config`` and return the sorted findings.

    A check that raises unexpectedly does not abort the run: its crash becomes
    an ``internal-error`` finding naming the check and the exception, so the
    user still gets a complete report.
    """
    threshold = _validate_threshold(ambiguity_threshold)
    active = tuple(CHECKS if checks is None else checks)
    findings: List[Dict[str, Any]] = []
    for name, check in active:
        try:
            findings.extend(check(config, config_path=config_path, ambiguity_threshold=threshold))
        except Exception as exc:  # noqa: BLE001 - one bad check must not kill the run
            findings.append(
                _finding(
                    "internal-error",
                    f"data check {name!r} failed: {type(exc).__name__}: {exc}",
                    fix=(
                        "This is a BeastXPy bug, not a problem with your data: "
                        "re-run with --json to capture the report and file it "
                        "with the findings attached."
                    ),
                    location=_loc(config_path, check=name),
                )
            )
    return C.sort_findings(findings)


def check_config(
    config: BEASTConfig,
    *,
    config_path: Optional[Any] = None,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
) -> Dict[str, Any]:
    """Return the full ``beastxpy.datacheck/v1`` envelope for a parsed config."""
    findings = collect_findings(
        config, config_path=config_path, ambiguity_threshold=ambiguity_threshold
    )
    counts = C.count_severities(findings)
    return C.envelope(
        C.SCHEMA_DATACHECK,
        config_path=C.display_path(config_path) if config_path is not None else None,
        verdict=C.verdict_from_counts(counts),
        counts=counts,
        findings=findings,
    )


def run_precheck(
    config: BEASTConfig,
    *,
    config_path: Optional[Any] = None,
    enabled: bool = True,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Findings list only (no envelope), for the generate/run pre-check hook.

    Returns ``[]`` when ``enabled`` is False (the ``--no-data-check`` escape
    hatch).
    """
    if not enabled:
        return []
    return collect_findings(
        config, config_path=config_path, ambiguity_threshold=ambiguity_threshold
    )


def findings_exit_code(counts: Dict[str, int]) -> int:
    """Map severity counts to the fixed check-data exit code (0/1/2)."""
    if counts.get(C.SEVERITY_ERROR, 0) > 0:
        return C.EXIT_CHECK_ERROR
    if counts.get(C.SEVERITY_WARNING, 0) > 0:
        return C.EXIT_CHECK_WARNING
    return C.EXIT_CHECK_PASS


def envelope_exit_code(envelope: Dict[str, Any]) -> int:
    """Exit code for a full datacheck envelope."""
    return findings_exit_code(envelope.get("counts") or {})


# ---------------------------------------------------------------------------
# Lenient reconstruction (used when Gate 0 rejected the config file)
# ---------------------------------------------------------------------------


def _windowed_copy(alignment: Alignment, from_pos: Any, to_pos: Any) -> Alignment:
    """Alignment copy carrying raw (unvalidated) window coordinates."""
    copy = Alignment(
        id=alignment.id,
        data_type=alignment.data_type,
        sequences=alignment.sequences,
        source_file=alignment.source_file,
        from_pos=from_pos,
        to_pos=to_pos,
        data_type_source=alignment.data_type_source,
        data_type_ambiguous=alignment.data_type_ambiguous,
    )
    if getattr(alignment, _CODON_MARKER, False):
        setattr(copy, _CODON_MARKER, True)
    return copy


def _lenient_calibration(raw: Any, index: int) -> Optional[CalibrationPoint]:
    """Build a CalibrationPoint from a raw YAML mapping, tolerating bad data."""
    if not isinstance(raw, dict):
        return None
    name_raw = raw.get("name")
    name = (
        str(name_raw)
        if isinstance(name_raw, str) and name_raw.strip()
        else f"calibration{index + 1}"
    )
    taxa_raw = raw.get("taxa")
    taxa: Optional[List[str]]
    if taxa_raw is None:
        taxa = None
    elif isinstance(taxa_raw, list):
        taxa = [str(taxon) for taxon in taxa_raw]
    else:
        taxa = [str(taxa_raw)]
    distribution = None
    dist_raw = raw.get("distribution")
    if isinstance(dist_raw, dict):
        params_raw = dist_raw.get("parameters")
        try:
            offset = float(dist_raw.get("offset", 0.0))
        except (TypeError, ValueError):
            offset = 0.0
        distribution = DistributionConfig(
            type=str(dist_raw.get("type", "")),
            parameters=dict(params_raw) if isinstance(params_raw, dict) else {},
            offset=offset,
        )
    method_raw = str(raw.get("calibration_method", "clade")).strip().lower()
    method = {"clade": "clade", "crown": "clade", "stem": "stem"}.get(method_raw, "clade")
    monophyletic = raw.get("monophyletic", True)
    return CalibrationPoint(
        name=name,
        taxa=taxa,
        monophyletic=monophyletic if isinstance(monophyletic, bool) else True,
        distribution=distribution,
        calibration_method=method,
    )


def build_lenient_config(config_path: Any) -> Optional[BEASTConfig]:
    """Reconstruct a checkable BEASTConfig from a Gate-0-rejected YAML file.

    Reads the alignment files with :func:`~beastxpy.sequence.read_alignment_lenient`
    and keeps raw partition windows, calibrations, tip dates and control values,
    so the data checks can still report per-taxon/per-site findings.  Returns
    None when even the YAML itself is unreadable.  Never raises.
    """
    path = Path(config_path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - unreadable YAML: config-parse-error covers it
        return None
    if not isinstance(raw, dict):
        return None

    base_dir = path.parent
    alignments: List[Alignment] = []
    raw_alignments = raw.get("alignments")
    if isinstance(raw_alignments, list):
        for entry in raw_alignments:
            if not isinstance(entry, dict):
                continue
            file_name = entry.get("file")
            if not isinstance(file_name, str) or not file_name:
                continue
            alignment_id = entry.get("id") or Path(file_name).stem
            declared = entry.get("data_type", "auto")
            try:
                alignment = read_alignment_lenient(
                    base_dir / file_name,
                    data_type=declared,
                    alignment_id=str(alignment_id),
                )
            except Exception:  # noqa: BLE001 - unreadable file: Gate 0 explains it
                continue
            if is_codon_data_type(declared):
                setattr(alignment, _CODON_MARKER, True)
            alignments.append(alignment)
    alignment_map = {alignment.id: alignment for alignment in alignments}

    partitions: List[Partition] = []
    raw_partitions = raw.get("partitions")
    if isinstance(raw_partitions, list):
        for index, entry in enumerate(raw_partitions):
            if not isinstance(entry, dict):
                continue
            alignment_id = entry.get("alignment_id") or entry.get("alignment")
            selected_alignment: Optional[Alignment] = (
                alignment_map.get(str(alignment_id)) if alignment_id is not None else None
            )
            if selected_alignment is None:
                continue
            alignment = selected_alignment
            site_raw = entry.get("site_model")
            substitution: Dict[str, Any] = {"type": "hky"}
            if isinstance(site_raw, dict):
                model_raw = site_raw.get("substitution_model")
                if isinstance(model_raw, dict) and isinstance(model_raw.get("type"), str):
                    substitution = {"type": model_raw["type"]}
            partitions.append(
                Partition(
                    id=str(entry.get("id") or f"partition{index + 1}"),
                    alignment=_windowed_copy(alignment, entry.get("from"), entry.get("to")),
                    site_model=SiteModelConfig(substitution_model=substitution),
                    clock_model=ClockModelConfig(),
                )
            )

    calibrations: List[CalibrationPoint] = []
    raw_calibrations = raw.get("calibrations")
    if isinstance(raw_calibrations, list):
        for index, entry in enumerate(raw_calibrations):
            calibration = _lenient_calibration(entry, index)
            if calibration is not None:
                calibrations.append(calibration)

    tip_dates_raw = raw.get("tip_dates")
    tip_dates: Dict[str, Any] = dict(tip_dates_raw) if isinstance(tip_dates_raw, dict) else {}

    control = ControlConfig()
    control_raw = raw.get("control")
    if isinstance(control_raw, dict):
        newick_raw = control_raw.get("newick")
        control = ControlConfig(
            beast_version=str(control_raw.get("beast_version") or "1.10.4"),
            initial_tree=str(control_raw.get("initial_tree") or "coalescent"),
            newick=newick_raw if isinstance(newick_raw, str) else None,
        )

    return BEASTConfig(
        metadata={"analysis_name": path.stem},
        partitions=partitions,
        all_alignments=alignments,
        calibrations=calibrations,
        control=control,
        tip_dates=tip_dates,
    )


def check_config_file(
    path: Any,
    *,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
) -> Dict[str, Any]:
    """Run the full data check against a config file path (CLI entry point).

    The strict Gate-0 parser runs first.  When it rejects the config, the
    rejection itself becomes a ``config-parse-error`` finding and every check
    re-runs against a lenient reconstruction of the file, so the specific data
    problems are still reported with precise locations.

    Raises:
        C.ContractError: the path does not exist or is not a file.
    """
    from .config import ConfigError, ConfigParser

    config_path = C.resolve_existing(path, what="config file")
    threshold = _validate_threshold(ambiguity_threshold)
    parse_crash: Optional[BaseException] = None
    config: Optional[BEASTConfig] = None
    try:
        config = ConfigParser().parse(str(config_path))
    except ConfigError as exc:
        parse_failure: Any = exc
    except Exception as exc:  # noqa: BLE001 - a parser crash still gets a report
        parse_crash = exc
        parse_failure = exc
    if config is None:
        if parse_crash is not None:
            findings: List[Dict[str, Any]] = [
                _finding(
                    "internal-error",
                    f"config parsing failed unexpectedly: "
                    f"{type(parse_crash).__name__}: {parse_crash}",
                    fix=(
                        "This is a BeastXPy bug: re-run with --json to capture "
                        "the report and file it; the findings below come from a "
                        "lenient re-read of the same file."
                    ),
                    location=_loc(config_path),
                )
            ]
        else:
            findings = [
                _finding(
                    "config-parse-error",
                    f"Gate 0 rejected this config: {parse_failure}",
                    fix=(
                        "Fix the reported problem(s) and re-run check-data; the "
                        "findings below come from a lenient re-read of the same "
                        "file and pinpoint the offending data."
                    ),
                    location=_loc(config_path),
                )
            ]
        lenient = build_lenient_config(config_path)
        if lenient is not None:
            findings.extend(
                collect_findings(lenient, config_path=config_path, ambiguity_threshold=threshold)
            )
        findings = C.sort_findings(findings)
        counts = C.count_severities(findings)
        return C.envelope(
            C.SCHEMA_DATACHECK,
            config_path=C.display_path(config_path),
            verdict=C.verdict_from_counts(counts),
            counts=counts,
            findings=findings,
        )
    return check_config(config, config_path=config_path, ambiguity_threshold=threshold)
