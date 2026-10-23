"""Unit tests for trace statistics (Tracer-style analysis)."""

from __future__ import annotations

import numpy as np
import pytest

from beastxpy.tracestats import (
    act_estimate,
    auto_burnin,
    compute_trace_stats,
    ess_estimate,
    geweke_z,
    parse_trace_log,
)


class TestTraceParsing:
    """Trace log file parsing."""

    def test_parse_simple_log(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text(
            "state\tposterior\tlikelihood\tprior\trootHeight\n"
            "0\t-10.5\t-20.0\t9.5\t1.2\n"
            "1\t-10.4\t-20.1\t9.7\t1.3\n"
            "2\t-10.3\t-20.2\t9.9\t1.4\n"
        )
        result = parse_trace_log(str(log))
        assert "posterior" in result["values"]
        assert len(result["values"]["posterior"]) == 3
        assert "rootHeight" in result["values"]


class TestAutocorrelation:
    """ACT/ESS estimates against the AR(1) analytic value."""

    def test_act_estimate_for_iid_is_near_one(self):
        # The full AR(1) theoretical sweep lives in tests/test_numerics.py;
        # here we only pin the lower bound that iid draws must approach.
        rng = np.random.default_rng(42)
        chain = rng.normal(0, 1, 100000)
        assert act_estimate(chain) == pytest.approx(1.0, abs=0.1)

    def test_ess_estimate_is_n_over_act(self):
        rng = np.random.default_rng(42)
        chain = rng.normal(0, 1, 10000)
        assert ess_estimate(chain) == pytest.approx(len(chain) / act_estimate(chain), rel=1e-9)
        assert 0 < ess_estimate(chain) <= len(chain)


class TestGeweke:
    """Geweke Z-score and its not-computable state."""

    def test_geweke_z(self):
        rng = np.random.default_rng(42)
        chain = rng.normal(0, 1, 10000)
        z = geweke_z(chain)
        assert isinstance(z, float)
        # Stationary halves: no evidence of a different mean.
        assert abs(z) < 3.0

    def test_geweke_detects_a_shifted_second_half(self):
        rng = np.random.default_rng(7)
        chain = np.concatenate([rng.normal(0, 1, 5000), rng.normal(3, 1, 5000)])
        assert geweke_z(chain) < -5.0

    def test_short_chain_is_not_computable_not_zero(self):
        # A z of 0.0 means "the halves agree"; a 10-sample chain cannot say
        # that, so the honest result is None.
        assert geweke_z(np.arange(10.0)) is None


class TestAutoBurnin:
    """Automatic burn-in detection."""

    def test_burnin_detection(self):
        rng = np.random.default_rng(42)
        # Converging chain: starts high, settles down.
        chain = np.concatenate([rng.normal(5, 1, 500), rng.normal(0, 1, 5000)])
        values = {"param": chain}
        burnin = auto_burnin(values)
        assert 0 < burnin < len(chain)
        # The transient occupies the first 500 draws; the probe should not
        # stop at zero.
        assert burnin >= 100


class TestComputeTraceStats:
    """Full trace statistics computation."""

    def test_compute_stats(self):
        rng = np.random.default_rng(42)
        chain = rng.normal(0, 1, 5000)
        result = compute_trace_stats({"param": chain})
        assert "columns" in result
        cols = result["columns"]
        assert len(cols) == 1
        stats = cols[0]
        assert stats["name"] == "param"
        assert "mean" in stats
        assert "ess" in stats


class TestAutoBurninNotComputable:
    """M7: an uncomputable Geweke score must not masquerade as burn-in 0."""

    def test_short_chain_returns_none(self):
        assert auto_burnin({"param": np.arange(50.0)}) is None

    def test_constant_chain_returns_none(self):
        assert auto_burnin({"param": np.ones(1000)}) is None

    def test_compute_trace_stats_reports_not_computable(self):
        result = compute_trace_stats({"param": np.ones(1000)}, burnin=None)
        assert result["burnin"] == 0
        assert result["burnin_status"] == "not_computable"
        assert any("not computable" in warning for warning in result["warnings"])

    def test_explicit_burnin_status(self):
        result = compute_trace_stats(
            {"param": np.random.default_rng(1).normal(size=500)}, burnin=10
        )
        assert result["burnin_status"] == "explicit"
