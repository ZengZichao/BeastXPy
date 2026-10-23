"""Diagnostics-only assessment of BeastXPy run workdirs (the ``assess`` command).

Scans the ``<workdir>/<seed>/`` layout produced by :mod:`beastxpy.runner`
(``seed_<S>_trace.log``, ``seed_<S>_tree.log``, ``run.log``, ``run_meta.json``)
and reports, per chain and across chains:

* per-parameter ESS / ACT / mean / median / 95% HPD (reusing
  :func:`beastxpy.tracestats.compute_trace_stats` and
  :func:`beastxpy.utils.hpd_interval`),
* multi-seed PSRF (Gelman-Rubin R-hat, **split-chain** estimator by default;
  see :func:`beastxpy.tracestats.gelman_rubin_psrf`),
* operator acceptance-rate anomalies parsed from the BEAST operator table in
  ``run.log`` (< 1%, > 95%, never triggered),
* tree-height / clock-rate / ``ucld.mean`` / ``ucld.stdev`` trajectory and HPD
  when those trace columns exist,
* multi-chain topology ASDSF (average standard deviation of split frequencies).

Honesty rules (CONTRACT.md §5 F1 -- these are hard requirements, not style):

* With fewer than two usable chains, PSRF and ASDSF are reported as
  ``status: "not_computable"`` with a plain-language reason.  A value is never
  fabricated, imputed or carried over from another run.
* Any statistic whose input column is absent is reported as
  ``not_computable`` naming the missing column, never as a guessed number.
* ``assess`` is **diagnostics only**: BeastXPy does not assert convergence and
  does not perform model selection.  A ``pass`` verdict means "no configured
  threshold was crossed", not "the chain converged".

This module contains no argparse and no printing; :mod:`beastxpy.cli_assess`
provides the CLI surface and :mod:`beastxpy.api` may wrap it later.
"""

from __future__ import annotations

import html
import json
import math
import re
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import _contracts as C
from .tracestats import (
    TraceError,
    _json_safe,
    asdsf,
    compute_multichain_psrf,
    compute_multichain_rank_diagnostics,
    compute_trace_stats,
    parse_operator_analysis,
    parse_trace_log,
    split_frequencies_from_trees,
)
from .treetools import NexusTree, TreeToolsError
from .utils import hpd_interval

# ---------------------------------------------------------------------------
# Thresholds and honest-reporting constants
# ---------------------------------------------------------------------------

DEFAULT_BURNIN_FRACTION = 0.1
DEFAULT_ESS_THRESHOLD = 200.0
DEFAULT_PSRF_THRESHOLD = 1.01
OPERATOR_LOW_RATE = 0.01
OPERATOR_HIGH_RATE = 0.95
ASDSF_WARN_THRESHOLD = 0.05
TRAJECTORY_POINTS = 20

LIMITATIONS: List[str] = [
    "Diagnostics only: BeastXPy does not claim or assert convergence. A 'pass' "
    "verdict means no user-configured diagnostic threshold was crossed, not "
    "that the MCMC converged.",
    "BeastXPy does not perform model selection; assess never compares models, "
    "marginal likelihoods or Bayes factors (see the separate compare command).",
    "ESS, ACT, PSRF (Gelman-Rubin R-hat) and ASDSF are finite-sample "
    "estimates; on short chains they are noisy and must not be read as proof "
    "of convergence or divergence.",
    "Statistics whose input columns or chains are missing are reported as "
    "not_computable with a reason; BeastXPy never substitutes a guessed value.",
]

# Tree/clock trajectory targets and the trace-column aliases that satisfy
# them (lowercased substring match, first alias wins).  A strict-clock run has
# no ucld.* columns and gets not_computable entries naming them -- that is the
# honest outcome, not an error.
TREE_SUMMARY_TARGETS: List[Tuple[str, List[str]]] = [
    ("tree_height", ["rootheight", "treeheight", "tree.height", "height(all)"]),
    ("clock_rate", ["clockrate", "clock.rate", "mutationrate", "ucldmean", "ucld.mean"]),
    ("ucld_mean", ["ucldmean", "ucld.mean"]),
    ("ucld_stdev", ["ucldstdev", "ucld.stdev"]),
]


class AssessmentError(C.ContractError):
    """Raised for user-facing assessment problems (always carries a fix)."""


# BEAST 1.10.4 writes tree-log lines as
# ``tree STATE_0 [&lnP=...] = [&R] (...);`` -- a metadata bracket sits between
# the state name and the ``=``.  ``treetools.parse_nexus_trees`` matches
# ``tree STATE_N = ...`` without that bracket, so it fails on these logs.  We
# reuse treetools' Newick parser and translate-block parser but scan the tree
# lines with a tolerant regex here rather than editing treetools (which is not
# owned by this feature).  The leading ``[&R]`` in the body is skipped by
# treetools' tokeniser as a comment.
_TREE_LINE_RE = re.compile(
    r"^\s*tree\s+(?:STATE_)?([A-Za-z0-9_.]+)\s*(?:\[[^\]]*\])?\s*=\s*(.*?)\s*$",
    re.IGNORECASE,
)


def load_trees(path: Any) -> List[NexusTree]:
    """Parse a BEAST NEXUS tree log into ``treetools.NexusTree`` items.

    Tolerates the ``tree STATE_N [&metadata] = [&R] (...);`` form that BEAST
    1.10.4 emits.  Newick parsing and the ``Translate`` block are delegated to
    :mod:`beastxpy.treetools`; malformed trees raise :class:`TreeToolsError`.
    """
    from .treetools import _parse_newick, _parse_translate_block

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Tree file not found: {path}")
    lines = file_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()

    trees: List[NexusTree] = []
    translation: Dict[str, str] = {}
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if re.match(r"^translate\b", stripped, re.IGNORECASE):
            translation, index = _parse_translate_block(lines, index)
            continue
        match = _TREE_LINE_RE.match(lines[index])
        if not match:
            index += 1
            continue
        state = match.group(1)
        body = match.group(2).strip()
        if body.endswith(";"):
            body = body[:-1].rstrip()
        try:
            _parse_newick(body, translation)
        except TreeToolsError as exc:
            raise TreeToolsError(f"{path}: tree {state}: {exc}") from None
        trees.append(
            NexusTree(newick=body, translation=dict(translation), metadata={"state": state})
        )
        index += 1
    if not trees:
        raise TreeToolsError(f"{path}: no 'tree STATE_N = ...;' lines found in the tree log")
    return trees


# ---------------------------------------------------------------------------
# Chain discovery
# ---------------------------------------------------------------------------


def discover_chains(workdir: Any) -> List[Dict[str, Any]]:
    """Find ``<workdir>/<seed>/`` chain directories produced by ``beastxpy run``.

    Returns one record per seed directory (sorted by seed) with the resolved
    paths of the four run artefacts; missing artefacts are recorded as
    ``None`` and handled by :func:`assess_chain` instead of raising here.

    Raises:
        AssessmentError: If ``workdir`` does not exist, is not a directory, or
            contains no seed directories at all (exit code 2 territory).
    """
    root = Path(workdir).expanduser()
    if not root.exists():
        raise AssessmentError(
            f"workdir not found: {C.display_path(root)}",
            fix=(
                "Pass the directory you gave to `beastxpy run --workdir`; it "
                "must contain one subdirectory per seed (e.g. 1/, 2/)."
            ),
        )
    if not root.is_dir():
        raise AssessmentError(
            f"workdir is not a directory: {C.display_path(root)}",
            fix="Pass the run workdir (a directory with <seed>/ subdirectories).",
        )
    records: List[Dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            seed = int(child.name)
        except ValueError:
            continue
        trace_candidate = child / f"seed_{seed}_trace.log"
        trace_log: Optional[Path] = trace_candidate if trace_candidate.exists() else None
        if trace_log is None:
            candidates = sorted(child.glob("*_trace.log")) or sorted(child.glob("*.log"))
            trace_log = candidates[0] if candidates else None
        tree_candidate = child / f"seed_{seed}_tree.log"
        tree_log: Optional[Path] = tree_candidate if tree_candidate.exists() else None
        if tree_log is None:
            candidates = sorted(child.glob("*_tree.log")) or sorted(child.glob("*.trees"))
            tree_log = candidates[0] if candidates else None
        run_log = child / "run.log"
        run_meta = child / "run_meta.json"
        records.append(
            {
                "seed": seed,
                "dir": child,
                "trace_log": trace_log if trace_log and trace_log.exists() else None,
                "tree_log": tree_log if tree_log and tree_log.exists() else None,
                "run_log": run_log if run_log.exists() else None,
                "run_meta": run_meta if run_meta.exists() else None,
            }
        )
    if not records:
        raise AssessmentError(
            f"no chains found in {C.display_path(root)}",
            fix=(
                "This does not look like a BeastXPy run workdir: no <seed>/ "
                "subdirectories were found. Run `beastxpy run --config "
                "config.yaml --workdir DIR` first, or point assess at the "
                "directory that holds the seed folders."
            ),
        )
    return records


def _load_run_meta(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _finite(value: Any) -> Optional[float]:
    """Float for JSON, or ``None`` for missing/non-finite values (never NaN)."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _operator_status(record: Dict[str, Any]) -> str:
    """Classify one operator record against the documented anomaly rules."""
    attempts = record.get("attempts")
    rate = record.get("acceptance_rate")
    if attempts is not None and int(attempts) == 0:
        return "never_triggered"
    if rate is None:
        return "not_computable"
    if rate < OPERATOR_LOW_RATE:
        return "low"
    if rate > OPERATOR_HIGH_RATE:
        return "high"
    return "ok"


def _find_column(columns: Sequence[str], aliases: Sequence[str]) -> Optional[str]:
    """First column whose lowercase name contains one of ``aliases``."""
    lowered = {str(col).strip().lower(): col for col in columns}
    for alias in aliases:
        for key, original in lowered.items():
            if alias in key:
                return original
    return None


def _trajectory(values: np.ndarray, points: int = TRAJECTORY_POINTS) -> List[Optional[float]]:
    """Downsample a post-burn-in trajectory to at most ``points`` values."""
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size == 0:
        return []
    points = max(2, int(points))
    if arr.size <= points:
        picked = arr
    else:
        idx = np.linspace(0, arr.size - 1, points).round().astype(int)
        picked = arr[np.unique(idx)]
    return [_finite(v) for v in picked.tolist()]


# ---------------------------------------------------------------------------
# Per-chain assessment
# ---------------------------------------------------------------------------


def assess_chain(
    record: Dict[str, Any],
    *,
    burnin_fraction: Optional[float],
    ess_threshold: float,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Assess one seed directory.

    ``burnin_fraction=None`` selects the automatic Geweke burn-in used by
    :func:`beastxpy.tracestats.compute_trace_stats`; a float selects that
    fixed fraction.  The effective fraction is reported per chain so the two
    modes can never be confused in the JSON.

    Returns ``(chain_entry, extras)`` where ``chain_entry`` is the JSON record
    for ``chains[]`` and ``extras`` carries in-memory data the multichain step
    needs (``post_burnin`` samples per parameter, ``split_frequencies``,
    ``usable`` flag).  Nothing here raises for missing/broken artefacts; the
    problems are recorded in the entry so one bad chain cannot hide the rest.
    """
    seed = record["seed"]
    external = bool(record.get("external"))
    auto_burnin = burnin_fraction is None
    entry: Dict[str, Any] = {
        "seed": seed,
        "status": C.STATUS_OK,
        "trace_log": C.display_path(record["trace_log"]) if record["trace_log"] else None,
        "tree_log": C.display_path(record["tree_log"]) if record["tree_log"] else None,
        "run_log": C.display_path(record["run_log"]) if record["run_log"] else None,
        "run_meta": C.display_path(record["run_meta"]) if record["run_meta"] else None,
        "samples": 0,
        "burnin_mode": "auto" if auto_burnin else "fraction",
        "burnin_fraction": (float(burnin_fraction) if burnin_fraction is not None else None),
        "burnin_samples": 0,
        "parameters": {},
        "operators": [],
        "tree_summary": {},
        "incomplete_reason": None,
        "external": external,
    }
    extras: Dict[str, Any] = {"post_burnin": {}, "split_frequencies": None, "usable": False}

    incomplete: List[str] = []
    meta = _load_run_meta(record["run_meta"])
    if meta is None and record["run_meta"] is not None:
        incomplete.append("run_meta.json exists but is unreadable or not a JSON object")
    if record["run_meta"] is None and not external:
        incomplete.append("run_meta.json is missing (provenance incomplete)")
    if record["run_log"] is None and not external:
        incomplete.append("run.log is missing (no operator table available)")
    if record["tree_log"] is None:
        incomplete.append("tree log is missing (no ASDSF input for this chain)")
    run_status = str((meta or {}).get("status") or "").lower()
    if run_status in ("failed", "timeout"):
        entry["status"] = C.STATUS_FAILED
        entry["incomplete_reason"] = (
            f"run_meta.json records status '{run_status}' (exit code "
            f"{(meta or {}).get('exit_code')}); the MCMC did not complete "
            "successfully, so its diagnostics describe a broken run"
        )
        if incomplete:
            entry["incomplete_reason"] += "; also: " + "; ".join(incomplete)
        return entry, extras
    if incomplete:
        entry["status"] = "incomplete"
        entry["incomplete_reason"] = "; ".join(incomplete)

    if record["trace_log"] is None:
        entry["status"] = C.STATUS_FAILED
        entry["incomplete_reason"] = (
            "no trace log found in the seed directory; nothing can be " "computed for this chain"
        )
        return entry, extras

    try:
        parsed = parse_trace_log(str(record["trace_log"]))
    except (TraceError, FileNotFoundError, OSError, ValueError) as exc:
        entry["status"] = C.STATUS_FAILED
        entry["incomplete_reason"] = f"trace log could not be parsed: {exc}"
        return entry, extras

    n_samples = int(parsed["n_samples"])
    chain_ranges = [(c["start"], c["end"]) for c in parsed.get("chains", [])] or None
    burnin_samples: Optional[int] = (
        int(math.floor(burnin_fraction * n_samples)) if burnin_fraction is not None else None
    )
    entry["samples"] = n_samples
    stats = compute_trace_stats(
        parsed["values"],
        burnin=burnin_samples,
        ess_threshold=ess_threshold,
        chains=chain_ranges,
    )
    entry["burnin_samples"] = int(stats.get("burnin", 0))
    entry["burnin_status"] = stats.get("burnin_status", "explicit")
    if n_samples:
        entry["burnin_fraction"] = entry["burnin_samples"] / float(n_samples)

    parameters: Dict[str, Any] = {}
    for column in stats["columns"]:
        name = str(column["name"])
        ess = _finite(column.get("ess"))
        parameters[name] = {
            "ess": ess,
            "act": _finite(column.get("act")),
            "mean": _finite(column.get("mean")),
            "median": _finite(column.get("median")),
            "hpd_lower": _finite(column.get("hpd_lower")),
            "hpd_upper": _finite(column.get("hpd_upper")),
            "ess_ok": bool(ess is not None and ess >= ess_threshold),
            "per_chain": column.get("per_chain", []),
        }
    entry["parameters"] = parameters

    # Post-burn-in samples per parameter (in memory only; feeds PSRF).
    for name in parameters:
        pieces: List[np.ndarray] = []
        column = np.asarray(parsed["values"][name], dtype=float)
        ranges = chain_ranges or [(0, n_samples)]
        for start, end in ranges:
            keep = min(entry["burnin_samples"], max(0, end - start - 2))
            piece = column[start + keep : end]
            piece = piece[np.isfinite(piece)]
            if piece.size:
                pieces.append(piece)
        if pieces:
            extras["post_burnin"][name] = np.concatenate(pieces)

    # Operator table from run.log.
    if record["run_log"] is not None:
        try:
            text = Path(record["run_log"]).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            text = ""
            entry["incomplete_reason"] = (
                entry["incomplete_reason"] + "; " if entry["incomplete_reason"] else ""
            ) + f"run.log could not be read: {exc}"
        operators = []
        for record_op in parse_operator_analysis(text):
            status = _operator_status(record_op)
            operators.append(
                {
                    "name": record_op.get("name"),
                    "tuning": _finite(record_op.get("tuning")),
                    "attempts": record_op.get("attempts"),
                    "accepted": record_op.get("accepted"),
                    "acceptance_rate": _finite(record_op.get("acceptance_rate")),
                    "status": status,
                }
            )
        entry["operators"] = operators

    # Tree/clock trajectory summaries from trace columns.
    columns = list(parsed["columns"])
    tree_summary: Dict[str, Any] = {}
    for target, aliases in TREE_SUMMARY_TARGETS:
        column = _find_column(columns, aliases)
        if column is None:
            tree_summary[target] = {
                "status": C.STATUS_NOT_COMPUTABLE,
                "column": None,
                "reason": (
                    f"no trace column matching '{aliases[0]}' is present in "
                    f"{Path(str(record['trace_log'])).name} (available columns: "
                    f"{', '.join(columns[:12])}{'...' if len(columns) > 12 else ''}); "
                    "no value is reported"
                ),
            }
            continue
        series = np.asarray(parsed["values"][column], dtype=float)
        kept = series[entry["burnin_samples"] :]
        kept = kept[np.isfinite(kept)]
        if kept.size == 0:
            tree_summary[target] = {
                "status": C.STATUS_NOT_COMPUTABLE,
                "column": column,
                "reason": f"column '{column}' has no finite post-burn-in samples",
            }
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            lo, hi = hpd_interval(kept, alpha=0.05)
        half = kept.size // 2
        tree_summary[target] = {
            "status": C.STATUS_OK,
            "column": column,
            "n": int(kept.size),
            "mean": _finite(kept.mean()),
            "median": _finite(np.median(kept)),
            "hpd_lower": _finite(lo),
            "hpd_upper": _finite(hi),
            "first_half_mean": _finite(kept[:half].mean()) if half else None,
            "second_half_mean": _finite(kept[half:].mean()) if kept.size - half else None,
            "trajectory": _trajectory(kept),
        }
    entry["tree_summary"] = tree_summary

    # Split frequencies for ASDSF (best effort; failures stay recorded).
    if record["tree_log"] is not None:
        try:
            trees = load_trees(record["tree_log"])
            effective_fraction = float(entry["burnin_fraction"] or 0.0)
            tree_burnin = int(math.floor(effective_fraction * len(trees)))
            tree_burnin = min(tree_burnin, max(0, len(trees) - 1))
            freqs = split_frequencies_from_trees(trees, burnin=tree_burnin)
            extras["split_frequencies"] = freqs
            entry["tree_summary"]["n_trees"] = len(trees)
            entry["tree_summary"]["n_trees_after_burnin"] = len(trees) - tree_burnin
            entry["tree_summary"]["n_nontrivial_splits"] = len(freqs)
        except (TreeToolsError, TraceError, FileNotFoundError, OSError, ValueError) as exc:
            entry["tree_summary"]["n_trees"] = None
            entry["tree_summary"]["split_status"] = C.STATUS_NOT_COMPUTABLE
            entry["tree_summary"]["split_reason"] = f"tree log could not be used for ASDSF: {exc}"

    if entry["status"] == C.STATUS_OK or entry["status"] == "incomplete":
        extras["usable"] = bool(parameters)
    return entry, extras


# ---------------------------------------------------------------------------
# Workdir-level assessment
# ---------------------------------------------------------------------------


def assess_workdir(
    workdir: Any,
    *,
    burnin: Any = DEFAULT_BURNIN_FRACTION,
    ess: float = DEFAULT_ESS_THRESHOLD,
    psrf: float = DEFAULT_PSRF_THRESHOLD,
    strict: bool = False,
    _records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Assess every chain under ``workdir`` and return the JSON envelope.

    Args:
        workdir: Directory containing ``<seed>/`` subdirectories.
        burnin: Burn-in *fraction* (0 <= burnin < 1) applied inside every
            chain of every trace log, or ``"auto"`` to use the Geweke-based
            automatic burn-in of :func:`beastxpy.tracestats.compute_trace_stats`.
            The effective per-chain fraction is recorded in ``chains[]``.
        ess: ESS threshold; parameters below it are reported as errors.
        psrf: PSRF (R-hat) threshold; values above it are reported as
            warnings.
        strict: Recorded in the envelope; the CLI maps warn -> exit 1.

    Returns:
        A ``beastxpy.assessment/v1`` envelope (see CONTRACT.md §5 F1).

    Raises:
        AssessmentError: workdir missing/unreadable or no chains found.
    """
    if isinstance(burnin, str):
        if burnin.strip().lower() != "auto":
            raise AssessmentError(
                f"--burnin must be a fraction in [0, 1) or 'auto', got {burnin!r}",
                fix="Pass --burnin 0.1 or --burnin auto.",
            )
        burnin_value: Optional[float] = None
        burnin_record: Any = "auto"
    else:
        if not (0.0 <= float(burnin) < 1.0):
            raise AssessmentError(
                f"--burnin must be a fraction in [0, 1) or 'auto', got {burnin!r}",
                fix="Pass a fraction of the chain to discard, e.g. --burnin 0.1.",
            )
        burnin_value = float(burnin)
        burnin_record = burnin_value
    if float(ess) <= 0.0:
        raise AssessmentError(
            f"--ess must be > 0, got {ess!r}",
            fix="Pass the minimum acceptable effective sample size, e.g. --ess 200.",
        )
    if float(psrf) < 1.0:
        raise AssessmentError(
            f"--psrf must be >= 1.0, got {psrf!r}",
            fix="Pass an R-hat acceptance threshold, e.g. --psrf 1.01.",
        )

    root = Path(workdir).expanduser()
    records = list(_records) if _records is not None else discover_chains(root)

    chains: List[Dict[str, Any]] = []
    extras_all: List[Dict[str, Any]] = []
    for record in records:
        entry, extras = assess_chain(record, burnin_fraction=burnin_value, ess_threshold=float(ess))
        chains.append(entry)
        extras_all.append(extras)

    findings: List[Dict[str, Any]] = []
    for entry in chains:
        seed = entry["seed"]
        if entry["status"] == C.STATUS_FAILED:
            findings.append(
                C.finding(
                    "chain-failed",
                    C.SEVERITY_ERROR,
                    f"seed {seed}: {entry['incomplete_reason']}",
                    category="chain",
                    location={"seed": seed},
                    fix=(
                        f"Inspect {entry['run_log'] or 'the seed directory'} and re-run "
                        f"this seed (beastxpy run --seeds {seed} --rerun-successful) "
                        "once the cause is fixed"
                    ),
                )
            )
        elif entry["status"] == "incomplete":
            findings.append(
                C.finding(
                    "chain-incomplete",
                    C.SEVERITY_WARNING,
                    f"seed {seed}: {entry['incomplete_reason']}",
                    category="chain",
                    location={"seed": seed},
                    fix=(
                        "Assess a complete run directory (all four artefacts written "
                        "by `beastxpy run`) for full diagnostics"
                    ),
                )
            )
        if entry.get("burnin_status") == "not_computable":
            findings.append(
                C.finding(
                    "burnin-not-computable",
                    C.SEVERITY_WARNING,
                    f"seed {seed}: automatic burn-in is not computable; no samples "
                    "were discarded and burn-in=0 is not an estimate of convergence",
                    category="burnin",
                    location={"seed": seed},
                    fix=(
                        "Pass an explicit burn-in fraction (for example "
                        "`--burnin 0.1`) or run a longer chain so the Geweke "
                        "diagnostic is defined"
                    ),
                )
            )
        for name, params in entry["parameters"].items():
            if params["ess"] is None:
                findings.append(
                    C.finding(
                        "ess-not-computable",
                        C.SEVERITY_WARNING,
                        f"seed {seed}: ESS for '{name}' is not computable from "
                        f"{entry['samples']} samples",
                        category="ess",
                        location={"seed": seed, "parameter": name},
                        fix="Run a longer chain or log more often so ESS becomes estimable",
                    )
                )
            elif not params["ess_ok"]:
                findings.append(
                    C.finding(
                        "ess-below-threshold",
                        C.SEVERITY_ERROR,
                        f"seed {seed}: ESS {params['ess']:.1f} for '{name}' is below "
                        f"the requested threshold {ess:g}",
                        category="ess",
                        location={"seed": seed, "parameter": name},
                        value=params["ess"],
                        threshold=ess,
                        fix=(
                            "Run a longer chain (increase mcmc.chain_length) and/or "
                            "check operator tuning; or, if this threshold was "
                            "deliberately strict, lower --ess"
                        ),
                    )
                )
        for operator in entry["operators"]:
            status = operator["status"]
            name = operator["name"]
            if status == "never_triggered":
                findings.append(
                    C.finding(
                        "operator-never-triggered",
                        C.SEVERITY_WARNING,
                        f"seed {seed}: operator '{name}' was never triggered " "(0 attempts)",
                        category="operator",
                        location={"seed": seed, "operator": name},
                        fix=(
                            "Check the operator schedule/weights; an operator that "
                            "never fires contributes no mixing"
                        ),
                    )
                )
            elif status == "low":
                findings.append(
                    C.finding(
                        "operator-acceptance-low",
                        C.SEVERITY_WARNING,
                        f"seed {seed}: operator '{name}' acceptance rate "
                        f"{operator['acceptance_rate']:.4f} is below "
                        f"{OPERATOR_LOW_RATE:.0%}",
                        category="operator",
                        location={"seed": seed, "operator": name},
                        value=operator["acceptance_rate"],
                        threshold=OPERATOR_LOW_RATE,
                        fix=(
                            "Consider adjusting the operator's tuning/weight; "
                            "very low acceptance wastes MCMC steps"
                        ),
                    )
                )
            elif status == "high":
                findings.append(
                    C.finding(
                        "operator-acceptance-high",
                        C.SEVERITY_WARNING,
                        f"seed {seed}: operator '{name}' acceptance rate "
                        f"{operator['acceptance_rate']:.4f} is above "
                        f"{OPERATOR_HIGH_RATE:.0%}",
                        category="operator",
                        location={"seed": seed, "operator": name},
                        value=operator["acceptance_rate"],
                        threshold=OPERATOR_HIGH_RATE,
                        fix=(
                            "Consider increasing the operator's step size/tuning; "
                            "near-total acceptance usually means moves are too small"
                        ),
                    )
                )
        for target, summary in entry["tree_summary"].items():
            if isinstance(summary, dict) and summary.get("status") == C.STATUS_NOT_COMPUTABLE:
                findings.append(
                    C.finding(
                        "tree-summary-not-computable",
                        C.SEVERITY_INFO,
                        f"seed {seed}: {target}: {summary.get('reason')}",
                        category="tree-summary",
                        location={"seed": seed, "target": target},
                        fix=(
                            "Expected for models that do not log this column "
                            "(e.g. strict clocks have no ucld.* columns); no "
                            "action needed unless you expected it"
                        ),
                    )
                )

    # Multichain step: PSRF and ASDSF, with the >= 2-chain honesty rule.
    usable_extras = [e for c, e in zip(chains, extras_all) if e["usable"]]
    psrf_result = compute_multichain_psrf(
        [e["post_burnin"] for e in usable_extras],
        split=True,
        threshold=float(psrf),
    )
    rank_result = compute_multichain_rank_diagnostics(
        [e["post_burnin"] for e in usable_extras],
        psrf_threshold=float(psrf),
        ess_threshold=float(ess),
    )
    for name, value in psrf_result["values"].items():
        modern = rank_result["values"].get(name) or {}
        value["classic_psrf"] = value.get("psrf")
        value["rank_psrf"] = modern.get("rank_psrf")
        value["ess_bulk"] = modern.get("ess_bulk")
        value["ess_tail"] = modern.get("ess_tail")
        value["ess_bulk_ok"] = modern.get("ess_bulk_ok")
        value["ess_tail_ok"] = modern.get("ess_tail_ok")
    if psrf_result["status"] == C.STATUS_NOT_COMPUTABLE:
        findings.append(
            C.finding(
                "psrf-not-computable",
                C.SEVERITY_INFO,
                f"PSRF is not computable: {psrf_result['reason']}",
                category="multichain",
                fix="Run at least two seeds and re-assess; no PSRF value is reported",
            )
        )
    else:
        for name, value in psrf_result["values"].items():
            rank_value = value.get("rank_psrf")
            classic_value = value.get("classic_psrf")
            checked = rank_value if rank_value is not None else classic_value
            estimator = (
                "rank-normalized split R-hat"
                if rank_value is not None
                else ("split-chain R-hat (rank normalization not computable)")
            )
            if checked is None:
                findings.append(
                    C.finding(
                        "psrf-not-computable-parameter",
                        C.SEVERITY_INFO,
                        f"PSRF for '{name}': {value['reason']}",
                        category="multichain",
                        location={"parameter": name},
                        fix="No action; the value is reported as not computable, not guessed",
                    )
                )
            elif checked > float(psrf):
                findings.append(
                    C.finding(
                        "psrf-above-threshold",
                        C.SEVERITY_WARNING,
                        f"PSRF ({estimator}) for '{name}' is "
                        f"{checked:.4f}, above the threshold {psrf:g}",
                        category="multichain",
                        location={"parameter": name},
                        value=checked,
                        threshold=psrf,
                        fix=(
                            "Chains disagree beyond the threshold: run longer "
                            "chains, check operator tuning, and re-assess; this "
                            "is a diagnostic signal, not a convergence proof"
                        ),
                    )
                )
            else:
                bulk = value.get("ess_bulk")
                tail = value.get("ess_tail")
                if bulk is not None and bulk < float(ess):
                    findings.append(
                        C.finding(
                            "bulk-ess-below-threshold",
                            C.SEVERITY_WARNING,
                            f"bulk ESS for '{name}' is {bulk:.1f}, below the " f"threshold {ess:g}",
                            category="multichain",
                            location={"parameter": name},
                            value=bulk,
                            threshold=float(ess),
                            fix=(
                                "The chain is not mixing well enough to estimate "
                                "the posterior location precisely; run longer "
                                "and re-assess"
                            ),
                        )
                    )
                if tail is not None and tail < float(ess):
                    findings.append(
                        C.finding(
                            "tail-ess-below-threshold",
                            C.SEVERITY_WARNING,
                            f"tail ESS for '{name}' is {tail:.1f}, below the " f"threshold {ess:g}",
                            category="multichain",
                            location={"parameter": name},
                            value=tail,
                            threshold=float(ess),
                            fix=(
                                "The chain samples the posterior tails poorly; "
                                "run longer and re-assess"
                            ),
                        )
                    )

    split_runs = [e["split_frequencies"] for e in usable_extras if e["split_frequencies"]]
    if len(split_runs) < 2:
        reason = (
            "ASDSF compares split frequencies between at least 2 tree logs; "
            f"{len(split_runs)} usable tree log(s) with non-trivial splits "
            "were found. No value is reported because none exists."
        )
        if len(usable_extras) >= 2:
            reason += (
                " The chains exist but their tree logs yielded no non-trivial "
                "splits (too few taxa or too little topological variation)."
            )
        asdsf_result: Dict[str, Any] = {
            "status": C.STATUS_NOT_COMPUTABLE,
            "value": None,
            "reason": reason,
        }
        findings.append(
            C.finding(
                "asdsf-not-computable",
                C.SEVERITY_INFO,
                f"ASDSF is not computable: {reason}",
                category="multichain",
                fix=(
                    "Run at least two seeds with tree logging and re-assess; "
                    "no ASDSF value is reported"
                ),
            )
        )
    else:
        value = asdsf(split_runs)
        if value is None:
            asdsf_result = {
                "status": C.STATUS_NOT_COMPUTABLE,
                "value": None,
                "reason": (
                    "no non-trivial splits are shared or observed across the "
                    "tree logs; ASDSF is undefined and no value is reported"
                ),
            }
        else:
            asdsf_result = {
                "status": C.STATUS_OK,
                "value": float(value),
                "reason": None,
            }
            if value > ASDSF_WARN_THRESHOLD:
                findings.append(
                    C.finding(
                        "asdsf-high",
                        C.SEVERITY_WARNING,
                        f"ASDSF across {len(split_runs)} tree logs is {value:.4f}, "
                        f"above the {ASDSF_WARN_THRESHOLD:g} screening threshold",
                        category="multichain",
                        value=float(value),
                        threshold=ASDSF_WARN_THRESHOLD,
                        fix=(
                            "Chains are sampling noticeably different topologies: "
                            "run longer chains and re-assess; ASDSF is a screening "
                            "diagnostic, not a convergence proof"
                        ),
                    )
                )

    multichain = {
        "available": len(usable_extras) >= 2,
        "n_chains": len(usable_extras),
        "psrf": {
            "status": psrf_result["status"],
            "estimator": psrf_result["estimator"],
            "reason": psrf_result["reason"],
            "values": psrf_result["values"],
        },
        "rank_diagnostics": {
            "status": rank_result["status"],
            "estimator": rank_result["estimator"],
            "ess_estimator": rank_result["ess_estimator"],
            "reason": rank_result["reason"],
            "values": rank_result["values"],
        },
        "asdsf": asdsf_result,
    }

    reasons = C.sort_findings(findings)
    counts = C.count_severities(reasons)
    verdict = C.verdict_from_counts(counts)
    envelope = C.envelope(
        C.SCHEMA_ASSESSMENT,
        workdir=C.display_path(root),
        verdict=verdict,
        strict=bool(strict),
        thresholds={
            "burnin": burnin_record,
            "ess": float(ess),
            "psrf": float(psrf),
            "operator_low": OPERATOR_LOW_RATE,
            "operator_high": OPERATOR_HIGH_RATE,
        },
        counts=counts,
        chains=chains,
        multichain=multichain,
        reasons=reasons,
        limitations=list(LIMITATIONS),
    )
    return _json_safe(envelope)


def assess_trace_files(
    trace_paths: Sequence[Any],
    tree_paths: Optional[Sequence[Any]] = None,
    *,
    burnin: Any = DEFAULT_BURNIN_FRACTION,
    ess: float = DEFAULT_ESS_THRESHOLD,
    psrf: float = DEFAULT_PSRF_THRESHOLD,
    strict: bool = False,
) -> Dict[str, Any]:
    """Assess bare BEAST trace logs produced outside a BeastXPy workdir.

    ``--trace`` may be repeated; every path becomes one chain (labelled
    ``1..N`` in input order).  ``tree_paths`` is optional and, when given, must
    provide one tree log per trace in the same order (ASDSF then has an
    input).  The returned envelope has the same schema as
    :func:`assess_workdir`; ``chains[].external`` marks the source and the
    operator table is empty because no ``run.log`` is available.
    """
    traces = [Path(path).expanduser() for path in trace_paths]
    if not traces:
        raise AssessmentError(
            "no --trace files given",
            fix="Pass at least one trace log: --trace seed_1_trace.log.",
        )
    for path in traces:
        if not path.is_file():
            raise AssessmentError(
                f"trace log not found: {C.display_path(path)}",
                fix="Pass an existing BEAST .log file (one chain per --trace).",
            )
    trees = [Path(path).expanduser() for path in (tree_paths or [])]
    if trees and len(trees) != len(traces):
        raise AssessmentError(
            f"--trees has {len(trees)} path(s) but --trace has {len(traces)}; "
            "provide one tree log per trace in the same order",
            fix="Repeat --trees for every --trace, or omit --trees.",
        )
    for path in trees:
        if not path.is_file():
            raise AssessmentError(
                f"tree log not found: {C.display_path(path)}",
                fix="Pass an existing BEAST .trees file or omit --trees.",
            )
    records: List[Dict[str, Any]] = []
    for index, trace in enumerate(traces, start=1):
        records.append(
            {
                "seed": index,
                "dir": trace.parent,
                "trace_log": trace,
                "tree_log": trees[index - 1] if trees else None,
                "run_log": None,
                "run_meta": None,
                "external": True,
            }
        )
    return assess_workdir(
        traces[0].parent,
        burnin=burnin,
        ess=ess,
        psrf=psrf,
        strict=strict,
        _records=records,
    )


def exit_code_for(doc: Dict[str, Any], *, strict: bool) -> int:
    """Map an assessment envelope to the documented assess exit code.

    0 = pass or warn; 1 = warn under ``--strict``; 3 = fail.  (Exit 2 is
    produced by the CLI when :func:`assess_workdir` raises.)
    """
    verdict = doc.get("verdict")
    if verdict == C.VERDICT_FAIL:
        return C.EXIT_FAILED
    if verdict == C.VERDICT_WARN and strict:
        return C.EXIT_USAGE
    return C.EXIT_OK


# ---------------------------------------------------------------------------
# Rendering (text summary and self-contained HTML)
# ---------------------------------------------------------------------------


def _fmt(value: Any, spec: str = ".5g") -> str:
    number = _finite(value)
    return "n/a" if number is None else format(number, spec)


def _ascii(value: Any) -> str:
    """Console-safe rendering: escape only what the console cannot encode."""
    return C.console_text(value)


def render_text_summary(doc: Dict[str, Any]) -> List[str]:
    """Human-readable summary lines (ASCII only) for stdout."""
    lines: List[str] = []
    lines.append(
        f"assess: {_ascii(doc.get('workdir'))} -> verdict {doc.get('verdict')} "
        f"({len(doc.get('chains') or [])} chain(s), strict={doc.get('strict')})"
    )
    thresholds = doc.get("thresholds") or {}
    lines.append(
        "thresholds: burnin={burnin} ess={ess} psrf={psrf} "
        "operator=[{operator_low}, {operator_high}]".format(**thresholds)
    )
    for chain in doc.get("chains") or []:
        lines.append(
            f"  seed {chain['seed']}: status={chain['status']} "
            f"samples={chain['samples']} burnin={chain['burnin_samples']} "
            f"parameters={len(chain['parameters'])} operators={len(chain['operators'])}"
        )
        if chain.get("incomplete_reason"):
            lines.append(f"    note: {_ascii(chain['incomplete_reason'])}")
    multichain = doc.get("multichain") or {}
    psrf = multichain.get("psrf") or {}
    lines.append(f"  multichain: {multichain.get('n_chains')} usable chain(s)")
    if psrf.get("status") == C.STATUS_OK:
        worst = max(
            (v["psrf"] for v in psrf["values"].values() if v.get("psrf") is not None),
            default=None,
        )
        lines.append(f"    PSRF ({psrf.get('estimator')}): max R-hat {_fmt(worst, '.4f')}")
        worst_rank = max(
            (v["rank_psrf"] for v in psrf["values"].values() if v.get("rank_psrf") is not None),
            default=None,
        )
        if worst_rank is not None:
            lines.append(
                f"    rank-normalized split R-hat: max {_fmt(worst_rank, '.4f')} "
                "(Vehtari et al. 2021)"
            )
        lines.append(
            "    bulk/tail ESS per parameter: see the JSON " "(multichain.rank_diagnostics)"
        )
    else:
        lines.append(f"    PSRF: not_computable -- {_ascii(psrf.get('reason'))}")
    asdsf_block = multichain.get("asdsf") or {}
    if asdsf_block.get("status") == C.STATUS_OK:
        lines.append(f"    ASDSF: {_fmt(asdsf_block.get('value'), '.4f')}")
    else:
        lines.append(f"    ASDSF: not_computable -- {_ascii(asdsf_block.get('reason'))}")
    reasons = doc.get("reasons") or []
    counts = doc.get("counts") or {}
    lines.append(
        f"  reasons: {counts.get('error', 0)} error(s), "
        f"{counts.get('warning', 0)} warning(s), {counts.get('info', 0)} info"
    )
    for reason in reasons[:10]:
        lines.append(f"    [{reason['severity']}] {reason['id']}: {_ascii(reason['message'])}")
    if len(reasons) > 10:
        lines.append(f"    ... and {len(reasons) - 10} more")
    lines.append("  limitations:")
    for limitation in doc.get("limitations") or []:
        lines.append(f"    - {_ascii(limitation)}")
    return lines


def render_html(doc: Dict[str, Any]) -> str:
    """Render the assessment as a single self-contained HTML page.

    No external resources; every dynamic string is escaped with
    :func:`html.escape` (``quote=True``) so hostile parameter names or paths
    in the input can never inject markup.
    """

    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    parts: List[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head><meta charset="utf-8">')
    parts.append(f"<title>BeastXPy assess -- {esc(doc.get('workdir'))}</title>")
    parts.append(
        "<style>body{font-family:sans-serif;margin:2em}table{border-collapse:"
        "collapse}td,th{border:1px solid #999;padding:2px 6px;font-size:0.9em}"
        ".error{color:#b00020}.warning{color:#a06000}.info{color:#404040}"
        "</style></head><body>"
    )
    parts.append("<h1>BeastXPy assess</h1>")
    parts.append(f"<p>workdir: <code>{esc(doc.get('workdir'))}</code></p>")
    parts.append(
        f"<p>verdict: <strong class=\"{esc(doc.get('verdict'))}\">"
        f"{esc(doc.get('verdict'))}</strong> "
        f"(schema {esc(doc.get('schema_version'))}, generated {esc(doc.get('generated_at'))})</p>"
    )
    thresholds = doc.get("thresholds") or {}
    if thresholds:
        parts.append("<h2>Thresholds used</h2><table><tr><th>setting</th><th>value</th></tr>")
        for key in sorted(thresholds):
            parts.append(f"<tr><td>{esc(key)}</td><td>{esc(thresholds[key])}</td></tr>")
        parts.append("</table>")
        parts.append(
            "<p><em>A pass verdict means no configured threshold was crossed; "
            "it is not a proof of convergence.</em></p>"
        )
    parts.append(
        "<h2>Chains</h2><table><tr><th>seed</th><th>status</th>"
        "<th>samples</th><th>burn-in</th><th>parameters</th>"
        "<th>operators</th><th>note</th></tr>"
    )
    for chain in doc.get("chains") or []:
        parts.append(
            "<tr>"
            f"<td>{esc(chain.get('seed'))}</td>"
            f"<td>{esc(chain.get('status'))}</td>"
            f"<td>{esc(chain.get('samples'))}</td>"
            f"<td>{esc(chain.get('burnin_samples'))}</td>"
            f"<td>{esc(len(chain.get('parameters') or {}))}</td>"
            f"<td>{esc(len(chain.get('operators') or {}))}</td>"
            f"<td>{esc(chain.get('incomplete_reason'))}</td>"
            "</tr>"
        )
    parts.append("</table>")
    for chain in doc.get("chains") or []:
        parts.append(f"<h2>Seed {esc(chain.get('seed'))} -- parameters</h2>")
        parts.append(
            "<table><tr><th>parameter</th><th>ESS</th><th>ACT</th><th>mean</th>"
            "<th>median</th><th>95% HPD</th></tr>"
        )
        for name, params in (chain.get("parameters") or {}).items():
            parts.append(
                "<tr>"
                f"<td>{esc(name)}</td>"
                f"<td>{esc(_fmt(params.get('ess'), '.1f'))}</td>"
                f"<td>{esc(_fmt(params.get('act'), '.2f'))}</td>"
                f"<td>{esc(_fmt(params.get('mean')))}</td>"
                f"<td>{esc(_fmt(params.get('median')))}</td>"
                f"<td>[{esc(_fmt(params.get('hpd_lower')))}, "
                f"{esc(_fmt(params.get('hpd_upper')))}]</td>"
                "</tr>"
            )
        parts.append("</table>")
    multichain = doc.get("multichain") or {}
    parts.append("<h2>Multichain</h2><ul>")
    psrf = multichain.get("psrf") or {}
    parts.append(f"<li>usable chains: {esc(multichain.get('n_chains'))}</li>")
    parts.append(
        f"<li>PSRF status: {esc(psrf.get('status'))} "
        f"(estimator: {esc(psrf.get('estimator'))})</li>"
    )
    if psrf.get("reason"):
        parts.append(f"<li>PSRF reason: {esc(psrf.get('reason'))}</li>")
    asdsf_block = multichain.get("asdsf") or {}
    parts.append(
        f"<li>ASDSF status: {esc(asdsf_block.get('status'))}, "
        f"value: {esc(_fmt(asdsf_block.get('value'), '.4f'))}</li>"
    )
    if asdsf_block.get("reason"):
        parts.append(f"<li>ASDSF reason: {esc(asdsf_block.get('reason'))}</li>")
    parts.append("</ul>")
    psrf_values = psrf.get("values") or {}
    if psrf_values:
        parts.append(
            "<h2>Multichain per-parameter diagnostics</h2>"
            "<table><tr><th>parameter</th><th>split R-hat (classic)</th>"
            "<th>rank-normalized split R-hat</th><th>bulk ESS</th>"
            "<th>tail ESS</th><th>not computable because</th></tr>"
        )
        for name in sorted(psrf_values):
            value = psrf_values.get(name) or {}
            parts.append(
                f"<tr><td>{esc(name)}</td>"
                f"<td>{esc(_fmt(value.get('classic_psrf'), '.4f'))}</td>"
                f"<td>{esc(_fmt(value.get('rank_psrf'), '.4f'))}</td>"
                f"<td>{esc(_fmt(value.get('ess_bulk'), '.1f'))}</td>"
                f"<td>{esc(_fmt(value.get('ess_tail'), '.1f'))}</td>"
                f"<td>{esc(value.get('reason'))}</td></tr>"
            )
        parts.append("</table>")
    parts.append("<h2>Reasons</h2><ul>")
    for reason in doc.get("reasons") or []:
        parts.append(
            f"<li class=\"{esc(reason.get('severity'))}\">"
            f"[{esc(reason.get('severity'))}] {esc(reason.get('id'))}: "
            f"{esc(reason.get('message'))}"
            + (f" <em>fix:</em> {esc(reason.get('fix'))}" if reason.get("fix") else "")
            + "</li>"
        )
    parts.append("</ul>")
    parts.append("<h2>Limitations</h2><ul>")
    for limitation in doc.get("limitations") or []:
        parts.append(f"<li>{esc(limitation)}</li>")
    parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def write_html(doc: Dict[str, Any], path: Any) -> Path:
    """Write the HTML rendering to ``path`` (UTF-8, parents created)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(doc), encoding="utf-8")
    return target
