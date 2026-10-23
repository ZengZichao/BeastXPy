"""Sequence error model (BEAUti ``sequenceerror`` component).

The core tree likelihood is extended with a ``<sequenceErrorModel>`` tip-state
model.  BEAUti's generator emits the model after the site model and references
it *inside* the tree likelihood; a post-mortem damage model has one or both of
``baseErrorRate`` (per-site damage probability) and ``ageRelatedErrorRate``
(per-site, per-unit-time rate, scaled by tip age).  Both rates are proper
parameters with operators, priors and log columns.

The model is scientifically the standard BEAST aDNA damage model: each tip
state is a mixture of the observed nucleotide and a damaged state (transitions
only or all substitutions), with the undamaged probability
``(1 - baseRate) * exp(-ageRate * age)``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..model import el, param_node
from . import find_all

ANALYSIS_TYPES = ("sequence_error", "sequenceerror")

_ERROR_TYPES = ("all", "transitions")


def _declaration(root: Dict[str, Any], tag: str, id_: str) -> Any:
    for node in find_all(root, tag):
        attrib = node.get("attrib") or {}
        if attrib.get("id") == id_:
            return node
    return None


def _number(raw: Dict[str, Any], key: str, default: float, errors: List[str]) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"analysis.{key}: must be numeric (got {value!r})")
        return default
    return float(value)


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    error_type = raw.get("error_type", "all")
    if not isinstance(error_type, str) or error_type not in _ERROR_TYPES:
        errors.append(
            f"analysis.error_type: unknown {error_type!r}; supported: " + ", ".join(_ERROR_TYPES)
        )
    has_base = "base_error_rate" in raw
    has_age = "age_related_error_rate" in raw
    if not has_base and not has_age:
        errors.append(
            "analysis: a sequence error model requires base_error_rate and/or "
            "age_related_error_rate (BEAST's SequenceErrorModelParser rejects a "
            "model with neither)"
        )
    base = _number(raw, "base_error_rate", 0.01, errors)
    if base < 0.0 or base >= 1.0:
        errors.append(f"analysis.base_error_rate: must be in [0, 1) (got {base})")
    age = _number(raw, "age_related_error_rate", 0.0, errors)
    if age < 0.0:
        errors.append(f"analysis.age_related_error_rate: must be >= 0 (got {age})")
    for key in ("include_taxa", "exclude_taxa"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            errors.append(f"analysis.{key}: must be a list of non-empty taxon names")
    if raw.get("include_taxa") and raw.get("exclude_taxa"):
        errors.append(
            "analysis: include_taxa and exclude_taxa are mutually exclusive "
            "(BEAST's sequenceErrorModel uses an XOR rule)"
        )
    estimate = raw.get("estimate", True)
    if not isinstance(estimate, bool):
        errors.append(f"analysis.estimate: must be true or false (got {estimate!r})")


def _add_parameter_targets(
    root: Dict[str, Any],
    pid: str,
    bounds: tuple,
    operator: Dict[str, Any],
    prior: Dict[str, Any],
) -> None:
    operators = _declaration(root, "operators", "operators")
    if operators is not None:
        operator.setdefault("children", []).append(el("parameter", attrib={"idref": pid}))
        operators.setdefault("children", []).append(operator)
    prior_node = _declaration(root, "prior", "prior")
    if prior_node is not None:
        prior.setdefault("children", []).append(el("parameter", attrib={"idref": pid}))
        prior_node.setdefault("children", []).append(prior)
    screen = _declaration(root, "log", "screenLog")
    if screen is not None:
        screen.setdefault("children", []).append(
            el(
                "column",
                attrib={"label": pid, "sf": "6", "width": "12"},
                children=[el("parameter", attrib={"idref": pid})],
            )
        )
    trace = _declaration(root, "log", "fileLog")
    if trace is not None:
        trace.setdefault("children", []).append(el("parameter", attrib={"idref": pid}))


def apply(
    root: Dict[str, Any], analysis: Dict[str, Any], config: Any, assembler: Any
) -> Dict[str, Any]:
    children = root.setdefault("children", [])
    likelihoods = [
        node
        for node in find_all(root, "treeLikelihood")
        if (node.get("attrib") or {}).get("id") is not None
    ]
    if not likelihoods:
        raise ValueError("sequence_error requires a core <treeLikelihood>")
    index = int(analysis.get("partition", 1)) - 1
    if index < 0 or index >= len(likelihoods):
        raise ValueError(
            f"analysis.partition: {analysis.get('partition')!r} does not select one "
            f"of the {len(likelihoods)} assembled tree likelihood(s)"
        )
    likelihood = likelihoods[index]

    error_type = str(analysis.get("error_type", "all"))
    estimate = bool(analysis.get("estimate", True))
    base_rate = float(analysis.get("base_error_rate", 0.01))
    age_rate = analysis.get("age_related_error_rate")

    model_children: List[Dict[str, Any]] = []
    if "base_error_rate" in analysis:
        model_children.append(
            el(
                "baseErrorRate",
                children=[
                    param_node(
                        "errorModel.baseErrorRate",
                        base_rate,
                        lower=0.0,
                        upper=1.0,
                        estimate=estimate,
                    )
                ],
            )
        )
    if age_rate is not None:
        model_children.append(
            el(
                "ageRelatedErrorRate",
                children=[
                    param_node(
                        "errorModel.ageRate",
                        float(age_rate),
                        lower=0.0,
                        estimate=estimate,
                    )
                ],
            )
        )

    includes = analysis.get("include_taxa") or []
    excludes = analysis.get("exclude_taxa") or []
    all_taxa = {
        seq.taxon
        for partition in getattr(config, "partitions", [])
        for seq in partition.alignment.sequences
    }
    for label, names in (("include_taxa", includes), ("exclude_taxa", excludes)):
        unknown = sorted(set(names) - all_taxa)
        if unknown:
            raise ValueError(
                f"analysis.{label}: unknown taxon name(s) {unknown}; "
                f"available taxa: {', '.join(sorted(all_taxa))}"
            )
    taxa_children: List[Dict[str, Any]] = []
    filter_tag = None
    if includes:
        filter_tag = "include"
        taxa_children = [el("taxon", attrib={"idref": name}) for name in includes]
    elif excludes:
        filter_tag = "exclude"
        taxa_children = [el("taxon", attrib={"idref": name}) for name in excludes]

    model = el(
        "sequenceErrorModel",
        attrib={"id": "errorModel", "type": error_type},
        children=model_children,
    )
    if taxa_children:
        assert filter_tag is not None
        taxa_id = "errorModel.taxa"
        model["children"].append(el(filter_tag, children=[el("taxa", attrib={"idref": taxa_id})]))
        taxa_node = el(
            "taxa",
            attrib={"id": taxa_id},
            children=taxa_children,
        )
    else:
        taxa_node = None

    likelihood.setdefault("children", []).append(
        el("sequenceErrorModel", attrib={"idref": "errorModel"})
    )

    insertion = (taxa_node, model) if taxa_node is not None else (model,)
    position = children.index(likelihood)
    for offset, node in enumerate(insertion):
        children.insert(position + offset, node)

    if estimate and "base_error_rate" in analysis:
        _add_parameter_targets(
            root,
            "errorModel.baseErrorRate",
            (0.0, 1.0),
            el("randomWalkOperator", attrib={"windowSize": "0.05", "weight": "1"}),
            el("uniformPrior", attrib={"lower": "0.0", "upper": "1.0"}),
        )
    if estimate and age_rate is not None:
        _add_parameter_targets(
            root,
            "errorModel.ageRate",
            (0.0, None),
            el("scaleOperator", attrib={"scaleFactor": "0.75", "weight": "1"}),
            el("exponentialPrior", attrib={"mean": "0.1"}),
        )
    return root
