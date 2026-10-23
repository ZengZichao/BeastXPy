"""Runner orchestration tests."""

from __future__ import annotations


import pytest

from beastxpy.runner import BeastRunner, RunSummary


class TestBeastRunner:
    """BeastRunner tests."""

    def test_runner_creation(self, examples_dir, tmp_path):
        runner = BeastRunner(
            config_path=str(examples_dir / "config_basic.yaml"),
            workdir=str(tmp_path / "run"),
        )
        assert runner is not None

    def test_run_result_summary(self):
        """RunResult and RunSummary should be constructible and serializable."""
        from beastxpy.runner import RunResult

        result = RunResult(
            seed=42,
            workdir="/tmp/run",
            exit_code=0,
            command=["beast", "config.xml"],
            log_path="/tmp/run/beast.log",
            ok=True,
            stdout="test output",
            stderr="",
        )
        summary = RunSummary(
            results=[result],
            workdir="/tmp/run",
            fingerprint="BXP-test",
            beast_version="1.10.4",
            overall_ok=True,
        )
        d = summary.as_dict()
        assert d["overall_ok"] is True
        assert len(d["results"]) == 1


class TestLoggerRewritingAndDuplicates:
    def test_only_beastxpy_own_loggers_are_renamed(self):
        from beastxpy.runner import BeastRunner

        xml = (
            '<beast><log id="fileLog" fileName="analysis.log" logEvery="1000"/>'
            '<logTree id="treeFileLog" fileName="analysis.trees"/>'
            '<log id="customLog" fileName="custom.log" logEvery="1000"/>'
            "</beast>"
        )
        rewritten = BeastRunner._rewrite_logger_names(xml)
        assert 'id="fileLog" fileName="trace.log"' in rewritten
        assert 'id="treeFileLog" fileName="tree.log"' in rewritten
        assert 'id="customLog" fileName="custom.log"' in rewritten
        assert "analysis.log" not in rewritten

    def test_logger_rewrite_handles_greater_than_inside_attributes(self):
        """A legal '>' inside an attribute must not truncate the tag."""
        from beastxpy.runner import BeastRunner

        xml = (
            '<beast><log id="fileLog" note="a > b" fileName="analysis.log" '
            'logEvery="1000"/></beast>'
        )
        rewritten = BeastRunner._rewrite_logger_names(xml)
        assert 'note="a > b"' in rewritten
        assert 'fileName="trace.log"' in rewritten
        assert 'logEvery="1000"' in rewritten

    def test_gate1_rejects_duplicate_logger_output_files(self):
        from beastxpy.validator import validate_xml_string

        xml = (
            '<beast version="2.0">'
            '<log id="a" fileName="same.log" logEvery="1"/>'
            '<log id="b" fileName="same.log" logEvery="1"/>'
            "</beast>"
        )
        findings = validate_xml_string(xml, beast_version="1.10.4", strict=False)
        assert any(f.code == "DUPLICATE_LOG_FILE" for f in findings)

    def test_parallel_zero_is_rejected_without_java(self):
        from beastxpy.runner import BeastRunner, RunError

        runner = BeastRunner(
            config_path="examples/config_subst_jc69.yaml",
            workdir="unused",
            seeds=[1],
            parallel=0,
        )
        with pytest.raises(RunError) as excinfo:
            runner.run()
        assert "--parallel" in str(excinfo.value)


def test_parallel_seed_execution_uses_threads_and_preserves_order(monkeypatch, tmp_path):
    """`--parallel N` runs seeds concurrently but returns them in seed order."""
    import threading
    import time

    from beastxpy import runner as runner_module
    from beastxpy.runner import BeastRunner, RunResult

    monkeypatch.setattr(
        runner_module, "probe_java", lambda: {"path": "/usr/bin/java", "version": "17"}
    )
    monkeypatch.setattr(
        runner_module,
        "resolve_jar_details",
        lambda version: {"path": "/tmp/beast.jar", "sha256": "0" * 64},
    )
    monkeypatch.setattr(runner_module, "lookup_beagle_details", lambda version: None)
    monkeypatch.setattr(runner_module, "_system_beagle_present", lambda: True)
    monkeypatch.setattr(BeastRunner, "_assemble", lambda self: ("<beast/>", {}))

    lock = threading.Lock()
    active = 0
    maximum = 0
    finished = []

    def fake_run_one(self, seed, run_dir, java, jar, env, flags, sidecars=None):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.15)
        with lock:
            active -= 1
            finished.append(seed)
        (run_dir / "run.log").write_text("", encoding="utf-8")
        return RunResult(
            seed=seed,
            workdir=str(run_dir),
            exit_code=0,
            command=[],
            log_path=str(run_dir / "run.log"),
            ok=True,
        )

    monkeypatch.setattr(BeastRunner, "_run_one", fake_run_one)
    runner = BeastRunner(
        config_path="examples/config_subst_jc69.yaml",
        workdir=str(tmp_path / "runs"),
        seeds=[4, 3, 2, 1],
        parallel=2,
    )
    summary = runner.run()
    assert [result.seed for result in summary.results] == [4, 3, 2, 1]
    assert maximum == 2, "at most --parallel workers should run at once"
    assert set(finished) == {1, 2, 3, 4}
    assert summary.overall_ok


def test_run_analysis_command_forwards_parallel(monkeypatch):
    """The library entry point must expose the same concurrency as the CLI (Minor-3)."""
    from beastxpy import runner as runner_module

    captured = {}

    class DummyRunner:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return "summary"

    monkeypatch.setattr(runner_module, "BeastRunner", DummyRunner)
    assert runner_module.run_analysis_command("config.yaml", parallel=3) == "summary"
    assert captured["parallel"] == 3


def test_java_source_uses_resolved_executable_identity(monkeypatch, tmp_path):
    from beastxpy.runner import _java_source

    jdk = tmp_path / "jdk-17"
    (jdk / "bin").mkdir(parents=True)
    java = jdk / "bin" / "java"
    java.write_text("#!/bin/sh\n", encoding="utf-8")
    other = tmp_path / "other-java"
    other.write_text("#!/bin/sh\n", encoding="utf-8")
    unrelated = tmp_path / "jdk-17-other" / "bin" / "java"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setenv("JAVA_HOME", str(jdk))
    monkeypatch.delenv("BEASTX_JAVA", raising=False)
    assert _java_source(str(java)) == "JAVA_HOME"
    # A sibling path with the same prefix must not be mislabelled.
    assert _java_source(str(unrelated)) == "PATH"

    monkeypatch.setenv("BEASTX_JAVA", str(other))
    assert _java_source(str(other)) == "BEASTX_JAVA"
