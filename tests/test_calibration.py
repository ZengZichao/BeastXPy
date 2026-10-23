"""Unit tests for calibration assembly and diagnostics."""

from __future__ import annotations


from beastxpy.calibration import (
    build_taxa_sets,
    calibration_taxa_id,
    children_of,
    monophyly_fragment,
    monophyly_id,
    prior_fragment,
    tmrca_fragment,
    tmrca_id,
)


class TestCalibrationIds:
    """ID generation helpers."""

    def test_tmrca_id(self):
        assert tmrca_id("Test") == "tmrca(Test)"

    def test_monophyly_id(self):
        assert monophyly_id("Test") == "monophyly(Test)"

    def test_calibration_taxa_id(self):
        assert calibration_taxa_id("Test") == "Test"


class TestCalibrationFragments:
    """XML fragment builders."""

    def test_tmrca_fragment(self, config_calibrated):
        cal = config_calibrated.calibrations[0]
        frag = tmrca_fragment(cal)
        assert frag["tag"] == "tmrcaStatistic"
        assert "id" in frag["attrib"]
        assert "children" in frag

    def test_monophyly_fragment(self, config_calibrated):
        cal = config_calibrated.calibrations[0]
        frag = monophyly_fragment(cal)
        assert frag["tag"] == "monophylyStatistic"
        assert "id" in frag["attrib"]

    def test_prior_fragment(self, config_calibrated):
        cal = config_calibrated.calibrations[0]
        from beastxpy.registry import ModelRegistry

        registry = ModelRegistry()
        frag = prior_fragment(cal, registry)
        assert frag["tag"] == "logNormalPrior"  # lognormal distribution

    def test_build_taxa_sets(self, config_calibrated):
        fragments = build_taxa_sets(config_calibrated)
        assert len(fragments) >= 1
        # Should include the global <taxa id="taxa"> and calibration taxa sets
        tags = [f["tag"] for f in fragments]
        assert "taxa" in tags


class TestCalibrationChildren:
    """children_of helper."""

    def test_children_of_taxa(self, config_calibrated):
        cal = config_calibrated.calibrations[0]
        children = children_of(cal)
        assert isinstance(children, list)


class TestCalibrationValidation:
    """Calibration correctness."""

    def test_calibration_distribution_present(self, config_calibrated):
        for cal in config_calibrated.calibrations:
            assert cal.distribution is not None

    def test_calibration_taxa_nonempty(self, config_calibrated):
        for cal in config_calibrated.calibrations:
            assert cal.taxa and len(cal.taxa) > 0

    def test_advanced_calibrations(self, config_advanced):
        assert len(config_advanced.calibrations) == 3
        names = [c.name for c in config_advanced.calibrations]
        assert "Catarrhini" in names
        assert "Hominoidea" in names
        assert "Homininae" in names
