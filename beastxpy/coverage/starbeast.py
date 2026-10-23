"""Minimal *BEAST (StarBEAST1) multi-species coalescent analysis (C08).

The plugin turns the standard single-gene document into a star-beast document:

* ``<species>`` binds every gene-tree taxon to a species (``<sp>`` entries)
  and lists the gene trees (``<geneTrees>``);
* ``<speciesTree>`` is a :class:`SpeciesTreeModel` parameterised by
  ``<sppSplitPopulations>``;
* ``<speciesCoalescent>`` embeds the gene tree in the species tree and is
  added to the joint likelihood;
* the core ``<speciationLikelihood id="speciation">`` (Yule or birth-death)
  is repointed from the gene tree to the species tree, so it becomes the
  species-tree prior;
* ``<nodeReHeight>`` moves the species tree and a random-walk operator moves
  the split-population parameter.

Supported subset
----------------
* exactly one alignment/partition (one gene tree);
* no calibrations and no tip dates (the deterministic starting trees place
  every tip at height 0);
* ``tree_prior`` must be ``yule`` or ``birth_death`` so that a core
  ``<speciationLikelihood>`` exists;
* an arbitrary ``species: {taxon: species}`` map, including several taxa per
  species.

Because the core coalescent starting tree has a random topology, the plugin
replaces it with a deterministic Newick caterpillar and gives the species tree
a matching caterpillar start tree.  This guarantees that the initial species
coalescent likelihood is finite (a random gene-tree topology would otherwise
be incompatible with the ladder species tree and give ``-Infinity``).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..model import ModelAssemblyError, el
from . import find_all

ANALYSIS_TYPES = ("starbeast", "*beast", "star_beast")

_SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_SAFE_NEWICK = re.compile(r"^[A-Za-z0-9_.-]+$")


# ---------------------------------------------------------------------------
# Gate 0
# ---------------------------------------------------------------------------


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    version = raw.get("beast_version")
    if version is not None and version not in ("1.10.4", "10.5.0", "both"):
        errors.append(
            "analysis.beast_version: must be '1.10.4' or '10.5.0' "
            "(starbeast is not a single-version analysis)"
        )

    species = raw.get("species")
    if not isinstance(species, dict) or not species:
        errors.append("analysis.species: a non-empty mapping {taxon: species} is required")
    else:
        for taxon, name in species.items():
            if not isinstance(taxon, str) or not taxon:
                errors.append("analysis.species: taxon keys must be non-empty strings")
            if not isinstance(name, str) or not name:
                errors.append(
                    f"analysis.species[{taxon!r}]: species name must be a " "non-empty string"
                )
        distinct = {name for name in species.values() if isinstance(name, str) and name}
        if species and len(distinct) < 2:
            errors.append("analysis.species: at least two distinct species are required")

    if "population_size" in raw:
        population_size = _coerce_number(raw["population_size"])
        if population_size is None:
            errors.append("analysis.population_size: must be a number > 0")
        elif population_size <= 0:
            errors.append("analysis.population_size: must be > 0")

    if "node_reheight_weight" in raw:
        weight = _coerce_number(raw["node_reheight_weight"])
        if weight is None or weight <= 0:
            errors.append("analysis.node_reheight_weight: must be a number > 0")


# ---------------------------------------------------------------------------
# Starting trees
# ---------------------------------------------------------------------------


class _Node:
    __slots__ = ("name", "children", "height")

    def __init__(
        self,
        name: Optional[str] = None,
        children: Optional[List["_Node"]] = None,
        height: float = 0.0,
    ) -> None:
        self.name = name
        self.children = children or []
        self.height = height


def _newick_label(name: str) -> str:
    if _SAFE_NEWICK.match(name):
        return name
    return "'" + name.replace("'", "''") + "'"


def _newick(node: _Node) -> str:
    if not node.children:
        return _newick_label(node.name or "")
    parts = [f"{_newick(child)}:{(node.height - child.height):.6f}" for child in node.children]
    return "(" + ",".join(parts) + ")"


def _caterpillar(nodes: List[_Node], heights: List[float]) -> _Node:
    current = nodes[0]
    for index, node in enumerate(nodes[1:]):
        current = _Node(children=[current, node], height=heights[index])
    return current


def _species_id(index: int, used: set) -> str:
    candidate = f"sp.{index + 1}"
    while candidate in used:
        candidate = "_" + candidate
    used.add(candidate)
    return candidate


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _require_version(config: Any) -> None:
    version = getattr(getattr(config, "control", None), "beast_version", None)
    if version not in ("1.10.4", "10.5.0"):
        raise ModelAssemblyError(
            "analysis.type 'starbeast' requires control.beast_version to be "
            f"exactly '1.10.4' or '10.5.0' (got {version!r}); 'both' emits one "
            "document per run"
        )


def _alignment_taxa(config: Any) -> List[str]:
    names: List[str] = []
    for partition in config.partitions:
        for sequence in partition.alignment.sequences:
            if sequence.taxon not in names:
                names.append(sequence.taxon)
    return names


def _group_species(analysis: Dict[str, Any], taxa: List[str]) -> List[Tuple[str, List[str]]]:
    mapping = analysis.get("species")
    if not isinstance(mapping, dict) or not mapping:
        raise ModelAssemblyError("analysis.species: a {taxon: species} mapping is required")

    unknown = sorted(set(mapping) - set(taxa))
    if unknown:
        raise ModelAssemblyError(
            "analysis.species: unknown taxon(s) "
            + ", ".join(unknown)
            + "; alignment taxa: "
            + ", ".join(taxa)
        )
    missing = [name for name in taxa if name not in mapping]
    if missing:
        raise ModelAssemblyError(
            "analysis.species: no species assigned to taxon(s) " + ", ".join(missing)
        )

    order: List[str] = []
    grouped: Dict[str, List[str]] = {}
    for taxon in taxa:
        species = str(mapping[taxon])
        if species not in grouped:
            grouped[species] = []
            order.append(species)
        grouped[species].append(taxon)
    return [(species, grouped[species]) for species in order]


def _starting_trees(
    groups: List[Tuple[str, List[str]]], used_ids: set
) -> Tuple[str, str, List[Tuple[str, List[str], str]]]:
    """Return (gene Newick, species Newick, [(species, taxa, sp id), ...])."""
    n_species = len(groups)
    if n_species < 2:
        raise ModelAssemblyError("analysis.species: at least two species are required")

    species_nodes: List[_Node] = []
    gene_species_nodes: List[_Node] = []
    assignments: List[Tuple[str, List[str], str]] = []

    for index, (species, members) in enumerate(groups):
        sp_id = _species_id(index, used_ids)
        assignments.append((species, members, sp_id))
        species_nodes.append(_Node(name=sp_id))

        if len(members) == 1:
            gene_species_nodes.append(_Node(name=members[0]))
        else:
            step = 0.8 / max(len(members) - 1, 1)
            leaves = [_Node(name=taxon) for taxon in members]
            heights = [step * (k + 1) for k in range(len(members) - 1)]
            gene_species_nodes.append(_caterpillar(leaves, heights))

    # Species tree: caterpillar at heights 1, 2, ..., n-1.
    species_heights = [float(k) for k in range(1, n_species)]
    species_tree = _caterpillar(species_nodes, species_heights)

    # Gene tree: same species order, species-level nodes at 2, 3, ..., n.
    # Every gene-tree clade height is strictly greater than the corresponding
    # species-tree node height, so the initial embedding is compatible; all
    # within-species coalescences are below the first species split.
    gene_heights = [float(k + 2) for k in range(n_species - 1)]
    gene_tree = _caterpillar(gene_species_nodes, gene_heights)

    return _newick(gene_tree) + ";", _newick(species_tree) + ";", assignments


def _top_level(root: Dict[str, Any], tag: str) -> List[Dict[str, Any]]:
    return [
        node
        for node in root.get("children", [])
        if isinstance(node, dict) and node.get("tag") == tag
    ]


def _insert_after(
    root: Dict[str, Any], anchor: Dict[str, Any], nodes: List[Dict[str, Any]]
) -> None:
    children = root.setdefault("children", [])
    index = children.index(anchor)
    children[index + 1 : index + 1] = nodes


def _node_by_id(root: Dict[str, Any], tag: str, node_id: str) -> Dict[str, Any]:
    for node in find_all(root, tag):
        if (node.get("attrib") or {}).get("id") == node_id:
            return node
    raise ModelAssemblyError(f"assembled XML has no <{tag} id={node_id!r}>")


def _log_parameter(root: Dict[str, Any], idref: str) -> None:
    for log in find_all(root, "log"):
        if (log.get("attrib") or {}).get("id") == "fileLog":
            log.setdefault("children", []).append(el("parameter", attrib={"idref": idref}))


def _check_config(config: Any) -> None:
    if len(config.partitions) != 1:
        raise ModelAssemblyError(
            "analysis.type 'starbeast' currently supports exactly one " "partition (one gene tree)"
        )
    if getattr(config, "calibrations", None):
        raise ModelAssemblyError(
            "analysis.type 'starbeast' does not support node calibrations in " "this minimal subset"
        )
    if getattr(config, "tip_dates", None):
        raise ModelAssemblyError(
            "analysis.type 'starbeast' does not support tip dates in this "
            "minimal subset (starting trees place all tips at height 0)"
        )
    tree_prior = getattr(config, "tree_prior", None)
    prior_type = getattr(getattr(tree_prior, "type", None), "value", None)
    if prior_type not in ("yule", "birth_death"):
        raise ModelAssemblyError(
            "analysis.type 'starbeast' requires tree_prior.type 'yule' or "
            f"'birth_death' so a <speciationLikelihood> exists (got {prior_type!r})"
        )


def apply(
    root: Dict[str, Any], analysis: Dict[str, Any], config: Any, assembler: Any
) -> Dict[str, Any]:
    _require_version(config)
    _check_config(config)

    tree_model = next(iter(_top_level(root, "treeModel")), None)
    if tree_model is None:
        raise ModelAssemblyError("assembled XML has no top-level <treeModel>")
    if not tree_model.get("children"):
        raise ModelAssemblyError("<treeModel> has no starting-tree child")

    taxa = _alignment_taxa(config)
    groups = _group_species(analysis, taxa)
    used_ids = {taxon for taxon in taxa}
    gene_newick, species_newick, assignments = _starting_trees(groups, used_ids)

    population = analysis.get("population_size", 0.01)
    if isinstance(population, dict):
        population = population.get("value", 0.01)
    population = float(population)
    weight = analysis.get("node_reheight_weight", 10.0)
    if isinstance(weight, dict):
        weight = weight.get("value", 10.0)
    weight = float(weight)

    # Deterministic gene-tree start (replaces the random coalescent start).
    gene_start = el(
        "newick",
        attrib={"id": "starbeast.geneStart", "units": "substitutions"},
        text=gene_newick,
    )
    tree_model["children"][0] = el("newick", attrib={"idref": "starbeast.geneStart"})
    _insert_after(root, _node_before(root, tree_model), [gene_start])

    # Species binding + species tree + species coalescent.
    species_children: List[Dict[str, Any]] = [
        el(
            "sp",
            attrib={"id": sp_id},
            children=[el("taxon", attrib={"idref": taxon}) for taxon in members],
        )
        for _species, members, sp_id in assignments
    ]
    species_children.append(
        el(
            "geneTrees",
            children=[el("treeModel", attrib={"idref": "treeModel"})],
        )
    )
    species_node = el("species", attrib={"id": "species"}, children=species_children)

    species_start = el(
        "newick",
        attrib={"id": "starbeast.speciesStart", "units": "substitutions"},
        text=species_newick,
    )
    species_tree = el(
        "speciesTree",
        attrib={"id": "speciesTree"},
        children=[
            el("species", attrib={"idref": "species"}),
            el("newick", attrib={"idref": "starbeast.speciesStart"}),
            el(
                "sppSplitPopulations",
                attrib={"value": f"{population:g}"},
                children=[el("parameter", attrib={"id": "speciesTree.sppSplitPopulations"})],
            ),
        ],
    )
    coalescent = el(
        "speciesCoalescent",
        attrib={"id": "speciesCoalescent"},
        children=[
            el("species", attrib={"idref": "species"}),
            el("speciesTree", attrib={"idref": "speciesTree"}),
        ],
    )
    _insert_after(root, tree_model, [species_node, species_start, species_tree, coalescent])

    # Repoint the core speciation likelihood from the gene tree to the
    # species tree (the model, e.g. <yuleModel>, stays untouched).
    speciation = next(
        (
            node
            for node in _top_level(root, "speciationLikelihood")
            if any(child.get("tag") == "model" for child in node.get("children") or [])
        ),
        None,
    )
    if speciation is None:
        raise ModelAssemblyError(
            "assembled XML has no <speciationLikelihood>; starbeast requires "
            "tree_prior yule or birth_death"
        )
    tree_ref = None
    for child in speciation.get("children") or []:
        if child.get("tag") == "speciesTree":
            tree_ref = child
            break
    if tree_ref is None:
        raise ModelAssemblyError("<speciationLikelihood> has no <speciesTree> child")
    tree_ref["children"] = [el("speciesTree", attrib={"idref": "speciesTree"})]

    # The species coalescent is part of the joint likelihood.
    likelihood = _node_by_id(root, "likelihood", "likelihood")
    likelihood.setdefault("children", []).append(
        el("speciesCoalescent", attrib={"idref": "speciesCoalescent"})
    )

    # Species-tree parameter prior.
    prior = _node_by_id(root, "prior", "prior")
    prior.setdefault("children", []).append(
        el(
            "logNormalPrior",
            attrib={
                "mean": f"{population:g}",
                "stdev": "1.0",
                "offset": "0.0",
                "meanInRealSpace": "true",
            },
            children=[el("parameter", attrib={"idref": "speciesTree.sppSplitPopulations"})],
        )
    )

    # Operators: species-tree topology/height moves and population sizes.
    operators = _node_by_id(root, "operators", "operators")
    operators.setdefault("children", []).extend(
        [
            el(
                "nodeReHeight",
                attrib={"weight": f"{weight:g}"},
                children=[
                    el("species", attrib={"idref": "species"}),
                    el("speciesTree", attrib={"idref": "speciesTree"}),
                ],
            ),
            el(
                "randomWalkOperator",
                attrib={"windowSize": "0.01", "weight": "3"},
                children=[el("parameter", attrib={"idref": "speciesTree.sppSplitPopulations"})],
            ),
        ]
    )

    _log_parameter(root, "speciesTree.sppSplitPopulations")
    return root


def _node_before(root: Dict[str, Any], anchor: Dict[str, Any]) -> Dict[str, Any]:
    """Return the child of ``root`` immediately preceding ``anchor``.

    ``_insert_after`` needs an anchor; the gene-tree Newick is inserted just
    before the tree model, so use the preceding sibling (or insert at 0).
    """
    children = root.get("children", [])
    index = children.index(anchor)
    if index == 0:  # pragma: no cover - taxonomy always precedes treeModel
        raise ModelAssemblyError("cannot insert a starting tree before <treeModel>")
    return children[index - 1]
