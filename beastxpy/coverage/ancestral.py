"""Ancestral-state reconstruction (C03) for BEAST 1.10.4 / 10.5.0.

The plugin adds the official ``<ancestralTreeLikelihood>`` object next to the
standard ``<treeLikelihood>`` and registers it in ``<logTree>`` so that every
node of the sampled tree carries its reconstructed state vector in the tree
log (exactly the construction used by BEAUti's "Ancestral States" panel and
by ``testAncestralReconstruction.xml``).

BEAST's ancestral likelihood is a BEAGLE-only implementation: the ancestral
object is used as a *trait provider* (it is not added to the compound
likelihood, otherwise the same site data would contribute twice).  The
standard tree likelihood remains the data likelihood.  Reconstructing the
states at logging time requires a working BEAGLE library; the ``java-core``
mode can therefore instantiate the document and evaluate the initial
posterior, but a full chain on a machine without native BEAGLE stops in
``GeneralBeagleImpl.getSiteLogLikelihoods`` (BEAST limitation, not an XML
error).

YAML::

    analysis:
      type: ancestral
      states: all          # all | internal | marginal (map is rejected, see below)
      state_tag_name: states

``states: all`` reconstructs the marginal posterior state vector at every
node (tips are written from the observed data).  ``internal`` is accepted as
the node-level spelling of the same BEAST construction and ``marginal`` is an
explicit alias.  ``map`` cannot be generated: BEAST's
``AncestralStateTreeLikelihoodParser`` reads ``useMAP`` but omits the matching
``BooleanRule`` from its syntax rules, so the strict parser rejects the
attribute as unhandled.  ``state_tag_name`` names the tree trait written into
the tree log; it defaults to ``states``, the official BEAST trait key.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..model import ModelAssemblyError, el
from . import find_all

ANALYSIS_TYPES = ("ancestral", "ancestral_states", "ancestral_reconstruction")

_STATES = ("all", "internal", "marginal", "map")
_KNOWN_OPTIONS = frozenset({"type", "states", "state_tag_name", "beast_version"})


def _state_tag_name(raw: Dict[str, Any], errors: List[str]) -> str:
    name = raw.get("state_tag_name", "states")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"analysis.state_tag_name: must be a non-empty string (got {name!r})")
        return "states"
    cleaned = name.strip()
    if not all(ch.isalnum() or ch in "_-." for ch in cleaned) or cleaned[0].isdigit():
        errors.append(
            "analysis.state_tag_name: must be a valid BEAST tree-trait name "
            "(letters, digits, '_', '-', '.'; must not start with a digit) "
            f"(got {name!r})"
        )
    return cleaned


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    unknown = sorted(str(key) for key in raw if key not in _KNOWN_OPTIONS)
    if unknown:
        errors.append(
            "analysis: unknown option(s) for 'ancestral': "
            + ", ".join(unknown)
            + "; supported: "
            + ", ".join(sorted(_KNOWN_OPTIONS))
        )
    states = raw.get("states", "all")
    if not isinstance(states, str) or states.strip().lower() not in _STATES:
        errors.append(
            f"analysis.states: unknown reconstruction mode {states!r}; "
            "supported: all, internal, marginal, map"
        )
    elif states.strip().lower() == "map":
        errors.append(
            "analysis.states: 'map' cannot be generated for BEAST 1.10.4 or "
            "BEAST X 10.5.0: AncestralStateTreeLikelihoodParser reads useMAP "
            "but its XMLSyntaxRule list omits the BooleanRule, so the official "
            "parser rejects useMAP='true' as an unhandled attribute. "
            "Use states: marginal."
        )
    _state_tag_name(raw, errors)


def _top_level(root: Dict[str, Any], tag: str) -> List[Dict[str, Any]]:
    return [
        child
        for child in root.get("children", [])
        if isinstance(child, dict) and child.get("tag") == tag
    ]


def _node_by_id(root: Dict[str, Any], tag: str, node_id: str) -> Dict[str, Any]:
    for node in find_all(root, tag):
        if (node.get("attrib") or {}).get("id") == node_id:
            return node
    raise ModelAssemblyError(f"cannot locate <{tag} id={node_id!r}>")


def _child_ref(node: Dict[str, Any], tag: str) -> str:
    for child in node.get("children", []):
        if child.get("tag") == tag:
            ref = (child.get("attrib") or {}).get("idref")
            if ref:
                return str(ref)
    raise ModelAssemblyError(f"<{node.get('tag')}> has no <{tag} idref>")


def apply(
    root: Dict[str, Any],
    analysis: Dict[str, Any],
    config: Any,
    assembler: Any,
) -> Dict[str, Any]:
    if len(config.partitions) != 1:
        raise ModelAssemblyError(
            "analysis.type 'ancestral' requires exactly one partition; "
            f"got {len(config.partitions)}"
        )

    likelihood = _top_level(root, "treeLikelihood")
    if not likelihood:
        raise ModelAssemblyError("ancestral: core <treeLikelihood> is missing")
    core_likelihood = likelihood[0]
    patterns_ref = _child_ref(core_likelihood, "patterns")
    tree_ref = _child_ref(core_likelihood, "treeModel")
    site_ref = _child_ref(core_likelihood, "siteModel")
    clock_ref = None
    for child in core_likelihood.get("children", []):
        tag = child.get("tag") or ""
        if tag.endswith("BranchRates") and (child.get("attrib") or {}).get("idref"):
            clock_ref = (child["tag"], child["attrib"]["idref"])
            break

    # Ancestral reconstruction is incompatible with compressed site
    # patterns (BEAST throws an XMLParseException).  The standard likelihood
    # accepts uncompressed patterns too, so one matrix serves both objects.
    patterns = _node_by_id(root, "patterns", patterns_ref)
    patterns.setdefault("attrib", {})["unique"] = "false"

    # The ancestral likelihood needs the substitution model as a direct
    # child (the siteModel reference alone is not enough).
    site_model = _node_by_id(root, "siteModel", site_ref)
    substitution = None
    for child in site_model.get("children", []):
        if child.get("tag") == "substitutionModel":
            for model in child.get("children", []):
                if model.get("tag") and (model.get("attrib") or {}).get("idref"):
                    substitution = (model["tag"], model["attrib"]["idref"])
                    break
    if substitution is None:
        raise ModelAssemblyError(
            "ancestral: the core <siteModel> has no substitution-model reference"
        )

    mode = str(analysis.get("states", "all")).strip().lower()
    if mode not in _STATES:
        raise ModelAssemblyError(
            f"ancestral: unsupported states mode {mode!r}; "
            "supported: all, internal, marginal, map"
        )
    if mode == "map":
        raise ModelAssemblyError(
            "ancestral: states='map' is not expressible in the official BEAST "
            "parser (useMAP is read but absent from its syntax rules); use "
            "states: marginal"
        )
    tag_name = _state_tag_name(analysis, [])
    attrib: Dict[str, Any] = {
        "id": "ancestralLikelihood",
        "stateTagName": tag_name,
    }

    children: List[Dict[str, Any]] = [
        el("patterns", attrib={"idref": patterns_ref}),
        el("treeModel", attrib={"idref": tree_ref}),
        el("siteModel", attrib={"idref": site_ref}),
        el(substitution[0], attrib={"idref": substitution[1]}),
    ]
    if clock_ref is not None:
        children.append(el(clock_ref[0], attrib={"idref": clock_ref[1]}))
    ancestral = el("ancestralTreeLikelihood", attrib=attrib, children=children)

    root_children = root.setdefault("children", [])
    root_children.insert(root_children.index(core_likelihood) + 1, ancestral)

    # The tree log lives inside <mcmc>, not at the top level.  BEAUti names
    # the reconstructed trait through a <trait> wrapper; the ancestral
    # likelihood itself is the TreeTraitProvider that supplies "states".
    log_trees = find_all(root, "logTree")
    if not log_trees:
        raise ModelAssemblyError("ancestral: core <logTree> is missing")
    log_trees[0].setdefault("children", []).append(
        el(
            "trait",
            attrib={"name": tag_name, "tag": tag_name},
            children=[el("ancestralTreeLikelihood", attrib={"idref": "ancestralLikelihood"})],
        )
    )
    return root
