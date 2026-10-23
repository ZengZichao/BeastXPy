"""Calibration assembly for BeastXPy.

Builds the BEAST 1 calibration stack for every calibration point:

* a ``<taxa>`` reference set (re-used during assembly),
* a ``<tmrcaStatistic id="tmrca(name)" includeStem=...>``,
* optionally a ``<monophylyStatistic>``,
* a distribution prior element in ``<prior>``,
* a ``constrainedTaxa`` starting-tree constraint (optionally carrying the
  calibration's ``ParametricDistributionModel`` as a child of ``<tmrca>``),
* the representative node age used to build calibration-aware starting trees
  (see :func:`calibration_start_age`),
* and an inline ``BXP-calibration:`` provenance comment.

A calibration with ``taxa=None`` is a *root* calibration: it uses the global
``<taxa id="taxa">`` set instead of a dedicated clade set.
All BEAST-visible identifiers are built by :func:`tmrca_id`,
:func:`monophyly_id` and :func:`calibration_taxa_id` so the statistics, the
priors, the starting-tree constraints and the loggers can never drift apart.

All elements are returned as plain Python dicts (a structure tree) that the
``xml_writer`` turns into real XML, so the assembler stays framework-agnostic.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    HYPERPRIOR_EMITTABLE_TARGETS,
    CalibrationPoint,
    DistributionConfig,
    HyperpriorConfig,
)
from .registry import ModelRegistry

#: BEAST ``TimeScale`` units accepted by the ``<date>`` element.  ``units`` is
#: always written, even for the default ``years`` (: BEAST X 10.5.0
#: calls ``TimeScale.unitString(null)`` and throws when it is omitted).
DATE_UNITS = ("years", "months", "days", "weeks")
DEFAULT_DATE_UNITS = "years"
DEFAULT_DATE_DIRECTION = "forwards"


def calibration_taxa_id(name: str) -> str:
    """The ``<taxa>`` id of a calibration clade."""
    return name


def tmrca_id(name: str) -> str:
    """The ``<tmrcaStatistic>`` id of a calibration point."""
    return f"tmrca({name})"


def monophyly_id(name: str) -> str:
    """The ``<monophylyStatistic>`` id of a calibration point."""
    return f"monophyly({name})"


def calibration_taxa_ref(cal: CalibrationPoint) -> str:
    """The ``<taxa>`` id a calibration refers to.

    A root calibration (``taxa`` is ``None`` or empty) uses the global
    ``taxa`` set declared by :func:`build_taxa_sets`.
    """
    if not cal.taxa:
        return "taxa"
    return calibration_taxa_id(cal.name)


def provenance_comment(cal: CalibrationPoint) -> str:
    """Render the BXP-calibration provenance comment line."""
    prov = cal.provenance
    bits = [f"name={cal.name}"]
    if prov is not None:
        bits.append(f"source={prov.source}")
        if prov.reference:
            bits.append(f"doi={prov.reference}")
        bits.append(f"type={prov.calibration_type}")
        if prov.original_age_min is not None:
            bits.append(f"original_age=[{prov.original_age_min},{prov.original_age_max}]")
    return "BXP-calibration: " + "; ".join(bits)


def calibration_tree(cal: CalibrationPoint, comment: bool = True) -> Dict[str, Any]:
    """Return the structure tree for one calibration point.

    The returned dict describes an XML fragment in the same shape used by
    ``model.py`` and consumed by ``xml_writer``:

    ``{"tag": ..., "attrib": {...}, "children": [...], "text": ...}``

    A calibration contributes:
      1. an optional ``<taxa id="name">`` set (only when it references taxa
         not covered by the global set -- the caller decides with
         ``emit_taxa_set``);
      2. the ``<tmrcaStatistic>`` block;
      3. the optional ``<monophylyStatistic>`` block;
      4. a prior fragment (distribution wrapping an idref) for the ``<prior>``;
      5. a ``constrainedTaxa`` fragment for the starting tree.
    """
    element: Dict[str, Any] = {"tag": "calibration", "children": []}
    if comment:
        element["comment"] = provenance_comment(cal)
    return element


def tmrca_fragment(cal: CalibrationPoint) -> Dict[str, Any]:
    """Build the ``<tmrcaStatistic>`` XML fragment."""
    # BEAST 1's tmrcaStatistic accepts either includeStem OR forParent (they
    # are mutually exclusive; setting both is rejected).  A stem calibration
    # measures the TMRCA of the clade plus its stem, which is exactly what
    # includeStem="true" expresses, so we use includeStem alone.
    include_stem = "true" if cal.calibration_method == "stem" else "false"
    attrs = {"id": tmrca_id(cal.name), "includeStem": include_stem}
    return {
        "tag": "tmrcaStatistic",
        "attrib": attrs,
        "children": [
            {
                "tag": "mrca",
                "children": [{"tag": "taxa", "attrib": {"idref": calibration_taxa_ref(cal)}}],
            },
            {"tag": "treeModel", "attrib": {"idref": "treeModel"}},
        ],
    }


def monophyly_fragment(cal: CalibrationPoint) -> Dict[str, Any]:
    """Build the ``<monophylyStatistic>`` XML fragment (calibration suffix)."""
    return {
        "tag": "monophylyStatistic",
        "attrib": {"id": monophyly_id(cal.name)},
        "children": [
            {
                "tag": "mrca",
                "children": [{"tag": "taxa", "attrib": {"idref": calibration_taxa_ref(cal)}}],
            },
            {"tag": "treeModel", "attrib": {"idref": "treeModel"}},
        ],
    }


def prior_fragment(
    cal: CalibrationPoint,
    registry: ModelRegistry,
    hyperparameter_ids: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build the distribution-prior XML fragment for one calibration.

    Without hyperpriors::

        <...Prior ...><statistic idref="tmrca(name)"/></...Prior>

    With hyperpriors the prior becomes a BEAST ``<distributionLikelihood>``
    whose ``ParametricDistributionModel`` takes the hyperpriorized parameter as
    a ``<parameter idref="..."/>`` input.  No prior is invented when a
    distribution is missing: the caller must reject that earlier.
    """
    dist = cal.distribution
    if dist is None:
        raise ValueError(
            f"calibration {cal.name!r} has no distribution; refusing to invent "
            "a Uniform(0, 1) prior"
        )
    if cal.hyperpriors:
        mapping = dict(hyperparameter_ids or {})
        missing = sorted(target for target in cal.hyperpriors if target not in mapping)
        if missing:
            raise ValueError(
                f"calibration {cal.name!r} declares hyperprior(s) for "
                f"{', '.join(missing)} but no hyperprior parameter was "
                "registered; hyperpriors must never be silently dropped"
            )
        return hyperprior_distribution_likelihood(cal, mapping)
    try:
        spec = registry.distribution_spec(dist.type)
    except ValueError as exc:
        raise ValueError(
            f"calibration {cal.name!r}: unknown distribution type {dist.type!r}"
        ) from exc
    attrs = registry.distribution_attrs(dist.type, dist.parameters, dist.offset)
    return {
        "tag": spec.element,
        "attrib": attrs,
        "children": [{"tag": "statistic", "attrib": {"idref": tmrca_id(cal.name)}}],
    }


# ---------------------------------------------------------------------------
# Hyperpriors
# ---------------------------------------------------------------------------


def hyperprior_parameter_id(cal: CalibrationPoint, target: str) -> str:
    """BEAST parameter id of a hyperpriorized calibration-distribution parameter."""
    return f"{cal.name}.{target}"


def _parameter_value(dist: DistributionConfig, target: str) -> float:
    params = dist.parameters or {}
    if target not in params:
        raise ValueError(
            f"distribution {dist.type!r} has no value for hyperprior target {target!r}"
        )
    try:
        return float(params[target])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"distribution {dist.type!r} parameter {target!r} is not numeric "
            f"({params[target]!r})"
        ) from exc


def hyperprior_parameter_bounds(
    cal: CalibrationPoint, target: str
) -> Tuple[Optional[float], Optional[float]]:
    """Bounds for the estimated hyperprior parameter.

    A uniform hyperprior contributes its own support; otherwise the target's
    natural domain is used (``stdev``/``scale``/``shape`` and real-space means
    are positive, a log-space lognormal mean is unbounded).
    """
    dist = cal.distribution
    if dist is None:  # pragma: no cover - Gate 0 guarantees a distribution
        raise ValueError(f"calibration {cal.name!r} has no distribution")
    hyper = cal.hyperpriors[target]
    if hyper.type == "uniform":
        params = hyper.parameters or {}
        lower = params.get("lower")
        upper = params.get("upper")
        try:
            lo = None if lower is None else float(lower)
            hi = None if upper is None else float(upper)
        except (TypeError, ValueError):
            lo, hi = None, None
        if lo is not None or hi is not None:
            return lo, hi
    if target in ("stdev", "scale", "shape"):
        return 0.0, None
    if dist.type == "lognormal":
        mrs = (dist.parameters or {}).get("meanInRealSpace", False)
        if isinstance(mrs, str):
            mrs = mrs.lower() == "true"
        return (0.0, None) if mrs else (None, None)
    if dist.type == "poisson":
        return 0.0, None
    if hyper.type in ("lognormal", "exponential", "gamma", "inverse_gamma"):
        offset = float(hyper.offset or 0.0)
        if offset > 0.0:
            return offset, None
    return None, None


def hyperprior_operator(cal: CalibrationPoint, target: str) -> Tuple[str, Dict[str, str]]:
    """Return ``(operator tag, attributes)`` moving a hyperprior parameter."""
    value = _parameter_value(cal.distribution, target)  # type: ignore[arg-type]
    lower, upper = hyperprior_parameter_bounds(cal, target)
    if lower is not None and upper is not None:
        span = float(upper) - float(lower)
        window = span / 50.0 if span > 0 else 1.0
        return "randomWalkOperator", {
            "windowSize": _format_number(max(window, 1e-6)),
            "weight": "1",
        }
    if lower is not None and upper is None:
        # A positive, unbounded parameter: scale moves are well defined.
        return "scaleOperator", {"scaleFactor": "0.75", "weight": "1"}
    window = max(0.1, abs(value) * 0.25)
    return "randomWalkOperator", {"windowSize": _format_number(window), "weight": "1"}


def hyperprior_prior_fragment(
    hyper: HyperpriorConfig, parameter_id: str, registry: ModelRegistry
) -> Dict[str, Any]:
    """Prior on the hyperprior parameter itself (``uniformPrior``, ...)."""
    try:
        spec = registry.distribution_spec(hyper.type)
    except ValueError as exc:
        raise ValueError(f"invalid hyperprior distribution {hyper.type!r}") from exc
    attrs = registry.distribution_attrs(hyper.type, hyper.parameters, hyper.offset)
    return {
        "tag": spec.element,
        "attrib": attrs,
        "children": [{"tag": "parameter", "attrib": {"idref": parameter_id}}],
    }


def _model_scalar(
    kind: str, target: str, params: Dict[str, Any], defaults: Dict[str, float]
) -> float:
    value = params.get(target, defaults.get(target, 0.0))
    try:
        return float(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - config validates
        raise ValueError(
            f"calibration distribution {kind!r}: parameter {target!r} is not "
            f"numeric ({value!r})"
        ) from exc


def _model_child(
    target: str,
    kind: str,
    dist: DistributionConfig,
    parameter_ids: Dict[str, str],
) -> Dict[str, Any]:
    pid = parameter_ids.get(target)
    if pid:
        return {"tag": target, "children": [{"tag": "parameter", "attrib": {"idref": pid}}]}
    defaults = {
        "lognormal": {"mean": 0.0, "stdev": 1.0},
        "normal": {"mean": 0.0, "stdev": 1.0},
        "gamma": {"shape": 1.0, "scale": 1.0},
        "poisson": {"mean": 1.0},
    }.get(kind, {})
    return {
        "tag": target,
        "text": _format_number(_model_scalar(kind, target, dist.parameters or {}, defaults)),
    }


def hyperprior_distribution_likelihood(
    cal: CalibrationPoint, parameter_ids: Dict[str, str]
) -> Dict[str, Any]:
    """``<distributionLikelihood>`` for a calibration with hyperpriorized args.

    BEAST's prior parsers (``logNormalPrior`` ...) only accept literal doubles.
    The equivalent hierarchical prior is a ``DistributionLikelihood`` over the
    ``tmrca`` statistic whose distribution model references the estimated
    parameter(s).  Only the (distribution, target) combinations verified against
    BEAST's ``*DistributionModelParser`` are accepted (see
    :data:`models.HYPERPRIOR_EMITTABLE_TARGETS`).
    """
    dist = cal.distribution
    if dist is None:  # pragma: no cover - callers check first
        raise ValueError(f"calibration {cal.name!r} has no distribution")
    kind = str(dist.type)
    supported = HYPERPRIOR_EMITTABLE_TARGETS.get(kind, ())
    unsupported = sorted(target for target in cal.hyperpriors if target not in supported)
    if unsupported:
        raise ValueError(
            f"calibration {cal.name!r}: BEAST cannot parameterize "
            f"{kind}.{', '.join(unsupported)}; hyperpriors are only implemented "
            "for "
            + ", ".join(f"{k}.{'/'.join(v)}" for k, v in HYPERPRIOR_EMITTABLE_TARGETS.items())
        )
    params = dist.parameters or {}
    if kind == "lognormal":
        mrs = params.get("meanInRealSpace", False)
        if isinstance(mrs, str):
            mrs = mrs.lower() == "true"
        attrib: Dict[str, Any] = {"meanInRealSpace": "true" if mrs else "false"}
        if float(dist.offset or 0.0):
            attrib["offset"] = _format_number(dist.offset)
        model = {
            "tag": "logNormalDistributionModel",
            "attrib": attrib,
            "children": [
                _model_child("mean", kind, dist, parameter_ids),
                _model_child("stdev", kind, dist, parameter_ids),
            ],
        }
    elif kind == "normal":
        model = {
            "tag": "normalDistributionModel",
            "children": [
                _model_child("mean", kind, dist, parameter_ids),
                _model_child("stdev", kind, dist, parameter_ids),
            ],
        }
    elif kind == "gamma":
        model = {
            "tag": "gammaDistributionModel",
            "children": [
                _model_child("shape", kind, dist, parameter_ids),
                _model_child("scale", kind, dist, parameter_ids),
            ],
        }
    elif kind == "poisson":
        model = {
            "tag": "poissonDistributionModel",
            "children": [_model_child("mean", kind, dist, parameter_ids)],
        }
    else:  # pragma: no cover - config rejects unsupported hyperprior types
        raise ValueError(f"calibration distribution {kind!r} cannot carry a hyperprior in BEAST 1")
    return {
        "tag": "distributionLikelihood",
        "children": [
            {
                "tag": "data",
                "children": [{"tag": "statistic", "attrib": {"idref": tmrca_id(cal.name)}}],
            },
            {"tag": "distribution", "children": [model]},
        ],
    }


# ---------------------------------------------------------------------------
# Starting-tree constraints
# ---------------------------------------------------------------------------


def distribution_model_fragment(dist: DistributionConfig) -> Dict[str, Any]:
    """Build a BEAST ``ParametricDistributionModel`` for a calibration.

    Unlike the ``*Prior`` elements used inside ``<prior>`` (which take their
    arguments as *attributes*), the distribution models accepted by
    ``<tmrca>`` / ``<distribution>`` take their arguments as *child elements*
    (``<mean>``, ``<stdev>``, ``<lower>``, ``<upper>``, ...).  Only the
    arguments supported by the corresponding BEAST parser are emitted.
    """
    params = dist.parameters or {}
    offset = float(dist.offset or 0.0)
    kind = str(dist.type)

    def number(key: str, default: float) -> float:
        value = params.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:  # pragma: no cover - config validates
            raise ValueError(
                f"calibration distribution {kind!r}: parameter {key!r} is not numeric ({value!r})"
            ) from exc

    def child(tag: str, value: float) -> Dict[str, Any]:
        return {"tag": tag, "text": _format_number(value)}

    if kind == "uniform":
        return {
            "tag": "uniformDistributionModel",
            "children": [
                child("lower", number("lower", 0.0)),
                child("upper", number("upper", 1.0)),
            ],
        }
    if kind == "lognormal":
        mrs = params.get("meanInRealSpace", False)
        if isinstance(mrs, str):
            mrs = mrs.lower() == "true"
        attrib: Dict[str, Any] = {"meanInRealSpace": "true" if mrs else "false"}
        if offset:
            attrib["offset"] = _format_number(offset)
        return {
            "tag": "logNormalDistributionModel",
            "attrib": attrib,
            "children": [
                child("mean", number("mean", 0.0)),
                child("stdev", number("stdev", params.get("S", 1.0))),  # type: ignore[arg-type]
            ],
        }
    if kind == "normal":
        return {
            "tag": "normalDistributionModel",
            "children": [
                child("mean", number("mean", 0.0)),
                child("stdev", number("stdev", params.get("sigma", 1.0))),  # type: ignore[arg-type]
            ],
        }
    if kind == "exponential":
        attrib = {"offset": _format_number(offset)} if offset else {}
        return {
            "tag": "exponentialDistributionModel",
            "attrib": attrib,
            "children": [child("mean", number("mean", 1.0))],
        }
    if kind == "gamma":
        attrib = {"offset": _format_number(offset)} if offset else {}
        return {
            "tag": "gammaDistributionModel",
            "attrib": attrib,
            "children": [
                child("shape", number("shape", params.get("alpha", 1.0))),  # type: ignore[arg-type]
                child("scale", number("scale", params.get("beta", 1.0))),  # type: ignore[arg-type]
            ],
        }
    if kind == "beta":
        if offset:
            raise ValueError(
                "calibration distribution 'beta' cannot carry an offset inside "
                "a <tmrca> constraint: BEAST's BetaDistributionModelParser has "
                "allowOffset()=false; the starting tree must use the "
                "coalescent-simulator path"
            )
        return {
            "tag": "betaDistributionModel",
            "children": [
                child("alpha", number("alpha", params.get("a", 1.0))),  # type: ignore[arg-type]
                child("beta", number("beta", params.get("b", 1.0))),  # type: ignore[arg-type]
            ],
        }
    raise ValueError(
        f"calibration distribution {kind!r} cannot be embedded in a <tmrca> "
        "starting-tree constraint (supported: uniform, lognormal, normal, "
        "exponential, gamma, beta)"
    )


def constrained_taxa_fragment(
    cal: CalibrationPoint, include_distribution: bool = False
) -> Dict[str, Any]:
    """Build a ``<tmrca monophyletic=...><taxa idref/>[distribution]</tmrca>``.

    When ``include_distribution`` is true the calibration's
    ``ParametricDistributionModel`` is passed as a child of ``<tmrca>``,
    which is what ``OldCoalescentSimulatorParser`` reads to place the clade
    inside the calibration support.
    """
    children: List[Dict[str, Any]] = [
        {"tag": "taxa", "attrib": {"idref": calibration_taxa_ref(cal)}}
    ]
    if include_distribution and cal.distribution is not None:
        children.append(distribution_model_fragment(cal.distribution))
    return {
        "tag": "tmrca",
        "attrib": {"monophyletic": "true" if cal.monophyletic else "false"},
        "children": children,
    }


def distribution_support(cal: CalibrationPoint) -> Tuple[float, float]:
    """Return the ``(lower, upper)`` numerical support of a calibration.

        ``upper`` is ``math.inf`` when the distribution has no finite upper bound.
        A calibration without a distribution is an error, not Uniform(0, 1)
    .
    """
    dist = cal.distribution
    if dist is None:
        raise ValueError(
            f"calibration {cal.name!r} has no distribution; refusing to assume " "Uniform(0, 1)"
        )
    params = dist.parameters or {}
    offset = float(dist.offset or 0.0)
    kind = str(dist.type)

    def as_float(key: str, default: float) -> float:
        try:
            return float(params.get(key, default))
        except (TypeError, ValueError):
            return default

    if kind == "uniform":
        return as_float("lower", 0.0), as_float("upper", 1.0)
    if kind in ("lognormal", "exponential", "gamma", "inverse_gamma"):
        return offset, math.inf
    if kind == "normal":
        return -math.inf, math.inf
    if kind == "beta":
        return offset, offset + 1.0
    return offset, math.inf


def calibration_start_age(cal: CalibrationPoint) -> float:
    """A representative node age that lies strictly inside the support.

    Used to build calibration-aware starting trees: every value returned is
    greater than the distribution's lower bound (when that bound is finite)
    so the calibration log-density is finite.  A missing
    distribution raises instead of inventing a 0-1 Ma age.
    """
    dist = cal.distribution
    if dist is None:
        raise ValueError(
            f"calibration {cal.name!r} has no distribution; refusing to invent " "a starting age"
        )
    params = dist.parameters or {}
    offset = float(dist.offset or 0.0)
    kind = str(dist.type)

    def as_float(key: str, default: float) -> float:
        try:
            return float(params.get(key, default))
        except (TypeError, ValueError):
            return default

    if kind == "uniform":
        lower = as_float("lower", 0.0)
        upper = as_float("upper", 1.0)
        if upper <= lower:
            upper = lower + 1.0
        return max(lower + (upper - lower) / 2.0, 0.01)
    if kind == "lognormal":
        mean = as_float("mean", 1.0)
        if offset > 0.0:
            # stay strictly above the offset (the support's lower bound)
            return max(offset * 1.05, offset + max(mean, 0.5))
        return max(mean, 0.5)
    if kind == "normal":
        return max(as_float("mean", 0.0), 0.01)
    if kind == "exponential":
        return max(offset + as_float("mean", 1.0), 0.01)
    if kind == "gamma":
        shape = as_float("shape", params.get("alpha", 1.0))  # type: ignore[arg-type]
        scale = as_float("scale", params.get("beta", 1.0))  # type: ignore[arg-type]
        return max(offset + shape * scale, 0.01)
    if kind == "inverse_gamma":
        return max(offset + 1.0, 0.01)
    if kind == "beta":
        alpha = as_float("alpha", params.get("a", 1.0))  # type: ignore[arg-type]
        beta = as_float("beta", params.get("b", 1.0))  # type: ignore[arg-type]
        if alpha + beta <= 0:
            return max(offset + 0.5, 0.01)
        fraction = min(max(alpha / (alpha + beta), 0.01), 0.99)
        return max(offset + fraction, 0.01)
    return max(offset + 1.0, 0.01)


def has_finite_support(cal: CalibrationPoint) -> bool:
    """True when the support can be embedded in a constrained starting tree.

    This is narrower than mathematical finite support: BEAST's
    ``BetaDistributionModelParser`` does not accept an ``offset`` attribute,
    so a beta calibration with a non-zero offset must use the nested
    coalescent-simulator starting tree (whose height is drawn from the
    offset-aware quantile) instead of ``constrainedTaxa``.
    """
    if cal.distribution is not None:
        if str(cal.distribution.type) == "beta" and float(cal.distribution.offset or 0.0):
            return False
    lower, upper = distribution_support(cal)
    return math.isfinite(lower) and math.isfinite(upper)


def children_of(
    cal: CalibrationPoint,
    calibrations: Optional[List[CalibrationPoint]] = None,
) -> List[str]:
    """Return the names of the *direct* children of ``cal`` in the clade tree.

    Containment is the normalized taxon-set relation: a calibration is nested
    inside another when its clade is a *strict subset*.  A root calibration
    (``taxa=None``) is the parent of the maximal clades (clades with no strict
    superset); every other calibration is the parent of the clades whose
    smallest strict superset is its own taxon set.  The returned
    list is sorted by name.

    ``calibrations`` is the full calibration list.  When omitted, the context
    attached by :class:`beastxpy.config.ConfigParser` /
    :class:`beastxpy.model.ModelAssembler` is used; a point constructed on its
    own therefore has no known descendants and returns ``[]``.
    """
    pool = (
        list(calibrations)
        if calibrations is not None
        else list(getattr(cal, "calibration_context", ()) or ())
    )
    if not pool:
        return []
    others = [other for other in pool if other is not cal and other.name != cal.name]
    other_sets = [other.taxa_set for other in others if other.taxa_set]
    if cal.is_root:
        universal = frozenset().union(*(other.taxa_set for other in pool))
        parent_set = universal
    else:
        parent_set = cal.taxa_set
    if not parent_set:
        return []

    def has_intermediate(taxa: frozenset) -> bool:
        """True when another clade lies strictly between ``taxa`` and parent."""
        return any(taxa < candidate < parent_set for candidate in other_sets)

    names: List[str] = []
    for other in others:
        candidate_set = other.taxa_set
        if not candidate_set or not (candidate_set < parent_set):
            continue
        # Direct children have no clade strictly between them and ``cal``.
        # For a root calibration ``parent_set`` is the union of every clade,
        # so this selects exactly the maximal clades.
        if not has_intermediate(candidate_set):
            names.append(other.name)
    return sorted(set(names))


# ---------------------------------------------------------------------------
# Whole-config helpers used by model.py
# ---------------------------------------------------------------------------


def build_taxa_sets(config) -> List[Dict[str, Any]]:
    """Build the ordered list of ``<taxa>`` fragments for the config.

    The first fragment is the global ``<taxa id="taxa">`` set (union of all
    partition taxa); each calibration with a taxon list gets its own set.
    Root calibrations (``taxa`` is ``None``) use the global set and therefore
    do not add a fragment.
    """
    seen_taxa: Dict[str, None] = {}
    for p in config.partitions:
        for seq in p.alignment.sequences:
            seen_taxa.setdefault(seq.taxon, None)

    tip_dates = getattr(config, "tip_dates", {}) or {}

    def _taxon_node(name: str, ref: bool = False) -> Dict[str, Any]:
        if ref:
            return {"tag": "taxon", "attrib": {"idref": name}}
        node: Dict[str, Any] = {"tag": "taxon", "attrib": {"id": name}}
        entry = tip_dates.get(name)
        if entry:
            if isinstance(entry, dict):
                value = entry.get("value", 0.0)
                direction = entry.get("direction", DEFAULT_DATE_DIRECTION)
                units = entry.get("units", DEFAULT_DATE_UNITS)
            else:
                value = entry
                direction = DEFAULT_DATE_DIRECTION
                units = DEFAULT_DATE_UNITS
            #: ``units`` is mandatory for BEAST X 10.5.0, so it is
            # always written, even for the default time scale.
            date_attrib: Dict[str, Any] = {
                "value": _format_number(value),
                "direction": str(direction),
                "units": str(units),
            }
            node["children"] = [{"tag": "date", "attrib": date_attrib}]
        return node

    fragments: List[Dict[str, Any]] = [
        {
            "tag": "taxa",
            "attrib": {"id": "taxa"},
            "children": [_taxon_node(name) for name in seen_taxa],
        }
    ]
    for cal in config.calibrations:
        if not cal.taxa:
            # Root calibration: re-use the global taxa set.
            continue
        fragments.append(
            {
                "tag": "taxa",
                "attrib": {"id": calibration_taxa_id(cal.name)},
                "children": [{"tag": "taxon", "attrib": {"idref": name}} for name in cal.taxa],
            }
        )
    return fragments


def calibrated_clades(config) -> List[CalibrationPoint]:
    """Return calibration points that pin down a clade (have taxa)."""
    return [c for c in config.calibrations if c.taxa]


def _format_number(value: Any) -> str:
    """Format a scalar for XML text/attributes without scientific notation."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite value {value!r} in calibration XML")
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


# ---------------------------------------------------------------------------
# Read-only data-check inspectors (F2 ``check-data``; append-only additions)
# ---------------------------------------------------------------------------
#
# Non-raising helpers that describe calibration/taxon problems per calibration
# so ``beastxpy.datacheck`` can report findings instead of aborting.


def missing_calibration_taxa(
    calibrations: List[CalibrationPoint], known_taxa
) -> List[Tuple[str, List[str]]]:
    """Calibrations naming taxa absent from the alignment(s).

    Returns ``(calibration_name, sorted_missing_taxa)`` for every calibration
    whose taxon list references at least one unknown name.  Root calibrations
    (``taxa`` is None) and empty lists are skipped here (see
    :func:`empty_taxa_calibrations`).
    """
    known = frozenset(known_taxa)
    result: List[Tuple[str, List[str]]] = []
    for cal in calibrations:
        if not cal.taxa:
            continue
        missing = sorted({str(taxon) for taxon in cal.taxa if taxon not in known})
        if missing:
            result.append((cal.name, missing))
    return result


def empty_taxa_calibrations(calibrations: List[CalibrationPoint]) -> List[str]:
    """Names of calibrations with a non-None but empty taxon list.

    ``taxa: []`` is never meaningful: it is not a root calibration (``taxa:
    null``) and constrains no clade, so a monophyly constraint built from it
    would be vacuous.
    """
    return [cal.name for cal in calibrations if cal.taxa is not None and len(cal.taxa) == 0]


def calibration_age_interval(
    cal: CalibrationPoint,
) -> Tuple[Optional[float], Optional[float]]:
    """Plausible ``(min, max)`` age interval implied by a calibration.

    Uses the 2.5%/97.5% quantiles from
    :func:`beastxpy.utils.distribution_quantiles` when they are computable and
    finite, otherwise falls back to the finite edges of
    :func:`distribution_support`.  ``None`` marks an unbounded edge.  A
    calibration without a distribution yields ``(None, None)``: no interval is
    invented.
    """
    if cal.distribution is None:
        return None, None
    from .utils import distribution_quantiles

    dist = cal.distribution
    quantiles = distribution_quantiles(dist.type, dist.parameters or {}, dist.offset)
    if quantiles is not None:
        low, high = quantiles
        low = low if math.isfinite(low) else None
        high = high if math.isfinite(high) else None
        if low is not None or high is not None:
            return low, high
    try:
        low, high = distribution_support(cal)
    except ValueError:
        return None, None
    return (
        low if math.isfinite(low) else None,
        high if math.isfinite(high) else None,
    )
