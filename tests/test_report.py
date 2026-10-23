"""Tests for the HTML run report (``beastxpy.report``).

The report is the artefact a user may hand to a collaborator, so the tests
concentrate on the properties that make it safe and honest: it opens offline,
it escapes user text, it speaks one language at a time, and it says
"not assessed" rather than inventing diagnostics.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from beastxpy import _contracts as C
from beastxpy import report as R

WORKDIR = Path("tests/fixtures/assess_multi")


@pytest.fixture()
def workdir(tmp_path) -> Path:
    target = tmp_path / "run"
    shutil.copytree(WORKDIR, target)
    return target


class TestSelfContained:
    def test_no_external_references(self, workdir, tmp_path):
        html_path, _ = R.build_report(workdir, tmp_path / "r.html", with_plot=False)
        text = html_path.read_text(encoding="utf-8")
        assert "<script src" not in text
        assert "@import" not in text
        assert "//cdn" not in text
        assert not re.findall(r"https?://", text)

    def test_inline_image_is_a_data_uri(self, workdir, tmp_path):
        html_path, _ = R.build_report(workdir, tmp_path / "r.html", with_plot=True)
        text = html_path.read_text(encoding="utf-8")
        if "data:image" in text:
            assert "data:image/png;base64," in text


class TestEscaping:
    def test_user_text_is_escaped(self, workdir, tmp_path):
        meta_path = workdir / "1" / "run_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        payload = '<script>alert(1)</script>"><img src=x onerror=alert(1)>'
        meta["command"] = [payload]
        meta["fingerprint"] = payload
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        html_path, _ = R.build_report(workdir, tmp_path / "r.html", with_plot=False)
        text = html_path.read_text(encoding="utf-8")
        assert "<script>alert(1)</script>" not in text
        assert "onerror=alert(1)>" not in text
        assert "&lt;script&gt;" in text

    def test_chinese_taxon_text_is_escaped_not_mangled(self, workdir, tmp_path):
        meta_path = workdir / "1" / "run_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["fingerprint"] = "人科 & <演化>"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        html_path, _ = R.build_report(workdir, tmp_path / "r.html", with_plot=False)
        text = html_path.read_text(encoding="utf-8")
        assert "人科" in text
        assert "&lt;演化&gt;" in text


class TestLanguage:
    def test_english_report_has_no_chinese_section_labels(self, workdir, tmp_path):
        html_path, _ = R.build_report(workdir, tmp_path / "en.html", lang="en", with_plot=False)
        text = html_path.read_text(encoding="utf-8")
        assert R.LABELS_EN["sec_limitations"] in text
        assert R.LABELS_ZH["sec_limitations"] not in text

    def test_chinese_report_uses_chinese_labels(self, workdir, tmp_path):
        html_path, _ = R.build_report(workdir, tmp_path / "zh.html", lang="zh", with_plot=False)
        text = html_path.read_text(encoding="utf-8")
        assert R.LABELS_ZH["sec_limitations"] in text
        assert R.LABELS_EN["sec_limitations"] not in text

    def test_rejects_unknown_language(self, workdir, tmp_path):
        with pytest.raises(C.ContractError):
            R.build_report(workdir, tmp_path / "x.html", lang="fr", with_plot=False)


class TestHonesty:
    def test_not_assessed_is_stated(self, workdir, tmp_path):
        html_path, data = R.build_report(workdir, tmp_path / "r.html", with_plot=False)
        text = html_path.read_text(encoding="utf-8")
        assert "not assessed" in text.lower() or "未评估" in text
        assert not (data["assessment"] or {}).get("available")

    def test_assessment_is_used_when_supplied(self, workdir, tmp_path):
        from beastxpy import assessment as A

        doc = A.assess_workdir(workdir, ess=0.1, psrf=1000.0)
        path = tmp_path / "assessment.json"
        C.write_json(path, doc)
        html_path, data = R.build_report(
            workdir, tmp_path / "r.html", assessment_path=path, with_plot=False
        )
        assert data["assessment"]["available"] is True
        assert data["assessment"]["verdict"] == doc["verdict"]

    def test_conclusion_does_not_claim_convergence(self, workdir, tmp_path):
        _, data = R.build_report(workdir, tmp_path / "r.html", with_plot=False)
        limitations = " ".join(data.get("limitations") or []).lower()
        assert "convergence" in limitations or "收敛" in limitations


class TestJsonEnvelope:
    def test_schema_and_keys(self, workdir, tmp_path):
        json_path = tmp_path / "report.json"
        R.build_report(workdir, tmp_path / "r.html", json_path=json_path, with_plot=False)
        document = json.loads(json_path.read_text(encoding="utf-8"))
        assert document["schema_version"] == C.SCHEMA_REPORT
        for key in (
            "conclusion",
            "provenance",
            "gates",
            "run_metadata",
            "assessment",
            "limitations",
        ):
            assert key in document


class TestCliContract:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "beastxpy.main", "report", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )

    def test_missing_workdir_exits_2(self, tmp_path):
        result = self._run(str(tmp_path / "nope"), "--output", str(tmp_path / "r.html"))
        assert result.returncode == 2, result.stdout + result.stderr
        assert "next step" in (result.stdout + result.stderr).lower()

    def test_successful_report(self, workdir, tmp_path):
        output = tmp_path / "r.html"
        result = self._run(str(workdir), "--output", str(output), "--lang", "en")
        assert result.returncode == 0, result.stderr
        assert output.is_file()
        assert "<html" in output.read_text(encoding="utf-8").lower()
