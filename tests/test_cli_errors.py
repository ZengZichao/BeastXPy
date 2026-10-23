"""Regression tests for the CLI/API/serialization/runner layers.

Covers CLI error paths, gate states, and runner/reproducibility
behaviour.

No test requires a real BEAST installation: Java/jar discovery and the BEAST
subprocess are mocked where needed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import beastxpy.reproducibility as repro
from beastxpy import runner as runner_mod
from beastxpy.api import BeastXPy, GateError
from beastxpy.config import ConfigParser
from beastxpy.main import _parser, main
from beastxpy.reproducibility import (
    analysis_fingerprint,
    content_fingerprint,
    generate_methods_latex,
    generate_nextflow,
    generate_snakemake,
    validate_pipeline_text,
)
from beastxpy.runner import BeastRunner, RunError
from beastxpy.xml_writer import (
    XmlWriter,
    XMLWriterError,
    sanitize_xml_comment,
    strip_xml_comments,
    write_xml_file,
)

BASIC = "examples/config_basic.yaml"
# A config whose generated XML currently passes the (independently owned)
# Gate 1; used where a test needs a real generated document.
JC69 = "examples/config_subst_jc69.yaml"


def _parse_basic():
    return ConfigParser().parse(BASIC)


def _parse_jc69():
    return ConfigParser().parse(JC69)


def _runner_config(tmp_path: Path, name: str = "runtest") -> Path:
    """Copy the JC69 example into ``tmp_path`` with a fixed RNG seed."""
    shutil.copy("examples/primates.fasta", tmp_path / "primates.fasta")
    text = Path(JC69).read_text(encoding="utf-8")
    text = text.replace("analysis_name: subst_jc69", f"analysis_name: {name}")
    text = text.replace("  beast_version: 1.10.4", "  beast_version: 1.10.4\n  seed: 12345")
    path = tmp_path / "runtest.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _fake_java(monkeypatch, module):
    monkeypatch.setattr(
        module, "probe_java", lambda: {"path": "java", "version": "17", "source": "test"}
    )


def _fake_jar(monkeypatch, module):
    monkeypatch.setattr(
        module,
        "resolve_jar_details",
        lambda version: {"path": "beast.jar", "source": "test", "sha256": "a" * 64},
    )


def _validation_result(**overrides):
    payload = {
        "ok": False,
        "version": "1.10.4",
        "exit_code": 1,
        "stdout": "",
        "stderr": "BEAST parse error: boom",
        "findings": [],
        "command": ["java", "-cp", "beast.jar", "BeastXValidator", "x.xml"],
        "mode": "java-core",
        "tree_likelihood_rule_source": "shim",
        "beagle": None,
        "beagle_version": None,
        "executed": True,
        "skipped": False,
    }
    payload.update(overrides)
    return types.SimpleNamespace(**payload)


def _write_trace(path: Path) -> Path:
    lines = ["# BEAST v1.10.4", "# Sample\tposterior\tlikelihood\tprior\trootHeight"]
    for index in range(60):
        lines.append(
            f"{index * 1000}\t{-100.0 + index * 0.01}\t{-110.0 - index * 0.01}\t"
            f"{10.0 + index * 0.01}\t{2.0 + (index % 7) * 0.01}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# generate without --output writes XML to stdout, logs to stderr
# ---------------------------------------------------------------------------


class TestGenerateStdout:
    def test_xml_on_stdout_and_logs_on_stderr(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        code = main(["generate", "--config", JC69])
        captured = capsys.readouterr()
        assert code == 0
        assert captured.out.startswith("<?xml")
        assert "<beast>" in captured.out
        assert "wrote <stdout>" not in captured.out
        assert "Gate 1/3" in captured.err
        ET.fromstring(captured.out)  # complete, well-formed document

    def test_both_without_output_is_a_clear_error(self, capsys, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        code = main(["generate", "--config", JC69, "--beast-version", "both"])
        captured = capsys.readouterr()
        assert code == 1
        assert captured.out == ""
        assert "requires --output" in captured.err


# ---------------------------------------------------------------------------
# beast_version=both -> one distinguishable file per version
# ---------------------------------------------------------------------------


class TestBothVersions:
    def test_cli_writes_one_file_per_version(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        out = tmp_path / "out.xml"
        code = main(
            [
                "generate",
                "--config",
                JC69,
                "--output",
                str(out),
                "--beast-version",
                "both",
                "--force",
            ]
        )
        captured = capsys.readouterr()
        assert code == 0, captured.err
        f104 = tmp_path / "out_1.10.4.xml"
        f105 = tmp_path / "out_10.5.0.xml"
        assert f104.exists() and f105.exists()
        assert "BXP-beast-version: 1.10.4" in f104.read_text(encoding="utf-8")
        assert "BXP-beast-version: 10.5.0" in f105.read_text(encoding="utf-8")
        # Same model content; only version metadata/validation differ.
        f104_text = f104.read_text(encoding="utf-8")
        f105_text = f105.read_text(encoding="utf-8")
        assert ET.tostring(ET.fromstring(f104_text)) == ET.tostring(ET.fromstring(f105_text))
        assert strip_xml_comments(f104_text) == strip_xml_comments(f105_text)
        assert f104_text != f105_text
        for version in ("1.10.4", "10.5.0"):
            sidecar = tmp_path / f"out_{version}.fingerprint.json"
            assert sidecar.exists()
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            assert data["beast_version"] == version

    def test_api_result_exposes_both_documents(self, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        result = BeastXPy(force=True).generate_xml_result(JC69, beast_version="both")
        assert [doc.version for doc in result.documents] == ["1.10.4", "10.5.0"]
        assert result.documents[0].fingerprint != result.documents[1].fingerprint
        assert result.validation_status == "not-requested"
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Defaults are deliberately generous
# ---------------------------------------------------------------------------


def test_cli_defaults_are_sized_for_real_analyses():
    parser = _parser()
    run_args = parser.parse_args(["run", "--config", "config.yaml"])
    assert run_args.timeout == 86400, "run default must be 24 h, not 1 h"
    quick_args = parser.parse_args(["quick", "--alignment", "a.fasta"])
    assert quick_args.chain_length == 1_000_000, "quick must not default to 10M steps"


# ---------------------------------------------------------------------------
# three-state Gate 3, no fake OK, distinguishing exit codes
# ---------------------------------------------------------------------------


class TestGate3ThreeState:
    def test_construct_only_mode_is_named_in_ok_output(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        import beastxpy.api as api_mod

        _fake_java(monkeypatch, api_mod)
        _fake_jar(monkeypatch, api_mod)
        monkeypatch.setattr(
            api_mod,
            "beast_validate_xml",
            lambda *a, **k: _validation_result(ok=True, exit_code=0, stderr="", mode="java-core"),
        )
        out = tmp_path / "validated.xml"
        code = main(
            ["generate", "--config", JC69, "--output", str(out), "--beast-validate", "--force"]
        )
        captured = capsys.readouterr()
        assert code == 0
        assert "validation: OK" in captured.err
        assert "mode=java-core" in captured.err
        assert "likelihood NOT evaluated" in captured.err
        assert "WARNING: this was NOT a full official validation" in captured.err

    def test_beagle_mode_is_named_and_has_no_construct_only_warning(
        self, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        import beastxpy.api as api_mod

        _fake_java(monkeypatch, api_mod)
        _fake_jar(monkeypatch, api_mod)
        monkeypatch.setattr(
            api_mod,
            "beast_validate_xml",
            lambda *a, **k: _validation_result(
                ok=True, exit_code=0, stderr="", mode="beagle", beagle_version="4.0.1"
            ),
        )
        out = tmp_path / "beagle.xml"
        code = main(
            ["generate", "--config", JC69, "--output", str(out), "--beast-validate", "--force"]
        )
        captured = capsys.readouterr()
        assert code == 0
        assert "mode=beagle" in captured.err
        assert "native hmsbeagle likelihood" in captured.err
        assert "WARNING: this was NOT a full official validation" not in captured.err

    def test_sidecar_records_mode_and_rule_source(self, tmp_path, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        import beastxpy.api as api_mod

        _fake_java(monkeypatch, api_mod)
        _fake_jar(monkeypatch, api_mod)
        monkeypatch.setattr(
            api_mod,
            "beast_validate_xml",
            lambda *a, **k: _validation_result(ok=True, exit_code=0, stderr="", mode="beagle"),
        )
        out = tmp_path / "sidecar.xml"
        code = main(
            ["generate", "--config", JC69, "--output", str(out), "--beast-validate", "--force"]
        )
        assert code == 0
        sidecar = json.loads(
            out.with_name(out.stem + ".fingerprint.json").read_text(encoding="utf-8")
        )
        assert sidecar["validation"]["mode"] == "beagle"
        assert sidecar["validation"]["tree_likelihood_rule_source"] == "shim"

    def test_allow_unvalidated_never_prints_ok(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        monkeypatch.setattr("beastxpy.api.probe_java", lambda: None)
        out = tmp_path / "allow.xml"
        code = main(
            [
                "generate",
                "--config",
                JC69,
                "--output",
                str(out),
                "--beast-validate",
                "--allow-unvalidated",
                "--force",
            ]
        )
        captured = capsys.readouterr()
        assert code == 2
        assert "UNVALIDATED" in captured.err
        assert "validation: OK" not in captured.err
        assert out.exists()  # allow_unvalidated lets generation complete
        methods = out.with_name(out.stem + "_methods.tex").read_text(encoding="utf-8")
        assert "parser validation was not performed" in methods
        assert "parser instantiation" not in methods

    def test_validation_failure_reports_reason_and_exit_3(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        import beastxpy.api as api_mod

        _fake_java(monkeypatch, api_mod)
        _fake_jar(monkeypatch, api_mod)
        monkeypatch.setattr(api_mod, "beast_validate_xml", lambda *a, **k: _validation_result())
        out = tmp_path / "failed.xml"
        with pytest.warns(RuntimeWarning, match="boom"):
            code = main(
                [
                    "generate",
                    "--config",
                    JC69,
                    "--output",
                    str(out),
                    "--beast-validate",
                    "--allow-unvalidated",
                    "--force",
                ]
            )
        captured = capsys.readouterr()
        assert code == 3
        assert "UNVALIDATED" in captured.err
        assert "boom" in captured.err  # real error text, not a literal {...}
        assert "{" not in captured.err
        assert out.exists()
        sidecar = json.loads(
            out.with_name(out.stem + ".fingerprint.json").read_text(encoding="utf-8")
        )
        assert sidecar["validation"]["status"] == "failed"

    def test_validation_failure_without_allow_writes_nothing(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        import beastxpy.api as api_mod

        _fake_java(monkeypatch, api_mod)
        _fake_jar(monkeypatch, api_mod)
        monkeypatch.setattr(api_mod, "beast_validate_xml", lambda *a, **k: _validation_result())
        out = tmp_path / "failed.xml"
        code = main(
            ["generate", "--config", JC69, "--output", str(out), "--beast-validate", "--force"]
        )
        captured = capsys.readouterr()
        assert code == 3
        assert not out.exists()
        assert "BEAST parse error" in captured.err
        assert "exit code: 1" in captured.err
        assert "command: java" in captured.err

    def test_all_versions_passing_prints_ok(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        import beastxpy.api as api_mod

        _fake_java(monkeypatch, api_mod)
        _fake_jar(monkeypatch, api_mod)
        monkeypatch.setattr(
            api_mod,
            "beast_validate_xml",
            lambda *a, **k: _validation_result(ok=True, exit_code=0, stderr=""),
        )
        out = tmp_path / "good.xml"
        code = main(
            [
                "generate",
                "--config",
                JC69,
                "--output",
                str(out),
                "--beast-validate",
                "--beast-version",
                "both",
                "--force",
            ]
        )
        captured = capsys.readouterr()
        assert code == 0
        assert "BEAST validation: OK" in captured.err
        assert "UNVALIDATED" not in captured.err
        methods = (tmp_path / "good_1.10.4_methods.tex").read_text(encoding="utf-8")
        assert "parser instantiation" in methods

    def test_gate3_result_is_surfaced_by_api(self, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        import beastxpy.api as api_mod

        _fake_java(monkeypatch, api_mod)
        _fake_jar(monkeypatch, api_mod)
        monkeypatch.setattr(
            api_mod,
            "beast_validate_xml",
            lambda *a, **k: _validation_result(ok=True, exit_code=0, stderr=""),
        )
        result = BeastXPy().generate_xml_result(JC69, beast_validate=True)
        assert result.documents[0].gate3.status == "passed"
        assert result.validation_status == "passed"
        assert result.exit_code == 0

    def test_validate_cli_prints_per_version_details(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        import beastxpy.api as api_mod

        xml_path = tmp_path / "doc.xml"
        BeastXPy(force=True).generate_xml(JC69, output=str(xml_path))
        _fake_java(monkeypatch, api_mod)
        _fake_jar(monkeypatch, api_mod)
        monkeypatch.setattr(api_mod, "beast_validate_xml", lambda *a, **k: _validation_result())
        assert main(["validate", "--xml", str(xml_path), "--beast"]) == 3
        err = capsys.readouterr().err
        assert "stderr: BEAST parse error: boom" in err
        assert "exit code: 1" in err
        assert "command: java" in err
        assert "UNVALIDATED" not in err  # only with --allow-unvalidated

    def test_validate_cli_unavailable_environment(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        import beastxpy.api as api_mod

        xml_path = tmp_path / "doc.xml"
        BeastXPy(force=True).generate_xml(JC69, output=str(xml_path))
        monkeypatch.setattr(api_mod, "probe_java", lambda: None)
        code = main(["validate", "--xml", str(xml_path), "--beast", "--allow-unvalidated"])
        err = capsys.readouterr().err
        assert code == 2
        assert "NOT RUN" in err
        assert "UNVALIDATED" in err
        assert "remediation:" in err
        assert "validation: OK" not in err

    def test_validate_xml_gate3_failure_is_gate_3_not_gate_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        import beastxpy.api as api_mod

        xml_path = tmp_path / "doc.xml"
        BeastXPy(force=True).generate_xml(JC69, output=str(xml_path))
        _fake_java(monkeypatch, api_mod)
        _fake_jar(monkeypatch, api_mod)
        monkeypatch.setattr(api_mod, "beast_validate_xml", lambda *a, **k: _validation_result())
        with pytest.raises(GateError) as excinfo:
            BeastXPy().validate_xml(str(xml_path), beast=True)
        assert excinfo.value.gate_name == "3"
        assert "BEAST parse error" in str(excinfo.value)
        # Gate-1 warnings are not attached to the Gate-3 error.
        assert excinfo.value.findings
        assert all(isinstance(item, dict) and "status" in item for item in excinfo.value.findings)

        allowed = BeastXPy().validate_xml(str(xml_path), beast=True, allow_unvalidated=True)
        assert allowed["ok"] is False
        assert allowed["gate3"][0]["status"] == "failed"


# ---------------------------------------------------------------------------
# check-environment exit codes
# ---------------------------------------------------------------------------


class TestCheckEnvironment:
    def _report(self, ok: bool) -> dict:
        jar = {"path": "beast.jar", "sha256": "b" * 64, "source": "test"} if ok else None
        return {
            "python": "3.10.2",
            "beastxpy_version": "0.1.0",
            "java": "java" if ok else None,
            "java_version": "17.0.2" if ok else None,
            "java_source": "JAVA_HOME" if ok else None,
            "jars": {"1.10.4": jar, "10.5.0": jar},
            "beagle": {
                "1.10.4": {"path": "/beagle31/lib", "source": "env"} if ok else None,
                "10.5.0": {"path": "/beagle40/lib", "source": "env"} if ok else None,
            },
            "beagle_guidance": (
                []
                if ok
                else [
                    "native BEAGLE for BEAST 1.10.4 not found: install BEAGLE 3.1.x, "
                    "then set BEASTX_BEAGLE_1_10_4_LIB to the directory that contains "
                    "libhmsbeagle-jni.so"
                ]
            ),
            "missing": [] if ok else ["java (set JAVA_HOME or BEASTX_JAVA)"],
            "ok": ok,
        }

    def test_missing_java_exits_2(self, capsys, monkeypatch):
        monkeypatch.setattr(
            BeastXPy, "check_environment", lambda self: TestCheckEnvironment._report(self, False)
        )
        assert main(["check-environment"]) == 2
        out = capsys.readouterr().out
        assert "NOT FOUND" in out

    def test_usable_environment_exits_0_and_hashes(self, capsys, monkeypatch):
        monkeypatch.setattr(
            BeastXPy, "check_environment", lambda self: TestCheckEnvironment._report(self, True)
        )
        assert main(["check-environment"]) == 0
        out = capsys.readouterr().out
        assert "version 17.0.2" in out
        assert "sha256:" + "b" * 64 in out
        assert "beagle 1.10.4: /beagle31/lib" in out

    def test_missing_beagle_guidance_is_printed(self, capsys, monkeypatch):
        monkeypatch.setattr(
            BeastXPy, "check_environment", lambda self: TestCheckEnvironment._report(self, False)
        )
        assert main(["check-environment"]) == 2
        out = capsys.readouterr().out
        assert "beagle 1.10.4: NOT FOUND" in out
        assert "guidance:" in out
        assert "BEASTX_BEAGLE_1_10_4_LIB" in out


# ---------------------------------------------------------------------------
# trace command
# ---------------------------------------------------------------------------


class TestTraceCommand:
    def test_valid_trace(self, tmp_path, capsys):
        log = _write_trace(tmp_path / "run.log")
        assert main(["trace", "--log", str(log)]) == 0
        out = capsys.readouterr().out
        assert "ESS" in out or "rootHeight" in out

    def test_bad_input_is_a_clean_error(self, tmp_path, capsys):
        bad = tmp_path / "bad.log"
        bad.write_text("this is not a trace\n", encoding="utf-8")
        code = main(["trace", "--log", str(bad)])
        captured = capsys.readouterr()
        assert code == 1
        assert "trace failed" in captured.err
        assert "Traceback" not in captured.err

    def test_missing_file(self, tmp_path, capsys):
        code = main(["trace", "--log", str(tmp_path / "nope.log")])
        captured = capsys.readouterr()
        assert code == 1
        assert "trace failed" in captured.err


# ---------------------------------------------------------------------------
# XML escaping, comment safety, encodings
# ---------------------------------------------------------------------------


class TestXmlSafety:
    def test_text_and_comment_are_xml_safe(self):
        xml = XmlWriter().write(
            {
                "tag": "beast",
                "comment": "doi:10.1/x--y & <z>",
                "children": [{"tag": "newick", "text": "(A<1>&B:1);"}],
            }
        )
        ET.fromstring(xml)
        assert "&lt;1&gt;&amp;B" in xml
        for match in re_find_comments(xml):
            assert "--" not in match
            assert not match.endswith("-")

    def test_long_comments_are_truncated(self):
        body = sanitize_xml_comment("x" * 5000)
        assert all(len(line) <= 1000 for line in body.splitlines())
        assert body.endswith("...")

    def test_invalid_tag_and_attribute_rejected(self):
        with pytest.raises(XMLWriterError):
            XmlWriter().write({"tag": "beast", "children": [{"tag": "bad tag"}]})
        with pytest.raises(XMLWriterError):
            XmlWriter().write({"tag": "beast", "children": [{"tag": "ok", "attrib": {"a b": "1"}}]})

    def test_strip_xml_comments_contract(self):
        xml = (
            '<?xml version="1.0"?><beast><!-- header -->'
            "<x><![CDATA[a<!--b-->]]></x><!-- tail --></beast>"
        )
        stripped = strip_xml_comments(xml)
        assert "<!-- header -->" not in stripped
        assert "<!-- tail -->" not in stripped
        assert "<![CDATA[a<!--b-->]]>" in stripped

    def test_file_is_utf8_lf(self, tmp_path):
        target = tmp_path / "nested" / "out.xml"
        write_xml_file(str(target), '<?xml version="1.0"?>\n<beast>é</beast>\n')
        raw = target.read_bytes()
        assert "é".encode("utf-8") in raw
        assert b"\r\n" not in raw
        ET.parse(target)


def re_find_comments(xml: str):
    import re

    return [match.group(1) for match in re.finditer(r"<!--(.*?)-->", xml, re.S)]


# ---------------------------------------------------------------------------
# fingerprint includes seed/science, excludes filenames/metadata
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_seed_changes_fingerprint(self):
        config = _parse_basic()
        base = analysis_fingerprint(config)
        base_content = content_fingerprint(config)
        config.control.seed = 999
        assert analysis_fingerprint(config) != base
        assert content_fingerprint(config) != base_content

    def test_output_filenames_do_not_change_fingerprint(self):
        config = _parse_basic()
        base = analysis_fingerprint(config)
        config.loggers.trace_log["file_name"] = "renamed.log"
        config.loggers.tree_log["file_name"] = "renamed.trees"
        assert analysis_fingerprint(config) == base

    def test_non_science_metadata_does_not_change_fingerprint(self):
        config = _parse_basic()
        base = analysis_fingerprint(config)
        config.diagnostics["overlap_threshold"] = 0.9
        config.metadata["author"] = "someone else"
        config.metadata["date"] = "2020-01-01"
        config.metadata["tool_version"] = "9.9.9"
        assert analysis_fingerprint(config) == base

    def test_embed_fingerprint_is_scientific(self):
        config = _parse_basic()
        base = analysis_fingerprint(config)
        config.output.embed_fingerprint = False
        assert analysis_fingerprint(config) != base

    def test_content_fingerprint_survives_tool_upgrade(self, monkeypatch):
        config = _parse_basic()
        content = content_fingerprint(config)
        displayed = analysis_fingerprint(config)
        monkeypatch.setattr(repro, "__version__", "99.0.0")
        assert content_fingerprint(config) == content
        assert analysis_fingerprint(config) != displayed


# ---------------------------------------------------------------------------
# runner command construction, seeds, resume, timeout, gates
# ---------------------------------------------------------------------------


class TestRunnerCommand:
    def _install_fake(self, monkeypatch, returncode=0):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            prefix = cmd[cmd.index("-prefix") + 1]
            cwd = Path(kwargs["cwd"])
            (cwd / f"{prefix}trace.log").write_text("# trace\n", encoding="utf-8")
            (cwd / f"{prefix}tree.log").write_text("#NEXUS\n", encoding="utf-8")
            if "-save_stem" in cmd:
                (cwd / "checkpoint_1000").write_text("state", encoding="utf-8")
            return types.SimpleNamespace(returncode=returncode, stdout="ok", stderr="")

        monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
        _fake_java(monkeypatch, runner_mod)
        _fake_jar(monkeypatch, runner_mod)
        return calls

    def test_uses_control_seed_prefix_and_per_seed_workdir(self, tmp_path, monkeypatch):
        config_path = _runner_config(tmp_path)
        config = ConfigParser().parse(str(config_path))
        calls = self._install_fake(monkeypatch)
        summary = BeastRunner(str(config_path), workdir=str(tmp_path / "runs"), force=True).run()
        assert summary.seeds == [12345]
        command, kwargs = calls[0]
        assert "-working" not in command
        assert command[command.index("-seed") + 1] == "12345"
        assert command[command.index("-prefix") + 1] == "seed_12345_"
        assert Path(kwargs["cwd"]).name == "12345"
        assert Path(command[-1]).is_absolute()
        assert Path(command[-1]).name == "runtest.xml"
        assert (tmp_path / "runs" / "12345" / "runtest.xml").exists()
        assert summary.results[0].trace_log_path.endswith("seed_12345_trace.log")
        assert Path(summary.results[0].trace_log_path).exists()
        assert Path(summary.results[0].tree_log_path).exists()
        meta = json.loads(
            (tmp_path / "runs" / "12345" / "run_meta.json").read_text(encoding="utf-8")
        )
        assert meta["beast_version"] == "1.10.4"
        assert meta["content_fingerprint"] == content_fingerprint(config)

    def test_duplicate_seeds_are_deduplicated_and_names_distinct(self, tmp_path, monkeypatch):
        config_path = _runner_config(tmp_path)
        calls = self._install_fake(monkeypatch)
        summary = BeastRunner(
            str(config_path), workdir=str(tmp_path / "runs"), seeds=[7, 7, 8], force=True
        ).run()
        assert summary.seeds == [7, 8]
        assert len(calls) == 2
        prefixes = {call[0][call[0].index("-prefix") + 1] for call in calls}
        assert prefixes == {"seed_7_", "seed_8_"}

    def test_resume_loads_latest_checkpoint(self, tmp_path, monkeypatch):
        config_path = _runner_config(tmp_path)
        config = ConfigParser().parse(str(config_path))
        workdir = tmp_path / "runs"
        run_dir = workdir / "12345"
        run_dir.mkdir(parents=True)
        (run_dir / "checkpoint_1000").write_text("state", encoding="utf-8")
        (run_dir / "resume.state").write_text(
            json.dumps({"checkpoint_every": 1000}), encoding="utf-8"
        )
        (run_dir / "run_meta.json").write_text(
            json.dumps(
                {
                    "fingerprint": analysis_fingerprint(config),
                    "content_fingerprint": content_fingerprint(config),
                    "exit_code": 1,
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "run.log").write_text("previous output\n", encoding="utf-8")
        calls = self._install_fake(monkeypatch)
        summary = BeastRunner(str(config_path), workdir=str(workdir), resume=True).run()
        assert summary.results[0].status == "ok"
        command = calls[0][0]
        assert command[command.index("-load_state") + 1].endswith("checkpoint_1000")
        assert "-force_resume" in command
        assert command[command.index("-save_every") + 1] == "1000"
        log_text = (run_dir / "run.log").read_text(encoding="utf-8")
        assert "previous output" in log_text
        assert "resume at" in log_text

    def test_resume_skips_successful_seed(self, tmp_path, monkeypatch):
        config_path = _runner_config(tmp_path)
        config = ConfigParser().parse(str(config_path))
        workdir = tmp_path / "runs"
        run_dir = workdir / "12345"
        run_dir.mkdir(parents=True)
        (run_dir / "run.log").write_text("done\n", encoding="utf-8")
        (run_dir / "run_meta.json").write_text(
            json.dumps(
                {
                    "fingerprint": analysis_fingerprint(config),
                    "content_fingerprint": content_fingerprint(config),
                    "exit_code": 0,
                }
            ),
            encoding="utf-8",
        )
        calls = self._install_fake(monkeypatch)
        summary = BeastRunner(str(config_path), workdir=str(workdir), resume=True).run()
        assert summary.results[0].status == "skipped"
        assert calls == []

    def test_timeout_is_marked_distinctly(self, tmp_path, monkeypatch):
        config_path = _runner_config(tmp_path)

        def timeout_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

        monkeypatch.setattr(runner_mod.subprocess, "run", timeout_run)
        _fake_java(monkeypatch, runner_mod)
        _fake_jar(monkeypatch, runner_mod)
        summary = BeastRunner(
            str(config_path), workdir=str(tmp_path / "runs"), force=True, timeout=1
        ).run()
        result = summary.results[0]
        assert result.timed_out is True
        assert result.status == "timeout"
        assert result.ok is False
        assert "TIMEOUT" in (tmp_path / "runs" / "12345" / "run.log").read_text(encoding="utf-8")

    def test_gate1_failure_aborts_before_java(self, tmp_path, monkeypatch):
        calls = self._install_fake(monkeypatch)
        finding = types.SimpleNamespace(severity="error", path="x", message="bad xml")

        monkeypatch.setattr(runner_mod, "validate_xml_string", lambda *a, **k: [finding])
        with pytest.raises(RunError, match="Gate 1 failed"):
            BeastRunner(BASIC, workdir=str(tmp_path / "runs"), force=True).run()
        assert calls == []

    def test_both_version_requires_explicit_choice(self, tmp_path, monkeypatch):
        self._install_fake(monkeypatch)
        shutil.copy("examples/primates.fasta", tmp_path / "primates.fasta")
        config_path = tmp_path / "both.yaml"
        config_path.write_text(
            Path(BASIC)
            .read_text(encoding="utf-8")
            .replace("beast_version: 1.10.4", "beast_version: both"),
            encoding="utf-8",
        )
        with pytest.raises(RunError, match="beast-version"):
            BeastRunner(str(config_path), workdir=str(tmp_path / "runs"), force=True).run()

    def test_unsafe_analysis_name_is_rejected(self, tmp_path, monkeypatch):
        self._install_fake(monkeypatch)
        shutil.copy("examples/primates.fasta", tmp_path / "primates.fasta")
        config_path = tmp_path / "weird.yaml"
        config_path.write_text(
            Path(BASIC)
            .read_text(encoding="utf-8")
            .replace("analysis_name: basic", "analysis_name: 'my analysis/v2'"),
            encoding="utf-8",
        )
        with pytest.raises(RunError):
            BeastRunner(str(config_path), workdir=str(tmp_path / "runs"), force=True).run()

    def test_jar_resolution_reports_hash(self):
        from beastxpy.runner import resolve_jar_details

        details = resolve_jar_details("1.10.4")
        if details is None:
            pytest.skip("no packaged beast jar in this checkout")
        assert details["path"].endswith(".jar")
        assert len(details["sha256"]) == 64


# ---------------------------------------------------------------------------
# pipelines and LaTeX methods
# ---------------------------------------------------------------------------


class TestPipelines:
    def test_snakemake_seed_config_and_outputs(self, tmp_path):
        config = _parse_basic()
        target = tmp_path / "Snakefile"
        text = generate_snakemake(config, str(target), config_filename="config_basic.yaml")
        assert validate_pipeline_text("snakemake", text) == []
        assert "SEEDS = [12345]" in text
        assert "range(1, 12345" not in text
        assert "config_basic.yaml" in text
        assert "seed_{seed}_trace.log" in text
        assert "seed_{seed}_tree.log" in text
        assert "<built-in function input>" not in text

    def test_nextflow_seed_config_and_outputs(self, tmp_path):
        config = _parse_basic()
        target = tmp_path / "main.nf"
        text = generate_nextflow(config, str(target), config_filename="config_basic.yaml")
        assert validate_pipeline_text("nextflow", text) == []
        assert "params.seeds = [12345]" in text
        assert "path(*" not in text
        assert "PREPARE.out.xml.first()" in text
        assert "${seed}/seed_${seed}_trace.log" in text

    def test_paths_are_quoted(self, tmp_path):
        config = _parse_basic()
        snake = generate_snakemake(
            config, str(tmp_path / "Snakefile"), config_filename="my config.yaml"
        )
        assert "'my config.yaml'" in snake
        nf = generate_nextflow(
            config, str(tmp_path / "main.nf"), config_filename='my "odd" config.yaml'
        )
        assert 'params.config = "my \\"odd\\" config.yaml"' in nf

    @pytest.mark.skipif(shutil.which("snakemake") is None, reason="snakemake not installed")
    def test_snakemake_dry_run(self, tmp_path):
        config = _parse_basic()
        generate_snakemake(config, str(tmp_path / "Snakefile"), config_filename="config_basic.yaml")
        shutil.copy(BASIC, tmp_path / "config_basic.yaml")
        proc = subprocess.run(
            ["snakemake", "-n", "--cores", "1", "--snakefile", str(tmp_path / "Snakefile")],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=300,
        )
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.skipif(
        shutil.which("nextflow") is None or os.name == "nt",
        reason=(
            "nextflow not installed; native Nextflow is unsupported on Windows "
            "(SIGHUP registration fails), use WSL there"
        ),
    )
    def test_nextflow_dry_run(self, tmp_path):
        config = _parse_basic()
        generate_nextflow(config, str(tmp_path / "main.nf"), config_filename="config_basic.yaml")
        shutil.copy(BASIC, tmp_path / "config_basic.yaml")
        proc = subprocess.run(
            ["nextflow", "run", str(tmp_path / "main.nf"), "-preview"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=300,
        )
        assert proc.returncode == 0, proc.stderr


class TestMethodsLatex:
    def test_default_does_not_claim_validation(self):
        config = _parse_basic()
        latex = generate_methods_latex(config)
        assert "parser instantiation" not in latex
        assert "was not performed" in latex

    def test_no_double_escaping_or_strict_strict(self):
        config = _parse_basic()
        latex = generate_methods_latex(config, validated=True, validation_versions=["1.10.4"])
        assert r"\Gamma_{4}" in latex
        assert r"$+\Gamma_{4}$" in latex
        assert "textbackslash{}Gamma" not in latex
        assert "strict strict" not in latex
        assert "parser instantiation" in latex


# ---------------------------------------------------------------------------
# quick_generate defaults and relative paths
# ---------------------------------------------------------------------------


class TestQuickGenerate:
    def test_defaults_pass_gate0_and_relative_paths_resolve(self, tmp_path, monkeypatch):
        fasta = tmp_path / "mini.fasta"
        fasta.write_text(
            ">A\nACGTACGTACGT\n>B\nACGTACGTACGA\n>C\nACGTACGTACGC\n>D\nACGTACGTACGG\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        xml = BeastXPy().quick_generate(alignment="mini.fasta", name="quicktest")
        assert "<beast" in xml
        assert "mini.fasta" not in xml  # embedded sequences, not a temp path

    def test_cli_quick_without_output_writes_stdout(self, tmp_path, capsys, monkeypatch):
        fasta = tmp_path / "mini.fasta"
        fasta.write_text(
            ">A\nACGTACGTACGT\n>B\nACGTACGTACGA\n>C\nACGTACGTACGC\n>D\nACGTACGTACGG\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        code = main(["quick", "--alignment", "mini.fasta", "--name", "quicktest"])
        captured = capsys.readouterr()
        assert code == 0, captured.err
        assert captured.out.startswith("<?xml")


# ---------------------------------------------------------------------------
# sidecar parent directories and no half-written artefacts
# ---------------------------------------------------------------------------


class TestSidecarSafety:
    """Writer/API-layer guarantees.

    Gate 0 may (legitimately) restrict configured sidecar names to a basename;
    the API must nevertheless create parent directories and never leave a
    half-written artefact when a caller supplies an explicit nested path.
    """

    def test_explicit_nested_sidecar_paths_create_parents(self, tmp_path, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        config = _parse_basic()
        config.output.fingerprint_file = str(tmp_path / "missing" / "sub" / "fp.json")
        config.output.methods_file = str(tmp_path / "missing" / "sub" / "methods.tex")
        xml_path = tmp_path / "out.xml"
        xml_path.write_text('<?xml version="1.0"?><beast/>', encoding="utf-8")
        BeastXPy()._write_sidecars(config, xml_path, None, multi=False)
        assert (tmp_path / "missing" / "sub" / "fp.json").exists()
        assert (tmp_path / "missing" / "sub" / "methods.tex").exists()

    def test_failed_sidecar_removes_xml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        config = _parse_basic()
        config.output.fingerprint_file = str(blocker / "sub" / "fp.json")
        config.output.methods_file = str(tmp_path / "methods.tex")
        xml_path = tmp_path / "out.xml"
        xml_path.write_text('<?xml version="1.0"?><beast/>', encoding="utf-8")
        with pytest.raises(OSError):
            BeastXPy()._write_sidecars(config, xml_path, None, multi=False)
        assert not xml_path.exists()

    def test_existing_output_without_force_raises_before_writing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        out = tmp_path / "out.xml"
        out.write_text("sentinel", encoding="utf-8")
        with pytest.raises(GateError) as excinfo:
            BeastXPy(force=False).generate_xml(JC69, output=str(out))
        assert excinfo.value.gate_name == "output"
        assert out.read_text(encoding="utf-8") == "sentinel"


# ---------------------------------------------------------------------------
# Real CLI smoke (subprocess) for stdout/stderr separation
# ---------------------------------------------------------------------------


class TestCliSubprocess:
    def test_generate_stdout_is_xml(self):
        root = Path(__file__).resolve().parent.parent
        proc = subprocess.run(
            [sys.executable, "-m", "beastxpy.main", "generate", "--config", BASIC],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.startswith("<?xml")
        assert "Gate 0" in proc.stderr
        ET.fromstring(proc.stdout)
