"""Capability manifest for BeastXPy (``capabilities`` subcommand).

The single source of truth is :file:`beastxpy/data/capabilities.yaml`.  This
module only loads, validates and renders that file: the CLI, the documentation
pages and the tests all read the same manifest, so there is no second
hand-maintained copy of the feature list anywhere in the repository.

What the manifest records -- and what it deliberately does not
--------------------------------------------------------------

Each row states, per BEAST version, whether BeastXPy can *generate and
validate* that feature (``supported``), can do so only within a documented
subset (``partial``), or cannot do it at all because the official BEAST jars
reject the construct (``blocked``).  ``blocked`` rows always carry the upstream
reason; two of them (codon GY94 and ARG recombination) are hard Gate-0
rejections on purpose, and no compatibility shim is described as official
BEAST support.

``requires_native_beagle`` marks the rows whose *execution* (not their XML
generation) needs a native hmsbeagle JNI library: BEAGLE 3.1.0 for BEAST
1.10.4 and BEAGLE 4.0.1 for BEAST X 10.5.0, with ``--beagle-threads 1`` on
Windows.  ``java-core`` validation never needs it.

Evidence modes (what "validated" means)
---------------------------------------
* ``java-core`` (the default for every row below): BeastXPy's XML is parsed
  and instantiated by the official BEAST parser classes, using the project's
  small ``TreeLikelihood`` parser shim because the pure-Java BEAGLE cannot
  evaluate a likelihood.  The document is constructed, not evaluated.
* ``beagle``: the full official parser set plus a real likelihood evaluation
  through a native hmsbeagle library.  This is the strongest evidence and is
  the mode that should be cited as "official validation".
* ``parse``: construction only, without the Gate-3 Java instantiation.

The per-row ``reason`` text names the mode.  ``xml_spec_provenance()`` exposes
the SHA-256 of the BEAST jar each element whitelist was extracted from, so a
reader can tell whether the whitelist matches the installed BEAST.

Nothing here is a convergence, mixing or model-selection claim, and a
``supported`` row is not a statement that a model is scientifically
appropriate for a given data set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from . import _contracts as C

#: Manifest location, relative to the package directory.
MANIFEST_NAME = "capabilities.yaml"
DATA_DIR_NAME = "data"

#: Statuses a per-version cell may carry.
STATUS_SUPPORTED = "supported"
STATUS_PARTIAL = "partial"
STATUS_BLOCKED = "blocked"
STATUSES: Sequence[str] = (STATUS_SUPPORTED, STATUS_PARTIAL, STATUS_BLOCKED)

#: ``--beast-version`` accepts the two concrete versions plus ``both``.
VERSION_BOTH = "both"

#: Keys every feature row must define.
_REQUIRED_FEATURE_KEYS = ("id", "name", "category", "versions", "verify_command")
#: Keys every per-version cell must define.
_REQUIRED_VERSION_KEYS = ("status", "requires_native_beagle", "reason")


class CapabilitiesError(C.ContractError):
    """Raised when the manifest is missing, unparsable or self-inconsistent."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def manifest_path() -> Path:
    """Return the path of the packaged capability manifest.

    The source-tree layout (``beastxpy/data/capabilities.yaml``) is tried
    first; ``importlib.resources`` is the fallback for an installed wheel
    where ``__file__`` may point into a zip archive.
    """
    direct = Path(__file__).resolve().parent / DATA_DIR_NAME / MANIFEST_NAME
    if direct.is_file():
        return direct
    try:  # pragma: no cover - exercised only from an installed package
        from importlib import resources

        candidate = resources.files("beastxpy").joinpath(  # type: ignore[attr-defined]
            f"{DATA_DIR_NAME}/{MANIFEST_NAME}"
        )
        return Path(str(candidate))
    except Exception:  # noqa: BLE001 - fall through to the direct path
        return direct


def _read_manifest(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise CapabilitiesError(
            f"capabilities manifest not found: {C.display_path(path)}",
            fix=(
                "Reinstall BeastXPy or restore beastxpy/data/capabilities.yaml; "
                "it is the single source of truth for `beastxpy capabilities`."
            ),
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapabilitiesError(
            f"capabilities manifest is not readable: {C.display_path(path)} ({exc})",
            fix="Check the file permissions and that the file is valid UTF-8.",
        ) from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CapabilitiesError(
            f"capabilities manifest is not valid YAML: {C.display_path(path)} ({exc})",
            fix="Fix the YAML syntax; the manifest must load with yaml.safe_load().",
        ) from exc
    if not isinstance(loaded, dict):
        raise CapabilitiesError(
            f"capabilities manifest must be a mapping, got {type(loaded).__name__}",
            fix="The top level of capabilities.yaml must be a YAML mapping.",
        )
    return loaded


def _validate(manifest: Dict[str, Any], path: Path) -> Dict[str, Any]:
    """Structurally validate the manifest; raise on any inconsistency."""
    schema = manifest.get("schema_version")
    if schema != C.SCHEMA_CAPABILITIES:
        raise CapabilitiesError(
            f"capabilities manifest {C.display_path(path)} declares schema_version "
            f"{schema!r}; expected {C.SCHEMA_CAPABILITIES!r}",
            fix="Regenerate or repair beastxpy/data/capabilities.yaml.",
        )

    versions = manifest.get("beast_versions")
    if not isinstance(versions, list) or not versions:
        raise CapabilitiesError(
            "capabilities manifest: 'beast_versions' must be a non-empty list",
            fix='Use beast_versions: ["1.10.4", "10.5.0"].',
        )
    beast_versions = [str(item) for item in versions]

    features = manifest.get("features")
    if not isinstance(features, list) or not features:
        raise CapabilitiesError(
            "capabilities manifest: 'features' must be a non-empty list",
            fix="Add at least one feature row to beastxpy/data/capabilities.yaml.",
        )

    seen: Dict[str, int] = {}
    normalised: List[Dict[str, Any]] = []
    for index, raw in enumerate(features):
        where = f"features[{index}]"
        if not isinstance(raw, dict):
            raise CapabilitiesError(
                f"capabilities manifest: {where} must be a mapping",
                fix="Every feature row is a YAML mapping with id/name/category/versions.",
            )
        for key in _REQUIRED_FEATURE_KEYS:
            if key not in raw:
                raise CapabilitiesError(
                    f"capabilities manifest: {where} is missing required key {key!r}",
                    fix=f"Add '{key}' to the feature row {raw.get('id', where)!r}.",
                )
        feature_id = str(raw["id"])
        if not feature_id:
            raise CapabilitiesError(
                f"capabilities manifest: {where} has an empty 'id'",
                fix="Every feature row needs a non-empty snake_case id.",
            )
        if feature_id in seen:
            raise CapabilitiesError(
                f"capabilities manifest: duplicate feature id {feature_id!r} "
                f"(rows {seen[feature_id]} and {index})",
                fix="Feature ids must be unique; merge or rename the duplicates.",
            )
        seen[feature_id] = index

        cells = raw.get("versions")
        if not isinstance(cells, dict) or not cells:
            raise CapabilitiesError(
                f"capabilities manifest: {feature_id}: 'versions' must be a mapping "
                "keyed by BEAST version",
                fix='Use versions: {"1.10.4": {...}, "10.5.0": {...}}.',
            )
        cell_map: Dict[str, Any] = {}
        for version in beast_versions:
            if version not in {str(k) for k in cells}:
                raise CapabilitiesError(
                    f"capabilities manifest: {feature_id}: no cell for BEAST {version}",
                    fix=f"Add a '{version}' entry with status/requires_native_beagle/reason.",
                )
            cell = cells[version] if version in cells else cells[_find_key(cells, version)]
            if not isinstance(cell, dict):
                raise CapabilitiesError(
                    f"capabilities manifest: {feature_id}[{version}] must be a mapping",
                    fix="Each version cell holds status/requires_native_beagle/reason.",
                )
            for key in _REQUIRED_VERSION_KEYS:
                if key not in cell:
                    raise CapabilitiesError(
                        f"capabilities manifest: {feature_id}[{version}] is missing {key!r}",
                        fix=f"Add '{key}' to the {version} cell of {feature_id}.",
                    )
            status = str(cell["status"]).strip().lower()
            if status not in STATUSES:
                raise CapabilitiesError(
                    f"capabilities manifest: {feature_id}[{version}].status is "
                    f"{cell['status']!r}; expected one of {list(STATUSES)}",
                    fix="Use supported, partial or blocked.",
                )
            reason = str(cell.get("reason") or "").strip()
            if not reason:
                raise CapabilitiesError(
                    f"capabilities manifest: {feature_id}[{version}].reason is empty",
                    fix=(
                        "Every row states why it has that status; a blocked row must "
                        "name the upstream defect."
                    ),
                )
            cell_map[version] = {
                "status": status,
                "requires_native_beagle": bool(cell["requires_native_beagle"]),
                "reason": reason,
            }

        verify_command = str(raw.get("verify_command") or "").strip()
        if not verify_command:
            raise CapabilitiesError(
                f"capabilities manifest: {feature_id}: 'verify_command' is empty",
                fix="Give a command that actually runs in this repository.",
            )

        normalised.append(
            {
                "id": feature_id,
                "name": str(raw.get("name") or feature_id),
                "category": str(raw.get("category") or "uncategorised"),
                "versions": cell_map,
                "verify_command": verify_command,
            }
        )

    validated = dict(manifest)
    validated["schema_version"] = str(schema)
    validated["beast_versions"] = beast_versions
    validated["features"] = normalised
    return validated


def _find_key(mapping: Dict[Any, Any], wanted: str) -> Any:
    for key in mapping:
        if str(key) == wanted:
            return key
    raise KeyError(wanted)


def load_capabilities(path: Optional[Any] = None) -> Dict[str, Any]:
    """Load and validate the capability manifest.

    Args:
        path: Optional alternative manifest location (used by the tests to
            prove the CLI reads the YAML instead of a hardcoded duplicate).

    Returns:
        The manifest with ``beast_versions`` normalised to strings and every
        feature row normalised to
        ``{id, name, category, versions{<version>: {status,
        requires_native_beagle, reason}}, verify_command}``.

    Raises:
        CapabilitiesError: If the manifest is missing, unparsable or violates
            the schema (a missing per-version cell, an unknown status, an
            empty reason, a duplicate id, ...).
    """
    target = Path(path) if path is not None else manifest_path()
    return _validate(_read_manifest(target), target)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def beast_versions(manifest: Optional[Dict[str, Any]] = None) -> List[str]:
    """The BEAST versions the manifest describes."""
    doc = manifest if manifest is not None else load_capabilities()
    return list(doc["beast_versions"])


def _check_version(version: str, doc: Dict[str, Any]) -> str:
    known = [str(item) for item in doc["beast_versions"]]
    if version not in known:
        raise CapabilitiesError(
            f"unknown BEAST version {version!r}; the manifest describes {known}",
            fix=f"Pass one of {known} (or 'both' on the command line).",
        )
    return version


def features_for(version: str, manifest: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Return every feature row flattened for one concrete BEAST version.

    Each result carries ``id``, ``name``, ``category``, ``status``,
    ``requires_native_beagle``, ``reason`` and ``verify_command`` for that
    version.  ``version`` must be a concrete version from the manifest, not
    ``both`` -- callers that want both versions call this twice.
    """
    doc = manifest if manifest is not None else load_capabilities()
    _check_version(version, doc)
    rows: List[Dict[str, Any]] = []
    for feature in doc["features"]:
        cell = feature["versions"][version]
        rows.append(
            {
                "id": feature["id"],
                "name": feature["name"],
                "category": feature["category"],
                "status": cell["status"],
                "requires_native_beagle": cell["requires_native_beagle"],
                "reason": cell["reason"],
                "verify_command": feature["verify_command"],
            }
        )
    return rows


def categories(manifest: Optional[Dict[str, Any]] = None) -> List[str]:
    """Distinct feature categories, in manifest order."""
    doc = manifest if manifest is not None else load_capabilities()
    ordered: List[str] = []
    for feature in doc["features"]:
        if feature["category"] not in ordered:
            ordered.append(feature["category"])
    return ordered


def status_counts(version: str, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """How many rows are supported/partial/blocked for ``version``."""
    counts = {STATUS_SUPPORTED: 0, STATUS_PARTIAL: 0, STATUS_BLOCKED: 0}
    for row in features_for(version, manifest):
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def beagle_dependent(version: str, manifest: Optional[Dict[str, Any]] = None) -> List[str]:
    """Feature ids whose execution needs a native BEAGLE library."""
    return [row["id"] for row in features_for(version, manifest) if row["requires_native_beagle"]]


def blocked_features(
    version: str, manifest: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Rows that are ``blocked`` for ``version`` (id + upstream reason)."""
    return [row for row in features_for(version, manifest) if row["status"] == STATUS_BLOCKED]


def xml_spec_provenance() -> Dict[str, Any]:
    """Per-version provenance of the Gate-1 element whitelist.

    Returns the source jar label and SHA-256 plus the parser/element counts
    recorded when ``xml_spec.json`` was generated.  A missing or unreadable
    spec yields ``{"status": "not_computable", "reason": ...}`` rather than a
    fabricated fingerprint.
    """
    path = Path(__file__).parent / DATA_DIR_NAME / "xml_spec.json"
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "status": "not_computable",
            "reason": f"xml_spec.json could not be read: {exc}",
        }
    provenance = spec.get("provenance") or {}
    inputs = provenance.get("inputs") or {}
    counts = provenance.get("counts") or {}
    versions: Dict[str, Any] = {}
    for version, record in inputs.items():
        if not isinstance(record, dict):
            continue
        versions[str(version)] = {
            "jar": record.get("jar"),
            "jar_sha256": record.get("jar_sha256"),
            "jar_source": record.get("jar_source") or "official-release",
            "parser_source": record.get("parser_source"),
            "parser_names": record.get("parser_names"),
            "example_tags": record.get("example_tags"),
            "element_count": (counts or {}).get(str(version)),
            "element_set_sha256": record.get("element_set_sha256"),
        }
    return {
        "status": "ok",
        "schema": spec.get("schema"),
        "version": spec.get("version"),
        "elements": len(spec.get("elements") or []),
        "versions": versions,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def to_envelope(
    version: str = VERSION_BOTH,
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the ``beastxpy.capabilities/v1`` JSON document.

    The envelope mirrors the manifest (``beast_versions``, ``features``) and
    adds ``generated_at``/``tool_version`` through :func:`C.envelope`, the
    requested ``beast_version`` filter, per-version status counts and the
    native-BEAGLE requirement block.
    """
    doc = manifest if manifest is not None else load_capabilities()
    if version != VERSION_BOTH:
        _check_version(version, doc)
    selected = list(doc["beast_versions"]) if version == VERSION_BOTH else [version]
    counts = {item: status_counts(item, doc) for item in selected}
    return C.envelope(
        C.SCHEMA_CAPABILITIES,
        manifest_path=C.display_path(manifest_path()),
        beast_version=version,
        beast_versions=list(doc["beast_versions"]),
        native_beagle=doc.get("native_beagle") or {},
        features=[
            {
                "id": feature["id"],
                "name": feature["name"],
                "category": feature["category"],
                "versions": feature["versions"],
                "verify_command": feature["verify_command"],
            }
            for feature in doc["features"]
        ],
        counts=counts,
        beagle_dependent={item: beagle_dependent(item, doc) for item in selected},
        xml_spec_provenance=xml_spec_provenance(),
        limitations=list(doc.get("limitations") or []),
    )


def _cell_mark(row: Dict[str, Any]) -> str:
    """Compact status cell: ``supported``, ``partial``, ``blocked[beagle]``."""
    text = row["status"]
    if row["requires_native_beagle"]:
        text += "+beagle"
    return text


def format_table(
    version: str = VERSION_BOTH,
    manifest: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Render the manifest as an aligned plain-text table (stdout)."""
    doc = manifest if manifest is not None else load_capabilities()
    if version != VERSION_BOTH:
        _check_version(version, doc)
    selected = list(doc["beast_versions"]) if version == VERSION_BOTH else [version]
    rows: Dict[str, List[Dict[str, Any]]] = {item: features_for(item, doc) for item in selected}
    features = doc["features"]

    id_width = max([len("feature")] + [len(f["id"]) for f in features])
    cat_width = max([len("category")] + [len(f["category"]) for f in features])
    status_width = max(
        [len(item) for item in selected]
        + [len(_cell_mark(row)) for item in selected for row in rows[item]]
    )
    name_width = max([len("name")] + [len(f["name"]) for f in features])

    lines: List[str] = []
    source = C.display_path(manifest_path()).encode("ascii", "backslashreplace").decode("ascii")
    lines.append(f"BeastXPy {C.tool_version()} capabilities (source: {source})")
    lines.append(
        "beast versions: "
        + ", ".join(str(item) for item in doc["beast_versions"])
        + f"   filter: {version}"
    )
    for item in selected:
        counts = status_counts(item, doc)
        lines.append(
            f"  {item}: {counts[STATUS_SUPPORTED]} supported, "
            f"{counts[STATUS_PARTIAL]} partial, {counts[STATUS_BLOCKED]} blocked "
            f"({len(features)} rows)"
        )
    lines.append("")

    header = (
        f"{'feature':<{id_width}}  {'category':<{cat_width}}  "
        + "  ".join(f"{item:>{status_width}}" for item in selected)
        + f"  {'name':<{name_width}}"
    )
    lines.append(header)
    lines.append("-" * min(len(header), 100))

    last_category = None
    for position, feature in enumerate(features):
        if last_category is not None and feature["category"] != last_category:
            lines.append("")
        last_category = feature["category"]
        cells = "  ".join(
            f"{_cell_mark(rows[item][position]):>{status_width}}" for item in selected
        )
        lines.append(
            f"{feature['id']:<{id_width}}  {feature['category']:<{cat_width}}  "
            f"{cells}  {feature['name']:<{name_width}}"
        )

    lines.append("")
    lines.append("status: supported | partial (documented subset) | blocked (upstream rejects it)")
    lines.append("+beagle: executing this feature needs a native hmsbeagle JNI library")
    lines.append(
        "evidence: supported/partial = XML generated and accepted by the official "
        "BEAST parsers in java-core mode (construct-only; TreeLikelihood shim, no "
        "likelihood evaluation). Cite --mode beagle (native BEAGLE) for a "
        "full-parser, real-likelihood check."
    )
    provenance = xml_spec_provenance()
    if provenance.get("status") == "ok":
        for item in selected:
            record = (provenance.get("versions") or {}).get(item)
            if not record:
                continue
            lines.append(
                "xml_spec: BEAST %s source jar %s sha256=%s (%s)"
                % (
                    item,
                    record.get("jar"),
                    record.get("jar_sha256"),
                    record.get("jar_source"),
                )
            )
            lines.append(
                "          whitelist=%s elements, %s parsers, %s official-example tags"
                % (
                    record.get("element_count") or provenance.get("elements", 0),
                    record.get("parser_names"),
                    record.get("example_tags"),
                )
            )
    else:
        lines.append("xml_spec: not computable -- %s" % provenance.get("reason"))

    beagle = doc.get("native_beagle") or {}
    if beagle:
        lines.append("")
        lines.append("native BEAGLE requirement:")
        for item in doc["beast_versions"]:
            info = beagle.get(item) or beagle.get(str(item)) or {}
            if not isinstance(info, dict):
                continue
            lines.append(
                f"  BEAST {item}: BEAGLE {info.get('version', '?')} "
                f"(plugin suffix {info.get('plugin_suffix', '?')}, "
                f"env {info.get('env_var', '?')}, "
                f"--beagle-threads {info.get('windows_beagle_threads', 1)} on Windows)"
            )

    limitations = list(doc.get("limitations") or [])
    if limitations:
        lines.append("")
        lines.append("limitations:")
        lines.extend(f"  - {text}" for text in limitations)
    return lines


def summary_lines(
    version: str = VERSION_BOTH,
    manifest: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Short human summary for the CLI (a few lines, no table)."""
    doc = manifest if manifest is not None else load_capabilities()
    if version != VERSION_BOTH:
        _check_version(version, doc)
    selected = list(doc["beast_versions"]) if version == VERSION_BOTH else [version]
    lines = [
        f"capabilities: {len(doc['features'])} features, "
        f"{len(doc['beast_versions'])} BEAST versions "
        f"({', '.join(str(item) for item in doc['beast_versions'])})"
    ]
    for item in selected:
        counts = status_counts(item, doc)
        blocked = blocked_features(item, doc)
        lines.append(
            f"  BEAST {item}: supported={counts[STATUS_SUPPORTED]} "
            f"partial={counts[STATUS_PARTIAL]} blocked={counts[STATUS_BLOCKED]}"
        )
        for row in blocked:
            lines.append(f"    blocked: {row['id']}")
    return lines


__all__ = [
    "STATUS_BLOCKED",
    "STATUS_PARTIAL",
    "STATUS_SUPPORTED",
    "STATUSES",
    "VERSION_BOTH",
    "CapabilitiesError",
    "beagle_dependent",
    "beast_versions",
    "blocked_features",
    "categories",
    "features_for",
    "format_table",
    "load_capabilities",
    "manifest_path",
    "status_counts",
    "summary_lines",
    "to_envelope",
]
