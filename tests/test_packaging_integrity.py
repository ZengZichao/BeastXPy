"""Release integrity tests: jar presence, package data, CLI availability."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestReleaseIntegrity:
    """Verify the packaged release includes all required files."""

    def test_core_wheel_variant_is_jar_free(self, tmp_path):
        """The optional beastxpy-core distribution removes both jars and LGPL."""
        import importlib.util

        script = ROOT / "tools" / "build_core_wheel.py"
        spec = importlib.util.spec_from_file_location("build_core_wheel", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        stage = module.stage_tree(tmp_path / "core")
        jars = list((stage / "beastxpy" / "tools" / "jars").glob("*.jar"))
        assert jars == []
        pyproject = (stage / "pyproject.toml").read_text(encoding="utf-8")
        assert 'name = "beastxpy-core"' in pyproject
        assert 'license = { text = "MIT" }' in pyproject
        assert "MIT-only" in pyproject
        assert '"tools/jars/*.jar"' not in pyproject

    def test_jar_present_1_10_4(self):
        jar = ROOT / "beastxpy" / "tools" / "jars" / "beast-1.10.4.jar"
        assert jar.exists(), "beast-1.10.4.jar missing"
        assert jar.stat().st_size > 100_000, "beast-1.10.4.jar suspiciously small"

    def test_jar_present_10_5_0(self):
        jar = ROOT / "beastxpy" / "tools" / "jars" / "beast-10.5.0.jar"
        assert jar.exists(), "beast-10.5.0.jar missing"
        assert jar.stat().st_size > 100_000, "beast-10.5.0.jar suspiciously small"

    def test_java_validator_source(self):
        java = ROOT / "beastxpy" / "tools" / "BeastXValidator.java"
        assert java.exists()
        content = java.read_text(encoding="utf-8")
        assert "class BeastXValidator" in content

    def test_xml_spec_json(self):
        spec = ROOT / "beastxpy" / "data" / "xml_spec.json"
        assert spec.exists()
        import json

        data = json.loads(spec.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_pyproject_exists(self):
        pyproject = ROOT / "pyproject.toml"
        assert pyproject.exists()
        content = pyproject.read_text(encoding="utf-8")
        assert "[project]" in content

    def test_package_init(self):
        init = ROOT / "beastxpy" / "__init__.py"
        assert init.exists()
        content = init.read_text(encoding="utf-8")
        assert "__version__" in content

    def test_cli_entry_point(self):
        main = ROOT / "beastxpy" / "main.py"
        assert main.exists()
        content = main.read_text(encoding="utf-8")
        # Should have a main function or entry point
        assert "def main" in content or "__main__" in content

    def test_versioned_validator_classes_present(self):
        """Both BEAST versions must have their own compiled validator."""
        for version in ("1.10.4", "10.5.0"):
            class_dir = ROOT / "beastxpy" / "tools" / "classes" / version
            assert (
                class_dir / "BeastXValidator.class"
            ).is_file(), f"BeastXValidator.class missing for BEAST {version}; run build_jars.py"
            assert (
                class_dir / "bxpshim" / "BxpShimTreeLikelihoodParser.class"
            ).is_file(), f"bxpshim parser missing for BEAST {version}; run build_jars.py"

    def test_windows_validation_wrapper_present(self):
        wrapper = ROOT / "beastxpy" / "tools" / "beastx_validate.cmd"
        assert wrapper.exists()
        content = wrapper.read_text(encoding="utf-8")
        assert "%JAVA_HOME%" in content
        assert ";" in content

    def test_example_configs_present(self):
        examples = ROOT / "examples"
        configs = list(examples.glob("config_*.yaml"))
        assert len(configs) >= 16

    def test_data_files_present(self):
        assert (ROOT / "examples" / "primates.fasta").exists()
        assert (ROOT / "examples" / "aminoacid.fasta").exists()


class TestEnvironment:
    """Environment checks."""

    def test_python_version(self):
        import sys

        # Keep this in step with pyproject.toml (requires-python = ">=3.10")
        # and the CI matrix (3.10 / 3.11).
        assert sys.version_info >= (3, 10)

    def test_java_detection_is_best_effort(self):
        """The runtime probe reports usability and a reason, never raises."""
        from beastxpy.validator import find_java_runtime

        runtime = find_java_runtime()
        assert isinstance(runtime.usable, bool)
        if not runtime.usable:
            assert runtime.reason


class TestBundledJarProvenance:
    """The shipped jars must be the official artifacts recorded in metadata."""

    def _sha256(self, path: Path) -> str:
        import hashlib

        digest = hashlib.sha256()
        # Built-in open keeps the two-argument form the UTF-8 meta-test checks.
        with open(str(path), "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def test_jars_match_xml_spec_fingerprints(self):
        import json

        spec = json.loads(
            (ROOT / "beastxpy" / "data" / "xml_spec.json").read_text(encoding="utf-8")
        )
        for version in ("1.10.4", "10.5.0"):
            jar = ROOT / "beastxpy" / "tools" / "jars" / f"beast-{version}.jar"
            recorded = spec["provenance"]["inputs"][version]["jar_sha256"]
            assert self._sha256(jar) == recorded, (
                f"bundled beast-{version}.jar does not match the SHA-256 in "
                "xml_spec.json; update both together"
            )
            assert spec["provenance"]["inputs"][version]["jar_source"] == "official-release"

    def test_manifest_records_official_hashes_and_no_personal_paths(self):
        manifest = (ROOT / "beastxpy" / "tools" / "jars" / "MANIFEST.txt").read_text(
            encoding="utf-8"
        )
        for version in ("1.10.4", "10.5.0"):
            jar = ROOT / "beastxpy" / "tools" / "jars" / f"beast-{version}.jar"
            assert self._sha256(jar) in manifest
            assert "official release artifacts" in manifest
        # Absolute Windows/POSIX user paths must never be committed here.
        assert "F:\\" not in manifest
        assert "/home/" not in manifest
        assert "C:\\Users" not in manifest
