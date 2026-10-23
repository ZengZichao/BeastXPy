"""Tests for model comparison (``beastxpy.compare``).

These tests cover the parts that can be verified deterministically: the
analysis block written into a derived config, the fact that BEAST can parse
that config, comparability checks, result parsing, and the fail-closed
behaviour when native BEAGLE is absent. A full path-sampling run needs native
BEAGLE and is exercised only when the environment provides it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from beastxpy import _contracts as C
from beastxpy import compare as M
from beastxpy.api import BeastXPy
from beastxpy.config import ConfigParser

LEFT = "examples/config_subst_jc69.yaml"
RIGHT = "examples/config_subst_gtr.yaml"
BEAGLE_ELEMENTS = (
    "marginalLikelihoodEstimator",
    "pathSamplingAnalysis",
    "steppingStoneSamplingAnalysis",
)


class TestMethodNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("stepping-stone", "stepping_stone"),
            ("stepping_stone", "stepping_stone"),
            ("ss", "stepping_stone"),
            ("path-sampling", "path_sampling"),
            ("path_sampling", "path_sampling"),
            ("ps", "path_sampling"),
        ],
    )
    def test_aliases(self, raw, expected):
        assert M.normalise_method(raw) == expected

    def test_unknown_method_is_refused(self):
        with pytest.raises(C.ContractError):
            M.normalise_method("bridge-sampling")


class TestAnalysisBlock:
    @pytest.mark.parametrize("method", ["stepping-stone", "path-sampling"])
    def test_type_is_a_registry_name(self, method):
        block = M.build_analysis_block(
            method=method,
            chain_length=100,
            path_steps=4,
            ps_file="ps.log",
            results_file="mle.txt",
        )
        # The coverage registry only knows the underscore spellings.
        assert block["type"] in ("path_sampling", "stepping_stone")

    def test_derived_config_is_accepted_by_gate0(self, tmp_path):
        config, _ = M.materialise_compare_config(
            Path(LEFT),
            tmp_path / "m.yaml",
            method="stepping-stone",
            chain_length=100,
            path_steps=4,
            beast_version="1.10.4",
            seed=1,
            ps_file=tmp_path / "ps.log",
            results_file=tmp_path / "mle.txt",
        )
        parsed = ConfigParser().parse(str(config))
        assert parsed.analysis["type"] == "stepping_stone"

    @pytest.mark.parametrize("method", ["stepping-stone", "path-sampling"])
    def test_generated_xml_contains_the_estimators(self, tmp_path, method):
        config, _ = M.materialise_compare_config(
            Path(LEFT),
            tmp_path / f"{method}.yaml",
            method=method,
            chain_length=100,
            path_steps=4,
            beast_version="1.10.4",
            seed=1,
            ps_file=tmp_path / "ps.log",
            results_file=tmp_path / "mle.txt",
        )
        xml = BeastXPy().generate_xml(str(config))
        for element in BEAGLE_ELEMENTS:
            assert f"<{element}" in xml, f"{method}: missing <{element}>"


class TestComparability:
    def _configs(self):
        return [
            ConfigParser().parse(LEFT),
            ConfigParser().parse(RIGHT),
        ]

    def test_same_data_is_comparable(self):
        checks = M.check_comparability(self._configs(), ["a", "b"])
        assert M.comparability_is_ok(checks)
        assert all(item["status"] == "match" for item in checks)

    def test_different_data_is_not_comparable(self):
        left = ConfigParser().parse(LEFT)
        right = ConfigParser().parse("examples/config_aa_jtt.yaml")
        checks = M.check_comparability([left, right], ["a", "b"])
        assert not M.comparability_is_ok(checks)
        assert any(item["status"] == "mismatch" for item in checks)


class TestResultParsing:
    def test_stepping_stone_line(self):
        text = (
            "some BEAST output\n"
            "log marginal likelihood (using stepping stone sampling) from "
            "steppingStoneSamplingAnalysis = -1234.5678\n"
        )
        parsed = M.parse_beast_results(text, "stepping_stone")
        assert parsed["found"] is True
        assert parsed["log_ml"] == pytest.approx(-1234.5678)

    def test_path_sampling_line(self):
        text = (
            "log marginal likelihood (using path sampling) from " "pathSamplingAnalysis = -900.5\n"
        )
        parsed = M.parse_beast_results(text, "path_sampling")
        assert parsed["found"] is True
        assert parsed["log_ml"] == pytest.approx(-900.5)

    def test_absent_line_is_not_a_number(self):
        parsed = M.parse_beast_results("nothing here", "stepping_stone")
        assert parsed["found"] is False
        assert parsed["log_ml"] is None

    def test_placeholder_log_is_flagged(self):
        from beastxpy.coverage.mle import _placeholder_log
        import tempfile

        path = Path(tempfile.mkdtemp()) / "ps.log"
        _placeholder_log(path, 4)
        parsed = M.parse_path_sampling_log(path.read_text(encoding="utf-8"))
        assert parsed["is_placeholder"] is True

    def test_bayes_factor_bands(self):
        # The string reports 2 ln BF and names which model the evidence favours.
        assert "first model (a)" in M.interpret_log_bayes_factor(3.0)
        assert "second model (b)" in M.interpret_log_bayes_factor(-3.0)
        assert "convention" in M.interpret_log_bayes_factor(3.0).lower()
        assert "not computable" in M.interpret_log_bayes_factor(None).lower()


class TestFailClosed:
    def test_missing_beagle_is_reported_not_guessed(self, monkeypatch):
        for key in ("BEASTX_BEAGLE_1_10_4_LIB", "BEASTX_BEAGLE_LIB", "BEAGLE_HOME", "BEAGLE_LIB"):
            monkeypatch.delenv(key, raising=False)
        env = M.probe_beagle_environment("1.10.4", require_beagle=True)
        assert env.available is False
        assert env.reason

    def test_missing_beagle_exits_2(self, tmp_path, monkeypatch):
        for key in ("BEASTX_BEAGLE_1_10_4_LIB", "BEASTX_BEAGLE_LIB", "BEAGLE_HOME", "BEAGLE_LIB"):
            monkeypatch.delenv(key, raising=False)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "beastxpy.main",
                "compare",
                "--configs",
                LEFT,
                RIGHT,
                "--method",
                "stepping-stone",
                "--workdir",
                str(tmp_path / "cmp"),
                "--chain-length",
                "100",
                "--seeds",
                "1",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
            env={**__import__("os").environ},
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert "next step" in (result.stdout + result.stderr).lower()
        # No fabricated numbers.
        assert "n/a" in result.stdout


class TestCliContract:
    def test_fewer_than_two_configs_is_a_usage_error(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "beastxpy.main",
                "compare",
                "--configs",
                LEFT,
                "--method",
                "stepping-stone",
                "--workdir",
                str(tmp_path / "cmp"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        assert result.returncode == 1, result.stdout + result.stderr

    def test_json_schema_when_failing_closed(self, tmp_path, monkeypatch):
        for key in ("BEASTX_BEAGLE_1_10_4_LIB", "BEASTX_BEAGLE_LIB", "BEAGLE_HOME", "BEAGLE_LIB"):
            monkeypatch.delenv(key, raising=False)
        out = tmp_path / "cmp.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "beastxpy.main",
                "compare",
                "--configs",
                LEFT,
                RIGHT,
                "--method",
                "path-sampling",
                "--workdir",
                str(tmp_path / "cmp"),
                "--json",
                str(out),
                "-q",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
        document = json.loads(out.read_text(encoding="utf-8"))
        assert document["schema_version"] == C.SCHEMA_COMPARE
        assert document["models"]
        for model in document["models"]:
            assert model.get("log_ml") is None


class TestMarginalLikelihoodReferenceValues:
    """Analytic anchors for the path-sampling / stepping-stone estimators."""

    def test_path_sampling_matches_a_trapezoid_integral(self):
        # Bin means 0, 2, 4 on theta 0, 0.5, 1 -> trapezoid integral 2.0.
        bins = {0.0: [0.0] * 10, 0.5: [2.0] * 10, 1.0: [4.0] * 10}
        value = M.estimate_log_ml(bins, M.METHOD_PATH_SAMPLING)
        assert value == pytest.approx(2.0, abs=1e-12)

    def test_stepping_stone_matches_the_same_constant_delta_integral(self):
        bins = {0.0: [2.0] * 10, 0.5: [2.0] * 10, 1.0: [2.0] * 10}
        value = M.estimate_log_ml(bins, M.METHOD_STEPPING_STONE)
        assert value == pytest.approx(2.0, abs=1e-12)

    def test_estimator_needs_at_least_two_steps(self):
        assert M.estimate_log_ml({0.0: [1.0] * 10}, M.METHOD_PATH_SAMPLING) is None
        assert M.estimate_log_ml({}, M.METHOD_STEPPING_STONE) is None

    def test_standard_error_is_a_delta_method_lower_bound(self):
        rng = np.random.default_rng(11)
        bins = {
            0.0: list(rng.normal(0.0, 1.0, 1000)),
            0.5: list(rng.normal(1.0, 1.0, 1000)),
            1.0: list(rng.normal(2.0, 1.0, 1000)),
        }
        se = M.estimate_standard_error(bins, M.METHOD_PATH_SAMPLING)
        assert se is not None and 0.0 < se < 0.2


class TestBayesFactorUncertainty:
    def test_small_log_bf_is_reported_as_not_evidence(self):
        text = M.interpret_log_bayes_factor(0.5, se_log_bf=1.0)
        assert "not distinguishable from zero" in text

    def test_large_log_bf_reports_the_ratio(self):
        text = M.interpret_log_bayes_factor(5.0, se_log_bf=1.0)
        assert "> 2*SE" in text
        assert "ratio 5.00" in text


class TestSeedSummary:
    def test_model_result_exposes_per_seed_values_and_range(self):
        from beastxpy.compare import ModelResult, SeedResult

        model = ModelResult(
            name="m",
            config="m.yaml",
            method=M.METHOD_PATH_SAMPLING,
            workdir="w",
            chain_length=1000,
            path_steps=10,
            seeds=[1, 2],
            beast_version="1.10.4",
            log_ml=-10.0,
            se=0.5,
            runs=[
                SeedResult(seed=1, status=M.STATUS_OK, log_ml=-10.0, se=0.5),
                SeedResult(seed=2, status=M.STATUS_OK, log_ml=-11.0, se=0.5),
            ],
        )
        payload = model.as_dict()
        assert [entry["log_ml"] for entry in payload["seed_estimates"]] == [-10.0, -11.0]
        assert payload["seed_log_ml_range"] == pytest.approx(1.0)


class TestCompareParallelArgument:
    def test_parallel_zero_is_rejected_before_any_environment_probe(self):
        with pytest.raises(C.ContractError) as excinfo:
            M.run_comparison(
                [LEFT, RIGHT],
                method=M.METHOD_STEPPING_STONE,
                workdir="unused",
                parallel=0,
            )
        assert "--parallel" in str(excinfo.value)


@pytest.mark.java
def test_official_path_sampling_example_matches_its_analytic_reference(tmp_path):
    """Reference-value regression for marginal likelihood.

    Uses BEAST's own ``testSimplePathSampling.xml`` (official v1.10.4 example),
    whose fixed-model log marginal likelihood is analytic and printed in the
    XML: ``-0.5*log(4*pi) - x^2/4 = -7.5155`` with x=5.  Only chain length,
    path steps and log cadence are reduced so the test finishes quickly.
    """
    import re
    import subprocess

    from beastxpy.validator import find_java_runtime, lookup_jar_details

    runtime = find_java_runtime()
    if not runtime.usable:
        pytest.fail(f"no usable Java runtime: {runtime.reason}")
    jar = lookup_jar_details("1.10.4")
    if jar is None:
        pytest.fail("no BEAST 1.10.4 jar found")

    source = Path(__file__).parent / "fixtures" / "path_sampling" / "testSimplePathSampling.xml"
    text = source.read_text(encoding="utf-8")
    text = re.sub(r'chainLength="\d+"', 'chainLength="5000"', text)
    text = re.sub(r'pathSteps="\d+"', 'pathSteps="8"', text)
    text = re.sub(r'logEvery="100"', 'logEvery="20"', text)
    xml_path = tmp_path / "testSimplePathSampling.xml"
    xml_path.write_text(text, encoding="utf-8")

    result = subprocess.run(
        [
            str(runtime.path),
            "-cp",
            str(jar["path"]),
            "dr.app.beast.BeastMain",
            "-overwrite",
            str(xml_path),
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(
            "BEAST failed on the official path-sampling example:\n"
            + (result.stderr or result.stdout or "")[-3000:]
        )

    log_path = tmp_path / "testSimplePathSampling.fixed.log"
    assert log_path.is_file(), "BEAST did not write the fixed-model log"
    log_text = log_path.read_text(encoding="utf-8")

    # BEAST prints the marginal-likelihood summary on stdout; the .log file
    # holds the raw theta/delta samples used for the independent re-estimate.
    parsed = M.parse_beast_results(result.stdout, M.METHOD_PATH_SAMPLING)
    assert parsed["found"], (result.stdout or "")[-3000:]
    # Analytic reference from the XML; a 5000-step-per-bin run is noisy, so
    # use a one-log-unit band rather than the 1e-6 recomputation tolerance.
    assert parsed["log_ml"] == pytest.approx(-7.5155, abs=1.0)

    # The project's independent re-estimate from the raw path samples must
    # agree with BEAST's printed value (this is the check compare performs).
    bins = M.parse_path_sampling_log(log_text)["bins"]
    recomputed = M.estimate_log_ml(bins, M.METHOD_PATH_SAMPLING)
    assert recomputed == pytest.approx(parsed["log_ml"], abs=1e-6)


def test_combined_seed_error_is_the_standard_error_of_the_mean():
    from beastxpy.compare import ModelResult, SeedResult, _combine_seed_estimates

    runs = [
        SeedResult(seed=1, status=M.STATUS_OK, log_ml=-10.0, se=0.5),
        SeedResult(seed=2, status=M.STATUS_OK, log_ml=-11.0, se=0.5),
    ]
    mean, se = _combine_seed_estimates(runs)
    assert mean == pytest.approx(-10.5)
    # sqrt(mean(se^2)/k + var(logML)/k) = sqrt(0.125 + 0.25)
    assert se == pytest.approx(0.6123724357)
    _ = ModelResult  # imported for the public API check above


def test_pairwise_bf_has_no_se_when_a_model_se_is_unknown():
    from beastxpy.compare import ModelResult, pairwise_log_bayes_factors

    def model(name, se):
        return ModelResult(
            name=name,
            config=f"{name}.yaml",
            method=M.METHOD_STEPPING_STONE,
            workdir="w",
            chain_length=1000,
            path_steps=10,
            seeds=[1],
            beast_version="1.10.4",
            status=M.STATUS_OK,
            log_ml=-10.0,
            se=se,
        )

    pair = pairwise_log_bayes_factors([model("a", 0.5), model("b", None)])[0]
    assert pair["se_log_bf"] is None
    assert "not distinguishable" not in pair["interpretation"]


def test_default_path_steps_prints_a_publication_grade_reminder(monkeypatch, capsys, tmp_path):
    import beastxpy.cli_compare as cli

    def fake_run(*_args, **_kwargs):
        return (
            {
                "method": "path-sampling",
                "settings": {},
                "models": [],
                "pairwise": [],
                "workdir": str(tmp_path),
            },
            2,
        )

    monkeypatch.setattr(cli.M, "run_comparison", fake_run)
    from beastxpy.main import main

    code = main(
        [
            "compare",
            "--configs",
            "a.yaml",
            "--method",
            "path-sampling",
            "--workdir",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "quick estimate" in captured.err
    assert "at least 50" in captured.err
