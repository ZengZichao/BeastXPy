"""Regression tests for the numeric, tree and diagnostics layers.

Covers numeric/trace/tree statistics and diagnostics.  Every test asserts the
*required behaviour*, in particular the exact examples quoted
there:

* AR(1) ACT for rho = 0.9 / 0.99 / 0.999 / 0.9999,
* HPD for n = 1..19 and an oversized burn-in,
* lognormal real-space median 0.894 for mean=1, S=0.5,
* MCC for ``((A:1,B:1):1,C:2)`` is ``((A,B),C)`` with root age 2.0,
* one NaN must not turn ESS into n,
* offset-consistent quantiles,
* 1000+ tip Newick parsing.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import beastxpy.treetools as treetools
from beastxpy.diagnostics import (
    CalibrationDiagnostics,
    calibration_children,
    calibration_clade_tree,
    diagnose_config,
    safe_filename,
    sample_distribution,
)
from beastxpy.models import (
    CalibrationPoint,
    ControlConfig,
    DistributionConfig,
)
from beastxpy.tracestats import (
    TraceError,
    _mode_estimate,
    act_estimate,
    compute_trace_stats,
    ess_estimate,
    format_stats_table,
    parse_trace_log,
    trace_to_json,
    write_trace_plot,
)
from beastxpy.treetools import (
    NexusTree,
    TreeToolsError,
    annotate_tree_command,
    build_mcc_tree,
    clade_statistics,
    combine_logs_command,
    parse_nexus_trees,
    run_tree_annotator,
)
from beastxpy.utils import (
    distribution_mean,
    distribution_quantiles,
    format_indent,
    format_number,
    gev_mean,
    hpd_interval,
    lognormal_mu_sigma,
    normalize_xml_for_compare,
    parse_number,
)

try:
    import scipy.stats as scipy_stats
except ImportError:  # pragma: no cover - scipy is a project dependency
    scipy_stats = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ar1(rho: float, n: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=n)
    chain = np.empty(n, dtype=float)
    chain[0] = noise[0]
    for i in range(1, n):
        chain[i] = rho * chain[i - 1] + noise[i]
    return chain


def _nexus(trees, translate=None, extra_translate=None) -> str:
    lines = ["#NEXUS", "Begin trees;"]
    if extra_translate is None:
        if translate:
            lines.append("  Translate")
            lines.extend(f"    {num} {name}," for num, name in translate.items())
            lines.append("  ;")
        for index, tree in enumerate(trees):
            lines.append(f"  tree STATE_{index} = [&R] {tree};")
    else:
        for block_index, (table, block_trees) in enumerate(
            [(translate, [trees[0]]), (extra_translate, [trees[1]])]
        ):
            if table:
                lines.append("  Translate")
                lines.extend(f"    {num} {name}," for num, name in table.items())
                lines.append("  ;")
            for index, tree in enumerate(block_trees):
                lines.append(
                    f"  tree STATE_{block_index * 10 + index} = [&{'R' if block_index == 0 else 'U'}] {tree};"
                )
    lines.append("End;")
    return "\n".join(lines)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _cal(name, taxa, **kwargs):
    return CalibrationPoint(name=name, taxa=list(taxa), **kwargs)


def _dist(kind, params, offset=0.0):
    return DistributionConfig(type=kind, parameters=dict(params), offset=offset)


def _make_config(calibrations, analysis_name="test"):
    return SimpleNamespace(
        analysis_name=analysis_name,
        control=ControlConfig(),
        diagnostics={},
        calibrations=list(calibrations),
    )


SENSITIVITY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<beast version="1.10.4">
  <taxa id="taxa"><taxon id="t1"/><taxon id="t2"/></taxa>
  <taxa id="A"><taxon idref="t1"/><taxon idref="t2"/></taxa>
  <taxa id="B"><taxon idref="t1"/></taxa>
  <treeModel id="treeModel">
    <taxa idref="taxa"/>
    <parameter id="treeModel.rootHeight" value="1.0"/>
  </treeModel>
  <tmrcaStatistic id="tmrca(A)" includeStem="false"><mrca><taxa idref="A"/></mrca><treeModel idref="treeModel"/></tmrcaStatistic>
  <tmrcaStatistic id="tmrca(B)" includeStem="false"><mrca><taxa idref="B"/></mrca><treeModel idref="treeModel"/></tmrcaStatistic>
  <treeLikelihood id="treeLikelihood"><treeModel idref="treeModel"/></treeLikelihood>
  <prior>
    <uniformPrior lower="0" upper="10"><statistic idref="tmrca(A)"/></uniformPrior>
    <uniformPrior lower="0" upper="5"><statistic idref="tmrca(B)"/></uniformPrior>
    <oneOnXPrior><parameter idref="treeModel.rootHeight"/></oneOnXPrior>
  </prior>
  <likelihood><treeLikelihood idref="treeLikelihood"/></likelihood>
  <constrainedTaxa>
    <tmrca monophyletic="true"><taxa idref="A"/></tmrca>
    <tmrca monophyletic="true"><taxa idref="B"/></tmrca>
  </constrainedTaxa>
  <operators>
    <scaleOperator><parameter idref="treeModel.rootHeight"/></scaleOperator>
  </operators>
  <mcmc id="mcmc"/>
</beast>
"""


# ---------------------------------------------------------------------------
# finite-only statistics, chain detection, state exclusion
# ---------------------------------------------------------------------------


class TestTraceFiniteAndChains:
    def test_one_nan_does_not_reset_ess(self):
        chain = _ar1(0.9, 20000)
        chain[100] = np.nan
        result = compute_trace_stats({"x": chain}, burnin=0)
        col = result["columns"][0]
        assert col["n_nonfinite"] == 1
        assert col["n"] == 19999
        # True ACT ~19 / ESS ~1053.  A single NaN must not give ACT=1.
        assert 12.0 < col["act"] < 30.0, col
        assert 600.0 < col["ess"] < 1600.0, col
        assert any("non-finite" in warning for warning in result["warnings"])

    def test_all_non_finite_is_not_a_perfect_chain(self):
        result = compute_trace_stats({"x": np.array([np.nan, np.inf, -np.inf])}, burnin=0)
        col = result["columns"][0]
        assert math.isnan(col["ess"])
        assert math.isnan(col["act"])
        assert any("no finite values" in warning for warning in result["warnings"])

    def test_act_estimate_rejects_non_finite(self):
        with pytest.raises(TraceError):
            act_estimate(np.array([1.0, np.nan, 2.0, 3.0]))
        with pytest.raises(TraceError):
            ess_estimate(np.array([1.0, np.inf, 2.0, 3.0]))

    def test_state_column_is_not_a_parameter(self):
        values = {"state": np.arange(1000.0), "x": np.random.default_rng(0).normal(size=1000)}
        result = compute_trace_stats(values, burnin=0)
        assert [column["name"] for column in result["columns"]] == ["x"]
        assert not any("state" in warning for warning in result["warnings"])
        table = format_stats_table(result)
        assert "state" not in table

    def test_concatenated_chains_are_analysed_per_chain(self, tmp_path):
        rows = ["state\tx"]
        for index in range(200):
            rows.append(f"{index}\t{(index % 2) * 2.0 - 1.0}")
        for index in range(200):
            rows.append(f"{index}\t{(index % 2) * 2.0 - 1.0}")
        log = _write(tmp_path, "concat.log", "\n".join(rows) + "\n")
        parsed = parse_trace_log(str(log))
        assert len(parsed["chains"]) == 2
        assert parsed["chains"][1]["start"] == 200
        result = compute_trace_stats(
            parsed["values"],
            burnin=0,
            chains=[(c["start"], c["end"]) for c in parsed["chains"]],
        )
        assert result["n_chains"] == 2
        col = result["columns"][0]
        # Two independent IID-ish chains: ESS ~ n.
        assert col["act"] == pytest.approx(1.0, abs=0.2)
        assert col["ess"] > 350.0
        assert any("concatenated chains" in warning for warning in result["warnings"])

    def test_repeated_header_starts_a_new_chain(self, tmp_path):
        text = "state\tx\n0\t1\n1\t2\nstate\tx\n0\t3\n1\t4\n"
        log = _write(tmp_path, "repeat.log", text)
        parsed = parse_trace_log(str(log))
        assert [chain["start"] for chain in parsed["chains"]] == [0, 2]

    def test_mode_is_an_observed_value_for_discrete_samples(self):
        samples = np.concatenate([np.full(900, 3.0), np.arange(100.0)])
        assert _mode_estimate(samples) == 3.0

    def test_json_is_strict(self):
        payload = {
            "act": float("inf"),
            "mean": float("nan"),
            "n": np.int64(5),
            "values": np.array([1.0, np.inf]),
        }
        text = trace_to_json(payload)
        assert "Infinity" not in text and "NaN" not in text
        decoded = json.loads(
            text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
        )
        assert decoded["act"] is None and decoded["mean"] is None

    def test_table_formats_infinities_safely(self):
        result = {
            "burnin": 0,
            "columns": [
                {
                    "name": "x",
                    "mean": float("inf"),
                    "ess": float("nan"),
                    "act": float("inf"),
                    "hpd_lower": float("nan"),
                    "hpd_upper": float("nan"),
                }
            ],
            "warnings": [],
        }
        table = format_stats_table(result)
        assert "inf" not in table.replace("information", "")
        assert "n/a" in table

    def test_constant_columns_do_not_crash_plot(self, tmp_path):
        out = tmp_path / "plot.png"
        with pytest.raises(TraceError):
            write_trace_plot({"state": np.arange(10.0), "flag": np.ones(10)}, 0, str(out))
        assert not out.exists()


# ---------------------------------------------------------------------------
# HPD and burn-in for small samples
# ---------------------------------------------------------------------------


class TestHpdSmallSamples:
    @pytest.mark.parametrize("n", list(range(1, 20)) + [20, 100])
    def test_no_crash_for_any_small_n(self, n):
        samples = np.arange(n, dtype=float)
        with pytest.warns(RuntimeWarning) if n < 20 else _nullcontext():
            lo, hi = hpd_interval(samples, alpha=0.05)
        assert math.isfinite(lo) and math.isfinite(hi)
        if n == 1:
            assert lo == hi == 0.0
        elif n < 20:
            assert lo == pytest.approx(0.0)
            assert hi == pytest.approx(n - 1.0)
        else:
            assert 0.0 <= lo <= hi <= n - 1.0
            assert hi - lo >= 0.95 * (n - 1) - 1.0

    @pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5])
    def test_invalid_alpha_raises(self, alpha):
        with pytest.raises(ValueError):
            hpd_interval(np.arange(100.0), alpha=alpha)

    def test_non_finite_values_are_excluded(self):
        with pytest.warns(RuntimeWarning):
            lo, hi = hpd_interval(np.array([1.0, np.nan, 2.0, 3.0, np.inf]), alpha=0.05)
        assert (lo, hi) == (1.0, 3.0)

    def test_oversized_burnin_does_not_crash(self):
        chain = np.random.default_rng(1).normal(size=500)
        result = compute_trace_stats({"x": chain}, burnin=499)
        assert result["burnin"] == 498
        assert result["columns"][0]["n"] == 2
        assert any("burn-in 499" in warning for warning in result["warnings"])

    def test_nineteen_samples_warns_about_full_range(self):
        result = compute_trace_stats({"x": np.arange(19, dtype=float)}, burnin=0)
        assert any("full sample range" in warning for warning in result["warnings"])


class _nullcontext:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# Geyer initial-positive-sequence ACT
# ---------------------------------------------------------------------------


class TestGeyerAct:
    @pytest.mark.parametrize(
        "rho,n,relative_tolerance",
        [(0.9, 20000, 0.25), (0.99, 100000, 0.25), (0.999, 200000, 0.25), (0.9999, 200000, 0.35)],
    )
    def test_ar1_act_matches_theory(self, rho, n, relative_tolerance):
        chain = _ar1(rho, n, seed=11)
        true_act = (1.0 + rho) / (1.0 - rho)
        info = act_estimate(chain, return_info=True)
        assert info["act"] == pytest.approx(true_act, rel=relative_tolerance), info
        assert ess_estimate(chain) == pytest.approx(n / info["act"], rel=1e-9)

    def test_iid_chain_act_is_one(self):
        chain = np.random.default_rng(3).normal(size=50000)
        assert act_estimate(chain) == pytest.approx(1.0, abs=0.25)

    def test_autocovariance_is_unbiased_at_lag_zero(self):
        from beastxpy.tracestats import _autocovariance

        chain = _ar1(0.5, 5000, seed=4)
        values = _autocovariance(chain, chain.mean(), 10)
        # acov[0] equals the ddof=0 sample variance, so rho[0] = 1 exactly.
        assert values[0] == pytest.approx(chain.var(ddof=0), rel=1e-10)


# ---------------------------------------------------------------------------
# parser errors are TraceError, not raw ValueError
# ---------------------------------------------------------------------------


class TestTraceParserErrors:
    def test_non_numeric_value(self, tmp_path):
        log = _write(tmp_path, "bad.log", "state\tx\n0\t1\n1\tnot_a_number\n")
        with pytest.raises(TraceError) as excinfo:
            parse_trace_log(str(log))
        assert "line 3" in str(excinfo.value)
        assert "not_a_number" in str(excinfo.value)

    def test_space_delimited_header(self, tmp_path):
        log = _write(tmp_path, "space.log", "state x\n0 1\n")
        with pytest.raises(TraceError) as excinfo:
            parse_trace_log(str(log))
        assert "space-delimited" in str(excinfo.value)

    def test_ragged_row(self, tmp_path):
        log = _write(tmp_path, "ragged.log", "state\tx\n0\t1\n1\t2\t3\n")
        with pytest.raises(TraceError):
            parse_trace_log(str(log))


# ---------------------------------------------------------------------------
# utils
# ---------------------------------------------------------------------------


class TestUtilsContracts:
    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
    def test_format_number_rejects_non_finite(self, value):
        with pytest.raises(ValueError) as excinfo:
            format_number(value)
        assert "non-finite" in str(excinfo.value)

    def test_format_indent_keeps_attribute_text(self):
        xml = '<root><sequence name="s1" taxon="t1">ACGT</sequence></root>'
        rendered = format_indent(xml)
        assert ">ACGT</sequence>" in rendered
        assert "ACGT" in rendered

    def test_normalize_does_not_collapse_distinct_ids(self):
        left = '<root><x id="0001" value="1.000"/></root>'
        right = '<root><x id="1" value="1.000"/></root>'
        assert normalize_xml_for_compare(left) != normalize_xml_for_compare(right)
        # Numeric attributes are still normalised.
        other = '<root><x id="0001" value="1"/></root>'
        assert normalize_xml_for_compare(left) == normalize_xml_for_compare(other)

    @pytest.mark.parametrize("value", ["nan", "Infinity", "-inf", "1_000", " 5 "])
    def test_parse_number_rejects_unsafe_forms(self, value):
        with pytest.raises(ValueError):
            parse_number(value, "x")

    def test_parse_number_accepts_plain_decimals(self):
        assert parse_number("1e-3", "x") == pytest.approx(0.001)
        assert parse_number("-2.5", "x") == -2.5

    def test_gev_mean_delegates_to_geweke(self):
        from beastxpy.tracestats import geweke_z

        chain = np.arange(500.0)
        assert gev_mean(chain) == pytest.approx(geweke_z(chain))


# ---------------------------------------------------------------------------
# distribution conversions and offsets
# ---------------------------------------------------------------------------


DISTRIBUTIONS = [
    ("normal", {"mean": 0.0, "stdev": 1.0}),
    ("uniform", {"lower": 0.0, "upper": 10.0}),
    ("lognormal", {"mean": 0.0, "stdev": 0.5}),
    ("exponential", {"mean": 2.0}),
    ("gamma", {"shape": 2.0, "scale": 1.5}),
    ("beta", {"alpha": 2.0, "beta": 5.0}),
    ("inverse_gamma", {"shape": 3.0, "scale": 2.0}),
    ("poisson", {"mean": 3.0}),
    ("chi_square", {"df": 3.0}),
]


class TestDistributionConversions:
    def test_lognormal_real_space_formula(self):
        mu, sigma = lognormal_mu_sigma({"mean": 1.0, "stdev": 0.5, "meanInRealSpace": True})
        assert mu == pytest.approx(-0.1115717756, rel=1e-9)
        assert sigma == pytest.approx(0.4723807270, rel=1e-9)
        assert math.exp(mu) == pytest.approx(0.89442719, rel=1e-7)

    def test_lognormal_sampling_median_matches_beast(self):
        samples = sample_distribution(
            "lognormal",
            {"mean": 1.0, "stdev": 0.5, "meanInRealSpace": True},
            offset=0.0,
            n=200000,
            seed=7,
        )
        assert np.median(samples) == pytest.approx(0.894, abs=0.01)

    def test_lognormal_quantiles_match_beast(self):
        if scipy_stats is None:
            pytest.skip("scipy unavailable")
        params = {"mean": 1.0, "stdev": 0.5, "meanInRealSpace": True}
        mu, sigma = lognormal_mu_sigma(params)
        low, high = distribution_quantiles("lognormal", params, 0.0)
        assert low == pytest.approx(scipy_stats.lognorm.ppf(0.025, sigma, scale=math.exp(mu)))
        assert high == pytest.approx(scipy_stats.lognorm.ppf(0.975, sigma, scale=math.exp(mu)))

    def test_explicit_zero_offset_is_respected(self):
        params = {"mean": 0.0, "stdev": 0.5, "offset": 5.0}
        zero = distribution_quantiles("lognormal", params, 0.0)
        without = distribution_quantiles("lognormal", {"mean": 0.0, "stdev": 0.5}, 0.0)
        assert zero == pytest.approx(without)
        assert distribution_mean("lognormal", params, 0.0) == pytest.approx(
            distribution_mean("lognormal", {"mean": 0.0, "stdev": 0.5}, 0.0)
        )

    def test_exponential_rate_is_converted_to_mean(self):
        rate = {"rate": 2.0}
        mean = {"mean": 0.5}
        assert distribution_quantiles("exponential", rate, 0.0) == pytest.approx(
            distribution_quantiles("exponential", mean, 0.0)
        )
        assert distribution_mean("exponential", rate, 0.0) == pytest.approx(0.5)
        assert distribution_mean("exponential", rate, 0.0) == distribution_mean(
            "exponential", mean, 0.0
        )

    @pytest.mark.parametrize("dist_type,params", DISTRIBUTIONS)
    def test_offsets_are_consistent_between_quantiles_and_mean(self, dist_type, params):
        q0 = distribution_quantiles(dist_type, params, 0.0)
        m0 = distribution_mean(dist_type, params, 0.0)
        if q0 is None or m0 is None:
            pytest.skip(f"{dist_type} has no finite statistics")
        for offset in (5.0, 20.0):
            q = distribution_quantiles(dist_type, params, offset)
            m = distribution_mean(dist_type, params, offset)
            assert q[0] == pytest.approx(q0[0] + offset)
            assert q[1] == pytest.approx(q0[1] + offset)
            assert m == pytest.approx(m0 + offset)


# ---------------------------------------------------------------------------
# MCC tree
# ---------------------------------------------------------------------------


class TestMccTree:
    def _trees(self, tmp_path, newicks, translate=None):
        path = _write(tmp_path, "trees.trees", _nexus(newicks, translate=translate))
        return parse_nexus_trees(str(path))

    def test_mcc_for_three_taxon_tree(self, tmp_path):
        trees = self._trees(tmp_path, ["((A:1,B:1):1,C:2)"])
        stats = clade_statistics(trees)
        assert stats["root"]["mean_height"] == pytest.approx(2.0)
        ab = next(c for c in stats["clades"] if c["tips"] == ["A", "B"])
        assert ab["mean_height"] == pytest.approx(1.0)
        assert ab["support"] == pytest.approx(1.0)

        mcc = build_mcc_tree(trees)
        assert mcc.startswith("((A:1,B:1)")
        assert mcc.endswith(";")
        # Branch lengths, support labels and heights are all present.
        assert "[&height=2,posterior=1]" in mcc or "[&height=2.0,posterior=1]" in mcc
        assert "[&height=1,posterior=1]" in mcc or "[&height=1.0,posterior=1]" in mcc
        root, tips = treetools._parse_newick(mcc, {})
        assert sorted(tips) == ["A", "B", "C"]
        assert all(child["length"] is not None for child in root["children"])

    def test_mcc_keeps_all_taxa_when_limit_suppresses_annotations(self, tmp_path):
        newicks = [
            "((A:1,B:1):1,(C:1,D:1):1)",
            "((A:1,B:1):1,(C:1,D:1):1)",
            "((A:1,B:1):1,(C:1,D:1):1)",
            "((A:1,C:1):1,(B:1,D:1):1)",
        ]
        trees = self._trees(tmp_path, newicks)
        mcc = build_mcc_tree(trees, support_threshold=0.9)
        _, tips = treetools._parse_newick(mcc, {})
        assert sorted(tips) == ["A", "B", "C", "D"]
        # The ABCD split has support 0.75 < 0.9: annotations suppressed, topology kept.
        assert "posterior=0.75" not in mcc
        assert mcc.startswith("((A:1,B:1):1,(C:1,D:1):1)") or mcc.startswith("((A:1,B:1)")

    def test_missing_tips_key_raises_tree_tools_error(self, tmp_path, monkeypatch):
        trees = self._trees(tmp_path, ["((A:1,B:1):1,C:2)"])
        monkeypatch.setattr(treetools, "clade_statistics", lambda *a, **k: {"clades": []})
        with pytest.raises(TreeToolsError) as excinfo:
            build_mcc_tree(trees)
        assert "tips" in str(excinfo.value)

    def test_hpd_is_shortest_interval_not_equal_tailed(self, tmp_path):
        # 95% of the clade heights are identical; 5% form a long tail.  The
        # shortest 95% interval is (0.1, 0.1) while the equal-tailed interval is
        # (0.1, 100).
        heights = [0.1] * 95 + [100.0] * 5
        newicks = [f"((A:{h},B:{h}):{h},C:{2 * h})" for h in heights]
        path = _write(tmp_path, "skew.trees", _nexus(newicks))
        trees = parse_nexus_trees(str(path))
        stats = clade_statistics(trees)
        ab = next(c for c in stats["clades"] if c["tips"] == ["A", "B"])
        expected = hpd_interval(np.array(heights), alpha=0.05)
        assert (ab["hpd_lower"], ab["hpd_upper"]) == pytest.approx(expected)
        assert ab["equal_tailed_lower"] == pytest.approx(np.quantile(heights, 0.025))
        assert (ab["hpd_lower"], ab["hpd_upper"]) != pytest.approx(
            (ab["equal_tailed_lower"], ab["equal_tailed_upper"])
        )


# ---------------------------------------------------------------------------
# Newick/NEXUS parsing
# ---------------------------------------------------------------------------


class TestNewickParsing:
    def test_quoted_taxa_and_comments(self):
        root, tips = treetools._parse_newick(
            "(('Homo sapiens':0.1[&x=1],'O''Brien':2e-4):0.5,Gorilla:1);", {}
        )
        assert tips == ["Homo sapiens", "O'Brien", "Gorilla"]
        child = root["children"][0]
        assert child["children"][0]["length"] == pytest.approx(0.1)
        assert child["children"][1]["length"] == pytest.approx(2e-4)

    def test_plain_newick_file(self, tmp_path):
        path = _write(tmp_path, "plain.nwk", "((A:1,B:1):1,C:2)\n")
        trees = parse_nexus_trees(str(path))
        assert len(trees) == 1
        _, tips = treetools._parse_newick(trees[0].newick, trees[0].translation)
        assert tips == ["A", "B", "C"]

    def test_nexus_without_final_semicolon(self, tmp_path):
        text = "#NEXUS\nBegin trees;\nTranslate\n 1 A,\n 2 B,\n 3 C\n;\ntree STATE_0 = ((1:1,2:1):1,3:2)\nEnd;"
        path = _write(tmp_path, "nosemi.trees", text)
        trees = parse_nexus_trees(str(path))
        assert len(trees) == 1

    @pytest.mark.parametrize("bad", ["(A B,C);", "(A,B,C;", "(A,B),C);", "((A,B),C); trailing"])
    def test_malformed_newick_raises(self, bad):
        with pytest.raises(TreeToolsError):
            treetools._parse_newick(bad, {})

    def test_duplicate_tip_labels_are_rejected(self):
        with pytest.raises(TreeToolsError) as excinfo:
            treetools._parse_newick("((A,B),(A,B),C);", {})
        assert "duplicate tip label 'A'" in str(excinfo.value)

    def test_per_tree_translation_and_rootedness(self, tmp_path):
        path = _write(
            tmp_path,
            "multi.trees",
            _nexus(
                ["(1,2);", "((1,2),3);"],
                translate={"1": "A", "2": "B"},
                extra_translate={"1": "X", "2": "Y"},
            ),
        )
        trees = parse_nexus_trees(str(path))
        assert len(trees) == 2
        assert trees[0].translation == {"1": "A", "2": "B"}
        assert trees[1].translation == {"1": "X", "2": "Y"}
        assert trees[0].metadata["rooted"] == "true"
        assert trees[1].metadata["rooted"] == "false"
        # Per-tree maps are snapshots, not the same object.
        assert trees[0].translation is not trees[1].translation

    def test_thousand_tip_caterpillar(self, tmp_path):
        def caterpillar(n):
            text = "A0:1"
            for index in range(1, n):
                text = f"({text},A{index}:1):1" if index < n - 1 else f"({text},A{index}:1)"
            return text + ";"

        newick = caterpillar(1200)
        root, tips = treetools._parse_newick(newick, {})
        assert len(tips) == 1200
        stats = clade_statistics([NexusTree(newick, {}), NexusTree(newick, {})])
        assert stats["n_tips"] == 1200
        assert stats["root"]["mean_height"] == pytest.approx(1199.0)
        assert len(stats["clades"]) == 1199


# ---------------------------------------------------------------------------
# Java invocation and fallback semantics
# ---------------------------------------------------------------------------


class TestTreeCommandSemantics:
    def test_java_failure_is_propagated_with_stderr(self, tmp_path, monkeypatch):
        trees = _write(tmp_path, "a.trees", _nexus(["((A:1,B:1):1,C:2)"]))
        monkeypatch.setattr(
            treetools,
            "run_tree_annotator",
            lambda *a, **k: treetools.CommandResult(
                ok=False,
                command=["java"],
                stderr="Unrecognized argument: -heights bogus",
                exit_code=1,
            ),
        )
        with pytest.raises(TreeToolsError) as excinfo:
            annotate_tree_command(str(trees), str(tmp_path / "out.tre"))
        assert "Unrecognized argument" in str(excinfo.value)
        assert "TreeAnnotator failed" in str(excinfo.value)

    def test_treeannotator_uses_burnin_trees(self, monkeypatch):
        captured = {}

        def fake_run(args, timeout=600):
            captured["args"] = args
            return treetools.CommandResult(ok=True, command=args)

        monkeypatch.setattr(treetools, "lookup_jar", lambda version: "beast.jar")
        monkeypatch.setattr(treetools, "_java_run", fake_run)
        run_tree_annotator("in.trees", "out.tre", burnin=7)
        assert "-burninTrees" in captured["args"]
        assert "-burnin" not in captured["args"]
        assert captured["args"][captured["args"].index("-burninTrees") + 1] == "7"

    def test_logcombine_failure_is_propagated(self, tmp_path, monkeypatch):
        log = _write(tmp_path, "a.log", "state\tx\n0\t1\n")
        monkeypatch.setattr(
            treetools,
            "run_log_combiner",
            lambda *a, **k: treetools.CommandResult(
                ok=False, command=["java"], stderr="boom: bad column count", exit_code=1
            ),
        )
        with pytest.raises(TreeToolsError) as excinfo:
            combine_logs_command([str(log)], str(tmp_path / "o.log"))
        assert "boom: bad column count" in str(excinfo.value)

    def test_python_logcombine_honours_burnin_and_resample(self, tmp_path):
        log = _write(
            tmp_path,
            "a.log",
            "# BEAST\nstate\tposterior\tconstant\n0\t-1\t5\n1\t-2\t5\n2\t-3\t5\n3\t-4\t5\n",
        )
        out = tmp_path / "o.log"
        combine_logs_command([str(log)], str(out), burnin=1, python_only=True)
        assert out.read_text().splitlines()[2:] == ["1\t-2\t5", "2\t-3\t5", "3\t-4\t5"]
        combine_logs_command([str(log)], str(out), burnin=0, resample=2, python_only=True)
        assert out.read_text().splitlines()[2:] == ["0\t-1\t5", "2\t-3\t5"]
        with pytest.raises(TreeToolsError):
            combine_logs_command([str(log)], str(out), strip=True, python_only=True)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


class TestDiagnosticsCladeSemantics:
    def test_clade_tree_and_children_replace_stub(self):
        config = _make_config(
            [
                _cal("Root", ["t1", "t2", "t3", "t4"]),
                _cal("Inner", ["t1", "t2"]),
                _cal("Leaf", ["t1"]),
            ]
        )
        nodes = calibration_clade_tree(config)
        assert {node.name for node in nodes} == {"Root", "Inner", "Leaf"}
        assert calibration_children(config, "Root") == ["Inner", "Leaf"]
        assert calibration_children(config, "Inner") == ["Leaf"]

    def test_duplicate_same_node_is_detected(self):
        config = _make_config(
            [
                _cal("A", ["t1", "t2"], distribution=_dist("normal", {"mean": 10, "stdev": 1})),
                _cal("B", ["t1", "t2"], distribution=_dist("normal", {"mean": 10, "stdev": 1})),
            ]
        )
        report = CalibrationDiagnostics(config).run()
        codes = [finding.code for finding in report.findings]
        assert "DUPLICATE_CALIBRATION" in codes
        assert "TEMPORAL_INCOMPATIBLE" not in codes

        different = _make_config(
            [
                _cal("A", ["t1", "t2"], distribution=_dist("normal", {"mean": 10, "stdev": 1})),
                _cal("B", ["t1", "t2"], distribution=_dist("normal", {"mean": 10, "stdev": 5})),
            ]
        )
        different_codes = [
            finding.code for finding in CalibrationDiagnostics(different).run().findings
        ]
        assert "DUPLICATE_CALIBRATION" in different_codes
        assert "TEMPORAL_INCOMPATIBLE" not in different_codes

    def test_crown_stem_pair_is_not_a_false_positive(self):
        config = _make_config(
            [
                _cal("Crown", ["t1", "t2"], distribution=_dist("normal", {"mean": 11, "stdev": 1})),
                _cal(
                    "Stem",
                    ["t1", "t2"],
                    calibration_method="stem",
                    distribution=_dist("normal", {"mean": 22, "stdev": 2}),
                ),
            ]
        )
        report = CalibrationDiagnostics(config).run()
        assert report.findings == []
        assert any("stem calibration" in note for note in report.extra["constraint_notes"])

    def test_inverted_crown_stem_is_an_error(self):
        config = _make_config(
            [
                _cal("Crown", ["t1", "t2"], distribution=_dist("normal", {"mean": 22, "stdev": 1})),
                _cal(
                    "Stem",
                    ["t1", "t2"],
                    calibration_method="stem",
                    distribution=_dist("normal", {"mean": 11, "stdev": 1}),
                ),
            ]
        )
        report = CalibrationDiagnostics(config).run()
        assert [finding.code for finding in report.findings] == ["CROWN_OLDER_THAN_STEM"]

    def test_monophyly_false_note(self):
        config = _make_config(
            [
                _cal(
                    "A",
                    ["t1", "t2"],
                    monophyletic=False,
                    distribution=_dist("normal", {"mean": 5, "stdev": 1}),
                )
            ]
        )
        report = CalibrationDiagnostics(config).run()
        assert any("monophyletic=false" in note for note in report.extra["constraint_notes"])


class TestDiagnosticsOverlapAndReports:
    def _disjoint_config(self):
        return _make_config(
            [
                _cal("A", ["t1", "t2"], distribution=_dist("normal", {"mean": 10, "stdev": 1})),
                _cal("B", ["t3", "t4"], distribution=_dist("normal", {"mean": 25, "stdev": 1})),
            ]
        )

    def test_disjoint_pairs_are_reported_with_zero_overlap(self):
        diag = CalibrationDiagnostics(self._disjoint_config())
        report = diag.run()
        assert len(report.pair_overlaps) == 1
        entry = report.pair_overlaps[0]
        assert entry["overlap"] == 0.0
        assert entry["comparability"] == "disjoint"
        assert entry["a"] == "A" and entry["b"] == "B"
        assert entry["interval_a"][0] < entry["interval_b"][0]
        assert not any(f.code == "OVERLAP_EXCEEDED" for f in report.findings)

    def test_overlap_is_symmetric(self):
        forward = CalibrationDiagnostics(self._disjoint_config()).run().pair_overlaps[0]
        elements = list(self._disjoint_config().calibrations)
        reversed_config = _make_config(list(reversed(elements)))
        backward = CalibrationDiagnostics(reversed_config).run().pair_overlaps[0]
        assert (forward["a"], forward["b"]) == (backward["a"], backward["b"])
        assert forward["overlap"] == pytest.approx(backward["overlap"])
        assert forward["divergence"] == pytest.approx(backward["divergence"])
        assert forward["wasserstein"] == pytest.approx(backward["wasserstein"])

    def test_nested_low_overlap_still_warns(self):
        config = _make_config(
            [
                _cal(
                    "Outer",
                    ["t1", "t2", "t3"],
                    distribution=_dist("normal", {"mean": 30, "stdev": 1}),
                ),
                _cal("Inner", ["t1", "t2"], distribution=_dist("normal", {"mean": 29, "stdev": 1})),
            ]
        )
        report = CalibrationDiagnostics(config).run()
        assert any(f.code == "OVERLAP_EXCEEDED" for f in report.findings)
        entry = report.pair_overlaps[0]
        assert entry["comparability"] == "nested"
        assert entry["overlap"] < 1.0
        assert entry["divergence"] > 0.15

    def test_html_report_escapes_names_and_messages(self):
        config = _make_config(
            [
                _cal(
                    "<script>alert(1)</script>&",
                    ["t1", "t2"],
                    distribution=_dist("normal", {"mean": 10, "stdev": 1}),
                ),
                _cal("B", ["t1", "t2"], distribution=_dist("normal", {"mean": 10.5, "stdev": 1})),
            ],
            analysis_name="<b>analysis</b>",
        )
        diag = CalibrationDiagnostics(config)
        diag.run()
        html = diag.render_html_report()
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
        assert "<b>analysis</b>" not in html
        assert "&lt;b&gt;analysis&lt;/b&gt;" in html

    def test_empty_and_single_calibration_reports_do_not_claim_clean(self):
        empty = CalibrationDiagnostics(_make_config([]))
        empty.run()
        assert "nothing was checked" in empty.render_text_report()
        one = CalibrationDiagnostics(
            _make_config(
                [_cal("A", ["t1", "t2"], distribution=_dist("normal", {"mean": 10, "stdev": 1}))]
            )
        )
        one.run()
        text = one.render_text_report()
        assert "insufficient data" in text
        assert "no calibration conflicts detected" not in text.lower()
        two = CalibrationDiagnostics(self._disjoint_config())
        two.run()
        assert "Checked 2 calibrations and 1 pairwise comparison(s)" in two.render_text_report()

    def test_stale_findings_are_cleared_between_runs(self):
        diag = CalibrationDiagnostics(self._disjoint_config())
        first = len(diag.run().findings)
        second = len(diag.run().findings)
        assert first == second


class TestDiagnosticsArtifacts:
    def test_d01_sensitivity_variants_have_no_dangling_references(self, tmp_path):
        xml_path = _write(tmp_path, "analysis.xml", SENSITIVITY_XML)
        config = _make_config(
            [
                _cal("A", ["t1", "t2"], distribution=_dist("uniform", {"lower": 0, "upper": 10})),
                _cal("B", ["t1"], distribution=_dist("uniform", {"lower": 0, "upper": 5})),
            ]
        )
        diag = CalibrationDiagnostics(config)
        created = diag.build_sensitivity_run(str(xml_path), str(tmp_path / "sensitivity"))
        assert len(created) == 3  # all + one per calibration
        for variant in created:
            root = ET.parse(variant).getroot()
            declared = {node.get("id") for node in root.iter() if node.get("id")}
            dangling = {
                node.get("idref")
                for node in root.iter()
                if node.get("idref") and node.get("idref") not in declared
            }
            assert dangling == set(), variant.name
            assert not any(element.tag == "treeLikelihood" for element in root.iter()), variant.name
            assert variant.read_text(encoding="utf-8").startswith("<?xml")

    def test_d03_launcher_is_cross_platform_and_surfaces_failures(self, tmp_path):
        xml_path = _write(tmp_path, "analysis.xml", SENSITIVITY_XML)
        config = _make_config(
            [_cal("A", ["t1", "t2"], distribution=_dist("uniform", {"lower": 0, "upper": 10}))]
        )
        diag = CalibrationDiagnostics(config)
        out_dir = tmp_path / "sensitivity"
        diag.build_sensitivity_run(str(xml_path), str(out_dir))
        launcher = out_dir / "run_sensitivity.py"
        assert launcher.exists()
        source = launcher.read_text(encoding="utf-8")
        assert "subprocess.run" in source
        assert "> /dev/null" not in source
        proc = subprocess.run(
            [
                sys.executable,
                str(launcher),
                "--java",
                "definitely-not-a-java-binary",
                "--jar",
                str(tmp_path / "fake.jar"),
            ],
            cwd=str(out_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.returncode == 1
        assert "failed" in (proc.stdout + proc.stderr).lower()

    def test_d05_artifact_filenames_are_filesystem_safe(self, tmp_path):
        config = _make_config(
            [
                _cal("a/b", ["t1"], distribution=_dist("normal", {"mean": 10, "stdev": 1})),
                _cal("a:b", ["t1"], distribution=_dist("normal", {"mean": 12, "stdev": 1})),
                _cal("a<b", ["t1"], distribution=_dist("normal", {"mean": 14, "stdev": 1})),
            ]
        )
        diag = CalibrationDiagnostics(config)
        diag.run()
        created = diag.write_density_plots(str(tmp_path / "plots"))
        assert len(created) == 4  # three priors + heatmap
        for path_text in created:
            path = Path(path_text)
            assert path.parent == tmp_path / "plots"
            assert path.exists()
            assert not any(char in path.name for char in "/:<>")
        assert "a/b" in diag.render_text_report()
        assert safe_filename("a/b").startswith("a_b_")

    def test_d21_check_only_does_not_create_directories(self, tmp_path):
        config = _make_config(
            [_cal("A", ["t1", "t2"], distribution=_dist("normal", {"mean": 10, "stdev": 1}))]
        )
        fresh = tmp_path / "fresh"
        report = diagnose_config(config, output_dir=str(fresh), visualize=False)
        assert not fresh.exists()
        assert report.extra["report"] is None
        assert report.extra["created"] == []

    def test_d21_html_suffix_is_case_insensitive_and_dirs_created(self, tmp_path):
        config = _make_config(
            [_cal("A", ["t1", "t2"], distribution=_dist("normal", {"mean": 10, "stdev": 1}))]
        )
        target = tmp_path / "missing" / "sub" / "REPORT.HTML"
        report = diagnose_config(
            config, output_dir=str(tmp_path / "unused"), report_path=str(target), visualize=False
        )
        assert target.exists()
        assert target.read_text(encoding="utf-8").lstrip().startswith("<!DOCTYPE html>")
        assert not (tmp_path / "unused").exists()
        assert report.extra["report"] == str(target)

    def test_d03_sensitivity_count_reflects_this_run(self, tmp_path):
        xml_path = _write(tmp_path, "analysis.xml", SENSITIVITY_XML)
        config = _make_config(
            [_cal("A", ["t1", "t2"], distribution=_dist("uniform", {"lower": 0, "upper": 10}))]
        )
        out_dir = tmp_path / "diag"
        (out_dir / "sensitivity").mkdir(parents=True)
        (out_dir / "sensitivity" / "sensitivity_99_stale.xml").write_text("<stale/>")
        report = diagnose_config(
            config,
            output_dir=str(out_dir),
            visualize=False,
            sensitivity=True,
            xml_path=str(xml_path),
        )
        assert report.n_sensitivity == 2  # all + no_A, not the stale file
