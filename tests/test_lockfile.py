"""Tests for the environment lock (``beastxpy.lockfile``).

A lock is shareable evidence, so the guarantees under test are: it records
digests rather than paths, it contains no credentials, it is sealed against
edit, and verification distinguishes a match from a tampered file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from beastxpy import _contracts as C
from beastxpy import lockfile as L

CONFIG = "examples/config_basic.yaml"


@pytest.fixture(scope="module")
def document() -> dict:
    return L.build_lock(CONFIG, seeds=[1, 2])


class TestDocumentShape:
    def test_schema_and_identity(self, document):
        assert document["schema_version"] == C.SCHEMA_LOCK
        assert document["tool_version"] == C.tool_version()
        assert document["tool"]["version"] == C.tool_version()

    def test_records_the_seeds(self, document):
        assert document["seeds"] == [1, 2]

    def test_records_jar_digest_not_path(self, document):
        beast = document["beast"]
        assert len(beast["jar_sha256"]) == 64
        assert "/" not in beast["jar_name"] and "\\" not in beast["jar_name"]

    def test_records_dependency_versions(self, document):
        deps = document["dependencies"]
        assert set(deps) >= {"pyyaml", "numpy", "scipy", "biopython", "matplotlib", "defusedxml"}

    def test_config_hash_matches_the_file(self, document):
        assert document["config"]["content_sha256"] == L.config_content_sha256(Path(CONFIG))

    def test_sealed(self, document):
        ok, fields, problem = L.verify_integrity(document)
        assert ok, (fields, problem)


class TestPrivacy:
    def test_no_absolute_paths_or_secrets(self, document):
        assert L.find_privacy_problems(document) == []

    def test_scanner_still_catches_a_real_secret(self):
        assert L.find_privacy_problems({"x": {"api_token": "abc123"}})
        assert L.find_privacy_problems({"x": "C:\\Users\\someone\\secret"})

    def test_shareable_assertion_refuses_a_leak(self):
        with pytest.raises(L.LockError):
            L.assert_shareable({"java": {"path": "C:\\Users\\someone\\jdk"}})

    def test_privacy_block_does_not_reject_itself(self, document):
        # The document's own redaction block says "secrets: none"; scanning it
        # must not read that as a leak.
        assert "secrets" in document["redaction"]
        L.assert_shareable(document)


class TestTamperDetection:
    def test_edited_field_is_detected(self, document):
        tampered = json.loads(json.dumps(document))
        tampered["beast"]["jar_sha256"] = "0" * 64
        ok, fields, _ = L.verify_integrity(tampered)
        assert not ok
        assert "beast.jar_sha256" in fields

    def test_load_lock_rejects_a_tampered_file(self, document, tmp_path):
        tampered = json.loads(json.dumps(document))
        tampered["seeds"] = [99]
        path = tmp_path / "beastxpy.lock"
        path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(L.LockTamperedError):
            L.load_lock(path)

    def test_load_lock_rejects_foreign_json(self, tmp_path):
        path = tmp_path / "other.json"
        path.write_text(json.dumps({"schema_version": "something/else"}), encoding="utf-8")
        with pytest.raises(L.LockTamperedError):
            L.load_lock(path)

    def test_round_trip_through_disk(self, document, tmp_path):
        path = tmp_path / "beastxpy.lock"
        L.write_lock(path, document)
        assert L.load_lock(path)["config"]["content_sha256"] == document["config"]["content_sha256"]


class TestVerification:
    def test_same_environment_matches(self, document):
        report = L.check_lock(document, config_path=CONFIG)
        assert report.verdict == "match"
        assert not report.mismatches
        assert report.matches

    def test_a_different_config_is_a_mismatch(self, document):
        report = L.check_lock(document, config_path="examples/config_subst_gtr.yaml")
        assert report.mismatches
        assert not report.ok

    def test_missing_config_is_reported_as_unchecked(self, document):
        report = L.check_lock(document)
        assert any(item.get("field", "").startswith("config.") for item in report.unchecked)
        assert report.verdict == "match_partial"
        assert report.ok
        assert "not checked" in (report.reason or "")


class TestCliContract:
    def test_lock_then_check_via_subprocess(self, tmp_path):
        import subprocess
        import sys

        lock_path = tmp_path / "beastxpy.lock"
        first = subprocess.run(
            [
                sys.executable,
                "-m",
                "beastxpy.main",
                "lock",
                "--config",
                CONFIG,
                "--output",
                str(lock_path),
                "--seeds",
                "1",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        assert first.returncode == 0, first.stderr
        assert lock_path.is_file()

        second = subprocess.run(
            [
                sys.executable,
                "-m",
                "beastxpy.main",
                "reproduce",
                "--lock",
                str(lock_path),
                "--check",
                "--config",
                CONFIG,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        assert second.returncode == 0, second.stderr

    def test_tampered_lock_exits_3(self, tmp_path):
        import subprocess
        import sys

        lock_path = tmp_path / "beastxpy.lock"
        document = L.build_lock(CONFIG, seeds=[1])
        L.write_lock(lock_path, document)
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        data["beast"]["jar_sha256"] = "0" * 64
        lock_path.write_text(json.dumps(data), encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "beastxpy.main",
                "reproduce",
                "--lock",
                str(lock_path),
                "--check",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        assert result.returncode == 3, result.stdout + result.stderr


class TestReproduceRun:
    """Regression tests for the execute path, which needs no real BEAST here."""

    def test_beagle_environment_is_set_then_restored(self, document, monkeypatch, tmp_path):
        import os

        import beastxpy.lockfile as L

        monkeypatch.setattr(
            L, "check_lock", lambda *a, **k: L.ReproduceReport(mode="check", verdict="match")
        )
        monkeypatch.setattr(L, "probe_beagle_environment", lambda *a, **k: object())
        monkeypatch.setattr(L, "beagle_runtime_environment", lambda _env: {"BXP_TEST_ONLY": "1"})

        seen = {}

        class FakeRunner:
            def __init__(self, **_kwargs):
                pass

            def run(self):
                seen["set"] = os.environ.get("BXP_TEST_ONLY")
                from types import SimpleNamespace

                results = [
                    SimpleNamespace(seed=1, status="ok", exit_code=0, elapsed_seconds=0.1, ok=True)
                ]
                return SimpleNamespace(
                    results=results,
                    overall_ok=True,
                    beast_version="1.10.4",
                    fingerprint="f",
                    content_fingerprint="c",
                    jar_sha256="0" * 64,
                    as_dict=lambda: {"results": [{"seed": 1}]},
                )

        monkeypatch.setattr(L, "BeastRunner", FakeRunner)
        monkeypatch.delenv("BXP_TEST_ONLY", raising=False)

        report = L.run_from_lock(document, config_path=CONFIG, workdir=tmp_path / "run")
        assert seen["set"] == "1", "the BEAGLE environment was not exposed to the run"
        assert os.environ.get("BXP_TEST_ONLY") is None, "environment was not restored"
        assert report.verdict == "ran"
        assert report.mode == "run"
        started = report.run["started_at"]
        assert isinstance(started, str) and len(started) >= 19, started
        assert started.endswith("Z")

    def test_json_carries_a_schema_version(self, document, tmp_path):
        import subprocess
        import sys

        lock_path = tmp_path / "beastxpy.lock"
        L.write_lock(lock_path, document)
        out = tmp_path / "repro.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "beastxpy.main",
                "reproduce",
                "--lock",
                str(lock_path),
                "--check",
                "--config",
                CONFIG,
                "--json",
                str(out),
                "-q",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["schema_version"] == C.SCHEMA_REPRODUCE
        assert payload["mode"] == "check"


def test_beagle_observed_version_drift_is_reported(document):
    """`check_lock` compares the observed BEAGLE version (Minor-8)."""
    assert "beagle.observed_version" in L.TRACKED_FIELDS
    altered = json.loads(json.dumps(document))
    altered["beagle"]["observed_version"] = "9.9.9-not-installed"
    report = L.check_lock(altered, config_path=CONFIG)
    assert any(m["field"] == "beagle.observed_version" for m in report.mismatches)
    assert not report.ok
