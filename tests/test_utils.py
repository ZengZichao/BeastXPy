"""Unit tests for utility functions."""

from __future__ import annotations

import numpy as np

from beastxpy.utils import (
    canonical_json,
    escape_xml_attr,
    escape_xml_text,
    format_number,
    format_param_value,
    hpd_interval,
    normalize_xml_for_compare,
    param_id,
    sanitize_id,
    sha256_hex,
    unique_id,
    valid_xml_charset_check,
    wasserstein_distance,
)


class TestIds:
    """ID helpers."""

    def test_sanitize_id(self):
        assert sanitize_id("My Model!") == "My_Model_"
        assert sanitize_id("simple") == "simple"

    def test_unique_id(self):
        used = {"existing"}
        result = unique_id("name", used)
        assert result == "name"  # 'name' not in used, so returns base
        assert "name" in used  # mutates used

    def test_unique_id_collision(self):
        used = {"name"}
        result = unique_id("name", used)
        assert result != "name"  # collision resolved

    def test_param_id(self):
        assert param_id("gene1", "kappa") == "gene1.kappa"
        assert param_id("", "kappa") == "kappa"


class TestXMLEscaping:
    """XML escape helpers."""

    def test_escape_text(self):
        assert escape_xml_text("a & b") == "a &amp; b"
        assert escape_xml_text("a < b") == "a &lt; b"

    def test_escape_attr(self):
        assert escape_xml_attr('say "hi"') == "say &quot;hi&quot;"
        assert escape_xml_attr("a & b") == "a &amp; b"


class TestFormatting:
    """Number and value formatting."""

    def test_format_number(self):
        assert format_number(0.5) == "0.5"
        assert format_number(1) == "1"

    def test_format_param_value(self):
        assert format_param_value(1.0) == "1"
        assert format_param_value("0.25 0.25") == "0.25 0.25"
        assert format_param_value([0.25, 0.75]) == "0.25 0.75"


class TestXMLNormalize:
    """XML comparison helpers."""

    def test_normalize_removes_comments(self):
        xml = "<beast><!-- comment --><mcmc/></beast>"
        result = normalize_xml_for_compare(xml)
        assert "comment" not in result

    def test_normalize_preserves_structure(self):
        xml = '<beast id="test"><mcmc/></beast>'
        result = normalize_xml_for_compare(xml)
        assert '<beast id="test">' in result


class TestStats:
    """Statistical utility functions."""

    def test_hpd_interval(self):
        samples = np.random.default_rng(42).normal(0, 1, 10000)
        lo, hi = hpd_interval(samples, alpha=0.05)
        assert lo <= np.median(samples) <= hi
        assert lo < hi

    def test_wasserstein(self):
        p = np.array([0.0, 0.0, 1.0])
        q = np.array([0.0, 0.0, 1.0])
        assert wasserstein_distance(p, q) == 0.0

    def test_wasserstein_different(self):
        # Use distributions with clearly different empirical CDFs
        p = np.array([0.0, 0.0, 0.0, 1.0, 1.0])
        q = np.array([0.0, 1.0, 1.0, 1.0, 1.0])
        assert wasserstein_distance(p, q) > 0.0


class TestHashing:
    """SHA-256 and JSON helpers."""

    def test_sha256(self):
        result = sha256_hex("test")
        assert len(result) == 64
        assert result == sha256_hex("test")

    def test_canonical_json(self):
        obj = {"b": 1, "a": 2}
        result1 = canonical_json(obj)
        obj2 = {"a": 2, "b": 1}
        result2 = canonical_json(obj2)
        assert result1 == result2


class TestValidation:
    """Input validation helpers."""

    def test_valid_xml_charset_check(self):
        assert valid_xml_charset_check("normal_name") is None
        # Check for XML-invalid chars (control chars etc.)
        assert valid_xml_charset_check("bad\x00name") is not None
