"""Dollo (mutation/death) analysis for binary presence/absence characters.

The fixture starts from a nucleotide alignment and a chosen derived state; the
plugin recodes every variable, fully resolved site into a binary character
(1 = derived state present, 0 = ancestral).  The resulting matrix is analysed
with BEAST's mutation/death (Dollo) model:

* ``<extendedDataType>`` with extant code ``1`` and death code ``0``;
* ``<mutationDeathModel>`` inside a regular ``<siteModel>``, with an estimated
  death rate (the pure-survival configuration used by Dollo analyses);
* ``<ascertainedPatterns>`` so the likelihood is conditioned on variable
  characters, wired into the pure-Java ``<treeLikelihood>`` parser;
* the assembled Yule/coalescent tree prior is retained.

BEAST 1.10.4 still ships the historical ``<alsSiteModel>`` and
``<exponentialBranchLengthsPrior>`` parsers, but only in
``old_parsers.properties``: the strict release parser profile does not load
them.  The released ``mutationDeathModel`` parser is the same irreversible
gain/loss process and is accepted by the modern ``siteModel`` and
``treeLikelihood`` parsers, so it is used here.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..model import el, param_node
from . import find_all

ANALYSIS_TYPES = ("dollo", "dollo_model")

_STATES = ("A", "C", "G", "T")


def _declaration(root: Dict[str, Any], tag: str, id_: str) -> Any:
    for node in find_all(root, tag):
        attrib = node.get("attrib") or {}
        if attrib.get("id") == id_:
            return node
    return None


def _top_level(node: Dict[str, Any]) -> bool:
    attrib = node.get("attrib") or {}
    return "idref" not in attrib


def _remove_declaration(root: Dict[str, Any], tag: str, id_: str) -> None:
    node = _declaration(root, tag, id_)
    if node is not None and node in root.get("children", []):
        root["children"].remove(node)


def _remove_parameter_everywhere(root: Dict[str, Any], pid: str) -> None:
    """Drop operator/prior/log references to a parameter that is no longer used."""
    operators = _declaration(root, "operators", "operators")
    if operators is not None:
        kept = []
        for operator in operators.get("children", []):
            refs = [
                child
                for child in operator.get("children", [])
                if child.get("tag") == "parameter"
                and (child.get("attrib") or {}).get("idref") == pid
            ]
            if refs:
                if operator.get("tag", "").endswith("Operator"):
                    continue
                operator["children"] = [
                    child for child in operator.get("children", []) if child not in refs
                ]
            kept.append(operator)
        operators["children"] = kept

    prior = _declaration(root, "prior", "prior")
    if prior is not None:
        kept = []
        for entry in prior.get("children", []):
            direct = [
                child
                for child in entry.get("children", [])
                if child.get("tag") == "parameter"
                and (child.get("attrib") or {}).get("idref") == pid
            ]
            if direct:
                continue
            kept.append(entry)
        prior["children"] = kept

    for log_id in ("screenLog", "fileLog"):
        log = _declaration(root, "log", log_id)
        if log is None:
            continue
        kept = []
        for child in log.get("children", []):
            refs = [
                sub
                for sub in child.get("children", [])
                if sub.get("tag") == "parameter" and (sub.get("attrib") or {}).get("idref") == pid
            ]
            if child.get("tag") == "parameter" and (child.get("attrib") or {}).get("idref") == pid:
                continue
            if refs:
                if child.get("tag") == "column":
                    continue
                child["children"] = [sub for sub in child.get("children", []) if sub not in refs]
            kept.append(child)
        log["children"] = kept


def _remove_substitution_stack(root: Dict[str, Any]) -> None:
    """Remove the core nucleotide site/likelihood objects and their parameter."""
    removed_ids = set()
    for node in list(root.get("children", [])):
        attrib = node.get("attrib") or {}
        if "idref" in attrib:
            continue
        tag = node.get("tag")
        if tag in ("hkyModel", "gtrModel", "tn93Model", "jc69Model", "siteModel"):
            removed_ids.add(str(attrib.get("id")))
            root["children"].remove(node)
        elif tag == "treeLikelihood":
            root["children"].remove(node)
    # Remove parameters that belonged only to that stack.
    for pid in ("kappa", "kappa1", "kappa2"):
        _remove_parameter_everywhere(root, pid)


def _sites(alignment: Any, from_pos: int, to_pos: int, derived: str, limit: int) -> List[int]:
    selected: List[int] = []
    sequences = alignment.sequences
    for position in range(from_pos - 1, to_pos):
        column = [seq.sequence[position].upper() for seq in sequences]
        if any(base not in _STATES for base in column):
            continue
        if derived not in column:
            continue
        if all(base == derived for base in column):
            continue
        selected.append(position)
        if len(selected) >= limit:
            break
    return selected


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    derived = raw.get("derived_state", "G")
    if not isinstance(derived, str) or derived.upper() not in _STATES:
        errors.append(
            f"analysis.derived_state: unknown state {derived!r}; must be one of "
            + ", ".join(_STATES)
        )
    death = raw.get("death_rate", 1.0)
    if isinstance(death, bool) or not isinstance(death, (int, float)) or death <= 0:
        errors.append(f"analysis.death_rate: must be a positive number (got {death!r})")
    limit = raw.get("max_sites", 200)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 2:
        errors.append(f"analysis.max_sites: must be an integer >= 2 (got {limit!r})")
    observation = raw.get("observation", "anyTip")
    if observation not in ("anyTip", "singleTip"):
        errors.append(
            f"analysis.observation: unknown {observation!r}; supported: anyTip, singleTip"
        )
    if observation == "singleTip":
        taxon = raw.get("single_tip")
        if not isinstance(taxon, str) or not taxon:
            errors.append(
                "analysis.single_tip: a taxon name is required when " "observation is 'singleTip'"
            )
    estimate = raw.get("estimate_death_rate", True)
    if not isinstance(estimate, bool):
        errors.append(f"analysis.estimate_death_rate: must be true or false (got {estimate!r})")
    if raw.get("branch_length_prior", "exponential") != "exponential":
        errors.append(
            "analysis.branch_length_prior: only 'exponential' is implemented "
            "(BEAST's exponentialBranchLengthsPrior)"
        )


def apply(
    root: Dict[str, Any], analysis: Dict[str, Any], config: Any, assembler: Any
) -> Dict[str, Any]:
    partitions = getattr(config, "partitions", [])
    if len(partitions) != 1:
        raise ValueError(
            "dollo expects exactly one partition (the binary character matrix "
            f"is derived from one alignment window); got {len(partitions)}"
        )
    partition = partitions[0]
    alignment = getattr(assembler, "_alignment_sources", {}).get(
        partition.alignment.id, partition.alignment
    )
    from_pos = partition.alignment.from_pos or 1
    to_pos = partition.alignment.to_pos or alignment.n_sites

    derived = str(analysis.get("derived_state", "G")).upper()
    limit = int(analysis.get("max_sites", 200))
    positions = _sites(alignment, int(from_pos), int(to_pos), derived, limit)
    if len(positions) < 2:
        raise ValueError(
            f"dollo: only {len(positions)} variable site(s) contain derived state "
            f"{derived!r} in {alignment.id!r}; at least 2 binary characters are "
            "required"
        )

    sequences = []
    for seq in alignment.sequences:
        bits = "".join(
            "1" if seq.sequence[position].upper() == derived else "0" for position in positions
        )
        sequences.append(
            el(
                "sequence",
                children=[el("taxon", attrib={"idref": seq.taxon})],
                text=bits,
            )
        )
    taxa_names = [seq.taxon for seq in alignment.sequences]
    observation = str(analysis.get("observation", "anyTip"))
    if observation == "singleTip" and analysis.get("single_tip") not in taxa_names:
        raise ValueError(
            f"analysis.single_tip: {analysis.get('single_tip')!r} is not a taxon "
            f"of alignment {alignment.id!r}"
        )

    old_alignment = _declaration(root, "alignment", alignment.id)
    if old_alignment is None:
        raise ValueError(f"dollo: alignment {alignment.id!r} was not emitted")
    alignment_index = root["children"].index(old_alignment)
    extended = el(
        "extendedDataType",
        attrib={"id": "dolloDataType"},
        children=[
            el("extantState", attrib={"code": "1"}),
            el("deathState", attrib={"code": "0"}),
            el("ambiguity", attrib={"code": "U"}),
        ],
    )
    new_alignment = el(
        "alignment",
        attrib={"id": alignment.id, "dataType": "binary"},
        children=sequences,
    )
    root["children"][alignment_index] = extended
    root["children"].insert(alignment_index + 1, new_alignment)

    # Replace patterns with ascertained patterns (Dollo data are ascertained).
    patterns = _declaration(root, "patterns", "patterns")
    if patterns is None:
        patterns = find_all(root, "patterns")[0]
    pattern_index = root["children"].index(patterns) if patterns in root["children"] else None
    plain = el(
        "patterns",
        attrib={"id": "dolloPatterns.all", "from": "1"},
        children=[el("alignment", attrib={"idref": alignment.id})],
    )
    ascertained = el(
        "ascertainedPatterns",
        attrib={"id": "dolloPatterns", "from": "1"},
        children=[el("alignment", attrib={"idref": alignment.id})],
    )
    if pattern_index is not None:
        root["children"][pattern_index] = ascertained
        root["children"].insert(pattern_index, plain)

    # Mutation/death (Dollo) substitution model in a regular site model.  The
    # historical ``<alsSiteModel>``/``<exponentialBranchLengthsPrior>`` pair
    # lives only in BEAST's old_parsers.properties, which the strict java-core
    # parser set does not load; the released ``mutationDeathModel`` parser
    # exposes the same irreversible gain/loss process and is consumed by the
    # released ``siteModel`` + pure-Java ``treeLikelihood`` parsers.
    death_rate = float(analysis.get("death_rate", 1.0))
    estimate = bool(analysis.get("estimate_death_rate", True))
    mutation_death = el(
        "mutationDeathModel",
        attrib={"id": "dolloModel"},
        children=[
            param_node(
                "dollo.death",
                death_rate,
                lower=0.0,
                estimate=estimate,
            ),
            el("extendedDataType", attrib={"idref": "dolloDataType"}),
        ],
    )
    site_model = el(
        "siteModel",
        attrib={"id": "dolloSiteModel"},
        children=[
            el("substitutionModel", children=[mutation_death]),
            el("mutationRate", children=[param_node("dollo.mu", 1.0, estimate=False)]),
        ],
    )
    likelihood_node = el(
        "treeLikelihood",
        attrib={"id": "dolloLikelihood", "useAmbiguities": "true"},
        children=[
            el("patterns", attrib={"idref": "dolloPatterns"}),
            el("treeModel", attrib={"idref": "treeModel"}),
            el("siteModel", attrib={"idref": "dolloSiteModel"}),
            el("strictClockBranchRates", attrib={"idref": "branchRates"}),
        ],
    )

    _remove_substitution_stack(root)

    mcmc = _declaration(root, "mcmc", "mcmc")
    operators = _declaration(root, "operators", "operators")
    if mcmc is None or operators is None:
        raise ValueError("dollo: core <operators>/<mcmc> objects are missing")
    # The death-rate parameter lives inside the site model; declare the site
    # model before <operators> so the new operator can reference it.
    site_position = root["children"].index(operators)
    root["children"].insert(site_position, site_model)
    mcmc_position = root["children"].index(mcmc)
    root["children"].insert(mcmc_position, likelihood_node)

    likelihood = _declaration(root, "likelihood", "likelihood")
    if likelihood is None:
        raise ValueError("dollo: core <likelihood id='likelihood'> is missing")
    likelihood["children"] = [el("treeLikelihood", attrib={"idref": "dolloLikelihood"})]

    prior = _declaration(root, "prior", "prior")
    if prior is None:
        raise ValueError("dollo: core <prior id='prior'> is missing")
    if estimate:
        operators = _declaration(root, "operators", "operators")
        if operators is not None:
            operators.setdefault("children", []).append(
                el(
                    "scaleOperator",
                    attrib={"scaleFactor": "0.75", "weight": "1"},
                    children=[el("parameter", attrib={"idref": "dollo.death"})],
                )
            )
        prior["children"].append(
            el(
                "gammaPrior",
                attrib={"shape": "0.5", "scale": "2.0", "offset": "0.0"},
                children=[el("parameter", attrib={"idref": "dollo.death"})],
            )
        )
        trace = _declaration(root, "log", "fileLog")
        if trace is not None:
            trace.setdefault("children", []).append(
                el("parameter", attrib={"idref": "dollo.death"})
            )
        screen = _declaration(root, "log", "screenLog")
        if screen is not None:
            screen.setdefault("children", []).append(
                el(
                    "column",
                    attrib={"label": "dollo.death", "sf": "6", "width": "12"},
                    children=[el("parameter", attrib={"idref": "dollo.death"})],
                )
            )
    return root
