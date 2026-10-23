"""Release-quality regression tests.

These tests do not re-test model semantics; they pin the engineering contract:
UTF-8 test I/O, real goldens, CI matrix, repository hygiene, cross-platform
build/validator tooling, packaging metadata and honest documentation.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
OWNED_TESTS = [
    ROOT / "tests" / "test_packaging_integrity.py",
    ROOT / "tests" / "test_golden_generation.py",
    ROOT / "tests" / "test_cli.py",
    ROOT / "tests" / "test_api.py",
    ROOT / "tests" / "test_xml_writer.py",
    ROOT / "tests" / "test_release_contract.py",
]


# ---------------------------------------------------------------------------
# explicit UTF-8 for all test text I/O
# ---------------------------------------------------------------------------


def _literal_text_mode(node: ast.Call) -> bool:
    """Return True for open() calls that read/write text (not binary)."""
    if len(node.args) < 2:
        return True  # default mode is "r"
    mode = node.args[1]
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "b" not in mode.value
    return True


def _encoding_problems(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        has_encoding = any(keyword.arg == "encoding" for keyword in node.keywords)
        if name == "read_text" and not has_encoding:
            problems.append(f"{path.name}:{node.lineno}: read_text() without encoding")
        if name == "open" and not has_encoding and _literal_text_mode(node):
            problems.append(f"{path.name}:{node.lineno}: text open() without encoding")
        if name in {"run", "check_output", "Popen"}:
            text_mode = any(
                keyword.arg in {"text", "universal_newlines"}
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            )
            if text_mode and not has_encoding:
                problems.append(f"{path.name}:{node.lineno}: subprocess text mode without encoding")
    return problems


def test_owned_tests_use_explicit_utf8():
    problems: list[str] = []
    for path in OWNED_TESTS:
        assert path.is_file(), f"missing owned test: {path}"
        problems.extend(_encoding_problems(path))
    assert not problems, "\n".join(problems)


def test_release_integrity_reads_utf8():
    source = (ROOT / "tests" / "test_packaging_integrity.py").read_text(encoding="utf-8")
    assert 'read_text(encoding="utf-8")' in source
    assert "read_text()" not in source


# ---------------------------------------------------------------------------
# Gate 3 tests must fail (not skip) when the environment is missing
# ---------------------------------------------------------------------------


def test_gate3_tests_do_not_skip_environment_errors():
    source = (ROOT / "tests" / "test_beast_validation.py").read_text(encoding="utf-8")
    assert "pytest.skip" not in source
    assert "_beagle_limitation" not in source
    assert "pytest.fail" in source
    assert "java_core=True" in source


# ---------------------------------------------------------------------------
# golden fixtures exist for both versions and cover the required configs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["1.10.4", "10.5.0"])
def test_golden_fixtures_complete(version):
    directory = ROOT / "tests" / "golden" / version
    assert directory.is_dir(), f"missing golden directory {directory}"
    goldens = {path.stem: path for path in directory.glob("*.xml")}
    assert len(goldens) == 16, f"expected 16 goldens in {directory}, got {len(goldens)}"
    # The representative example configs must be present.
    required = {
        "config_basic",
        "config_subst_gtr",
        "config_rlc",
        "config_skyline",
        "config_ebsp",
        "config_multi_partition",
        "config_tip_dates",
    }
    assert required <= set(goldens)
    for golden in goldens.values():
        assert golden.stat().st_size > 1000


# ---------------------------------------------------------------------------
# CI matrix and pinned test requirements
# ---------------------------------------------------------------------------


def test_ci_workflow_matrix_and_steps():
    workflow_path = ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow_path.is_file()
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    job = workflow["jobs"]["test"]
    matrix = job["strategy"]["matrix"]
    assert matrix["os"] == ["ubuntu-latest", "windows-latest", "macos-latest"]
    assert matrix["python"] == ["3.10", "3.11"]
    assert matrix["beast"] == ["1.10.4", "10.5.0"]

    steps_text = "\n".join(json.dumps(step, ensure_ascii=False) for step in job["steps"])
    assert "pip install -e ." in steps_text
    assert "requirements-test.txt" in steps_text
    assert "pytest" in steps_text
    assert "tools/official_validate.py" in steps_text
    assert "--mode java-core" in steps_text
    assert "actions/upload-artifact" in steps_text


def test_requirements_test_use_compatible_ranges():
    """Review M-minor 9: hard equality pins conflict with supported indexes."""
    requirements = (ROOT / "requirements-test.txt").read_text(encoding="utf-8")
    for package in ("pytest", "pytest-cov", "hypothesis", "flake8", "black", "mypy"):
        assert f"{package}>=" in requirements
        assert f"{package}==" not in requirements


# ---------------------------------------------------------------------------
# documentation claims and enum aliases
# ---------------------------------------------------------------------------


def test_readme_claims_are_verified():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # The README points at the commands that back its claims.
    assert "python -m pytest" in readme
    assert "official_validate.py" in readme
    assert "validate_coverage.py" in readme
    assert "native BEAGLE" in readme
    assert "does not assert convergence" in readme.lower()
    assert "skips gracefully" not in readme.lower()
    assert "dual support" not in readme.lower()
    # Both languages exist and are linked from each other.
    assert (ROOT / "README.zh-CN.md").is_file()
    assert "README.zh-CN.md" in readme


def test_configuration_docs_match_gate0_vocabulary():
    for name in ("docs/en/configuration.md", "docs/zh/configuration.md"):
        docs = (ROOT / name).read_text(encoding="utf-8")
        for token in (
            "metadata",
            "alignments",
            "partitions",
            "tree_prior",
            "calibrations",
            "parameter_priors",
            "mcmc",
            "loggers",
            "control",
            "tip_dates",
            "analysis",
            "custom_fragments",
        ):
            assert token in docs, f"{name} does not document {token!r}"

    from beastxpy.config import (
        CALIBRATION_METHOD_ALIASES,
        FREQUENCY_MODE_ALIASES,
        PROVENANCE_TYPE_ALIASES,
        TREE_PRIOR_ALIASES,
        subst_raw_cats,
    )

    assert CALIBRATION_METHOD_ALIASES["crown"] == "clade"
    assert CALIBRATION_METHOD_ALIASES["stem"] == "stem"
    assert FREQUENCY_MODE_ALIASES["equal"] == "uniform"
    assert PROVENANCE_TYPE_ALIASES["upper"] == "soft"
    # ConfigParser lower-cases the value first; the table stores "birthdeath".
    assert TREE_PRIOR_ALIASES["birthdeath"] == "birth_death"
    assert subst_raw_cats(0, "test", []) == 1


def test_validation_docs_describe_gate3_modes():
    docs = (ROOT / "docs" / "en" / "validation.md").read_text(encoding="utf-8")
    lowered = docs.lower()
    assert "java-core" in docs
    assert "beagle" in docs
    assert "initial posterior" in lowered
    # The substituted parser must be described honestly, not hidden.
    assert "harness" in lowered or "not beast functionality" in lowered
    assert "allow-unvalidated" in lowered


# ---------------------------------------------------------------------------
# repository hygiene
# ---------------------------------------------------------------------------


def test_root_generated_artifacts_are_absent():
    for name in (
        "basic.log",
        "basic.trees",
        "calibrated_yule.log",
        "calibrated_yule.trees",
        "subst_jc69.log",
        "subst_jc69.trees",
        "testCalibration.log",
        ".DS_Store",
        ".tmp_cli_test",
        "diagnostics",
    ):
        assert not (ROOT / name).exists(), f"generated artifact at repository root: {name}"


def test_gitignore_covers_generated_files():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        "*.log",
        "*.trees",
        ".DS_Store",
        ".pytest_cache/",
        "build/",
        "dist/",
        "validation_runs/",
        ".coverage",
        "htmlcov/",
    ):
        assert pattern in ignored, f".gitignore missing {pattern!r}"


# ---------------------------------------------------------------------------
# cross-platform build_jars.py
# ---------------------------------------------------------------------------


def test_build_jars_timestamp_and_jdk_detection(monkeypatch):
    sys.path.insert(0, str(ROOT / "beastxpy" / "tools"))
    import build_jars

    assert "per-version" in (build_jars.__doc__ or "")
    assert "pdf? no" not in (build_jars.__doc__ or "")
    timestamp = build_jars.utc_timestamp()
    import re

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp)

    fake_home = Path(sys.executable).parent
    monkeypatch.setenv("JAVA_HOME", str(fake_home))
    monkeypatch.setattr(build_jars.shutil, "which", lambda name: None)
    # find_java_home() must only accept a directory that actually has javac.
    expected = (fake_home / "bin" / "javac.exe").is_file() or (
        fake_home / "bin" / "javac"
    ).is_file()
    detected = build_jars.find_java_home()
    if expected:
        assert detected == fake_home
    else:
        assert detected is None


def test_build_jars_compiles_both_versions_without_touching_package(tmp_path):
    if shutil.which("javac") is None:
        pytest.skip("no javac on PATH; set JAVA_HOME to a JDK to test build_jars.py")

    package_classes = ROOT / "beastxpy" / "tools" / "classes"
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in package_classes.rglob("*.class")
    }
    script = ROOT / "beastxpy" / "tools" / "build_jars.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--skip-ant",
            "--classes-dir",
            str(tmp_path / "classes"),
            "--manifest",
            str(tmp_path / "MANIFEST.txt"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    for version in ("1.10.4", "10.5.0"):
        assert (tmp_path / "classes" / version / "BeastXValidator.class").is_file()
        assert (
            tmp_path / "classes" / version / "bxpshim" / "BxpShimTreeLikelihoodParser.class"
        ).is_file()
    manifest = (tmp_path / "MANIFEST.txt").read_text(encoding="utf-8")
    assert "built: " in manifest
    # Packaged jars are official releases; a locally copied asset would be
    # recorded as existing-jar.  Either is a valid --skip-ant source.
    assert "source=official-release" in manifest or "source=existing-jar" in manifest
    assert "sha256=" in manifest
    assert "out=beastxpy/tools/classes/" in manifest
    assert "BeastXValidator+shim" in manifest

    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in package_classes.rglob("*.class")
    }
    assert after == before, "build_jars.py modified the packaged class directories"


# ---------------------------------------------------------------------------
# Windows wrapper
# ---------------------------------------------------------------------------


def test_windows_validator_wrapper_uses_pathsep_and_java_home():
    wrapper = (ROOT / "beastxpy" / "tools" / "beastx_validate.cmd").read_text(encoding="utf-8")
    assert "%JAVA_HOME%" in wrapper
    assert "CLASSPATH=!CLASSPATH!;" in wrapper
    assert "-Dfile.encoding=UTF-8" in wrapper
    assert "classes\\!BEAST_VERSION!" in wrapper


def test_official_validate_python_entry_point_is_cross_platform():
    """The Python entry point must run without a POSIX shell."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "official_validate.py"), "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "--mode" in proc.stdout
    assert "java-core" in proc.stdout


# ---------------------------------------------------------------------------
# packaging manifest
# ---------------------------------------------------------------------------


def test_pyproject_packages_versioned_classes_and_jars():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "tools/jars/*.jar" in pyproject
    assert "tools/classes/*/*.class" in pyproject
    assert "tools/classes/*/bxpshim/*.class" in pyproject
    assert "tools/beastx_validate.cmd" in pyproject
    assert "tools/NOTICE" not in pyproject
    assert (ROOT / "beastxpy" / "tools" / "jars" / "beast-1.10.4.jar").is_file()
    assert (ROOT / "beastxpy" / "tools" / "jars" / "beast-10.5.0.jar").is_file()


# ---------------------------------------------------------------------------
# check-environment exit status
# ---------------------------------------------------------------------------


def _env_report(ok: bool) -> dict:
    return {
        "python": "3.11",
        "beastxpy_version": "0.1.0",
        "java": "java" if ok else None,
        "java_version": "17" if ok else None,
        "java_source": "JAVA_HOME" if ok else None,
        "jars": {
            "1.10.4": {"path": "beast.jar", "sha256": "0" * 64} if ok else None,
            "10.5.0": {"path": "beast.jar", "sha256": "0" * 64} if ok else None,
        },
        "missing": [] if ok else ["java", "beast-1.10.4.jar", "beast-10.5.0.jar"],
        "ok": ok,
    }


def test_check_environment_exit_codes(monkeypatch):
    from argparse import Namespace

    from beastxpy import main

    monkeypatch.setattr(main.BeastXPy, "check_environment", lambda self: _env_report(True))
    assert main.cmd_check_environment(Namespace()) == 0

    monkeypatch.setattr(main.BeastXPy, "check_environment", lambda self: _env_report(False))
    assert main.cmd_check_environment(Namespace()) == 2


def test_ci_runs_replayable_beagle_evidence_and_spec_drift_guard():
    """Continuous evidence against silent drift in parser and trace numerics."""
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    assert "beagle-evidence" in jobs
    beagle_text = "\n".join(
        json.dumps(step, ensure_ascii=False) for step in jobs["beagle-evidence"]["steps"]
    )
    assert "tools/run_beagle_evidence.py" in beagle_text
    assert "actions/upload-artifact" in beagle_text

    assert "spec-drift" in jobs
    spec_text = "\n".join(
        json.dumps(step, ensure_ascii=False) for step in jobs["spec-drift"]["steps"]
    )
    assert "tools/fetch_beast.py" in spec_text
    assert "tools/patch_xml_spec.py --check" in spec_text

    coverage_text = "\n".join(
        json.dumps(step, ensure_ascii=False) for step in jobs["coverage"]["steps"]
    )
    assert "--cov-fail-under=70" in coverage_text
    assert "not java" not in coverage_text
    assert "actions/setup-java" in coverage_text


def test_citation_and_project_urls_are_not_placeholders():
    """The citation record names the real author; no invented URL or DOI."""
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    text = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    from beastxpy import __version__ as pkg_version

    assert citation["cff-version"] == "1.2.0"
    assert citation["version"] == pkg_version
    assert "beastxpy/beastxpy" not in text
    assert "doi" not in citation
    authors = citation["authors"]
    assert any(
        a.get("family-names") == "Zeng"
        and a.get("given-names") == "Zichao"
        and a.get("email") == "zengzichao@sjtu.edu.cn"
        and a.get("orcid") == "https://orcid.org/0000-0001-6553-970X"
        for a in authors
    )
    assert citation["license"] == "MIT AND LGPL-2.1-only"
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "beastxpy/beastxpy" not in pyproject
    assert "zengzichao@sjtu.edu.cn" in pyproject


def test_version_is_consistent_across_metadata():
    """pyproject, the package __init__ and CITATION.cff agree on the version."""
    import re

    from beastxpy import __version__ as pkg_version

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "(\d+\.\d+\.\d+)"', pyproject, re.M)
    assert match is not None, "pyproject.toml must declare a plain version"
    assert match.group(1) == pkg_version
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    assert citation["version"] == pkg_version
