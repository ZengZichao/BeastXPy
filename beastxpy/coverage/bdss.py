"""Birth-death serial skyline (BDSS) tree prior (C08).

Uses BEAST's core ``<birthDeathSerialSampling>`` model (Stadler's birth-death
sampling-through-time process with piecewise-constant rates) together with a
single background ``<speciationLikelihood>``.  The default fixture uses one
time epoch (``times`` dimension 1), relative death rate, psi and a sampling
probability, which is the smallest complete BDSS model the parser accepts.

The emitted element is ``birthDeathSerialSampling``, which is present in Gate
1's element whitelist for both BEAST versions.  ``birthDeathSkyline`` is only a
local label alias in this module, not an emitted element; it is intentionally
not whitelisted because the core BEAST 1 parser set does not contain it.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..model import ModelAssemblyError, el, param_node
from . import find_all

ANALYSIS_TYPES = ("bdss",)

_TREE_PRIOR_TAGS = {
    "yuleModel",
    "birthDeathModel",
    "birthDeathSerialSampling",
    "speciationLikelihood",
    "coalescentLikelihood",
    "generalizedSkyLineLikelihood",
    "gmrfSkyrideLikelihood",
    "exponentialGrowth",
    "logisticGrowth",
    "expansion",
}

_TREE_PRIOR_REFS = {
    "speciationLikelihood",
    "coalescentLikelihood",
    "generalizedSkyLineLikelihood",
    "gmrfSkyrideLikelihood",
    "gmrfSkyLineLikelihood",
}


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    if "sample_probability" in raw:
        errors.append(
            "analysis.sample_probability: the core birthDeathSerialSampling "
            "parser fixes p=0 and its syntax rules reject a <sampleProbability> "
            "child; remove this key"
        )
    for key in ("birth_rate", "psi", "relative_death_rate", "origin"):
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"analysis.{key}: must be a number")
            continue
        if key == "relative_death_rate" and not 0.0 <= float(value) <= 1.0:
            errors.append("analysis.relative_death_rate: must be in [0, 1]")
        elif key == "sample_probability" and not 0.0 <= float(value) <= 1.0:
            errors.append("analysis.sample_probability: must be in [0, 1]")
        elif key in ("birth_rate", "psi", "origin") and float(value) <= 0.0:
            errors.append(f"analysis.{key}: must be > 0")


def _top_level(root: Dict[str, Any], tag: str) -> List[Dict[str, Any]]:
    return [c for c in root.get("children", []) if isinstance(c, dict) and c.get("tag") == tag]


def _node_by_id(root: Dict[str, Any], tag: str, node_id: str) -> Dict[str, Any]:
    for node in find_all(root, tag):
        if (node.get("attrib") or {}).get("id") == node_id:
            return node
    raise ModelAssemblyError(f"cannot locate <{tag} id={node_id!r}>")


def _declared_parameter_ids(root: Dict[str, Any]) -> set:
    return {
        str((node.get("attrib") or {})["id"])
        for node in find_all(root, "parameter")
        if (node.get("attrib") or {}).get("id")
    }


def _references_missing_parameter(node: Dict[str, Any], declared: set) -> bool:
    stack = [node]
    while stack:
        current = stack.pop()
        attrib = current.get("attrib") or {}
        if current.get("tag") == "parameter" and "idref" in attrib:
            if str(attrib["idref"]) not in declared:
                return True
        stack.extend(current.get("children") or [])
    return False


def _prune_missing_parameter_refs(root: Dict[str, Any]) -> None:
    declared = _declared_parameter_ids(root)
    for block_tag, block_id in (("operators", "operators"), ("prior", "prior")):
        block = _node_by_id(root, block_tag, block_id)
        block["children"] = [
            node
            for node in block.get("children", [])
            if not _references_missing_parameter(node, declared)
        ]
    for log in find_all(root, "log"):
        log["children"] = [
            node
            for node in log.get("children", [])
            if not _references_missing_parameter(node, declared)
        ]


def _remove_tree_prior(root: Dict[str, Any]) -> None:
    def is_prior(node: Dict[str, Any]) -> bool:
        return node.get("tag") in _TREE_PRIOR_TAGS and node.get("tag") != "constantSize"

    root["children"] = [
        node for node in root.get("children", []) if not (isinstance(node, dict) and is_prior(node))
    ]
    prior = _node_by_id(root, "prior", "prior")
    prior["children"] = [
        node for node in prior.get("children", []) if node.get("tag") not in _TREE_PRIOR_REFS
    ]
    for log in find_all(root, "log"):
        log["children"] = [
            node for node in log.get("children", []) if node.get("tag") not in _TREE_PRIOR_REFS
        ]


def _param(
    raw: Dict[str, Any],
    key: str,
    default: float,
    lower: float,
    upper: float,
    pid: str,
    estimate: bool = True,
) -> Dict[str, Any]:
    value = raw.get(key, default)
    if isinstance(value, dict):
        value = value.get("value", default)
    return param_node(pid, float(value), lower=lower, upper=upper, estimate=estimate)


def _walk(pid: str, weight: str, window: str) -> Dict[str, Any]:
    return el(
        "randomWalkOperator",
        attrib={"windowSize": window, "weight": weight},
        children=[el("parameter", attrib={"idref": pid})],
    )


def _column(label: str, pid: str) -> Dict[str, Any]:
    return el(
        "column",
        attrib={"label": label, "sf": "6", "width": "12"},
        children=[el("parameter", attrib={"idref": pid})],
    )


def apply(
    root: Dict[str, Any], analysis: Dict[str, Any], config: Any, assembler: Any
) -> Dict[str, Any]:
    if len(config.partitions) != 1:
        raise ModelAssemblyError("analysis.type 'bdss' currently requires exactly one partition")
    _remove_tree_prior(root)

    origin = analysis.get("origin", 10.0)
    if isinstance(origin, dict):
        origin = origin.get("value", 10.0)
    model = el(
        "birthDeathSerialSampling",
        attrib={
            "id": "birthDeath",
            "hasFinalSample": "false",
        },
        children=[
            el(
                "birthRate",
                children=[_param(analysis, "birth_rate", 1.0, 0.0, 100.0, "bdss.birthRate")],
            ),
            el(
                "relativeDeathRate",
                children=[
                    _param(analysis, "relative_death_rate", 0.5, 0.0, 1.0, "bdss.relativeDeathRate")
                ],
            ),
            el("psi", children=[_param(analysis, "psi", 0.5, 0.0, 100.0, "bdss.psi")]),
            el("origin", children=[param_node("bdss.origin", float(origin), estimate=False)]),
        ],
    )
    likelihood = el(
        "speciationLikelihood",
        attrib={"id": "speciation"},
        children=[
            el("model", children=[el("birthDeathSerialSampling", attrib={"idref": "birthDeath"})]),
            el("speciesTree", children=[el("treeModel", attrib={"idref": "treeModel"})]),
        ],
    )
    tree_model = next(iter(_top_level(root, "treeModel")), None)
    if tree_model is None:
        raise ModelAssemblyError("assembled XML has no top-level <treeModel>")
    children = root.setdefault("children", [])
    children[children.index(tree_model) + 1 : children.index(tree_model) + 1] = [model, likelihood]

    prior = _node_by_id(root, "prior", "prior")
    prior.setdefault("children", []).append(
        el("speciationLikelihood", attrib={"idref": "speciation"})
    )
    prior["children"].extend(
        [
            el(
                "logNormalPrior",
                attrib={
                    "mean": "1.0",
                    "stdev": "1.25",
                    "offset": "0.0",
                    "meanInRealSpace": "false",
                },
                children=[el("parameter", attrib={"idref": "bdss.birthRate"})],
            ),
            el(
                "uniformPrior",
                attrib={"lower": "0.0", "upper": "1.0"},
                children=[el("parameter", attrib={"idref": "bdss.relativeDeathRate"})],
            ),
            el(
                "logNormalPrior",
                attrib={"mean": "0.5", "stdev": "1.0", "offset": "0.0", "meanInRealSpace": "false"},
                children=[el("parameter", attrib={"idref": "bdss.psi"})],
            ),
        ]
    )

    operators = _node_by_id(root, "operators", "operators")
    operators.setdefault("children", []).extend(
        [
            _walk("bdss.birthRate", "3", "0.5"),
            _walk("bdss.relativeDeathRate", "3", "0.1"),
            _walk("bdss.psi", "3", "0.5"),
        ]
    )

    for log in find_all(root, "log"):
        log_id = (log.get("attrib") or {}).get("id")
        if log_id in ("screenLog", "fileLog"):
            log.setdefault("children", []).extend(
                [
                    _column("bdss.birthRate", "bdss.birthRate"),
                    _column("bdss.relativeDeathRate", "bdss.relativeDeathRate"),
                    _column("bdss.psi", "bdss.psi"),
                ]
            )

    _prune_missing_parameter_refs(root)
    return root
