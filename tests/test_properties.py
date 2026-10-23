"""Property-based tests for pure helper functions (Hypothesis).

These complement the example-based tests: they search the input space for
counterexamples instead of checking a handful of hand-picked values.  The
module is skipped when Hypothesis is not installed, so a minimal runtime
environment can still run the rest of the suite; CI installs it from
``requirements-test.txt``.
"""

from __future__ import annotations

import math
import re

import pytest

pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from beastxpy.utils import (  # noqa: E402
    hpd_interval,
    lognormal_mu_sigma,
    parse_number,
    sanitize_id,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.()\-]*$")
_PLAIN = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


@given(st.text(max_size=64))
@settings(max_examples=200, deadline=None)
def test_sanitize_id_is_total_and_preserves_length(text):
    cleaned = sanitize_id(text)
    assert isinstance(cleaned, str)
    assert len(cleaned) == len(text)
    assert _SAFE_ID.match(cleaned)
    # Characters that are already XML-safe are never rewritten.
    for before, after in zip(text, cleaned):
        if re.match(r"[A-Za-z0-9_.()\-]", before):
            assert before == after


@given(st.floats(allow_nan=False, allow_infinity=False))
@settings(max_examples=300, deadline=None)
def test_parse_number_round_trips_finite_floats(value):
    text = repr(value)
    parsed = parse_number(text, "x")
    assert math.isfinite(parsed)
    assert parsed == value


@given(st.integers(), st.sampled_from([" ", "\t", "\n"]))
@settings(max_examples=100, deadline=None)
def test_parse_number_rejects_padded_numbers(number, padding):
    text = f"{padding}{number}{padding}"
    assert _PLAIN.match(str(number))
    with pytest.raises(ValueError):
        parse_number(text, "x")


@given(
    st.floats(min_value=1e-6, max_value=1e4, allow_nan=False),
    st.floats(min_value=1e-6, max_value=1e3, allow_nan=False),
)
@settings(max_examples=200, deadline=None)
def test_lognormal_real_space_mean_round_trip(mean, stdev):
    mu, sigma = lognormal_mu_sigma({"mean": mean, "stdev": stdev, "meanInRealSpace": True})
    recovered = math.exp(mu + sigma * sigma / 2.0)
    assert recovered == pytest.approx(mean, rel=1e-9, abs=0.0)


@given(
    st.lists(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
        min_size=30,
        max_size=200,
    )
)
@settings(max_examples=100, deadline=None)
def test_hpd_interval_contains_bulk_and_is_ordered(values):
    lo, hi = hpd_interval(values, alpha=0.05)
    assert lo <= hi
    assert min(values) - 1e-9 <= lo <= max(values) + 1e-9
    assert min(values) - 1e-9 <= hi <= max(values) + 1e-9
