"""Amino-acid mixture model (C07/C09, BEAST X 10.5.0 only).

BEAST 1.10.4 has no ``instantaneousMixtureSubstitutionModel`` and no
``aminoAcidMixtureModel`` parser, so this analysis is version-gated to
10.5.0.  The emitted model mixes two empirical amino-acid rate matrices
(default WAG and JTT) with a free mixing weight and a shared 20-state root
frequency model, exactly the structure consumed by
``dr.evomodelxml.substmodel.InstantaneousMixtureSubstitutionModelParser``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..model import ModelAssemblyError, el, param_node
from . import find_all

ANALYSIS_TYPES = ("aa_mixture",)

EMPIRICAL_MODELS = {
    "wag": "WAG",
    "jtt": "JTT",
    "dayhoff": "dayhoff",
    "blosum62": "blosum62",
    "cprev": "cpREV",
    "mtrev": "mtREV",
}


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    version = raw.get("beast_version")
    if version != "10.5.0":
        errors.append(
            "analysis.beast_version: must be '10.5.0'; the amino-acid mixture "
            "model (instantaneousMixtureSubstitutionModel) does not exist in "
            "BEAST 1.10.4"
        )
    components = raw.get("components", ["wag", "jtt"])
    if not isinstance(components, list) or len(components) != 2:
        errors.append(
            "analysis.components: exactly two empirical amino-acid models are "
            "required (instantaneousMixtureSubstitutionModel supports two)"
        )
        return
    for index, name in enumerate(components):
        key = str(name).strip().lower()
        if key not in EMPIRICAL_MODELS:
            errors.append(
                f"analysis.components[{index}]: unknown empirical model {name!r}; "
                "choose from " + ", ".join(sorted(EMPIRICAL_MODELS))
            )
    weight = raw.get("weight", 0.5)
    if isinstance(weight, dict):
        weight = weight.get("value", 0.5)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        errors.append("analysis.weight: must be a number in [0, 1]")
    elif not 0.0 <= float(weight) <= 1.0:
        errors.append("analysis.weight: must be in [0, 1]")


def _top_level(root: Dict[str, Any], tag: str) -> List[Dict[str, Any]]:
    return [c for c in root.get("children", []) if isinstance(c, dict) and c.get("tag") == tag]


def _node_by_id(root: Dict[str, Any], tag: str, node_id: str) -> Dict[str, Any]:
    for node in find_all(root, tag):
        if (node.get("attrib") or {}).get("id") == node_id:
            return node
    raise ModelAssemblyError(f"cannot locate <{tag} id={node_id!r}>")


def _equal_frequencies(dimension: int) -> str:
    unit = round(1.0 / dimension, 12)
    values = [unit] * dimension
    values[-1] = round(1.0 - sum(values[:-1]), 12)
    return " ".join(f"{value:.12g}" for value in values)


def apply(
    root: Dict[str, Any], analysis: Dict[str, Any], config: Any, assembler: Any
) -> Dict[str, Any]:
    if len(config.partitions) != 1:
        raise ModelAssemblyError(
            "analysis.type 'aa_mixture' currently requires exactly one partition"
        )
    version = getattr(getattr(config, "control", None), "beast_version", "")
    if version != "10.5.0":
        raise ModelAssemblyError(
            "analysis.type 'aa_mixture' requires beast_version 10.5.0; the "
            f"instantaneous amino-acid mixture parser is absent in {version!r}"
        )
    partition = config.partitions[0]
    data_type = getattr(partition.alignment, "data_type", None)
    value = getattr(data_type, "value", data_type)
    if str(value) != "amino-acid":
        raise ModelAssemblyError(
            "analysis.type 'aa_mixture' requires an amino-acid alignment " f"(got {value!r})"
        )

    components = [str(c).strip().lower() for c in analysis.get("components", ["wag", "jtt"])]
    weight = analysis.get("weight", 0.5)
    if isinstance(weight, dict):
        weight = weight.get("value", 0.5)

    old_model = next(iter(_top_level(root, "aminoAcidModel")), None)
    if old_model is None:
        raise ModelAssemblyError(
            "aa_mixture analysis expects the core empirical aminoAcidModel fragment"
        )
    site_model = next(iter(_top_level(root, "siteModel")), None)
    if site_model is None:
        raise ModelAssemblyError("assembled XML has no top-level <siteModel>")

    component_nodes = []
    for index, key in enumerate(components):
        component_nodes.append(
            el(
                "aminoAcidModel",
                attrib={"id": f"aaMixture.component{index}", "type": EMPIRICAL_MODELS[key]},
            )
        )
    frequency_model = el(
        "frequencyModel",
        attrib={"id": "aaMixture.freq", "dataType": "amino acid"},
        children=[
            el(
                "frequencies",
                children=[
                    param_node(
                        "aaMixture.frequencies",
                        _equal_frequencies(20),
                        dimension=20,
                        estimate=False,
                    )
                ],
            ),
        ],
    )
    mixture = el(
        "instantaneousMixtureSubstitutionModel",
        attrib={"id": "aaMixture", "dataType": "amino acid"},
        children=[
            el(
                "rootFrequencies",
                children=[el("frequencyModel", attrib={"idref": "aaMixture.freq"})],
            ),
            component_nodes[0],
            component_nodes[1],
            el(
                "mixtureWeights",
                children=[
                    param_node(
                        "aaMixture.weight", float(weight), lower=0.0, upper=1.0, estimate=True
                    )
                ],
            ),
        ],
    )

    children = root.setdefault("children", [])
    index = children.index(old_model)
    children[index : index + 1] = [frequency_model, mixture]

    substitution = None
    for child in site_model.get("children") or []:
        if child.get("tag") == "substitutionModel":
            substitution = child
            break
    if substitution is None:
        raise ModelAssemblyError("the emitted <siteModel> has no <substitutionModel>")
    substitution["children"] = [
        el("instantaneousMixtureSubstitutionModel", attrib={"idref": "aaMixture"})
    ]

    operators = _node_by_id(root, "operators", "operators")
    operators.setdefault("children", []).append(
        el(
            "randomWalkOperator",
            attrib={"windowSize": "0.1", "weight": "1"},
            children=[el("parameter", attrib={"idref": "aaMixture.weight"})],
        )
    )
    prior = _node_by_id(root, "prior", "prior")
    prior.setdefault("children", []).append(
        el(
            "uniformPrior",
            attrib={"lower": "0.0", "upper": "1.0"},
            children=[el("parameter", attrib={"idref": "aaMixture.weight"})],
        )
    )
    file_log = next(
        (log for log in find_all(root, "log") if (log.get("attrib") or {}).get("id") == "fileLog"),
        None,
    )
    if file_log is not None:
        file_log.setdefault("children", []).append(
            el(
                "column",
                attrib={"label": "aaMixture.weight", "sf": "6", "width": "12"},
                children=[el("parameter", attrib={"idref": "aaMixture.weight"})],
            )
        )
    return root
