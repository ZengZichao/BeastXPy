"""Tests for the multi-chain helpers appended to beastxpy.tracestats (F1).

Covers the split-chain / across-chains Gelman-Rubin PSRF, split frequencies
and ASDSF, and the BEAST operator-analysis table parser.  Real BEAST output
comes from tests/fixtures/assess_multi and tests/fixtures/assess_single (see
their PROVENANCE.txt); the detailed operator table is exercised against
tests/fixtures/parser_operator_table_detailed.log, which is a hand-written
PARSER fixture and never convergence evidence.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy import assessment  # noqa: E402
from beastxpy.tracestats import (  # noqa: E402
    TraceError,
    asdsf,
    compute_multichain_psrf,
    gelman_rubin_psrf,
    parse_operator_analysis,
    split_chain_halves,
    split_frequencies_from_trees,
)
from beastxpy.treetools import NexusTree  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _tree(newick: str) -> NexusTree:
    return NexusTree(newick=newick, translation={}, metadata={})


# ---------------------------------------------------------------------------
# split_chain_halves
# ---------------------------------------------------------------------------


def test_split_chain_halves_even_and_odd():
    halves = split_chain_halves(np.arange(10.0))
    assert len(halves) == 2
    assert list(halves[0]) == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert list(halves[1]) == [5.0, 6.0, 7.0, 8.0, 9.0]
    # Odd length drops the middle sample so both halves have equal length.
    halves = split_chain_halves(np.arange(9.0))
    assert list(halves[0]) == [0.0, 1.0, 2.0, 3.0]
    assert list(halves[1]) == [5.0, 6.0, 7.0, 8.0]


def test_split_chain_halves_too_short():
    assert split_chain_halves(np.array([1.0, 2.0, 3.0])) == []
    assert split_chain_halves(np.array([])) == []


def test_split_chain_halves_drops_nonfinite():
    halves = split_chain_halves(np.array([1.0, np.nan, 2.0, 3.0, 4.0, 5.0]))
    assert all(np.all(np.isfinite(h)) for h in halves)


# ---------------------------------------------------------------------------
# gelman_rubin_psrf
# ---------------------------------------------------------------------------


def test_psrf_requires_two_chains():
    assert gelman_rubin_psrf([np.arange(50.0)]) is None
    assert gelman_rubin_psrf([]) is None


def test_psrf_identical_chains_near_one():
    rng = np.random.default_rng(7)
    chain = rng.normal(size=200)
    r_hat = gelman_rubin_psrf([chain, chain.copy()], split=False)
    assert r_hat is not None
    # B = 0 for identical chains: R-hat = sqrt((n-1)/n) < 1.
    assert math.isclose(r_hat, math.sqrt(199.0 / 200.0), rel_tol=1e-9)


def test_psrf_well_separated_chains_large():
    rng = np.random.default_rng(11)
    a = rng.normal(loc=0.0, scale=1.0, size=200)
    b = rng.normal(loc=10.0, scale=1.0, size=200)
    r_hat = gelman_rubin_psrf([a, b], split=False)
    assert r_hat is not None
    assert r_hat > 1.5


def test_psrf_constant_chains_not_computable():
    # Zero within-chain variance: R-hat is 0/0 and must never be reported.
    assert gelman_rubin_psrf([np.ones(50), np.ones(50)]) is None
    assert gelman_rubin_psrf([np.ones(50), np.full(50, 2.0)]) is None


def test_psrf_short_chains_not_computable():
    assert gelman_rubin_psrf([np.array([1.0, 2.0]), np.array([3.0, 4.0])]) is None


def test_split_estimator_detects_within_chain_drift():
    # Two identical drifting chains: the plain across-chains estimator sees no
    # between-chain difference, the split-chain estimator does.  Noise keeps
    # the within-half variance positive (constant halves are not computable).
    rng = np.random.default_rng(17)
    drift = np.concatenate([rng.normal(0.0, 1.0, 100), rng.normal(10.0, 1.0, 100)])
    across = gelman_rubin_psrf([drift, drift.copy()], split=False)
    split = gelman_rubin_psrf([drift, drift.copy()], split=True)
    assert across is not None and split is not None
    assert across < 1.05
    assert split > 1.1


def test_psrf_unequal_lengths_truncated_to_shortest():
    rng = np.random.default_rng(3)
    a = rng.normal(size=200)
    b = rng.normal(size=150)
    r_hat = gelman_rubin_psrf([a, b], split=False)
    assert r_hat is not None and r_hat >= 0.9


# ---------------------------------------------------------------------------
# compute_multichain_psrf
# ---------------------------------------------------------------------------


def test_compute_multichain_psrf_fewer_than_two_chains():
    result = compute_multichain_psrf([{"posterior": np.arange(50.0)}], threshold=1.01)
    assert result["status"] == "not_computable"
    assert result["values"] == {}
    assert "at least 2" in result["reason"]
    assert "split-chain" in result["estimator"]


def test_compute_multichain_psrf_structure_and_ok_flag():
    rng = np.random.default_rng(5)
    chains = [
        {"posterior": rng.normal(size=100), "prior": rng.normal(size=100)},
        {"posterior": rng.normal(size=100), "prior": rng.normal(size=100)},
    ]
    result = compute_multichain_psrf(chains, threshold=1.5)
    assert result["status"] == "ok"
    assert result["n_chains"] == 2
    for name in ("posterior", "prior"):
        entry = result["values"][name]
        assert entry["psrf"] is not None
        assert entry["ok"] is True
        assert entry["reason"] is None


def test_compute_multichain_psrf_missing_column_reports_reason():
    rng = np.random.default_rng(5)
    chains = [
        {"posterior": rng.normal(size=100), "onlyInOne": rng.normal(size=100)},
        {"posterior": rng.normal(size=100)},
    ]
    result = compute_multichain_psrf(chains, threshold=1.5)
    entry = result["values"]["onlyInOne"]
    assert entry["psrf"] is None
    assert entry["ok"] is None
    assert "onlyInOne" in entry["reason"]


def test_compute_multichain_psrf_constant_column_reason():
    chains = [
        {"flat": np.ones(100)},
        {"flat": np.ones(100)},
    ]
    result = compute_multichain_psrf(chains, threshold=1.5)
    assert result["status"] == "not_computable"
    assert result["values"]["flat"]["psrf"] is None
    assert "constant" in result["values"]["flat"]["reason"]


def test_compute_multichain_psrf_skips_state_column():
    rng = np.random.default_rng(5)
    chains = [
        {"state": np.arange(100.0), "posterior": rng.normal(size=100)},
        {"state": np.arange(100.0), "posterior": rng.normal(size=100)},
    ]
    result = compute_multichain_psrf(chains, threshold=1.5)
    assert "state" not in result["values"]
    assert "posterior" in result["values"]


def test_compute_multichain_psrf_across_chains_estimator_documented():
    rng = np.random.default_rng(5)
    chains = [
        {"posterior": rng.normal(size=100)},
        {"posterior": rng.normal(size=100)},
    ]
    result = compute_multichain_psrf(chains, split=False, threshold=1.5)
    assert "across-chains" in result["estimator"]


# ---------------------------------------------------------------------------
# split frequencies and ASDSF
# ---------------------------------------------------------------------------


def test_split_frequencies_single_topology():
    trees = [_tree("((A,B),(C,D));")] * 5
    freqs = split_frequencies_from_trees(trees)
    assert freqs == {frozenset({"A", "B"}): 1.0}


def test_split_frequencies_exclude_trivial_and_root():
    # Three taxa: the only internal bipartition is trivial (1 vs 2).
    trees = [_tree("((A,B),C);")] * 3
    assert split_frequencies_from_trees(trees) == {}


def test_split_frequencies_mixed_sample():
    trees = [_tree("((A,B),(C,D));"), _tree("((A,C),(B,D));")]
    freqs = split_frequencies_from_trees(trees)
    assert freqs[frozenset({"A", "B"})] == 0.5
    assert freqs[frozenset({"A", "C"})] == 0.5


def test_split_frequencies_burnin_errors():
    trees = [_tree("((A,B),(C,D));")]
    with pytest.raises(TraceError):
        split_frequencies_from_trees(trees, burnin=1)
    with pytest.raises(TraceError):
        split_frequencies_from_trees(trees, burnin=-1)


def test_asdsf_identical_runs_zero():
    freqs = {frozenset({"A", "B"}): 1.0, frozenset({"A", "C"}): 0.5}
    assert asdsf([freqs, dict(freqs)]) == 0.0


def test_asdsf_disjoint_splits_known_value():
    run1 = {frozenset({"A", "B"}): 1.0}
    run2 = {frozenset({"A", "C"}): 1.0}
    value = asdsf([run1, run2])
    assert value is not None
    # Each split: frequencies (1, 0) -> SD = sqrt(0.5); mean over 2 splits.
    assert math.isclose(value, math.sqrt(0.5), rel_tol=1e-12)


def test_asdsf_requires_two_runs():
    assert asdsf([{frozenset({"A", "B"}): 1.0}]) is None
    assert asdsf([]) is None


def test_asdsf_no_nontrivial_splits_not_computable():
    assert asdsf([{}, {}]) is None


def test_asdsf_three_runs():
    run1 = {frozenset({"A", "B"}): 1.0}
    run2 = {frozenset({"A", "B"}): 1.0}
    run3 = {frozenset({"A", "B"}): 0.5}
    value = asdsf([run1, run2, run3])
    assert value is not None
    freqs = [1.0, 1.0, 0.5]
    mean = sum(freqs) / 3
    expected = math.sqrt(sum((p - mean) ** 2 for p in freqs) / 2)
    assert math.isclose(value, expected, rel_tol=1e-12)


def test_asdsf_on_real_fixtures_is_nonnegative():
    # Real BEAST tree logs (100-step chains -- a parser/plumbing exercise, not
    # convergence evidence).
    trees1 = assessment.load_trees(FIXTURES / "assess_multi" / "1" / "seed_1_tree.log")
    trees2 = assessment.load_trees(FIXTURES / "assess_multi" / "2" / "seed_2_tree.log")
    freqs1 = split_frequencies_from_trees(trees1, burnin=1)
    freqs2 = split_frequencies_from_trees(trees2, burnin=1)
    assert freqs1 and freqs2
    value = asdsf([freqs1, freqs2])
    assert value is not None
    assert 0.0 <= value <= 1.0


# ---------------------------------------------------------------------------
# parse_operator_analysis
# ---------------------------------------------------------------------------


def test_parse_operator_analysis_simple_real_fixture():
    # Real BEAST 1.10.4 run.log (simple Count/Pr(accept) layout).
    text = (FIXTURES / "assess_multi" / "1" / "run.log").read_text(encoding="utf-8")
    ops = parse_operator_analysis(text)
    by_name = {op["name"]: op for op in ops}
    assert len(ops) == 10
    never = by_name["scale(siteModel.alpha)"]
    assert never["attempts"] == 0
    assert never["acceptance_rate"] is None  # BEAST printed '?'
    assert never["accepted"] is None  # simple layout has no accept count
    slide = by_name["subtreeSlide(treeModel)"]
    assert slide["attempts"] == 21
    assert math.isclose(slide["acceptance_rate"], 0.2857, rel_tol=1e-6)
    narrow = by_name["Narrow Exchange(treeModel)"]
    assert narrow["tuning"] is None  # untunable topology operator
    assert narrow["attempts"] == 23
    assert sum(op["attempts"] for op in ops) == 100  # chain length


def test_parse_operator_analysis_detailed_parser_fixture():
    # Hand-written PARSER fixture (labelled as such in the file header).
    text = (FIXTURES / "parser_operator_table_detailed.log").read_text(encoding="utf-8")
    ops = parse_operator_analysis(text)
    by_name = {op["name"]: op for op in ops}
    assert len(ops) == 3
    tuned = by_name["scale(yule.birthRate)"]
    assert tuned["attempts"] == 100
    assert tuned["accepted"] == 30
    assert math.isclose(tuned["acceptance_rate"], 0.3, rel_tol=1e-9)
    untuned = by_name["Narrow Exchange(treeModel)"]
    assert untuned["tuning"] is None
    assert untuned["attempts"] == 200
    assert untuned["accepted"] == 50
    never = by_name["scale(siteModel.alpha)"]
    assert never["attempts"] == 0
    assert never["acceptance_rate"] is None


def test_parse_operator_analysis_absent_returns_empty():
    assert parse_operator_analysis("no tables here\njust text\n") == []
    assert parse_operator_analysis("") == []


def test_parse_operator_analysis_last_block_wins():
    text = (
        "Operator analysis\n"
        "Operator                                          Tuning   Count      Time     Time/Op  Pr(accept)\n"
        "scale(a)                                      0.5     10       0        0.0      0.1\n"
        "\n"
        "partial table above; resumed run continues\n"
        "\n"
        "Operator analysis\n"
        "Operator                                          Tuning   Count      Time     Time/Op  Pr(accept)\n"
        "scale(a)                                      0.5     40       0        0.0      0.25\n"
        "\n"
    )
    ops = parse_operator_analysis(text)
    assert len(ops) == 1
    assert ops[0]["attempts"] == 40
    assert math.isclose(ops[0]["acceptance_rate"], 0.25)


# ---------------------------------------------------------------------------
# Rank-normalized R-hat / bulk / tail ESS (Vehtari et al. 2021)
# ---------------------------------------------------------------------------


def test_rank_normalized_rhat_detects_a_mean_shift():
    from beastxpy.tracestats import rank_normalized_split_rhat

    rng = np.random.default_rng(3)
    first = rng.normal(0.0, 1.0, 4000)
    second = rng.normal(0.0, 1.0, 4000)
    shifted = rng.normal(1.5, 1.0, 4000)
    assert rank_normalized_split_rhat([first, second]) == pytest.approx(1.0, abs=0.01)
    assert rank_normalized_split_rhat([first, shifted]) > 1.1
    assert rank_normalized_split_rhat([np.ones(100), np.ones(100)]) is None
    assert rank_normalized_split_rhat([first]) is None


def test_bulk_and_tail_ess_are_finite_and_bounded():
    from beastxpy.tracestats import bulk_ess, tail_ess

    rng = np.random.default_rng(4)
    chains = [rng.normal(0.0, 1.0, 4000) for _ in range(2)]
    bulk = bulk_ess(chains)
    tail = tail_ess(chains)
    assert bulk is not None and tail is not None
    assert 0 < bulk <= 8000
    assert 0 < tail <= 8000
    # Strong autocorrelation must reduce the effective sample size.
    correlated = np.cumsum(rng.normal(0.0, 1.0, 4000))
    assert bulk_ess([correlated, correlated + 0.01]) < bulk


def test_rank_diagnostics_report_not_computable_with_one_chain():
    from beastxpy.tracestats import compute_multichain_rank_diagnostics

    rng = np.random.default_rng(5)
    result = compute_multichain_rank_diagnostics([{"x": rng.normal(size=100)}])
    assert result["status"] == "not_computable"
    assert result["values"] == {}
    assert "at least 2" in result["reason"]


def test_rank_diagnostics_columns_for_two_chains():
    from beastxpy.tracestats import compute_multichain_rank_diagnostics

    rng = np.random.default_rng(6)
    result = compute_multichain_rank_diagnostics(
        [{"x": rng.normal(size=2000)}, {"x": rng.normal(size=2000)}],
        psrf_threshold=1.01,
        ess_threshold=200,
    )
    entry = result["values"]["x"]
    assert entry["rank_psrf"] is not None
    assert entry["classic_psrf"] is not None
    assert entry["ess_bulk"] is not None
    assert entry["ess_tail"] is not None
    assert entry["ok"] is True
    assert entry["ess_bulk_ok"] is True and entry["ess_tail_ok"] is True


def test_assess_reports_rank_diagnostics_and_auto_burnin():
    doc = assessment.assess_workdir(FIXTURES / "assess_multi", burnin="auto", ess=0.1, psrf=1000.0)
    assert doc["thresholds"]["burnin"] == "auto"
    for chain in doc["chains"]:
        assert chain["burnin_mode"] == "auto"
        assert chain["burnin_fraction"] is not None
        for parameter in chain["parameters"].values():
            assert parameter["per_chain"]
    rank = doc["multichain"]["rank_diagnostics"]
    assert rank["status"] == "ok"
    assert rank["estimator"].startswith("rank-normalized")
    assert "x" not in rank["values"]  # fixtures log posterior/treeLikelihood, not x
    assert doc["multichain"]["psrf"]["values"]


def test_per_chain_act_metadata_is_reported():
    """JSON consumers can see which chain truncated (Minor-9)."""
    import numpy as np

    from beastxpy.tracestats import compute_trace_stats

    rng = np.random.default_rng(11)
    values = {"x": np.concatenate([rng.normal(size=500), rng.normal(size=500)])}
    doc = compute_trace_stats(values, burnin=0, chains=[(0, 500), (500, 1000)])
    column = next(c for c in doc["columns"] if c["name"] == "x")
    assert len(column["per_chain"]) == 2
    assert [c["index"] for c in column["per_chain"]] == [0, 1]
    for entry in column["per_chain"]:
        assert entry["n"] > 0
        assert entry["act"] >= 1.0
        assert entry["ess"] > 0
        assert isinstance(entry["act_truncated"], bool)
        assert entry["act_max_lag"] >= 0
    assert abs(sum(c["ess"] for c in column["per_chain"]) - column["ess"]) < 1e-9
