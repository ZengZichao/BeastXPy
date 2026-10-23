"""Integration tests for the BeastXPy API facade."""

from __future__ import annotations


import pytest

from beastxpy.api import BeastXPy


@pytest.fixture
def bxp() -> BeastXPy:
    return BeastXPy(verbose=False, force=True)


class TestBeastXPyAPI:
    """BeastXPy facade tests."""

    def test_generate_xml(self, bxp, examples_dir):
        xml = bxp.generate_xml(
            str(examples_dir / "config_basic.yaml"),
            beast_version=None,
            allow_unvalidated=True,
        )
        assert "<beast" in xml
        assert "<mcmc" in xml

    def test_generate_xml_jc69(self, bxp, examples_dir):
        xml = bxp.generate_xml(
            str(examples_dir / "config_subst_jc69.yaml"),
            beast_version=None,
            allow_unvalidated=True,
        )
        assert "<beast" in xml
        # JC69 kappa should not carry estimate or bounds (BEAST 1 fixed)
        assert 'id="kappa"' in xml
        assert 'estimate="false"' not in xml

    def test_validate_xml(self, bxp, examples_dir):
        result = bxp.validate_xml(
            str(examples_dir / "config_basic.yaml"),
            beast_version=None,
        )
        # Returns a dict with ok status and findings
        assert isinstance(result, dict)
        assert "ok" in result
        assert "gate1_findings" in result

    def test_diagnose(self, bxp, examples_dir, tmp_path):
        # Always write diagnostics into pytest's temp tree: the repository
        # root must stay free of generated reports.
        report = bxp.diagnose(
            str(examples_dir / "config_calibrated_yule.yaml"),
            output_dir=str(tmp_path / "diagnostics"),
        )
        assert report is not None
        assert (tmp_path / "diagnostics").is_dir()

    def test_generate_fingerprint(self, bxp, examples_dir):
        fp = bxp.generate_fingerprint(
            str(examples_dir / "config_basic.yaml"),
        )
        assert "BXP-" in fp

    def test_generate_methods(self, bxp, examples_dir):
        latex = bxp.generate_methods(
            str(examples_dir / "config_basic.yaml"),
        )
        assert isinstance(latex, str)

    def test_list_models(self, bxp):
        models = bxp.list_models()
        assert "substitution_models" in models or "substitution" in models
        assert "distributions" in models or "distribution" in models

    def test_check_environment(self, bxp):
        env = bxp.check_environment()
        assert isinstance(env, dict)


class TestAllExampleConfigs:
    """Run the full pipeline (gates 0-2) on every example."""

    @pytest.mark.parametrize(
        "config_name",
        [
            "config_aa_jtt.yaml",
            "config_aa_wag.yaml",
            "config_advanced.yaml",
            "config_basic.yaml",
            "config_calibrated_yule.yaml",
            "config_coalescent_exponential.yaml",
            "config_ebsp.yaml",
            "config_multi_partition.yaml",
            "config_relaxed_clock.yaml",
            "config_relaxed_exponential.yaml",
            "config_rlc.yaml",
            "config_skyline.yaml",
            "config_subst_gtr.yaml",
            "config_subst_jc69.yaml",
            "config_subst_tn93.yaml",
            "config_tip_dates.yaml",
        ],
    )
    def test_all_examples_generate(self, bxp, examples_dir, config_name):
        xml = bxp.generate_xml(
            str(examples_dir / config_name),
            beast_version=None,
            allow_unvalidated=True,
        )
        assert "<beast" in xml


def test_gate3_raw_status_preserves_environment_missing():
    """The facade keeps the four-state detail in raw_status (Minor-1)."""
    from types import SimpleNamespace

    from beastxpy.api import BeastXPy

    fake = SimpleNamespace(
        ok=False,
        status="environment-missing",
        stderr="no usable Java runtime",
        stdout="",
        exit_code=None,
        command=[],
        findings=[],
        skipped=True,
        executed=False,
        tree_likelihood_rule_source=None,
    )
    outcome = BeastXPy()._interpret_validation_result(fake, "1.10.4", allow_unvalidated=False)
    assert outcome.status == "not-run"
    assert outcome.raw_status == "environment-missing"
    assert outcome.environment_error is True
    assert outcome.as_dict()["raw_status"] == "environment-missing"
