"""Empirical tree distribution analysis (C06) for BEAST 1.10.4 / 10.5.0.

The standard analysis is converted into BEAUti's "empirical tree
distribution" construction: the analysis tree becomes the official
``<empiricalTreeDistributionModel>`` (id ``treeModel``), which reads a NEXUS
*tree set* from disk and exposes each imported tree as a fixed ``TreeModel``.
The topology/height operators of the core analysis are removed (the tree is
no longer sampled) and the official ``<empiricalTreeDistributionOperator>``
is added to move between the trees of the set.  The data likelihood, site
model, clock model and remaining priors are reused unchanged, so the
document stays a single coherent analysis.  The original ``<treeModel>``
declaration is kept as an inert ``coreTreeModel`` object because Gate 1
requires a ``<treeModel>`` definition whenever a likelihood is present; no
likelihood, prior, operator or logger references it any more.

The empirical model also exposes a ``Current Tree`` statistic; the generated
document logs it in the screen and trace logs.

YAML::

    analysis:
      type: empirical_tree
      tree_file: trees.nexus   # NEXUS TREES block (relative to the data)
      starting_tree: 0         # index of the initial tree (default 0)
      iterate: false           # walk the set sequentially instead of randomly
      weight: 1.0              # empiricalTreeDistributionOperator weight
      metropolis_hastings: true

The NEXUS file must contain a ``TREES`` block whose trees cover the taxa of
the core ``<taxa>`` block.  Relative ``tree_file`` paths are resolved against
the directory of the first alignment so the generated XML works regardless
of where it is written.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import ModelAssemblyError, el

ANALYSIS_TYPES = ("empirical_tree", "empirical_trees", "tree_set")

_KNOWN_OPTIONS = frozenset(
    {
        "type",
        "beast_version",
        "tree_file",
        "starting_tree",
        "iterate",
        "weight",
        "metropolis_hastings",
    }
)

_TREE_MODEL = "treeModel"
_TREE_OPERATOR_TAGS = frozenset({"subtreeSlide", "narrowExchange", "wideExchange", "wilsonBalding"})

_NEXUS_HEADER = re.compile(r"#\s*nexus", re.IGNORECASE)
_TREES_BLOCK = re.compile(r"begin\s+trees\s*;", re.IGNORECASE)
_TREE_STATEMENT = re.compile(
    r"^\s*(?:tree|utree)\s+\*?\s*[^\s=]+\s*=", re.IGNORECASE | re.MULTILINE
)


# ---------------------------------------------------------------------------
# Validation (Gate 0)
# ---------------------------------------------------------------------------
def _bool(raw: Dict[str, Any], key: str, default: bool, errors: List[str]) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        errors.append(f"analysis.{key}: must be true or false (got {value!r})")
        return default
    return value


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    unknown = sorted(str(key) for key in raw if key not in _KNOWN_OPTIONS)
    if unknown:
        errors.append(
            "analysis: unknown option(s) for 'empirical_tree': "
            + ", ".join(unknown)
            + "; supported: "
            + ", ".join(sorted(_KNOWN_OPTIONS))
        )

    tree_file = raw.get("tree_file")
    if not isinstance(tree_file, str) or not tree_file.strip():
        errors.append("analysis.tree_file: required NEXUS tree-set path " f"(got {tree_file!r})")

    starting_tree = raw.get("starting_tree", 0)
    if isinstance(starting_tree, bool) or not isinstance(starting_tree, int):
        errors.append(f"analysis.starting_tree: must be an integer index (got {starting_tree!r})")
    elif starting_tree < 0:
        errors.append(f"analysis.starting_tree: must be >= 0 (got {starting_tree})")

    weight = raw.get("weight", 1.0)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        errors.append(f"analysis.weight: must be numeric (got {weight!r})")
    elif float(weight) <= 0.0:
        errors.append(f"analysis.weight: must be > 0 (got {weight})")

    _bool(raw, "iterate", False, errors)
    _bool(raw, "metropolis_hastings", True, errors)


# ---------------------------------------------------------------------------
# Structure-tree helpers
# ---------------------------------------------------------------------------
def _node_by_id(root: Dict[str, Any], tag: str, node_id: str) -> Optional[Dict[str, Any]]:
    stack: List[Dict[str, Any]] = [root]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("tag") == tag and (node.get("attrib") or {}).get("id") == node_id:
            return node
        stack.extend(node.get("children") or [])
    return None


def _descendants(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        found.append(current)
        stack.extend(current.get("children") or [])
    return found


def _references_tree_parameter(node: Dict[str, Any]) -> bool:
    for child in _descendants(node):
        if child.get("tag") != "parameter":
            continue
        ref = (child.get("attrib") or {}).get("idref")
        if isinstance(ref, str) and ref.startswith(f"{_TREE_MODEL}."):
            return True
    return False


def _touches_tree_model(node: Dict[str, Any]) -> bool:
    tag = node.get("tag")
    if tag in _TREE_OPERATOR_TAGS:
        return True
    for child in _descendants(node):
        if child.get("tag") == _TREE_MODEL:
            return True
        if child.get("tag") == "parameter":
            ref = (child.get("attrib") or {}).get("idref")
            if isinstance(ref, str) and ref.startswith(f"{_TREE_MODEL}."):
                return True
    return False


def _resolve_tree_set(raw_path: str, config: Any) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        bases: List[Path] = []
        for alignment in getattr(config, "all_alignments", []) or []:
            source = getattr(alignment, "source_file", None)
            if not source:
                continue
            source_path = Path(source)
            if not source_path.is_absolute():
                source_path = Path.cwd() / source_path
            bases.append(source_path.resolve().parent)
        base = bases[0] if bases else Path.cwd()
        candidate = base / candidate
    return candidate.resolve()


def _check_tree_set(path: Path) -> None:
    if not path.is_file():
        raise ModelAssemblyError(f"empirical_tree: tree set not found: {path}")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ModelAssemblyError(f"empirical_tree: cannot read tree set {path}: {exc}") from exc
    if not _NEXUS_HEADER.search(text):
        raise ModelAssemblyError(f"empirical_tree: {path} is not a NEXUS file (missing '#NEXUS')")
    if not _TREES_BLOCK.search(text):
        raise ModelAssemblyError(f"empirical_tree: {path} has no NEXUS 'BEGIN TREES' block")
    if not _TREE_STATEMENT.search(text):
        raise ModelAssemblyError(f"empirical_tree: {path} contains no TREE statements")


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def apply(
    root: Dict[str, Any],
    analysis: Dict[str, Any],
    config: Any,
    assembler: Any,
) -> Dict[str, Any]:
    raw_path = analysis.get("tree_file")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ModelAssemblyError("empirical_tree: analysis.tree_file is required")
    tree_set = _resolve_tree_set(raw_path.strip(), config)
    _check_tree_set(tree_set)

    tree_model = _node_by_id(root, "treeModel", _TREE_MODEL)
    if tree_model is None:
        raise ModelAssemblyError("empirical_tree: core <treeModel> is missing")

    taxa = _node_by_id(root, "taxa", "taxa")
    if taxa is None:
        raise ModelAssemblyError("empirical_tree: core <taxa id='taxa'> is missing")

    starting_tree = analysis.get("starting_tree", 0)
    if isinstance(starting_tree, bool) or not isinstance(starting_tree, int) or starting_tree < 0:
        raise ModelAssemblyError(f"empirical_tree: invalid starting_tree {starting_tree!r}")
    iterate = analysis.get("iterate", False)
    if not isinstance(iterate, bool):
        raise ModelAssemblyError(f"empirical_tree: invalid iterate {iterate!r}")
    weight = analysis.get("weight", 1.0)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
        raise ModelAssemblyError(f"empirical_tree: invalid weight {weight!r}")
    metropolis_hastings = analysis.get("metropolis_hastings", True)
    if not isinstance(metropolis_hastings, bool):
        raise ModelAssemblyError(
            f"empirical_tree: invalid metropolis_hastings {metropolis_hastings!r}"
        )

    children = root.setdefault("children", [])
    position = children.index(tree_model)

    # Keep the core tree model as an inert declaration (no operator, prior or
    # likelihood references it any more).  Gate 1 requires a <treeModel>
    # definition in every document that carries a likelihood; renaming rather
    # than deleting it keeps that contract without affecting the analysis.
    tree_model.setdefault("attrib", {})["id"] = "coreTreeModel"

    empirical_attrib: Dict[str, Any] = {
        "id": _TREE_MODEL,
        "fileName": tree_set.as_posix(),
        "startingTree": str(starting_tree),
    }
    if iterate:
        empirical_attrib["iterate"] = "true"
    empirical = el(
        "empiricalTreeDistributionModel",
        attrib=empirical_attrib,
        children=[el("taxa", attrib={"idref": "taxa"})],
    )
    children.insert(position + 1, empirical)

    # The empirical model registers the "Current Tree" statistic itself; the
    # explicit <statistic> makes it loggable and documents the source.
    children.insert(
        position + 2,
        el(
            "statistic",
            attrib={"id": "treeModel.currentTree", "name": "Current Tree"},
            children=[el("empiricalTreeDistributionModel", attrib={"idref": _TREE_MODEL})],
        ),
    )

    # Topology/height operators no longer apply to a fixed tree.
    operators = _node_by_id(root, "operators", "operators")
    if operators is not None:
        kept = [
            operator
            for operator in operators.get("children", [])
            if isinstance(operator, dict) and not _touches_tree_model(operator)
        ]
        operators["children"] = kept
        kept.append(
            el(
                "empiricalTreeDistributionOperator",
                attrib={
                    "weight": f"{float(weight):g}",
                    "metropolisHastings": "true" if metropolis_hastings else "false",
                },
                children=[el("empiricalTreeDistributionModel", attrib={"idref": _TREE_MODEL})],
            )
        )

    # Logger columns pointing at the removed <treeModel> parameters must go;
    # the empirical tree index takes their place.
    mcmc = _node_by_id(root, "mcmc", "mcmc")
    if mcmc is not None:
        for logger in mcmc.get("children", []) or []:
            if not isinstance(logger, dict):
                continue
            tag = logger.get("tag")
            if tag == "log":
                logger["children"] = [
                    entry
                    for entry in logger.get("children", []) or []
                    if isinstance(entry, dict) and not _references_tree_parameter(entry)
                ]
                if (logger.get("attrib") or {}).get("id") == "screenLog":
                    logger["children"].append(
                        el(
                            "column",
                            attrib={"label": "Tree#", "dp": "0", "width": "12"},
                            children=[el("statistic", attrib={"idref": "treeModel.currentTree"})],
                        )
                    )
                elif (logger.get("attrib") or {}).get("id") == "fileLog":
                    logger["children"].append(
                        el("statistic", attrib={"idref": "treeModel.currentTree"})
                    )
    return root
