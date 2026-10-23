"""Tests for the container definitions (``beastxpy.cli_container``).

The command's contract is narrow and worth pinning down: it prints the file
that ships in the repository, byte for byte, and it builds nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from beastxpy import _contracts as C
from beastxpy import cli_container as CC

ROOT = Path(__file__).resolve().parent.parent


class TestPrintMatchesFile:
    @pytest.mark.parametrize(
        "kind,filename", [("docker", "Dockerfile"), ("apptainer", "apptainer.def")]
    )
    def test_output_is_byte_identical(self, kind, filename):
        result = subprocess.run(
            [sys.executable, "-m", "beastxpy.main", "container", "--print", kind],
            capture_output=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        expected = (ROOT / "deploy" / filename).read_bytes()
        assert result.stdout == expected

    def test_definition_path_points_into_deploy(self):
        assert CC.definition_path("docker").name == "Dockerfile"
        assert CC.definition_path("apptainer").name == "apptainer.def"


class TestBadArguments:
    def test_unknown_kind_is_rejected(self):
        with pytest.raises(C.ContractError):
            CC.definition_path("podman")

    def test_missing_print_flag_exits_non_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "beastxpy.main", "container"],
            capture_output=True,
            timeout=120,
        )
        assert result.returncode != 0


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")


class TestImageProperties:
    """Static checks on the shipped Dockerfile, independent of a daemon."""

    def test_base_image_is_jdk17(self, dockerfile):
        assert "FROM eclipse-temurin:17-jdk" in dockerfile

    def test_entrypoint_is_beastxpy(self, dockerfile):
        assert 'ENTRYPOINT ["beastxpy"]' in dockerfile

    def test_runs_as_non_root(self, dockerfile):
        assert "USER beastxpy" in dockerfile
        assert "USER root" not in dockerfile

    def test_installs_native_beagle_and_checks_it(self, dockerfile):
        assert "libhmsbeagle.so" in dockerfile
        assert "BUILD_CUDA=OFF" in dockerfile

    def test_no_curl_pipe_to_shell(self, dockerfile):
        assert "curl | sh" not in dockerfile
        assert "curl | bash" not in dockerfile

    def test_committed_jars_are_used_not_downloaded(self, dockerfile):
        # The BEAST jars are package data, so Gate 3 works offline.
        assert "COPY beastxpy" in dockerfile
        assert "beast.jar" not in dockerfile or "jars" in dockerfile


class TestApptainerDefinition:
    def test_has_required_sections(self):
        text = (ROOT / "deploy" / "apptainer.def").read_text(encoding="utf-8")
        for section in ("Bootstrap:", "From:", "%post", "%runscript", "%environment"):
            assert section in text, section

    def test_runscript_invokes_beastxpy(self):
        text = (ROOT / "deploy" / "apptainer.def").read_text(encoding="utf-8")
        assert "exec beastxpy" in text


class TestDeployReadme:
    def test_documents_unverified_build_honestly(self):
        text = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8").lower()
        # Whatever the current status, the README must state it rather than
        # implying a build that was never run.
        assert "verified" in text or "not build" in text
