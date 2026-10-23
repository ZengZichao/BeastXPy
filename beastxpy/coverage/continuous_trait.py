"""Continuous trait analysis (Brownian motion over the core tree).

Inline ``analysis.values`` (``taxon -> number``) becomes a one-dimensional
trait parameter in tip (taxa) order.  The trait evolves under a multivariate
Brownian-motion diffusion model (here 1x1: the ``precision`` is the inverse
Brownian variance rate).  Internal trait values are integrated out with a
conjugate root prior and the precision is estimated by a Gibbs sampler, so the
model is the standard BEAST phylogenetic BM / comparative-method model.

The trait likelihood is added to the joint likelihood of the core divergence
time analysis and the diffusion precision is logged.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from ..model import el, param_node
from . import find_all

ANALYSIS_TYPES = ("continuous_trait", "continuous_traits")

_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")


def _declaration(root: Dict[str, Any], tag: str, id_: str) -> Optional[Dict[str, Any]]:
    for node in find_all(root, tag):
        if (node.get("attrib") or {}).get("id") == id_:
            return node
    return None


def _number(raw: Dict[str, Any], key: str, default: float, errors: List[str]) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"analysis.{key}: must be numeric (got {value!r})")
        return default
    return float(value)


def _trait_name(raw: Dict[str, Any], errors: List[str]) -> str:
    name = raw.get("trait_name", "trait")
    if not isinstance(name, str) or not name:
        errors.append(f"analysis.trait_name: must be a non-empty string (got {name!r})")
        return "trait"
    if not set(name) <= _NAME_CHARS or name[0].isdigit():
        errors.append(
            "analysis.trait_name: must be an XML/BEAST attribute name "
            "(letters, digits, '_', '-', '.'; must not start with a digit) "
            f"(got {name!r})"
        )
    return name


def _values(raw: Dict[str, Any], errors: List[str]) -> Dict[str, float]:
    values = raw.get("values")
    if not isinstance(values, dict) or not values:
        errors.append(
            "analysis.values: required mapping of taxon name to numeric trait "
            "value (got " + (repr(values) if values is not None else "nothing") + ")"
        )
        return {}
    cleaned: Dict[str, float] = {}
    for taxon, value in values.items():
        if not isinstance(taxon, str) or not taxon.strip():
            errors.append(f"analysis.values: taxon keys must be non-empty strings (got {taxon!r})")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(
                f"analysis.values[{taxon!r}]: trait value must be numeric " f"(got {value!r})"
            )
            continue
        number = float(value)
        if not math.isfinite(number):
            errors.append(
                f"analysis.values[{taxon!r}]: trait value must be finite " f"(got {value!r})"
            )
            continue
        cleaned[taxon] = number
    if len(cleaned) < 2:
        errors.append(
            "analysis.values: a continuous trait needs at least two tip values "
            f"(got {len(cleaned)})"
        )
    return cleaned


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    _values(raw, errors)
    _trait_name(raw, errors)
    precision = _number(raw, "precision", 1.0, errors)
    if precision <= 0.0:
        errors.append(f"analysis.precision: must be > 0 (got {precision})")
    root_mean = _number(raw, "root_mean", 0.0, errors)
    if not math.isfinite(root_mean):
        errors.append(f"analysis.root_mean: must be finite (got {root_mean!r})")
    prior_scale = _number(raw, "prior_scale", 1.0, errors)
    if prior_scale <= 0.0:
        errors.append(f"analysis.prior_scale: must be > 0 (got {prior_scale})")
    prior_df = raw.get("prior_df", 1)
    if isinstance(prior_df, bool) or not isinstance(prior_df, int) or prior_df < 1:
        errors.append(f"analysis.prior_df: must be an integer >= 1 (got {prior_df!r})")
    estimate = raw.get("estimate_rate", True)
    if not isinstance(estimate, bool):
        errors.append(f"analysis.estimate_rate: must be true or false (got {estimate!r})")


def _taxon_nodes(root: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    candidates = [node for node in find_all(root, "taxa") if node.get("attrib")]
    taxa = next(
        (node for node in candidates if (node.get("attrib") or {}).get("id") == "taxa"),
        candidates[0] if candidates else None,
    )
    if taxa is None:
        return None, []
    return taxa, [child for child in (taxa.get("children") or []) if child.get("tag") == "taxon"]


def _insert_before(root: Dict[str, Any], anchor_tag: str, nodes: List[Dict[str, Any]]) -> None:
    children = root.setdefault("children", [])
    position = next(
        (index for index, node in enumerate(children) if node.get("tag") == anchor_tag),
        len(children),
    )
    for offset, node in enumerate(nodes):
        children.insert(position + offset, node)


def _append_to_root_by_id(
    root: Dict[str, Any], tag: str, id_: str, nodes: List[Dict[str, Any]]
) -> None:
    parent = _declaration(root, tag, id_)
    if parent is None:
        raise ValueError(f"continuous_trait requires a core <{tag} id={id_!r}>")
    parent.setdefault("children", []).extend(nodes)


def apply(
    root: Dict[str, Any], analysis: Dict[str, Any], config: Any, assembler: Any
) -> Dict[str, Any]:
    values = analysis.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError("continuous_trait requires a non-empty analysis.values mapping")

    taxa_node, taxon_nodes = _taxon_nodes(root)
    if taxa_node is None:
        raise ValueError("continuous_trait requires the core <taxa> block")
    taxa_names = [
        name
        for node in taxon_nodes
        if isinstance(name := (node.get("attrib") or {}).get("id"), str) and name
    ]
    unknown_taxa = sorted(set(values) - set(taxa_names))
    missing_taxa = sorted(set(taxa_names) - set(values))
    if unknown_taxa:
        raise ValueError(
            "analysis.values: unknown taxon(s) not present in the core <taxa> block: "
            + ", ".join(repr(name) for name in unknown_taxa)
        )
    if missing_taxa:
        raise ValueError(
            "analysis.values: no trait value supplied for taxon(s): "
            + ", ".join(repr(name) for name in missing_taxa)
        )
    try:
        ordered_values = [float(values[name]) for name in taxa_names]
    except (TypeError, ValueError) as exc:
        raise ValueError("analysis.values: every trait value must be numeric " f"({exc})") from exc
    if any(not math.isfinite(value) for value in ordered_values):
        raise ValueError("analysis.values: every trait value must be finite")
    if len(ordered_values) < 2:
        raise ValueError("continuous_trait requires at least two tip values")

    trait_name = str(analysis.get("trait_name", "trait"))
    # The traitParameter is populated automatically from the taxon attributes;
    # setting values on the parameter itself is rejected by BEAST.
    for node, value in zip(taxon_nodes, ordered_values):
        node.setdefault("children", []).append(
            el("attr", attrib={"name": trait_name}, text=format_trait_value(value))
        )

    precision = float(analysis.get("precision", 1.0))
    root_mean = float(analysis.get("root_mean", 0.0))
    prior_scale = float(analysis.get("prior_scale", 1.0))
    prior_df = int(analysis.get("prior_df", 1))
    estimate = bool(analysis.get("estimate_rate", True))

    diffusion = el(
        "multivariateDiffusionModel",
        attrib={"id": "trait.diffusionModel"},
        children=[
            el(
                "precisionMatrix",
                children=[
                    el(
                        "matrixParameter",
                        attrib={"id": "trait.precision"},
                        children=[
                            param_node(
                                "trait.precision.col1",
                                precision,
                                lower=0.0 if estimate else None,
                                estimate=estimate,
                            )
                        ],
                    )
                ],
            )
        ],
    )
    precision_prior = el(
        "multivariateWishartPrior",
        attrib={"id": "trait.precisionPrior", "df": str(prior_df)},
        children=[
            el(
                "scaleMatrix",
                children=[
                    el(
                        "matrixParameter",
                        children=[el("parameter", attrib={"value": str(prior_scale)})],
                    )
                ],
            ),
            el(
                "data",
                children=[el("matrixParameter", attrib={"idref": "trait.precision"})],
            ),
        ],
    )
    likelihood = el(
        "multivariateTraitLikelihood",
        attrib={
            "id": "trait.treeLikelihood",
            "traitName": trait_name,
            "useTreeLength": "true",
            "scaleByTime": "true",
            "integrateInternalTraits": "true",
        },
        children=[
            el("multivariateDiffusionModel", attrib={"idref": "trait.diffusionModel"}),
            el("treeModel", attrib={"idref": "treeModel"}),
            el(
                "traitParameter",
                children=[el("parameter", attrib={"id": "trait.values"})],
            ),
            el(
                "conjugateRootPrior",
                children=[
                    el(
                        "meanParameter",
                        children=[el("parameter", attrib={"value": format_trait_value(root_mean)})],
                    ),
                    el(
                        "priorSampleSize",
                        children=[el("parameter", attrib={"value": "0.001"})],
                    ),
                ],
            ),
        ],
    )

    _insert_before(root, "treeLikelihood", [diffusion, precision_prior, likelihood])

    joint = _declaration(root, "likelihood", "likelihood")
    if joint is None:
        raise ValueError("continuous_trait requires the core <likelihood id='likelihood'>")
    joint.setdefault("children", []).append(
        el("multivariateTraitLikelihood", attrib={"idref": "trait.treeLikelihood"})
    )

    if estimate:
        _append_to_root_by_id(
            root,
            "prior",
            "prior",
            [el("multivariateWishartPrior", attrib={"idref": "trait.precisionPrior"})],
        )
        _append_to_root_by_id(
            root,
            "operators",
            "operators",
            [
                el(
                    "precisionGibbsOperator",
                    attrib={"weight": "10"},
                    children=[
                        el(
                            "multivariateTraitLikelihood",
                            attrib={"idref": "trait.treeLikelihood"},
                        ),
                        el(
                            "multivariateWishartPrior",
                            attrib={"idref": "trait.precisionPrior"},
                        ),
                    ],
                )
            ],
        )

    screen = _declaration(root, "log", "screenLog")
    if screen is not None and estimate:
        screen.setdefault("children", []).append(
            el(
                "column",
                attrib={"label": "trait.precision", "sf": "6", "width": "12"},
                children=[el("matrixParameter", attrib={"idref": "trait.precision"})],
            )
        )
    trace = _declaration(root, "log", "fileLog")
    if trace is not None:
        trace.setdefault("children", []).append(
            el("multivariateTraitLikelihood", attrib={"idref": "trait.treeLikelihood"})
        )
        if estimate:
            trace["children"].append(el("matrixParameter", attrib={"idref": "trait.precision"}))
    return root


def format_trait_value(value: float) -> str:
    """Format a continuous trait value without scientific notation noise."""
    return format(value, ".12g")
