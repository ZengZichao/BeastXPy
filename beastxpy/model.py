"""Model assembly for BeastXPy.

Builds the complete BEAST 1 XML structure tree (a tree of plain dicts) from a
:class:`BEASTConfig`.  Two invariants drive the design:

**1. Document order is dependency order.**  BEAST's ``XMLParser``
constructs objects strictly in document order, so every ``idref`` must point at
an object declared earlier.  Instead of hard-coding a top-level order, each
emitted fragment is scanned for the ids it *provides* and the ids it
*requires*; the fragments are then arranged by a stable topological sort
(ties broken by construction order) with cycle detection.  The construction
order below therefore only expresses intent, while the sort guarantees that
e.g. ``treeModel`` precedes every clock/rate statistic, ``<patterns>`` follows
its ``<alignment>`` and EBSP's ``variableDemographic`` follows ``treeModel``.
``_verify_reference_order`` re-checks the final document and raises
:class:`ModelAssemblyError` if any reference is still forward.

**2. Parameters are typed.**  Every emitted parameter is registered with its
kind (continuous / simplex / binary / category / integer / height) and its
effective bounds.  Operators and automatic priors are chosen from that
information plus :data:`BEAST_IMPLICIT_BOUNDS` (bounds BEAST imposes inside a
model, independent of what the YAML wrote), so that

* a ``scaleOperator`` is only used when at least one bound is infinite
  (BEAST rejects scale operators on doubly-bounded parameters),
* integer/category/indicator parameters never receive a continuous prior
 and use bit-flip / swap / integer delta-exchange moves
,
* every ``randomWalkIntegerOperator`` carries a required ``windowSize``.

Conventions fixed by this module
--------------------------------
``GTR_BASE_RATE``
    GTR is emitted through five independent ``rateXX`` sub-elements; the sixth
    base rate ``rateGT`` is *fixed at 1.0 and omitted* (BEAST's ``GTRParser``
    accepts either one 6-dimensional ``<rates>`` parameter or exactly five
    ``rateXX`` elements).  No operator, prior or log column is
    created for the omitted base.

``BEAST_DATA_TYPE``
    Internal ``DataType.AMINOACID`` spells itself ``amino-acid``; BEAST only
    accepts ``amino acid`` (with a space) in the ``dataType`` attribute
.

Calibrated starting trees
    A freshly simulated coalescent tree is far younger than a fossil
    calibration, so ``LogNormal(tmrca(...))`` starts at ``-Inf`` and BEAST
    refuses to start ("initial posterior is zero").  When the calibration
    distributions have finite support (uniform), the tree is a
    ``<coalescentTree>`` with ``<constrainedTaxa>`` whose ``<tmrca>`` elements
    carry the calibration ``ParametricDistributionModel`` child.  When the
    support is unbounded (lognormal/normal/exponential), BEAST's simulator
    cannot use the distribution's bounds, so the starting tree is built from
    nested ``<coalescentSimulator>`` elements with explicit ``height`` values
    taken from the calibration supports (:func:`calibration.calibration_start_age`).
    Both variants place every calibrated clade inside its distribution support.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .models import (
    BEASTConfig,
    HYPERPRIOR_EMITTABLE_TARGETS,
    CalibrationPoint,
    ClockModelType,
    InitTreeType,
    Partition,
    RealParameter,
    TreePriorType,
)
from .utils import format_param_value, param_id

StrTree = Dict[str, Any]

# ---------------------------------------------------------------------------
# Fixed conventions
# ---------------------------------------------------------------------------

#: Internal data-type spelling -> BEAST ``dataType`` attribute literal.
BEAST_DATA_TYPE = {"nucleotide": "nucleotide", "amino-acid": "amino acid"}

#: GTR rate element names, in BEAST's canonical order.
GTR_RATE_NAMES = ("rateAC", "rateAG", "rateAT", "rateCG", "rateCT", "rateGT")

#: The GTR rate that is fixed to 1.0 and omitted from the XML (BEAST expects
#: exactly five ``rateXX`` sub-elements; see module docstring).
GTR_BASE_RATE = "rateGT"
GTR_EMITTED_RATES = GTR_RATE_NAMES[: GTR_RATE_NAMES.index(GTR_BASE_RATE)]

#: Bounds that BEAST imposes *inside* a model on otherwise plain parameters.
#: Operator selection must use these (intersected with the YAML bounds), not
#: the presence or absence of an ``upper`` attribute in the configuration
#:.  Keyed by full or suffix parameter id.
BEAST_IMPLICIT_BOUNDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    "birthDeath.relativeDeathRate": (0.0, 1.0),
}
BEAST_IMPLICIT_BOUND_SUFFIXES: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    ".relativeDeathRate": (0.0, 1.0),
    "frequencies": (0.0, 1.0),
    ".pInv": (0.0, 1.0),
    ".indicators": (0.0, 1.0),
    ".categories": (0.0, None),
}

# Parameter kinds.  They drive operator and prior selection.
CONTINUOUS = "continuous"
SIMPLEX = "simplex"  # frequencies: sum-to-one, delta exchange
BINARY = "binary"  # 0/1 indicators: bit flip
CATEGORY = "category"  # category indices: swap
INTEGER = "integer"  # bounded integer vectors: integer delta exchange
HEIGHT = "height"  # tree heights: dedicated operators only


class ModelAssemblyError(ValueError):
    """Raised when the assembly invariants are violated."""


# ---------------------------------------------------------------------------
# Structure-tree helpers
# ---------------------------------------------------------------------------


def el(
    tag: str,
    attrib: Optional[Dict[str, Any]] = None,
    children: Optional[List[StrTree]] = None,
    text: Optional[str] = None,
    comment: Optional[str] = None,
) -> StrTree:
    """Build a structure-tree node."""
    node: Dict[str, Any] = {"tag": tag}
    if attrib:
        node["attrib"] = attrib
    if children:
        node["children"] = children
    if text is not None:
        node["text"] = text
    if comment is not None:
        node["comment"] = comment
    return node


def describe_distribution(node: StrTree) -> str:
    """One-line ``element(attr=value, ...)`` description of a prior node."""
    attributes = node.get("attrib") or {}
    rendered = ", ".join(f"{key}={value}" for key, value in attributes.items())
    return f"{node.get('tag', '?')}({rendered})" if rendered else str(node.get("tag", "?"))


def param_node(
    id_: str,
    value: Any = None,
    lower: Optional[float] = None,
    upper: Optional[float] = None,
    dimension: Optional[int] = None,
    estimate: Optional[bool] = None,
) -> StrTree:
    """Build a ``<parameter>`` node.

    BEAST 1 parameters do not accept an ``estimate`` attribute.  A parameter
    is *estimated* when it carries ``lower``/``upper`` bounds (an operator may
    then move it within bounds); a parameter with no bounds is a fixed
    constant.  Therefore:
      * estimated (estimate=True): keep lower/upper bounds.
      * fixed (estimate=False): drop lower/upper (emit value only).
    """
    attrib: Dict[str, Any] = {"id": id_}
    if value is not None:
        attrib["value"] = format_param_value(value)
    # BEAST 1: a fixed parameter carries no bounds.  Only keep bounds for
    # estimated parameters.
    if estimate is not False:
        if lower is not None:
            attrib["lower"] = format_param_value(lower)
        if upper is not None and not (isinstance(upper, float) and math.isinf(upper)):
            attrib["upper"] = format_param_value(upper)
    if dimension is not None and dimension > 1:
        attrib["dimension"] = str(dimension)
    return el("parameter", attrib=attrib)


def wrapped_param(
    wrapper: str,
    id_: str,
    value: Any = None,
    lower: Optional[float] = None,
    upper: Optional[float] = None,
    dimension: Optional[int] = None,
    estimate: Optional[bool] = None,
) -> StrTree:
    """Build ``<wrapper><parameter .../></wrapper>``."""
    return el(
        wrapper,
        children=[param_node(id_, value, lower, upper, dimension, estimate)],
    )


def _beast_data_type(alignment: Any) -> str:
    """Map an internal data type onto BEAST's ``dataType`` literal."""
    literal = getattr(alignment, "beast_data_type", None)
    if isinstance(literal, str) and literal:
        return literal
    data_type = getattr(alignment, "data_type", None)
    value = getattr(data_type, "value", data_type)
    return BEAST_DATA_TYPE.get(str(value), str(value))


def _implicit_bounds(pid: str, kind: str) -> Tuple[Optional[float], Optional[float]]:
    """Return BEAST's internal bounds for a parameter, if any."""
    if kind not in (CONTINUOUS, SIMPLEX):
        return (None, None)
    if pid in BEAST_IMPLICIT_BOUNDS:
        return BEAST_IMPLICIT_BOUNDS[pid]
    for suffix, bounds in BEAST_IMPLICIT_BOUND_SUFFIXES.items():
        if pid.endswith(suffix):
            return bounds
    return (None, None)


def _intersect_bounds(
    lower: Optional[float],
    upper: Optional[float],
    implicit: Tuple[Optional[float], Optional[float]],
) -> Tuple[Optional[float], Optional[float]]:
    ilo, ihi = implicit
    if ilo is not None:
        lower = ilo if lower is None else max(lower, ilo)
    if ihi is not None:
        upper = ihi if upper is None else min(upper, ihi)
    return lower, upper


# ---------------------------------------------------------------------------
# Parameter bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class _ParamInfo:
    id: str
    kind: str
    lower: Optional[float] = None
    upper: Optional[float] = None
    aliases: Tuple[str, ...] = ()

    @property
    def doubly_bounded(self) -> bool:
        return self.lower is not None and self.upper is not None


class _ParamRegistry:
    """Tracks every parameter emitted plus operator/prior coverage."""

    def __init__(self) -> None:
        self.all_ids: Set[str] = set()
        self.info: Dict[str, _ParamInfo] = {}
        self.estimated: Dict[str, _ParamInfo] = {}
        self.post_params: Set[str] = set()
        self.operator_targets: Set[str] = set()
        self.prior_targets: Set[str] = set()
        #: parameter id -> {"distribution": ..., "source": ...} for automatic priors
        self.auto_priors: Dict[str, Dict[str, str]] = {}
        #: alias -> ids using that alias
        self.alias_index: Dict[str, Set[str]] = {}

    def register(
        self,
        id_: str,
        estimate: bool,
        lower: Optional[float] = None,
        upper: Optional[float] = None,
        kind: str = CONTINUOUS,
        aliases: Iterable[str] = (),
    ) -> None:
        if id_ in self.all_ids:
            raise ModelAssemblyError(f"duplicate parameter id: {id_}")
        self.all_ids.add(id_)
        implicit = _implicit_bounds(id_, kind)
        lower, upper = _intersect_bounds(lower, upper, implicit)
        alias_values = [str(a) for a in aliases]
        # Partition-scoped ids historically use ``gene2.`` as prefix and
        # ``param_id`` adds another dot (``gene2..kappa``).  Accept the
        # natural single-dot spelling as an exact-id alias.
        if ".." in id_:
            alias_values.append(id_.replace("..", ".", 1))
        alias_tuple = tuple(dict.fromkeys(alias_values))
        info = _ParamInfo(id=id_, kind=kind, lower=lower, upper=upper, aliases=alias_tuple)
        self.info[id_] = info
        if estimate:
            self.estimated[id_] = info
        for alias in alias_tuple:
            self.alias_index.setdefault(alias, set()).add(id_)

    def claim_operator(self, target: str) -> None:
        self.operator_targets.add(target)

    def claim_prior(self, target: str) -> None:
        self.prior_targets.add(target)

    def candidates_for(self, target: Any) -> List[str]:
        """All emitted parameter ids an explicit-prior target refers to."""
        if not isinstance(target, str):
            return []
        found: Set[str] = set()
        if target in self.all_ids:
            found.add(target)
        found.update(self.alias_index.get(target, set()))
        return sorted(found)

    def check_invariants(self) -> List[str]:
        findings = []
        for pid in sorted(self.estimated):
            info = self.estimated[pid]
            if pid not in self.operator_targets:
                findings.append(f"estimated parameter '{pid}' has no operator (invariant violated)")
            if info.kind in (CONTINUOUS, SIMPLEX) and pid not in self.prior_targets:
                findings.append(f"estimated parameter '{pid}' has no prior (invariant violated)")
        return findings


@dataclass
class _Fragment:
    """A top-level XML node plus its place in the construction sequence."""

    node: StrTree
    index: int
    provides: Set[str] = field(default_factory=set)
    requires: Set[str] = field(default_factory=set)


def _scan_ids(node: StrTree) -> Tuple[Set[str], Set[str]]:
    """Return ``(declared ids, referenced ids)`` found anywhere in ``node``."""
    provides: Set[str] = set()
    refs: Set[str] = set()
    stack: List[Any] = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        attrib = current.get("attrib") or {}
        if "id" in attrib:
            provides.add(str(attrib["id"]))
        if "idref" in attrib:
            refs.add(str(attrib["idref"]))
        children = current.get("children")
        if children:
            stack.extend(children)
    return provides, refs


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


class ModelAssembler:
    """Assemble the full XML structure tree for a BEASTConfig."""

    def __init__(self, config: BEASTConfig) -> None:
        self.config = config
        self.registry = config_registry(config)
        self.params = _ParamRegistry()
        self.taxa_by_name: Dict[str, List[str]] = {}
        self._alignment_sources: Dict[str, Any] = {}
        #: portable sidecar file name -> absolute source path written next to
        #: the XML (for example the MDS distance table BEAST reads at parse time)
        self._sidecar_files: Dict[str, Path] = {}
        self._clock_rate_ids: Set[str] = set()
        self._explicit_prior_used: Set[int] = set()
        #: calibration name -> {distribution parameter -> emitted parameter id}
        self._hyperprior_parameter_ids: Dict[str, Dict[str, str]] = {}
        #: emitted hyperprior parameter id -> HyperpriorConfig
        self._hyperprior_priors: Dict[str, Any] = {}
        #: emitted hyperprior parameter id -> (operator tag, attributes)
        self._hyperprior_operators: Dict[str, Tuple[str, Dict[str, str]]] = {}
        # Give standalone (non-YAML) calibrations their sibling list too
        # .
        context = tuple(config.calibrations)
        for cal in config.calibrations:
            cal.calibration_context = context

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def assemble(self) -> StrTree:
        """Return the full ``<beast>`` structure tree."""
        self._collect_taxa()
        self._resolve_alignment_sources()
        self._validate_partition_ranges()

        builders: List[Any] = []
        # Construction order (the topological sort below is authoritative).
        builders.extend(self._build_taxa_sets())
        builders.extend(self._build_alignment_fragments())
        builders.extend(self._build_patterns())
        demo = self._build_starting_demo_model()
        if demo is not None:
            builders.append(demo)
        builders.append(self._build_starting_tree())
        builders.append(self._build_tree_model())
        builders.extend(self._build_tree_prior_objects())
        builders.extend(self._build_partition_models())
        builders.extend(self._build_clock_models())
        tree_prior_likelihood = self._build_tree_prior_likelihood()
        if tree_prior_likelihood is not None:
            builders.append(tree_prior_likelihood)
        builders.extend(self._build_calibration_statistics())
        builders.extend(self._build_hyperprior_parameters())
        builders.append(self._build_operators())
        builders.append(self._build_mcmc())
        builders.append(self._build_report())

        ordered = self._order_fragments(builders)
        self._verify_reference_order(ordered)

        issues = self.params.check_invariants()
        if issues:
            raise ModelAssemblyError("; ".join(issues))
        root = el("beast", children=ordered)
        from .coverage import apply_coverage

        try:
            return apply_coverage(root, self.config, self)
        except Exception as exc:  # noqa: BLE001 - surface plugin failures clearly
            raise ModelAssemblyError(
                f"analysis coverage extension failed: {type(exc).__name__}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Data preparation
    # ------------------------------------------------------------------
    def _collect_taxa(self) -> None:
        """Build the global taxon-name registry (keyed by name)."""
        for p in self.config.partitions:
            for seq in p.alignment.sequences:
                self.taxa_by_name.setdefault(seq.taxon, [])

    @property
    def _n_taxa(self) -> int:
        return len(self.taxa_by_name)

    @property
    def sidecar_files(self) -> Dict[str, Path]:
        """Portable sidecar files the XML references by relative name."""
        return dict(self._sidecar_files)

    def _resolve_alignment_sources(self) -> None:
        """Choose one *complete* alignment object per alignment id.

        ``config.all_alignments`` holds the alignments exactly as read from
        disk.  Partitions attach a ``from``/``to`` window to a *copy* of an
        alignment; preferring the longest copy guarantees the emitted
        ``<alignment>`` carries full sequences even if a caller handed us
        pre-sliced partitions.
        """
        sources: Dict[str, Any] = {}
        for alignment in getattr(self.config, "all_alignments", []) or []:
            sources.setdefault(alignment.id, alignment)
        for partition in self.config.partitions:
            alignment = partition.alignment
            existing = sources.get(alignment.id)
            if existing is None:
                sources[alignment.id] = alignment
                continue
            try:
                if alignment.n_sites > existing.n_sites:
                    sources[alignment.id] = alignment
            except ValueError as exc:
                raise ModelAssemblyError(str(exc)) from exc
        self._alignment_sources = sources

    def _partition_range(self, p: Partition) -> Tuple[int, int, int]:
        """Effective 1-based ``[from, to]`` plus the full alignment length."""
        source = self._alignment_sources.get(p.alignment.id, p.alignment)
        try:
            full_length = source.n_sites
        except ValueError as exc:
            raise ModelAssemblyError(str(exc)) from exc
        from_pos = p.alignment.from_pos if p.alignment.from_pos is not None else 1
        to_pos = p.alignment.to_pos if p.alignment.to_pos is not None else full_length
        return int(from_pos), int(to_pos), full_length  # type: ignore[return-value]

    def _validate_partition_ranges(self) -> None:
        """: from/to must be inside the full alignment."""
        for p in self.config.partitions:
            from_pos, to_pos, full_length = self._partition_range(p)
            if from_pos < 1:
                raise ModelAssemblyError(
                    f"partition {p.id!r}: 'from' must be >= 1 (got {from_pos})"
                )
            if to_pos < from_pos:
                raise ModelAssemblyError(
                    f"partition {p.id!r}: 'to' ({to_pos}) must be >= 'from' ({from_pos})"
                )
            if to_pos > full_length:
                raise ModelAssemblyError(
                    f"partition {p.id!r}: 'to' ({to_pos}) exceeds alignment "
                    f"{p.alignment.id!r} length ({full_length}); the assembler "
                    "needs the complete alignment"
                )

    # ------------------------------------------------------------------
    # Fragment ordering
    # ------------------------------------------------------------------
    def _order_fragments(self, nodes: Sequence[StrTree]) -> List[StrTree]:
        fragments: List[_Fragment] = []
        provider: Dict[str, int] = {}
        for index, node in enumerate(nodes):
            provides, refs = _scan_ids(node)
            frag = _Fragment(node=node, index=index, provides=provides, requires=refs)
            fragments.append(frag)
            for pid in provides:
                if pid in provider:
                    raise ModelAssemblyError(
                        f"duplicate XML id {pid!r} provided by more than one fragment"
                    )
                provider[pid] = index

        dependencies: List[Set[int]] = []
        for frag in fragments:
            deps = {
                provider[rid]
                for rid in frag.requires
                if rid in provider and provider[rid] != frag.index
            }
            dependencies.append(deps)

        remaining = {frag.index: set(dependencies[frag.index]) for frag in fragments}
        ordered: List[int] = []
        while remaining:
            ready = sorted(idx for idx, deps in remaining.items() if not deps)
            if not ready:
                cycle = self._find_cycle(remaining, fragments)
                raise ModelAssemblyError("cyclic XML object dependencies: " + " -> ".join(cycle))
            chosen = ready[0]
            ordered.append(chosen)
            del remaining[chosen]
            for deps in remaining.values():
                deps.discard(chosen)

        return [fragments[idx].node for idx in ordered]

    @staticmethod
    def _find_cycle(remaining: Dict[int, Set[int]], fragments: List[_Fragment]) -> List[str]:
        """Best-effort cycle description for the error message."""
        start = min(remaining)
        path = [start]
        seen = {start}
        current = start
        while True:
            deps = sorted(remaining.get(current, ()))
            if not deps:
                break
            nxt = deps[0]
            if nxt in seen:
                cycle = path[path.index(nxt) :] + [nxt]
                names = []
                for idx in cycle:
                    names.append(",".join(sorted(fragments[idx].provides)) or f"#{idx}")
                return names
            path.append(nxt)
            seen.add(nxt)
            current = nxt
        return [",".join(sorted(fragments[i].provides)) or f"#{i}" for i in path]

    @staticmethod
    def _verify_reference_order(nodes: Sequence[StrTree]) -> None:
        """Assert that no ``idref`` precedes the declaration of its target."""
        declared: Set[str] = set()
        all_ids: Set[str] = set()
        for node in nodes:
            provides, _ = _scan_ids(node)
            all_ids.update(provides)

        def walk(node: Any) -> None:
            if not isinstance(node, dict):
                return
            attrib = node.get("attrib") or {}
            ref = attrib.get("idref")
            if ref is not None:
                ref = str(ref)
                if ref not in declared and ref in all_ids:
                    raise ModelAssemblyError(
                        f"<{node.get('tag')}> references {ref!r} before it is declared "
                        "(BEAST requires declaration order)"
                    )
            if "id" in attrib:
                declared.add(str(attrib["id"]))
            for child in node.get("children") or []:
                walk(child)

        for node in nodes:
            walk(node)

    # ------------------------------------------------------------------
    # Taxa / alignment / patterns
    # ------------------------------------------------------------------
    def _build_taxa_sets(self) -> List[StrTree]:
        from .calibration import build_taxa_sets

        return build_taxa_sets(self.config)

    def _build_alignment_fragments(self) -> List[StrTree]:
        """One *complete* ``<alignment>`` per alignment id."""
        emitted: Set[str] = set()
        fragments: List[StrTree] = []
        for p in self.config.partitions:
            alignment_id = p.alignment.id
            if alignment_id in emitted:
                continue
            emitted.add(alignment_id)
            source = self._alignment_sources.get(alignment_id, p.alignment)
            fragments.append(self._alignment_fragment(source))
        return fragments

    def _alignment_fragment(self, alignment: Any) -> StrTree:
        attrib = {"id": alignment.id, "dataType": _beast_data_type(alignment)}
        sequences = [
            {
                "tag": "sequence",
                "children": [{"tag": "taxon", "attrib": {"idref": seq.taxon}}],
                "text": seq.sequence,
            }
            for seq in alignment.sequences
        ]
        return {"tag": "alignment", "attrib": attrib, "children": sequences}

    def _build_patterns(self) -> List[StrTree]:
        """One ``<patterns from=... to=...>`` per partition."""
        fragments: List[StrTree] = []
        for p in self.config.partitions:
            from_pos, to_pos, _ = self._partition_range(p)
            fragments.append(
                el(
                    "patterns",
                    attrib={
                        "id": self._patterns_id(p),
                        "from": str(from_pos),
                        "to": str(to_pos),
                    },
                    children=[el("alignment", attrib={"idref": p.alignment.id})],
                )
            )
        return fragments

    def _patterns_id(self, p: Partition) -> str:
        return f"patterns.{p.id}" if len(self.config.partitions) > 1 else "patterns"

    # ------------------------------------------------------------------
    # Starting tree
    # ------------------------------------------------------------------
    def _build_starting_demo_model(self) -> Optional[StrTree]:
        """Fixed constant-size model reused by coalescent starting trees."""
        mode = self.config.control.initial_tree
        if mode not in (InitTreeType.COALESCENT.value, "coalescent", None, ""):
            return None
        pid = "startingTree.constantSize.popSize"
        self.params.register(pid, False)
        return el(
            "constantSize",
            attrib={"id": "startingTree.constantSize", "units": "substitutions"},
            children=[wrapped_param("populationSize", pid, 1.0, estimate=False)],
        )

    def _build_starting_tree(self) -> StrTree:
        mode = self.config.control.initial_tree
        if mode == InitTreeType.NEWICK.value:
            return el(
                "newick",
                attrib={"id": "startingTree"},
                text=self.config.control.newick or "",
            )
        if mode == InitTreeType.UPGMA.value:
            return self._upgma_tree()
        calibrated = [c for c in self.config.calibrations if c.distribution is not None]
        if calibrated:
            if self._calibrated_starting_tree_kind(calibrated) == "coalescentSimulator":
                return self._calibrated_coalescent_simulator()
            return self._constrained_coalescent_tree(calibrated)
        return el(
            "coalescentTree",
            attrib={"id": "startingTree"},
            children=[
                el("taxa", attrib={"idref": "taxa"}),
                el("constantSize", attrib={"idref": "startingTree.constantSize"}),
            ],
        )

    @staticmethod
    def _calibrated_starting_tree_kind(calibrations: Sequence[CalibrationPoint]) -> str:
        """Choose the starting-tree element for calibrated configurations.

        A constrained coalescent tree (``<constrainedTaxa><tmrca>``) is only
        safe when every calibration has finite support: BEAST's
        ``OldCoalescentSimulatorParser`` ignores the ``offset`` attribute of
        the embedded distribution model, so an offset calibration would start
        below its own support and yield a zero initial posterior.  Those
        configurations use nested ``coalescentSimulator`` trees whose root
        height is the calibration-aware starting age.
        """
        from .calibration import has_finite_support

        if all(has_finite_support(cal) for cal in calibrations):
            return "coalescentTree"
        return "coalescentSimulator"

    def _constrained_coalescent_tree(self, calibrations: Sequence[CalibrationPoint]) -> StrTree:
        """``<coalescentTree>`` whose ``<tmrca>`` children carry the calibration
        distributions (works because every support is finite)."""
        from .calibration import constrained_taxa_fragment

        root_height = max(
            (
                self._starting_height_for_set(frozenset(self._calibration_set(cal)))
                for cal in calibrations
            ),
            default=1.0,
        )
        constrained: List[StrTree] = [el("taxa", attrib={"idref": "taxa"})]
        for cal in calibrations:
            constrained.append(constrained_taxa_fragment(cal, include_distribution=True))
        attrib: Dict[str, Any] = {"id": "startingTree"}
        attrib["rescaleHeight"] = format_param_value(root_height * 1.01)
        return el(
            "coalescentTree",
            attrib=attrib,
            children=[
                el("constrainedTaxa", children=constrained),
                el("constantSize", attrib={"idref": "startingTree.constantSize"}),
            ],
        )

    def _calibration_set(self, cal: CalibrationPoint) -> Set[str]:
        if not cal.taxa:
            return set(self.taxa_by_name)
        return set(cal.taxa)

    def _starting_height_for_set(self, taxa_set: frozenset) -> float:
        from .calibration import calibration_start_age

        ages = [
            calibration_start_age(cal)
            for cal in self.config.calibrations
            if frozenset(self._calibration_set(cal)) == taxa_set
        ]
        return max(ages, default=0.0)

    def _calibrated_coalescent_simulator(self) -> StrTree:
        """Nested ``<coalescentSimulator>`` tree with explicit clade heights.

        Each calibration taxon set becomes a subtree whose root height is at
        least the calibration's support lower bound, so every
        ``LogNormal(tmrca(...))`` / ``Normal(tmrca(...))`` density is finite at
        the initial state.  Nested clades are real clades of the
        simulated tree, which also enforces the monophyly semantics.
        """
        all_taxa = frozenset(self.taxa_by_name)
        groups: Dict[frozenset, List[CalibrationPoint]] = {}
        for cal in self.config.calibrations:
            taxa = frozenset(self._calibration_set(cal))
            if not taxa:
                continue
            if not taxa <= all_taxa:
                missing = sorted(taxa - all_taxa)
                raise ModelAssemblyError(
                    f"calibration {cal.name!r} references taxa not present in any "
                    f"partition: {', '.join(missing)}"
                )
            groups.setdefault(taxa, []).append(cal)

        if not groups:
            return el(
                "coalescentTree",
                attrib={"id": "startingTree"},
                children=[
                    el("taxa", attrib={"idref": "taxa"}),
                    el("constantSize", attrib={"idref": "startingTree.constantSize"}),
                ],
            )

        # Laminar check: clade sets must be nested or disjoint.
        sets = list(groups)
        for i, a in enumerate(sets):
            for b in sets[i + 1 :]:
                if a & b and not (a <= b or b <= a):
                    raise ModelAssemblyError(
                        "calibration taxon sets overlap without containment "
                        f"({sorted(a & b)}); a starting tree cannot make both "
                        "sets monophyletic"
                    )

        parent: Dict[frozenset, Optional[frozenset]] = {}
        children: Dict[frozenset, List[frozenset]] = {s: [] for s in sets}
        for s in sets:
            supersets = [t for t in sets if s < t]
            parent[s] = min(supersets, key=len) if supersets else None
            if parent[s] is not None:
                children[parent[s]].append(s)  # type: ignore[index]

        heights: Dict[frozenset, float] = {}

        def height_of(taxa_set: frozenset) -> float:
            if taxa_set in heights:
                return heights[taxa_set]
            own = self._starting_height_for_set(taxa_set)
            child_max = max((height_of(c) for c in children[taxa_set]), default=0.0)
            if child_max > 0:
                gap = max(own * 0.05, child_max * 0.05, 0.01)
                value = max(own, child_max + gap)
            else:
                value = max(own, 0.01)
            heights[taxa_set] = value
            return value

        def taxa_id_for(taxa_set: frozenset) -> str:
            if taxa_set == all_taxa:
                return "taxa"
            from .calibration import calibration_taxa_id

            return calibration_taxa_id(groups[taxa_set][0].name)

        def simulator(taxa_set: frozenset, sim_id: Optional[str]) -> StrTree:
            attrib: Dict[str, Any] = {}
            if sim_id:
                attrib["id"] = sim_id
            attrib["height"] = format_param_value(height_of(taxa_set))
            nested = [simulator(c, None) for c in children[taxa_set]]
            nested.append(el("taxa", attrib={"idref": taxa_id_for(taxa_set)}))
            nested.append(el("constantSize", attrib={"idref": "startingTree.constantSize"}))
            return el("coalescentSimulator", attrib=attrib, children=nested)

        top_level = [s for s in sets if parent[s] is None]
        if any(s == all_taxa for s in top_level):
            root_set = all_taxa
        else:
            root_set = all_taxa
            child_max = max((height_of(s) for s in top_level), default=0.0)
            heights[root_set] = max(child_max * 1.05, child_max + 0.01, 0.01)
            children[root_set] = top_level
            for s in top_level:
                parent[s] = root_set
            # ``taxa_id_for`` already returns the global set for all taxa.

        return simulator(root_set, "startingTree")

    def _upgma_tree(self) -> StrTree:
        p0 = self.config.partitions[0]
        return el(
            "upgmaTree",
            attrib={"id": "startingTree"},
            children=[
                el(
                    "distanceMatrix",
                    attrib={"correction": "JC"},
                    children=[el("patterns", attrib={"idref": self._patterns_id(p0)})],
                )
            ],
        )

    # ------------------------------------------------------------------
    # treeModel
    # ------------------------------------------------------------------
    def _build_tree_model(self) -> StrTree:
        self.params.register("treeModel.rootHeight", True, kind=HEIGHT)
        self.params.register("treeModel.internalNodeHeights", True, kind=HEIGHT)
        self.params.register("treeModel.allInternalNodeHeights", True, kind=HEIGHT)
        return el(
            "treeModel",
            attrib={"id": "treeModel"},
            children=[
                el(self._starting_tree_ref_tag(), attrib={"idref": "startingTree"}),
                el("rootHeight", children=[param_node("treeModel.rootHeight", estimate=True)]),
                el(
                    "nodeHeights",
                    attrib={"internalNodes": "true"},
                    children=[param_node("treeModel.internalNodeHeights", estimate=True)],
                ),
                el(
                    "nodeHeights",
                    attrib={"internalNodes": "true", "rootNode": "true"},
                    children=[param_node("treeModel.allInternalNodeHeights", estimate=True)],
                ),
            ],
        )

    def _starting_tree_ref_tag(self) -> str:
        mode = self.config.control.initial_tree
        if mode == InitTreeType.NEWICK.value:
            return "newick"
        if mode == InitTreeType.UPGMA.value:
            return "upgmaTree"
        calibrated = [c for c in self.config.calibrations if c.distribution is not None]
        if calibrated:
            return self._calibrated_starting_tree_kind(calibrated)
        return "coalescentTree"

    # ------------------------------------------------------------------
    # Tree prior objects (models + skyline / EBSP likelihoods)
    # ------------------------------------------------------------------
    def _build_tree_prior_objects(self) -> List[StrTree]:
        tp = self.config.tree_prior
        if tp.type == TreePriorType.SKYLINE:
            return [self._skyline_fragment(tp.params)]
        if tp.type == TreePriorType.EBSP:
            return self._ebsp_fragments(tp.params)
        if tp.type == TreePriorType.YULE:
            return [self._yule_fragment(tp.params)]
        if tp.type == TreePriorType.BIRTH_DEATH:
            return [self._birth_death_fragment(tp.params, tp.conditional_on_root)]
        return [self._population_fragment(tp.type, tp.params)]

    def _yule_fragment(self, params: Dict[str, Any]) -> StrTree:
        birth_rate = params.get("birth_rate") or RealParameter(value=1.0, lower=0.0, upper=1000.0)
        self.params.register(
            "yule.birthRate",
            birth_rate.estimate,
            birth_rate.lower,
            birth_rate.upper,
            aliases=("birth_rate", "yule.birthRate"),
        )
        return el(
            "yuleModel",
            attrib={"id": "yule", "units": "substitutions"},
            children=[
                wrapped_param(
                    "birthRate",
                    "yule.birthRate",
                    birth_rate.value,
                    birth_rate.lower,
                    birth_rate.upper,
                    estimate=birth_rate.estimate,
                )
            ],
        )

    def _birth_death_fragment(self, params: Dict[str, Any], conditional: bool) -> StrTree:
        bmd = params.get("birth_minus_death") or RealParameter(value=0.5, lower=0.0)
        rdr = params.get("relative_death") or RealParameter(value=0.5, lower=0.0, upper=1.0)
        #: BirthDeathModel constrains relativeDeathRate to [0, 1]
        # internally; make the bounds explicit so the scale/random-walk choice
        # is visible in the XML.
        rdr_lower = 0.0 if rdr.lower is None else max(0.0, float(rdr.lower))
        rdr_upper = 1.0 if rdr.upper is None else min(1.0, float(rdr.upper))
        self.params.register(
            "birthDeath.bMinusD",
            bmd.estimate,
            bmd.lower,
            bmd.upper,
            aliases=("birth_minus_death", "birthDeath.bMinusD"),
        )
        self.params.register(
            "birthDeath.relativeDeathRate",
            rdr.estimate,
            rdr_lower,
            rdr_upper,
            aliases=("relative_death", "birthDeath.relativeDeathRate"),
        )
        attrib = {"id": "birthDeath", "units": "substitutions"}
        if conditional:
            attrib["conditionalOnRoot"] = "true"
        return el(
            "birthDeathModel",
            attrib=attrib,
            children=[
                wrapped_param(
                    "birthMinusDeathRate",
                    "birthDeath.bMinusD",
                    bmd.value,
                    bmd.lower,
                    bmd.upper,
                    estimate=bmd.estimate,
                ),
                wrapped_param(
                    "relativeDeathRate",
                    "birthDeath.relativeDeathRate",
                    rdr.value,
                    rdr_lower,
                    rdr_upper,
                    estimate=rdr.estimate,
                ),
            ],
        )

    def _population_fragment(self, tp_type: TreePriorType, params: Dict[str, Any]) -> StrTree:
        """Coalescent population models (constant/exponential/logistic/expansion)."""
        if tp_type == TreePriorType.COALESCENT_CONSTANT:
            tag, prefix = "constantSize", "constant"
            entries = [
                (
                    "populationSize",
                    "constant.popSize",
                    params.get("population_size"),
                    RealParameter(value=1.0, lower=0.0, upper=1e8),
                ),
            ]
        elif tp_type == TreePriorType.COALESCENT_EXPONENTIAL:
            tag, prefix = "exponentialGrowth", "expGrowth"
            entries = [
                (
                    "populationSize",
                    "expGrowth.popSize",
                    params.get("population_size"),
                    RealParameter(value=1.0, lower=0.0, upper=1e8),
                ),
                (
                    "growthRate",
                    "expGrowth.growthRate",
                    params.get("growth_rate"),
                    RealParameter(value=0.1, lower=-100.0, upper=100.0),
                ),
            ]
        elif tp_type == TreePriorType.COALESCENT_LOGISTIC:
            tag, prefix = "logisticGrowth", "logistic"
            entries = [
                (
                    "populationSize",
                    "logistic.popSize",
                    params.get("population_size"),
                    RealParameter(value=1.0, lower=0.0, upper=1e8),
                ),
                (
                    "growthRate",
                    "logistic.growthRate",
                    params.get("growth_rate"),
                    RealParameter(value=0.1, lower=0.0),
                ),
                ("t50", "logistic.t50", params.get("t50"), RealParameter(value=0.1, lower=0.0)),
            ]
        else:  # COALESCENT_EXPANSION
            tag, prefix = "expansion", "expansion"
            entries = [
                (
                    "populationSize",
                    "expansion.popSize",
                    params.get("population_size"),
                    RealParameter(value=1.0, lower=0.0, upper=1e8),
                ),
                (
                    "ancestralPopulationProportion",
                    "expansion.ancestralProportion",
                    params.get("ancestral_population_proportion"),
                    RealParameter(value=0.1, lower=0.0, upper=1.0),
                ),
                (
                    "growthRate",
                    "expansion.growthRate",
                    params.get("growth_rate"),
                    RealParameter(value=0.1, lower=0.0),
                ),
            ]

        children = []
        for wrapper, pid, raw_param, default in entries:
            param = raw_param if raw_param is not None else default
            aliases = [pid]
            if pid.endswith("popSize"):
                aliases.append("population_size")
            elif pid.endswith("growthRate"):
                aliases.append("growth_rate")
            elif pid.endswith("ancestralProportion"):
                aliases.append("ancestral_population_proportion")
            elif pid.endswith("t50"):
                aliases.append("t50")
            self.params.register(pid, param.estimate, param.lower, param.upper, aliases=aliases)
            children.append(
                wrapped_param(
                    wrapper,
                    pid,
                    param.value,
                    param.lower,
                    param.upper,
                    dimension=getattr(param, "dimension", None),
                    estimate=param.estimate,
                )
            )
        return el(tag, attrib={"id": prefix, "units": "substitutions"}, children=children)

    # -- skyline ---------------------------------------------
    def _skyline_fragment(self, params: Dict[str, Any]) -> StrTree:
        """``<generalizedSkyLineLikelihood linear="false">``.

        Stepwise skyline (``linear="false"``) requires the same number of
        population sizes and group sizes; both default to five control points.
        """
        from .models import RealParameter as RP  # local alias for readability

        groups_raw = params.get("skyline_groups")
        pops_raw = params.get("skyline_pop_sizes")
        population_size = params.get("population_size")

        group_dim, group_value = self._vector_spec(groups_raw, None, 1.0)
        pop_dim, pop_value = self._vector_spec(pops_raw, None, None)
        if pop_dim is None and isinstance(population_size, RP):
            if (population_size.dimension or 1) > 1:
                pop_dim, pop_value = population_size.dimension, population_size.value
            elif pop_value is None:
                pop_value = population_size.value
        elif (
            pop_dim is None
            and isinstance(population_size, int)
            and not isinstance(population_size, bool)
        ):
            pop_dim = population_size

        dim = group_dim or pop_dim or 5
        if group_dim is not None and pop_dim is not None and group_dim != pop_dim:
            raise ModelAssemblyError(
                "tree_prior.skyline_groups and skyline_pop_sizes specify "
                f"different dimensions ({group_dim} vs {pop_dim}); a stepwise "
                "skyline requires equal numbers of population sizes and groups"
            )
        if dim > self._n_taxa - 1:
            raise ModelAssemblyError(
                f"tree_prior.skyline_groups={dim} exceeds the number of "
                f"coalescent events ({self._n_taxa - 1}) available for "
                f"{self._n_taxa} taxa"
            )
        if dim < 1:
            raise ModelAssemblyError("skyline control points must be >= 1")

        pop_value = 1.0 if pop_value is None else pop_value
        group_value = 1.0 if group_value is None else group_value

        self.params.register(
            "skyline.popSize",
            True,
            lower=0.0,
            upper=None,
            aliases=("population_size", "skyline.popSize", "skyline_pop_sizes"),
        )
        self.params.register(
            "skyline.groupSize",
            True,
            lower=1.0,
            upper=None,
            kind=INTEGER,
            aliases=("skyline.groupSize", "skyline_groups"),
        )
        return el(
            "generalizedSkyLineLikelihood",
            attrib={"id": "skyline", "linear": "false"},
            children=[
                wrapped_param(
                    "populationSizes",
                    "skyline.popSize",
                    pop_value,
                    lower=0.0,
                    upper=None,
                    dimension=dim,
                ),
                wrapped_param(
                    "groupSizes",
                    "skyline.groupSize",
                    group_value,
                    lower=1.0,
                    upper=None,
                    dimension=dim,
                ),
                el("populationTree", children=[el("treeModel", attrib={"idref": "treeModel"})]),
            ],
        )

    # -- EBSP ------------------------------------------------
    def _ebsp_fragments(self, params: Dict[str, Any]) -> List[StrTree]:
        """``<variableDemographic>`` + sum statistic + population-mean model."""
        pop_dim, pop_value = self._vector_spec(params.get("ebsp_pop_sizes"), None, None)
        if params.get("population_size") is not None and pop_dim is None:
            dim, value = self._vector_spec(params.get("population_size"), None, None)
            if dim is not None:
                pop_dim, pop_value = dim, value
        dim = pop_dim if pop_dim is not None else 10
        if dim < 2:
            raise ModelAssemblyError("tree_prior.ebsp_pop_sizes must be >= 2")
        pop_value = 0.5 if pop_value is None else pop_value
        indicator_dim = max(1, dim - 1)

        self.params.register(
            "demographic.popSize",
            True,
            lower=0.0,
            upper=None,
            aliases=("population_size", "demographic.popSize", "ebsp_pop_sizes"),
        )
        self.params.register(
            "demographic.indicators",
            True,
            lower=0.0,
            upper=1.0,
            kind=BINARY,
            aliases=("demographic.indicators",),
        )
        self.params.register(
            "demographic.populationMean",
            True,
            lower=0.0,
            upper=None,
            aliases=("demographic.populationMean",),
        )

        demographic = el(
            "variableDemographic",
            attrib={"id": "demographic", "type": "linear", "useMidpoints": "true"},
            children=[
                wrapped_param(
                    "populationSizes",
                    "demographic.popSize",
                    pop_value,
                    lower=0.0,
                    upper=None,
                    dimension=dim,
                ),
                wrapped_param(
                    "indicators",
                    "demographic.indicators",
                    0.0,
                    lower=0.0,
                    upper=1.0,
                    dimension=indicator_dim,
                ),
                el(
                    "trees",
                    children=[
                        el(
                            "ptree",
                            attrib={"ploidy": "2.0"},
                            children=[el("treeModel", attrib={"idref": "treeModel"})],
                        )
                    ],
                ),
            ],
        )
        sum_stat = el(
            "sumStatistic",
            attrib={"id": "demographic.populationSizeChanges", "elementwise": "true"},
            children=[el("parameter", attrib={"idref": "demographic.indicators"})],
        )
        mean_dist = el(
            "exponentialDistributionModel",
            attrib={"id": "demographic.populationMeanDist"},
            children=[
                el(
                    "mean",
                    children=[
                        param_node("demographic.populationMean", 0.5, lower=0.0, estimate=True)
                    ],
                )
            ],
        )
        return [demographic, sum_stat, mean_dist]

    # ------------------------------------------------------------------
    # Clock models
    # ------------------------------------------------------------------
    def _build_clock_models(self) -> List[StrTree]:
        shared = all(p.clock == "shared" for p in self.config.partitions)
        fragments: List[StrTree] = []
        if shared:
            p0 = self.config.partitions[0]
            fragments.append(self._clock_fragment(p0.clock_model, ""))
            fragments.extend(self._relaxed_clock_statistics(p0.clock_model, ""))
        else:
            for p in self.config.partitions:
                fragments.append(self._clock_fragment(p.clock_model, f"{p.id}."))
                fragments.extend(self._relaxed_clock_statistics(p.clock_model, f"{p.id}."))
        return fragments

    def _clock_fragment(self, clock_model: Any, prefix: str) -> StrTree:
        ctype = clock_model.type
        clock_id = param_id(prefix, "branchRates")
        if ctype == ClockModelType.STRICT:
            rate = clock_model.clock_rate or RealParameter(value=1.0, lower=0.0)
            pid = param_id(prefix, "clock.rate")
            self.params.register(
                pid,
                rate.estimate,
                rate.lower,
                rate.upper,
                aliases=self._clock_aliases(prefix, "clock_rate"),
            )
            if rate.estimate:
                self._clock_rate_ids.add(pid)
            return el(
                "strictClockBranchRates",
                attrib={"id": clock_id},
                children=[
                    wrapped_param(
                        "rate", pid, rate.value, rate.lower, rate.upper, estimate=rate.estimate
                    )
                ],
            )

        if ctype in (ClockModelType.UCLN, ClockModelType.UCE):
            mean = clock_model.ucld_mean or RealParameter(value=1.0, lower=0.0)
            if ctype == ClockModelType.UCE and clock_model.exponential_mean is not None:
                mean = clock_model.exponential_mean
            mean_id = param_id(prefix, "ucld.mean")
            aliases = list(self._clock_aliases(prefix, "clock_rate"))
            aliases.extend([param_id(prefix, "ucld.mean")])
            if ctype == ClockModelType.UCE:
                aliases.append(param_id(prefix, "exponential_mean"))
            self.params.register(
                mean_id,
                mean.estimate,
                mean.lower,
                mean.upper,
                aliases=aliases,
            )
            if mean.estimate:
                self._clock_rate_ids.add(mean_id)
            categories_id = param_id(prefix, "branchRates.categories")
            self.params.register(categories_id, True, lower=0.0, upper=None, kind=CATEGORY)
            if ctype == ClockModelType.UCLN:
                stdev = clock_model.ucld_stdev or RealParameter(value=1.0 / 3.0, lower=0.0)
                stdev_id = param_id(prefix, "ucld.stdev")
                self.params.register(
                    stdev_id,
                    stdev.estimate,
                    stdev.lower,
                    stdev.upper,
                    aliases=(param_id(prefix, "ucld.stdev"),),
                )
                dist = el(
                    "logNormalDistributionModel",
                    attrib={"meanInRealSpace": "true"},
                    children=[
                        wrapped_param(
                            "mean",
                            mean_id,
                            mean.value,
                            mean.lower,
                            mean.upper,
                            estimate=mean.estimate,
                        ),
                        wrapped_param(
                            "stdev",
                            stdev_id,
                            stdev.value,
                            stdev.lower,
                            stdev.upper,
                            estimate=stdev.estimate,
                        ),
                    ],
                )
            else:
                dist = el(
                    "exponentialDistributionModel",
                    children=[
                        wrapped_param(
                            "mean",
                            mean_id,
                            mean.value,
                            mean.lower,
                            mean.upper,
                            estimate=mean.estimate,
                        )
                    ],
                )
            return el(
                "discretizedBranchRates",
                attrib={"id": clock_id},
                children=[
                    el("treeModel", attrib={"idref": "treeModel"}),
                    el("distribution", children=[dist]),
                    el(
                        "rateCategories",
                        children=[
                            # Explicitly sized to the branch count of a
                            # rooted binary tree and started on a valid index.
                            param_node(
                                categories_id,
                                value=1,
                                lower=0.0,
                                estimate=True,
                                dimension=max(2, 2 * self._n_taxa - 2),
                            )
                        ],
                    ),
                ],
            )

        # Random local clock
        rate = (
            clock_model.clock_rate or clock_model.ucld_mean or RealParameter(value=1.0, lower=0.0)
        )
        rates_id = param_id(prefix, "rlc.relativeRates")
        indicator_id = param_id(prefix, "rlc.indicators")
        clock_rate_id = param_id(prefix, "clock.rate")
        branches = max(2, 2 * self._n_taxa - 2)
        self.params.register(
            rates_id,
            True,
            lower=0.0,
            upper=None,
            aliases=(param_id(prefix, "rlc.relativeRates"), param_id(prefix, "rlc_rates")),
        )
        self.params.register(
            indicator_id,
            True,
            lower=0.0,
            upper=1.0,
            kind=BINARY,
            aliases=(param_id(prefix, "rlc.indicators"),),
        )
        self.params.register(
            clock_rate_id,
            rate.estimate,
            rate.lower,
            rate.upper,
            aliases=self._clock_aliases(prefix, "clock_rate"),
        )
        if rate.estimate:
            self._clock_rate_ids.add(clock_rate_id)
        return el(
            "randomLocalClockModel",
            attrib={"id": clock_id, "ratesAreMultipliers": "false"},
            children=[
                el("treeModel", attrib={"idref": "treeModel"}),
                el(
                    "rates",
                    children=[
                        param_node(rates_id, 1.0, lower=0.0, estimate=True, dimension=branches)
                    ],
                ),
                el(
                    "rateIndicator",
                    children=[
                        param_node(
                            indicator_id,
                            0.0,
                            lower=0.0,
                            upper=1.0,
                            estimate=True,
                            dimension=branches,
                        )
                    ],
                ),
                el(
                    "clockRate",
                    children=[
                        param_node(
                            clock_rate_id,
                            rate.value,
                            rate.lower,
                            rate.upper,
                            estimate=rate.estimate,
                        )
                    ],
                ),
            ],
        )

    def _clock_aliases(self, prefix: str, legacy: str) -> Tuple[str, ...]:
        if prefix:
            return (f"{prefix}clock_rate", f"{prefix}clock.rate")
        return ("clock_rate", "clock.rate", legacy)

    def _clock_ref_tag(self, ctype: ClockModelType) -> str:
        if ctype == ClockModelType.STRICT:
            return "strictClockBranchRates"
        if ctype in (ClockModelType.UCLN, ClockModelType.UCE):
            return "discretizedBranchRates"
        return "randomLocalClockModel"

    def _relaxed_clock_statistics(self, clock_model: Any, prefix: str) -> List[StrTree]:
        if clock_model.type == ClockModelType.STRICT:
            return []
        tag = self._clock_ref_tag(clock_model.type)
        clock_id = param_id(prefix, "branchRates")
        stats = []
        for stat_id, name, mode in (
            (param_id(prefix, "meanRate"), "meanRate", "mean"),
            (
                param_id(prefix, "coefficientOfVariation"),
                "coefficientOfVariation",
                "coefficientOfVariation",
            ),
        ):
            stats.append(
                el(
                    "rateStatistic",
                    attrib={
                        "id": stat_id,
                        "name": name,
                        "mode": mode,
                        "internal": "true",
                        "external": "true",
                    },
                    children=[
                        el("treeModel", attrib={"idref": "treeModel"}),
                        el(tag, attrib={"idref": clock_id}),
                    ],
                )
            )
        return stats

    # ------------------------------------------------------------------
    # tree prior likelihood (speciation / coalescent)
    # ------------------------------------------------------------------
    def _tree_prior_model_idref(self) -> StrTree:
        tp = self.config.tree_prior
        table = {
            TreePriorType.YULE: ("yuleModel", "yule"),
            TreePriorType.BIRTH_DEATH: ("birthDeathModel", "birthDeath"),
            TreePriorType.COALESCENT_CONSTANT: ("constantSize", "constant"),
            TreePriorType.COALESCENT_EXPONENTIAL: ("exponentialGrowth", "expGrowth"),
            TreePriorType.COALESCENT_LOGISTIC: ("logisticGrowth", "logistic"),
            TreePriorType.COALESCENT_EXPANSION: ("expansion", "expansion"),
        }
        element, obj_id = table[tp.type]
        return el(element, attrib={"idref": obj_id})

    def _build_tree_prior_likelihood(self) -> StrTree:
        tp = self.config.tree_prior
        if tp.type in (TreePriorType.YULE, TreePriorType.BIRTH_DEATH):
            node = el(
                "speciationLikelihood",
                attrib={"id": "speciation"},
                children=[
                    el("model", children=[self._tree_prior_model_idref()]),
                    el("speciesTree", children=[el("treeModel", attrib={"idref": "treeModel"})]),
                ],
            )
        elif tp.type == TreePriorType.SKYLINE:
            # The skyline is a likelihood object referenced directly from
            # <prior>; there is no coalescentLikelihood wrapper.
            return el("generalizedSkyLineLikelihood", attrib={"idref": "skyline"})
        elif tp.type == TreePriorType.EBSP:
            node = el(
                "coalescentLikelihood",
                attrib={"id": "coalescent"},
                children=[
                    el(
                        "model",
                        children=[el("variableDemographic", attrib={"idref": "demographic"})],
                    )
                ],
            )
        else:
            node = el(
                "coalescentLikelihood",
                attrib={"id": "coalescent"},
                children=[
                    el("model", children=[self._tree_prior_model_idref()]),
                    el(
                        "populationTree",
                        children=[el("treeModel", attrib={"idref": "treeModel"})],
                    ),
                ],
            )
        # The tree prior likelihood is the prior of the tree heights (:
        # no extra oneOnX on rootHeight / nodeHeights).
        for pid in (
            "treeModel.rootHeight",
            "treeModel.internalNodeHeights",
            "treeModel.allInternalNodeHeights",
        ):
            self.params.claim_prior(pid)
        return node

    # ------------------------------------------------------------------
    # Partition models (substitution + site + treeLikelihood)
    # ------------------------------------------------------------------
    def _build_partition_models(self) -> List[StrTree]:
        fragments: List[StrTree] = []
        for index, p in enumerate(self.config.partitions):
            prefix = "" if index == 0 else f"{p.id}."
            fragments.append(self._substitution_fragment(p, prefix))
            fragments.append(self._site_model_fragment(p, prefix))
            fragments.append(self._tree_likelihood_fragment(p, prefix))
        return fragments

    def _substitution_model_key(self, p: Partition) -> str:
        return str(p.site_model.substitution_model.get("type", "hky"))

    def _substitution_model_id(self, p: Partition, prefix: str) -> str:
        key = self._substitution_model_key(p)
        if key in ("wag", "jtt", "dayhoff", "blosum62", "cprev", "mtrev"):
            return param_id(prefix, "aaModel")
        if key in ("jc69", "hky"):
            return param_id(prefix, "hky")
        return param_id(prefix, key)

    def _substitution_fragment(self, p: Partition, prefix: str) -> StrTree:
        subst = p.site_model.substitution_model
        key = self._substitution_model_key(p)
        spec = self.registry.substitution_spec(key)
        model_id = self._substitution_model_id(p, prefix)

        if key in ("wag", "jtt", "dayhoff", "blosum62", "cprev", "mtrev"):
            # Empirical amino-acid models carry their own frequencies.
            return el(spec.element, attrib={**spec.attributes, "id": model_id})

        children: List[StrTree] = [self._frequencies_fragment(p, prefix, key)]

        if key in ("jc69", "hky"):
            kappa = subst.get("kappa") or RealParameter(value=2.0, lower=0.0, upper=100.0)
            kappa_id = param_id(prefix, "kappa")
            self.params.register(
                kappa_id,
                kappa.estimate,
                kappa.lower,
                kappa.upper,
                aliases=(param_id(prefix, "kappa"),),
            )
            children.append(
                wrapped_param(
                    "kappa",
                    kappa_id,
                    kappa.value,
                    kappa.lower,
                    kappa.upper,
                    estimate=kappa.estimate,
                )
            )
        elif key == "tn93":
            for kkey in ("kappa1", "kappa2"):
                param = subst.get(kkey) or RealParameter(value=1.0, lower=0.0, upper=100.0)
                pid = param_id(prefix, kkey)
                self.params.register(
                    pid,
                    param.estimate,
                    param.lower,
                    param.upper,
                    aliases=(pid,),
                )
                children.append(
                    wrapped_param(
                        kkey, pid, param.value, param.lower, param.upper, estimate=param.estimate
                    )
                )
        elif key == "gtr":
            rate_params = []
            rates_raw = subst.get("rates")
            if isinstance(rates_raw, dict) and rates_raw.get("params"):
                rate_params = list(rates_raw["params"])
            while len(rate_params) < 6:
                rate_params.append(RealParameter(value=1.0, lower=0.0, upper=100.0))
            #: five rateXX elements; rateGT is the fixed base (=1).
            for name, rp in zip(GTR_RATE_NAMES, rate_params):
                if name == GTR_BASE_RATE:
                    continue
                pid = param_id(prefix, name)
                self.params.register(
                    pid,
                    rp.estimate,
                    rp.lower,
                    rp.upper,
                    aliases=(pid, param_id(prefix, "rates")),
                )
                children.append(
                    wrapped_param(name, pid, rp.value, rp.lower, rp.upper, estimate=rp.estimate)
                )
        return el(spec.element, attrib={"id": model_id}, children=children)

    def _frequencies_fragment(self, p: Partition, prefix: str, key: str) -> StrTree:
        subst = p.site_model.substitution_model
        freq_raw = subst.get("frequencies")
        mode = "uniform" if key == "jc69" else "estimated"
        if isinstance(freq_raw, dict):
            mode = str(freq_raw.get("mode", mode))

        freq_id = param_id(prefix, "frequencies")
        model_children: List[StrTree] = []
        estimate = False
        value: Any = "0.25 0.25 0.25 0.25"
        dimension: Optional[int] = None

        if mode == "uniform":
            value = "0.25 0.25 0.25 0.25"
        elif mode == "empirical":
            value = self._empirical_frequencies(p)
        elif mode == "fixed":
            raw_value = freq_raw.get("value") if isinstance(freq_raw, dict) else None
            value = raw_value if raw_value is not None else "0.25 0.25 0.25 0.25"
        else:  # estimated
            estimate = True
            raw_value = freq_raw.get("value") if isinstance(freq_raw, dict) else None
            value = raw_value if raw_value is not None else "0.25 0.25 0.25 0.25"
            dimension = 4

        if isinstance(freq_raw, dict) and freq_raw.get("dimension"):
            dimension = int(freq_raw["dimension"])

        kind = SIMPLEX
        self.params.register(
            freq_id,
            estimate,
            lower=0.0,
            upper=1.0,
            kind=kind,
            aliases=(freq_id,),
        )
        model_children.append(
            el(
                "frequencies",
                children=[param_node(freq_id, value, dimension=dimension, estimate=estimate)],
            )
        )
        return el(
            "frequencies",
            children=[
                el("frequencyModel", attrib={"dataType": "nucleotide"}, children=model_children)
            ],
        )

    def _empirical_frequencies(self, p: Partition) -> str:
        counts = [0.0] * 4
        from_pos, to_pos, full_length = self._partition_range(p)
        alignment = self._alignment_sources.get(p.alignment.id, p.alignment)
        start = from_pos - 1
        end = to_pos
        for seq in alignment.sequences:
            for ch in seq.sequence[start:end].upper():
                idx = "ACGT".find(ch)
                if idx >= 0:
                    counts[idx] += 1
        total = sum(counts) or 1.0
        values = [c / total for c in counts]
        # BEAST rejects frequency vectors that do not sum to 1 within its
        # tolerance; format with enough precision and put the rounding
        # residual into the largest component.
        values = [round(v, 12) for v in values]
        residual = 1.0 - sum(values)
        pivot = max(range(len(values)), key=lambda i: values[i])
        values[pivot] = round(values[pivot] + residual, 12)
        return " ".join(f"{v:.12g}" for v in values)

    def _site_model_fragment(self, p: Partition, prefix: str) -> StrTree:
        sm = p.site_model
        children: List[StrTree] = [
            el(
                "substitutionModel",
                children=[
                    el(
                        self.registry.substitution_spec(self._substitution_model_key(p)).element,
                        attrib={"idref": self._substitution_model_id(p, prefix)},
                    )
                ],
            )
        ]
        if sm.gamma_categories and sm.gamma_categories > 1:
            alpha = sm.gamma_shape or RealParameter(value=0.5, lower=0.0, upper=100.0)
            alpha_id = param_id(prefix, "siteModel.alpha")
            self.params.register(
                alpha_id,
                alpha.estimate,
                alpha.lower,
                alpha.upper,
                aliases=(alpha_id,),
            )
            children.append(
                el(
                    "gammaShape",
                    attrib={"gammaCategories": str(sm.gamma_categories)},
                    children=[
                        param_node(
                            alpha_id, alpha.value, alpha.lower, alpha.upper, estimate=alpha.estimate
                        )
                    ],
                )
            )
        if sm.proportion_invariant is not None:
            pinv = sm.proportion_invariant
            pinv_id = param_id(prefix, "siteModel.pInv")
            self.params.register(
                pinv_id,
                pinv.estimate,
                pinv.lower,
                pinv.upper,
                aliases=(pinv_id,),
            )
            children.append(
                el(
                    "proportionInvariant",
                    children=[
                        param_node(
                            pinv_id, pinv.value, pinv.lower, pinv.upper, estimate=pinv.estimate
                        )
                    ],
                )
            )
        return el("siteModel", attrib={"id": param_id(prefix, "siteModel")}, children=children)

    def _tree_likelihood_fragment(self, p: Partition, prefix: str) -> StrTree:
        clock_prefix = "" if p.clock == "shared" else f"{p.id}."
        children: List[StrTree] = [
            el("patterns", attrib={"idref": self._patterns_id(p)}),
            el("treeModel", attrib={"idref": "treeModel"}),
            el("siteModel", attrib={"idref": param_id(prefix, "siteModel")}),
            el(
                self._clock_ref_tag(p.clock_model.type),
                attrib={"idref": param_id(clock_prefix, "branchRates")},
            ),
        ]
        # BEAST 1: do not set useAmbiguities=false here.  When it is false the
        # pure-Java fallback likelihood core (GeneralBeagleImpl) is asked for
        # site-specific log likelihoods, which it does not implement, so the
        # document fails to instantiate on machines without native BEAGLE.
        return el(
            "treeLikelihood", attrib={"id": param_id(prefix, "treeLikelihood")}, children=children
        )

    # ------------------------------------------------------------------
    # Calibration statistics (incl. root calibrations)
    # ------------------------------------------------------------------
    def _build_calibration_statistics(self) -> List[StrTree]:
        from .calibration import (
            monophyly_fragment,
            provenance_comment,
            tmrca_fragment,
        )

        fragments: List[StrTree] = []
        for cal in self.config.calibrations:
            comment = provenance_comment(cal) if cal.provenance else None
            node = tmrca_fragment(cal)
            if comment:
                node["comment"] = comment
            fragments.append(node)
            if cal.monophyletic and cal.taxa:
                fragments.append(monophyly_fragment(cal))
        return fragments

    # ------------------------------------------------------------------
    # Calibration hyperpriors
    # ------------------------------------------------------------------
    def _build_hyperprior_parameters(self) -> List[StrTree]:
        """Declare estimated parameters for calibration hyperpriors.

        A hyperprior is only emitted when BEAST can actually parameterize the
        distribution argument (``HYPERPRIOR_EMITTABLE_TARGETS``): the
        calibration prior becomes a ``<distributionLikelihood>`` whose
        ``*DistributionModel`` references these parameters instead of literal
        numbers.  Unsupported combinations are rejected at Gate 0, never
        dropped silently.
        """
        from .calibration import (
            hyperprior_operator,
            hyperprior_parameter_bounds,
            hyperprior_parameter_id,
        )

        fragments: List[StrTree] = []
        for cal in self.config.calibrations:
            if not cal.hyperpriors:
                continue
            if cal.distribution is None:
                raise ModelAssemblyError(
                    f"calibration {cal.name!r} declares hyperpriors but has no " "distribution"
                )
            kind = str(cal.distribution.type)
            supported = HYPERPRIOR_EMITTABLE_TARGETS.get(kind, ())
            mapping: Dict[str, str] = {}
            for target, hyper in cal.hyperpriors.items():
                if target not in supported:
                    raise ModelAssemblyError(
                        f"calibration {cal.name!r}: hyperprior on {kind}.{target} "
                        "cannot be expressed in BEAST 1; remove it or provide an "
                        "explicit parameter prior"
                    )
                params = cal.distribution.parameters or {}
                if target not in params:
                    raise ModelAssemblyError(
                        f"calibration {cal.name!r}: hyperprior target {target!r} "
                        f"is not a parameter of distribution {kind!r}"
                    )
                try:
                    value = float(params[target])
                except (TypeError, ValueError) as exc:
                    raise ModelAssemblyError(
                        f"calibration {cal.name!r}: distribution parameter "
                        f"{target!r} is not numeric ({params[target]!r})"
                    ) from exc
                pid = hyperprior_parameter_id(cal, target)
                lower, upper = hyperprior_parameter_bounds(cal, target)
                self.params.register(pid, True, lower=lower, upper=upper)
                self._hyperprior_priors[pid] = hyper
                self._hyperprior_operators[pid] = hyperprior_operator(cal, target)
                mapping[target] = pid
                fragments.append(param_node(pid, value, lower, upper, estimate=True))
            self._hyperprior_parameter_ids[cal.name] = mapping
        return fragments

    # ------------------------------------------------------------------
    # Operators
    # ------------------------------------------------------------------
    def _build_operators(self) -> StrTree:
        operators: List[StrTree] = []
        for pid, info in self.params.estimated.items():
            node = self._operator_for(info)
            if node is not None:
                operators.append(node)
                self.params.claim_operator(pid)
        operators.extend(self._tree_operators())
        self._apply_operator_weights(operators)
        return el("operators", attrib={"id": "operators"}, children=operators)

    @staticmethod
    def _operator_parameter_refs(node: StrTree) -> List[str]:
        """All ``<parameter idref="..."/>`` targets inside an operator."""
        found: List[str] = []
        stack: List[Any] = [node]
        while stack:
            current = stack.pop()
            if not isinstance(current, dict):
                continue
            attrib = current.get("attrib") or {}
            if current.get("tag") == "parameter" and "idref" in attrib:
                found.append(str(attrib["idref"]))
            stack.extend(current.get("children") or [])
        return sorted(set(found))

    def _apply_operator_weights(self, operators: Sequence[StrTree]) -> None:
        """Apply ``mcmc.operator_weights`` to the emitted ``<operators>``.

        Targets:

        * an exact emitted parameter id (``clock.rate``, ``gene2.ucld.mean``)
          or a registered alias (``ucld_mean``); the parameter must be moved by
          exactly one operator, otherwise the target is ambiguous;
        * an operator tag (``scaleOperator``) that occurs exactly once.

        Anything else raises :class:`ModelAssemblyError`; weights are never
        silently ignored.
        """
        weights = dict(getattr(self.config.mcmc, "operator_weights", {}) or {})
        if not weights:
            return
        by_param: Dict[str, List[StrTree]] = {}
        by_tag: Dict[str, List[StrTree]] = {}
        for operator in operators:
            tag = str(operator.get("tag"))
            by_tag.setdefault(tag, []).append(operator)
            for pid in self._operator_parameter_refs(operator):
                by_param.setdefault(pid, []).append(operator)

        unique_tags = sorted(tag for tag, nodes in by_tag.items() if len(nodes) == 1)
        for target, weight in weights.items():
            matches = by_param.get(target)
            if matches is None:
                tag_matches = by_tag.get(target)
                if tag_matches is not None:
                    if len(tag_matches) != 1:
                        details = []
                        for node in tag_matches:
                            operator_id = str((node.get("attrib") or {}).get("id", "<no id>"))
                            refs = self._operator_parameter_refs(node)
                            details.append(
                                f"{operator_id} -> {', '.join(refs) if refs else '<no parameter>'}"
                            )
                        raise ModelAssemblyError(
                            f"mcmc.operator_weights target {target!r} is an "
                            f"ambiguous operator tag: {len(tag_matches)} "
                            f"<{target}> operators exist ({'; '.join(details)}); "
                            "target the parameter id instead"
                        )
                    matches = tag_matches
            if not matches:
                raise ModelAssemblyError(
                    f"mcmc.operator_weights target {target!r} does not match any "
                    "emitted parameter or operator tag; parameter targets: "
                    f"{', '.join(sorted(by_param)) or 'none'}; unique operator "
                    f"tags: {', '.join(unique_tags) or 'none'}"
                )
            if len(matches) > 1:
                tags = ", ".join(sorted({str(node.get("tag")) for node in matches}))
                raise ModelAssemblyError(
                    f"mcmc.operator_weights target {target!r} is moved by "
                    f"{len(matches)} operators ({tags}); target a unique operator "
                    "tag instead"
                )
            matches[0].setdefault("attrib", {})["weight"] = format_param_value(weight)

    def _operator_for(self, info: _ParamInfo) -> Optional[StrTree]:
        ref = [el("parameter", attrib={"idref": info.id})]
        hyper = self._hyperprior_operators.get(info.id)
        if hyper is not None:
            tag, attrs = hyper
            return el(tag, attrib=dict(attrs), children=ref)
        if info.kind == BINARY:
            return el("bitFlipOperator", attrib={"weight": "15"}, children=ref)
        if info.kind == CATEGORY:
            return el(
                "swapOperator",
                attrib={"size": "1", "autoOptimize": "false", "weight": "10"},
                children=ref,
            )
        if info.kind == INTEGER:
            return el(
                "deltaExchange",
                attrib={"delta": "1", "integer": "true", "weight": "6", "autoOptimize": "false"},
                children=ref,
            )
        if info.kind == SIMPLEX:
            return el("deltaExchange", attrib={"delta": "0.01", "weight": "1"}, children=ref)
        if info.kind == HEIGHT:
            return None
        # BEAST 1.10.4 gives these parameters finite internal bounds
        # (RandomLocalClockModel: rates in [0, MAX_VALUE];
        # VariableDemographicModel: popSize in [-MAX_VALUE, MAX_VALUE]), so a
        # scale operator is rejected there even though the XML has no upper.
        if info.id.endswith((".relativeRates", "demographic.popSize")):
            return el(
                "randomWalkOperator",
                attrib={"windowSize": "0.5", "weight": "1"},
                children=ref,
            )
        if info.doubly_bounded:
            span = float(info.upper) - float(info.lower)  # type: ignore[arg-type]
            window = span / 100.0 if span > 0 else 1.0
            return el(
                "randomWalkOperator",
                attrib={"windowSize": format_param_value(max(window, 1e-6)), "weight": "1"},
                children=ref,
            )
        # A scale operator is only legal when the initial value cannot cross
        # zero: BEAST requires a non-negative lower (or non-positive upper)
        # *and* an infinite bound on the acting side.
        if info.lower is None or info.lower < 0.0:
            if info.upper is None or info.upper > 0.0:
                return el(
                    "randomWalkOperator",
                    attrib={"windowSize": "0.5", "weight": "1"},
                    children=ref,
                )
        return el("scaleOperator", attrib={"scaleFactor": "0.75", "weight": "1"}, children=ref)

    def _tree_operators(self) -> List[StrTree]:
        ops: List[StrTree] = []
        tree_ref = lambda: el("treeModel", attrib={"idref": "treeModel"})  # noqa: E731
        for tag, weight in (
            ("subtreeSlide", 15),
            ("narrowExchange", 15),
            ("wideExchange", 3),
            ("wilsonBalding", 3),
        ):
            attrib: Dict[str, Any] = {"weight": str(weight)}
            if tag == "subtreeSlide":
                attrib.update({"size": "1.7", "gaussian": "true"})
            ops.append(el(tag, attrib=attrib, children=[tree_ref()]))
        ops.append(
            el(
                "scaleOperator",
                attrib={"scaleFactor": "0.75", "weight": "3"},
                children=[el("parameter", attrib={"idref": "treeModel.rootHeight"})],
            )
        )
        ops.append(
            el(
                "uniformOperator",
                attrib={"weight": "30"},
                children=[el("parameter", attrib={"idref": "treeModel.internalNodeHeights"})],
            )
        )
        up = [el("parameter", attrib={"idref": pid}) for pid in sorted(self._clock_rate_ids)]
        ops.append(
            el(
                "upDownOperator",
                attrib={"scaleFactor": "0.75", "weight": "3"},
                children=[
                    el("up", children=up),
                    el(
                        "down",
                        children=[
                            el("parameter", attrib={"idref": "treeModel.allInternalNodeHeights"})
                        ],
                    ),
                ],
            )
        )
        for pid in (
            "treeModel.rootHeight",
            "treeModel.internalNodeHeights",
            "treeModel.allInternalNodeHeights",
        ):
            self.params.claim_operator(pid)
        return ops

    # ------------------------------------------------------------------
    # MCMC / prior / likelihood / loggers
    # ------------------------------------------------------------------
    def _build_mcmc(self) -> StrTree:
        self._validate_explicit_priors()
        prior_children: List[StrTree] = []

        from .calibration import (
            hyperprior_prior_fragment,
            monophyly_id,
            prior_fragment,
        )

        for cal in self.config.calibrations:
            prior_children.append(
                prior_fragment(
                    cal,
                    self.registry,
                    self._hyperprior_parameter_ids.get(cal.name, {}),
                )
            )
            if cal.monophyletic and cal.taxa:
                prior_children.append(
                    el(
                        "booleanLikelihood",
                        children=[
                            el("monophylyStatistic", attrib={"idref": monophyly_id(cal.name)})
                        ],
                    )
                )

        for pid, hyper in self._hyperprior_priors.items():
            prior_children.append(hyperprior_prior_fragment(hyper, pid, self.registry))
            self.params.claim_prior(pid)

        for pid, info in self.params.estimated.items():
            if pid in self._hyperprior_priors:
                continue
            node = self._parameter_prior(info)
            if node is not None:
                prior_children.append(node)

        prior_children.extend(self._tree_prior_prior_children())

        prior_node = el("prior", attrib={"id": "prior"}, children=prior_children)

        likelihood_children: List[StrTree] = []
        for index, p in enumerate(self.config.partitions):
            prefix = "" if index == 0 else f"{p.id}."
            likelihood_children.append(
                el("treeLikelihood", attrib={"idref": param_id(prefix, "treeLikelihood")})
            )

        posterior_children = [
            prior_node,
            el("likelihood", attrib={"id": "likelihood"}, children=likelihood_children),
        ]
        mcmc_attrib = {
            "id": "mcmc",
            "chainLength": str(self.config.mcmc.chain_length),
            "autoOptimize": ("true" if self.config.mcmc.auto_optimize else "false"),
        }
        # BEAST's MCMCParser reads preBurnin as a chain length offset.
        # Only emit it when configured so default documents stay unchanged.
        if getattr(self.config.mcmc, "pre_burnin", 0):
            mcmc_attrib["preBurnin"] = str(self.config.mcmc.pre_burnin)
        return el(
            "mcmc",
            attrib=mcmc_attrib,
            children=[
                el("posterior", attrib={"id": "posterior"}, children=posterior_children),
                el("operators", attrib={"idref": "operators"}),
            ]
            + self._build_loggers(),
        )

    def _tree_prior_prior_children(self) -> List[StrTree]:
        tp = self.config.tree_prior
        if tp.type in (TreePriorType.YULE, TreePriorType.BIRTH_DEATH):
            return [el("speciationLikelihood", attrib={"idref": "speciation"})]
        if tp.type == TreePriorType.SKYLINE:
            return [el("generalizedSkyLineLikelihood", attrib={"idref": "skyline"})]
        if tp.type == TreePriorType.EBSP:
            return [
                el(
                    "poissonPrior",
                    attrib={"mean": "0.6931471805599453", "offset": "0.0"},
                    children=[
                        el(
                            "statistic",
                            attrib={"idref": "demographic.populationSizeChanges"},
                        )
                    ],
                ),
                el("coalescentLikelihood", attrib={"idref": "coalescent"}),
                el(
                    "mixedDistributionLikelihood",
                    children=[
                        el(
                            "distribution0",
                            children=[
                                el(
                                    "exponentialDistributionModel",
                                    attrib={"idref": "demographic.populationMeanDist"},
                                )
                            ],
                        ),
                        el(
                            "distribution1",
                            children=[
                                el(
                                    "exponentialDistributionModel",
                                    attrib={"idref": "demographic.populationMeanDist"},
                                )
                            ],
                        ),
                        el(
                            "data",
                            children=[el("parameter", attrib={"idref": "demographic.popSize"})],
                        ),
                        el(
                            "indicators",
                            children=[el("parameter", attrib={"idref": "demographic.indicators"})],
                        ),
                    ],
                ),
            ]
        return [el("coalescentLikelihood", attrib={"idref": "coalescent"})]

    # -- explicit parameter priors ---------------------------
    def _explicit_prior_candidates(self, target: Any) -> List[str]:
        """Emitted parameter ids matching an explicit-prior target.

        Besides exact ids and aliases this accepts the natural per-partition
        spelling ``gene1.kappa`` / ``gene2.kappa``.  The first partition emits
        its site-model parameters without a prefix, so the partition qualifier
        is stripped before lookup.
        """
        candidates = self.params.candidates_for(target)
        if candidates or not isinstance(target, str) or "." not in target:
            return candidates
        head, rest = target.split(".", 1)
        partition_ids = [p.id for p in self.config.partitions]
        if head not in partition_ids:
            return candidates
        if head == partition_ids[0]:
            candidates = self.params.candidates_for(rest)
        if not candidates:
            candidates = self.params.candidates_for(f"{head}.{rest}")
        return candidates

    def _validate_explicit_priors(self) -> None:
        """Resolve every ``parameter_priors`` target against emitted ids."""
        for index, entry in enumerate(getattr(self.config, "parameter_priors", []) or []):
            target = entry.get("parameter")
            if entry.get("distribution") is None:
                raise ModelAssemblyError(
                    f"parameter_priors[{index}] ({target!r}) has no distribution"
                )
            candidates = self._explicit_prior_candidates(target)
            if not candidates:
                raise ModelAssemblyError(
                    f"parameter_priors[{index}] targets {target!r}, which does not "
                    "match any parameter emitted by this configuration; valid "
                    f"targets include: {', '.join(sorted(self.params.info))}"
                )
            estimated = [
                pid
                for pid in candidates
                if pid in self.params.estimated
                and self.params.estimated[pid].kind in (CONTINUOUS, SIMPLEX)
            ]
            if not estimated:
                raise ModelAssemblyError(
                    f"parameter_priors[{index}] targets {target!r}, which resolves "
                    f"only to fixed or discrete parameters ({', '.join(candidates)}); "
                    "a continuous prior cannot be applied there"
                )
            if len(estimated) > 1:
                raise ModelAssemblyError(
                    f"parameter_priors[{index}] target {target!r} is ambiguous: it "
                    f"matches {', '.join(estimated)}; use the full parameter id"
                )

    def _parameter_prior(self, info: _ParamInfo) -> Optional[StrTree]:
        pid = info.id
        explicit = self._find_explicit_prior(pid)
        if explicit is not None:
            self.params.claim_prior(pid)
            return explicit
        if info.kind == HEIGHT:
            # Tree heights are constrained by the tree prior likelihood and the
            # calibration priors only.
            self.params.claim_prior(pid)
            return None
        if info.kind in (BINARY, CATEGORY, INTEGER):
            # Integer/category/indicator parameters get no continuous prior
            # .
            return None
        node = self._auto_prior(info)
        description = describe_distribution(node)
        source = self._auto_prior_source(pid)
        node["comment"] = (
            f"BXP auto prior ({source}): "
            f"parameter={pid}; distribution={description}; "
            "override under parameter_priors"
        )
        self.params.auto_priors[pid] = {"distribution": description, "source": source}
        self.params.claim_prior(pid)
        return node

    @staticmethod
    def _auto_prior_source(pid: str) -> str:
        """Provenance label for an automatic prior (never a blanket claim).

        Only the subset verified against ``dr/app/beauti/options`` is called a
        BEAUti convention; the other branches are explicit BeastXPy choices.
        """
        if (
            pid.endswith("birthRate")
            or pid.endswith("bMinusD")
            or pid.endswith("relativeDeathRate")
            or pid.endswith(".stdev")
            or pid.endswith("ucld.stdev")
            or pid.endswith("kappa")
            or pid.endswith("kappa1")
            or pid.endswith("kappa2")
        ):
            return "BEAUti-1.10.4 convention"
        if pid.endswith("siteModel.pInv") or any(pid.endswith(name) for name in GTR_EMITTED_RATES):
            return "BEAST parameter-bound convention"
        return "BeastXPy convention"

    def _auto_prior(self, info: _ParamInfo) -> StrTree:
        pid = info.id
        ref = [el("parameter", attrib={"idref": pid})]

        def uniform(lower: float, upper: float) -> StrTree:
            return el(
                "uniformPrior",
                attrib={"lower": format_param_value(lower), "upper": format_param_value(upper)},
                children=ref,
            )

        def lognormal(mean: float, stdev: float) -> StrTree:
            return el(
                "logNormalPrior",
                attrib={
                    "mean": format_param_value(mean),
                    "stdev": format_param_value(stdev),
                    "offset": "0.0",
                    "meanInRealSpace": "false",
                },
                children=ref,
            )

        if info.kind == SIMPLEX:
            return uniform(0.0, 1.0)
        if pid.endswith(".stdev") or pid.endswith("ucld.stdev"):
            return el("exponentialPrior", attrib={"mean": "0.3333333333333333"}, children=ref)
        if pid.endswith("relativeDeathRate"):
            return uniform(0.0, 1.0)
        if pid.endswith("siteModel.pInv"):
            return uniform(
                0.0 if info.lower is None else float(info.lower),
                1.0 if info.upper is None else float(info.upper),
            )
        if any(pid.endswith(name) for name in GTR_EMITTED_RATES):
            return uniform(
                0.0 if info.lower is None else float(info.lower),
                1e8 if info.upper is None else float(info.upper),
            )
        if pid.endswith("rlc.relativeRates"):
            return el(
                "gammaPrior",
                attrib={"shape": "0.5", "scale": "2.0", "offset": "0.0"},
                children=ref,
            )
        if pid.endswith("populationMean"):
            return el("oneOnXPrior", children=ref)
        if pid in self._clock_rate_ids or pid.endswith("clock.rate") or pid.endswith("ucld.mean"):
            return el("oneOnXPrior", children=ref)
        if pid.endswith("birthRate") or pid.endswith("bMinusD"):
            return lognormal(2.0, 1.5)
        if pid.endswith("growthRate"):
            return el("normalPrior", attrib={"mean": "0.0", "stdev": "1.0"}, children=ref)
        if pid.endswith("popSize"):
            return uniform(0.0, 1e8)
        if pid.endswith("alpha"):
            return lognormal(1.0, 1.25)
        if pid.endswith("kappa") or pid.endswith("kappa1") or pid.endswith("kappa2"):
            return lognormal(1.0, 1.25)
        if info.doubly_bounded:
            return uniform(float(info.lower), float(info.upper))  # type: ignore[arg-type]
        if info.lower is not None and info.upper is None:
            return el("oneOnXPrior", children=ref)
        return lognormal(1.0, 1.25)

    def _find_explicit_prior(self, pid: str) -> Optional[StrTree]:
        for index, entry in enumerate(getattr(self.config, "parameter_priors", []) or []):
            target = entry.get("parameter")
            dist = entry.get("distribution")
            if dist is None:
                continue
            candidates = self._explicit_prior_candidates(target)
            if pid not in candidates:
                continue
            if index in self._explicit_prior_used:
                raise ModelAssemblyError(
                    f"parameter_priors[{index}] target {target!r} matches more than "
                    "one emitted parameter"
                )
            self._explicit_prior_used.add(index)
            try:
                spec = self.registry.distribution_spec(dist.type)
            except ValueError as exc:
                raise ModelAssemblyError(str(exc)) from exc
            attrs = self.registry.distribution_attrs(dist.type, dist.parameters, dist.offset)
            return el(spec.element, attrib=attrs, children=[el("parameter", attrib={"idref": pid})])
        return None

    # -- loggers ---------------------------------------
    def _build_report(self) -> StrTree:
        return el(
            "report",
            children=[
                el(
                    "property",
                    attrib={"name": "timer"},
                    children=[el("mcmc", attrib={"idref": "mcmc"})],
                )
            ],
        )

    def _log_every(self, section: Dict[str, Any], default: int, where: str) -> int:
        raw = section.get("log_every") if isinstance(section, dict) else None
        if raw is None:
            return max(1, int(default))
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ModelAssemblyError(
                f"loggers.{where}.log_every must be a positive integer (got {raw!r})"
            ) from exc
        if value < 1:
            raise ModelAssemblyError(f"loggers.{where}.log_every must be >= 1 (got {raw!r})")
        return value

    def _build_loggers(self) -> List[StrTree]:
        loggers = self.config.loggers
        trace = loggers.trace_log or {}
        tree = loggers.tree_log or {}
        screen = loggers.screen_log or {}
        name = self.config.analysis_name
        chain_length = max(1, int(self.config.mcmc.chain_length))

        screen_every = self._log_every(screen, max(1, chain_length // 100), "screen_log")
        trace_every = self._log_every(trace, max(1, chain_length // 1000), "trace_log")
        tree_every = self._log_every(tree, max(1, chain_length // 1000), "tree_log")

        trace_file = str(trace.get("file_name") or f"{name}.log")
        tree_file = str(tree.get("file_name") or f"{name}.trees")

        clock_ids = sorted(self._clock_rate_ids)
        tree_prior_columns = self._tree_prior_parameter_ids()

        screen_columns: List[StrTree] = [
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
                attrib={"label": "rootHeight", "sf": "6", "width": "12"},
                children=[el("parameter", attrib={"idref": "treeModel.rootHeight"})],
            ),
        ]
        for pid in clock_ids:
            screen_columns.append(
                el(
                    "column",
                    attrib={"label": pid, "sf": "6", "width": "12"},
                    children=[el("parameter", attrib={"idref": pid})],
                )
            )
        if self.config.tree_prior.type == TreePriorType.SKYLINE:
            screen_columns.append(
                el(
                    "column",
                    attrib={"label": "skyline", "dp": "4", "width": "12"},
                    children=[el("generalizedSkyLineLikelihood", attrib={"idref": "skyline"})],
                )
            )
        elif self.config.tree_prior.type == TreePriorType.EBSP:
            screen_columns.append(
                el(
                    "column",
                    attrib={"label": "demographic.popSize", "sf": "6", "width": "12"},
                    children=[el("parameter", attrib={"idref": "demographic.popSize"})],
                )
            )
        screen_log = el(
            "log",
            attrib={"id": "screenLog", "logEvery": str(screen_every)},
            children=screen_columns,
        )

        trace_children: List[StrTree] = [
            el("posterior", attrib={"idref": "posterior"}),
            el("prior", attrib={"idref": "prior"}),
            el("likelihood", attrib={"idref": "likelihood"}),
            el("parameter", attrib={"idref": "treeModel.rootHeight"}),
        ]
        logged: Set[str] = {"treeModel.rootHeight"}
        for pid, info in self.params.estimated.items():
            if pid.startswith("treeModel.") or pid in logged:
                continue
            logged.add(pid)
            trace_children.append(el("parameter", attrib={"idref": pid}))
        from .calibration import tmrca_id

        for cal in self.config.calibrations:
            trace_children.append(el("tmrcaStatistic", attrib={"idref": tmrca_id(cal.name)}))
        if self.config.tree_prior.type in (TreePriorType.YULE, TreePriorType.BIRTH_DEATH):
            trace_children.append(el("speciationLikelihood", attrib={"idref": "speciation"}))
        elif self.config.tree_prior.type == TreePriorType.SKYLINE:
            trace_children.append(el("generalizedSkyLineLikelihood", attrib={"idref": "skyline"}))
        else:
            trace_children.append(el("coalescentLikelihood", attrib={"idref": "coalescent"}))
        for pid in tree_prior_columns:
            if pid not in logged:
                logged.add(pid)
                trace_children.append(el("parameter", attrib={"idref": pid}))
        for stat_id in self._statistic_ids():
            ref_tag = (
                "sumStatistic"
                if stat_id.endswith("populationSizeChanges") or stat_id.endswith("rateChanges")
                else "rateStatistic"
            )
            trace_children.append(el(ref_tag, attrib={"idref": stat_id}))
        trace_log = el(
            "log",
            attrib={
                "id": "fileLog",
                "logEvery": str(trace_every),
                "fileName": trace_file,
                # `overwrite` is a documented attribute of BEAST's LogParser
                # (used by every official BEAUti example) and only affects the
                # logger's own file, not the CLI's -overwrite flag.
                "overwrite": "false",
            },
            children=trace_children,
        )

        tree_children: List[StrTree] = [el("treeModel", attrib={"idref": "treeModel"})]
        tree_children.extend(self._tree_log_rate_traits())
        tree_children.append(el("posterior", attrib={"idref": "posterior"}))
        tree_log = el(
            "logTree",
            attrib={
                "id": "treeFileLog",
                "logEvery": str(tree_every),
                "nexusFormat": "true" if tree.get("nexus_format", True) else "false",
                "fileName": tree_file,
                "sortTranslationTable": (
                    "true" if tree.get("sort_translation_table", True) else "false"
                ),
            },
            children=tree_children,
        )
        return [screen_log, trace_log, tree_log]

    def _tree_prior_parameter_ids(self) -> List[str]:
        tp = self.config.tree_prior
        if tp.type == TreePriorType.SKYLINE:
            return ["skyline.popSize", "skyline.groupSize"]
        if tp.type == TreePriorType.EBSP:
            return ["demographic.popSize", "demographic.indicators", "demographic.populationMean"]
        return []

    def _tree_log_rate_traits(self) -> List[StrTree]:
        """Branch-rate traits for ``<logTree>``.

        A single shared clock is referenced directly (its ``rate`` trait is
        logged).  With independent clocks each partition's *own* clock is
        included under a distinct tag, so a tree log never borrows another
        partition's branch rates.
        """
        entries: List[Tuple[str, str, str]] = []  # (ref_tag, id, tag)
        shared = all(p.clock == "shared" for p in self.config.partitions)
        if shared:
            p0 = self.config.partitions[0]
            entries.append((self._clock_ref_tag(p0.clock_model.type), "branchRates", "rate"))
        else:
            for p in self.config.partitions:
                entries.append(
                    (
                        self._clock_ref_tag(p.clock_model.type),
                        param_id(f"{p.id}.", "branchRates"),
                        f"rate.{p.id}",
                    )
                )
        if len(entries) == 1:
            tag, ref_id, _ = entries[0]
            return [el(tag, attrib={"idref": ref_id})]
        return [
            el(
                "trait",
                attrib={"name": "rate", "tag": tag_name},
                children=[el(ref_tag, attrib={"idref": ref_id})],
            )
            for ref_tag, ref_id, tag_name in entries
        ]

    def _statistic_ids(self) -> List[str]:
        """Ids of rate/sum statistics declared by the model objects."""
        ids: List[str] = []
        tp = self.config.tree_prior
        if tp.type == TreePriorType.EBSP:
            ids.append("demographic.populationSizeChanges")
        shared = all(p.clock == "shared" for p in self.config.partitions)
        prefixes = [""] if shared else [f"{p.id}." for p in self.config.partitions]
        for prefix in prefixes:
            if self._clock_is_relaxed(prefix):
                ids.append(param_id(prefix, "meanRate"))
                ids.append(param_id(prefix, "coefficientOfVariation"))
        return ids

    def _clock_is_relaxed(self, prefix: str) -> bool:
        shared = all(p.clock == "shared" for p in self.config.partitions)
        if shared:
            return self.config.partitions[0].clock_model.type != ClockModelType.STRICT
        for p in self.config.partitions:
            if f"{p.id}." == prefix:
                return p.clock_model.type != ClockModelType.STRICT
        return False

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _vector_spec(
        raw: Any, default_dim: Optional[int], default_value: Any
    ) -> Tuple[Optional[int], Any]:
        """Normalise an int/list/RealParameter dimension declaration.

        Returns ``(dimension or None, initial value)``.  A bare scalar is
        returned as a value with ``dimension=None`` so the caller can apply
        its documented default dimension.
        """
        if raw is None:
            return default_dim, default_value
        if isinstance(raw, RealParameter):
            dimension = raw.dimension if raw.dimension and raw.dimension > 1 else None
            return dimension, raw.value
        if isinstance(raw, bool):
            raise ModelAssemblyError(f"expected a dimension or a list of values, got {raw!r}")
        if isinstance(raw, int):
            if raw < 1:
                raise ModelAssemblyError(f"dimension must be >= 1 (got {raw})")
            return raw, default_value
        if isinstance(raw, (list, tuple)):
            if not raw:
                raise ModelAssemblyError("dimension list must not be empty")
            return len(raw), list(raw)
        if isinstance(raw, (float, str)):
            return None, raw
        raise ModelAssemblyError(
            f"unsupported dimension/value declaration {raw!r} "
            "(expected int, list, RealParameter or scalar)"
        )


def config_registry(config: BEASTConfig):
    """Return a ModelRegistry for the config (cached on the config)."""
    from .registry import ModelRegistry

    registry = getattr(config, "_registry", None)
    if registry is None:
        registry = ModelRegistry()
        config._registry = registry  # type: ignore[attr-defined]
    return registry
