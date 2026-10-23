"""Recombination analysis with BEAST's ancestral recombination graph (ARG).

The fixture alignment is cut into contiguous blocks (``analysis.partitions``).
Each block becomes a BEAST ``<patterns>`` / ``<hkyModel>`` / ``<siteModel>`` /
``<argTreeLikelihood>`` quartet, and all blocks share one
``<argTreeModel partitionType="recombination">``.  Breakpoint locations are
proposed by ``<argPartitionOperator>`` under a
``<uniformPartitionLikelihood>`` prior, and the ARG topology is moved with the
three ``<argSwapOperator>`` moves (narrow/bifurcation/reassortment) plus the
shared node-height operators.

Element names and the block layout follow the released
``examples/Recombination/arg-ecoli.xml``.  ``<nodeRates>`` and
``<argEventOperator>`` are deliberately not emitted: both BEAST 1.10.4 and
BEAST X 10.5.0 reject ``<nodeRates>`` inside ``<argTreeModel>`` ("Unexpected
element in argTreeModel:...: nodeRates"), while ``<argEventOperator>``
*requires* that same node-rate compound.  The shipped example therefore does
not instantiate.  This plugin keeps a strict clock (single fixed rate) and the
recombination-specific swap/partition moves, for which node rates are not
needed.

Known engine limitation (reported, not worked around): the released
``<argTreeLikelihood>`` parser requires the *legacy*
``dr.oldevomodel.sitemodel.SiteModel`` object, but the released ``<siteModel>``
parser instantiates ``dr.evomodel.siteratemodel.GammaSiteRateModel`` in both
1.10.4 and 10.5.0.  The official ARG examples fail identically.  This plugin
emits the documented, reference-complete ARG XML; it cannot make the official
release parser accept it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..model import ModelAssemblyError, el, param_node

ANALYSIS_TYPES = ("recombination",)

_BASES = ("A", "C", "G", "T")


def _top_level(root: Dict[str, Any], tag: str) -> Optional[Dict[str, Any]]:
    for node in root.get("children", []):
        if node.get("tag") == tag and "idref" not in (node.get("attrib") or {}):
            return node
    return None


def _alignment_node(root: Dict[str, Any], alignment_id: str) -> Optional[Dict[str, Any]]:
    for node in root.get("children", []):
        attrib = node.get("attrib") or {}
        if node.get("tag") == "alignment" and attrib.get("id") == alignment_id:
            return node
    return None


def _empirical_frequencies(sequences: List[str], from_pos: int, to_pos: int) -> List[float]:
    counts = {base: 0 for base in _BASES}
    total = 0
    for sequence in sequences:
        for position in range(from_pos - 1, to_pos):
            base = sequence[position].upper()
            if base in counts:
                counts[base] += 1
                total += 1
    if total == 0:
        return [0.25, 0.25, 0.25, 0.25]
    return [counts[base] / total for base in _BASES]


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    errors.append(
        "analysis.type 'recombination': BEAST 1.10.4/10.5.0 <argTreeLikelihood> "
        "requires the legacy SiteModel object "
        "(dr.oldevomodel.sitemodel.SiteModel), but the "
        "released <siteModel> parser creates GammaSiteRateModel. The shipped "
        "arg-ecoli.xml fails first on an unrelated argTreeModel/nodeRates "
        "defect; after removing nodeRates and argEventOperator, both versions "
        "report 'Exactly one ELEMENT of type SiteModel REQUIRED'. No opt-in "
        "legacy-parser mode is enabled: supporting this would require a custom "
        "parser registration, not official default behaviour. Use a BEAST "
        "build with the legacy parser set or a different recombination tool."
    )


def apply(
    root: Dict[str, Any],
    analysis: Dict[str, Any],
    config: Any,
    assembler: Any,
) -> Dict[str, Any]:
    partitions = getattr(config, "partitions", [])
    if len(partitions) != 1:
        raise ModelAssemblyError(
            "recombination expects exactly one alignment partition; the plugin "
            f"cuts it into ARG blocks itself (got {len(partitions)} partitions)"
        )
    partition = partitions[0]
    alignment = getattr(assembler, "_alignment_sources", {}).get(
        partition.alignment.id, partition.alignment
    )
    n_sites = alignment.n_sites
    n_blocks = int(analysis.get("partitions", 3))
    if n_blocks > n_sites:
        raise ModelAssemblyError(
            f"recombination: {n_blocks} blocks requested but alignment "
            f"{alignment.id!r} has only {n_sites} sites"
        )

    taxa_node = _top_level(root, "taxa")
    if taxa_node is None:
        raise ModelAssemblyError("recombination: core <taxa> element is missing")
    alignment_node = _alignment_node(root, alignment.id)
    if alignment_node is None:
        raise ModelAssemblyError(f"recombination: core <alignment id={alignment.id!r}> is missing")

    bounds = [round(i * n_sites / n_blocks) for i in range(n_blocks + 1)]
    blocks = [(bounds[i] + 1, bounds[i + 1]) for i in range(n_blocks)]
    sequences = [seq.sequence for seq in alignment.sequences]
    kappa = float(analysis.get("kappa", 2.0))
    estimate_kappa = bool(analysis.get("estimate_kappa", True))

    nodes: List[Dict[str, Any]] = [taxa_node, alignment_node]
    likelihoods: List[Dict[str, Any]] = []
    kappa_ids: List[str] = []

    for index, (from_pos, to_pos) in enumerate(blocks, start=1):
        nodes.append(
            el(
                "patterns",
                attrib={
                    "id": f"patterns.{index}",
                    "from": str(from_pos),
                    "to": str(to_pos),
                },
                children=[el("alignment", attrib={"idref": alignment.id})],
            )
        )

    for index, (from_pos, to_pos) in enumerate(blocks, start=1):
        frequencies = _empirical_frequencies(sequences, from_pos, to_pos)
        kappa_id = f"hky.kappa.{index}"
        kappa_ids.append(kappa_id)
        nodes.append(
            el(
                "hkyModel",
                attrib={"id": f"hky.{index}"},
                children=[
                    el(
                        "frequencies",
                        children=[
                            el(
                                "frequencyModel",
                                attrib={"dataType": "nucleotide"},
                                children=[
                                    el("alignment", attrib={"idref": alignment.id}),
                                    el(
                                        "frequencies",
                                        children=[
                                            param_node(
                                                f"hky.frequencies.{index}",
                                                " ".join(f"{f:.10g}" for f in frequencies),
                                                dimension=4,
                                                estimate=False,
                                            )
                                        ],
                                    ),
                                ],
                            )
                        ],
                    ),
                    el(
                        "kappa",
                        children=[
                            param_node(
                                kappa_id,
                                kappa,
                                lower=0.0,
                                estimate=estimate_kappa,
                            )
                        ],
                    ),
                ],
            )
        )

    for index in range(1, n_blocks + 1):
        nodes.append(
            el(
                "siteModel",
                attrib={"id": f"siteModel.{index}"},
                children=[
                    el(
                        "substitutionModel",
                        children=[el("hkyModel", attrib={"idref": f"hky.{index}"})],
                    ),
                    el(
                        "relativeRate",
                        children=[param_node(f"siteModel.mu.{index}", 1.0, estimate=False)],
                    ),
                ],
            )
        )

    nodes.append(
        el(
            "constantSize",
            attrib={"id": "arg.popSize", "units": "substitutions"},
            children=[
                el(
                    "populationSize",
                    children=[param_node("arg.popSize.value", 1.0, lower=0.0)],
                )
            ],
        )
    )
    nodes.append(
        el(
            "coalescentTree",
            attrib={"id": "argStartingTree", "rootHeight": "1.0"},
            children=[
                el("taxa", attrib={"idref": taxa_node["attrib"]["id"]}),
                el("constantSize", attrib={"idref": "arg.popSize"}),
            ],
        )
    )
    nodes.append(
        el(
            "argTreeModel",
            attrib={"id": "argModel", "partitionType": "recombination"},
            children=[
                el("coalescentTree", attrib={"idref": "argStartingTree"}),
                el("rootHeight", children=[el("parameter", attrib={"id": "argModel.rootHeight"})]),
                el(
                    "nodeHeights",
                    attrib={"internalNodes": "true"},
                    children=[el("parameter", attrib={"id": "argModel.internalNodeHeights"})],
                ),
                el(
                    "nodeHeights",
                    attrib={"internalNodes": "true", "rootNode": "true"},
                    children=[el("parameter", attrib={"id": "argModel.allInternalNodeHeights"})],
                ),
            ],
        )
    )
    nodes.append(
        el(
            "strictClockBranchRates",
            attrib={"id": "branchRates"},
            children=[el("rate", children=[param_node("clock.rate", 1.0, estimate=False)])],
        )
    )
    nodes.append(
        el(
            "gammaPrior",
            attrib={"id": "rootHeightPrior", "shape": "0.01", "scale": "100.0", "offset": "0.0"},
            children=[el("parameter", attrib={"idref": "argModel.rootHeight"})],
        )
    )
    nodes.append(
        el(
            "argUniformPrior",
            attrib={"id": "argUniformPrior"},
            children=[el("argModel", attrib={"idref": "argModel"})],
        )
    )
    nodes.append(
        el(
            "uniformPartitionLikelihood",
            attrib={"id": "partitionPrior"},
            children=[el("argModel", attrib={"idref": "argModel"})],
        )
    )
    for index in range(1, n_blocks + 1):
        if estimate_kappa:
            nodes.append(
                el(
                    "gammaPrior",
                    attrib={
                        "id": f"kappaPrior.{index}",
                        "shape": "0.5",
                        "scale": "2.0",
                        "offset": "0.0",
                    },
                    children=[el("parameter", attrib={"idref": f"hky.kappa.{index}"})],
                )
            )

    for index in range(1, n_blocks + 1):
        likelihood = el(
            "argTreeLikelihood",
            attrib={"id": f"treeLikelihood.{index}"},
            children=[
                el("patterns", attrib={"idref": f"patterns.{index}"}),
                el("argTreeModel", attrib={"idref": "argModel"}),
                el("siteModel", attrib={"idref": f"siteModel.{index}"}),
                el("strictClockBranchRates", attrib={"idref": "branchRates"}),
            ],
        )
        likelihoods.append(likelihood)
        nodes.append(likelihood)

    operators: List[Dict[str, Any]] = [
        el(
            "scaleOperator",
            attrib={"id": "rootHeightScaler", "scaleFactor": "0.5", "weight": "5"},
            children=[el("parameter", attrib={"idref": "argModel.rootHeight"})],
        ),
        el(
            "uniformOperator",
            attrib={"weight": "30"},
            children=[el("parameter", attrib={"idref": "argModel.internalNodeHeights"})],
        ),
        el(
            "argSwapOperator",
            attrib={"weight": "10", "type": "narrowSwap"},
            children=[el("argModel", attrib={"idref": "argModel"})],
        ),
        el(
            "argSwapOperator",
            attrib={"weight": "4", "type": "bifurcationSwap"},
            children=[el("argModel", attrib={"idref": "argModel"})],
        ),
        el(
            "argSwapOperator",
            attrib={"weight": "4", "type": "reassortmentSwap"},
            children=[el("argModel", attrib={"idref": "argModel"})],
        ),
        el(
            "argPartitionOperator",
            attrib={"weight": "20"},
            children=[el("argTreeModel", attrib={"idref": "argModel"})],
        ),
    ]
    if estimate_kappa:
        for index in range(1, n_blocks + 1):
            operators.append(
                el(
                    "scaleOperator",
                    attrib={"scaleFactor": "0.75", "weight": "1"},
                    children=[el("parameter", attrib={"idref": f"hky.kappa.{index}"})],
                )
            )
    nodes.append(el("operators", attrib={"id": "operators"}, children=operators))

    posterior_prior_children: List[Dict[str, Any]] = [
        el("gammaPrior", attrib={"idref": "rootHeightPrior"}),
        el("argUniformPrior", attrib={"idref": "argUniformPrior"}),
        el("uniformPartitionLikelihood", attrib={"idref": "partitionPrior"}),
    ]
    if estimate_kappa:
        for index in range(1, n_blocks + 1):
            posterior_prior_children.append(
                el("gammaPrior", attrib={"idref": f"kappaPrior.{index}"})
            )

    mcmc_children: List[Dict[str, Any]] = [
        el(
            "posterior",
            attrib={"id": "posterior"},
            children=[
                el("prior", attrib={"id": "prior"}, children=posterior_prior_children),
                el(
                    "likelihood",
                    attrib={"id": "likelihood"},
                    children=[
                        el("argTreeLikelihood", attrib={"idref": likelihood["attrib"]["id"]})
                        for likelihood in likelihoods
                    ],
                ),
            ],
        ),
        el("operators", attrib={"idref": "operators"}),
    ]

    trace = getattr(config, "loggers", None)
    trace_name = ""
    trace_every = None
    screen_every = None
    if trace is not None:
        trace_name = str(trace.trace_log.get("file_name") or "")
        trace_every = trace.trace_log.get("log_every")
        screen_every = trace.screen_log.get("log_every")
    mcmc_children.append(
        el(
            "log",
            attrib={
                "id": "screenLog",
                "logEvery": str(screen_every or 1000),
            },
            children=[
                el(
                    "column",
                    attrib={"label": "Posterior", "dp": "4", "width": "12"},
                    children=[el("posterior", attrib={"idref": "posterior"})],
                ),
                el(
                    "column",
                    attrib={"label": "Prior", "dp": "4", "width": "12"},
                    children=[el("prior", attrib={"idref": "prior"})],
                ),
                el(
                    "column",
                    attrib={"label": "Likelihood", "dp": "4", "width": "12"},
                    children=[el("likelihood", attrib={"idref": "likelihood"})],
                ),
                el(
                    "column",
                    attrib={"label": "ARG.rootHeight", "sf": "6", "width": "12"},
                    children=[el("parameter", attrib={"idref": "argModel.rootHeight"})],
                ),
            ],
        )
    )
    file_children: List[Dict[str, Any]] = [
        el("posterior", attrib={"idref": "posterior"}),
        el("prior", attrib={"idref": "prior"}),
        el("likelihood", attrib={"idref": "likelihood"}),
        el("argModel", attrib={"idref": "argModel"}),
        el("parameter", attrib={"idref": "argModel.rootHeight"}),
    ]
    for index in range(1, n_blocks + 1):
        file_children.append(el("parameter", attrib={"idref": f"hky.kappa.{index}"}))
    mcmc_children.append(
        el(
            "log",
            attrib={
                "id": "fileLog",
                "logEvery": str(trace_every or 1000),
                "fileName": trace_name or "recombination.log",
            },
            children=file_children,
        )
    )
    nodes.append(
        el(
            "mcmc",
            attrib={
                "id": "mcmc",
                "chainLength": str(int(getattr(config.mcmc, "chain_length", 10000000))),
                "autoOptimize": "true",
            },
            children=mcmc_children,
        )
    )

    nodes.append(
        el(
            "report",
            children=[
                el(
                    "property",
                    attrib={"name": "timer"},
                    children=[el("object", attrib={"idref": "mcmc"})],
                )
            ],
        )
    )

    root["children"] = nodes
    return root
