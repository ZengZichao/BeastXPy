"""BEAST X 10.5.0-only analyses (C09).

Two analyses that rely on parsers that do **not** exist in BEAST 1.10.4:

``age_statistic``
    Adds ``<treeHeightStatistic>`` (portable) wrapped in ``<ageStatistic>``
    (``dr.evomodelxml.tree.AgeStatisticParser``, new in 10.5.0).  The age is
    the height of the tree expressed on the absolute time axis of the dated
    tips (tip dates are required; the parser uses ``Taxon.getMostRecentDate``).

``tree_report``
    Reports a per-node tree summary via the new-in-10.5.0
    ``<branchLengthStatistic>`` parser: the length of the stem branch of a
    user-selected MRCA clade, logged to the trace.

    The ``<reportTree>`` logger parser (``TreeReportParser``) also exists in
    the 10.5.0 sources, but it is registered only in
    ``development_parsers.properties``.  The official 10.5.0 release jar does
    not register it, and BEAST silently ignores unknown top-level elements in
    ``<beast>`` (verified: both ``<reportTree>`` and a deliberately bogus tag
    produce no file and still pass).  This plugin therefore does not emit the
    dead ``<reportTree>`` element; it uses the registered, executable
    ``branchLengthStatistic`` instead and names the analysis ``tree_report``.

Both analyses are version-gated at Gate 0 through ``analysis.beast_version``
and again at assembly time through ``control.beast_version``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..model import ModelAssemblyError, el
from . import find_all

ANALYSIS_TYPES = ("age_statistic", "tree_report")

_VERSION_ERROR = (
    "analysis.beast_version: must be '10.5.0'; {analysis} uses {element}, a "
    "parser added in BEAST X 10.5.0 that does not exist in BEAST 1.10.4"
)

_ID_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _check_version(raw: Dict[str, Any], analysis: str, element: str, errors: List[str]) -> None:
    version = raw.get("beast_version")
    if version != "10.5.0":
        errors.append(_VERSION_ERROR.format(analysis=analysis, element=element))


def _name(raw: Dict[str, Any], default: str = "stemLength") -> str:
    name = raw.get("name", default)
    return str(name) if isinstance(name, str) and name else default


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    analysis = str(raw.get("type"))
    if analysis == "age_statistic":
        _check_version(raw, analysis, "<ageStatistic>", errors)
        if "name" in raw:
            errors.append(
                "analysis.name: <ageStatistic> declares no name attribute in "
                "the 10.5.0 parser; the statistic is logged under its id "
                "'treeReport.age'"
            )
        return

    _check_version(raw, analysis, "<branchLengthStatistic>", errors)
    if "name" in raw and not isinstance(raw.get("name"), str):
        errors.append("analysis.name: must be a string")
    taxa = raw.get("mrca_taxa")
    if taxa is None:
        return
    if not isinstance(taxa, list) or len(taxa) < 2:
        errors.append("analysis.mrca_taxa: must be a list of at least two taxon names")
        return
    for index, taxon in enumerate(taxa):
        if not isinstance(taxon, str) or not taxon:
            errors.append(f"analysis.mrca_taxa[{index}]: must be a non-empty taxon name")


def _require_control_version(config: Any, analysis: str, element: str) -> None:
    version = getattr(getattr(config, "control", None), "beast_version", None)
    if version != "10.5.0":
        raise ModelAssemblyError(
            f"analysis.type {analysis!r} requires control.beast_version "
            f"'10.5.0' (got {version!r}); {element} is not available in "
            "BEAST 1.10.4"
        )


def _top_level(root: Dict[str, Any], tag: str) -> List[Dict[str, Any]]:
    return [
        node
        for node in root.get("children", [])
        if isinstance(node, dict) and node.get("tag") == tag
    ]


def _node_by_id(root: Dict[str, Any], tag: str, node_id: str) -> Dict[str, Any]:
    for node in find_all(root, tag):
        if (node.get("attrib") or {}).get("id") == node_id:
            return node
    raise ModelAssemblyError(f"cannot locate <{tag} id={node_id!r}>")


def _insert_after(
    root: Dict[str, Any], anchor: Dict[str, Any], nodes: List[Dict[str, Any]]
) -> None:
    children = root.setdefault("children", [])
    index = children.index(anchor)
    children[index + 1 : index + 1] = nodes


def _add_column(log: Dict[str, Any], tag: str, idref: str) -> None:
    log.setdefault("children", []).append(el(tag, attrib={"idref": idref}))


def _log_nodes(root: Dict[str, Any], tag: str, idref: str) -> None:
    for log in find_all(root, "log"):
        if (log.get("attrib") or {}).get("id") in ("screenLog", "fileLog"):
            _add_column(log, tag, idref)


def _alignment_taxa(config: Any) -> List[str]:
    names: List[str] = []
    for partition in getattr(config, "partitions", []):
        for sequence in partition.alignment.sequences:
            if sequence.taxon not in names:
                names.append(sequence.taxon)
    return names


def _taxon_id(name: str) -> str:
    return name if _ID_SAFE.match(name) else re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _apply_age_statistic(root: Dict[str, Any], analysis: Dict[str, Any], config: Any) -> None:
    if not getattr(config, "tip_dates", None):
        raise ModelAssemblyError(
            "analysis.type 'age_statistic' requires top-level tip_dates: "
            "<ageStatistic> computes the age from Taxon.getMostRecentDate() "
            "and has no absolute time axis without dated tips"
        )
    tree_model = next(iter(_top_level(root, "treeModel")), None)
    if tree_model is None:
        raise ModelAssemblyError("assembled XML has no top-level <treeModel>")

    height_id = "treeReport.treeHeight"
    age_id = "treeReport.age"
    nodes = [
        el(
            "treeHeightStatistic",
            attrib={"id": height_id},
            children=[el("treeModel", attrib={"idref": "treeModel"})],
        ),
        el(
            "ageStatistic",
            attrib={"id": age_id},
            children=[el("treeHeightStatistic", attrib={"idref": height_id})],
        ),
    ]
    _insert_after(root, tree_model, nodes)
    _log_nodes(root, "ageStatistic", age_id)


def _apply_tree_report(root: Dict[str, Any], analysis: Dict[str, Any], config: Any) -> None:
    tree_model = next(iter(_top_level(root, "treeModel")), None)
    if tree_model is None:
        raise ModelAssemblyError("assembled XML has no top-level <treeModel>")

    available = _alignment_taxa(config)
    requested = analysis.get("mrca_taxa")
    if requested is None:
        requested = available[:2]
    taxa: List[str] = []
    for name in requested:
        if name not in available:
            raise ModelAssemblyError(
                f"analysis.mrca_taxa: taxon {name!r} is not in the alignment; "
                f"known taxa: {', '.join(available)}"
            )
        if name not in taxa:
            taxa.append(name)
    if len(taxa) < 2:
        raise ModelAssemblyError(
            "analysis.mrca_taxa: at least two distinct alignment taxa are required"
        )

    taxa_id = "treeReport.taxa"
    stat_id = "treeReport.stemLength"
    global_taxa = _node_by_id(root, "taxa", "taxa")
    taxa_node = el(
        "taxa",
        attrib={"id": taxa_id},
        children=[el("taxon", attrib={"idref": _taxon_id(name)}) for name in taxa],
    )
    _insert_after(root, global_taxa, [taxa_node])
    stat_node = el(
        "branchLengthStatistic",
        attrib={"id": stat_id, "name": _name(analysis)},
        children=[
            el("treeModel", attrib={"idref": "treeModel"}),
            el("mrca", children=[el("taxa", attrib={"idref": taxa_id})]),
        ],
    )
    _insert_after(root, tree_model, [stat_node])
    _log_nodes(root, "branchLengthStatistic", stat_id)


def apply(
    root: Dict[str, Any], analysis: Dict[str, Any], config: Any, assembler: Any
) -> Dict[str, Any]:
    analysis_type = str(analysis.get("type"))
    if analysis_type == "age_statistic":
        _require_control_version(config, analysis_type, "<ageStatistic>")
        _apply_age_statistic(root, analysis, config)
    else:
        _require_control_version(config, analysis_type, "<branchLengthStatistic>")
        _apply_tree_report(root, analysis, config)
    return root
