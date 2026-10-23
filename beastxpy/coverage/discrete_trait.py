"""Discrete trait / phylogeography analysis (BEAST 1 ``ancestralTreeLikelihood``).

The plugin turns inline ``analysis.values`` (``taxon -> state``) into a BEAST
discrete character matrix.  The matrix is stored as ``<attr>`` children of the
core ``<taxa>`` block, wrapped by an ``<attributePatterns>`` object over a
``<generalDataType>``.  A general (symmetric, reversible) CTMC model and a
strict clock are attached to the *core* ``<treeModel>`` through an
``<ancestralTreeLikelihood>``.  The trait likelihood is added to the joint
likelihood and the relative rates and trait clock rate are estimated with
operators, priors and log columns.

The analysis is run jointly with the core sequence likelihood, mirroring
BEAUti's discrete trait phylogeography template.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..model import el, param_node
from . import find_all

ANALYSIS_TYPES = ("discrete_trait", "discrete_traits")

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


def _values(raw: Dict[str, Any], errors: List[str]) -> Dict[str, str]:
    values = raw.get("values")
    if not isinstance(values, dict) or not values:
        errors.append(
            "analysis.values: required mapping of taxon name to discrete state "
            "(got " + (repr(values) if values is not None else "nothing") + ")"
        )
        return {}
    cleaned: Dict[str, str] = {}
    for taxon, state in values.items():
        if not isinstance(taxon, str) or not taxon.strip():
            errors.append(f"analysis.values: taxon keys must be non-empty strings (got {taxon!r})")
            continue
        if not isinstance(state, str) or not state.strip():
            errors.append(
                f"analysis.values[{taxon!r}]: state must be a non-empty string " f"(got {state!r})"
            )
            continue
        cleaned[taxon] = state
    return cleaned


def _states(raw: Dict[str, Any], values: Dict[str, str], errors: List[str]) -> List[str]:
    declared = raw.get("states")
    if declared is not None:
        if not isinstance(declared, (list, tuple)) or not declared:
            errors.append("analysis.states: must be a non-empty list of state names")
            return []
        states: List[str] = []
        for state in declared:
            if not isinstance(state, str) or not state:
                errors.append(
                    f"analysis.states: state names must be non-empty strings " f"(got {state!r})"
                )
                continue
            if state in states:
                errors.append(f"analysis.states: duplicate state {state!r}")
            else:
                states.append(state)
        if len(states) < 2:
            errors.append(
                f"analysis.states: a discrete trait needs at least two states "
                f"(got {len(states)})"
            )
        unknown = sorted({state for state in values.values() if state not in states})
        if unknown:
            errors.append(
                "analysis.values: unknown state(s) "
                + ", ".join(repr(s) for s in unknown)
                + "; declared states: "
                + ", ".join(repr(s) for s in states)
            )
        return states
    observed = sorted(set(values.values()))
    if len(observed) < 2:
        errors.append(
            "analysis.values: a discrete trait needs at least two distinct states "
            f"(got {len(observed)}: {observed})"
        )
    return observed


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    values = _values(raw, errors)
    _states(raw, values, errors)
    _trait_name(raw, errors)
    clock_rate = _number(raw, "clock_rate", 1.0, errors)
    if clock_rate <= 0.0:
        errors.append(f"analysis.clock_rate: must be > 0 (got {clock_rate})")
    for key in ("estimate_rates", "estimate_clock_rate"):
        value = raw.get(key, True)
        if not isinstance(value, bool):
            errors.append(f"analysis.{key}: must be true or false (got {value!r})")


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
        raise ValueError(f"discrete_trait requires a core <{tag} id={id_!r}>")
    parent.setdefault("children", []).extend(nodes)


def _gamma_prior(pid: str, shape: float = 1.0, scale: float = 1.0) -> Dict[str, Any]:
    return el(
        "gammaPrior",
        attrib={"shape": str(shape), "scale": str(scale), "offset": "0.0"},
        children=[el("parameter", attrib={"idref": pid})],
    )


def _log_parameter(pid: str) -> Dict[str, Any]:
    return el("parameter", attrib={"idref": pid})


def apply(
    root: Dict[str, Any], analysis: Dict[str, Any], config: Any, assembler: Any
) -> Dict[str, Any]:
    values = analysis.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError("discrete_trait requires a non-empty analysis.values mapping")
    declared = analysis.get("states")
    if declared is not None:
        if not isinstance(declared, (list, tuple)) or not declared:
            raise ValueError("analysis.states: must be a non-empty list of state names")
        states = []
        for state in declared:
            if not isinstance(state, str) or not state:
                raise ValueError(
                    f"analysis.states: state names must be non-empty strings (got {state!r})"
                )
            if state in states:
                raise ValueError(f"analysis.states: duplicate state {state!r}")
            states.append(state)
    else:
        states = sorted(set(values.values()))
    if len(states) < 2:
        raise ValueError("discrete_trait requires at least two states")

    taxa_node, taxon_nodes = _taxon_nodes(root)
    if taxa_node is None:
        raise ValueError("discrete_trait requires the core <taxa> block")
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
            "analysis.values: no state supplied for taxon(s): "
            + ", ".join(repr(name) for name in missing_taxa)
        )
    unknown_states = sorted({value for value in values.values() if value not in states})
    if unknown_states:
        raise ValueError(
            "analysis.values: unknown state(s) "
            + ", ".join(repr(state) for state in unknown_states)
            + "; declared states: "
            + ", ".join(repr(state) for state in states)
        )

    trait_name = str(analysis.get("trait_name", "trait"))
    estimate_rates = bool(analysis.get("estimate_rates", True))
    estimate_clock = bool(analysis.get("estimate_clock_rate", True))
    clock_rate = float(analysis.get("clock_rate", 1.0))

    # Attach the states to the core taxa so <attributePatterns> can read them.
    for node in taxon_nodes:
        taxon_id = (node.get("attrib") or {}).get("id")
        node.setdefault("children", []).append(
            el("attr", attrib={"name": trait_name}, text=values[taxon_id])
        )

    n_states = len(states)
    n_rates = n_states * (n_states - 1) // 2
    equal_frequencies = [1.0 / n_states] * n_states

    data_type = el(
        "generalDataType",
        attrib={"id": "trait.dataType"},
        children=[el("state", attrib={"code": state}) for state in states],
    )
    patterns = el(
        "attributePatterns",
        attrib={"id": "trait.pattern", "attribute": trait_name},
        children=[
            el("taxa", attrib={"idref": (taxa_node.get("attrib") or {}).get("id")}),
            el("generalDataType", attrib={"idref": "trait.dataType"}),
        ],
    )
    frequencies = el(
        "frequencies",
        children=[
            el(
                "frequencyModel",
                attrib={"id": "trait.frequencyModel", "normalize": "true"},
                children=[
                    el("generalDataType", attrib={"idref": "trait.dataType"}),
                    el(
                        "frequencies",
                        children=[
                            param_node(
                                "trait.frequencies",
                                value=equal_frequencies,
                                dimension=n_states,
                            )
                        ],
                    ),
                ],
            )
        ],
    )
    rates = el(
        "rates",
        children=[
            param_node(
                "trait.rates",
                1.0,
                lower=0.0 if estimate_rates else None,
                dimension=n_rates,
                estimate=estimate_rates,
            )
        ],
    )
    model = el(
        "generalSubstitutionModel",
        attrib={"id": "trait.model"},
        children=[
            el("generalDataType", attrib={"idref": "trait.dataType"}),
            frequencies,
            rates,
        ],
    )
    site_model = el(
        "siteModel",
        attrib={"id": "trait.siteModel"},
        children=[
            el(
                "substitutionModel",
                children=[el("generalSubstitutionModel", attrib={"idref": "trait.model"})],
            )
        ],
    )
    branch_rates = el(
        "strictClockBranchRates",
        attrib={"id": "trait.branchRates"},
        children=[
            el(
                "rate",
                children=[
                    param_node(
                        "trait.clock.rate",
                        clock_rate,
                        lower=0.0 if estimate_clock else None,
                        estimate=estimate_clock,
                    )
                ],
            )
        ],
    )
    likelihood = el(
        "ancestralTreeLikelihood",
        attrib={"id": "trait.treeLikelihood", "stateTagName": trait_name + ".states"},
        children=[
            el("attributePatterns", attrib={"idref": "trait.pattern"}),
            el("treeModel", attrib={"idref": "treeModel"}),
            el("siteModel", attrib={"idref": "trait.siteModel"}),
            el("generalSubstitutionModel", attrib={"idref": "trait.model"}),
            el("strictClockBranchRates", attrib={"idref": "trait.branchRates"}),
        ],
    )

    _insert_before(
        root,
        "treeLikelihood",
        [data_type, patterns, model, site_model, branch_rates, likelihood],
    )

    joint = _declaration(root, "likelihood", "likelihood")
    if joint is None:
        raise ValueError("discrete_trait requires the core <likelihood id='likelihood'>")
    joint.setdefault("children", []).append(
        el("ancestralTreeLikelihood", attrib={"idref": "trait.treeLikelihood"})
    )

    prior_children: List[Dict[str, Any]] = []
    if estimate_rates:
        prior_children.append(_gamma_prior("trait.rates"))
    if estimate_clock:
        prior_children.append(_gamma_prior("trait.clock.rate"))
    if prior_children:
        _append_to_root_by_id(root, "prior", "prior", prior_children)

    operator_children: List[Dict[str, Any]] = []
    if estimate_rates:
        operator_children.append(
            el(
                "scaleOperator",
                attrib={
                    "scaleFactor": "0.75",
                    "weight": "10",
                    "scaleAllIndependently": "true",
                    "autoOptimize": "true",
                },
                children=[el("parameter", attrib={"idref": "trait.rates"})],
            )
        )
    if estimate_clock:
        operator_children.append(
            el(
                "scaleOperator",
                attrib={"scaleFactor": "0.75", "weight": "3"},
                children=[el("parameter", attrib={"idref": "trait.clock.rate"})],
            )
        )
    if operator_children:
        _append_to_root_by_id(root, "operators", "operators", operator_children)

    screen = _declaration(root, "log", "screenLog")
    if screen is not None:
        for pid in ("trait.clock.rate", "trait.rates"):
            if (pid == "trait.rates" and not estimate_rates) or (
                pid == "trait.clock.rate" and not estimate_clock
            ):
                continue
            screen.setdefault("children", []).append(
                el(
                    "column",
                    attrib={"label": pid, "sf": "6", "width": "12"},
                    children=[_log_parameter(pid)],
                )
            )
    trace = _declaration(root, "log", "fileLog")
    if trace is not None:
        trace.setdefault("children", []).append(
            el("ancestralTreeLikelihood", attrib={"idref": "trait.treeLikelihood"})
        )
        for pid in ("trait.clock.rate", "trait.rates"):
            if (pid == "trait.rates" and not estimate_rates) or (
                pid == "trait.clock.rate" and not estimate_clock
            ):
                continue
            trace["children"].append(_log_parameter(pid))
    return root
