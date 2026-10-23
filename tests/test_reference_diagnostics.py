"""Golden-value agreement with reference implementations.

Two fixtures are pinned in ``tests/golden/reference_diagnostics/``:

* fixed simulated chains and ArviZ 0.23.4 values for rank-normalized R-hat,
  bulk ESS, tail ESS, and the anti-correlated ESS>draws case;
* a fixed simulated BEAST-style log with Tracer 1.7.2 values for mean,
  median, 95% HPD, ESS and ACT.

The reference values were computed once from the stored inputs; the test
recomputes them with BeastXPy and asserts agreement.  No reference package is
needed at test time.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from beastxpy.tracestats import (
    _multi_chain_ess,
    act_estimate,
    bulk_ess,
    ess_estimate,
    rank_normalized_split_rhat,
    tail_ess,
)
from beastxpy.utils import hpd_interval

GOLDEN = Path(__file__).resolve().parent / "golden" / "reference_diagnostics"


def _load(name: str):
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def data():
    return _load("chains.json")


@pytest.fixture(scope="module")
def arviz():
    return _load("arviz.json")


@pytest.fixture(scope="module")
def tracer():
    return _load("tracer.json")


def test_arviz_version_is_recorded(arviz):
    assert arviz["arviz_version"] == "0.23.4"


def test_rank_rhat_matches_arviz(data, arviz):
    assert rank_normalized_split_rhat(data["chains"]) == pytest.approx(
        arviz["rank_rhat"], rel=1e-12, abs=1e-12
    )


def test_bulk_ess_matches_arviz(data, arviz):
    assert bulk_ess(data["chains"]) == pytest.approx(arviz["bulk_ess"], rel=1e-9, abs=1e-9)


def test_tail_ess_matches_arviz(data, arviz):
    assert tail_ess(data["chains"]) == pytest.approx(arviz["tail_ess"], rel=1e-9, abs=1e-9)


def test_anticorrelated_ess_may_exceed_draws(data, arviz):
    """ArviZ legitimately reports ESS > N for anti-correlated chains."""
    anti = np.asarray(data["anticorrelated"], dtype=float)
    ours = _multi_chain_ess([anti])
    assert ours is not None
    assert ours > anti.size
    assert ours == pytest.approx(arviz["bulk_ess_anticorrelated"], rel=1e-9)


def test_tracer_input_log_matches_its_recorded_digest():
    log = GOLDEN / "tracer_input.log"
    recorded = (GOLDEN / "tracer_log_sha256.txt").read_text(encoding="utf-8").strip()
    assert hashlib.sha256(log.read_bytes()).hexdigest() == recorded


def test_single_chain_statistics_match_tracer(data, tracer):
    """Tracer 1.7.2 on the stored synthetic log."""
    assert tracer["tracer_version"] == "1.7.2"
    values = np.asarray(data["tracer_chain"], dtype=float)
    reference = tracer["columns"]["p1"]
    lower, upper = hpd_interval(values, alpha=0.05)
    assert float(values.mean()) == pytest.approx(reference["mean"], rel=1e-9)
    # Tracer's median interpolation differs in the last few decimals.
    assert float(np.median(values)) == pytest.approx(reference["median"], abs=2e-3)
    assert lower == pytest.approx(reference["hpd_lower"], rel=1e-9, abs=1e-9)
    assert upper == pytest.approx(reference["hpd_upper"], rel=1e-9, abs=1e-9)
    assert ess_estimate(values) == pytest.approx(reference["ess"], rel=1e-3)
    assert act_estimate(values) == pytest.approx(reference["act"], rel=1e-3)
