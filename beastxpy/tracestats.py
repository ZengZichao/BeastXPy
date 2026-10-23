"""Trace-file statistics for BeastXPy (``trace`` subcommand).

Parses BEAST 1 ``.log`` files (comment lines + tab-separated column headers)
and computes Tracer-inspired diagnostics in pure Python / numpy:

* automatic burn-in detection (Geweke test, conservatively taking the larger
  of the two candidate burn-ins),
* autocorrelation time (Geyer initial-positive-sequence / monotone window
  estimator with an adaptive maximum lag),
* effective sample size (ESS),
* mean / geometric mean / median / standard deviation,
* 95% highest posterior density interval,
* mode (observed-value estimate for discrete samples, histogram otherwise).

Policies (documented behaviour, exercised by ``tests/test_numerics``):

* **Non-finite samples.** ``NaN``/``Inf`` values are *excluded* from every
  statistic.  ACT/ESS are computed on the finite subsequence only and
  ``n_nonfinite`` is recorded per column; a single ``NaN`` can therefore never
  turn ESS into the nominal sample count.  A column with fewer than three
  finite samples reports ``act = None`` / ``ess = None`` (rendered ``n/a``)
  instead of a fabricated value.
* **Concatenated chains.** A non-monotone ``state`` column (``state[i] <=
  state[i-1]``) or a repeated header line marks a new chain.  Statistics are
  computed within each chain and combined as
  ``ESS_total = sum(n_i / ACT_i)``; burn-in is applied inside each chain.
* **ACT ceiling.** The Geyer estimator starts with ``_DEFAULT_MAX_LAG`` lags and
  extends the lag window to ``10 * ACT`` (capped at ``n - 1``).  When the
  window still ends at the chain length the estimate is marked truncated and
  ESS should be read as an order-of-magnitude value (a hard ceiling remains
  ``n - 1`` lags).

All statistics are deliberately dependency-light so the ``trace`` command
works even on machines without a Java runtime or a BEAST installation.
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from .utils import hpd_interval

#: A one-dimensional sample sequence: either a plain Python sequence or an
#: ``numpy`` array.  ``numpy.ndarray`` does not satisfy ``Sequence[float]`` in
#: the numpy 2 type stubs, so callers may pass either representation.
ChainLike = Union[Sequence[float], np.ndarray]

# Initial number of lags used for the spectral-variance ACT estimator; the
# estimator extends this adaptively (see ``act_estimate``).
_DEFAULT_MAX_LAG = 1000
# Columns that are not model parameters (iteration index / sample counter).
NON_PARAMETER_COLUMNS = frozenset({"state", "sample"})
_CHAIN_HINT_LIMIT = 8


class TraceError(ValueError):
    """Raised when a trace file cannot be parsed or analysed."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_trace_log(path: str, max_lines: Optional[int] = None) -> Dict[str, Any]:
    """Parse a BEAST 1 log file into a ``{"columns": [...], "values": {name: array}}`` dict.

    Args:
        path: Path to the ``.log`` file.
        max_lines: Optional cap on the number of data lines read (for tests).

    Returns:
        ``{"columns", "values", "n_samples", "comment_lines", "beast_version",
        "chains", "chain_boundaries"}`` where ``chains`` is a list of
        ``{"start", "end"}`` half-open row ranges.

    Raises:
        TraceError: If the header cannot be found, a data line is ragged or
            non-numeric, or the file is space-delimited instead of tab
            separated.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Trace file not found: {path}")

    columns: Optional[List[str]] = None
    comment_lines: List[str] = []
    rows: List[Tuple[int, List[str]]] = []
    header_restarts: List[int] = []

    with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip("\n\r")
            if not line.strip():
                continue
            if line.startswith("#"):
                # A comment block after data marks a new run/concatenation.
                if rows:
                    header_restarts.append(len(rows))
                comment_lines.append(line)
                continue
            if "\t" not in line and columns is None and " " in line.strip():
                raise TraceError(
                    f"{path}: line {line_no}: header is space-delimited; BEAST "
                    f"logs use a tab-separated header (convert whitespace to tabs)"
                )
            parts = [part.strip() for part in line.split("\t")]
            if columns is None:
                columns = parts
                continue
            if parts == columns:
                # A second header block: BEAST logs concatenated by hand or by
                # tools that do not remove the header of subsequent runs.
                header_restarts.append(len(rows))
                continue
            if len(parts) != len(columns):
                raise TraceError(
                    f"{path}: line {line_no}: expected {len(columns)} columns "
                    f"({len(parts)} values found)"
                )
            rows.append((line_no, parts))
            if max_lines is not None and len(rows) >= max_lines:
                break

    if columns is None:
        raise TraceError(
            f"{path}: no column header found (BEAST 1 log files start with a "
            f"'#'-comment block followed by a tab-separated header line)"
        )
    if not rows:
        raise TraceError(f"{path}: no data rows found")

    matrix = np.empty((len(rows), len(columns)), dtype=float)
    for row_index, (line_no, parts) in enumerate(rows):
        for col_index, value in enumerate(parts):
            try:
                matrix[row_index, col_index] = float(value)
            except (TypeError, ValueError):
                raise TraceError(
                    f"{path}: line {line_no}: non-numeric value {value!r} in "
                    f"column {columns[col_index]!r} (BEAST logs are tab-separated "
                    f"numeric columns)"
                ) from None

    values: Dict[str, np.ndarray] = {}
    for col_index, name in enumerate(columns):
        values[name] = matrix[:, col_index]

    state_index = next(
        (i for i, name in enumerate(columns) if name.strip().lower() == "state"),
        None,
    )
    boundaries = sorted(set(header_restarts))
    if state_index is not None:
        states = matrix[:, state_index]
        resets = [
            int(i)
            for i in range(1, len(states))
            if np.isfinite(states[i]) and np.isfinite(states[i - 1]) and states[i] <= states[i - 1]
        ]
        boundaries = sorted(set(boundaries).union(resets))

    chains: List[Dict[str, int]] = []
    starts = [0] + [b for b in boundaries if 0 < b < len(rows)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(rows)
        if end > start:
            chains.append({"start": start, "end": end})

    beast_line = next((c for c in comment_lines if "BEAST" in c), None)

    return {
        "columns": columns,
        "values": values,
        "n_samples": len(rows),
        "comment_lines": comment_lines,
        "beast_version": beast_line,
        "chains": chains,
        "chain_boundaries": [c["start"] for c in chains[1:]],
    }


# ---------------------------------------------------------------------------
# Autocorrelation / ESS
# ---------------------------------------------------------------------------


def act_estimate(
    chain: np.ndarray,
    max_lag: Optional[int] = None,
    return_info: bool = False,
) -> Any:
    """Estimate the integrated autocorrelation time (Geyer monotone window).

    Implements Geyer's initial-positive-sequence estimator:

    * the autocovariance is the *unbiased* estimate
      ``sum_{t=0}^{n-k-1}(x_t-mu)(x_{t+k}-mu)/(n-k)`` so ``rho[0] == 1``;
    * consecutive pairs ``Gamma_m = rho[2m] + rho[2m+1]`` are summed while
      positive, then made monotonically non-increasing
      (``Gamma_m = min(Gamma_m, Gamma_{m-1})``);
    * ``ACT = -1 + 2 * sum(Gamma_m)``.

    The lag window starts at ``_DEFAULT_MAX_LAG`` (or ``max_lag``) and grows to
    ``max(10 * ACT, window)`` capped at ``n - 1`` unless the caller supplied an
    explicit ``max_lag``.

    Args:
        chain: One-dimensional sample chain (must be finite).
        max_lag: Optional hard cap on the number of lags considered.
        return_info: Also return ``{"act", "max_lag_used", "truncated"}``.

    Returns:
        ACT (>= 1.0) or the info dict when ``return_info`` is true.

    Raises:
        TraceError: If the chain contains non-finite values.
    """
    arr = np.asarray(chain, dtype=float).ravel()
    n = int(arr.size)
    if n < 3:
        return (
            1.0
            if not return_info
            else {"act": 1.0, "max_lag_used": max(0, n - 1), "truncated": False}
        )
    if not np.all(np.isfinite(arr)):
        raise TraceError(
            f"act_estimate: chain contains {int((~np.isfinite(arr)).sum())} "
            f"non-finite value(s); filter them first or use compute_trace_stats"
        )
    mean = float(arr.mean())
    var = float(arr.var(ddof=1))
    if not math.isfinite(var) or var <= 0.0:
        info = {"act": 1.0, "max_lag_used": 0, "truncated": False}
        return info if return_info else 1.0

    adaptive = max_lag is None
    limit = min(n - 1, _DEFAULT_MAX_LAG if max_lag is None else int(max_lag))
    limit = max(1, limit)
    act, truncated, lag_used = _geyer_act(arr, mean, limit)
    if adaptive:
        for _ in range(8):
            needed = min(n - 1, max(limit + 1, int(math.ceil(10.0 * act)) + 2))
            if needed <= limit:
                break
            limit = needed
            act, truncated, lag_used = _geyer_act(arr, mean, limit)
    info = {
        "act": max(1.0, float(act)),
        "max_lag_used": int(lag_used),
        # A window that ends before the chain length is a documented estimator
        # property; only a window pinned at n-1 means "ESS is an upper bound".
        "truncated": bool(truncated and limit >= n - 1),
    }
    return info if return_info else info["act"]


def _geyer_act(arr: np.ndarray, mean: float, limit: int) -> Tuple[float, bool, int]:
    """Geyer initial-positive-sequence ACT for a fixed lag ``limit``."""
    acov = _autocovariance(arr, mean, limit)
    if len(acov) < 2 or acov[0] <= 0:
        return 1.0, False, 0
    rho = acov / acov[0]
    pairs: List[float] = []
    truncated = True
    m = 0
    while 2 * m + 1 < len(rho):
        gamma = float(rho[2 * m] + rho[2 * m + 1])
        if gamma <= 0.0:
            truncated = False
            break
        pairs.append(gamma)
        m += 1
    for i in range(1, len(pairs)):
        if pairs[i] > pairs[i - 1]:
            pairs[i] = pairs[i - 1]
    act = -1.0 + 2.0 * math.fsum(pairs)
    lag_used = min(limit, 2 * len(pairs) - 1)
    return act, truncated, lag_used


def _autocovariance(
    arr: np.ndarray, mean: float, max_lag: int, *, unbiased: bool = True
) -> np.ndarray:
    """Autocovariance for lags 0..max_lag via FFT.

    ``unbiased=True`` applies the ``n/(n-lag)`` correction used by the
    single-chain Geyer ACT estimator.  The multi-chain bulk/tail ESS estimator
    uses ``unbiased=False`` to match the reference implementation
    (ArviZ/posterior): there the marginal variance is reconstructed from the
    biased lag-0 autocovariance and the between-chain variance.
    """
    n = len(arr)
    centered = arr - mean
    fft_size = 1
    while fft_size < 2 * n:
        fft_size *= 2
    spectrum = np.fft.rfft(centered, fft_size)
    biased = np.fft.irfft(spectrum * np.conj(spectrum), fft_size)[:n]
    biased = biased.real / n
    lags = np.arange(min(max_lag + 1, n))
    if unbiased and n > 1:
        biased = biased[: len(lags)] * (n / (n - lags))
    return biased


def ess_estimate(chain: np.ndarray, max_lag: Optional[int] = None) -> float:
    """Effective sample size (= n / ACT) of a finite chain."""
    arr = np.asarray(chain, dtype=float).ravel()
    n = int(arr.size)
    if n == 0:
        return float("nan")
    if not np.all(np.isfinite(arr)):
        raise TraceError("ess_estimate: chain contains non-finite values; ESS is undefined")
    act = act_estimate(arr, max_lag=max_lag)
    if not math.isfinite(act) or act <= 0:
        return float("nan")
    return n / act


def geweke_z(chain: np.ndarray, fraction1: float = 0.1, fraction2: float = 0.5) -> Optional[float]:
    """Geweke z-score comparing the first and last segments of a chain.

    Returns ``None`` when the score is not computable (fewer than 100 finite
    samples, a segment shorter than three samples, or a zero/non-finite
    spectral variance).  A z-score of 0.0 would mean "both segments have the
    same mean" and must never stand in for "not computable".
    """
    from .utils import _spectral_var  # local import to avoid cycles

    arr = np.asarray(chain, dtype=float)
    finite = arr[np.isfinite(arr)]
    n = len(finite)
    if n < 100:
        return None
    n1 = max(10, int(fraction1 * n))
    n2 = max(10, int((1.0 - fraction2) * n))
    first = finite[:n1]
    last = finite[n2:]
    if len(first) < 3 or len(last) < 3:
        return None
    var_first = _spectral_var(first)
    var_last = _spectral_var(last)
    if not math.isfinite(var_first) or not math.isfinite(var_last):
        return None
    if var_first <= 0 or var_last <= 0:
        return None
    # Geweke (1992): the segment means are averages, so each spectral
    # variance is divided by its segment length.  Omitting these terms
    # understates z by roughly sqrt(n/2) and makes automatic burn-in far
    # too permissive.
    standard_error = math.sqrt(var_first / len(first) + var_last / len(last))
    if not math.isfinite(standard_error) or standard_error <= 0:
        return None
    return float((first.mean() - last.mean()) / standard_error)


def auto_burnin(
    values: Dict[str, np.ndarray],
    max_frac: float = 0.5,
    chains: Optional[Sequence[Tuple[int, int]]] = None,
) -> Optional[int]:
    """Return an automatic burn-in (number of samples), or ``None``.

    For every parameter column the Geweke z-score is evaluated at burn-in
    fractions 0.0, 0.05, ..., up to ``max_frac``; the largest fraction at
    which any column still exceeds |z| > 2 stabilises is chosen.  When the
    trace consists of several chains the burn-in is computed per chain and the
    largest count is returned (it is applied inside every chain).

    ``None`` means **not computable** (a chain is shorter than 100 samples,
    or no parameter column yields a Geweke score).  ``None`` must not be
    reported as burn-in 0: zero discards nothing and is the most permissive
    choice, not a conservative estimate of convergence.  Callers either ask
    for an explicit fraction or surface the not-computable status.
    """
    parameter_values = {
        name: chain
        for name, chain in values.items()
        if name.strip().lower() not in NON_PARAMETER_COLUMNS
    }
    if not parameter_values:
        return None
    if chains is None:
        chains = [(0, min(len(v) for v in parameter_values.values()))]
    burnins: List[int] = []
    for start, end in chains:
        n = end - start
        if n < 100:
            return None
        chain_values = {name: chain[start:end] for name, chain in parameter_values.items()}
        value = _auto_burnin_single(chain_values, max_frac)
        if value is None:
            return None
        burnins.append(value)
    return int(max(burnins)) if burnins else None


def _auto_burnin_single(values: Dict[str, np.ndarray], max_frac: float) -> Optional[int]:
    n = min(len(v) for v in values.values()) if values else 0
    if n < 100:
        return None
    worst = 0.0
    for frac in np.arange(0.0, max_frac + 1e-9, 0.05):
        start = int(frac * n)
        if start >= n - 1:
            break
        z = 0.0
        computable = False
        for chain in values.values():
            if np.all(chain == chain[0]):
                continue
            seg = chain[start:]
            if len(seg) < 100:
                continue
            value = geweke_z(seg)
            if value is None:
                continue
            z = max(z, abs(value))
            computable = True
        if not computable:
            # No Geweke score exists at this or any later fraction.  Selecting
            # a burn-in would be a guess, so report "not computable" instead of
            # the maximally permissive burn-in=0.
            return None
        worst = max(worst, float(frac))
        if z <= 2.0:
            break
    return int(worst * n)


# ---------------------------------------------------------------------------
# Per-column statistics
# ---------------------------------------------------------------------------


def compute_trace_stats(
    values: Dict[str, np.ndarray],
    burnin: Optional[int] = None,
    ess_threshold: float = 200.0,
    chains: Optional[Sequence[Tuple[int, int]]] = None,
    include_non_parameters: bool = False,
) -> Dict[str, Any]:
    """Compute Tracer-inspired statistics for every model-parameter column.

    Args:
        values: Column name -> sample array (from :func:`parse_trace_log`).
        burnin: Number of samples dropped from the start of every chain
            (``None`` = automatic Geweke-based burn-in).
        ess_threshold: ESS warning threshold.
        chains: Optional ``(start, end)`` row ranges of concatenated chains.
        include_non_parameters: Also analyse ``state``/``sample`` columns
            (default: excluded; see :data:`NON_PARAMETER_COLUMNS`).

    Returns:
        A dict with ``burnin``, ``columns`` (list of stat dicts), ``warnings``,
        ``n_chains`` and per-chain metadata.
    """
    n = min(len(v) for v in values.values()) if values else 0
    if n == 0:
        return {
            "burnin": 0,
            "columns": [],
            "warnings": ["trace table is empty"],
            "n_chains": 0,
            "chains": [],
        }

    chain_ranges: List[Tuple[int, int]]
    if chains is None:
        chain_ranges = [(0, n)]
    else:
        chain_ranges = [
            (max(0, int(start)), min(n, int(end))) for start, end in chains if end > start
        ]
        if not chain_ranges:
            chain_ranges = [(0, n)]

    parameter_names = [
        name
        for name in values
        if include_non_parameters or name.strip().lower() not in NON_PARAMETER_COLUMNS
    ]

    if burnin is None:
        automatic = auto_burnin(
            {name: values[name] for name in parameter_names}, chains=chain_ranges
        )
        if automatic is None:
            burnin = 0
            burnin_status = "not_computable"
        else:
            burnin = automatic
            burnin_status = "auto"
    else:
        burnin_status = "explicit"

    warnings_list: List[str] = []
    if burnin_status == "not_computable":
        warnings_list.append(
            "automatic burn-in is not computable (Geweke z needs at least 100 "
            "samples and at least one non-constant parameter); no samples were "
            "discarded, but burn-in=0 is not an estimate of convergence. Pass an "
            "explicit burn-in fraction (for example --burnin 0.1)."
        )
    requested_burnin = int(max(0, burnin))
    # Burn-in is applied inside every chain.  A chain must keep at least two
    # samples; chains shorter than that are clamped individually (and reported)
    # rather than forcing every chain down to the shortest one.
    chain_burnins = [min(requested_burnin, max(0, end - start - 2)) for start, end in chain_ranges]
    clamped = sum(1 for keep in chain_burnins if keep != requested_burnin)
    burnin = max(chain_burnins) if chain_burnins else 0
    if clamped and requested_burnin > 0:
        warnings_list.append(
            f"requested burn-in {requested_burnin} leaves fewer than 2 samples in "
            f"{clamped} chain(s); those chains use their maximum ({burnin})"
        )

    if len(chain_ranges) > 1:
        warnings_list.append(
            f"trace contains {len(chain_ranges)} concatenated chains (state resets "
            f"or repeated headers at rows {[s for s, _ in chain_ranges[1:]][:_CHAIN_HINT_LIMIT]}); "
            f"ACT/ESS and burn-in are applied per chain and combined as "
            f"ESS_total = sum(n_i / ACT_i)"
        )

    columns: List[Dict[str, Any]] = []
    for name in values:
        full = np.asarray(values[name], dtype=float)
        stats: Dict[str, Any] = {"name": name, "n": 0, "n_nonfinite": 0}
        if name.strip().lower() in NON_PARAMETER_COLUMNS and not include_non_parameters:
            # Iteration counters are not model parameters; do not report
            # HPD/ESS/mode for them at all.
            continue

        pieces: List[np.ndarray] = []
        total_nonfinite = 0
        chains_ess = 0.0
        usable_chains = 0
        truncated = False
        max_lag_used = 0
        per_chain: List[Dict[str, Any]] = []
        truncated_chains: List[int] = []
        for chain_index, ((start, end), keep) in enumerate(zip(chain_ranges, chain_burnins)):
            piece = full[start + keep : end]
            total_nonfinite += int((~np.isfinite(piece)).sum())
            finite = piece[np.isfinite(piece)]
            if finite.size:
                pieces.append(finite)
            chain_stats: Dict[str, Any] = {
                "index": chain_index,
                "start": int(start),
                "end": int(end),
                "burnin": int(keep),
                "n": int(finite.size),
                "act": float("nan"),
                "ess": float("nan"),
                "act_max_lag": 0,
                "act_truncated": False,
            }
            if finite.size >= 3:
                info = act_estimate(finite, return_info=True)
                act_i = info["act"]
                chain_truncated = bool(info["truncated"])
                truncated = truncated or chain_truncated
                max_lag_used = max(max_lag_used, int(info["max_lag_used"]))
                chains_ess += finite.size / act_i
                usable_chains += 1
                chain_stats.update(
                    {
                        "act": float(act_i),
                        "ess": float(finite.size / act_i),
                        "act_max_lag": int(info["max_lag_used"]),
                        "act_truncated": chain_truncated,
                    }
                )
                if chain_truncated:
                    truncated_chains.append(chain_index)
            elif finite.size:
                # Too short for an ACT estimate: treat as independent.
                chains_ess += float(finite.size)
                chain_stats.update({"act": 1.0, "ess": float(finite.size)})
            per_chain.append(chain_stats)

        stats["n_nonfinite"] = total_nonfinite
        finite_all = np.concatenate(pieces) if pieces else np.array([], dtype=float)
        stats["n"] = int(finite_all.size)
        if finite_all.size == 0:
            stats.update(_empty_stats())
            stats["per_chain"] = per_chain
            warnings_list.append(
                f"{name}: no finite values ({total_nonfinite} dropped); "
                f"all statistics are undefined"
            )
            columns.append(stats)
            continue

        stats.update(_summary_stats(finite_all))
        if usable_chains == 0 and finite_all.size > 0 and len(chain_ranges) == 1:
            stats["act"] = 1.0 if finite_all.size >= 2 else float("nan")
            stats["ess"] = float(finite_all.size) if finite_all.size >= 2 else float("nan")
        elif chains_ess <= 0 or not math.isfinite(chains_ess):
            stats["ess"] = float("nan")
            stats["act"] = float("nan")
        else:
            stats["ess"] = float(chains_ess)
            total_used = sum(p.size for p in pieces)
            stats["act"] = float(total_used / chains_ess) if total_used else float("nan")
        stats["act_max_lag"] = int(max_lag_used)
        stats["act_truncated"] = bool(truncated)
        stats["per_chain"] = per_chain

        if total_nonfinite:
            warnings_list.append(
                f"{name}: {total_nonfinite} non-finite value(s) excluded from "
                f"statistics; ACT/ESS use {int(finite_all.size)} finite sample(s)"
            )
        if finite_all.size < 2:
            warnings_list.append(
                f"{name}: only {int(finite_all.size)} finite sample(s); ESS/ACT "
                f"and the 95% interval are not estimable (reported as n/a)"
            )
        elif finite_all.size < 20:
            warnings_list.append(
                f"{name}: only {int(finite_all.size)} finite sample(s); the 95% "
                f"interval is the full sample range (shortest HPD not estimable)"
            )
        if truncated:
            which = (
                f" in chain(s) {', '.join(str(i) for i in truncated_chains)}"
                if truncated_chains
                else ""
            )
            warnings_list.append(
                f"{name}: ACT estimate reached the lag ceiling of the chain"
                f"{which} (lag {max_lag_used}); ESS should be read as an upper bound"
            )
        ess = stats.get("ess")
        if (
            ess is not None
            and isinstance(ess, float)
            and math.isfinite(ess)
            and ess < ess_threshold
        ):
            warnings_list.append(
                f"{name}: ESS {ess:.1f} < {ess_threshold:.0f} "
                f"(chain may be insufficiently mixed)"
            )
        columns.append(stats)

    return {
        "burnin": burnin,
        "burnin_status": burnin_status,
        "columns": columns,
        "warnings": warnings_list,
        "n_chains": len(chain_ranges),
        "chains": [{"start": int(s), "end": int(e)} for s, e in chain_ranges],
    }


def _empty_stats() -> Dict[str, Any]:
    nan = float("nan")
    return {
        "mean": nan,
        "std": nan,
        "median": nan,
        "geometric_mean": nan,
        "hpd_lower": nan,
        "hpd_upper": nan,
        "equal_tailed_lower": nan,
        "equal_tailed_upper": nan,
        "min": nan,
        "max": nan,
        "mode": nan,
        "act": nan,
        "ess": nan,
    }


def _summary_stats(finite: np.ndarray) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "mean": float(np.mean(finite)),
        "std": float(np.std(finite, ddof=1)) if finite.size > 1 else 0.0,
        "median": float(np.median(finite)),
        "geometric_mean": float(
            np.exp(np.mean(np.log(finite[finite > 0]))) if np.any(finite > 0) else float("nan")
        ),
        "min": float(np.min(finite)),
        "max": float(np.max(finite)),
        "mode": _mode_estimate(finite),
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        lo, hi = hpd_interval(finite, alpha=0.05)
    stats["hpd_lower"] = float(lo)
    stats["hpd_upper"] = float(hi)
    stats["equal_tailed_lower"] = float(np.quantile(finite, 0.025))
    stats["equal_tailed_upper"] = float(np.quantile(finite, 0.975))
    return stats


def _mode_estimate(samples: np.ndarray, grid: int = 256) -> float:
    """Mode estimate: most frequent observed value for discrete samples.

    All-integral samples (or samples with at most 50 distinct values) use
    ``np.unique`` so the result is an observed value; continuous samples keep
    the densest-histogram-bin midpoint.
    """
    arr = np.asarray(samples, dtype=float)
    if len(arr) == 0:
        return float("nan")
    lo, hi = float(np.min(arr)), float(np.max(arr))
    if not math.isfinite(hi - lo) or hi - lo == 0:
        return float(lo)
    values, counts = np.unique(arr, return_counts=True)
    integral = bool(np.all(np.equal(np.mod(arr, 1.0), 0.0)))
    if (integral and values.size <= max(grid, 1)) or values.size <= 50:
        best = int(np.argmax(counts))  # first (smallest) value wins ties
        return float(values[best])
    counts, edges = np.histogram(arr, bins=min(grid, len(arr)))
    best = int(np.argmax(counts))
    return float((edges[best] + edges[best + 1]) / 2.0)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _format_float(value: Any, spec: str = ".5g") -> str:
    """Format a float for text tables; non-finite/None -> ``n/a``."""
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(number):
        return "n/a"
    return format(number, spec)


def format_stats_table(result: Dict[str, Any], ess_threshold: float = 200.0) -> str:
    """Render the trace statistics as a plain-text table."""
    lines = [
        f"burnin: {result['burnin']} samples (removed inside every chain; clamped to keep 2 samples)",
    ]
    if result.get("n_chains", 1) > 1:
        lines.append(f"chains: {result['n_chains']} (statistics combined per chain)")
    lines.extend(
        [
            "",
            f"{'column':<28}{'mean':>14}{'ess':>10}{'act':>8}{'95% HPD':>26}",
            "-" * 88,
        ]
    )
    for col in result["columns"]:
        if col["name"].strip().lower() in NON_PARAMETER_COLUMNS:
            continue
        hpd = "n/a"
        if math.isfinite(float(col.get("hpd_lower", float("nan")))) and math.isfinite(
            float(col.get("hpd_upper", float("nan")))
        ):
            hpd = f"[{col['hpd_lower']:.5g}, {col['hpd_upper']:.5g}]"
        ess = col.get("ess")
        ess_mark = (
            " *"
            if ess is not None and math.isfinite(float(ess)) and float(ess) < ess_threshold
            else ""
        )
        lines.append(
            f"{col['name'][:28]:<28}{_format_float(col.get('mean')):>14}"
            f"{_format_float(ess, '.1f'):>9}{ess_mark}"
            f"{_format_float(col.get('act'), '.2f'):>8}{hpd:>26}"
        )
    if result["warnings"]:
        lines.append("")
        lines.append("warnings:")
        lines.extend(f"  - {w}" for w in result["warnings"])
    return "\n".join(lines)


def write_trace_plot(
    values: Dict[str, np.ndarray],
    burnin: int,
    output_png: str,
    max_columns: int = 8,
) -> None:
    """Render a trace/density grid plot for the most important columns."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    max_columns = max(1, int(max_columns))
    names = [
        name
        for name, chain in values.items()
        if name.strip().lower() not in NON_PARAMETER_COLUMNS
        and np.asarray(chain).size > 0
        and np.any(np.asarray(chain) != np.asarray(chain)[0])
    ]
    names = names[:max_columns]
    if not names:
        raise TraceError(
            "no varying parameter columns to plot (all columns are constant, "
            "empty, or non-parameter columns such as 'state')"
        )
    ncols = 2
    nrows = math.ceil(len(names) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(12, 3 * nrows), squeeze=False)
    fig.suptitle("BEAST 1 trace diagnostics (burn-in shown as dashed line)")
    for idx, name in enumerate(names):
        ax: Any = axes[idx // ncols][idx % ncols]
        chain = np.asarray(values[name], dtype=float)
        ax.plot(np.arange(len(chain)), chain, lw=0.4, color="#4C72B0")
        ax.axvline(burnin, color="k", ls="--", lw=1)
        ax.set_title(name, fontsize=9)
        ax.tick_params(labelsize=7)
    for idx in range(len(names), nrows * ncols):
        unused_ax: Any = axes[idx // ncols][idx % ncols]
        unused_ax.axis("off")
    fig.tight_layout()
    fig.savefig(output_png, dpi=120)
    plt.close(fig)


def trace_command(
    log_path: str,
    burnin: Optional[int] = None,
    ess_threshold: float = 200.0,
    plot: Optional[str] = None,
    as_json: bool = False,
) -> Dict[str, Any]:
    """Full ``trace`` pipeline; returns the machine-readable result dict."""
    parsed = parse_trace_log(log_path)
    chains = [(c["start"], c["end"]) for c in parsed.get("chains", [])]
    result = compute_trace_stats(
        parsed["values"],
        burnin=burnin,
        ess_threshold=ess_threshold,
        chains=chains or None,
    )
    result["path"] = log_path
    result["n_samples"] = parsed["n_samples"]
    result["beast_version"] = parsed["beast_version"]
    result["ess_threshold"] = ess_threshold
    if plot:
        write_trace_plot(parsed["values"], result["burnin"], plot)
        result["plot"] = plot
    return result


def _json_safe(obj: Any) -> Any:
    """Recursively replace non-finite floats with ``None`` for strict JSON."""
    if isinstance(obj, dict):
        return {str(key): _json_safe(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(value) for value in obj]
    if isinstance(obj, np.ndarray):
        return [_json_safe(value) for value in obj.tolist()]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        number = float(obj)
        return number if math.isfinite(number) else None
    if isinstance(obj, Path):
        return str(obj)
    return obj


def trace_to_json(result: Dict[str, Any]) -> str:
    """Serialize the trace result to strict JSON (no ``NaN``/``Infinity``)."""
    return json.dumps(_json_safe(result), indent=2, allow_nan=False, default=str)


# ---------------------------------------------------------------------------
# Multi-chain diagnostics: Gelman-Rubin PSRF (appended for the ``assess``
# command; existing code above is intentionally untouched).
# ---------------------------------------------------------------------------


def split_chain_halves(chain: np.ndarray) -> List[np.ndarray]:
    """Split one sample chain into its two halves (split-chain R-hat trick).

    An odd-length chain drops its middle sample so both halves have equal
    length, which the classic Gelman-Rubin formula requires.  Chains shorter
    than four samples yield ``[]`` (splitting them cannot be informative).
    """
    arr = np.asarray(chain, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if arr.size < 4:
        return []
    half = arr.size // 2
    return [arr[:half], arr[arr.size - half :]]


def gelman_rubin_psrf(
    chains: Sequence[ChainLike],
    *,
    split: bool = True,
) -> Optional[float]:
    """Classic Gelman-Rubin potential scale reduction factor (R-hat, "PSRF").

    Estimator (documented per CONTRACT F1): the **split-chain** estimator by
    default -- every input chain is cut in half (:func:`split_chain_halves`),
    giving ``2m`` chains, and the classic across-chains formula is applied to
    those halves.  Pass ``split=False`` for the plain across-chains estimator
    on the chains as given.  Splitting detects within-chain non-stationarity
    that the plain estimator misses (Gelman & Rubin 1992; Gelman et al., BDA3).

    Formula, with ``m`` chains of equal length ``n`` (chains are truncated to
    the shortest one), ``W`` = mean within-chain variance (ddof=1) and ``B`` =
    between-chain variance of the chain means (ddof=1) scaled by ``n``::

        var_plus = (n - 1) / n * W + B / n
        R_hat    = sqrt(var_plus / W)

    Args:
        chains: Two or more one-dimensional sample sequences (post burn-in).
        split: Use the split-chain estimator (default) or the plain one.

    Returns:
        R-hat (>= 0), or ``None`` when it is **not computable**: fewer than
        two usable chains, fewer than two finite samples per chain, or a
        pooled within-chain variance of exactly zero.  Callers must report
        ``None`` as ``not_computable`` and never substitute a number.  Note
        that ``split=True`` still requires at least two *input* chains: the
        halves of a single chain carry no between-chain information, so one
        chain is always ``not_computable`` (CONTRACT.md §5 F1).
    """
    finite_chains: List[np.ndarray] = []
    for chain in chains:
        arr = np.asarray(chain, dtype=float).ravel()
        finite_chains.append(arr[np.isfinite(arr)])
    if len(finite_chains) < 2:
        return None
    usable: List[np.ndarray] = []
    for arr in finite_chains:
        if split:
            usable.extend(split_chain_halves(arr))
        elif arr.size >= 2:
            usable.append(arr)
    if len(usable) < 2:
        return None
    n = min(int(arr.size) for arr in usable)
    if n < 2:
        return None
    trimmed = [arr[:n] for arr in usable]
    within_vars = np.array([float(arr.var(ddof=1)) for arr in trimmed])
    means = np.array([float(arr.mean()) for arr in trimmed])
    w = float(within_vars.mean())
    if not math.isfinite(w) or w <= 0.0:
        # Every chain is constant: R-hat is 0/0 (undefined) unless the means
        # also differ, in which case it diverges.  Report neither as a number.
        return None
    b = float(means.var(ddof=1)) * n
    var_plus = (n - 1.0) / n * w + b / n
    if not math.isfinite(var_plus) or var_plus < 0.0:
        return None
    return math.sqrt(var_plus / w)


def compute_multichain_psrf(
    values_by_chain: Sequence[Dict[str, np.ndarray]],
    *,
    split: bool = True,
    threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Gelman-Rubin PSRF for every parameter shared by >= 2 chains.

    Args:
        values_by_chain: One ``{parameter: post-burn-in samples}`` mapping per
            chain (as produced per seed directory by the assess pipeline).
        split: Use the split-chain estimator (default; see
            :func:`gelman_rubin_psrf`).
        threshold: When given, each entry carries ``ok = psrf <= threshold``.

    Returns:
        ``{"estimator", "n_chains", "status", "reason", "values"}`` where
        ``values[name]`` is ``{"psrf", "ok", "reason"}``.  ``status`` is
        ``"not_computable"`` (with a plain-language ``reason`` and empty
        ``values``) when fewer than two chains are available; a per-parameter
        ``psrf`` of ``None`` carries its own ``reason`` (e.g. the parameter is
        missing from some chains, or the within-chain variance is zero).
    """
    estimator = (
        "split-chain Gelman-Rubin R-hat (every chain split in half; "
        "across+within variance ratio)"
        if split
        else "across-chains Gelman-Rubin R-hat (classic, chains as given)"
    )
    n_chains = len(values_by_chain)
    if n_chains < 2:
        return {
            "estimator": estimator,
            "n_chains": n_chains,
            "status": "not_computable",
            "reason": (
                "PSRF (Gelman-Rubin R-hat) compares between-chain and "
                f"within-chain variance and needs at least 2 independent "
                f"chains; {n_chains} usable chain(s) were found. No value is "
                "reported because none exists. Run additional seeds (for "
                "example `beastxpy run --seeds 1,2,3`) and re-assess."
            ),
            "values": {},
        }

    names: List[str] = []
    seen = set()
    for mapping in values_by_chain:
        for name in mapping:
            key = str(name).strip()
            if key.lower() in NON_PARAMETER_COLUMNS:
                continue
            if key not in seen:
                seen.add(key)
                names.append(key)

    values: Dict[str, Any] = {}
    for name in names:
        present = [
            np.asarray(mapping[name], dtype=float).ravel()
            for mapping in values_by_chain
            if name in mapping
        ]
        if len(present) < 2:
            values[name] = {
                "psrf": None,
                "ok": None,
                "reason": (
                    f"column {name!r} is present in {len(present)} of "
                    f"{n_chains} chains; PSRF needs it in at least 2"
                ),
            }
            continue
        r_hat = gelman_rubin_psrf(present, split=split)
        if r_hat is None:
            values[name] = {
                "psrf": None,
                "ok": None,
                "reason": (
                    f"PSRF for {name!r} is undefined: every chain is constant "
                    "after burn-in (zero within-chain variance) or the chains "
                    "are too short after splitting; no value is reported"
                ),
            }
            continue
        entry: Dict[str, Any] = {"psrf": float(r_hat), "ok": None, "reason": None}
        if threshold is not None:
            entry["ok"] = bool(r_hat <= float(threshold))
        values[name] = entry

    any_computable = any(v["psrf"] is not None for v in values.values())
    return {
        "estimator": estimator,
        "n_chains": n_chains,
        "status": "ok" if any_computable else "not_computable",
        "reason": (
            None
            if any_computable
            else (
                "no parameter column produced a computable PSRF (see the " "per-parameter reasons)"
            )
        ),
        "values": values,
    }


# ---------------------------------------------------------------------------
# Rank-normalized R-hat and bulk/tail ESS (Vehtari et al. 2021)
# ---------------------------------------------------------------------------

RANK_PSRF_ESTIMATOR = (
    "rank-normalized split R-hat (Vehtari, Gelman, Simpson, Carpenter & "
    "Bürkner 2021; max of the bulk and folded rank-normalized split-chain "
    "estimates)"
)
BULK_TAIL_ESS_ESTIMATOR = (
    "bulk ESS on rank-normalized split chains and tail ESS on split-chain "
    "5% / 95% indicators (Vehtari et al. 2021; matches ArviZ/posterior when "
    "at least two input chains are supplied)"
)

#: Deliberate deviation from ArviZ/posterior: BeastXPy requires at least two
#: input chains for bulk/tail ESS and never manufactures a second chain by
#: splitting one.  Single-chain runs still get the Geyer ESS.  This matches
#: the PSRF contract (multi-chain diagnostics need multiple chains) and avoids
#: reporting an over-confident bulk ESS from one chain pretending to be two.
MULTI_CHAIN_ESS_MIN_CHAINS = 2


def _prepare_equal_chains(chains: Sequence[ChainLike], *, split: bool) -> List[np.ndarray]:
    """Finite, equal-length chains (optionally split in half) or ``[]``."""
    finite = [np.asarray(chain, dtype=float).ravel() for chain in chains]
    finite = [arr[np.isfinite(arr)] for arr in finite]
    if len(finite) < 1:
        return []
    usable: List[np.ndarray] = []
    for arr in finite:
        if split:
            usable.extend(split_chain_halves(arr))
        elif arr.size >= 2:
            usable.append(arr)
    if len(usable) < 2:
        return []
    n = min(int(arr.size) for arr in usable)
    if n < 2:
        return []
    return [arr[:n] for arr in usable]


def _classic_rhat_arrays(arrays: Sequence[np.ndarray]) -> Optional[float]:
    """Classic R-hat for equal-length arrays (no splitting, no ranking)."""
    if len(arrays) < 2:
        return None
    n = min(int(arr.size) for arr in arrays)
    if n < 2:
        return None
    trimmed = [arr[:n] for arr in arrays]
    within = np.array([float(arr.var(ddof=1)) for arr in trimmed])
    means = np.array([float(arr.mean()) for arr in trimmed])
    w = float(within.mean())
    if not math.isfinite(w) or w <= 0.0:
        return None
    b = float(means.var(ddof=1)) * n
    var_plus = (n - 1.0) / n * w + b / n
    if not math.isfinite(var_plus) or var_plus < 0.0:
        return None
    return math.sqrt(var_plus / w)


def _rank_normalize_arrays(arrays: Sequence[np.ndarray]) -> List[np.ndarray]:
    """Rank-normalize pooled draws to normal scores, preserving chain shapes."""
    from scipy.stats import norm, rankdata

    lengths = [int(arr.size) for arr in arrays]
    pooled = np.concatenate([np.asarray(arr, dtype=float).ravel() for arr in arrays])
    total = pooled.size
    if total < 2:
        return [np.array([], dtype=float) for _ in arrays]
    ranks = rankdata(pooled, method="average")
    scores = norm.ppf((ranks - 0.375) / (total + 0.25))
    out: List[np.ndarray] = []
    offset = 0
    for length in lengths:
        out.append(np.asarray(scores[offset : offset + length], dtype=float))
        offset += length
    return out


def rank_normalized_split_rhat(
    chains: Sequence[ChainLike],
) -> Optional[float]:
    """Rank-normalized split R-hat (Vehtari et al. 2021).

    ``max(R-hat(bulk), R-hat(folded))`` where both are computed after mapping
    the pooled draws to normal scores.  ``None`` means not computable (fewer
    than two chains, fewer than four finite draws per chain, or zero variance).
    """
    if len(chains) < 2:
        return None
    arrays = _prepare_equal_chains(chains, split=True)
    if len(arrays) < 2:
        return None
    bulk = _classic_rhat_arrays(_rank_normalize_arrays(arrays))
    pooled = np.concatenate(arrays)
    median = float(np.median(pooled))
    folded = [np.abs(arr - median) for arr in arrays]
    folded_rhat = _classic_rhat_arrays(_rank_normalize_arrays(folded))
    candidates = [value for value in (bulk, folded_rhat) if value is not None]
    if not candidates:
        return None
    return float(max(candidates))


def _multi_chain_ess(arrays: Sequence[np.ndarray]) -> Optional[float]:
    """Geyer ESS for equal-length chains (Vehtari et al. 2021 / ArviZ).

    Uses the unbiased marginal-variance decomposition of the reference
    implementation::

        mean_var = mean(acov_i[0]) * n / (n - 1)
        var_plus = mean_var * (n - 1) / n
        if m > 1: var_plus += var(chain_means, ddof=1)
        tau = -1 + 2 * sum(Geyer-monotone rho pairs)
        ESS = m * n / tau          (not capped at m * n)

    Not capping matters for anti-correlated chains, for which the reference
    implementation legitimately returns ESS > total draws.  Returns ``None``
    when tau is not estimable (constant chains, too few draws, or zero
    variance).
    """
    if len(arrays) < 1:
        return None
    m = len(arrays)
    n = min(int(arr.size) for arr in arrays)
    if m < 1 or n < 4:
        return None
    trimmed = [np.asarray(arr[:n], dtype=float) for arr in arrays]
    acov = np.array(
        [_autocovariance(arr, float(arr.mean()), n - 1, unbiased=False) for arr in trimmed]
    )
    if acov.size < 2 or not math.isfinite(float(acov[0, 0])) or acov[0, 0] <= 0:
        return None
    chain_means = np.array([float(arr.mean()) for arr in trimmed])
    mean_var = float(np.mean(acov[:, 0])) * n / (n - 1.0)
    var_plus = mean_var * (n - 1.0) / n
    if m > 1:
        var_plus += float(np.var(chain_means, ddof=1))
    if not math.isfinite(var_plus) or var_plus <= 0.0:
        return None

    rho = np.zeros(n, dtype=float)
    rho[0] = 1.0
    rho[1] = 1.0 - (mean_var - float(np.mean(acov[:, 1]))) / var_plus
    rho_even = 1.0
    rho_odd = float(rho[1])
    t = 1
    while t < n - 3 and (rho_even + rho_odd) > 0.0:
        rho_even = 1.0 - (mean_var - float(np.mean(acov[:, t + 1]))) / var_plus
        rho_odd = 1.0 - (mean_var - float(np.mean(acov[:, t + 2]))) / var_plus
        if rho_even + rho_odd >= 0.0:
            rho[t + 1] = rho_even
            rho[t + 2] = rho_odd
        t += 2
    max_t = t - 2
    if rho_even > 0.0:
        rho[max_t + 1] = rho_even
    t = 1
    while t <= max_t - 2:
        if (rho[t + 1] + rho[t + 2]) > (rho[t - 1] + rho[t]):
            rho[t + 1] = (rho[t - 1] + rho[t]) / 2.0
            rho[t + 2] = rho[t + 1]
        t += 2
    total = m * n
    tau = -1.0 + 2.0 * float(np.sum(rho[: max_t + 1])) + float(np.sum(rho[max_t + 1 : max_t + 2]))
    if not math.isfinite(tau):
        return None
    tau = max(tau, 1.0 / math.log10(total))
    ess = total / tau
    if not math.isfinite(ess) or ess <= 0.0:
        return None
    return float(ess)


def bulk_ess(chains: Sequence[ChainLike]) -> Optional[float]:
    """Bulk effective sample size on rank-normalized split chains.

    This is the ESS of the level (location) of the posterior, matching the
    ``ess_bulk`` column of ArviZ/posterior.  ``None`` when not computable.

    Unlike ArviZ, a single input chain is not split in half to manufacture
    the two chains the estimator needs; this implementation returns ``None``
    and callers fall back to the single-chain Geyer ESS.  The choice is
    deliberate (see :data:`MULTI_CHAIN_ESS_MIN_CHAINS`) and consistent with
    the multi-chain PSRF contract.
    """
    if len(chains) < MULTI_CHAIN_ESS_MIN_CHAINS:
        return None
    arrays = _prepare_equal_chains(chains, split=True)
    if len(arrays) < 2:
        return None
    return _multi_chain_ess(_rank_normalize_arrays(arrays))


def tail_ess(chains: Sequence[ChainLike], tail: float = 0.05) -> Optional[float]:
    """Minimum lower/upper tail ESS from quantile indicators.

    Uses the empirical ``tail`` and ``1 - tail`` quantiles of the pooled
    draws, computes the Geyer ESS of each **split-chain** indicator series,
    and returns the smaller value (the weakest tail).  Splitting follows
    Vehtari et al. (2021) and matches ArviZ/posterior ``ess_tail`` when at
    least two input chains are supplied.  Like :func:`bulk_ess`, a single
    chain is never split in half to stand in for two chains; ``None`` is
    returned instead.  ``None`` when not computable.
    """
    if not (0.0 < float(tail) < 0.5):
        raise TraceError(f"tail must be in (0, 0.5), got {tail!r}")
    if len(chains) < MULTI_CHAIN_ESS_MIN_CHAINS:
        return None
    arrays = _prepare_equal_chains(chains, split=True)
    if len(arrays) < 2:
        return None
    n = min(int(arr.size) for arr in arrays)
    if n < 20:
        return None
    trimmed = [arr[:n] for arr in arrays]
    pooled = np.concatenate(trimmed)
    low = float(np.quantile(pooled, tail))
    high = float(np.quantile(pooled, 1.0 - tail))
    lower = [(arr <= low).astype(float) for arr in trimmed]
    upper = [(arr >= high).astype(float) for arr in trimmed]
    ess_low = _multi_chain_ess(lower)
    ess_high = _multi_chain_ess(upper)
    candidates = [value for value in (ess_low, ess_high) if value is not None]
    if not candidates:
        return None
    return float(min(candidates))


def compute_multichain_rank_diagnostics(
    values_by_chain: Sequence[Dict[str, np.ndarray]],
    *,
    psrf_threshold: Optional[float] = None,
    ess_threshold: Optional[float] = None,
    tail: float = 0.05,
) -> Dict[str, Any]:
    """Rank-normalized R-hat plus bulk/tail ESS for every shared parameter.

    Args:
        values_by_chain: One ``{parameter: post-burn-in samples}`` mapping per
            chain.
        psrf_threshold: When given, each entry carries ``ok`` for
            ``rank_psrf <= threshold`` (``None`` when not computable).
        ess_threshold: When given, each entry carries ``ess_bulk_ok`` and
            ``ess_tail_ok``.
        tail: Tail probability for :func:`tail_ess`.

    Returns:
        ``{"estimator", "ess_estimator", "n_chains", "status", "reason",
        "values"}``.  ``values[name]`` contains ``rank_psrf``, ``classic_psrf``,
        ``ess_bulk``, ``ess_tail``, their ``ok`` flags and a ``reason`` when a
        value is ``None``.  No value is ever guessed.
    """
    n_chains = len(values_by_chain)
    base = {
        "estimator": RANK_PSRF_ESTIMATOR,
        "ess_estimator": BULK_TAIL_ESS_ESTIMATOR,
        "n_chains": n_chains,
        "tail_probability": float(tail),
    }
    if n_chains < 2:
        base.update(
            {
                "status": "not_computable",
                "reason": (
                    "rank-normalized R-hat and bulk/tail ESS compare between- "
                    "and within-chain variation and need at least 2 chains; "
                    f"{n_chains} usable chain(s) were found. No value is "
                    "reported because none exists."
                ),
                "values": {},
            }
        )
        return base

    names: List[str] = []
    seen = set()
    for mapping in values_by_chain:
        for name in mapping:
            key = str(name).strip()
            if key.lower() in NON_PARAMETER_COLUMNS or key in seen:
                continue
            seen.add(key)
            names.append(key)

    values: Dict[str, Any] = {}
    for name in names:
        present = [
            np.asarray(mapping[name], dtype=float).ravel()
            for mapping in values_by_chain
            if name in mapping
        ]
        if len(present) < 2:
            values[name] = {
                "rank_psrf": None,
                "classic_psrf": None,
                "ess_bulk": None,
                "ess_tail": None,
                "ok": None,
                "ess_bulk_ok": None,
                "ess_tail_ok": None,
                "reason": (
                    f"column {name!r} is present in {len(present)} of "
                    f"{n_chains} chains; multichain diagnostics need it in at least 2"
                ),
            }
            continue
        classic = gelman_rubin_psrf(present, split=True)
        rank = rank_normalized_split_rhat(present)
        e_bulk = bulk_ess(present)
        e_tail = tail_ess(present, tail=tail)
        reasons: List[str] = []
        if rank is None:
            reasons.append("rank-normalized R-hat is undefined (constant chains or too few draws)")
        if e_bulk is None:
            reasons.append("bulk ESS is undefined (constant chains or too few draws)")
        if e_tail is None:
            reasons.append("tail ESS is undefined (constant chains or too few draws)")
        entry: Dict[str, Any] = {
            "rank_psrf": None if rank is None else float(rank),
            "classic_psrf": None if classic is None else float(classic),
            "ess_bulk": None if e_bulk is None else float(e_bulk),
            "ess_tail": None if e_tail is None else float(e_tail),
            "ok": None,
            "ess_bulk_ok": None,
            "ess_tail_ok": None,
            "reason": "; ".join(reasons) if reasons else None,
        }
        if rank is not None and psrf_threshold is not None:
            entry["ok"] = bool(rank <= float(psrf_threshold))
        if e_bulk is not None and ess_threshold is not None:
            entry["ess_bulk_ok"] = bool(e_bulk >= float(ess_threshold))
        if e_tail is not None and ess_threshold is not None:
            entry["ess_tail_ok"] = bool(e_tail >= float(ess_threshold))
        values[name] = entry

    any_computable = any(
        entry["rank_psrf"] is not None
        or entry["ess_bulk"] is not None
        or entry["ess_tail"] is not None
        for entry in values.values()
    )
    base.update(
        {
            "status": "ok" if any_computable else "not_computable",
            "reason": (
                None
                if any_computable
                else "no parameter produced a computable rank-normalized diagnostic"
            ),
            "values": values,
        }
    )
    return base


# ---------------------------------------------------------------------------
# Topology comparison: split frequencies and ASDSF
# ---------------------------------------------------------------------------


def split_frequencies_from_trees(
    trees: Sequence[Any],
    burnin: int = 0,
) -> Dict[frozenset, float]:
    """Non-trivial split (bipartition) frequencies of a parsed tree sample.

    Builds on :mod:`beastxpy.treetools` (``_parse_newick`` /
    ``_clade_and_heights``); no second Newick parser is written here.  Each
    clade is canonicalised to the smaller of ``clade`` / ``complement`` so
    rooted and unrooted readings of the same bipartition agree.  Trivial
    splits (a single taxon, or all-but-one taxon) are excluded, matching the
    MrBayes ASDSF convention.

    Args:
        trees: ``treetools.NexusTree`` items (see
            ``treetools.parse_nexus_trees``).
        burnin: Number of leading *trees* to discard.

    Returns:
        ``{split: frequency}`` with frequencies in ``[0, 1]``.

    Raises:
        TraceError: If burn-in leaves no trees or the trees have no taxa.
    """
    from .treetools import _clade_and_heights, _iter_postorder, _parse_newick

    burnin = int(burnin)
    if burnin < 0:
        raise TraceError(f"burn-in must be >= 0, got {burnin}")
    all_trees = list(trees)
    sampled = all_trees[burnin:]
    if not sampled:
        raise TraceError(f"burn-in {burnin} leaves no trees (input has {len(all_trees)})")

    counts: Dict[frozenset, int] = {}
    all_tips: set = set()
    for tree in sampled:
        root, tips = _parse_newick(tree.newick, tree.translation)
        tip_set = frozenset(tips)
        all_tips |= set(tips)
        clades, _ = _clade_and_heights(root)
        seen: set = set()
        for node in _iter_postorder(root):
            if not node["children"]:
                continue
            clade = clades[id(node)]
            complement = tip_set - clade
            if not clade or not complement:
                continue  # root clade / full set: no bipartition
            split = min((clade, complement), key=lambda s: (len(s), sorted(s)))
            if len(split) <= 1:
                continue  # trivial split (single taxon)
            if split in seen:
                continue
            seen.add(split)
            counts[split] = counts.get(split, 0) + 1
    if not all_tips:
        raise TraceError("no tip labels found in the sampled trees")
    n = len(sampled)
    return {split: count / n for split, count in counts.items()}


def asdsf(split_frequency_runs: Sequence[Dict[frozenset, float]]) -> Optional[float]:
    """Average standard deviation of split frequencies (MrBayes ASDSF).

    For every non-trivial split, the frequency in each run (0.0 when absent)
    is compared across runs: ``SD_i = sqrt(sum_j (p_ij - mean_i)^2 / (m - 1))``
    with ``m`` runs; the ASDSF is the mean of ``SD_i`` over all splits in the
    union.  With two runs this is the familiar MrBayes average standard
    deviation of split frequencies; the formula generalises to more runs.

    Returns:
        The ASDSF (>= 0), or ``None`` when it is not computable: fewer than
        two runs, or no non-trivial splits in any run.  Callers must report
        ``None`` as ``not_computable`` and never substitute a number.
    """
    runs = [dict(freqs) for freqs in split_frequency_runs if freqs is not None]
    if len(runs) < 2:
        return None
    union: set = set()
    for run in runs:
        union |= set(run)
    if not union:
        return None
    m = len(runs)
    total = 0.0
    for split in union:
        per_run = [float(run.get(split, 0.0)) for run in runs]
        mean = math.fsum(per_run) / m
        variance = math.fsum((p - mean) ** 2 for p in per_run) / (m - 1)
        total += math.sqrt(variance)
    return total / len(union)


# ---------------------------------------------------------------------------
# Operator analysis table parsing (BEAST run log / stdout)
# ---------------------------------------------------------------------------

_OPERATOR_NAME_WIDTH_FALLBACK = 50
_OPERATOR_INT_FIELDS = frozenset({"attempts", "accepted", "rejected"})


def _operator_number(token: str) -> Optional[float]:
    """Parse one numeric cell of an operator table; ``''``/``'?'`` -> ``None``."""
    token = token.strip()
    if not token or token == "?":
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _operator_block_layout(header: str) -> Optional[Tuple[str, int]]:
    """Return ``("simple"|"detailed", name_field_width)`` for a table header."""
    if not header.lstrip().lower().startswith("operator"):
        return None
    tuning_pos = header.find("Tuning")
    width = tuning_pos if tuning_pos > 0 else _OPERATOR_NAME_WIDTH_FALLBACK
    if "#Tried" in header and "#Accept" in header:
        return ("detailed", width)
    if "Count" in header and "Pr(accept)" in header:
        return ("simple", width)
    return None


def _operator_simple_row(rest: List[str]) -> Optional[Dict[str, Any]]:
    """Map the tokens after the fixed-width name field of a simple-format row.

    Simple format (BEAST 1.10.4 ``Operator analysis``): columns are
    ``Tuning Count Time Time/Op Pr(accept)``; ``Tuning`` is absent for
    operators that are not tunable (topology swaps), giving 4 instead of 5
    trailing tokens.  ``Count`` is the number of *attempts*; BEAST reports no
    accept count here, so ``accepted`` stays ``None`` rather than being
    back-computed from ``Count * Pr(accept)`` (that would be a rounded guess).
    """
    if len(rest) == 5:
        tuning, attempts, _time, _time_op, rate = rest
    elif len(rest) == 4:
        tuning = None
        attempts, _time, _time_op, rate = rest
    else:
        return None
    attempts_value = _operator_number(attempts)
    if attempts_value is None:
        return None
    rate_value = _operator_number(rate)
    return {
        "tuning": _operator_number(tuning) if tuning is not None else None,
        "attempts": int(attempts_value),
        "accepted": None,
        "acceptance_rate": rate_value,
    }


def _operator_detailed_row(rest: List[str]) -> Optional[Dict[str, Any]]:
    """Map the tokens after the name field of the ``#Tried/#Accept`` format.

    Detailed format: ``Tuning #Tried #Accept #Reject Acceptance MeanTime
    MinTime MaxTime`` (``Tuning`` optional, so 8 or 7 trailing tokens).
    """
    if len(rest) == 8:
        tuning, tried, accept, reject, rate = rest[0], rest[1], rest[2], rest[3], rest[4]
    elif len(rest) == 7:
        tuning = None
        tried, accept, reject, rate = rest[0], rest[1], rest[2], rest[3]
    else:
        return None
    tried_value = _operator_number(tried)
    accept_value = _operator_number(accept)
    if tried_value is None:
        return None
    rate_value = _operator_number(rate)
    if rate_value is None and accept_value is not None:
        rate_value = accept_value / tried_value if tried_value else None
    rejected_value = _operator_number(reject)
    return {
        "tuning": _operator_number(tuning) if tuning is not None else None,
        "attempts": int(tried_value),
        "accepted": int(accept_value) if accept_value is not None else None,
        "rejected": int(rejected_value) if rejected_value is not None else None,
        "acceptance_rate": rate_value,
    }


def parse_operator_analysis(text: str) -> List[Dict[str, Any]]:
    """Parse the BEAST operator analysis table(s) out of a run log / stdout.

    Two table layouts are recognised:

    * the **simple** ``Operator analysis`` table that BEAST 1.10.4 writes to
      stdout at the end of an MCMC (``Tuning Count Time Time/Op Pr(accept)``,
      verified against the real fixture logs in ``tests/fixtures``);
    * the **detailed** variant with ``#Tried/#Accept/#Reject/Acceptance``
      columns (compatibility target; exercised by a labelled hand-written
      parser fixture, not observed in the committed real BEAST output).

    The operator name occupies a fixed-width leading field (taken from the
    header's ``Tuning`` column position), because names contain spaces.  When
    a log holds several tables (e.g. a resumed run), the **last** table wins:
    it describes the most complete schedule, and merging partial tables would
    fabricate totals BEAST never reported.

    Args:
        text: Full text of ``run.log`` (or any BEAST stdout capture).

    Returns:
        ``[{"name", "tuning", "attempts", "accepted", "acceptance_rate"}, …]``
        in table order.  ``accepted`` is ``None`` for the simple format
        (BEAST does not print an accept count there); ``acceptance_rate`` is
        ``None`` when BEAST printed ``?`` (zero attempts).  An empty list is
        returned when no operator table is present -- callers report
        "no operator table found" instead of inventing rates.
    """
    lines = text.splitlines()
    blocks: List[List[Dict[str, Any]]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped.lower().startswith("operator analysis"):
            index += 1
            continue
        # Locate the column header (next line starting with "Operator").
        header_index = index + 1
        while header_index < len(lines) and not lines[header_index].strip():
            header_index += 1
        if header_index >= len(lines):
            break
        layout = _operator_block_layout(lines[header_index])
        if layout is None:
            index = header_index + 1
            continue
        kind, name_width = layout
        row_parser = _operator_simple_row if kind == "simple" else _operator_detailed_row
        rows: List[Dict[str, Any]] = []
        cursor = header_index + 1
        while cursor < len(lines):
            raw = lines[cursor]
            if not raw.strip():
                break
            if raw.lstrip().lower().startswith(("operator analysis", "operator ")):
                break
            name = raw[:name_width].strip()
            rest = raw[name_width:].split()
            parsed = row_parser(rest) if name and rest else None
            if parsed is None:
                break
            parsed_record = {"name": name}
            parsed_record.update(parsed)
            rows.append(parsed_record)
            cursor += 1
        if rows:
            blocks.append(rows)
        index = cursor + 1
    return blocks[-1] if blocks else []
