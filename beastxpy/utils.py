"""Shared utility functions for BeastXPy.

Provides id generation, XML (de)serialisation helpers, distribution
statistics and canonical formatting helpers used across the package.
"""

from __future__ import annotations

import hashlib
import math
import re
import warnings
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------

_SAFE_ID = re.compile(r"[^A-Za-z0-9_\-\.\(\)]")


def sanitize_id(name: str) -> str:
    """Return ``name`` with XML-unsafe characters replaced by underscores."""
    return _SAFE_ID.sub("_", name)


def unique_id(base: str, used: set) -> str:
    """Return a unique id starting from ``base``, mutating ``used``."""
    candidate = base
    i = 2
    while candidate in used:
        candidate = f"{base}{i}"
        i += 1
    used.add(candidate)
    return candidate


def param_id(partition_id: str, name: str) -> str:
    """Id of a parameter belonging to ``partition_id`` ('' -> global id)."""
    return f"{partition_id}.{name}" if partition_id else name


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def escape_xml_text(text: str) -> str:
    """Escape a string for XML text content."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_xml_attr(text: str) -> str:
    """Escape a string for an XML attribute value."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def format_number(value: Any) -> str:
    """Format a number for XML attributes, dropping trailing zeros.

    Non-finite floats (``inf``/``nan``) are rejected with a descriptive
    ``ValueError`` instead of leaking ``OverflowError``/``ValueError`` from
    ``int()``.  BEAST bounds that genuinely need infinities must be written by
    a call site that knows the element accepts them.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(
                f"cannot format non-finite number {value!r} for XML "
                f"(inf/nan are not valid in BEAST attributes)"
            )
        if number == int(number) and abs(number) < 1e15:
            return str(int(number))
        return repr(number)
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        return str(int(value))
    return str(value)


def format_param_value(value: Union[float, str, List[float]]) -> str:
    """Format a parameter value (scalar or vector) as a space-joined string."""
    if isinstance(value, (list, tuple)):
        return " ".join(format_number(v) for v in value)
    return format_number(value)


def format_indent(xml_str: str, indent: str = "    ") -> str:
    """Pretty-print an XML string with 4-space indentation.

    Text content and attribute values are preserved (and escaped) even when an
    element carries attributes -- e.g. ``<sequence name="s1">ACGT</sequence>``
    keeps its ``ACGT`` payload.  Mixed content (text *and* child elements) is
    emitted verbatim on a single line rather than being silently dropped.
    """
    import xml.etree.ElementTree as ET
    from defusedxml import ElementTree as DT

    try:
        root = DT.fromstring(xml_str)
    except DT.ParseError:
        return xml_str
    lines: List[str] = []

    def _attrs(node) -> str:
        return "".join(
            f' {key}="{escape_xml_attr(str(value))}"' for key, value in node.attrib.items()
        )

    def _walk(node, depth: int) -> None:
        pad = indent * depth
        children = list(node)
        raw_text = node.text or ""
        has_text = bool(raw_text.strip())
        attrs = _attrs(node)
        if not children:
            if has_text:
                lines.append(
                    f"{pad}<{node.tag}{attrs}>{escape_xml_text(raw_text.strip())}" f"</{node.tag}>"
                )
            else:
                lines.append(f"{pad}<{node.tag}{attrs}/>")
        elif has_text:
            # Mixed content: keep the exact ordering of text/tails/children.
            inline = ET.tostring(node, encoding="unicode").strip()
            lines.append(f"{pad}{inline}")
        else:
            lines.append(f"{pad}<{node.tag}{attrs}>")
            for child in children:
                _walk(child, depth + 1)
            lines.append(f"{pad}</{node.tag}>")
        if node is not root and node.tail and node.tail.strip():
            lines.append(f"{pad}{escape_xml_text(node.tail.strip())}")

    _walk(root, 0)
    return "\n".join(lines)


def strip_xml_comments(xml_str: str) -> str:
    """Remove XML comment blocks (used by the golden normaliser)."""
    return re.sub(r"<!--.*?-->", "", xml_str, flags=re.DOTALL)


_NUMERIC_ATTRIBUTES = frozenset(
    {
        "value",
        "lower",
        "upper",
        "mean",
        "meanInRealSpace",
        "stdev",
        "sigma",
        "shape",
        "scale",
        "offset",
        "alpha",
        "beta",
        "lambda",
        "kappa",
        "df",
        "weight",
        "windowSize",
        "rates",
        "frequencies",
        "dimension",
        "from",
        "to",
        "logEvery",
        "chainLength",
    }
)


def normalize_xml_for_compare(xml_str: str) -> str:
    """Normalise XML for golden comparison.

    Drops comments, collapses whitespace between attributes, sorts attributes
    alphabetically, and normalises *numeric* attributes (whitelist
    ``_NUMERIC_ATTRIBUTES``).  Identifier attributes (``id``, ``idref``,
    ``name``, ``taxon``, ``spec`` ...) are never passed through ``float()``, so
    distinct ids such as ``"0001"`` and ``"1"`` stay distinct.
    """
    import xml.etree.ElementTree as ET
    from defusedxml import ElementTree as DT

    text = strip_xml_comments(xml_str)

    def _round(value: str) -> str:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return value
        if not math.isfinite(f):
            return value
        if f == int(f) and abs(f) < 1e15:
            return str(int(f))
        return f"{f:.15g}"

    tree = DT.fromstring(text)

    def _norm(node: ET.Element) -> None:
        attrs = {
            key: (_round(value) if key in _NUMERIC_ATTRIBUTES else value)
            for key, value in node.attrib.items()
        }
        node.attrib.clear()
        node.attrib.update({key: attrs[key] for key in sorted(attrs)})
        if node.text:
            node.text = " ".join(node.text.split())
        for child in node:
            _norm(child)

    _norm(tree)
    return ET.tostring(tree, encoding="unicode")


# ---------------------------------------------------------------------------
# Distribution statistics
# ---------------------------------------------------------------------------


def gev_mean(
    chain: np.ndarray, alpha: float = 0.05, max_lag: Optional[int] = None
) -> Optional[float]:
    """Deprecated alias of :func:`beastxpy.tracestats.geweke_z`.

    Historically misnamed (it never computed a GEV mean) and duplicated the
    trace tool with different windows.  It now delegates so both entry points
    always agree; ``alpha``/``max_lag`` are accepted for backwards
    compatibility and ignored.  ``None`` means the z-score is not computable
    (for example a chain shorter than 100 samples).
    """
    from .tracestats import geweke_z

    return geweke_z(np.asarray(chain, dtype=float))


def _spectral_var(chain: np.ndarray, max_lag: Optional[int] = None) -> float:
    """Spectral variance estimate from the ACT of a chain segment."""
    from .tracestats import act_estimate

    act = act_estimate(chain, max_lag=max_lag)
    if not math.isfinite(act):
        return float("nan")
    return float(chain.var(ddof=1) * act)


def hpd_interval(samples: np.ndarray, alpha: float = 0.05) -> Tuple[float, float]:
    """Return the (1-alpha) highest-posterior-density interval of ``samples``.

    ``alpha`` is the *tail* probability (0.05 -> 95% HPD) and must lie
    strictly between 0 and 1.  Non-finite samples are excluded; if fewer than
    two finite samples remain a ``(nan, nan)`` interval is returned with a
    warning.  When the HPD window covers every sample (small ``n``) the full
    ``(min, max)`` range is returned with a warning instead of raising.

    The window contains ``ceil((1 - alpha) * n)`` order statistics.  Other
    implementations (including Tracer) round the window size differently, so
    on small samples the endpoints can differ by one observation; for the
    sample sizes at which HPDs are normally reported the difference is
    negligible.  This is a shortest-interval HPD, not an equal-tailed
    interval (which is reported separately by ``assess``).
    """
    if not (0.0 < float(alpha) < 1.0):
        raise ValueError(f"alpha must be in (0, 1) (tail probability), got {alpha!r}")
    arr = np.asarray(samples, dtype=float).ravel()
    finite = arr[np.isfinite(arr)]
    n_dropped = int(arr.size - finite.size)
    if n_dropped:
        warnings.warn(
            f"hpd_interval: dropped {n_dropped} non-finite sample(s)",
            RuntimeWarning,
            stacklevel=2,
        )
    if finite.size == 0:
        warnings.warn(
            "hpd_interval: no finite samples; returning (nan, nan)",
            RuntimeWarning,
            stacklevel=2,
        )
        return (float("nan"), float("nan"))
    sorted_arr = np.sort(finite)
    n = int(sorted_arr.size)
    if n < 2:
        warnings.warn(
            f"hpd_interval: only {n} finite sample(s); interval is degenerate",
            RuntimeWarning,
            stacklevel=2,
        )
        return (float(sorted_arr[0]), float(sorted_arr[0]))
    n_in = max(1, int(np.ceil((1.0 - alpha) * n)))
    n_in = min(n_in, n)
    if n_in >= n:
        warnings.warn(
            f"hpd_interval: HPD window covers all {n} sample(s); " f"returning the full range",
            RuntimeWarning,
            stacklevel=2,
        )
        return (float(sorted_arr[0]), float(sorted_arr[-1]))
    # The HPD window includes n_in samples (indices idx .. idx+n_in-1);
    # its width is sorted_arr[idx+n_in-1] - sorted_arr[idx].  Vectorise over
    # all valid starting positions (0 .. n-n_in) by subtracting shifted arrays.
    widths = sorted_arr[n_in - 1 :] - sorted_arr[: n - n_in + 1]
    idx = int(np.argmin(widths))
    return (float(sorted_arr[idx]), float(sorted_arr[idx + n_in - 1]))


def quantile(samples: np.ndarray, q: float) -> float:
    """Return quantile ``q`` of ``samples``."""
    return float(np.quantile(np.asarray(samples, dtype=float), q))


def wasserstein_distance(p: np.ndarray, q: np.ndarray) -> float:
    """1-Wasserstein distance between two empirical distributions."""
    from scipy.stats import wasserstein_distance as _wd

    return float(_wd(np.asarray(p, dtype=float), np.asarray(q, dtype=float)))


# ---------------------------------------------------------------------------
# Generic canonicalisation
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    """Canonical JSON dump with sorted keys for stable hashing."""
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(data: str) -> str:
    """SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


_PLAIN_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def parse_number(value: Any, name: str) -> float:
    """Parse a numeric YAML value, rejecting bools with a clear message.

    Non-finite values (``nan``/``Infinity``) and Python's lenient literal
    forms (``1_000``, ``" 5 "``) are rejected: they silently propagate into
    XML attributes where BEAST cannot read them.
    """
    if isinstance(value, bool):
        raise ValueError(f"'{name}' must be a number, got boolean {value!r}")
    if isinstance(value, str):
        if not _PLAIN_NUMBER.match(value.strip()) or value.strip() != value:
            raise ValueError(f"'{name}' must be a plain decimal number, got {value!r}")
        value = value.strip()
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' must be a number, got {value!r}") from None
    if not math.isfinite(result):
        raise ValueError(f"'{name}' must be finite, got {value!r}")
    return result


def valid_xml_charset_check(name: str) -> Optional[str]:
    """Return an error string if ``name`` contains characters illegal in XML."""
    bad = [ch for ch in name if ord(ch) < 0x20 and ch not in "\t\n\r"]
    if bad:
        return f"Taxon name {name!r} contains XML-illegal control character(s): {bad!r}"
    return None


def distribution_offset(params: Dict[str, Any], offset: Optional[float] = None) -> float:
    """Resolve the distribution offset, honouring an explicit ``offset=0``.

    ``offset or params.get(...)`` treated 0 as "not supplied"; this helper uses
    ``is None`` so callers can deliberately request a zero offset even when the
    parameter dict carries a non-zero one.
    """
    if offset is not None:
        return float(offset)
    return float(params.get("offset", 0.0) or 0.0)


def lognormal_mu_sigma(params: Dict[str, Any]) -> Tuple[float, float]:
    """BEAST ``LogNormalDistributionModel`` real-space -> log-space conversion.

    With ``meanInRealSpace`` BEAST treats ``mean``/``stdev`` as the mean and
    standard deviation of the real-space variable::

        sigma^2 = log(1 + S^2 / M^2)
        mu      = log(M) - sigma^2 / 2

    (``LogNormalDistributionModel.java`` ``calculateMu``/``calculateSigma``).
    Without it, ``mean`` is already ``mu`` and ``stdev``/``S`` is ``sigma``.
    """
    mean = float(params.get("mean", 1.0))
    stdev = float(params.get("stdev", params.get("S", 1.0)))
    if params.get("meanInRealSpace", False) in (True, "true", 1):
        if mean <= 0:
            raise ValueError(f"lognormal real-space mean must be > 0, got {mean!r}")
        sigma_squared = math.log1p((stdev * stdev) / (mean * mean))
        mu = math.log(mean) - sigma_squared / 2.0
        return mu, math.sqrt(sigma_squared)
    return mean, stdev


def exponential_mean(params: Dict[str, Any]) -> float:
    """Return the mean of an exponential distribution.

    BEAST's ``ExponentialDistributionModel`` is parameterised by its *mean*.
    ``rate`` is accepted as a convenience and converted as ``mean = 1/rate`` in
    every consumer (sampling, quantiles and the mean).
    """
    if "mean" in params:
        mean = float(params["mean"])
    elif "rate" in params:
        rate = float(params["rate"])
        mean = 1.0 / rate if rate != 0 else float("inf")
    else:
        mean = 1.0
    if not (mean > 0) or not math.isfinite(mean):
        raise ValueError(f"exponential mean must be finite and > 0, got {mean!r}")
    return mean


def distribution_quantiles(dist_type: str, params: Dict[str, Any], offset: Optional[float] = None):
    """Approximate 2.5% / 97.5% quantiles for supported distributions.

    Every branch applies the same offset as :func:`distribution_mean` and the
    diagnostics sampler.  Returns ``None`` when the distribution has no
    defined quantiles (e.g. ``one_on_x``) or the parameters are invalid.

    Args:
        dist_type: Distribution type name.
        params: Distribution parameters.
        offset: Explicit offset (``None`` = use ``params['offset']``).

    Returns:
        (q025, q975) or ``None``.
    """
    from scipy import stats

    off = distribution_offset(params, offset)
    try:
        if dist_type in ("normal", "gaussian"):
            mean = float(params.get("mean", 0.0))
            stdev = float(params.get("stdev", params.get("sigma", 1.0)))
            return (
                off + stats.norm.ppf(0.025, mean, stdev),
                off + stats.norm.ppf(0.975, mean, stdev),
            )
        if dist_type == "lognormal":
            mu, sigma = lognormal_mu_sigma(params)
            return (
                off + stats.lognorm.ppf(0.025, sigma, scale=math.exp(mu)),
                off + stats.lognorm.ppf(0.975, sigma, scale=math.exp(mu)),
            )
        if dist_type == "uniform":
            lower = float(params.get("lower", 0.0))
            upper = float(params.get("upper", 1.0))
            length = upper - lower
            return (off + lower + 0.025 * length, off + lower + 0.975 * length)
        if dist_type == "exponential":
            mean = exponential_mean(params)
            return (
                off + stats.expon.ppf(0.025, scale=mean),
                off + stats.expon.ppf(0.975, scale=mean),
            )
        if dist_type == "gamma":
            shape = float(params.get("shape", params.get("alpha", 1.0)))
            scale = float(params.get("scale", params.get("beta", 1.0)))
            return (
                off + stats.gamma.ppf(0.025, shape, scale=scale),
                off + stats.gamma.ppf(0.975, shape, scale=scale),
            )
        if dist_type == "beta":
            a = float(params.get("alpha", params.get("a", 2.0)))
            b = float(params.get("beta", params.get("b", 2.0)))
            return (
                off + stats.beta.ppf(0.025, a, b),
                off + stats.beta.ppf(0.975, a, b),
            )
        if dist_type == "inverse_gamma":
            shape = float(params.get("shape", params.get("alpha", 2.0)))
            scale = float(params.get("scale", params.get("beta", 1.0)))
            return (
                off + stats.invgamma.ppf(0.025, shape, scale=scale),
                off + stats.invgamma.ppf(0.975, shape, scale=scale),
            )
        if dist_type == "poisson":
            mu = float(params.get("mean", params.get("mu", 1.0)))
            return (
                off + stats.poisson.ppf(0.025, mu),
                off + stats.poisson.ppf(0.975, mu),
            )
        if dist_type == "half_normal":
            mean = float(params.get("mean", 0.0))
            stdev = float(params.get("stdev", params.get("sigma", 1.0)))
            # BEAST's HalfNormalDistribution(mean, stdev) is a normal density
            # truncated at zero, not a zero-centred half-normal.
            a = (0.0 - mean) / stdev
            return (
                off + stats.truncnorm.ppf(0.025, a, math.inf, loc=mean, scale=stdev),
                off + stats.truncnorm.ppf(0.975, a, math.inf, loc=mean, scale=stdev),
            )
        if dist_type == "laplace":
            mean = float(params.get("mean", 0.0))
            scale = float(params.get("scale", 1.0))
            return (
                off + stats.laplace.ppf(0.025, loc=mean, scale=scale),
                off + stats.laplace.ppf(0.975, loc=mean, scale=scale),
            )
        if dist_type == "chi_square":
            df = float(params.get("df", params.get("degrees_of_freedom", 1.0)))
            return (
                off + stats.chi2.ppf(0.025, df),
                off + stats.chi2.ppf(0.975, df),
            )
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return None


def distribution_mean(dist_type: str, params: Dict[str, Any], offset: Optional[float] = None):
    """Approximate mean of a supported distribution, or None.

    Uses the same offset resolution and parameter interpretation as
    :func:`distribution_quantiles`.
    """
    from scipy import stats  # noqa: F401  (kept for parity with quantiles)

    off = distribution_offset(params, offset)
    try:
        if dist_type in ("normal", "gaussian"):
            return off + float(params.get("mean", 0.0))
        if dist_type == "lognormal":
            if params.get("meanInRealSpace", False) in (True, "true", 1):
                return off + float(params.get("mean", 1.0))
            mu, sigma = lognormal_mu_sigma(params)
            return off + math.exp(mu + sigma**2 / 2.0)
        if dist_type == "uniform":
            lower = float(params.get("lower", 0.0))
            upper = float(params.get("upper", 1.0))
            return off + (lower + upper) / 2.0
        if dist_type == "exponential":
            return off + exponential_mean(params)
        if dist_type == "gamma":
            shape = float(params.get("shape", params.get("alpha", 1.0)))
            scale = float(params.get("scale", params.get("beta", 1.0)))
            return off + shape * scale
        if dist_type == "beta":
            a = float(params.get("alpha", params.get("a", 2.0)))
            b = float(params.get("beta", params.get("b", 2.0)))
            return off + a / (a + b)
        if dist_type == "inverse_gamma":
            shape = float(params.get("shape", params.get("alpha", 2.0)))
            scale = float(params.get("scale", params.get("beta", 1.0)))
            return off + scale / (shape - 1.0) if shape > 1.0 else None
        if dist_type == "poisson":
            return off + float(params.get("mean", params.get("mu", 1.0)))
        if dist_type == "half_normal":
            mean = float(params.get("mean", 0.0))
            stdev = float(params.get("stdev", params.get("sigma", 1.0)))
            a = (0.0 - mean) / stdev
            return off + float(stats.truncnorm.mean(a, math.inf, loc=mean, scale=stdev))
        if dist_type == "laplace":
            return off + float(params.get("mean", 0.0))
        if dist_type == "chi_square":
            return off + float(params.get("df", params.get("degrees_of_freedom", 1.0)))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return None
