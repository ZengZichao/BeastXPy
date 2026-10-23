"""User-supplied XML fragments spliced into a generated BEAST document (F8).

BeastXPy generates a complete BEAST 1 XML document from a YAML config.  Some
users need one extra object in it -- a taxon set for a new calibration check,
a read-only statistic, a standalone prior definition.  ``custom_fragments``
splices such snippets into the **final XML text**; it never touches
:mod:`beastxpy.model`, so the generator's own contract is unchanged.

The feature is **off by default**.  It becomes active only when the config sets
``custom_fragments.enabled: true`` or the command line passes
``--allow-custom-fragments`` (see :func:`fragments_enabled`).

What this module guarantees
---------------------------

* Fragments are parsed with :mod:`defusedxml` and are pre-scanned lexically, so
  DTDs, entity declarations, external references, processing instructions,
  comments and CDATA sections are refused instead of being expanded or
  silently dropped.  An XXE payload therefore cannot read a file.
* ``file:`` values are resolved and confined to a base directory (normally the
  config's directory).  Absolute paths, ``..`` traversal and symlinks that
  leave the base directory are rejected.
* Only allow-listed elements and attributes survive; the MCMC, operator,
  logger and likelihood blocks are excluded so a fragment can sit *next to*
  BeastXPy's machinery but never replace or re-define it.
* Ids stay unique, ``idref``s stay resolvable, and the anchor matches exactly
  once.
* Every spliced document carries ``requires_gate3: true`` and a ``custom``
  provenance record.  There is no code path that returns a spliced document
  without them, and a validation error means **no document at all**.

What this module does *not* claim
---------------------------------

It does not decide whether the spliced document is a valid BEAST analysis.
BeastXPy deliberately does not re-implement BEAST's semantics: only Gate 3
(the official BEAST jars) can do that, which is exactly why fragments force it.

Public API (all pure functions, no argparse)::

    fragments_enabled(config, cli_flag=False) -> bool
    parse_spec(raw, base_dir=None, cli_flag=False) -> dict
    validate_fragments(spec, xml_text) -> dict      # beastxpy.fragments/v1
    apply_fragments(xml_text, spec) -> str          # raises C.ContractError
    splice_document(xml_text, spec) -> dict         # xml + report + provenance
    provenance_record(report) -> dict
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple
from xml.sax.saxutils import escape

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as defused_fromstring

from . import _contracts as C

__all__ = [
    "FRAGMENT_ALLOWED_ATTRIBUTES",
    "FRAGMENT_ALLOWED_ELEMENT_ATTRIBUTES",
    "FRAGMENT_ALLOWED_ELEMENTS",
    "FRAGMENT_BLOCKED_CONSTRUCT_ELEMENTS",
    "FRAGMENT_CORE_BLOCK_ELEMENTS",
    "FRAGMENT_POSITIONS",
    "FRAGMENT_PROTECTED_ANCHOR_ELEMENTS",
    "FRAGMENT_REFERENCE_ONLY_ELEMENTS",
    "apply_fragments",
    "fragments_enabled",
    "parse_spec",
    "provenance_record",
    "splice_document",
    "validate_fragments",
]

#: Marker so a spec produced by :func:`parse_spec` is recognised as parsed.
SPEC_VERSION = "beastxpy.fragments.spec/v1"

#: Anchor positions a fragment may request.
FRAGMENT_POSITIONS: Tuple[str, str, str] = ("before", "after", "inside")

#: Indent unit BeastXPy writes with; used to re-indent a spliced fragment.
FRAGMENT_INDENT = "    "

#: A fragment is a snippet, not a payload.  Larger inputs are refused so a
#: config cannot smuggle a whole second analysis (or a data file) into the XML.
MAX_FRAGMENT_BYTES = 64 * 1024

# ---------------------------------------------------------------------------
# The allow-list
# ---------------------------------------------------------------------------
#
# ``FRAGMENT_ALLOWED_ELEMENT_ATTRIBUTES`` maps every element a fragment may
# contain to the attributes that element may carry.  It is deliberately *tiny*
# and deliberately *inert*: each entry either declares data BEAST ignores
# unless something references it (a taxon set, a parameter), reads a value out
# of an object that already exists (a statistic), or defines a distribution
# that only takes effect if a ``<prior>`` references it -- and a fragment can
# never reach ``<prior>``.
#
# Every one of these elements was checked against the official BEAST 1.10.4
# and BEAST X 10.5.0 jars in ``java-core`` mode; see ``tests/test_fragments.py``
# and the module docstring of that file for what actually passed.
#
# DELIBERATELY EXCLUDED, and why:
#
#   * The analysis machinery -- ``mcmc``, ``operators`` and every operator
#     element (``scaleOperator``, ``subtreeSlide``, ``narrowExchange``,
#     ``wideExchange``, ``wilsonBalding``, ``uniformOperator``,
#     ``upDownOperator``, ``deltaExchange``, ``randomWalkOperator``, ...),
#     ``posterior``, ``prior``, ``likelihood``, ``booleanLikelihood``,
#     ``speciationLikelihood``, ``treeLikelihood``, ``report``, ``property``,
#     ``marginalLikelihoodEstimator``, ``pathSamplingAnalysis``,
#     ``steppingStoneSamplingAnalysis``.  These *are* BeastXPy's promise to
#     the user: the posterior being sampled, the operator schedule and the
#     marginal-likelihood machinery.  A fragment that could add, redefine or
#     re-parent them would silently change the analysis while the recorded
#     fingerprint still described the original one.  Listed in
#     ``FRAGMENT_CORE_BLOCK_ELEMENTS`` and reported as ``fragment-core-block``
#     rather than as an anonymous "unknown element".
#   * Logging -- ``log``, ``logger``, ``logTree``, ``column``.  They carry
#     ``fileName``/``logEvery``/``overwrite``, i.e. they write files that
#     BeastXPy's output manifest and sidecars know nothing about, and they are
#     the only way to make an analysis lie about what it sampled.
#   * Data and model definitions -- ``alignment``, ``sequence``, ``patterns``,
#     ``convert``, ``siteModel``, ``frequencyModel``, ``HKYModel``/``GTRModel``
#     and friends, ``yuleModel``, ``birthDeathModel``, ``constantSize``,
#     ``coalescentSimulator``, ``skyline*``, ``treeModel`` *as a definition*.
#     Re-typing or re-building the alignment/model is exactly how a fragment
#     would smuggle a Gate-0-blocked construct past Gate 0, and it would
#     invalidate the recorded data digest.  ``treeModel`` and ``statistic`` are
#     therefore allowed **only as a reference** (``idref``, never ``id``); see
#     ``FRAGMENT_REFERENCE_ONLY_ELEMENTS``.
#   * Anything executable or file-loading -- ``script``, ``import``,
#     ``include``, ``plugin``, ``extension``, ``object`` with a ``class``,
#     ``java``, ``bean``.  BeastXPy has no business running user code from a
#     config file.
#   * Namespaces.  ``xmlns`` and any ``prefix:name`` are refused outright,
#     because an allow-list keyed on local names cannot be enforced across a
#     namespace boundary.
#
# Excluded *attributes*, on top of the per-element sets above: ``fileName``,
# ``logEvery``, ``overwrite``, ``nexusFormat`` (write files), ``chainLength``,
# ``autoOptimize``, ``weight``, ``scaleFactor`` (drive the MCMC), ``estimate``
# (only meaningful together with an operator, which fragments cannot add),
# ``dataType`` and ``geneticCode`` (re-type an alignment -- the Gate-0-blocked
# codon GY94 route), ``partitionType`` (the Gate-0-blocked ARG route) and
# ``label``/``dp``/``sf``/``width`` (logger column cosmetics).

FRAGMENT_ALLOWED_ELEMENT_ATTRIBUTES: Dict[str, FrozenSet[str]] = {
    # --- inert taxon sets --------------------------------------------------
    "taxa": frozenset({"id", "idref"}),
    "taxon": frozenset({"id", "idref"}),
    # --- inert numeric parameters -----------------------------------------
    "parameter": frozenset({"id", "idref", "value", "lower", "upper", "dimension"}),
    # --- read-only views of objects that already exist ---------------------
    "treeModel": frozenset({"idref"}),
    "mrca": frozenset(),
    "statistic": frozenset({"idref"}),
    "tmrcaStatistic": frozenset({"id", "includeStem"}),
    "monophylyStatistic": frozenset({"id"}),
    "treeHeightStatistic": frozenset({"id"}),
    "branchLengthStatistic": frozenset({"id"}),
    # --- standalone distributions: inert until a <prior> references them ----
    "logNormalPrior": frozenset({"id", "mean", "stdev", "offset", "meanInRealSpace"}),
    "normalPrior": frozenset({"id", "mean", "stdev", "offset"}),
    "uniformPrior": frozenset({"id", "lower", "upper", "offset"}),
    "logUniformPrior": frozenset({"id", "lower", "upper"}),
    "gammaPrior": frozenset({"id", "shape", "scale", "offset"}),
    "exponentialPrior": frozenset({"id", "mean", "offset"}),
    "inverseGammaPrior": frozenset({"id", "shape", "scale", "offset"}),
    "laplaceDistributionPrior": frozenset({"id", "mean", "scale"}),
}

#: Element names a fragment may contain.
FRAGMENT_ALLOWED_ELEMENTS: FrozenSet[str] = frozenset(FRAGMENT_ALLOWED_ELEMENT_ATTRIBUTES)

#: Attribute names a fragment may use (union of the per-element sets).
FRAGMENT_ALLOWED_ATTRIBUTES: FrozenSet[str] = frozenset(
    attr for attrs in FRAGMENT_ALLOWED_ELEMENT_ATTRIBUTES.values() for attr in attrs
)

#: Elements that may only *point at* an existing object.  Giving one an ``id``
#: would define (or redefine) core machinery, so it is refused.
FRAGMENT_REFERENCE_ONLY_ELEMENTS: FrozenSet[str] = frozenset({"treeModel", "statistic"})

#: BeastXPy's own machinery.  Never allowed inside a fragment, and never a
#: legal ``position: inside`` anchor (a fragment may sit *next to* these).
FRAGMENT_CORE_BLOCK_ELEMENTS: FrozenSet[str] = frozenset(
    {
        "mcmc",
        "operators",
        "operator",
        "log",
        "logger",
        "logTree",
        "column",
        "report",
        "property",
        "posterior",
        "prior",
        "likelihood",
        "booleanLikelihood",
        "speciationLikelihood",
        "treeLikelihood",
        "marginalLikelihoodEstimator",
        "pathSamplingAnalysis",
        "steppingStoneSamplingAnalysis",
    }
)

#: Elements whose *contents* define the data or the model.  A fragment may be
#: placed before or after them, but never inside them: inserting into an
#: alignment, a site model or a tree prior changes the analysis instead of
#: observing it.
FRAGMENT_PROTECTED_ANCHOR_ELEMENTS: FrozenSet[str] = frozenset(
    {
        "alignment",
        "sequence",
        "patterns",
        "taxa",
        "siteModel",
        "treeModel",
        "yuleModel",
        "birthDeathModel",
        "constantSize",
        "coalescentSimulator",
        "frequencyModel",
        "HKYModel",
        "GTRModel",
    }
)

#: Elements that re-introduce a construct Gate 0 hard-rejects.  Codon GY94 and
#: ARG recombination are blocked by two independent upstream BEAST parser
#: defects each; a custom fragment is not a route around that, and BeastXPy
#: never presents a shim as official BEAST support.
FRAGMENT_BLOCKED_CONSTRUCT_ELEMENTS: Dict[str, str] = {
    "yangCodonModel": "codon GY94",
    "codonModel": "codon GY94",
    "convert": 'codon GY94 (<convert dataType="codon">)',
    "argTreeModel": "ARG recombination",
    "argTreeLikelihood": "ARG recombination",
    "argSwapOperator": "ARG recombination",
    "argEventOperator": "ARG recombination",
    "argUniformPrior": "ARG recombination",
    "argModel": "ARG recombination",
    "uniformPartitionLikelihood": "ARG recombination",
    "coalescentTree": "ARG recombination",
    "nodeRates": "ARG recombination",
}

#: Attribute *names* that belong to a Gate-0-blocked construct.
FRAGMENT_BLOCKED_ATTRIBUTES: Dict[str, str] = {
    "geneticCode": "codon GY94",
    "partitionType": "ARG recombination",
    "dataType": "alignment re-typing (codon GY94 route)",
}

# ---------------------------------------------------------------------------
# Lexical patterns refused before any parser sees the text
# ---------------------------------------------------------------------------

_RE_XML_DECL = re.compile(r"^\s*<\?xml\b[^>]*\?>", re.IGNORECASE)
_RE_PI = re.compile(r"<\?")
_RE_COMMENT = re.compile(r"<!--")
_RE_CDATA = re.compile(r"<!\[CDATA\[", re.IGNORECASE)
_RE_DOCTYPE = re.compile(r"<!DOCTYPE", re.IGNORECASE)
_RE_ENTITY = re.compile(r"<!ENTITY", re.IGNORECASE)
_RE_ENCODED_TRAVERSAL = re.compile(r"%2e|%2f|%5c", re.IGNORECASE)
_RE_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_ATTR_ESCAPE = {'"': "&quot;", "'": "&apos;"}


class _ScanError(ValueError):
    """The offset scanner could not make sense of the text."""


class _Span:
    """Character offsets of one element in the source text."""

    __slots__ = (
        "tag",
        "open_start",
        "open_end",
        "close_start",
        "close_end",
        "parent_index",
        "self_closing",
    )

    def __init__(
        self,
        tag: str,
        open_start: int,
        open_end: int,
        self_closing: bool,
        parent_index: Optional[int],
    ) -> None:
        self.tag = tag
        self.open_start = open_start
        self.open_end = open_end
        self.close_start: Optional[int] = None
        self.close_end: Optional[int] = None
        self.self_closing = self_closing
        self.parent_index = parent_index

    @property
    def end(self) -> int:
        """Offset just past the element (its end tag, or its start tag)."""
        return self.close_end if self.close_end is not None else self.open_end


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Offset scanner: ElementTree cannot tell us *where* an element lives in the
# text, and re-serialising the whole document would throw away the header
# fingerprint comment.  So the tree comes from ElementTree and the offsets come
# from this scanner; the two are cross-checked element by element.
# ---------------------------------------------------------------------------


def _read_start_tag(text: str, start: int) -> Tuple[str, int, bool]:
    """Read ``<name ...>`` at ``start``; return (name, offset past it, self-closing)."""
    size = len(text)
    index = start + 1
    while index < size and text[index] not in " \t\r\n/>":
        index += 1
    name = text[start + 1 : index]
    if not name:
        raise _ScanError(f"empty element name at offset {start}")
    quote = ""
    while index < size:
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        if char == ">":
            return name, index + 1, text[index - 1 : index] == "/"
        index += 1
    raise _ScanError(f"unterminated start tag <{name}>")


def _scan_spans(text: str) -> List[_Span]:
    """Return every element's offsets in document order, skipping markup noise."""
    spans: List[_Span] = []
    stack: List[int] = []
    index = 0
    size = len(text)
    while index < size:
        if text[index] != "<":
            index += 1
            continue
        if text.startswith("<!--", index):
            end = text.find("-->", index + 4)
            if end < 0:
                raise _ScanError("unterminated comment")
            index = end + 3
            continue
        if text.startswith("<![CDATA[", index):
            end = text.find("]]>", index + 9)
            if end < 0:
                raise _ScanError("unterminated CDATA section")
            index = end + 3
            continue
        if text.startswith("<?", index):
            end = text.find("?>", index + 2)
            if end < 0:
                raise _ScanError("unterminated processing instruction")
            index = end + 2
            continue
        if text.startswith("</", index):
            end = text.find(">", index + 2)
            if end < 0:
                raise _ScanError("unterminated end tag")
            name = text[index + 2 : end].strip()
            if not stack:
                raise _ScanError(f"unexpected </{name}> with no open element")
            span = spans[stack.pop()]
            if span.tag != name:
                raise _ScanError(f"mismatched </{name}> for <{span.tag}>")
            span.close_start = index
            span.close_end = end + 1
            index = end + 1
            continue
        if text.startswith("<!", index):
            end = text.find(">", index + 2)
            if end < 0:
                raise _ScanError("unterminated declaration")
            index = end + 1
            continue
        name, end, self_closing = _read_start_tag(text, index)
        spans.append(_Span(name, index, end, self_closing, stack[-1] if stack else None))
        if not self_closing:
            stack.append(len(spans) - 1)
        index = end
    if stack:
        raise _ScanError(f"unclosed <{spans[stack[-1]].tag}>")
    return spans


class _Document:
    """The parsed document plus the offsets of each of its elements."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.root = defused_fromstring(text)
        self.elements: List[ET.Element] = list(self.root.iter())
        self.spans: List[_Span] = _scan_spans(text)
        if len(self.spans) != len(self.elements):
            raise _ScanError(
                f"element count mismatch: scanner saw {len(self.spans)}, "
                f"ElementTree saw {len(self.elements)}"
            )
        for position, (span, element) in enumerate(zip(self.spans, self.elements)):
            if span.tag != element.tag:
                raise _ScanError(
                    f"element #{position} disagrees: scanner saw <{span.tag}>, "
                    f"ElementTree saw <{element.tag}>"
                )

    def ids(self) -> Dict[str, List[int]]:
        """Map every ``id`` to the positions of the elements declaring it."""
        found: Dict[str, List[int]] = {}
        for position, element in enumerate(self.elements):
            value = element.get("id")
            if value is not None:
                found.setdefault(value, []).append(position)
        return found

    def idrefs(self) -> List[Tuple[int, str]]:
        """Every ``(position, idref)`` pair in document order."""
        out: List[Tuple[int, str]] = []
        for position, element in enumerate(self.elements):
            value = element.get("idref")
            if value is not None:
                out.append((position, value))
        return out

    def subtree_size(self, position: int) -> int:
        """Number of elements in the subtree rooted at ``position`` (inclusive)."""
        span = self.spans[position]
        start, stop = span.open_start, span.end
        return sum(1 for item in self.spans if start <= item.open_start < stop)

    def indent_of(self, position: int) -> Tuple[int, str]:
        """Return ``(insert_offset, indent_string)`` for the element's own line."""
        span = self.spans[position]
        line_start = self.text.rfind("\n", 0, span.open_start) + 1
        prefix = self.text[line_start : span.open_start]
        if prefix.strip() == "":
            return line_start, prefix
        return span.open_start, ""

    def content_offset(self, position: int) -> int:
        """Offset of the line break that precedes this element's end tag.

        Splicing at this offset keeps the closing tag on its own line and the
        new content indented one level deeper than the element itself.
        """
        span = self.spans[position]
        if span.close_start is None:
            return span.open_end
        index = span.close_start
        while index > span.open_end and self.text[index - 1] in " \t":
            index -= 1
        if index > span.open_end and self.text[index - 1] == "\n":
            index -= 1
        return index


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


def fragments_enabled(config: Any, *, cli_flag: bool = False) -> bool:
    """Decide whether custom fragments are active.

    Off by default.  Active when the config block sets ``enabled: true`` **or**
    the command line passed ``--allow-custom-fragments``.  ``config`` may be a
    :class:`~beastxpy.models.BEASTConfig` or a plain mapping, so the decision
    stays testable without building a whole config.
    """
    block: Any = None
    if isinstance(config, dict):
        block = config.get("custom_fragments", config if "fragments" in config else None)
    else:
        block = getattr(config, "custom_fragments", None)
    if not isinstance(block, dict):
        block = {}
    return bool(cli_flag) or bool(block.get("enabled", False))


def _require(condition: bool, message: str, fix: str) -> None:
    if not condition:
        raise C.ContractError(message, fix=fix)


def _clean_position(value: Any, where: str) -> str:
    position = "after" if value is None else str(value)
    _require(
        position in FRAGMENT_POSITIONS,
        f"{where}.position: unknown {position!r}",
        fix=f"Use one of {', '.join(FRAGMENT_POSITIONS)}.",
    )
    return position


def _resolve_fragment_file(value: Any, base_dir: Path, where: str) -> Path:
    """Resolve a fragment ``file:`` value and confine it to ``base_dir``.

    Refuses absolute paths, drive letters, UNC shares, ``~``, ``..`` traversal
    and percent-encoded traversal, then resolves symlinks and re-checks that
    the real path is still inside ``base_dir``.
    """
    raw = str(value)
    _require(bool(raw.strip()), f"{where}.file: is empty", fix="Give the fragment a file name.")
    _require(
        not any(ord(char) < 32 for char in raw),
        f"{where}.file: contains a control character",
        fix="Use a plain relative file name inside the config directory.",
    )
    _require(
        not _RE_ENCODED_TRAVERSAL.search(raw),
        f"{where}.file: {raw!r} looks percent-encoded",
        fix="Pass the literal relative path; encoded separators are refused.",
    )
    _require(
        not raw.startswith("~"),
        f"{where}.file: {raw!r} uses a home-directory shortcut",
        fix="Use a path relative to the config directory.",
    )
    unified = raw.replace("\\", "/")
    _require(
        not unified.startswith("//") and not _RE_WINDOWS_DRIVE.match(raw),
        f"{where}.file: {raw!r} is an absolute path or network share",
        fix=f"Use a path relative to the config directory ({C.display_path(base_dir)}).",
    )
    pure = PurePosixPath(unified)
    _require(
        not pure.is_absolute(),
        f"{where}.file: {raw!r} is an absolute path",
        fix=f"Use a path relative to the config directory ({C.display_path(base_dir)}).",
    )
    _require(
        ".." not in pure.parts,
        f"{where}.file: {raw!r} escapes the config directory",
        fix=(
            f"Copy the fragment into {C.display_path(base_dir)} (or a directory "
            "below it) and reference it by relative path."
        ),
    )
    resolved_base = base_dir.resolve()
    candidate = (base_dir / pure).resolve()
    _require(
        _is_within(candidate, resolved_base),
        f"{where}.file: {raw!r} resolves outside the config directory",
        fix=(
            f"Keep fragment files below {C.display_path(resolved_base)}; a symlink "
            "or junction pointing elsewhere is refused."
        ),
    )
    return candidate


def _is_within(candidate: Path, base: Path) -> bool:
    try:
        candidate.relative_to(base)
    except ValueError:
        return False
    return True


def _parse_entry(raw: Any, index: int, base_dir: Path) -> Dict[str, Any]:
    where = f"custom_fragments.fragments[{index}]"
    _require(
        isinstance(raw, dict),
        f"{where}: must be a mapping",
        fix="Write each fragment as a YAML mapping with 'name', 'xml' or 'file', and an anchor.",
    )
    entry: Dict[str, Any] = dict(raw)
    known = {"name", "file", "xml", "anchor", "anchor_id", "anchor_tag", "position"}
    unknown = sorted(str(key) for key in entry if key not in known)
    _require(
        not unknown,
        f"{where}: unknown key(s) {', '.join(unknown)}",
        fix=f"Known keys are {', '.join(sorted(known))}.",
    )
    file_value = entry.get("file")
    xml_value = entry.get("xml")
    _require(
        (file_value is None) != (xml_value is None),
        f"{where}: needs exactly one of 'file' or inline 'xml'",
        fix="Set 'xml: |' for an inline fragment or 'file: name.xml' for an external one.",
    )
    anchor_id = entry.get("anchor_id")
    anchor_tag = entry.get("anchor_tag")
    _require(
        (anchor_id is None) != (anchor_tag is None),
        f"{where}: needs exactly one of 'anchor_id' or 'anchor_tag'",
        fix="Use anchor_id for one specific element, or anchor_tag when the tag is unique.",
    )
    name = entry.get("name")
    if name is None:
        name = f"fragment[{index}]"
    _require(
        bool(isinstance(name, str) and name.strip()),
        f"{where}.name: must be a non-empty string",
        fix="Give the fragment a short name; it is recorded in the provenance.",
    )
    anchor_label = entry.get("anchor")
    _require(
        anchor_label is None or isinstance(anchor_label, str),
        f"{where}.anchor: must be a string label",
        fix="'anchor' is a free-text note recorded in the provenance; drop it or make it a string.",
    )
    return {
        "index": index,
        "name": str(name),
        "label": anchor_label,
        "source": "file" if file_value is not None else "inline",
        "file": str(file_value) if file_value is not None else None,
        "file_path": (
            _resolve_fragment_file(file_value, base_dir, where) if file_value is not None else None
        ),
        "xml": str(xml_value) if xml_value is not None else None,
        "anchor_id": str(anchor_id) if anchor_id is not None else None,
        "anchor_tag": str(anchor_tag) if anchor_tag is not None else None,
        "position": _clean_position(entry.get("position"), where),
    }


def parse_spec(
    raw: Any,
    *,
    base_dir: Optional[Any] = None,
    cli_flag: bool = False,
) -> Dict[str, Any]:
    """Normalise the Gate-0-checked ``custom_fragments`` block into a spec.

    Gate 0 (:meth:`beastxpy.config.ConfigParser._parse_custom_fragments`) has
    already shape-checked the block when it came from a config file; this
    function re-checks the shape for callers that build a spec directly, adds
    the defaults, and resolves every ``file:`` value against ``base_dir``
    (the config's directory in normal use, the working directory otherwise)
    with the traversal guards described in :func:`_resolve_fragment_file`.

    Raises :class:`beastxpy._contracts.ContractError` (always with a fix) when
    the block is malformed or a fragment path escapes ``base_dir``.
    """
    block: Any = {} if raw is None else raw
    _require(
        isinstance(block, dict),
        "custom_fragments: must be a mapping",
        fix="Write 'custom_fragments:' as a YAML mapping with 'enabled' and 'fragments'.",
    )
    unknown = sorted(str(key) for key in block if key not in {"enabled", "fragments"})
    _require(
        not unknown,
        f"custom_fragments: unknown key(s) {', '.join(unknown)}",
        fix="Known keys are 'enabled' and 'fragments'.",
    )
    config_enabled = block.get("enabled", False)
    if config_enabled is None:
        config_enabled = False
    _require(
        isinstance(config_enabled, bool),
        f"custom_fragments.enabled: expected a boolean, got {config_enabled!r}",
        fix="Write 'enabled: true' or 'enabled: false' (no quotes).",
    )
    raw_fragments = block.get("fragments") or []
    _require(
        isinstance(raw_fragments, list),
        "custom_fragments.fragments: must be a list",
        fix="Write 'fragments:' as a YAML list of mappings.",
    )
    base = Path(base_dir).expanduser() if base_dir is not None else Path.cwd()
    entries = [_parse_entry(item, index, base) for index, item in enumerate(raw_fragments)]
    return {
        "spec_version": SPEC_VERSION,
        "enabled": bool(config_enabled) or bool(cli_flag),
        "config_enabled": bool(config_enabled),
        "cli_allowed": bool(cli_flag),
        "base_dir": C.display_path(base),
        "fragments": entries,
    }


def _ensure_spec(spec: Any) -> Dict[str, Any]:
    if isinstance(spec, dict) and spec.get("spec_version") == SPEC_VERSION:
        return spec
    return parse_spec(spec)


# ---------------------------------------------------------------------------
# Fragment text handling
# ---------------------------------------------------------------------------


def _lexical_findings(text: str, where: str) -> List[Dict[str, Any]]:
    """Refuse the markup tricks a tree parser would silently drop or expand."""
    findings: List[Dict[str, Any]] = []

    def reject(match: Optional[Any], code: str, what: str, fix: str) -> None:
        if match is not None:
            findings.append(
                C.finding(
                    code,
                    C.SEVERITY_ERROR,
                    f"{where}: the fragment contains {what}",
                    category="xml-injection",
                    location={"fragment": where},
                    fix=fix,
                )
            )

    reject(
        _RE_DOCTYPE.search(text),
        "fragment-doctype",
        "a <!DOCTYPE> declaration",
        fix=(
            "Remove the DOCTYPE. BeastXPy refuses internal and external DTDs so an "
            "entity expansion can never read a file; a fragment must be plain XML."
        ),
    )
    reject(
        _RE_ENTITY.search(text),
        "fragment-entity",
        "an <!ENTITY> declaration",
        fix=(
            "Remove the entity declaration and inline the literal value. External and "
            "parameter entities (XXE) are refused, not expanded."
        ),
    )
    body = _RE_XML_DECL.sub("", text, count=1)
    reject(
        _RE_PI.search(body),
        "fragment-processing-instruction",
        "a processing instruction such as <?xml-stylesheet?>",
        fix=(
            "Remove the processing instruction. ElementTree drops it, so it would vanish "
            "silently from the spliced document; if you need a stylesheet, post-process "
            "the XML after Gate 3 instead."
        ),
    )
    reject(
        _RE_COMMENT.search(text),
        "fragment-comment",
        "an XML comment",
        fix=(
            "Remove the comment. Comments are dropped when a fragment is spliced, so a "
            "comment written to close and re-open an element would change the document "
            "without leaving a trace."
        ),
    )
    reject(
        _RE_CDATA.search(text),
        "fragment-cdata",
        "a CDATA section",
        fix=(
            "Remove the CDATA wrapper and escape the text instead (&lt; &gt; &amp;). A "
            "']]>' sequence is how a CDATA breakout is attempted."
        ),
    )
    return findings


def _parse_fragment(text: str, where: str) -> Tuple[Optional[ET.Element], List[Dict[str, Any]]]:
    """Parse a fragment with entities and DTDs forbidden."""
    try:
        element = defused_fromstring(
            text, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except DefusedXmlException as exc:
        return None, [
            C.finding(
                "fragment-unsafe-xml",
                C.SEVERITY_ERROR,
                f"{where}: refused by the safe XML parser ({type(exc).__name__}: {exc})",
                category="xml-injection",
                location={"fragment": where},
                fix=(
                    "Remove the DTD/entity/external reference from the fragment. "
                    "BeastXPy parses fragments with defusedxml and never expands entities."
                ),
            )
        ]
    except ET.ParseError as exc:
        return None, [
            C.finding(
                "fragment-not-well-formed",
                C.SEVERITY_ERROR,
                f"{where}: the fragment is not well-formed XML ({exc})",
                category="xml",
                location={"fragment": where},
                fix=(
                    "Fix the fragment so it is a single well-formed element; an XML "
                    'editor or `python -c "import xml.etree.ElementTree as E; '
                    "E.fromstring(open('f.xml',encoding='utf-8').read())\"` shows the position."
                ),
            )
        ]
    if not isinstance(element.tag, str):
        return None, [
            C.finding(
                "fragment-not-well-formed",
                C.SEVERITY_ERROR,
                f"{where}: the fragment has no single root element",
                category="xml",
                location={"fragment": where},
                fix="Wrap the fragment in exactly one root element, e.g. <taxa id='...'>.",
            )
        ]
    return element, []


def _walk_fragment(
    element: ET.Element,
    where: str,
    findings: List[Dict[str, Any]],
    declared: List[str],
    references: List[Tuple[int, str]],
    depth: int = 0,
    counter: Optional[List[int]] = None,
) -> None:
    """Allow-list every element/attribute in a fragment; collect ids and idrefs."""
    order = counter if counter is not None else [0]
    position = order[0]
    order[0] += 1
    tag = element.tag
    location = {"fragment": where, "element": tag, "depth": depth}

    if not isinstance(tag, str) or ":" in tag or "{" in tag:
        findings.append(
            C.finding(
                "fragment-namespace",
                C.SEVERITY_ERROR,
                f"{where}: <{tag}> uses a namespace",
                category="allow-list",
                location=location,
                fix=(
                    "Remove the namespace and the xmlns declaration. The allow-list is "
                    "keyed on plain BEAST element names and cannot be enforced across a "
                    "namespace boundary."
                ),
            )
        )
        return

    blocked = _blocked_construct(tag, element.attrib)
    if blocked is not None:
        construct, detail = blocked
        findings.append(
            C.finding(
                "fragment-blocked-construct",
                C.SEVERITY_ERROR,
                f"{where}: <{tag}> re-introduces {construct}, which Gate 0 hard-rejects "
                f"({detail})",
                category="gate0-bypass",
                location=location,
                fix=(
                    f"Remove <{tag}>. {construct} is blocked because the official BEAST "
                    "1.10.4/10.5.0 parsers reject it; a custom fragment is not a route "
                    "around Gate 0 and BeastXPy does not ship a shim. Run "
                    "`beastxpy capabilities` for the recorded status and reason."
                ),
            )
        )
        return

    if tag in FRAGMENT_CORE_BLOCK_ELEMENTS:
        findings.append(
            C.finding(
                "fragment-core-block",
                C.SEVERITY_ERROR,
                f"{where}: <{tag}> is one of BeastXPy's own core blocks",
                category="core-block",
                location=location,
                fix=(
                    f"A fragment may sit next to <{tag}> but may never define, redefine or "
                    f"re-parent it. Remove <{tag}> and, if you need to change the MCMC, the "
                    "operators or a logger, set it in the config instead."
                ),
            )
        )
        return

    if tag not in FRAGMENT_ALLOWED_ELEMENTS:
        findings.append(
            C.finding(
                "fragment-unknown-element",
                C.SEVERITY_ERROR,
                f"{where}: <{tag}> is not in the fragment element allow-list",
                category="allow-list",
                location=location,
                value=sorted(FRAGMENT_ALLOWED_ELEMENTS),
                fix=(
                    f"Remove <{tag}> or express it with an allow-listed element. The "
                    "allow-list covers inert taxon sets, parameters, read-only statistics "
                    "and standalone prior distributions; see "
                    "beastxpy.fragments.FRAGMENT_ALLOWED_ELEMENTS."
                ),
            )
        )
        return

    allowed_attrs = FRAGMENT_ALLOWED_ELEMENT_ATTRIBUTES[tag]
    for name in element.attrib:
        if ":" in name or "{" in name:
            findings.append(
                C.finding(
                    "fragment-namespace",
                    C.SEVERITY_ERROR,
                    f"{where}: attribute {name!r} on <{tag}> uses a namespace",
                    category="allow-list",
                    location=location,
                    fix="Remove the namespaced attribute (and any xmlns declaration).",
                )
            )
            continue
        blocked_attr = _blocked_attribute(tag, name, element.attrib[name])
        if blocked_attr is not None:
            findings.append(
                C.finding(
                    "fragment-blocked-construct",
                    C.SEVERITY_ERROR,
                    f"{where}: attribute {name!r} on <{tag}> belongs to {blocked_attr}, "
                    "which Gate 0 hard-rejects",
                    category="gate0-bypass",
                    location=location,
                    fix=(
                        f"Remove the {name!r} attribute. {blocked_attr} is blocked at Gate 0 "
                        "and a custom fragment must not become a back door for it."
                    ),
                )
            )
            continue
        if name not in allowed_attrs:
            findings.append(
                C.finding(
                    "fragment-unknown-attribute",
                    C.SEVERITY_ERROR,
                    f"{where}: attribute {name!r} is not allowed on <{tag}>",
                    category="allow-list",
                    location=location,
                    value=sorted(allowed_attrs),
                    fix=(
                        f"Remove {name!r}. <{tag}> accepts "
                        f"{', '.join(sorted(allowed_attrs)) or 'no attributes'}."
                    ),
                )
            )

    if tag in FRAGMENT_REFERENCE_ONLY_ELEMENTS and element.get("id") is not None:
        findings.append(
            C.finding(
                "fragment-core-block-definition",
                C.SEVERITY_ERROR,
                f"{where}: <{tag} id={element.get('id')!r}> would define a new {tag}",
                category="core-block",
                location=location,
                fix=(
                    f"<{tag}> may only appear as a reference (idref) inside a fragment. "
                    f'Point at the one BeastXPy generated, e.g. <{tag} idref="..."/>.'
                ),
            )
        )

    value = element.get("id")
    if value is not None:
        declared.append(value)
    reference = element.get("idref")
    if reference is not None:
        references.append((position, reference))

    for child in element:
        _walk_fragment(child, where, findings, declared, references, depth + 1, order)


def _blocked_construct(tag: str, attrib: Dict[str, str]) -> Optional[Tuple[str, str]]:
    """Return ``(construct, detail)`` when a fragment element is a Gate-0 block."""
    if tag in FRAGMENT_BLOCKED_CONSTRUCT_ELEMENTS:
        return ("a Gate-0-blocked construct", FRAGMENT_BLOCKED_CONSTRUCT_ELEMENTS[tag])
    lowered = tag.lower()
    if "codon" in lowered:
        return ("a Gate-0-blocked construct", f"codon GY94 (<{tag}>)")
    if lowered.startswith("arg"):
        return ("a Gate-0-blocked construct", f"ARG recombination (<{tag}>)")
    data_type = attrib.get("dataType", "")
    if "codon" in data_type.lower():
        return ("a Gate-0-blocked construct", f'codon GY94 (dataType="{data_type}")')
    if attrib.get("partitionType", "").lower() in {"recombination", "arg"}:
        return ("a Gate-0-blocked construct", "ARG recombination (partitionType)")
    return None


def _blocked_attribute(tag: str, name: str, value: str) -> Optional[str]:
    if name in FRAGMENT_BLOCKED_ATTRIBUTES:
        return FRAGMENT_BLOCKED_ATTRIBUTES[name]
    if name == "dataType" and "codon" in str(value).lower():
        return "codon GY94"
    return None


def _serialize_element(element: ET.Element, level: int = 0) -> str:
    """Serialise an allow-listed fragment element with BeastXPy's indentation."""
    pad = FRAGMENT_INDENT * level
    attributes = "".join(
        ' {}="{}"'.format(name, escape(str(value), _ATTR_ESCAPE))
        for name, value in element.attrib.items()
    )
    text = (element.text or "").strip()
    children = list(element)
    if not children and not text:
        return f"{pad}<{element.tag}{attributes}/>"
    if not children:
        return f"{pad}<{element.tag}{attributes}>{escape(text)}</{element.tag}>"
    lines = [f"{pad}<{element.tag}{attributes}>"]
    if text:
        lines.append(f"{FRAGMENT_INDENT * (level + 1)}{escape(text)}")
    for child in children:
        lines.append(_serialize_element(child, level + 1))
    lines.append(f"{pad}</{element.tag}>")
    return "\n".join(lines)


def _reindent(body: str, indent: str) -> str:
    if not indent:
        return body
    return "\n".join(indent + line if line else line for line in body.split("\n"))


def _load_fragment_text(entry: Dict[str, Any], where: str) -> Tuple[Optional[str], List[Any]]:
    """Read an inline or file-backed fragment; report instead of raising."""
    if entry.get("source") == "inline":
        return entry.get("xml"), []
    path: Optional[Path] = entry.get("file_path")
    if path is None:
        return None, [
            C.finding(
                "fragment-file-escape",
                C.SEVERITY_ERROR,
                f"{where}: the fragment file path was not resolved",
                category="path-traversal",
                location={"fragment": where, "file": entry.get("file")},
                fix="Give 'file' a relative path inside the config directory.",
            )
        ]
    if not path.is_file():
        return None, [
            C.finding(
                "fragment-file-missing",
                C.SEVERITY_ERROR,
                f"{where}: fragment file not found: {C.display_path(path)}",
                category="path",
                location={"fragment": where, "file": entry.get("file")},
                fix=(
                    f"Create {C.display_path(path)} or point 'file' at an existing fragment "
                    "inside the config directory."
                ),
            )
        ]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, [
            C.finding(
                "fragment-file-unreadable",
                C.SEVERITY_ERROR,
                f"{where}: cannot read {C.display_path(path)} ({exc})",
                category="path",
                location={"fragment": where, "file": entry.get("file")},
                fix="Check the file permissions, or inline the fragment with 'xml: |'.",
            )
        ]
    if len(raw) > MAX_FRAGMENT_BYTES:
        return None, [
            C.finding(
                "fragment-too-large",
                C.SEVERITY_ERROR,
                f"{where}: the fragment is {len(raw)} bytes, over the "
                f"{MAX_FRAGMENT_BYTES}-byte limit",
                category="path",
                location={"fragment": where, "file": entry.get("file")},
                fix=(
                    "A fragment is a snippet, not a second analysis. Split it, or put the "
                    "model in the config where Gate 0 can check it."
                ),
            )
        ]
    try:
        return raw.decode("utf-8"), []
    except UnicodeDecodeError as exc:
        return None, [
            C.finding(
                "fragment-file-unreadable",
                C.SEVERITY_ERROR,
                f"{where}: {C.display_path(path)} is not valid UTF-8 ({exc.reason})",
                category="path",
                location={"fragment": where, "file": entry.get("file")},
                fix="Re-save the fragment as UTF-8.",
            )
        ]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _insert(text: str, document: _Document, position: int, body: str, where: str) -> str:
    """Return ``text`` with ``body`` spliced at ``position`` in ``where``'s place."""
    span = document.spans[position]
    base, indent = document.indent_of(position)
    if span.parent_index is None and where in ("before", "after"):
        raise _ScanError(f"{where}: cannot splice relative to the document root")
    if where == "before":
        return text[:base] + _reindent(body, indent) + "\n" + text[base:]
    if where == "after":
        return text[: span.end] + "\n" + _reindent(body, indent) + text[span.end :]
    inner = indent + FRAGMENT_INDENT
    if span.self_closing:
        head = text[span.open_start : span.open_end]
        if head.endswith("/>"):
            head = head[:-2].rstrip() + ">"
        replacement = f"{head}\n{_reindent(body, inner)}\n{indent}</{span.tag}>"
        return text[: span.open_start] + replacement + text[span.open_end :]
    offset = document.content_offset(position)
    return text[:offset] + "\n" + _reindent(body, inner) + text[offset:]


def _evaluate(
    spec: Dict[str, Any],
    xml_text: str,
) -> Tuple[List[Dict[str, Any]], Optional[str], List[Dict[str, Any]]]:
    """Run every check and (when all pass) build the spliced document.

    Returns ``(findings, spliced_text_or_None, per_fragment_records)``.  The
    inputs are never mutated.
    """
    findings: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    entries: Sequence[Dict[str, Any]] = list(spec.get("fragments") or [])

    if entries and not spec.get("enabled"):
        findings.append(
            C.finding(
                "fragments-disabled",
                C.SEVERITY_ERROR,
                f"{len(entries)} custom fragment(s) are configured but custom_fragments "
                "is disabled",
                category="policy",
                fix=(
                    "Set 'custom_fragments.enabled: true' in the config, or pass "
                    "--allow-custom-fragments on the command line. Fragments are off by "
                    "default and always force Gate 3."
                ),
            )
        )
        return findings, None, records

    try:
        document = _Document(xml_text)
    except (ET.ParseError, _ScanError) as exc:
        findings.append(
            C.finding(
                "document-not-well-formed",
                C.SEVERITY_ERROR,
                f"the document handed to the fragment splicer is not well-formed ({exc})",
                category="xml",
                fix=(
                    "Regenerate the XML with `beastxpy generate` before applying custom "
                    "fragments; the splicer only accepts a document BeastXPy produced."
                ),
            )
        )
        return findings, None, records

    if not entries:
        return findings, xml_text, records

    prepared = [
        _prepare_fragment(entry, f"fragment {entry['name']!r}", findings) for entry in entries
    ]
    first_declarer: Dict[str, int] = {}
    for index, item in enumerate(prepared):
        for value in (item or {}).get("declared", []):
            first_declarer.setdefault(value, index)

    known_ids = document.ids()
    text = xml_text
    spliced_count = 0
    applied_ids: Set[str] = set()

    for index, entry in enumerate(entries):
        where = f"fragment {entry['name']!r}"
        mark = len(findings)
        record: Dict[str, Any] = {
            "name": entry["name"],
            "source": entry["source"],
            "file": entry.get("file"),
            "label": entry.get("label"),
            "anchor_id": entry.get("anchor_id"),
            "anchor_tag": entry.get("anchor_tag"),
            "position": entry["position"],
            "status": "rejected",
            "ids": [],
            "idrefs": [],
            "sha256": None,
        }
        records.append(record)

        item = prepared[index]
        if item is None:
            continue
        record["sha256"] = item["sha256"]
        record["ids"] = list(item["declared"])
        record["idrefs"] = [value for _position, value in item["references"]]

        for value in item["declared"]:
            if value in known_ids:
                findings.append(
                    C.finding(
                        "fragment-duplicate-id",
                        C.SEVERITY_ERROR,
                        f"{where}: id {value!r} already exists in the document",
                        category="identity",
                        location={"fragment": where, "id": value},
                        fix=(
                            f"Rename the fragment's id (for example 'custom.{value}'). A "
                            "fragment may never re-declare an id BeastXPy already emitted."
                        ),
                    )
                )
            elif first_declarer.get(value) != index:
                findings.append(
                    C.finding(
                        "fragment-duplicate-id",
                        C.SEVERITY_ERROR,
                        f"{where}: id {value!r} is also declared by "
                        f"fragment {first_declarer.get(value)!r}",
                        category="identity",
                        location={"fragment": where, "id": value},
                        fix="Give each fragment its own ids; they are merged into one document.",
                    )
                )

        if item["element"] is None:
            continue

        anchor_position = _locate_anchor(document, entry, where, findings)
        if anchor_position is None:
            continue
        if not _anchor_policy_ok(document, anchor_position, entry, where, findings, applied_ids):
            continue

        _check_references(
            document,
            anchor_position,
            entry,
            item,
            index,
            first_declarer,
            known_ids,
            where,
            findings,
        )

        if _lands_after_mcmc(document, anchor_position, entry["position"]):
            findings.append(
                C.finding(
                    "fragment-after-mcmc",
                    C.SEVERITY_WARNING,
                    f"{where}: the fragment lands after the <mcmc> block "
                    f"({_anchor_description(entry)}, position {entry['position']!r})",
                    category="beast-order",
                    location={"fragment": where},
                    fix=(
                        "BEAST's parser stops once it has built the MCMC object, so this "
                        "fragment is never constructed: it cannot change the analysis, but "
                        "Gate 3 does not look at it either. Anchor the fragment before "
                        "<mcmc> (for example anchor_id 'speciation' or the id of any "
                        "element above the operators block) if you want it validated."
                    ),
                )
            )

        if any(entry_finding["severity"] == C.SEVERITY_ERROR for entry_finding in findings[mark:]):
            continue

        try:
            text = _insert(
                text,
                document,
                anchor_position,
                _serialize_element(item["element"]),
                entry["position"],
            )
            document = _Document(text)
        except (ET.ParseError, _ScanError) as exc:
            findings.append(
                C.finding(
                    "fragment-result-not-well-formed",
                    C.SEVERITY_ERROR,
                    f"{where}: the document is not well-formed after splicing ({exc})",
                    category="xml",
                    location={"fragment": where},
                    fix=(
                        "The fragment broke the document structure. Check that it is a "
                        "single element and that the anchor is a whole element."
                    ),
                )
            )
            return findings, None, records

        known_ids = document.ids()
        record["status"] = "applied"
        spliced_count += 1
        findings.append(
            C.finding(
                "fragment-applied",
                C.SEVERITY_INFO,
                f"{where}: spliced {entry['position']} "
                f"{_anchor_description(entry)} as a custom fragment",
                category="provenance",
                location={"fragment": where},
                value={"sha256": record["sha256"]},
                fix=(
                    "No action needed; the spliced document must still pass Gate 3 against "
                    "the official BEAST jars before BeastXPy writes it."
                ),
            )
        )

    if any(item["severity"] == C.SEVERITY_ERROR for item in findings):
        return findings, None, records

    final_findings = _final_identity_checks(document, set(first_declarer))
    findings.extend(final_findings)
    if final_findings:
        return findings, None, records

    findings.append(
        C.finding(
            "fragments-gate3-required",
            C.SEVERITY_INFO,
            f"{spliced_count} custom fragment(s) applied; the document is marked 'custom' "
            "and Gate 3 (official BEAST validation) is now mandatory",
            category="policy",
            value={"requires_gate3": True},
            fix=(
                "Run `beastxpy generate --beast-validate` (or `beastxpy validate --beast`). "
                "BeastXPy refuses to write a spliced document that Gate 3 did not pass."
            ),
        )
    )
    return findings, text, records


def _final_identity_checks(document: _Document, added: Set[str]) -> List[Dict[str, Any]]:
    """Defence in depth: no added id may appear twice, no idref may dangle."""
    findings: List[Dict[str, Any]] = []
    ids = document.ids()
    for value in sorted(added):
        if len(ids.get(value, [])) > 1:
            findings.append(
                C.finding(
                    "fragment-duplicate-id",
                    C.SEVERITY_ERROR,
                    f"id {value!r} appears {len(ids[value])} times in the spliced document",
                    category="identity",
                    location={"id": value},
                    fix="Rename the fragment's id so it is unique in the whole document.",
                )
            )
    known = set(ids)
    for position, value in document.idrefs():
        if value in known:
            continue
        findings.append(
            C.finding(
                "fragment-unresolved-idref",
                C.SEVERITY_ERROR,
                f"idref {value!r} on <{document.elements[position].tag}> does not resolve "
                "in the spliced document",
                category="identity",
                location={"idref": value},
                fix=(
                    "Point the idref at an id that exists; available ids: "
                    f"{_sample(sorted(known))}."
                ),
            )
        )
    return findings


def _anchor_description(entry: Dict[str, Any]) -> str:
    if entry.get("anchor_id") is not None:
        return f"anchor id={entry['anchor_id']!r}"
    return f"anchor tag=<{entry.get('anchor_tag')}>"


def _sample(values: Sequence[str], limit: int = 12) -> str:
    shown = ", ".join(repr(value) for value in values[:limit])
    if len(values) > limit:
        shown += f", ... ({len(values)} total)"
    return shown or "(none)"


def _locate_anchor(
    document: _Document,
    entry: Dict[str, Any],
    where: str,
    findings: List[Dict[str, Any]],
) -> Optional[int]:
    """Return the single element position the fragment anchors to, or None."""
    if entry.get("anchor_id") is not None:
        wanted = entry["anchor_id"]
        matches = [
            position
            for position, element in enumerate(document.elements)
            if element.get("id") == wanted
        ]
        describe = f"id={wanted!r}"
        hint = (
            f"Add the anchor, or pick one of the ids already in the document: "
            f"{_sample(sorted(document.ids()))}."
        )
    else:
        wanted = entry.get("anchor_tag")
        matches = [
            position for position, element in enumerate(document.elements) if element.tag == wanted
        ]
        describe = f"tag=<{wanted}>"
        tags = sorted({element.tag for element in document.elements})
        hint = f"Use anchor_id instead of anchor_tag, or pick a unique tag from: {_sample(tags)}."

    if not matches:
        findings.append(
            C.finding(
                "fragment-anchor-not-found",
                C.SEVERITY_ERROR,
                f"{where}: anchor not found -- no element matches {describe}",
                category="anchor",
                location={"fragment": where, "anchor": describe},
                fix=hint,
            )
        )
        return None
    if len(matches) > 1:
        findings.append(
            C.finding(
                "fragment-anchor-ambiguous",
                C.SEVERITY_ERROR,
                f"{where}: anchor ambiguous -- {len(matches)} elements match {describe}",
                category="anchor",
                location={"fragment": where, "anchor": describe, "matches": len(matches)},
                fix=(
                    "An anchor must match exactly one element so the splice is "
                    f"deterministic. {hint}"
                ),
            )
        )
        return None
    return matches[0]


def _ancestors(document: _Document, position: int) -> List[int]:
    """Element positions of ``position``'s ancestors, nearest first."""
    chain: List[int] = []
    index = document.spans[position].parent_index
    while index is not None:
        chain.append(index)
        index = document.spans[index].parent_index
    return chain


def _is_owned(element: ET.Element, owned_ids: Set[str]) -> bool:
    """True when a fragment (not BeastXPy) introduced this element."""
    value = element.get("id")
    return value is not None and value in owned_ids


def _anchor_policy_ok(
    document: _Document,
    position: int,
    entry: Dict[str, Any],
    where: str,
    findings: List[Dict[str, Any]],
    owned_ids: Set[str],
) -> bool:
    """Refuse anchors that would let a fragment get inside core machinery.

    A fragment may sit *next to* BeastXPy's blocks, so ``before``/``after`` on
    a top-level ``<mcmc>`` or ``<operators>`` is fine.  What is not fine is
    landing *within* one -- directly, or by anchoring on one of its children --
    because that changes the analysis instead of observing it.  Elements a
    fragment introduced itself are exempt: the user owns those.
    """
    elements = document.elements
    span = document.spans[position]
    tag = elements[position].tag
    splice = entry["position"]

    if span.parent_index is None and splice in ("before", "after"):
        findings.append(
            C.finding(
                "fragment-anchor-root-sibling",
                C.SEVERITY_ERROR,
                f"{where}: cannot splice {splice} the document root <{tag}>",
                category="anchor",
                location={"fragment": where, "anchor": tag},
                fix=(
                    f"Use position 'inside' with anchor_tag '{tag}' to add a top-level "
                    "element, or anchor on a child element."
                ),
            )
        )
        return False

    for index in _ancestors(document, position):
        ancestor = elements[index]
        if _is_owned(ancestor, owned_ids):
            continue
        if ancestor.tag in FRAGMENT_CORE_BLOCK_ELEMENTS:
            findings.append(
                C.finding(
                    "fragment-anchor-core-block",
                    C.SEVERITY_ERROR,
                    f"{where}: anchoring on <{tag}> would splice the fragment inside "
                    f"<{ancestor.tag}>, one of BeastXPy's core blocks",
                    category="core-block",
                    location={"fragment": where, "anchor": tag, "enclosing": ancestor.tag},
                    fix=(
                        f"Anchor on an element outside <{ancestor.tag}> instead. A fragment "
                        "may sit next to the MCMC, the operators or a logger but may never "
                        "be placed inside them; change those in the YAML config."
                    ),
                )
            )
            return False
        if ancestor.tag in FRAGMENT_PROTECTED_ANCHOR_ELEMENTS:
            findings.append(
                C.finding(
                    "fragment-anchor-core-block",
                    C.SEVERITY_ERROR,
                    f"{where}: anchoring on <{tag}> would splice the fragment inside "
                    f"<{ancestor.tag}>, whose contents define the data or the model",
                    category="core-block",
                    location={"fragment": where, "anchor": tag, "enclosing": ancestor.tag},
                    fix=(
                        f"Anchor on an element outside <{ancestor.tag}>. Changing the "
                        "alignment, the site model or the tree prior belongs in the config, "
                        "where Gate 0 checks it and the recorded data digest stays true."
                    ),
                )
            )
            return False

    if splice != "inside" or _is_owned(elements[position], owned_ids):
        return True

    if tag in FRAGMENT_CORE_BLOCK_ELEMENTS:
        findings.append(
            C.finding(
                "fragment-anchor-core-block",
                C.SEVERITY_ERROR,
                f"{where}: refusing to splice inside <{tag}>, one of BeastXPy's core blocks",
                category="core-block",
                location={"fragment": where, "anchor": tag},
                fix=(
                    f"A fragment may sit before or after <{tag}> but never inside it: "
                    "inserting there changes the MCMC, the operator schedule or a logger "
                    "instead of observing them. Configure that in the YAML config instead."
                ),
            )
        )
        return False
    if tag in FRAGMENT_PROTECTED_ANCHOR_ELEMENTS:
        findings.append(
            C.finding(
                "fragment-anchor-core-block",
                C.SEVERITY_ERROR,
                f"{where}: refusing to splice inside <{tag}>, whose contents define the "
                "data or the model",
                category="core-block",
                location={"fragment": where, "anchor": tag},
                fix=(
                    f"Place the fragment before or after <{tag}>, or change the "
                    "data/model in the config so Gate 0 can check it and the recorded "
                    "data digest stays true."
                ),
            )
        )
        return False
    return True


def _cutoff(document: _Document, position: int, splice_position: str) -> int:
    """Element positions strictly below this are declared before the fragment."""
    if splice_position == "before":
        return position
    return position + document.subtree_size(position)


def _lands_after_mcmc(document: _Document, position: int, splice_position: str) -> bool:
    """True when the fragment would be spliced after the whole ``<mcmc>`` block.

    BEAST's ``XMLParser.parse(reader, MCMC.class)`` -- which is what Gate 3's
    instantiate stage calls -- stops as soon as it has built the MCMC object,
    so anything after ``<mcmc>`` is never constructed.  Such a fragment cannot
    change the analysis, but Gate 3 will not have looked at it either, and the
    user is told so instead of being left to assume it was validated.
    """
    mcmc = [
        index
        for index, element in enumerate(document.elements)
        if element.tag == "mcmc" and element.get("idref") is None
    ]
    if not mcmc:
        return False
    start = mcmc[0]
    end = start + document.subtree_size(start) - 1
    return _cutoff(document, position, splice_position) - 1 >= end


def _prepare_fragment(
    entry: Dict[str, Any],
    where: str,
    findings: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Load, lexically scan, parse and allow-list one fragment.

    Returns ``None`` when the fragment text could not be read at all (the
    finding says why), otherwise a dict with the parsed element, the ids it
    declares and the ``idref``s it uses, in the fragment's own document order.
    """
    raw_text, load_findings = _load_fragment_text(entry, where)
    findings.extend(load_findings)
    if raw_text is None:
        return None
    prepared: Dict[str, Any] = {
        "sha256": _sha256(raw_text),
        "element": None,
        "declared": [],
        "references": [],
    }
    if not raw_text.strip():
        findings.append(
            C.finding(
                "fragment-empty",
                C.SEVERITY_ERROR,
                f"{where}: the fragment is empty",
                category="xml",
                location={"fragment": where},
                fix="Put a single XML element in the fragment, or remove the entry.",
            )
        )
        return prepared
    findings.extend(_lexical_findings(raw_text, where))
    element, parse_findings = _parse_fragment(raw_text, where)
    findings.extend(parse_findings)
    if element is None:
        return prepared
    declared: List[str] = []
    references: List[Tuple[int, str]] = []
    _walk_fragment(element, where, findings, declared, references)
    prepared["element"] = element
    prepared["declared"] = declared
    prepared["references"] = references
    return prepared


def _check_references(
    document: _Document,
    position: int,
    entry: Dict[str, Any],
    item: Dict[str, Any],
    index: int,
    first_declarer: Dict[str, int],
    known_ids: Dict[str, List[int]],
    where: str,
    findings: List[Dict[str, Any]],
) -> None:
    """Every ``idref`` must resolve *and* be declared before the fragment.

    BEAST instantiates a document in order and refuses an ``idref`` whose
    target has not been declared yet ("Object with idref=... has not been
    previously declared").  BeastXPy cannot fix that by moving the fragment --
    the anchor decides where it goes -- so a forward reference is refused here
    rather than being discovered by Gate 3.
    """
    cutoff = _cutoff(document, position, entry["position"])
    declared: List[str] = list(item["declared"])
    order_fix = (
        "BEAST instantiates elements in document order and rejects an idref whose target is "
        "declared later. Anchor the fragment after that element (for example position "
        "'inside' with anchor_tag 'beast', or 'after' with a later anchor_id)."
    )
    for reference_position, value in item["references"]:
        if value in declared and declared.index(value) < reference_position:
            continue
        if value in declared:
            findings.append(
                C.finding(
                    "fragment-forward-reference",
                    C.SEVERITY_ERROR,
                    f"{where}: idref {value!r} is declared later inside the same fragment",
                    category="beast-order",
                    location={"fragment": where, "idref": value},
                    fix=order_fix,
                )
            )
            continue
        owner = first_declarer.get(value)
        if owner is not None:
            if owner < index:
                continue
            findings.append(
                C.finding(
                    "fragment-forward-reference",
                    C.SEVERITY_ERROR,
                    f"{where}: idref {value!r} is declared by "
                    f"custom_fragments.fragments[{owner}], which is spliced later",
                    category="beast-order",
                    location={"fragment": where, "idref": value},
                    fix=(
                        "Reorder custom_fragments.fragments so the fragment that declares "
                        f"{value!r} comes first, or merge the two fragments. " + order_fix
                    ),
                )
            )
            continue
        declared_at = known_ids.get(value)
        if not declared_at:
            findings.append(
                C.finding(
                    "fragment-unresolved-idref",
                    C.SEVERITY_ERROR,
                    f"{where}: idref {value!r} does not resolve in the spliced document",
                    category="identity",
                    location={"fragment": where, "idref": value},
                    fix=(
                        "Point the idref at an id that exists. Ids already in the document: "
                        f"{_sample(sorted(known_ids))}."
                    ),
                )
            )
            continue
        if declared_at[0] >= cutoff:
            findings.append(
                C.finding(
                    "fragment-forward-reference",
                    C.SEVERITY_ERROR,
                    f"{where}: idref {value!r} points at "
                    f"<{document.elements[declared_at[0]].tag}>, which is declared after the "
                    f"splice point ({_anchor_description(entry)}, position "
                    f"{entry['position']!r})",
                    category="beast-order",
                    location={"fragment": where, "idref": value},
                    fix=order_fix,
                )
            )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _error_summary(findings: Sequence[Dict[str, Any]]) -> Tuple[str, str]:
    errors = [item for item in findings if item["severity"] == C.SEVERITY_ERROR]
    ordered = C.sort_findings(errors)
    messages = "; ".join(str(item["message"]) for item in ordered[:5])
    if len(ordered) > 5:
        messages += f"; ... ({len(ordered)} errors total)"
    fix = str(ordered[0].get("fix") or "") if ordered else ""
    return messages, fix


def provenance_record(report: Dict[str, Any]) -> Dict[str, Any]:
    """The provenance block the controller records next to a spliced document.

    ``custom_fragments`` is the list the contract asks for: one entry per
    applied fragment, each marked ``custom`` with the anchor, the position and
    the SHA-256 of the exact text that was spliced.
    """
    entries = [
        {
            "kind": "custom",
            "name": item["name"],
            "source": item["source"],
            "file": item.get("file"),
            "anchor_id": item.get("anchor_id"),
            "anchor_tag": item.get("anchor_tag"),
            "position": item["position"],
            "sha256": item.get("sha256"),
        }
        for item in report.get("fragments", [])
        if item.get("status") == "applied"
    ]
    return {
        "custom_fragments": entries,
        "requires_gate3": bool(report.get("requires_gate3", True)),
        "marked": "custom" if entries else "generated",
    }


def _build_report(
    spec: Dict[str, Any],
    findings: List[Dict[str, Any]],
    spliced: Optional[str],
    records: List[Dict[str, Any]],
    xml_text: str,
) -> Dict[str, Any]:
    counts = C.count_severities(findings)
    applied = [item for item in records if item.get("status") == "applied"]
    requires_gate3 = bool(spec.get("enabled")) or bool(applied) or bool(records)
    report: Dict[str, Any] = {
        "enabled": bool(spec.get("enabled")),
        "config_enabled": bool(spec.get("config_enabled")),
        "cli_allowed": bool(spec.get("cli_allowed")),
        "base_dir": spec.get("base_dir"),
        "fragment_count": len(records),
        "applied_count": len(applied),
        "verdict": C.verdict_from_counts(counts),
        "counts": counts,
        "findings": C.sort_findings(findings),
        "fragments": records,
        "requires_gate3": requires_gate3,
        "spliced": spliced is not None,
        "result_sha256": _sha256(spliced) if spliced is not None else None,
        "source_sha256": _sha256(xml_text),
        "provenance": {
            "custom_fragments": [
                {
                    "kind": "custom",
                    "name": item["name"],
                    "source": item["source"],
                    "file": item.get("file"),
                    "anchor_id": item.get("anchor_id"),
                    "anchor_tag": item.get("anchor_tag"),
                    "position": item["position"],
                    "sha256": item.get("sha256"),
                }
                for item in applied
            ],
            "requires_gate3": requires_gate3,
            "marked": "custom" if applied else "generated",
        },
        "limitations": [
            "BeastXPy checks well-formedness, ids, idrefs, the element/attribute "
            "allow-list and the anchor; only Gate 3 (the official BEAST jars) can decide "
            "whether the spliced document is a valid BEAST analysis.",
            "A document containing custom fragments always requires Gate 3 and is always "
            "marked 'custom' in provenance; BeastXPy never writes one unvalidated.",
            "The allow-list deliberately excludes the MCMC, operator, logger, likelihood "
            "and alignment blocks, so a fragment can sit next to BeastXPy's machinery but "
            "can never replace it.",
            "Codon GY94 and ARG recombination stay hard-rejected at Gate 0; a fragment "
            "that names them is refused, and no shim is offered as official BEAST support.",
            "BEAST's parser stops once it has built the MCMC object, so a fragment spliced "
            "after <mcmc> is never constructed. BeastXPy reports that as a "
            "'fragment-after-mcmc' warning instead of implying Gate 3 covered it.",
        ],
    }
    return report


def validate_fragments(spec: Any, xml_text: str) -> Dict[str, Any]:
    """Return a ``beastxpy.fragments/v1`` envelope of findings.

    Never mutates ``spec`` or ``xml_text`` and never returns a document: this
    is the read-only view.  Use :func:`apply_fragments` (or
    :func:`splice_document`, which also returns the provenance record) to get
    the spliced XML.  A malformed spec block is reported as a finding rather
    than raised, so a caller always gets an envelope back.
    """
    try:
        parsed = _ensure_spec(spec)
    except C.ContractError as exc:
        parsed = {
            "spec_version": SPEC_VERSION,
            "enabled": False,
            "config_enabled": False,
            "cli_allowed": False,
            "base_dir": None,
            "fragments": [],
        }
        findings = [
            C.finding(
                "fragment-spec-invalid",
                C.SEVERITY_ERROR,
                f"custom_fragments is malformed: {exc.message}",
                category="policy",
                fix=exc.fix,
            )
        ]
        return C.envelope(C.SCHEMA_FRAGMENTS, **_build_report(parsed, findings, None, [], xml_text))
    findings, spliced, records = _evaluate(parsed, xml_text)
    return C.envelope(
        C.SCHEMA_FRAGMENTS, **_build_report(parsed, findings, spliced, records, xml_text)
    )


def splice_document(xml_text: str, spec: Any) -> Dict[str, Any]:
    """Validate and splice; the only way this module hands out XML.

    Returns ``{"xml", "report", "requires_gate3", "provenance"}``.
    ``requires_gate3`` is always ``True`` here, because the only way to reach
    the returned XML is through a spec that turned the feature on.  Raises
    :class:`beastxpy._contracts.ContractError` -- with the first error's fix --
    when validation fails: **no document is ever returned unvalidated**.
    """
    parsed = _ensure_spec(spec)
    findings, spliced, records = _evaluate(parsed, xml_text)
    report = C.envelope(
        C.SCHEMA_FRAGMENTS, **_build_report(parsed, findings, spliced, records, xml_text)
    )
    if spliced is None:
        message, fix = _error_summary(findings)
        if not message:
            message = "custom fragments were not applied"
            fix = (
                "Enable them with 'custom_fragments.enabled: true' or "
                "--allow-custom-fragments, and give each fragment an anchor."
            )
        raise C.ContractError(
            f"refusing to generate: {message}",
            fix=fix or "Fix the fragment and regenerate; see the findings for each error.",
        )
    payload: Dict[str, Any] = {
        "xml": spliced,
        "report": report,
        "requires_gate3": True,
        "provenance": provenance_record(report),
    }
    if not payload["requires_gate3"]:  # pragma: no cover - unreachable by construction
        raise C.ContractError(
            "internal error: a spliced document was produced without requires_gate3",
            fix="Report this as a BeastXPy bug; never write the document.",
        )
    return payload


def apply_fragments(xml_text: str, spec: Any) -> str:
    """Return ``xml_text`` with every fragment spliced in.

    Raises :class:`beastxpy._contracts.ContractError` (always carrying a fix)
    when validation fails, when the feature is disabled, or when the spliced
    result would not be well-formed.  A caller that gets a string back still
    owes Gate 3: see :func:`splice_document` for the ``requires_gate3`` flag
    and the ``custom`` provenance record.
    """
    return str(splice_document(xml_text, spec)["xml"])
