"""Unit tests for reproducibility artefacts."""

from __future__ import annotations


from beastxpy.reproducibility import (
    analysis_fingerprint,
    data_digest,
    fingerprint_comment,
    fingerprint_payload,
    generate_methods_latex,
    write_fingerprint_json,
)


class TestFingerprint:
    """Analysis fingerprint tests."""

    def test_fingerprint_consistent(self, config_basic):
        fp1 = analysis_fingerprint(config_basic)
        fp2 = analysis_fingerprint(config_basic)
        assert fp1 == fp2
        assert fp1.startswith("BXP-")

    def test_fingerprint_differs_for_different_configs(self, config_basic, config_jc69):
        fp1 = analysis_fingerprint(config_basic)
        fp2 = analysis_fingerprint(config_jc69)
        assert fp1 != fp2

    def test_data_digest(self, config_basic):
        digest = data_digest(config_basic)
        assert len(digest) >= 16

    def test_fingerprint_payload(self, config_basic):
        payload = fingerprint_payload(config_basic)
        assert isinstance(payload, dict)
        assert "fingerprint" in payload
        assert "beast_version" in payload


class TestFingerprintComment:
    """XML comment generation."""

    def test_comment_content(self, config_basic):
        comment = fingerprint_comment(config_basic)
        assert "BXP-" in comment


class TestFingerprintJSON:
    """JSON sidecar output."""

    def test_write_json(self, config_basic, tmp_path):
        out = tmp_path / "fingerprint.json"
        result = write_fingerprint_json(config_basic, str(out))
        assert out.exists()
        assert isinstance(result, dict)


class TestMethodsLatex:
    """LaTeX methods paragraph generation."""

    def test_generate_methods(self, config_basic):
        latex = generate_methods_latex(config_basic)
        assert isinstance(latex, str)
        assert len(latex) > 0
        # Should be LaTeX-ish
        assert "\\" in latex or "BEAST" in latex

    def test_generate_methods_calibrated(self, config_calibrated):
        latex = generate_methods_latex(config_calibrated)
        assert isinstance(latex, str)
        assert len(latex) > 0
