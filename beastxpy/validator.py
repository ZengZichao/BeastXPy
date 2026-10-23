"""Validation subsystem for BeastXPy (Gate 1 pure Python + Gate 3 BEAST).

Gate 1 (:class:`XMLValidator`) is a structural and semantic validator over a
BEAST 1 XML document.  Its element vocabulary is generated from real evidence
(``beastxpy/data/xml_spec.json``, see ``tools/patch_xml_spec.py``): parser
names from the official BEAST jars/source trees, element names accepted by
parser syntax rules, and every element name occurring in the official example
corpora of BEAST 1.10.4 and BEAST X 10.5.0.  Hand-written ``EXTRA`` lists are
not used, so names such as ``generalizedSkyline`` or ``bayesianSkyline`` are
reported as unknown.

Gate 1 checks:

* well-formedness (``defusedxml``), root ``<beast>``, unique ids, resolvable
  and *backward* ``idref`` references (declaration must precede use),
* element whitelist and version gate (1.10.4 / 10.5.0 / ``both`` = intersection),
* required attributes (operators, ``<mcmc chainLength>``) and numeric ranges,
* per-dimension parameter bounds, vector lengths and ``dimension`` agreement,
* ``<patterns from/to>`` ranges against the referenced alignment length,
* ``<alignment dataType>`` enumeration (``amino acid`` with a space),
* ``<date value/units/direction>`` requirements,
* GTR exactly five rateXX children (or a six-value ``<rates>`` vector),
* RLC child names, skyline ``groupSizes``, EBSP ``variableDemographic``,
* estimated-parameter detection from operator/prior cross references
  (bounds alone never make a parameter estimated).

Gate 3 wraps ``BeastXValidator`` (Java) and reports a four-state result:
``executed-pass``, ``executed-fail``, ``not-run`` or ``environment-missing``.
Availability of Java is established by executing the discovered runtime and
parsing ``java.version``; vendor strings are never used as a gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_BEAST_VERSIONS = ("1.10.4", "10.5.0")
TARGET_VERSIONS = ("1.10.4", "10.5.0", "both")
VALIDATE_PREFIX = "BXP-VALIDATE|"

GATE3_EXECUTED_PASS = "executed-pass"
GATE3_EXECUTED_FAIL = "executed-fail"
GATE3_NOT_RUN = "not-run"
GATE3_ENVIRONMENT_MISSING = "environment-missing"
GATE3_STATUSES = (
    GATE3_EXECUTED_PASS,
    GATE3_EXECUTED_FAIL,
    GATE3_NOT_RUN,
    GATE3_ENVIRONMENT_MISSING,
)

_OPERATOR_EXTRA_TAGS = {
    "deltaExchange",
    "subtreeSlide",
    "narrowExchange",
    "wideExchange",
    "wilsonBalding",
    "upDownOperator",
    "subtreeLeap",
    "subtreeSwap",
    "treeStretchOperator",
    "treeBitMoveOperator",
    "nodeReheight",
    "formattedNarrowExchange",
}

_PRIOR_LIKELIHOOD_TAGS = {
    "prior",
    "coalescentLikelihood",
    "speciationLikelihood",
    "birthDeathLikelihood",
    "yuleLikelihood",
}

_CLOCK_CHILD_TAGS = {
    "strictClockBranchRates",
    "discretizedBranchRates",
    "arbitraryBranchRates",
    "localClockModel",
    "randomLocalClockModel",
    "uncorrelatedExponential",
    "uncorrelatedLogNormal",
}

_GTR_RATE_TAGS = ("rateAC", "rateAG", "rateAT", "rateCG", "rateCT", "rateGT")

# Handles are filled by ``_load_spec``.
_SPEC: Optional[Dict[str, Any]] = None
_ELEMENT_INDEX: Optional[Dict[str, Dict[str, Any]]] = None
_ELEMENT_CASEFOLD_INDEX: Optional[Dict[str, List[str]]] = None


def _spec_path() -> Path:
    return Path(__file__).parent / "data" / "xml_spec.json"


def _load_spec() -> Dict[str, Any]:
    global _SPEC, _ELEMENT_INDEX, _ELEMENT_CASEFOLD_INDEX
    if _SPEC is None:
        try:
            with open(_spec_path(), "r", encoding="utf-8") as handle:
                _SPEC = json.load(handle)
        except (OSError, json.JSONDecodeError):
            _SPEC = {"elements": {}, "provenance": {}, "structure": {}}
        elements = _SPEC.get("elements") if isinstance(_SPEC, dict) else {}
        _ELEMENT_INDEX = {
            str(name): entry for name, entry in (elements or {}).items() if isinstance(entry, dict)
        }
        _ELEMENT_CASEFOLD_INDEX = {}
        for name in _ELEMENT_INDEX:
            _ELEMENT_CASEFOLD_INDEX.setdefault(name.casefold(), []).append(name)
    return _SPEC


def _element_entry(tag: str) -> Optional[Dict[str, Any]]:
    _load_spec()
    assert _ELEMENT_INDEX is not None
    return _ELEMENT_INDEX.get(tag)


def _case_variant_names(tag: str) -> List[str]:
    """Known element names that differ from ``tag`` only by letter case."""
    _load_spec()
    assert _ELEMENT_CASEFOLD_INDEX is not None
    return sorted(name for name in _ELEMENT_CASEFOLD_INDEX.get(tag.casefold(), []) if name != tag)


def _entry_versions(entry: Dict[str, Any]) -> List[str]:
    versions = entry.get("versions")
    if isinstance(versions, list) and versions:
        return [str(v) for v in versions]
    since = entry.get("since")
    if since == "both":
        return ["1.10.4", "10.5.0"]
    if isinstance(since, str) and since in VALID_BEAST_VERSIONS:
        return [since]
    return []


def spec_summary() -> Dict[str, Any]:
    """Return a compact, machine-readable description of the loaded spec."""
    spec = _load_spec()
    provenance = spec.get("provenance") or {}
    elements = spec.get("elements") or {}
    return {
        "schema": spec.get("schema"),
        "version": spec.get("version"),
        "elements": len(elements),
        "counts": provenance.get("counts", {}),
        "project_only": (provenance.get("project") or {}).get("project_only_tags", []),
        "renames": provenance.get("renames", {}),
        "removed_in_10_5_0": provenance.get("removed_in_10_5_0", []),
    }


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass
class ValidationFinding:
    """A single validation finding.

    Attributes:
        severity: ``"error"`` or ``"warning"``.
        path: Full XML element path (``beast/.../parameter[id=...]``) or file.
        message: Human-readable description.
        code: Stable machine-readable code (used by tests and the CLI).
    """

    severity: str
    path: str
    message: str
    code: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
            "code": self.code,
        }


def _severity_sort_key(finding: ValidationFinding) -> Tuple[int, str, str]:
    return (0 if finding.severity == "error" else 1, finding.path, finding.message)


class XMLValidationError(ValueError):
    """Raised when a document fails Gate 1 (all findings collected below)."""

    def __init__(self, findings: List[ValidationFinding]) -> None:
        self.findings = findings
        super().__init__(
            "; ".join(f"{f.path}: {f.message}" for f in findings if f.severity == "error")
        )


# ---------------------------------------------------------------------------
# Gate 1 validator
# ---------------------------------------------------------------------------


class XMLValidator:
    """Pure-Python Gate 1 validator over BEAST 1 XML documents."""

    def __init__(self, beast_version: str = "1.10.4", strict: bool = True) -> None:
        if beast_version not in TARGET_VERSIONS:
            raise ValueError(
                f"beast_version must be one of {TARGET_VERSIONS}, got {beast_version!r}"
            )
        self.beast_version = beast_version
        self.strict = strict
        self.findings: List[ValidationFinding] = []
        self._paths: List[Tuple[Any, str]] = []
        self._parent: Dict[int, Any] = {}
        self._path_by_id: Dict[int, str] = {}

    # ------------------------------------------------------------------
    def validate_file(self, path: str) -> List[ValidationFinding]:
        """Validate an XML file; returns a (possibly empty) finding list."""
        with open(path, "rb") as handle:
            raw = handle.read()
        return self.validate_bytes(raw, source=str(path))

    def validate_string(self, xml_str: str, source: str = "<string>") -> List[ValidationFinding]:
        """Validate an XML document string; returns the finding list."""
        return self.validate_bytes(xml_str.encode("utf-8"), source=source)

    def validate_bytes(self, raw: bytes, source: str = "<bytes>") -> List[ValidationFinding]:
        self.findings = []
        try:
            root = self._parse(raw, source)
        except XMLValidationError as exc:
            return exc.findings
        self._index_tree(root)
        self._validate_structure(root, source)
        self._validate_semantics(root, source)
        self.findings.sort(key=_severity_sort_key)
        return list(self.findings)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    # ------------------------------------------------------------------
    # Parsing / tree indexing
    # ------------------------------------------------------------------
    def _parse(self, raw: bytes, source: str):
        from defusedxml.ElementTree import fromstring

        try:
            return fromstring(raw)
        except Exception as exc:  # defusedxml raises several exception types
            self.findings.append(
                ValidationFinding(
                    severity="error",
                    path=source,
                    message=f"malformed XML: {exc}",
                    code="XML_WELLFORMED",
                )
            )
            raise XMLValidationError(self.findings) from exc

    def _index_tree(self, root) -> None:
        """Build a real parent map and a path for every element.

        ``xml.etree.ElementTree.Element`` has no ``getparent()``; walking the
        tree recursively is the only portable way to report full ancestor
        paths such as ``beast/treeModel/parameter[id=...]``.
        """
        self._paths = []
        self._parent = {}
        self._path_by_id = {}

        def visit(element, parent: Optional[Any], prefix: str) -> None:
            if parent is not None:
                self._parent[id(element)] = parent
            name = element.tag
            node_id = element.get("id")
            if node_id:
                name = f"{name}[id={node_id}]"
            path = f"{prefix}/{name}" if prefix else name
            self._paths.append((element, path))
            self._path_by_id[id(element)] = path
            for child in list(element):
                visit(child, element, path)

        visit(root, None, "")

    def _path(self, element) -> str:
        return self._path_by_id.get(id(element), element.tag)

    def _error(self, path: str, message: str, code: str) -> None:
        self.findings.append(
            ValidationFinding(severity="error", path=path, message=message, code=code)
        )

    def _warn(self, path: str, message: str, code: str) -> None:
        if self.strict:
            self.findings.append(
                ValidationFinding(severity="warning", path=path, message=message, code=code)
            )

    # ------------------------------------------------------------------
    # Structure: ids, references, parameter arithmetic
    # ------------------------------------------------------------------
    def _validate_structure(self, root, source: str) -> None:
        if root.tag != "beast":
            self._error(
                source,
                f"root element must be <beast>, got <{root.tag}>",
                "ROOT_ELEMENT",
            )
            return

        declared: Dict[str, str] = {}
        declarations: Dict[str, str] = {}
        references: List[Tuple[str, str, int]] = []
        later_declarations: Dict[str, List[int]] = {}

        for order, (element, path) in enumerate(self._paths):
            node_id = element.get("id")
            node_idref = element.get("idref")
            if node_id is not None:
                if node_id in declarations:
                    self._error(
                        path,
                        f"duplicate id {node_id!r} (first declared at {declarations[node_id]})",
                        "DUP_ID",
                    )
                else:
                    declarations[node_id] = path
                declared[node_id] = path
                later_declarations.setdefault(node_id, []).append(order)
            if node_idref is not None:
                references.append((node_idref, path, order))
                extra_attrs = [a for a in element.attrib if a not in ("idref", "index")]
                if extra_attrs:
                    self._error(
                        path,
                        f"<{element.tag} idref={node_idref!r}> must not carry other "
                        f"attributes ({', '.join(sorted(extra_attrs))}); BEAST rejects this",
                        "IDREF_EXTRA_CONTENT",
                    )
                if list(element):
                    self._error(
                        path,
                        f"<{element.tag} idref={node_idref!r}> must not have child elements",
                        "IDREF_EXTRA_CONTENT",
                    )
            if element.tag == "parameter":
                self._check_parameter_attrs(element, path)

        # Two loggers writing the same file silently overwrite each other (the
        # run pipeline renames its own logger to trace.log/tree.log, so this
        # also protects custom fragments from colliding with them).
        outputs: Dict[str, str] = {}
        for element, path in self._paths:
            if element.tag not in ("log", "logTree"):
                continue
            file_name = element.get("fileName")
            if not file_name:
                continue
            key = file_name.strip()
            if not key:
                continue
            if key in outputs:
                self._error(
                    path,
                    f"duplicate logger output fileName {key!r} (already written "
                    f"by {outputs[key]}); one logger would overwrite the other",
                    "DUPLICATE_LOG_FILE",
                )
            else:
                outputs[key] = path

        for node_idref, path, order in references:
            if node_idref in declared:
                if order < later_declarations[node_idref][0]:
                    self._error(
                        path,
                        f"forward idref {node_idref!r}: BEAST parses XML in document "
                        "order and requires the object to be declared first "
                        f"(declaration is at {declarations[node_idref]})",
                        "FORWARD_IDREF",
                    )
            else:
                self._error(
                    path,
                    f"dangling idref {node_idref!r}: no element declares this id",
                    "DANGLING_IDREF",
                )

    def _check_parameter_attrs(self, element, path: str) -> None:
        """Validate numeric vectors, dimension agreement and every bound."""
        if element.get("idref") is not None:
            return
        for attr in ("value", "lower", "upper"):
            raw = element.get(attr)
            if raw is None:
                continue
            tokens = raw.split()
            if not tokens:
                self._error(path, f"<parameter {attr}=''>: empty numeric vector", "PARAM_NUMERIC")
                continue
            if not all(_is_number_token(token) for token in tokens):
                self._error(
                    path,
                    f"<parameter {attr}={raw!r}>: contains a non-numeric token",
                    "PARAM_NUMERIC",
                )
        values = _vector_floats(element.get("value"))
        lower = _vector_floats(element.get("lower"))
        upper = _vector_floats(element.get("upper"))
        dimension_raw = element.get("dimension")
        dimension = _parse_dimension(dimension_raw)
        if (
            dimension_raw is not None
            and dimension is None
            and not re.fullmatch(r"\s*\d+\s*[nN]\s*(?:[-+]\s*\d+)?\s*", dimension_raw)
        ):
            self._error(
                path,
                f"<parameter dimension={dimension_raw!r}> is neither a positive "
                "integer nor a symbolic dimension such as '2n-2'",
                "PARAM_DIMENSION",
            )
        if dimension is not None and dimension < 1:
            self._error(
                path, f"parameter dimension must be >= 1, got {dimension}", "PARAM_DIMENSION"
            )
        if values and dimension is not None and len(values) not in (1, dimension):
            # BEAST's ParameterParser broadcasts a single value across the
            # declared dimension (official testBSP.xml does this), but a
            # vector of the wrong length is a real error.
            self._error(
                path,
                f"dimension={dimension} but value has {len(values)} component(s); "
                "BEAST accepts 1 (broadcast) or exactly dimension values",
                "PARAM_DIMENSION",
            )
        if lower and len(lower) not in (1, len(values) or len(lower)):
            self._error(
                path,
                f"lower bound has {len(lower)} component(s), value has {len(values)}",
                "PARAM_LENGTH",
            )
        if upper and len(upper) not in (1, len(values) or len(upper)):
            self._error(
                path,
                f"upper bound has {len(upper)} component(s), value has {len(values)}",
                "PARAM_LENGTH",
            )
        if not values:
            return
        for index, value in enumerate(values):
            low = lower[index] if len(lower) > 1 else (lower[0] if lower else None)
            high = upper[index] if len(upper) > 1 else (upper[0] if upper else None)
            if low is not None and high is not None and low > high:
                self._error(
                    path,
                    f"component {index + 1}: lower {low} > upper {high}",
                    "PARAM_BOUNDS",
                )
            if low is not None and value < low:
                self._error(
                    path,
                    f"component {index + 1}: value {value} < lower {low}",
                    "PARAM_BOUNDS",
                )
            if high is not None and value > high:
                self._error(
                    path,
                    f"component {index + 1}: value {value} > upper {high}",
                    "PARAM_BOUNDS",
                )

    # ------------------------------------------------------------------
    # Semantics
    # ------------------------------------------------------------------
    def _validate_semantics(self, root, source: str) -> None:
        self._check_element_whitelist(root)
        self._check_required_attributes()
        self._check_patterns()
        self._check_data_types()
        self._check_dates()
        self._check_gtr()
        self._check_coalescent_models()
        self._check_random_local_clock()
        self._check_skyline()
        self._check_variable_demographic()
        self._check_model_completeness(root, source)
        self._check_coverage(root, source)

    # -- element vocabulary / version gate -----------------------------
    def _check_element_whitelist(self, root) -> None:
        spec = _load_spec()
        provenance = spec.get("provenance") or {}
        project_only = set((provenance.get("project") or {}).get("project_only_tags") or [])
        renames = provenance.get("renames") or {}
        removed = set(provenance.get("removed_in_10_5_0") or [])

        for element, path in self._paths:
            tag = element.tag
            entry = _element_entry(tag)
            if entry is None:
                variants = _case_variant_names(tag)
                if variants:
                    canonical = ", ".join(f"<{name}>" for name in variants)
                    self._error(
                        path,
                        f"element <{tag}> matches the BEAST element {canonical} only "
                        f"when letter case is ignored; BEAST parser names are "
                        f"case-sensitive and will reject <{tag}>. Use {canonical}.",
                        "ELEMENT_CASE_MISMATCH",
                    )
                    continue
                hint = ""
                if tag in project_only:
                    hint = (
                        " (BeastXPy emits this name, but neither BEAST parser store "
                        "nor the official example corpora contain it)"
                    )
                elif tag in removed:
                    hint = " (present in BEAST 1.10.4 examples but absent from 10.5.0)"
                elif tag in renames:
                    hint = f" (BEAST X 10.5.0 renamed this element to <{renames[tag]}>)"
                self._error(
                    path,
                    f"element <{tag}> is not a BEAST element in the evidence-backed "
                    f"whitelist for {self.beast_version}{hint}",
                    "UNKNOWN_ELEMENT",
                )
                continue
            versions = _entry_versions(entry)
            if self.beast_version == "both":
                missing = [v for v in VALID_BEAST_VERSIONS if v not in versions]
                if missing:
                    extra = ""
                    if renames.get(tag):
                        extra = f"; BEAST X 10.5.0 uses <{renames[tag]}> instead"
                    self._error(
                        path,
                        f"element <{tag}> is not available in {', '.join(missing)} "
                        f"(versions: {', '.join(versions) or 'none'}){extra}; "
                        "beast_version='both' requires the intersection",
                        "VERSION_GATE",
                    )
            elif self.beast_version not in versions:
                extra = ""
                if self.beast_version == "10.5.0" and tag in removed:
                    extra = " (removed or renamed in BEAST X 10.5.0)"
                if self.beast_version == "10.5.0" and renames.get(tag):
                    extra = f" (renamed to <{renames[tag]}>)"
                self._error(
                    path,
                    f"element <{tag}> is not available in BEAST {self.beast_version} "
                    f"(versions: {', '.join(versions) or 'none'}){extra}",
                    "VERSION_GATE",
                )

    # -- required attributes ------------------------------------------
    def _check_required_attributes(self) -> None:
        structure = _load_spec().get("structure") or {}
        required = structure.get("required_attributes") or {}
        for element, path in self._paths:
            if element.get("idref") is not None:
                continue
            attrs = required.get(element.tag)
            if not attrs:
                continue
            for attr in attrs:
                if element.get(attr) is None:
                    self._error(
                        path,
                        f"<{element.tag}> requires attribute {attr!r} "
                        "(BEAST parser AttributeRule is not optional)",
                        "REQUIRED_ATTRIBUTE",
                    )
            if element.tag == "randomWalkIntegerOperator":
                window = element.get("windowSize")
                if window is not None:
                    try:
                        numeric = float(window)
                    except ValueError:
                        self._error(
                            path,
                            f"windowSize={window!r} is not numeric",
                            "OPERATOR_WINDOW_SIZE",
                        )
                    else:
                        if numeric != int(numeric) or numeric <= 0:
                            self._error(
                                path,
                                f"windowSize={window!r} must be a positive integer",
                                "OPERATOR_WINDOW_SIZE",
                            )

    # -- patterns / alignment length -----------------------------------
    def _check_patterns(self) -> None:
        alignments: Dict[str, Any] = {}
        for element, _ in self._paths:
            if element.tag == "alignment" and element.get("idref") is None:
                alignment_id = element.get("id")
                if alignment_id:
                    alignments[alignment_id] = element

        for element, path in self._paths:
            if element.tag != "patterns" or element.get("idref") is not None:
                continue
            from_raw = element.get("from")
            to_raw = element.get("to")
            from_value = 1 if from_raw is None else _parse_int(from_raw)
            to_value = None if to_raw is None else _parse_int(to_raw)
            if from_raw is not None and from_value is None:
                self._error(path, f"patterns from={from_raw!r} is not an integer", "PATTERNS_RANGE")
            if to_raw is not None and to_value is None:
                self._error(path, f"patterns to={to_raw!r} is not an integer", "PATTERNS_RANGE")
            if from_value is not None and from_value < 1:
                self._error(path, f"patterns from={from_value} must be >= 1", "PATTERNS_RANGE")
            if from_value is not None and to_value is not None and to_value < from_value:
                self._error(
                    path,
                    f"patterns range {from_value}-{to_value} is empty (to < from)",
                    "PATTERNS_RANGE",
                )
            alignment_ref = None
            for child in list(element):
                if child.tag == "alignment" and child.get("idref"):
                    alignment_ref = child.get("idref")
                    break
            if alignment_ref is None:
                self._error(
                    path,
                    "<patterns> is missing its <alignment idref=...> child",
                    "PATTERNS_ALIGNMENT",
                )
                continue
            alignment = alignments.get(alignment_ref)
            if alignment is None:
                continue
            lengths = _alignment_lengths(alignment)
            if not lengths:
                continue
            length = lengths[0]
            if from_value is not None and from_value > length:
                self._error(
                    path,
                    f"patterns from={from_value} exceeds alignment {alignment_ref!r} "
                    f"length {length}",
                    "PATTERNS_RANGE",
                )
            if to_value is not None and to_value > length:
                self._error(
                    path,
                    f"patterns to={to_value} exceeds alignment {alignment_ref!r} "
                    f"length {length}",
                    "PATTERNS_RANGE",
                )

        for alignment_id, element in alignments.items():
            lengths = _alignment_lengths(element)
            if len(lengths) > 1:
                self._error(
                    self._path(element),
                    f"alignment {alignment_id!r} has sequences of differing lengths: "
                    f"{sorted(lengths)}",
                    "ALIGNMENT_LENGTH",
                )

    # -- dataType, date ------------------------------------------------
    def _check_data_types(self) -> None:
        structure = _load_spec().get("structure") or {}
        enums = structure.get("attribute_enums") or {}
        allowed = [str(value) for value in enums.get("alignment.dataType") or []]
        allowed_lower = {value.casefold(): value for value in allowed}
        for element, path in self._paths:
            if element.tag != "alignment" or element.get("idref") is not None:
                continue
            data_type = element.get("dataType")
            has_child = any(child.tag == "dataType" for child in list(element))
            if data_type is None:
                if not has_child:
                    self._error(
                        path,
                        "<alignment> requires a dataType attribute or a <dataType> child",
                        "DATATYPE_MISSING",
                    )
                continue
            value = data_type.strip()
            folded = value.casefold()
            if folded.startswith("codon-") or folded == "codon":
                continue
            if folded not in allowed_lower:
                hint = ""
                if folded in ("amino-acid", "aminoacid"):
                    hint = "; BEAST expects 'amino acid' (with a space)"
                self._error(
                    path,
                    f"dataType {value!r} is not a BEAST data type "
                    f"(allowed: {', '.join(allowed)}){hint}",
                    "DATATYPE_ENUM",
                )

    def _check_dates(self) -> None:
        structure = _load_spec().get("structure") or {}
        enums = structure.get("attribute_enums") or {}
        units_allowed = {str(v).casefold() for v in enums.get("date.units") or []}
        direction_allowed = {str(v).casefold() for v in enums.get("date.direction") or []}
        for element, path in self._paths:
            if element.tag != "date" or element.get("idref") is not None:
                continue
            if element.get("value") is None:
                self._error(path, "<date> requires a value attribute", "DATE_REQUIRED")
            units = element.get("units")
            if units is None:
                self._error(
                    path,
                    "<date> requires a units attribute (years/months/days); "
                    "BEAST X 10.5.0 raises in TimeScale.unitString otherwise",
                    "DATE_REQUIRED",
                )
            elif units_allowed and units.casefold() not in units_allowed:
                self._error(
                    path,
                    f"date units {units!r} is not one of {sorted(units_allowed)}",
                    "DATE_ENUM",
                )
            direction = element.get("direction")
            if direction is None:
                self._warn(
                    path,
                    "<date> has no direction attribute; BEAST defaults to forwards, "
                    "but generated XML should write it explicitly",
                    "DATE_DIRECTION_DEFAULT",
                )
            elif direction_allowed and direction.casefold() not in direction_allowed:
                self._error(
                    path,
                    f"date direction {direction!r} is not one of {sorted(direction_allowed)}",
                    "DATE_ENUM",
                )

    # -- substitution / clock / demographic structures -----------------
    def _check_gtr(self) -> None:
        for element, path in self._paths:
            if element.tag != "gtrModel" or element.get("idref") is not None:
                continue
            children = list(element)
            if not any(child.tag == "frequencies" for child in children):
                self._error(
                    path,
                    "<gtrModel> requires a <frequencies> child",
                    "GTR_FREQUENCIES",
                )
            rate_children = [child.tag for child in children if child.tag in _GTR_RATE_TAGS]
            rates_vector = [child for child in children if child.tag == "rates"]
            if rates_vector and rate_children:
                self._error(
                    path,
                    "<gtrModel> must not mix a <rates> vector with rateXX children",
                    "GTR_RATE_COUNT",
                )
            elif rates_vector:
                parameter = next(
                    (child for child in rates_vector[0].iter("parameter") if child.get("value")),
                    None,
                )
                dimension = (
                    _parse_dimension(parameter.get("dimension")) if parameter is not None else None
                )
                values = _vector_floats(parameter.get("value")) if parameter is not None else []
                if dimension != 6 and len(values) != 6:
                    self._error(
                        path,
                        "<gtrModel><rates> must contain a six-value parameter "
                        f"(got dimension={dimension}, {len(values)} values)",
                        "GTR_RATE_COUNT",
                    )
            elif len(rate_children) != 5:
                self._error(
                    path,
                    "BEAST GTR requires exactly five of the six rateXX elements "
                    f"(found {len(rate_children)}: {', '.join(rate_children) or 'none'}); "
                    "the sixth rate is fixed to 1.0",
                    "GTR_RATE_COUNT",
                )

    def _check_coalescent_models(self) -> None:
        """Required children of the BEAST coalescent demographic models.

        BEAST's ``LogisticGrowthModelParser`` requires ``t50`` (there is no
        ``ancestralPopulationSize``), and ``ExpansionModelParser`` requires
        ``populationSize`` + ``ancestralPopulationProportion``; both grow
        models take a XOR of ``growthRate``/``doublingTime``.
        """
        requirements = {
            "constantSize": {"populationSize"},
            "exponentialGrowth": {"populationSize"},
            "logisticGrowth": {"populationSize", "t50"},
            "expansion": {"populationSize", "ancestralPopulationProportion"},
        }
        for element, path in self._paths:
            if element.get("idref") is not None:
                continue
            required = requirements.get(element.tag)
            if required is None:
                continue
            direct = {child.tag: child for child in list(element)}
            for name in sorted(required):
                wrapper = direct.get(name)
                if wrapper is None:
                    self._error(
                        path,
                        f"<{element.tag}> requires a <{name}> child "
                        "(BEAST's parser ElementRule is not optional)",
                        "COALESCENT_CHILD",
                    )
                elif not any(child.tag == "parameter" for child in list(wrapper)):
                    self._error(
                        path,
                        f"<{element.tag}><{name}> requires a <parameter> child",
                        "COALESCENT_CHILD",
                    )
            if element.tag in ("logisticGrowth", "expansion", "exponentialGrowth"):
                growth = [name for name in ("growthRate", "doublingTime") if name in direct]
                if len(growth) != 1:
                    self._error(
                        path,
                        f"<{element.tag}> requires exactly one of <growthRate> or "
                        f"<doublingTime> (found {len(growth)})",
                        "COALESCENT_CHILD",
                    )
                for name in growth:
                    wrapper = direct[name]
                    if not any(child.tag == "parameter" for child in list(wrapper)):
                        self._error(
                            path,
                            f"<{element.tag}><{name}> requires a <parameter> child",
                            "COALESCENT_CHILD",
                        )

    def _check_random_local_clock(self) -> None:
        for element, path in self._paths:
            if element.tag != "randomLocalClockModel" or element.get("idref") is not None:
                continue
            direct = {child.tag: child for child in list(element)}
            for required in ("treeModel", "rates", "rateIndicator"):
                if required not in direct:
                    self._error(
                        path,
                        f"<randomLocalClockModel> requires a <{required}> child",
                        "RLC_CHILD",
                    )
                else:
                    wrapper = direct[required]
                    if required != "treeModel" and not any(
                        child.tag == "parameter" for child in list(wrapper)
                    ):
                        self._error(
                            path,
                            f"<randomLocalClockModel><{required}> requires a " "<parameter> child",
                            "RLC_CHILD",
                        )
            for forbidden in ("rateIndicators", "rateCategories"):
                if forbidden in direct:
                    self._error(
                        path,
                        f"<randomLocalClockModel> has no <{forbidden}> child in BEAST; "
                        "use the singular <rateIndicator> (and no rate categories)",
                        "RLC_CHILD",
                    )
            clock_rate = direct.get("clockRate")
            if clock_rate is not None and not any(
                child.tag == "parameter" for child in list(clock_rate)
            ):
                self._error(
                    path,
                    "<randomLocalClockModel><clockRate> requires a <parameter> child",
                    "RLC_CHILD",
                )

    def _check_skyline(self) -> None:
        for element, path in self._paths:
            if element.tag != "generalizedSkyLineLikelihood" or element.get("idref") is not None:
                continue
            direct = {child.tag: child for child in list(element)}
            for required in ("populationSizes", "groupSizes", "populationTree"):
                if required not in direct:
                    self._error(
                        path,
                        f"<generalizedSkyLineLikelihood> requires a <{required}> child"
                        + (
                            "; BEAST rejects skyline models without group sizes"
                            if required == "groupSizes"
                            else ""
                        ),
                        "SKYLINE_CHILD",
                    )
            pop = _first_parameter(direct.get("populationSizes"))
            groups = _first_parameter(direct.get("groupSizes"))
            pop_dim = _parse_dimension(pop.get("dimension")) if pop is not None else None
            group_dim = _parse_dimension(groups.get("dimension")) if groups is not None else None
            if pop is not None and groups is not None:
                if pop_dim is not None and group_dim is not None and pop_dim != group_dim:
                    self._error(
                        path,
                        f"skyline populationSizes dimension {pop_dim} != groupSizes "
                        f"dimension {group_dim}; each group needs one population size",
                        "SKYLINE_DIMENSION",
                    )
            tree = direct.get("populationTree")
            if tree is not None and not any(child.tag == "treeModel" for child in list(tree)):
                self._error(
                    path,
                    "<generalizedSkyLineLikelihood><populationTree> requires a "
                    "<treeModel idref=...> child",
                    "SKYLINE_CHILD",
                )

    def _check_variable_demographic(self) -> None:
        for element, path in self._paths:
            if element.tag != "variableDemographic" or element.get("idref") is not None:
                continue
            direct = {child.tag: child for child in list(element)}
            for required in ("populationSizes", "indicators", "trees"):
                if required not in direct:
                    self._error(
                        path,
                        f"<variableDemographic> requires a <{required}> child "
                        "(extended Bayesian skyline parameterisation)",
                        "EBSP_CHILD",
                    )
            for wrapper_name in ("populationSizes", "indicators"):
                wrapper = direct.get(wrapper_name)
                if wrapper is not None and not any(
                    child.tag == "parameter" for child in list(wrapper)
                ):
                    self._error(
                        path,
                        f"<variableDemographic><{wrapper_name}> requires a " "<parameter> child",
                        "EBSP_CHILD",
                    )
            trees = direct.get("trees")
            if trees is not None:
                ptrees = [child for child in list(trees) if child.tag == "ptree"]
                if not ptrees:
                    self._error(
                        path,
                        "<variableDemographic><trees> requires at least one <ptree>",
                        "EBSP_CHILD",
                    )
                for ptree in ptrees:
                    if not any(
                        child.tag == "treeModel" and child.get("idref") for child in list(ptree)
                    ):
                        self._error(
                            self._path(ptree),
                            "<ptree> requires a <treeModel idref=...> child",
                            "EBSP_CHILD",
                        )

    # -- model completeness -------------------------------------------
    def _check_model_completeness(self, root, source: str) -> None:
        patterns = [e for e in root.iter("patterns") if e.get("idref") is None]
        tree_likelihoods = [e for e in root.iter("treeLikelihood") if e.get("idref") is None]
        tree_models = [e for e in root.iter("treeModel") if e.get("idref") is None]
        if patterns and not tree_likelihoods:
            self._error(
                source,
                f"{len(patterns)} <patterns> element(s) but no <treeLikelihood>: "
                "the site model would never be evaluated",
                "MODEL_INCOMPLETE",
            )
        if tree_likelihoods and not patterns and not list(root.iter("mergePatterns")):
            self._error(
                source,
                f"{len(tree_likelihoods)} <treeLikelihood> element(s) but no "
                "<patterns>/<mergePatterns> object",
                "MODEL_INCOMPLETE",
            )
        if (patterns or tree_likelihoods) and not tree_models:
            self._error(source, "no <treeModel> definition found", "NO_TREE_MODEL")
        has_data_likelihood = any(
            element.tag.endswith(("Likelihood", "likelihood")) for element in root.iter()
        )
        if not patterns and not tree_likelihoods and not has_data_likelihood:
            self._error(
                source,
                "no likelihood object found (treeLikelihood/ancestral/trait/MDS/...); "
                "the MCMC would sample the prior only",
                "MODEL_INCOMPLETE",
            )
        if len(patterns) != len(tree_likelihoods) and patterns and tree_likelihoods:
            self._warn(
                source,
                f"partition mismatch: {len(patterns)} <patterns> vs "
                f"{len(tree_likelihoods)} <treeLikelihood>",
                "PARTITION_MISMATCH",
            )
        for tl in tree_likelihoods:
            direct = {child.tag for child in list(tl)}
            for required in ("treeModel", "siteModel"):
                if required not in direct:
                    self._error(
                        self._path(tl),
                        f"<treeLikelihood> lacks a <{required} idref=...> child",
                        "TL_REF_MISSING",
                    )
            if "patterns" not in direct and "mergePatterns" not in direct:
                self._error(
                    self._path(tl),
                    "<treeLikelihood> lacks a <patterns>/<mergePatterns> reference",
                    "TL_REF_MISSING",
                )
            if not (direct & _CLOCK_CHILD_TAGS):
                self._warn(
                    self._path(tl),
                    "<treeLikelihood> has no recognised clock/branch-rate child; "
                    "the document may rely on an implicit clock",
                    "NO_CLOCK",
                )

    # -- operator/prior coverage --------------------------------------
    def _check_coverage(self, root, source: str) -> None:
        declarations: Dict[str, str] = {}
        declared_paths: Dict[str, str] = {}
        for element, path in self._paths:
            if (
                element.tag == "parameter"
                and element.get("id") is not None
                and element.get("idref") is None
            ):
                pid = element.get("id")
                if pid:
                    declarations[pid] = path
                    declared_paths[pid] = path

        operator_targets: Set[str] = set()
        prior_targets: Set[str] = set()
        explicit_estimated: Set[str] = set()

        gmrf_operator = any(
            element.tag
            in {
                "gmrfBlockUpdateOperator",
                "gmrfSkyrideBlockUpdateOperator",
                "gmrfSkyrideFixedEffectsGibbsOperator",
            }
            for element, _ in self._paths
        )
        for pid, path in declared_paths.items():
            # A GMRF block-update operator moves the whole skyride/skygrid
            # model (population sizes, group sizes, precision); BEAST does not
            # list those targets as <parameter> children.
            if gmrf_operator and (
                "/gmrfskyridelikelihood[" in path.casefold()
                or "/gmrfskygridlikelihood[" in path.casefold()
            ):
                operator_targets.add(pid)

        for element, path in self._paths:
            tag = element.tag
            if tag == "parameter" and element.get("id") is not None:
                estimate = element.get("estimate")
                if isinstance(estimate, str) and estimate.strip().lower() in ("true", "1"):
                    explicit_estimated.add(element.get("id") or "")
            is_operator = tag in _OPERATOR_EXTRA_TAGS or (
                tag.endswith("Operator") and tag != "operators"
            )
            is_prior = (
                tag in _PRIOR_LIKELIHOOD_TAGS
                or tag.endswith("Prior")
                or tag.endswith("Distribution")
                or tag.endswith("prior")
            )
            if is_operator:
                for child in element.iter("parameter"):
                    if child.get("idref"):
                        operator_targets.add(child.get("idref") or "")
            elif is_prior:
                for child in element.iter("parameter"):
                    if child.get("idref"):
                        prior_targets.add(child.get("idref") or "")
                for child in element.iter("statistic"):
                    if child.get("idref"):
                        prior_targets.add(child.get("idref") or "")

        estimated = (operator_targets | prior_targets | explicit_estimated) & set(declarations)
        for pid in sorted(estimated):
            path = declared_paths[pid]
            if _is_tree_parameter(pid):
                # BEAST derives these from the tree operators; they are not
                # independent estimated parameters in the XML.
                continue
            if pid not in operator_targets:
                # Advisory: BEAST accepts parameters that are only moved by a
                # compound operator or that are fixed after construction.
                self._warn(
                    path,
                    f"estimated parameter {pid!r} is referenced by a prior but by no "
                    "operator (check the operator set)",
                    "NO_OPERATOR",
                )
            if pid not in prior_targets and not _has_implicit_prior(pid, path):
                self._warn(
                    path,
                    f"estimated parameter {pid!r} is referenced by an operator but by "
                    "no prior (BEAST may use an implicit prior)",
                    "NO_PRIOR",
                )

        for pid in sorted(operator_targets):
            if pid and pid not in declarations:
                self._warn(
                    source,
                    f"operator targets {pid!r} which is not declared as a parameter id "
                    "in this document",
                    "OPERATOR_TARGET_UNKNOWN",
                )


# ---------------------------------------------------------------------------
# Small helpers used by the validator
# ---------------------------------------------------------------------------


def _is_number_token(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def _vector_floats(value: Optional[str]) -> List[float]:
    if value is None:
        return []
    out: List[float] = []
    for token in value.split():
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def _parse_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _parse_dimension(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _alignment_lengths(alignment) -> List[int]:
    lengths: List[int] = []
    for sequence in alignment.iter("sequence"):
        text = sequence.text or ""
        for child in list(sequence):
            if child.tag == "text":
                text = child.text or ""
        text = "".join(text.split())
        if text:
            lengths.append(len(text))
    return lengths


def _first_parameter(wrapper) -> Optional[Any]:
    if wrapper is None:
        return None
    for child in list(wrapper):
        if child.tag == "parameter":
            return child
    return None


def _is_tree_parameter(pid: str) -> bool:
    """True for parameters BEAST derives from tree operators.

    Official examples name them ``treeModel.rootHeight`` or, for multi-locus
    documents, ``26.treeModel.rootHeight``; a prefix-only test missed the
    latter and produced false NO_PRIOR errors.
    """
    return bool(re.search(r"(?:^|\.)treeModel\.", pid))


def _has_implicit_prior(pid: str, path: str) -> bool:
    """Parameters whose prior is supplied by the enclosing likelihood.

    BEAST's RLC/category indicators and skyline population sizes do not need a
    separate ``<...Prior>`` element: the indicator prior and the demographic
    likelihood are the prior.  This exemption is deliberately narrow so a
    missing prior on an ordinary continuous parameter is still reported.
    """
    lowered = pid.casefold()
    if "indicator" in lowered or "categor" in lowered or lowered.endswith(".changes"):
        return True
    lowered_path = path.casefold()
    if (
        "/generalizedskylinelikelihood[" in lowered_path
        or "/variabledemographic[" in lowered_path
        or "/gmrfskyridelikelihood[" in lowered_path
        or "/gmrfskygridlikelihood[" in lowered_path
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Convenience Gate 1 entry points
# ---------------------------------------------------------------------------


def validate_xml_string(
    xml_str: str, beast_version: str = "1.10.4", strict: bool = True
) -> List[ValidationFinding]:
    """Validate a BEAST 1 XML string; returns the finding list (Gate 1)."""
    return XMLValidator(beast_version=beast_version, strict=strict).validate_string(xml_str)


def validate_xml_file(
    path: str, beast_version: str = "1.10.4", strict: bool = True
) -> List[ValidationFinding]:
    """Validate a BEAST 1 XML file; returns the finding list (Gate 1)."""
    return XMLValidator(beast_version=beast_version, strict=strict).validate_file(path)


# ---------------------------------------------------------------------------
# Java discovery
# ---------------------------------------------------------------------------


class JavaExecutable(str):
    """A ``str`` path with machine-readable runtime metadata.

    ``find_java`` historically returned ``Optional[str]`` and callers in
    ``runner``/``treetools`` still use it as a path.  Returning a ``str``
    subclass keeps that contract while exposing ``.version``, ``.path`` and
    ``.runtime`` (requires path *and* version).
    """

    runtime: "JavaRuntime"

    def __new__(cls, runtime: "JavaRuntime") -> "JavaExecutable":
        obj = super().__new__(cls, runtime.path or "")
        obj.runtime = runtime
        obj.version = runtime.version
        obj.path = runtime.path
        obj.vendor = runtime.vendor
        obj.source = runtime.source
        return obj

    version: Optional[str]
    path: Optional[str]
    vendor: Optional[str]
    source: Optional[str]


@dataclass
class JavaRuntime:
    """Result of a Java discovery probe."""

    path: Optional[str] = None
    version: Optional[str] = None
    vendor: Optional[str] = None
    vendor_version: Optional[str] = None
    java_home: Optional[str] = None
    source: str = ""
    usable: bool = False
    reason: str = ""
    attempts: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "version": self.version,
            "vendor": self.vendor,
            "vendor_version": self.vendor_version,
            "java_home": self.java_home,
            "source": self.source,
            "usable": self.usable,
            "reason": self.reason,
            "attempts": list(self.attempts),
        }


def _probe_java(path: str, source: str, timeout: int = 20) -> JavaRuntime:
    """Execute one candidate and parse ``java.version`` from its output."""
    runtime = JavaRuntime(path=path, source=source)
    if not Path(path).is_file():
        runtime.reason = "candidate is not a file"
        runtime.attempts.append({"candidate": path, "source": source, "status": "not-found"})
        return runtime
    commands = [
        [path, "-XshowSettings:properties", "-version"],
        [path, "-version"],
    ]
    for command in commands:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            runtime.attempts.append(
                {
                    "candidate": path,
                    "source": source,
                    "command": command,
                    "status": "error",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            runtime.reason = f"cannot execute {path}: {type(exc).__name__}: {exc}"
            continue
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        version = _parse_java_properties_version(output)
        properties = _parse_java_properties(output)
        if proc.returncode != 0:
            runtime.attempts.append(
                {
                    "candidate": path,
                    "source": source,
                    "command": command,
                    "status": "nonzero-exit",
                    "returncode": proc.returncode,
                }
            )
            runtime.reason = f"java probe exited {proc.returncode}"
            continue
        if not version:
            runtime.attempts.append(
                {
                    "candidate": path,
                    "source": source,
                    "command": command,
                    "status": "no-version",
                    "returncode": proc.returncode,
                    "output_head": output.strip()[:300],
                }
            )
            runtime.reason = "java exited 0 but no java.version could be parsed"
            continue
        runtime.version = version
        runtime.vendor = properties.get("java.vendor")
        runtime.vendor_version = properties.get("java.vendor.version")
        runtime.java_home = properties.get("java.home")
        runtime.usable = True
        runtime.reason = ""
        runtime.attempts.append(
            {
                "candidate": path,
                "source": source,
                "command": command,
                "status": "ok",
                "returncode": 0,
                "version": version,
            }
        )
        return runtime
    return runtime


def _parse_java_properties(output: str) -> Dict[str, str]:
    properties: Dict[str, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if " = " in stripped:
            key, value = stripped.split(" = ", 1)
            if key.startswith("java."):
                properties[key] = value.strip()
    return properties


def _parse_java_properties_version(output: str) -> Optional[str]:
    properties = _parse_java_properties(output)
    version = properties.get("java.version")
    if version:
        return version
    # ``java -XshowSettings`` is unavailable on very old runtimes; fall back
    # to the regular version banner, never to a vendor name.
    match = re.search(r'version\s+"([^"]+)"', output)
    if match:
        return match.group(1)
    match = re.search(r"\b(?:openjdk|java)\s+([0-9][^\s]*)", output, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _java_candidates(
    explicit: Sequence[str], environ: Dict[str, str], which
) -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    for value in explicit:
        if value:
            candidates.append((value, "explicit"))
    beastx_java = environ.get("BEASTX_JAVA")
    if beastx_java:
        base = Path(beastx_java)
        if base.is_dir():
            for name in ("java.exe", "java"):
                candidates.append((str(base / "bin" / name), "BEASTX_JAVA"))
        else:
            candidates.append((beastx_java, "BEASTX_JAVA"))
            if os.name == "nt" and not beastx_java.lower().endswith(".exe"):
                # BEASTX_JAVA=C:\...\bin\java is a natural spelling on Windows.
                candidates.append((beastx_java + ".exe", "BEASTX_JAVA"))
    java_home = environ.get("JAVA_HOME")
    if java_home:
        for name in ("java.exe", "java"):
            candidates.append((str(Path(java_home) / "bin" / name), "JAVA_HOME"))
    for name in ("java.exe", "java"):
        found = which(name)
        if found:
            candidates.append((found, "PATH"))
    # Platform fallbacks.  They are only *candidates*: each one is executed
    # and its version parsed before it is accepted, so a stale directory never
    # produces a false positive.
    if sys.platform.startswith("win"):
        roots = [
            environ.get("ProgramFiles", r"C:\Program Files"),
            environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]
        patterns = [
            "Eclipse Adoptium/*/bin/java.exe",
            "Eclipse Foundation/*/bin/java.exe",
            "Java/*/bin/java.exe",
            "Microsoft/*/bin/java.exe",
            "Zulu/*/bin/java.exe",
            "Amazon Corretto/*/bin/java.exe",
            "Semeru/*/bin/java.exe",
        ]
        for root in roots:
            if not root:
                continue
            for pattern in patterns:
                for match in sorted(Path(root).glob(pattern)):
                    candidates.append((str(match), "platform-scan"))
    elif sys.platform == "darwin":
        java_home_tool = shutil.which("java_home") or "/usr/libexec/java_home"
        if Path(java_home_tool).exists():
            candidates.append((java_home_tool, "java_home"))
    else:
        for match in sorted(Path("/usr/lib/jvm").glob("*/bin/java")):
            candidates.append((str(match), "platform-scan"))
    return candidates


def find_java_runtime(
    candidates: Optional[Sequence[str]] = None,
    environ: Optional[Dict[str, str]] = None,
    which=None,
) -> JavaRuntime:
    """Locate a JDK by executing it and parsing ``java.version``.

    Accepts Temurin, OpenJDK, Oracle JDK, Zulu, Corretto, ...  The vendor
    string is recorded for diagnostics but is never used as an availability
    gate.
    """
    env = dict(os.environ if environ is None else environ)
    which_fn = which or shutil.which
    attempts: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for candidate, source in _java_candidates(candidates or [], env, which_fn):
        resolved = candidate
        if source == "java_home":
            try:
                proc = subprocess.run(
                    [candidate],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                    shell=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                attempts.append(
                    {
                        "candidate": candidate,
                        "source": source,
                        "status": "error",
                        "reason": str(exc),
                    }
                )
                continue
            home = (proc.stdout or "").strip()
            if not home:
                attempts.append({"candidate": candidate, "source": source, "status": "no-home"})
                continue
            resolved = str(Path(home) / "bin" / "java")
        path = Path(resolved)
        if not path.is_file():
            found = which_fn(resolved)
            if found:
                path = Path(found)
        key = str(path).casefold() if os.name == "nt" else str(path)
        if key in seen:
            continue
        seen.add(key)
        runtime = _probe_java(str(path), source)
        attempts.extend(runtime.attempts)
        if runtime.usable:
            runtime.attempts = attempts
            return runtime
    return JavaRuntime(
        usable=False,
        reason="no usable Java runtime found (set JAVA_HOME or BEASTX_JAVA, or install a JDK)",
        attempts=attempts,
    )


def find_java(candidates: Optional[List[str]] = None) -> Optional[str]:
    """Return a usable ``java`` executable path (with ``.version`` metadata).

    The return value is a :class:`JavaExecutable` (a ``str`` subclass) so
    existing callers keep working, while new callers can read
    ``find_java().version`` / ``.runtime.as_dict()``.
    """
    runtime = find_java_runtime(candidates)
    if not runtime.usable or not runtime.path:
        return None
    return JavaExecutable(runtime)


def java_environment_report() -> Dict[str, Any]:
    """Machine-readable Java diagnostics (path, version, attempts)."""
    return find_java_runtime().as_dict()


# ---------------------------------------------------------------------------
# Jar discovery
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _packaged_jar(version: str) -> Path:
    return Path(__file__).parent / "tools" / "jars" / f"beast-{version}.jar"


def jar_candidates(version: str, search_root: Optional[Path] = None) -> List[Tuple[Path, str]]:
    """Ordered ``(path, source)`` candidates for a BEAST jar.

    Explicit environment overrides win over the packaged jar.  An official
    installation found next to the checkout ranks above the packaged
    source-build jar.
    """
    tag = version.replace(".", "_")
    candidates: List[Tuple[Path, str]] = []
    for env_key in (f"BEASTX_JAR_{tag}", f"BEAST_{tag}_JAR", "BEASTX_JAR"):
        value = os.environ.get(env_key)
        if not value:
            continue
        path = Path(value)
        if path.is_dir():
            candidates.append((path / "lib" / "beast.jar", f"env:{env_key}"))
        else:
            candidates.append((path, f"env:{env_key}"))
    for env_key in (f"BEAST_{tag}_HOME", f"BEASTX_{tag}_HOME"):
        value = os.environ.get(env_key)
        if value:
            base = Path(value)
            candidates.append((base / "lib" / "beast.jar", f"env:{env_key}"))
            candidates.append((base / "beast.jar", f"env:{env_key}"))
    packaged = _packaged_jar(version)
    ref_dir = os.environ.get("BEASTX_REF_DIR")
    if ref_dir:
        candidates.append(
            (
                Path(ref_dir) / f"beast-mcmc-{version}" / "build" / "dist" / "beast.jar",
                "BEASTX_REF_DIR",
            )
        )
    # Reference/official installation next to this checkout (read-only fallback).
    project = Path(search_root) if search_root else Path(__file__).resolve().parent.parent
    for parent in (project.parent, project):
        for directory in _reference_install_dirs(version, parent):
            candidates.append((directory / "lib" / "beast.jar", "reference-install"))
    # The bundled jar is a last resort: it is a project rebuild, not the
    # official distribution.
    candidates.append((packaged, "packaged-fallback"))
    return candidates


def _reference_install_dirs(version: str, search_root: Path) -> List[Path]:
    """Official BEAST distribution roots beside ``search_root``.

    The reference bundle uses several directory spellings (``BEASTv1.10.4``,
    ``BEAST.v1.10.4/BEAST v1.10.4``, ...), and Gate 3 must not silently fall
    back to the packaged source-build jar just because one spelling was
    hard-coded wrongly.  Each candidate is only accepted if it actually holds
    ``lib/beast.jar`` (see :func:`jar_candidates`).
    """
    directories: List[Path] = []
    reference = search_root / "BeastXPy-参考软件"
    for parent in (reference, search_root):
        if version == "1.10.4":
            spellings = [
                "BEASTv1.10.4",
                "BEAST.v1.10.4",
                "BEAST v1.10.4",
            ]
        else:
            spellings = [
                "BEASTv10.5.0",
                "BEAST.X.v10.5.0",
                "BEAST X v10.5.0",
                "BEASTXv10.5.0",
            ]
        for spelling in spellings:
            candidate = parent / spelling
            directories.append(candidate)
            directories.append(candidate / spelling)
        for pattern in ("*BEAST*v*", "*BEAST*X*v*"):
            directories.extend(sorted(parent.glob(pattern)))
    seen: Set[Path] = set()
    unique: List[Path] = []
    for directory in directories:
        if directory in seen:
            continue
        seen.add(directory)
        unique.append(directory)
    return [directory for directory in unique if version in str(directory)]


def lookup_jar_details(version: str) -> Optional[Dict[str, str]]:
    """Return ``{path, source, sha256, size}`` for the selected jar."""
    if version not in VALID_BEAST_VERSIONS:
        return None
    seen: Set[str] = set()
    for path, source in jar_candidates(version):
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file() and path.stat().st_size > 0:
            return {
                "path": str(path),
                "source": source,
                "sha256": _sha256_file(path),
                "size": str(path.stat().st_size),
            }
    return None


def lookup_jar(beast_version: str) -> Optional[Path]:
    """Return a usable BEAST jar path for ``beast_version`` (or ``None``)."""
    details = lookup_jar_details(beast_version)
    return Path(details["path"]) if details else None


def _native_library_markers() -> Tuple[str, ...]:
    """Filenames that identify a native BEAGLE/JNI installation."""
    if os.name == "nt":
        return ("hmsbeagle64.dll", "hmsbeagle32.dll", "hmsbeagle-jni64.dll", "hmsbeagle-jni.dll")
    if sys.platform == "darwin":
        return ("libhmsbeagle-jni.jnilib", "libhmsbeagle.dylib")
    return ("libhmsbeagle-jni.so", "libhmsbeagle.so")


def _is_beagle_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any((path / name).is_file() for name in _native_library_markers())


def lookup_beagle_details(
    beast_version: str, explicit: Optional[str] = None
) -> Optional[Dict[str, str]]:
    """Return ``{path, source, version?}`` for a native BEAGLE directory.

    Only *native* (JNI) installations count.  The beagle.jar wrapper that
    ships with BEAST is never accepted as evidence of native support.
    Discovery order: explicit argument, version-specific environment
    variable, generic environment variable, then the conventional
    ``lib/beagle`` directory next to the selected BEAST jar.
    """
    tag = beast_version.replace(".", "_")
    candidates: List[Tuple[Path, str]] = []
    if explicit:
        candidates.append((Path(explicit), "explicit"))
    for env_key in (f"BEASTX_BEAGLE_{tag}_LIB", "BEASTX_BEAGLE_LIB", "BEAGLE_HOME", "BEAGLE_LIB"):
        value = os.environ.get(env_key)
        if not value:
            continue
        base = Path(value)
        candidates.append((base, f"env:{env_key}"))
        candidates.append((base / "lib", f"env:{env_key}"))
        candidates.append((base / "bin", f"env:{env_key}"))
    jar = lookup_jar(beast_version)
    if jar is not None:
        candidates.append((jar.parent / "beagle", "jar-relative"))
        candidates.append((jar.parent / "beagle" / "lib", "jar-relative"))
    seen: Set[str] = set()
    for path, source in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if _is_beagle_dir(path):
            return {"path": str(path.resolve()), "source": source}
    return None


def lookup_beagle(beast_version: str, explicit: Optional[str] = None) -> Optional[Path]:
    """Return the directory holding a native BEAGLE JNI library (or ``None``)."""
    details = lookup_beagle_details(beast_version, explicit)
    return Path(details["path"]) if details else None


# ---------------------------------------------------------------------------
# Gate 3: BEAST invocation
# ---------------------------------------------------------------------------


@dataclass
class BeastValidationResult:
    """Structured result of a BEAST (Gate 3) validation run.

    ``status`` is one of :data:`GATE3_STATUSES`:

    * ``executed-pass``      Java ran and BEAST accepted the document;
    * ``executed-fail``      Java ran and BEAST rejected the document;
    * ``not-run``            validation did not execute (timeout, missing file,
                             missing validator class, ...);
    * ``environment-missing`` no usable Java/JDK or BEAST jar was found.

    ``ok`` is true **only** for ``executed-pass``.  ``reason`` always explains
    a non-pass status.  ``findings``/``stdout``/``stderr``/``command`` are
    retained so the CLI can print readable failure details.
    """

    ok: bool
    version: str
    status: str = GATE3_NOT_RUN
    reason: str = ""
    mode: str = "instantiate"
    stdout: str = ""
    stderr: str = ""
    findings: List[ValidationFinding] = field(default_factory=list)
    command: List[str] = field(default_factory=list)
    exit_code: Optional[int] = None
    executed: bool = False
    skipped: bool = True
    environment_error: bool = False
    java_path: Optional[str] = None
    java_version: Optional[str] = None
    jar_path: Optional[str] = None
    jar_sha256: Optional[str] = None
    beagle: Optional[str] = None
    beagle_version: Optional[str] = None
    tree_likelihood_rule_source: Optional[str] = None
    stages: List[Dict[str, str]] = field(default_factory=list)
    chain_length_seen: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "version": self.version,
            "status": self.status,
            "reason": self.reason,
            "mode": self.mode,
            "exit_code": self.exit_code,
            "executed": self.executed,
            "skipped": self.skipped,
            "environment_error": self.environment_error,
            "java_path": self.java_path,
            "java_version": self.java_version,
            "jar_path": self.jar_path,
            "jar_sha256": self.jar_sha256,
            "beagle": self.beagle,
            "beagle_version": self.beagle_version,
            "tree_likelihood_rule_source": self.tree_likelihood_rule_source,
            "chain_length_seen": self.chain_length_seen,
            "command": list(self.command),
            "findings": [finding.as_dict() for finding in self.findings],
            "stages": list(self.stages),
            "stderr_head": _head_lines(self.stderr, 20),
        }


class JavaNotFoundError(RuntimeError):
    """Raised when Gate 3 cannot find a usable JDK (carries diagnostics)."""

    def __init__(self, runtime: JavaRuntime) -> None:
        self.runtime = runtime
        super().__init__(runtime.reason or "no usable Java runtime")


def _head_lines(text: str, limit: int = 20) -> List[str]:
    return [line.rstrip() for line in (text or "").splitlines() if line.strip()][:limit]


def _validator_classpath(version: str, jar: Path) -> str:
    """Build the classpath with the platform separator."""
    base = Path(__file__).parent / "tools" / "classes"
    per_version = base / version
    classes = per_version if per_version.is_dir() else base
    parts: List[str] = []
    if classes.is_dir():
        parts.append(str(classes))
    parsers = Path(__file__).parent / "tools" / "parsers"
    version_parsers = parsers / version
    if version_parsers.is_dir():
        parts.append(str(version_parsers))
    if parsers.is_dir():
        parts.append(str(parsers))
    parts.append(str(jar))
    return os.pathsep.join(parts)


def _run_java(
    args: List[str],
    timeout: int = 300,
    cwd: Optional[Path] = None,
    env_extra: Optional[Dict[str, str]] = None,
    runtime: Optional[JavaRuntime] = None,
) -> subprocess.CompletedProcess:
    if runtime is None:
        runtime = find_java_runtime()
    if not runtime.usable or not runtime.path:
        raise JavaNotFoundError(runtime)
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [runtime.path] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        cwd=str(cwd) if cwd else None,
        env=env,
    )


def _beagle_env(beagle_dir: Path) -> Tuple[Dict[str, str], List[str]]:
    """Environment and JVM flags that expose a native BEAGLE directory.

    Windows resolves plugin DLLs through ``PATH`` (``LoadLibrary`` does not
    search the JNI library's own directory); Unix uses the loader path.
    """
    env: Dict[str, str] = {}
    if os.name == "nt":
        env["PATH"] = str(beagle_dir) + os.pathsep + os.environ.get("PATH", "")
    elif sys.platform == "darwin":
        env["DYLD_LIBRARY_PATH"] = (
            str(beagle_dir) + os.pathsep + os.environ.get("DYLD_LIBRARY_PATH", "")
        )
    else:
        env["LD_LIBRARY_PATH"] = (
            str(beagle_dir) + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
        )
    flags = [f"-Djava.library.path={beagle_dir}"]
    return env, flags


def run_beast_validation(
    xml_path: str,
    beast_version: str = "1.10.4",
    seed: Optional[int] = None,
    chain_length: Optional[int] = None,
    tests: Optional[int] = None,
    dry_parse: bool = True,
    timeout: int = 600,
    parse_only: bool = False,
    java_core: bool = False,
    relaxed: bool = False,
    require_beagle: bool = False,
    run_chain: bool = False,
    native_beagle: bool = False,
    beagle_dir: Optional[str] = None,
    beagle_threads: Optional[int] = None,
) -> BeastValidationResult:
    """Run ``BeastXValidator`` against ``xml_path`` and report a four-state result.

    Args:
        xml_path: BEAST 1 XML file.
        beast_version: ``1.10.4`` or ``10.5.0``.
        seed: Optional random seed.
        chain_length: Rewrite ``chainLength`` into a real temporary XML file
            and parse that file; the observed chain length is reported.
        tests: Number of full-evaluation consistency tests (requires ``--run``
            in the Java helper for observable effect).
        dry_parse: Backward-compatible flag.  It maps to the truthful
            ``instantiate`` mode: BEAST constructs the MCMC object and
            evaluates the initial posterior, but no chain is run.  Use
            ``parse_only=True`` for the XML-only check.
        timeout: Subprocess timeout in seconds.
        parse_only: Strict DOM/XML-idref/parser-name check; does not construct
            BEAST model objects.
        java_core: Replace the BEAGLE treeLikelihood parser with BEAST's legacy
            pure-Java ``TreeLikelihood`` parser (clearly labelled non-BEAGLE).
        require_beagle: Fail with ``environment-missing`` when no native BEAGLE
            library is available.
        run_chain: Actually call ``MCMC.run()`` (requires a likelihood core).
        native_beagle: Use the native BEAGLE likelihood core (implies
            ``require_beagle``) and expose ``beagle_dir`` to the JVM.
        beagle_dir: Native BEAGLE installation directory; when omitted it is
            discovered from ``BEASTX_BEAGLE_*_LIB``/``BEAGLE_HOME`` or the
            ``lib/beagle`` directory next to the BEAST jar.
        beagle_threads: Value for BEAST's ``beagle.thread.count`` property.
    """
    if beast_version not in VALID_BEAST_VERSIONS:
        raise ValueError(
            f"BeastXValidator supports single-version targets, got {beast_version!r}; "
            "use 'both' at the API/CLI layer to run both jars"
        )
    jar_details = lookup_jar_details(beast_version)
    if jar_details is None:
        return BeastValidationResult(
            ok=False,
            version=beast_version,
            status=GATE3_ENVIRONMENT_MISSING,
            reason=(
                f"no beast-{beast_version}.jar found. Set BEAST_"
                f"{beast_version.replace('.', '_')}_HOME, BEASTX_JAR_"
                f"{beast_version.replace('.', '_')} or BEASTX_JAR, or build "
                "beastxpy/tools/jars with tools/build_jars.py."
            ),
            mode="parse" if parse_only else "instantiate",
            environment_error=True,
        )
    jar = Path(jar_details["path"])
    runtime = find_java_runtime()
    if not runtime.usable or not runtime.path:
        return BeastValidationResult(
            ok=False,
            version=beast_version,
            status=GATE3_ENVIRONMENT_MISSING,
            reason=runtime.reason,
            mode="parse" if parse_only else "instantiate",
            environment_error=True,
            java_path=None,
            java_version=None,
            jar_path=str(jar),
            jar_sha256=jar_details["sha256"],
            stderr=json.dumps(runtime.as_dict(), indent=2),
        )
    classes_base = Path(__file__).parent / "tools" / "classes"
    if (
        not (classes_base / beast_version).is_dir()
        and not (classes_base / "BeastXValidator.class").exists()
    ):
        return BeastValidationResult(
            ok=False,
            version=beast_version,
            status=GATE3_NOT_RUN,
            reason=(
                "BeastXValidator.class is not compiled; run "
                "tools/build_jars.py (or tools/official_validate.py for the "
                "official-jar probe)"
            ),
            mode="parse" if parse_only else "instantiate",
            environment_error=True,
            java_path=runtime.path,
            java_version=runtime.version,
            jar_path=str(jar),
            jar_sha256=jar_details["sha256"],
        )

    beagle_details: Optional[Dict[str, str]] = None
    if native_beagle:
        require_beagle = True
        beagle_details = lookup_beagle_details(beast_version, beagle_dir)
        if beagle_details is None:
            return BeastValidationResult(
                ok=False,
                version=beast_version,
                status=GATE3_ENVIRONMENT_MISSING,
                reason=(
                    "native BEAGLE requested but no hmsbeagle JNI library was "
                    "found; set BEASTX_BEAGLE_LIB (or BEASTX_BEAGLE_<version>_LIB) "
                    "to the directory containing hmsbeagle64.dll/libhmsbeagle-jni.so"
                ),
                mode="beagle",
                environment_error=True,
                java_path=runtime.path,
                java_version=runtime.version,
                jar_path=str(jar),
                jar_sha256=jar_details["sha256"],
            )

    classpath = _validator_classpath(beast_version, jar)
    if parse_only:
        mode = "parse"
    elif java_core:
        mode = "java-core"
    elif native_beagle:
        mode = "beagle"
    else:
        mode = "instantiate"
    args: List[str] = ["-Dfile.encoding=UTF-8"]
    # Even parse-only mode builds intermediate parser objects; the shim keeps
    # that path free of the BEAGLE dependency on machines without hmsbeagle.
    if mode in ("java-core", "parse"):
        args.append("-Dparsers=bxpshim")
    env_extra: Dict[str, str] = {}
    if beagle_details is not None:
        env_extra, beagle_flags = _beagle_env(Path(beagle_details["path"]))
        args = beagle_flags + args
    args += [
        "-cp",
        classpath,
        "BeastXValidator",
        os.path.abspath(xml_path),
        "--" + mode,
    ]
    if seed is not None:
        args += ["--seed", str(seed)]
    if chain_length is not None:
        args += ["--chain-length", str(chain_length)]
    if tests is not None:
        args += ["--tests", str(tests)]
    if relaxed:
        args.append("--relaxed")
    if require_beagle:
        args.append("--require-beagle")
    if beagle_threads is not None:
        args += ["--beagle-threads", str(beagle_threads)]
    if run_chain:
        args.append("--run")

    xml_dir = Path(xml_path).resolve().parent
    try:
        proc = _run_java(args, timeout=timeout, cwd=xml_dir, env_extra=env_extra, runtime=runtime)
    except JavaNotFoundError as exc:
        return BeastValidationResult(
            ok=False,
            version=beast_version,
            status=GATE3_ENVIRONMENT_MISSING,
            reason=str(exc),
            mode=mode,
            command=args,
            environment_error=True,
            java_path=exc.runtime.path,
            java_version=exc.runtime.version,
            jar_path=str(jar),
            jar_sha256=jar_details["sha256"],
        )
    except subprocess.TimeoutExpired:
        return BeastValidationResult(
            ok=False,
            version=beast_version,
            status=GATE3_NOT_RUN,
            reason=f"BeastXValidator timed out after {timeout}s",
            mode=mode,
            command=args,
            java_path=runtime.path,
            java_version=runtime.version,
            jar_path=str(jar),
            jar_sha256=jar_details["sha256"],
        )

    findings = _parse_validate_output(proc.stdout, proc.stderr)
    stages = _parse_validate_stages(proc.stdout, proc.stderr)
    status, reason = _interpret_gate3_exit(proc.returncode, stages, proc.stderr)
    if proc.returncode not in (0, 2) and not findings:
        findings = [
            ValidationFinding(
                severity="error",
                path=str(xml_path),
                message=(
                    "BEAST exited with code "
                    f"{proc.returncode} but produced no structured finding: "
                    + (" ".join(_head_lines(proc.stderr, 5)) or "<empty stderr>")
                ),
                code="BEAST_EXIT",
            )
        ]
    ok = status == GATE3_EXECUTED_PASS and not any(
        finding.severity == "error" for finding in findings
    )
    if status == GATE3_EXECUTED_PASS and not ok:
        status = GATE3_EXECUTED_FAIL
        reason = "BeastXValidator exited 0 but reported error findings"

    beagle = _stage_detail(stages, "environment", "beagle")
    beagle_version = _stage_detail(stages, "environment", "beagle_version") or None
    match = re.search(r"Using BEAGLE library v([^\s]+)", proc.stdout + proc.stderr)
    if match and not beagle_version:
        beagle_version = match.group(1)
    chain_seen = _stage_detail(stages, "chain-length", "chain_length")
    return BeastValidationResult(
        ok=ok,
        version=beast_version,
        status=status,
        reason=reason,
        mode=mode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        findings=findings,
        command=args,
        exit_code=proc.returncode,
        executed=status not in (GATE3_NOT_RUN, GATE3_ENVIRONMENT_MISSING),
        skipped=status in (GATE3_NOT_RUN, GATE3_ENVIRONMENT_MISSING),
        environment_error=status == GATE3_ENVIRONMENT_MISSING,
        java_path=runtime.path,
        java_version=runtime.version,
        jar_path=str(jar),
        jar_sha256=jar_details["sha256"],
        beagle=beagle,
        beagle_version=beagle_version,
        tree_likelihood_rule_source=_stage_detail(
            stages, "environment", "tree_likelihood_rule_source"
        ),
        stages=stages,
        chain_length_seen=int(chain_seen) if chain_seen and chain_seen.isdigit() else None,
    )


def _interpret_gate3_exit(
    returncode: int, stages: List[Dict[str, str]], stderr: str
) -> Tuple[str, str]:
    stage_status = {stage.get("stage"): stage.get("status") for stage in stages}
    stage_reason = {stage.get("stage"): stage.get("reason", "") for stage in stages}
    if any(stage_status.get(name) == "environment-missing" for name in stage_status):
        return (
            GATE3_ENVIRONMENT_MISSING,
            stage_reason.get("environment", "")
            or "the validation environment is incomplete (BEAGLE required or unavailable)",
        )
    if returncode == 0:
        return GATE3_EXECUTED_PASS, ""
    if returncode == 2:
        return GATE3_ENVIRONMENT_MISSING, stage_reason.get("environment", "") or (
            "BEAST environment missing; see stderr"
        )
    if returncode == 3:
        return GATE3_NOT_RUN, (
            stage_reason.get("input", "")
            or stage_reason.get("read", "")
            or "the input XML was unavailable, so validation did not run"
        )
    failing = [
        f"{stage}: {stage_reason.get(stage, '')}"
        for stage, status in stage_status.items()
        if status in ("fail", "error")
    ]
    reason = "; ".join(failing) or (
        " ".join(_head_lines(stderr, 3)) or "BEAST rejected the document"
    )
    return GATE3_EXECUTED_FAIL, reason


def _parse_validate_stages(stdout: str, stderr: str) -> List[Dict[str, str]]:
    """Parse ``BXP-VALIDATE|result|stage|status|reason|key=value`` lines."""
    stages: List[Dict[str, str]] = []
    pattern = re.compile(
        re.escape(VALIDATE_PREFIX)
        + r"result\|(?P<stage>[^|]*)\|(?P<status>[^|]*)\|(?P<reason>[^|]*)(?:\|(?P<details>.*))?"
    )
    for line in (stdout + "\n" + stderr).splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        stage = {
            "stage": match.group("stage").strip(),
            "status": match.group("status").strip(),
            "reason": match.group("reason").strip(),
        }
        details = match.group("details") or ""
        for part in details.split("|"):
            if "=" in part:
                key, value = part.split("=", 1)
                stage[key.strip()] = value.strip()
        stages.append(stage)
    return stages


def _stage_detail(stages: List[Dict[str, str]], stage_name: str, key: str) -> Optional[str]:
    for stage in stages:
        if stage.get("stage") == stage_name and key in stage:
            return stage[key]
    return None


def _parse_validate_output(stdout: str, stderr: str) -> List[ValidationFinding]:
    """Parse ``BXP-VALIDATE|severity|code|path|message`` finding lines."""
    findings: List[ValidationFinding] = []
    pattern = re.compile(
        re.escape(VALIDATE_PREFIX)
        + r"(?P<severity>error|warning)\|(?P<code>[^|]*)\|(?P<path>[^|]*)\|(?P<message>.*)"
    )
    for line in (stdout + "\n" + stderr).splitlines():
        match = pattern.match(line.strip())
        if match:
            findings.append(
                ValidationFinding(
                    severity=match.group("severity"),
                    path=match.group("path") or "<beast>",
                    message=match.group("message"),
                    code=match.group("code"),
                )
            )
    return findings


def beast_validate_xml(
    xml_path: str,
    beast_version: str = "1.10.4",
    seed: Optional[int] = None,
    chain_length: Optional[int] = None,
    tests: Optional[int] = None,
    dry_parse: bool = True,
    **kwargs: Any,
) -> BeastValidationResult:
    """Gate-3 entry point (see :func:`run_beast_validation`)."""
    return run_beast_validation(
        xml_path,
        beast_version=beast_version,
        seed=seed,
        chain_length=chain_length,
        tests=tests,
        dry_parse=dry_parse,
        **kwargs,
    )


def summarize_gate3(results: Iterable[BeastValidationResult]) -> Dict[str, Any]:
    """Aggregate Gate-3 results into the contract consumed by the CLI/API.

    Returns ``{"status", "ok", "counts", "versions", "reasons"}`` where the
    overall status is ``executed-pass`` only when every version executed and
    passed.  ``executed-fail`` dominates ``environment-missing``, which in turn
    dominates ``not-run``.
    """
    items = list(results)
    counts = {status: 0 for status in GATE3_STATUSES}
    versions: Dict[str, Dict[str, Any]] = {}
    reasons: List[str] = []
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
        versions[item.version] = item.as_dict()
        if item.status != GATE3_EXECUTED_PASS:
            reasons.append(f"{item.version}: {item.status}: {item.reason}")
    if not items:
        overall = GATE3_NOT_RUN
    elif counts[GATE3_EXECUTED_FAIL]:
        overall = GATE3_EXECUTED_FAIL
    elif counts[GATE3_ENVIRONMENT_MISSING]:
        overall = GATE3_ENVIRONMENT_MISSING
    elif counts[GATE3_NOT_RUN]:
        overall = GATE3_NOT_RUN
    else:
        overall = GATE3_EXECUTED_PASS
    return {
        "status": overall,
        "ok": overall == GATE3_EXECUTED_PASS,
        "counts": counts,
        "versions": versions,
        "reasons": reasons,
    }


def environment_report() -> Dict[str, Any]:
    """Readable environment status for ``check-environment`` style callers."""
    runtime = find_java_runtime()
    jars: Dict[str, Any] = {}
    for version in VALID_BEAST_VERSIONS:
        jars[version] = lookup_jar_details(version)
    missing: List[str] = []
    if not runtime.usable:
        missing.append("java: " + runtime.reason)
    for version, details in jars.items():
        if details is None:
            missing.append(f"beast-{version}.jar")
    return {
        "ok": not missing,
        "java": runtime.as_dict(),
        "jars": jars,
        "missing": missing,
    }
