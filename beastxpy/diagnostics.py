"""Calibration diagnostics for BeastXPy (Gate 2 + ``diagnose`` command).

Implements the calibration-validation layer of the four-gate scheme over the
parsed :class:`BEASTConfig`:

1. **Clade tree** -- calibrations are ordered by *clade* (taxon-set inclusion)
   and by *crown vs stem* node identity.  :func:`calibration_clade_tree` and
   :func:`calibration_children` mirror the now-implemented
   ``calibration.children_of`` clade-containment contract.
2. **Temporal consistency** -- a nested clade must be younger than the clade
   that contains it.  Two calibrations on the same node (same taxon set, same
   crown/stem identity) are reported as duplicates (the effective prior is the
   product of both densities); a crown/stem pair on the same clade is a
   legitimate configuration and is only checked for ``crown < stem``.
3. **Distribution divergence / overlap** -- every pair (including pairs whose
   95% intervals are disjoint) is recorded with a symmetric overlap fraction
   in ``[0, 1]`` (0 = disjoint) and a symmetric 1-Wasserstein divergence.  Only
   *nested* pairs can encode conflicting temporal information, so only they
   warn.
4. **Monophyly conflicts** -- clades marked ``monophyletic: true`` are checked
   for intersection / containment.
5. **Sensitivity analysis** -- N+1 structurally edited "prior-only" XML files
   (``<treeLikelihood>`` removed) plus a cross-platform Python launcher that
   surfaces every failure.  Variants are referentially closed (no dangling
   ``idref``).
6. **Visualisation** -- per-calibration density curves with 95% bands and a
   pairwise overlap heat-map (matplotlib).
7. **Reports** -- self-contained HTML (escaped, inline CSS) and plain-text
   versions; both state how many calibrations/comparisons were actually
   audited.

All calibration distributions use the shared BEAST conversions from
:mod:`beastxpy.utils` (``lognormal_mu_sigma``, ``exponential_mean``, and the
offset handling inside ``distribution_quantiles``/``distribution_mean``) so
plots, quantiles and the generated XML agree.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from html import escape as html_escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from .models import BEASTConfig, CalibrationPoint
from .utils import (
    distribution_offset,
    distribution_quantiles,
    exponential_mean,
    lognormal_mu_sigma,
    wasserstein_distance,
)

DEFAULT_OVERLAP_THRESHOLD = 0.15


class DiagnosticsError(ValueError):
    """Raised when a diagnostics artefact cannot be built safely."""


# ---------------------------------------------------------------------------
# Filenames
# ---------------------------------------------------------------------------

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str, max_length: int = 60) -> str:
    """Return a filesystem-safe, unique stem derived from ``name``.

    Characters outside ``[A-Za-z0-9._-]`` are replaced by ``_`` (so ``/``, ``:``,
    ``<`` and friends cannot create directories or fail on Windows) and a
    6-hex digest of the original name preserves uniqueness and traceability.
    """
    original = str(name)
    base = _UNSAFE_FILENAME.sub("_", original).strip("._-")
    if not base:
        base = "item"
    digest = hashlib.sha1(original.encode("utf-8")).hexdigest()[:6]
    return f"{base[:max_length]}_{digest}"


# ---------------------------------------------------------------------------
# Findings / report containers
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticFinding:
    """A Gate-2 diagnostic finding.

    Attributes:
        severity: ``"error"`` | ``"warning"`` | ``"info"``.
        kind: TemporalConsistency | DistributionOverlap | MonophylyConflict | ...
        where: Calibration names involved.
        message: Human-readable description.
        code: Stable machine-readable code.
    """

    severity: str
    kind: str
    message: str
    where: str = ""
    code: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "kind": self.kind,
            "where": self.where,
            "message": self.message,
            "code": self.code,
        }


@dataclass
class DiagnosticReport:
    """Result of a full diagnostics run."""

    findings: List[DiagnosticFinding] = field(default_factory=list)
    pair_overlaps: List[Dict[str, Any]] = field(default_factory=list)
    n_sensitivity: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == "warning" for f in self.findings)


# ---------------------------------------------------------------------------
# Calibration clade tree (diagnostics-side replacement for children_of)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationNode:
    """A calibration's node identity: clade (taxon set) plus crown/stem."""

    name: str
    taxa: frozenset
    is_stem: bool

    @property
    def method(self) -> str:
        return "stem" if self.is_stem else "crown"


def _crown_taxa(cal: CalibrationPoint, all_taxa: frozenset) -> frozenset:
    """Taxon set of the calibrated clade (empty/None taxa = whole tree)."""
    if cal.taxa is None or len(cal.taxa) == 0:
        return all_taxa
    return frozenset(cal.taxa)


def calibration_clade_tree(config: BEASTConfig) -> List[CalibrationNode]:
    """Build the calibration clade tree as ordered :class:`CalibrationNode` items.

    Ordering is defined only where it is biologically meaningful:

    * ``crown(X) < stem(X)`` (the stem node is the parent of the crown node);
    * every node of clade ``X`` is younger than every node of clade ``Y`` when
      ``X`` is a strict subset of ``Y``;
    * nodes on disjoint or incomparable clades have no ordering.

    ``calibration.children_of`` implements the same strict-containment rule
    for the model layer; both must agree.
    """
    all_taxa: Set[str] = set()
    for cal in config.calibrations:
        if cal.taxa:
            all_taxa.update(cal.taxa)
    all_taxa_frozen = frozenset(all_taxa)
    nodes = [
        CalibrationNode(
            name=cal.name,
            taxa=_crown_taxa(cal, all_taxa_frozen),
            is_stem=(cal.calibration_method == "stem"),
        )
        for cal in config.calibrations
    ]
    return sorted(nodes, key=lambda n: (n.name, n.is_stem))


def calibration_children(config: BEASTConfig, name: str) -> List[str]:
    """Return names of calibrations whose clade is strictly nested in ``name``.

    This mirrors the ``calibration.children_of`` clade-containment contract.
    """
    nodes = calibration_clade_tree(config)
    parent = next((node for node in nodes if node.name == name), None)
    if parent is None:
        raise DiagnosticsError(f"unknown calibration {name!r}")
    children = []
    for node in nodes:
        if node.name == name:
            continue
        if node.taxa and parent.taxa and node.taxa < parent.taxa:
            children.append(node.name)
    return sorted(set(children))


def _clade_relation(a: CalibrationNode, b: CalibrationNode) -> str:
    """Classify a calibration pair: equal | nested | disjoint | incomparable."""
    if a.taxa == b.taxa:
        return "equal"
    if a.taxa < b.taxa or b.taxa < a.taxa:
        return "nested"
    if a.taxa and b.taxa and not (a.taxa & b.taxa):
        return "disjoint"
    return "incomparable"


def _is_younger(child: CalibrationNode, parent: CalibrationNode) -> bool:
    """True when ``child``'s node is necessarily below ``parent``'s node."""
    if child.taxa == parent.taxa:
        return child.is_stem and not parent.is_stem
    return child.taxa < parent.taxa


# ---------------------------------------------------------------------------
# Distribution sampling helpers
# ---------------------------------------------------------------------------


def sample_distribution(
    dist_type: str,
    params: Dict[str, Any],
    offset: float = 0.0,
    n: int = 20000,
    seed: int = 42,
) -> Optional[np.ndarray]:
    """Draw samples from a supported calibration distribution.

    Lognormal real-space parameters and exponential ``rate`` are converted with
    the same helpers used by :func:`utils.distribution_quantiles` and the XML
    generator.  Returns ``None`` for distributions without finite statistics
    (``one_on_x``) or for bad parameters.
    """
    from scipy import stats

    rng = np.random.default_rng(seed)
    try:
        if dist_type in ("normal", "gaussian"):
            mean = float(params.get("mean", 0.0))
            stdev = float(params.get("stdev", params.get("sigma", 1.0)))
            return rng.normal(mean, stdev, n) + offset
        if dist_type == "lognormal":
            mu, sigma = lognormal_mu_sigma(params)
            return offset + rng.lognormal(mu, sigma, n)
        if dist_type == "uniform":
            lower = float(params.get("lower", 0.0))
            upper = float(params.get("upper", 1.0))
            return offset + rng.uniform(lower, upper, n)
        if dist_type == "exponential":
            return offset + rng.exponential(exponential_mean(params), n)
        if dist_type == "gamma":
            shape = float(params.get("shape", params.get("alpha", 1.0)))
            scale = float(params.get("scale", params.get("beta", 1.0)))
            return offset + rng.gamma(shape, scale=scale, size=n)
        if dist_type == "beta":
            a = float(params.get("alpha", params.get("a", 2.0)))
            b = float(params.get("beta", params.get("b", 2.0)))
            return offset + rng.beta(a, b, n)
        if dist_type == "inverse_gamma":
            shape = float(params.get("shape", params.get("alpha", 2.0)))
            scale = float(params.get("scale", params.get("beta", 1.0)))
            return offset + stats.invgamma.rvs(shape, scale=scale, size=n, random_state=rng)
        if dist_type == "poisson":
            mu = float(params.get("mean", params.get("mu", 1.0)))
            return offset + rng.poisson(mu, n)
        if dist_type == "half_normal":
            mean = float(params.get("mean", 0.0))
            stdev = float(params.get("stdev", params.get("sigma", 1.0)))
            draws = rng.normal(mean, stdev, n * 4)
            draws = draws[draws >= 0.0]
            return offset + draws[:n]
        if dist_type == "laplace":
            return offset + rng.laplace(
                float(params.get("mean", 0.0)), float(params.get("scale", 1.0)), n
            )
        if dist_type == "half_t":
            scale = float(params.get("scale", 1.0))
            df = float(params.get("df", 1.0))
            return offset + np.abs(stats.t.rvs(df, loc=0.0, scale=scale, size=n, random_state=rng))
        if dist_type == "chi_square":
            df = float(params.get("df", params.get("degrees_of_freedom", 1.0)))
            return offset + rng.chisquare(df, n)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return None


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class CalibrationDiagnostics:
    """Run all Gate-2 checks over a configuration."""

    def __init__(
        self,
        config: BEASTConfig,
        overlap_threshold: float = DEFAULT_OVERLAP_THRESHOLD,
        overlap_measure: str = "relative",
        strict: bool = True,
    ) -> None:
        self.config = config
        self.threshold = overlap_threshold
        self.measure = overlap_measure
        self.strict = strict
        self.findings: List[DiagnosticFinding] = []
        self.pair_overlaps: List[Dict[str, Any]] = []
        self.constraint_notes: List[str] = []

    # ------------------------------------------------------------------
    def run(self) -> DiagnosticReport:
        """Execute every check; returns the aggregated report."""
        self.findings = []
        self.pair_overlaps = []
        self.constraint_notes = []
        self.check_temporal_consistency()
        self.check_distribution_overlap()
        self.check_monophyly_conflicts()
        self.check_starting_tree_semantics()
        self.check_lognormal_parameterisation()
        report = DiagnosticReport(
            findings=sorted(
                self.findings,
                key=lambda f: (
                    0 if f.severity == "error" else 1 if f.severity == "warning" else 2,
                    f.where,
                ),
            ),
            pair_overlaps=list(self.pair_overlaps),
        )
        report.extra.update(
            {
                "n_calibrations": len(self.config.calibrations),
                "n_pairs": len(self.pair_overlaps),
                "clade_tree": [
                    {"name": node.name, "taxa": sorted(node.taxa), "method": node.method}
                    for node in calibration_clade_tree(self.config)
                ],
                "constraint_notes": list(self.constraint_notes),
            }
        )
        return report

    # -- clade helpers ---------------------------------------------------
    def clade_tree(self) -> List[CalibrationNode]:
        """Return the calibration clade tree (see :func:`calibration_clade_tree`)."""
        return calibration_clade_tree(self.config)

    def children_of(self, name: str) -> List[str]:
        """Names of calibrations strictly nested inside ``name``."""
        return calibration_children(self.config, name)

    # -- 1. temporal consistency ----------------------------------------
    def _intervals(self) -> Dict[str, Optional[Tuple[float, float]]]:
        intervals: Dict[str, Optional[Tuple[float, float]]] = {}
        for cal in self.config.calibrations:
            if cal.distribution is None or cal.distribution.type == "one_on_x":
                intervals[cal.name] = None
                continue
            intervals[cal.name] = distribution_quantiles(
                cal.distribution.type,
                cal.distribution.parameters,
                cal.distribution.offset,
            )
        return intervals

    def check_temporal_consistency(self) -> None:
        """Detect duplicate nodes and nesting violations using clade ordering."""
        nodes = self.clade_tree()
        intervals = self._intervals()
        reported: Set[Tuple[str, str, str]] = set()
        for i, a in enumerate(nodes):
            for b in nodes[i + 1 :]:
                relation = _clade_relation(a, b)
                interval_a = intervals.get(a.name)
                interval_b = intervals.get(b.name)
                if relation == "equal":
                    if a.is_stem == b.is_stem:
                        key = ("duplicate",) + tuple(sorted((a.name, b.name)))
                        if key not in reported:
                            reported.add(key)  # type: ignore[arg-type]
                            self._add(
                                "error" if self.strict else "warning",
                                "TemporalConsistency",
                                f"{a.name} == {b.name}",
                                (
                                    f"calibrations '{a.name}' and '{b.name}' constrain "
                                    f"the same node ({'stem' if a.is_stem else 'crown'} of "
                                    f"the same clade); both priors multiply on one tmrca, "
                                    f"so the effective prior is the product of the two densities"
                                ),
                                "DUPLICATE_CALIBRATION",
                            )
                        continue
                    # Legitimate crown/stem pair on the same clade: crown must be
                    # younger than stem.
                    crown = a if not a.is_stem else b
                    stem = b if not a.is_stem else a
                    if interval_a is None or interval_b is None:
                        continue
                    crown_interval = intervals[crown.name]
                    stem_interval = intervals[stem.name]
                    assert crown_interval is not None and stem_interval is not None
                    if crown_interval[0] > stem_interval[1]:
                        height = stem_interval[1] - stem_interval[0]
                        tol = 0.05 * height if height > 0 else 0.0
                        if crown_interval[0] > stem_interval[1] + tol:
                            self._add(
                                "error" if self.strict else "warning",
                                "TemporalConsistency",
                                f"{crown.name} (crown) vs {stem.name} (stem)",
                                (
                                    f"crown calibration '{crown.name}' "
                                    f"[{crown_interval[0]:.4g}, {crown_interval[1]:.4g}] "
                                    f"starts older than the stem upper bound "
                                    f"{stem_interval[1]:.4g}; a crown node cannot be "
                                    f"older than its stem"
                                ),
                                "CROWN_OLDER_THAN_STEM",
                            )
                    continue
                if relation != "nested":
                    continue
                # child = the node that is necessarily below the other.
                child, parent = (a, b) if _is_younger(a, b) else (b, a)
                if child is parent:
                    continue
                p_interval = intervals.get(parent.name)
                c_interval = intervals.get(child.name)
                if p_interval is None or c_interval is None:
                    continue
                p_lo, p_hi = p_interval
                c_lo, c_hi = c_interval
                tol = 0.05 * (p_hi - p_lo) if p_hi > p_lo else 0.0
                violations: List[str] = []
                if c_hi > p_hi + tol:
                    violations.append(f"nested 97.5% upper bound {c_hi:.4g} > parent {p_hi:.4g}")
                if c_lo > p_hi + tol:
                    violations.append(
                        f"nested clade is entirely older than the parent interval "
                        f"([{c_lo:.4g}, {c_hi:.4g}] vs [{p_lo:.4g}, {p_hi:.4g}])"
                    )
                if violations:
                    self._add(
                        "error" if self.strict else "warning",
                        "TemporalConsistency",
                        f"{parent.name} -> {child.name}",
                        (
                            f"calibration '{child.name}' ({child.method} node) is nested "
                            f"inside '{parent.name}' ({parent.method} node) but its 95% "
                            f"interval is not younger-compatible: " + "; ".join(violations)
                        ),
                        "TEMPORAL_INCOMPATIBLE",
                    )

    # -- 2. distribution divergence / overlap ----------------------------
    def check_distribution_overlap(self) -> None:
        """Symmetric pairwise overlap/divergence, including disjoint supports."""
        nodes = {node.name: node for node in self.clade_tree()}
        calibrations = [
            c
            for c in self.config.calibrations
            if c.distribution is not None and c.distribution.type != "one_on_x"
        ]
        samples: Dict[str, Optional[np.ndarray]] = {}
        intervals: Dict[str, Optional[Tuple[float, float]]] = {}
        for cal in calibrations:
            dist = cal.distribution
            assert dist is not None  # filtered above
            samples[cal.name] = sample_distribution(dist.type, dist.parameters, dist.offset)
            intervals[cal.name] = distribution_quantiles(dist.type, dist.parameters, dist.offset)

        for i, a in enumerate(calibrations):
            for b in calibrations[i + 1 :]:
                sample_a = samples.get(a.name)
                sample_b = samples.get(b.name)
                interval_a = intervals.get(a.name)
                interval_b = intervals.get(b.name)
                if sample_a is None or sample_b is None:
                    continue
                if interval_a is None or interval_b is None:
                    continue
                node_a = nodes.get(a.name)
                node_b = nodes.get(b.name)
                relation = _clade_relation(node_a, node_b) if node_a and node_b else "incomparable"

                width_a = interval_a[1] - interval_a[0]
                width_b = interval_b[1] - interval_b[0]
                mean_width = (width_a + width_b) / 2.0
                overlap_lo = max(interval_a[0], interval_b[0])
                overlap_hi = min(interval_a[1], interval_b[1])
                overlap_length = max(0.0, overlap_hi - overlap_lo)
                overlap = (
                    overlap_length / min(width_a, width_b) if min(width_a, width_b) > 0 else 0.0
                )
                overlap = float(min(1.0, max(0.0, overlap)))
                w1 = float(wasserstein_distance(sample_a, sample_b))
                divergence = w1 / mean_width if mean_width > 0 else float("inf")
                comparability = (
                    "disjoint"
                    if overlap_length <= 0
                    else ("nested" if relation in ("equal", "nested") else "independent")
                )
                # Deterministic, symmetric pair naming.
                name_a, name_b = sorted((a.name, b.name))
                assert interval_a is not None and interval_b is not None
                entry = {
                    "a": name_a,
                    "b": name_b,
                    "wasserstein": w1,
                    "divergence": float(divergence),
                    "overlap": overlap,
                    "metric": float(divergence),
                    "measure": self.measure,
                    "relation": relation,
                    "comparability": comparability,
                    "threshold": self.threshold,
                    "interval_a": list(interval_a),
                    "interval_b": list(interval_b),
                }
                self.pair_overlaps.append(entry)
                if (
                    relation == "nested"
                    and comparability == "nested"
                    and divergence > self.threshold
                ):
                    self._add(
                        "warning",
                        "DistributionOverlap",
                        f"{name_a} vs {name_b}",
                        (
                            f"nested calibrations have low prior overlap "
                            f"(overlap {overlap:.3f}, 1-Wasserstein divergence "
                            f"{divergence:.3f} > threshold {self.threshold}); "
                            f"the 95% intervals carry conflicting temporal information"
                        ),
                        "OVERLAP_EXCEEDED",
                    )
        self.pair_overlaps.sort(key=lambda entry: (entry["a"], entry["b"]))

    # -- 3. monophyly conflicts -------------------------------------------
    def check_monophyly_conflicts(self) -> None:
        """Reject contradictory monophyletic clade constraints."""
        monophyletic = [c for c in self.config.calibrations if c.monophyletic and c.taxa]
        sets: List[Tuple[str, Set[str]]] = [
            (c.name, set(c.taxa)) for c in monophyletic if c.taxa is not None
        ]
        for i, (a_name, a_set) in enumerate(sets):
            for b_name, b_set in sets[i + 1 :]:
                if not a_set or not b_set:
                    continue
                intersection = a_set & b_set
                if not intersection:
                    continue
                if not (a_set <= b_set or b_set <= a_set):
                    self._add(
                        "error",
                        "MonophylyConflict",
                        f"{a_name} vs {b_name}",
                        (
                            f"monophyletic clades '{a_name}' and '{b_name}' overlap "
                            f"({len(intersection)} shared taxa: "
                            f"{sorted(intersection)[:8]}{'...' if len(intersection) > 8 else ''}) "
                            f"but neither contains the other; impossible on a rooted tree"
                        ),
                        "MONOPHYLY_CONFLICT",
                    )

    # -- 4. starting-tree constraint semantics ----------------------
    def check_starting_tree_semantics(self) -> None:
        """Record config-level caveats for stem / non-monophyletic calibrations.

        These are generation-layer issues: ``calibration.constrained_taxa_fragment``
        must target the stem node for stem calibrations and must not emit a
        ``<tmrca>`` constraint when ``monophyletic=False``.  They are surfaced
        as notes (not errors) so Gate 2 does not false-positive; the fix itself
        lives in ``calibration.py``.
        """
        for cal in self.config.calibrations:
            if cal.calibration_method == "stem":
                self.constraint_notes.append(
                    f"calibration '{cal.name}' is a stem calibration: the "
                    f"starting-tree constraint must constrain the stem node "
                    f"(parent of the MRCA), not the crown <taxa> set"
                )
            if cal.monophyletic is False:
                self.constraint_notes.append(
                    f"calibration '{cal.name}' declares monophyletic=false: no "
                    f"<tmrca> starting-tree constraint should be emitted for it"
                )

    # -- 5. lognormal parameterisation hint (BEAUti migration trap) ---------
    def check_lognormal_parameterisation(self) -> None:
        """Flag lognormal calibrations whose mean is silently in log space.

        BEAST reads ``mean``/``stdev`` as log-space parameters unless
        ``meanInRealSpace="true"``; BEAUti's calibration builder defaults to a
        real-space mean.  Users migrating from BEAUti therefore get a very
        different prior when they write ``mean: 12``.  This is an ``info``
        finding (never an error): it states the actual median and 95%
        interval so the value can be checked at a glance.
        """
        for cal in self.config.calibrations:
            dist = cal.distribution
            if dist is None or dist.type != "lognormal":
                continue
            if "meanInRealSpace" in dist.parameters:
                continue
            params = dist.parameters or {}
            try:
                offset = distribution_offset(params, dist.offset)
                mu, sigma = lognormal_mu_sigma(params)
                median = offset + math.exp(mu)
                interval = distribution_quantiles(dist.type, params, offset)
            except (TypeError, ValueError, OverflowError) as exc:
                self._add(
                    "info",
                    "LognormalParameterisation",
                    f"calibration {cal.name}",
                    (
                        f"calibration '{cal.name}' uses a lognormal prior whose "
                        f"parameters could not be interpreted: {exc}"
                    ),
                    "LOGNORMAL_PARAMETERISATION_UNKNOWN",
                )
                continue
            interval_text = (
                f"[{interval[0]:.4g}, {interval[1]:.4g}]"
                if interval is not None
                else "not computable"
            )
            self._add(
                "info",
                "LognormalParameterisation",
                f"calibration {cal.name}",
                (
                    f"calibration '{cal.name}' does not set meanInRealSpace, so "
                    f"BEAST interprets mean={params.get('mean')!r} and "
                    f"stdev={params.get('stdev', params.get('S'))!r} in log space "
                    f"(BEAST's default; BEAUti defaults to a real-space mean). "
                    f"With offset {offset:g} the actual prior median is "
                    f"{median:.4g} and the 95% interval is {interval_text}. "
                    f"Set parameters.meanInRealSpace: true if you intended "
                    f"mean to be the real-space mean (e.g. a date in Ma)."
                ),
                "LOGNORMAL_LOG_SPACE_MEAN",
            )

    def _add(self, severity: str, kind: str, where: str, message: str, code: str) -> None:
        self.findings.append(
            DiagnosticFinding(severity=severity, kind=kind, where=where, message=message, code=code)
        )

    # ------------------------------------------------------------------
    # Sensitivity analysis
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_xml_comments(xml_text: str) -> str:
        """Strip comments, preferring ``xml_writer``'s helper when available.

        ``xml_writer.strip_xml_comments`` is being added by the writer owner; the
        local fallback keeps this path working until then.
        """
        try:
            from .xml_writer import strip_xml_comments as writer_strip

            return writer_strip(xml_text)
        except (ImportError, AttributeError, TypeError):
            # The writer helper is optional / may still be under construction;
            # ``utils.strip_xml_comments`` is the local safe implementation.
            from .utils import strip_xml_comments as utils_strip

            return utils_strip(xml_text)

    def build_sensitivity_run(
        self,
        xml_path: str,
        output_dir: str,
        seed: int = 1,
        jar: Optional[str] = None,
    ) -> List[Path]:
        """Generate N+1 prior-only XML variants and a cross-platform launcher.

        Variants are produced structurally (ElementTree), not by regex: after
        removing a calibration's ``<taxa>``/statistics/priors every dangling
        ``idref`` is removed as well, so each variant is referentially closed.  A ``run_sensitivity.py`` launcher uses ``subprocess.run([...])``
        with absolute paths -- no POSIX shell, every failure is reported and the
        exit status is non-zero.

        Returns:
            The list of variant XML paths created by *this* call.
        """
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        xml_source = Path(xml_path).read_text(encoding="utf-8")
        xml_body = self._strip_xml_comments(xml_source)

        variants: List[Tuple[str, Set[str]]] = [("all", set())]
        for cal in self.config.calibrations:
            variants.append((f"no_{cal.name}", {cal.name}))

        created: List[Path] = []
        manifest: List[Dict[str, str]] = []
        for idx, (variant_name, removed) in enumerate(variants):
            variant_xml = self._prior_only_variant(xml_body, removed)
            stem = safe_filename(variant_name)
            out_name = out_dir / f"sensitivity_{idx:02d}_{stem}.xml"
            out_name.write_text(variant_xml, encoding="utf-8")
            created.append(out_name)
            manifest.append(
                {
                    "xml": str(out_name.resolve()),
                    "prefix": f"sensitivity_{idx:02d}_{stem}",
                    "label": variant_name,
                }
            )

        launcher = out_dir / "run_sensitivity.py"
        launcher.write_text(
            _sensitivity_launcher_source(manifest, seed=seed, jar=jar),
            encoding="utf-8",
        )
        return created

    def _prior_only_variant(self, xml_str: str, removed: Set[str]) -> str:
        """Remove likelihoods and one calibration, then close all references."""
        root, parent_map = _parse_xml_with_parents(xml_str)
        removed_ids: Set[str] = set()
        removed_stats: Set[str] = set()
        for name in removed:
            removed_ids.update({name, f"tmrca({name})", f"monophyly({name})"})
            removed_stats.add(f"tmrca({name})")

        # 1. Drop all treeLikelihood likelihood terms (with or without children).
        for node in list(root.iter()):
            if _local_name(node.tag) == "treeLikelihood" and _is_attached(root, node):
                _detach(parent_map, node)

        # 2. Drop the removed calibration's definitions.
        for node in list(root.iter()):
            if node.get("id") in removed_ids and _is_attached(root, node):
                _detach(parent_map, node)

        # 3. Drop priors that wrap a removed statistic.
        for node in list(root.iter()):
            if not _is_attached(root, node):
                continue
            if any(
                _local_name(child.tag) == "statistic" and child.get("idref") in removed_stats
                for child in list(node)
            ):
                _detach(parent_map, node)

        # 4. Iterate until no idref points at a removed/absent id.
        for _ in range(200):
            declared = {node.get("id") for node in root.iter() if node.get("id")}
            dangling = [
                node
                for node in root.iter()
                if node.get("idref") and node.get("idref") not in declared
            ]
            if not dangling:
                break
            before = sum(1 for _ in root.iter())
            for node in dangling:
                if not _is_attached(root, node):
                    continue
                parent = parent_map.get(node)
                parent_tag = _local_name(parent.tag) if parent is not None else ""
                # References whose container is meaningless without the target
                # are removed as a whole (e.g. <tmrca><taxa idref=.../></tmrca>).
                target = node
                if parent_tag in ("tmrca", "prior") or parent_tag.endswith("Prior"):
                    target = parent
                _detach(parent_map, target)
            after = sum(1 for _ in root.iter())
            if after == before:
                break
        declared = {node.get("id") for node in root.iter() if node.get("id")}
        unresolved = sorted(
            {
                node.get("idref")
                for node in root.iter()
                if node.get("idref") and node.get("idref") not in declared
            }
        )
        if unresolved:
            raise DiagnosticsError(
                f"sensitivity variant still references missing ids: {unresolved[:10]}"
            )

        # 5. Drop operator elements that lost all of their parameters; BEAST
        #    rejects an operator without a target.
        for node in list(root.iter()):
            if _local_name(node.tag).endswith("Operator") and len(node) == 0:
                if _is_attached(root, node):
                    _detach(parent_map, node)
            elif _local_name(node.tag) == "operators" and len(node) == 0:
                if _is_attached(root, node):
                    _detach(parent_map, node)
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + _serialize_xml(root)

    # ------------------------------------------------------------------
    # Visualisation
    # ------------------------------------------------------------------
    def write_density_plots(self, output_dir: str, prefix: str = "prior") -> List[str]:
        """Write one PNG per calibration + a pairwise overlap heatmap."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        created: List[str] = []
        calibrations = [
            c
            for c in self.config.calibrations
            if c.distribution is not None and c.distribution.type != "one_on_x"
        ]
        for cal in calibrations:
            dist = cal.distribution
            assert dist is not None  # filtered above
            samples = sample_distribution(dist.type, dist.parameters, dist.offset)
            if samples is None:
                continue
            q = distribution_quantiles(dist.type, dist.parameters, dist.offset)
            fig, ax = plt.subplots(figsize=(6, 3))
            ax.hist(samples, bins=70, density=True, alpha=0.5, color="#4C72B0")
            if q is not None:
                lo, hi = q
                ax.axvline(lo, color="k", ls="--", lw=0.8)
                ax.axvline(hi, color="k", ls="--", lw=0.8)
                ax.axvspan(lo, hi, alpha=0.12, color="C3")
                ax.annotate("95% interval", xy=(hi, ax.get_ylim()[1] * 0.85), fontsize=7)
            ax.set_title(f"{cal.name} ({dist.type})", fontsize=9)
            ax.tick_params(labelsize=7)
            fig.tight_layout()
            out = out_dir / f"{prefix}_{safe_filename(cal.name)}.png"
            fig.savefig(out, dpi=120)
            plt.close(fig)
            created.append(str(out))

        if len(calibrations) > 1:
            names = [c.name for c in calibrations]
            index = {name: i for i, name in enumerate(names)}
            matrix = np.full((len(names), len(names)), np.nan, dtype=float)
            np.fill_diagonal(matrix, 1.0)
            for entry in self.pair_overlaps:
                i, j = index[entry["a"]], index[entry["b"]]
                matrix[i, j] = matrix[j, i] = entry["overlap"]
            masked = np.ma.masked_invalid(matrix)
            fig, ax = plt.subplots(figsize=(6, 5))
            cmap = plt.get_cmap("YlOrRd").with_extremes(bad="#cccccc")
            im = ax.imshow(masked, cmap=cmap, vmin=0.0, vmax=1.0)
            ax.set_xticks(range(len(names)))
            ax.set_yticks(range(len(names)))
            ax.set_xticklabels(names, rotation=45, ha="right", fontsize=7)
            ax.set_yticklabels(names, fontsize=7)
            fig.colorbar(im, ax=ax, label="prior overlap (0 = disjoint)")
            ax.set_title("Calibration prior overlap", fontsize=10)
            fig.tight_layout()
            heatmap_out = out_dir / f"{prefix}_overlap_heatmap.png"
            fig.savefig(heatmap_out, dpi=120)
            plt.close(fig)
            created.append(str(heatmap_out))
        return created

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    def _audit_sentence(self) -> str:
        """Describe how much was actually audited."""
        n = len(self.config.calibrations)
        pairs = len(self.pair_overlaps)
        if n == 0:
            return "No calibrations configured - nothing was checked."
        if n == 1:
            return (
                "1 calibration configured - nesting and pairwise overlap checks "
                "are not applicable (insufficient data)."
            )
        if not self.findings:
            return (
                f"Checked {n} calibrations and {pairs} pairwise comparison(s): "
                f"no calibration conflicts detected."
            )
        return f"Checked {n} calibrations and {pairs} pairwise comparison(s):"

    def render_text_report(self) -> str:
        lines = [
            "BeastXPy calibration diagnostics report",
            "=======================================",
            f"analysis: {self.config.analysis_name}",
            f"beast_version: {self.config.control.beast_version}",
            f"calibrations: {len(self.config.calibrations)}",
            f"pairwise comparisons: {len(self.pair_overlaps)}",
            "",
            self._audit_sentence(),
        ]
        if self.findings:
            lines.append(f"{len(self.findings)} finding(s):")
            lines.append("")
            for finding in self.findings:
                lines.append(f"[{finding.severity.upper()}] {finding.where} {finding.code}")
                lines.append(f"    {finding.message}")
        if self.constraint_notes:
            lines.append("")
            lines.append("Starting-tree constraint notes:")
            lines.extend(f"  - {note}" for note in self.constraint_notes)
        if self.pair_overlaps:
            lines.append("")
            lines.append("Pairwise prior comparisons (symmetric):")
            for entry in self.pair_overlaps:
                lines.append(
                    f"  {entry['a']} vs {entry['b']}: "
                    f"overlap={entry['overlap']:.3f} "
                    f"divergence={entry['divergence']:.4g} "
                    f"W1={entry['wasserstein']:.4g} "
                    f"[{entry['relation']}/{entry['comparability']}]"
                )
        return "\n".join(lines) + "\n"

    def render_html_report(self) -> str:
        """Self-contained, fully escaped HTML report."""
        analysis = html_escape(str(self.config.analysis_name))
        beast_version = html_escape(str(self.config.control.beast_version))
        findings_html: List[str] = []
        if not self.findings:
            status_class = "ok" if len(self.config.calibrations) >= 2 else "muted"
            findings_html.append(
                f"<p class='{status_class}'>{html_escape(self._audit_sentence())}</p>"
            )
        else:
            for finding in self.findings:
                badge = html_escape(finding.severity)
                findings_html.append(
                    f"<div class='finding {badge}'>"
                    f"<span class='badge'>{badge}</span> "
                    f"<code>{html_escape(finding.kind)}</code> "
                    f"<strong>{html_escape(finding.where or 'config')}</strong> "
                    f"<span class='msg'>{html_escape(finding.message)}</span></div>"
                )
        rows_html: List[str] = []
        for entry in self.pair_overlaps:
            flag = (
                "warn"
                if (
                    entry["relation"] == "nested"
                    and entry["comparability"] == "nested"
                    and entry["divergence"] > self.threshold
                )
                else ("muted" if entry["comparability"] == "disjoint" else "ok")
            )
            rows_html.append(
                f"<tr class='{flag}'><td>{html_escape(entry['a'])}</td>"
                f"<td>{html_escape(entry['b'])}</td>"
                f"<td>{html_escape(entry['relation'])}</td>"
                f"<td>{html_escape(entry['comparability'])}</td>"
                f"<td>{entry['overlap']:.3f}</td>"
                f"<td>{entry['divergence']:.4g}</td>"
                f"<td>{entry['wasserstein']:.4g}</td></tr>"
            )
        notes_html = ""
        if self.constraint_notes:
            notes_html = (
                "<h2>Starting-tree constraint notes</h2><ul>"
                + "".join(f"<li>{html_escape(note)}</li>" for note in self.constraint_notes)
                + "</ul>"
            )
        css = """
        body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:2em auto;max-width:900px;color:#222}
        h1{font-size:1.4em} .ok{color:#1a7f37} .muted{color:#666} .finding{border:1px solid #ddd;border-radius:6px;padding:8px;margin:6px 0}
        .finding.error{border-color:#d1242f;background:#fff0f0}.finding.warning{border-color:#e2a300;background:#fff8e6}
        .finding.info{border-color:#ccc}.badge{font-weight:700;text-transform:uppercase;font-size:.75em}
        table{border-collapse:collapse;width:100%;margin-top:1em}td,th{border:1px solid #ddd;padding:4px 8px;font-size:.9em}
        tr.warn td{background:#fff8e6} tr.ok td{background:#f0fff4} tr.muted td{background:#eeeeee;color:#666}
        .msg{margin-left:6px}
        """
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>BeastXPy diagnostics -- {analysis}</title>
<style>{css}</style></head><body>
<h1>BeastXPy calibration diagnostics</h1>
<p>analysis: <code>{analysis}</code> &middot;
beast_version: <code>{beast_version}</code> &middot;
calibrations: <code>{len(self.config.calibrations)}</code> &middot;
pairwise comparisons: <code>{len(self.pair_overlaps)}</code></p>
<h2>Findings</h2>
{''.join(findings_html)}
{notes_html}
<h2>Pairwise prior comparisons</h2>
<table><tr><th>calibration A</th><th>calibration B</th><th>clade relation</th>
<th>comparability</th><th>overlap (0-1)</th><th>divergence (W1/width)</th><th>W1</th></tr>
{''.join(rows_html) if rows_html else '<tr><td colspan="7">no pairwise comparisons (fewer than two calibrations)</td></tr>'}
</table>
<p style="margin-top:2em;color:#888;font-size:.8em">Validation does not prove biological correctness.</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# XML helpers for the structural sensitivity variants
# ---------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_xml_with_parents(xml_str: str):
    try:
        from defusedxml import ElementTree as DET

        root = DET.fromstring(xml_str)
    except ImportError:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
    parents = {child: parent for parent in root.iter() for child in parent}
    return root, parents


def _serialize_xml(root) -> str:
    try:
        from defusedxml import ElementTree as DET

        return DET.tostring(root, encoding="unicode")
    except ImportError:
        import xml.etree.ElementTree as ET

        return ET.tostring(root, encoding="unicode")


def _is_attached(root, node) -> bool:
    return any(candidate is node for candidate in root.iter())


def _detach(parent_map, node) -> None:
    parent = parent_map.get(node)
    if parent is not None:
        try:
            parent.remove(node)
        except ValueError:
            pass


def _sensitivity_launcher_source(
    manifest: Sequence[Dict[str, str]], seed: int, jar: Optional[str]
) -> str:
    """Source of the cross-platform ``run_sensitivity.py`` launcher."""
    variants_json = json.dumps(list(manifest), indent=4)
    jar_literal = json.dumps(str(Path(jar).resolve()) if jar else "")
    return f'''"""BeastXPy sensitivity launcher (generated).

Runs every prior-only variant with ``java -cp <jar> dr.app.beast.BeastMain``
using ``subprocess.run([...])`` (no POSIX shell).  Failures are printed with
their stderr tail and the process exits non-zero.

Set ``BEASTX_JAR`` (or pass --jar) and optionally ``BEASTX_JAVA``.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VARIANTS = {variants_json}
DEFAULT_JAR = {jar_literal}
SEED = {int(seed)}


def main() -> int:
    # BEAST/JVM diagnostics are UTF-8; do not let a GBK console codepage
    # corrupt the launcher's redirected output on Chinese Windows.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Run BeastXPy sensitivity variants")
    parser.add_argument("--jar", default=os.environ.get("BEASTX_JAR", DEFAULT_JAR))
    parser.add_argument("--java", default=os.environ.get("BEASTX_JAVA", "java"))
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    if not args.jar:
        print("error: no BEAST jar (set BEASTX_JAR or pass --jar)", file=sys.stderr)
        return 2
    jar = str(Path(args.jar).resolve())
    failures = []
    for variant in VARIANTS:
        prefix = variant["prefix"]
        command = [
            args.java, "-cp", jar, "dr.app.beast.BeastMain",
            "-java", "-seed", str(args.seed), "-overwrite",
            "-prefix", prefix, variant["xml"],
        ]
        print("== running", variant["label"], "==")
        try:
            proc = subprocess.run(
                command, cwd=str(HERE), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            failures.append((variant["label"], str(exc)))
            print("failed to start Java:", exc, file=sys.stderr)
            continue
        log_path = HERE / (prefix + ".run.log")
        log_path.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
        if proc.returncode != 0:
            tail = "\\n".join((proc.stderr or proc.stdout or "").splitlines()[-20:])
            failures.append((variant["label"], tail))
            print("variant failed (exit %d): %s" % (proc.returncode, tail), file=sys.stderr)
    if failures:
        print("%d of %d sensitivity variants failed" % (len(failures), len(VARIANTS)), file=sys.stderr)
        return 1
    print("all %d sensitivity variants completed" % len(VARIANTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# Convenience entry points
# ---------------------------------------------------------------------------


def run_diagnostics(config: BEASTConfig, **kwargs: Any) -> DiagnosticReport:
    """Run Gate-2 diagnostics over a parsed configuration."""
    return CalibrationDiagnostics(config, **kwargs).run()


def diagnose_config(
    config: BEASTConfig,
    output_dir: Optional[str] = "diagnostics",
    report_path: Optional[str] = None,
    visualize: bool = True,
    sensitivity: bool = False,
    xml_path: Optional[str] = None,
) -> DiagnosticReport:
    """Full ``diagnose`` pipeline: checks + optional report/plots/sensitivity.

    Directories are created only when an artefact is actually written; the
    report suffix is matched case-insensitively; ``n_sensitivity`` counts the
    variants created by *this* call (no stale directory globs).

    Returns:
        The diagnostic report (with ``extra`` holding created files).
    """
    diagnostics_cfg = config.diagnostics or {}
    threshold = float(diagnostics_cfg.get("overlap_threshold", DEFAULT_OVERLAP_THRESHOLD))
    measure = config.control.overlap_measure
    diag = CalibrationDiagnostics(
        config,
        overlap_threshold=threshold,
        overlap_measure=measure,
        strict=config.control.strict,
    )
    # Run every check before touching the filesystem.
    report = diag.run()

    created: List[str] = []
    out_dir = Path(output_dir) if output_dir else None

    if visualize:
        if out_dir is None:
            raise DiagnosticsError("visualize=True requires an output_dir")
        created.extend(diag.write_density_plots(str(out_dir)))

    if sensitivity:
        if not xml_path:
            raise DiagnosticsError("sensitivity=True requires xml_path")
        if out_dir is None:
            raise DiagnosticsError("sensitivity=True requires an output_dir")
        variants = diag.build_sensitivity_run(xml_path, str(out_dir / "sensitivity"))
        created.extend(str(path) for path in variants)
        report.n_sensitivity = len(variants)

    write_report = bool(report_path) or visualize or sensitivity
    target_report: Optional[Path] = None
    if write_report:
        if report_path:
            target_report = Path(report_path)
        else:
            assert out_dir is not None
            target_report = out_dir / "report.html"
        target_report.parent.mkdir(parents=True, exist_ok=True)
        if target_report.suffix.lower() in (".html", ".htm"):
            target_report.write_text(diag.render_html_report(), encoding="utf-8")
        else:
            target_report.write_text(diag.render_text_report(), encoding="utf-8")
        created.append(str(target_report))

    report.extra["report"] = str(target_report) if target_report is not None else None
    report.extra["created"] = created
    return report
