"""Unit tests for calibration diagnostics."""

from __future__ import annotations

import numpy as np

from beastxpy.diagnostics import (
    CalibrationDiagnostics,
    DiagnosticFinding,
    DiagnosticReport,
    sample_distribution,
)


class TestSampleDistribution:
    """Distribution sampling."""

    def test_lognormal(self):
        samples = sample_distribution("lognormal", {"mean": 1.0, "stdev": 0.5}, offset=60.0)
        assert len(samples) > 0
        assert np.all(samples >= 60.0)  # offset applied

    def test_normal(self):
        samples = sample_distribution("normal", {"mean": 7.0, "stdev": 1.0}, offset=0.0)
        assert len(samples) > 0

    def test_uniform(self):
        samples = sample_distribution("uniform", {"lower": 0.0, "upper": 10.0}, offset=0.0)
        assert len(samples) > 0
        assert np.all(samples >= 0.0)
        assert np.all(samples <= 10.0)

    def test_exponential(self):
        samples = sample_distribution("exponential", {"mean": 1.0}, offset=0.0)
        assert len(samples) > 0

    def test_unknown_distribution(self):
        # sample_distribution may return None for unknown distributions
        samples = sample_distribution("unknown", {}, 0.0)
        assert samples is None or len(samples) > 0


class TestDiagnosticFinding:
    """DiagnosticFinding data structure."""

    def test_finding_creation(self):
        finding = DiagnosticFinding(
            severity="error",
            kind="TIME_CONFLICT",
            message="test finding",
            where="cal1",
            code="TIME_CONFLICT",
        )
        assert finding.severity == "error"
        assert finding.code == "TIME_CONFLICT"
        d = finding.as_dict()
        assert d["severity"] == "error"


class TestDiagnosticReport:
    """DiagnosticReport container."""

    def test_has_errors(self):
        report = DiagnosticReport()
        report.findings.append(DiagnosticFinding("error", "E1", "msg"))
        assert report.has_errors is True

    def test_has_warnings(self):
        report = DiagnosticReport()
        report.findings.append(DiagnosticFinding("warning", "W1", "msg"))
        assert report.has_warnings is True
        assert report.has_errors is False


class TestCalibrationDiagnostics:
    """End-to-end diagnostics runs."""

    def test_diagnose_calibrated_config(self, config_calibrated):
        diag = CalibrationDiagnostics(config_calibrated)
        report = diag.run()
        assert report is not None

    def test_diagnose_advanced_config(self, config_advanced):
        diag = CalibrationDiagnostics(config_advanced)
        report = diag.run()
        assert report is not None

    def test_temporal_consistency(self, config_calibrated):
        diag = CalibrationDiagnostics(config_calibrated)
        diag.check_temporal_consistency()
        assert diag.findings is not None

    def test_text_report(self, config_calibrated):
        diag = CalibrationDiagnostics(config_calibrated)
        diag.run()
        text = diag.render_text_report()
        assert isinstance(text, str)
        assert len(text) > 0


class TestLognormalParameterisationHint:
    def test_omitted_mean_in_real_space_emits_an_info_hint(self, config_basic):
        report = CalibrationDiagnostics(config_basic).run()
        hints = [f for f in report.findings if f.code == "LOGNORMAL_LOG_SPACE_MEAN"]
        assert hints, "expected the V04 meanInRealSpace migration hint"
        assert all(f.severity == "info" for f in hints)
        assert "median" in hints[0].message and "95% interval" in hints[0].message

    def test_explicit_flag_suppresses_the_hint(self, config_basic):
        config_basic.calibrations[0].distribution.parameters["meanInRealSpace"] = True
        report = CalibrationDiagnostics(config_basic).run()
        assert not [f for f in report.findings if f.code == "LOGNORMAL_LOG_SPACE_MEAN"]
