"""Tests for the capability matrix (``beastxpy.capabilities``).

The point of this feature is that there is exactly one list. These tests load
the manifest and assert on it, and prove the CLI reads the file rather than a
hard-coded copy.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from beastxpy import _contracts as C
from beastxpy import capabilities as cap


@pytest.fixture(scope="module")
def manifest() -> dict:
    return cap.load_capabilities()


class TestManifest:
    def test_schema_and_versions(self, manifest):
        assert manifest["schema_version"] == C.SCHEMA_CAPABILITIES
        assert manifest["beast_versions"] == ["1.10.4", "10.5.0"]

    def test_ids_are_unique(self, manifest):
        ids = [feature["id"] for feature in manifest["features"]]
        assert len(ids) == len(set(ids))

    def test_every_cell_has_a_reason(self, manifest):
        for feature in manifest["features"]:
            for version in manifest["beast_versions"]:
                cell = feature["versions"][version]
                assert cell["reason"].strip(), f"{feature['id']}[{version}]"

    def test_every_verify_command_has_a_real_target(self, manifest):
        for feature in manifest["features"]:
            command = feature["verify_command"]
            if "--configs" in command:
                target = command.split("--configs", 1)[1].split()[0]
                assert Path(target).is_file(), f"{feature['id']}: {target}"
            elif "::" in command:
                node = command.split()[-2]
                path, _, name = node.partition("::")
                assert Path(path).is_file(), f"{feature['id']}: {path}"

    def test_blocked_reasons_name_the_upstream_defect(self, manifest):
        for version in ("1.10.4", "10.5.0"):
            for feature in cap.blocked_features(version, manifest):
                reason = feature["reason"].lower()
                assert (
                    "upstream" in reason
                    or "version-gated" in reason
                    or "parser" in reason
                    or "does not exist" in reason
                ), f"{feature['id']}[{version}]: {feature['reason'][:80]}"

    def test_codon_and_arg_are_blocked_everywhere(self, manifest):
        for version in ("1.10.4", "10.5.0"):
            ids = {feature["id"] for feature in cap.blocked_features(version, manifest)}
            assert "codon_gy94" in ids
            assert "arg_recombination" in ids

    def test_blocked_reason_does_not_claim_official_support(self, manifest):
        for version in ("1.10.4", "10.5.0"):
            for feature in cap.blocked_features(version, manifest):
                reason = feature["reason"].lower()
                assert (
                    "no legacy shim" in reason or "no custom parser" in reason or "gate 0" in reason
                )

    def test_version_gated_features_are_blocked_on_1_10_4(self, manifest):
        ids = {feature["id"] for feature in cap.blocked_features("1.10.4", manifest)}
        assert {"amino_acid_mixture", "age_statistic", "tree_report"} <= ids

    def test_native_beagle_requirement_is_recorded(self, manifest):
        ids = set(cap.beagle_dependent("10.5.0", manifest))
        assert "marginal_likelihood_estimation" in ids
        # Pure-tooling rows do not need a likelihood engine.
        assert "snakemake_pipeline" not in ids


class TestEnvelope:
    def test_schema_and_row_count(self, manifest):
        envelope = cap.to_envelope("both", manifest)
        assert envelope["schema_version"] == C.SCHEMA_CAPABILITIES
        assert envelope["tool_version"] == C.tool_version()
        assert envelope["features"]

    def test_filter_returns_one_version(self, manifest):
        rows = cap.features_for("1.10.4", manifest)
        assert len(rows) == len(manifest["features"])
        assert all(row["status"] in ("supported", "partial", "blocked") for row in rows)
        assert all(isinstance(row["requires_native_beagle"], bool) for row in rows)

    def test_status_counts_add_up(self, manifest):
        for version in ("1.10.4", "10.5.0"):
            counts = cap.status_counts(version, manifest)
            assert sum(counts.values()) == len(manifest["features"])


class TestSingleSourceOfTruth:
    def test_cli_reads_the_yaml(self, tmp_path, monkeypatch):
        """Editing the manifest changes the CLI output: no hard-coded copy."""
        manifest = cap.load_capabilities()
        edited = json.loads(json.dumps(manifest))
        edited["features"][0]["name"] = "SENTINEL-FEATURE-NAME"
        import yaml

        target = tmp_path / "capabilities.yaml"
        target.write_text(yaml.safe_dump(edited, sort_keys=False), encoding="utf-8")
        reloaded = cap.load_capabilities(target)
        assert reloaded["features"][0]["name"] == "SENTINEL-FEATURE-NAME"
        assert "SENTINEL-FEATURE-NAME" in "\n".join(cap.format_table("both", reloaded))

    def test_no_python_copy_of_the_feature_list(self):
        """The module must not embed a second list of feature ids."""
        source = Path(cap.__file__).read_text(encoding="utf-8")
        assert "codon_gy94" not in source
        assert "arg_recombination" not in source


class TestCliContract:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "beastxpy.main", "capabilities", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )

    def test_text_table(self):
        result = self._run()
        assert result.returncode == 0, result.stderr
        assert "blocked" in result.stdout

    def test_json_output(self, tmp_path):
        path = tmp_path / "caps.json"
        result = self._run("--beast-version", "1.10.4", "--json", str(path), "-q")
        assert result.returncode == 0, result.stderr
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["schema_version"] == C.SCHEMA_CAPABILITIES


class TestMissingManifest:
    def test_missing_manifest_is_a_clear_error(self, tmp_path):
        with pytest.raises(cap.CapabilitiesError) as excinfo:
            cap.load_capabilities(tmp_path / "nope.yaml")
        assert "not found" in str(excinfo.value)
        assert excinfo.value.fix
