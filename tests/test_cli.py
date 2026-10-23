"""CLI smoke tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_cli(args, timeout=30):
    """Run the beastxpy CLI with the environment's python."""
    python = sys.executable
    return subprocess.run(
        [python, "-m", "beastxpy.main", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(ROOT),
    )


class TestCLI:
    """CLI entry points."""

    def test_help(self):
        result = _run_cli(["--help"])
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "usage" in result.stderr.lower()

    def test_version(self):
        result = _run_cli(["--version"])
        assert result.returncode == 0
        assert "0.1" in result.stdout

    def test_list_models(self):
        result = _run_cli(["list-models"])
        assert result.returncode == 0
        assert "substitution" in result.stdout.lower() or "substitution" in result.stderr.lower()

    def test_check_environment(self, monkeypatch, capsys):
        # The exit-code contract must not depend on the reviewer's machine.
        from argparse import Namespace

        from beastxpy import main

        report = {
            "python": "3.11",
            "beastxpy_version": "0.1.0",
            "java": "java",
            "java_version": "17",
            "java_source": "JAVA_HOME",
            "jars": {
                "1.10.4": {"path": "beast.jar", "sha256": "0" * 64},
                "10.5.0": {"path": "beast.jar", "sha256": "0" * 64},
            },
            "missing": [],
            "ok": True,
        }
        monkeypatch.setattr(main.BeastXPy, "check_environment", lambda self: report)
        assert main.cmd_check_environment(Namespace()) == 0
        output = capsys.readouterr().out
        assert "java: java" in output
        assert "jar beast-1.10.4" in output
