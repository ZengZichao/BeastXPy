"""Tests for beastxpy.assessment and the ``beastxpy assess`` CLI (F1).

The multi-chain and single-chain inputs are REAL BEAST 1.10.4 outputs checked
in under tests/fixtures/assess_multi and tests/fixtures/assess_single (see
tests/fixtures/assess_multi/PROVENANCE.txt).  They are 100-step chains: they
exercise the parsing and multi-chain plumbing, and are far too short to be
convergence evidence.  Hand-written trace logs below are labelled parser
fixtures and are used only for escaping/error paths, never as diagnostics of
a real analysis.

CLI tests run in a subprocess (``python -m beastxpy.main assess ...``) and
assert stdout/stderr, exit codes and --json contents per CONTRACT.md §7.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy import _contracts as C  # noqa: E402
from beastxpy import assessment  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MULTI = FIXTURES / "assess_multi"
SINGLE = FIXTURES / "assess_single"

CONTRACT_TOP_LEVEL_KEYS = [
    "schema_version",
    "generated_at",
    "tool_version",
    "workdir",
    "verdict",
    "strict",
    "thresholds",
    "chains",
    "multichain",
    "reasons",
    "limitations",
]


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "beastxpy.main", "assess", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
    )


def _make_seed_dir(
    workdir: Path,
    seed: int,
    *,
    trace_text: str,
    run_meta: dict = None,
    run_log: str = "",
) -> Path:
    """Build a minimal <workdir>/<seed>/ directory (parser-fixture input)."""
    seed_dir = workdir / str(seed)
    seed_dir.mkdir(parents=True)
    (seed_dir / f"seed_{seed}_trace.log").write_text(trace_text, encoding="utf-8")
    (seed_dir / "run.log").write_text(run_log, encoding="utf-8")
    meta = {"seed": seed, "status": "ok", "exit_code": 0} if run_meta is None else run_meta
    (seed_dir / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return seed_dir


SIMPLE_TRACE = (
    "# PARSER FIXTURE (hand-written, synthetic): exercises the assess plumbing\n"
    "# only. NOT a BEAST run; never interpret its numbers as diagnostics.\n"
    "state\tposterior\n"
    "0\t-10.0\n"
    "10\t-9.0\n"
    "20\t-8.5\n"
    "30\t-8.2\n"
    "40\t-8.1\n"
    "50\t-8.0\n"
    "60\t-8.05\n"
    "70\t-8.02\n"
    "80\t-8.01\n"
    "90\t-8.0\n"
    "100\t-8.0\n"
)


# ---------------------------------------------------------------------------
# Core API: workdir assessment
# ---------------------------------------------------------------------------


def test_multi_fixture_schema_and_multichain():
    doc = assessment.assess_workdir(MULTI, burnin=0.1, ess=0.1, psrf=1000.0)
    for key in CONTRACT_TOP_LEVEL_KEYS:
        assert key in doc, f"missing top-level key {key}"
    assert doc["schema_version"] == C.SCHEMA_ASSESSMENT
    assert doc["strict"] is False
    assert doc["thresholds"] == {
        "burnin": 0.1,
        "ess": 0.1,
        "psrf": 1000.0,
        "operator_low": assessment.OPERATOR_LOW_RATE,
        "operator_high": assessment.OPERATOR_HIGH_RATE,
    }
    assert len(doc["chains"]) == 2
    multichain = doc["multichain"]
    assert multichain["available"] is True
    assert multichain["n_chains"] == 2
    assert multichain["psrf"]["status"] == "ok"
    assert multichain["psrf"]["values"], "PSRF values must not be empty for 2 chains"
    assert "posterior" in multichain["psrf"]["values"]
    assert multichain["asdsf"]["status"] == "ok"
    assert multichain["asdsf"]["value"] is not None
    assert multichain["asdsf"]["value"] >= 0.0
    # Verdict must be computable from the reasons alone.
    counts = C.count_severities(doc["reasons"])
    assert doc["verdict"] == C.verdict_from_counts(counts)
    assert doc["verdict"] in (C.VERDICT_PASS, C.VERDICT_WARN)


def test_multi_fixture_chain_entries_have_contract_fields():
    doc = assessment.assess_workdir(MULTI, ess=0.1, psrf=1000.0)
    for chain in doc["chains"]:
        for key in (
            "seed",
            "status",
            "trace_log",
            "tree_log",
            "run_log",
            "run_meta",
            "samples",
            "burnin_fraction",
            "burnin_samples",
            "parameters",
            "operators",
            "tree_summary",
            "incomplete_reason",
        ):
            assert key in chain
        assert chain["samples"] == 11
        assert chain["burnin_samples"] == 1
        posterior = chain["parameters"]["posterior"]
        for key in ("ess", "act", "mean", "median", "hpd_lower", "hpd_upper", "ess_ok"):
            assert key in posterior
        assert posterior["hpd_lower"] <= posterior["hpd_upper"]
        assert chain["operators"], "real run.log must yield an operator table"
        for operator in chain["operators"]:
            assert operator["status"] in (
                "ok",
                "low",
                "high",
                "never_triggered",
                "not_computable",
            )


def test_multi_fixture_tree_summary_honest_about_missing_columns():
    doc = assessment.assess_workdir(MULTI, ess=0.1, psrf=1000.0)
    summary = doc["chains"][0]["tree_summary"]
    # Strict-clock JC69 run: rootHeight and clock.rate exist ...
    assert summary["tree_height"]["status"] == "ok"
    assert summary["tree_height"]["column"] == "treeModel.rootHeight"
    assert summary["clock_rate"]["status"] == "ok"
    # ... relaxed-clock ucld.* columns do not, and must be not_computable.
    for target in ("ucld_mean", "ucld_stdev"):
        assert summary[target]["status"] == "not_computable"
        assert "ucld" in summary[target]["reason"]
        assert "value" not in summary[target]


def test_default_ess_threshold_fails_on_short_fixture():
    # The 100-step fixture has ESS << 200 for several parameters: honest
    # reporting must turn that into error-severity findings and a fail verdict.
    doc = assessment.assess_workdir(MULTI)
    assert doc["verdict"] == C.VERDICT_FAIL
    assert any(r["id"] == "ess-below-threshold" for r in doc["reasons"])


def test_single_fixture_psrf_and_asdsf_not_computable():
    doc = assessment.assess_workdir(SINGLE, ess=0.1, psrf=1000.0)
    multichain = doc["multichain"]
    assert multichain["available"] is False
    assert multichain["n_chains"] == 1
    psrf = multichain["psrf"]
    assert psrf["status"] == "not_computable"
    assert psrf["values"] == {}  # never a fabricated value
    assert "at least 2" in psrf["reason"]
    asdsf_block = multichain["asdsf"]
    assert asdsf_block["status"] == "not_computable"
    assert asdsf_block["value"] is None
    assert "2" in asdsf_block["reason"]
    # The single chain itself is still fully diagnosed.
    assert len(doc["chains"]) == 1
    assert doc["chains"][0]["parameters"]


def test_limitations_state_no_convergence_no_model_selection():
    doc = assessment.assess_workdir(SINGLE, ess=0.1, psrf=1000.0)
    joined = " ".join(doc["limitations"]).lower()
    assert "convergence" in joined
    assert "model selection" in joined
    assert "diagnostics only" in joined


def test_json_is_strict_and_nan_free():
    doc = assessment.assess_workdir(MULTI, ess=0.1, psrf=1000.0)
    text = C.json_dumps(doc)
    assert "NaN" not in text
    assert "Infinity" not in text
    json.loads(text)  # must be strict-parseable


def test_threshold_validation_errors(tmp_path):
    with pytest.raises(C.ContractError) as excinfo:
        assessment.assess_workdir(MULTI, burnin=1.5)
    assert excinfo.value.fix
    with pytest.raises(C.ContractError):
        assessment.assess_workdir(MULTI, burnin=-0.1)
    with pytest.raises(C.ContractError):
        assessment.assess_workdir(MULTI, ess=0.0)
    with pytest.raises(C.ContractError):
        assessment.assess_workdir(MULTI, psrf=0.5)


def test_missing_workdir_raises_with_fix(tmp_path):
    with pytest.raises(C.ContractError) as excinfo:
        assessment.assess_workdir(tmp_path / "does-not-exist")
    assert "not found" in excinfo.value.message
    assert excinfo.value.fix


def test_empty_workdir_raises_no_chains(tmp_path):
    with pytest.raises(C.ContractError) as excinfo:
        assessment.assess_workdir(tmp_path)
    assert "no chains" in excinfo.value.message
    assert "beastxpy run" in excinfo.value.fix


def test_failed_run_meta_is_error_verdict_fail(tmp_path):
    _make_seed_dir(
        tmp_path,
        1,
        trace_text=SIMPLE_TRACE,
        run_meta={"seed": 1, "status": "failed", "exit_code": 1},
    )
    doc = assessment.assess_workdir(tmp_path, ess=0.1, psrf=1000.0)
    assert doc["chains"][0]["status"] == C.STATUS_FAILED
    assert doc["verdict"] == C.VERDICT_FAIL
    assert any(r["id"] == "chain-failed" and r["severity"] == "error" for r in doc["reasons"])


def test_unparsable_trace_log_is_reported_not_fatal(tmp_path):
    _make_seed_dir(tmp_path, 1, trace_text="this is not a BEAST log at all\n")
    doc = assessment.assess_workdir(tmp_path, ess=0.1, psrf=1000.0)
    assert doc["chains"][0]["status"] == C.STATUS_FAILED
    assert "could not be parsed" in doc["chains"][0]["incomplete_reason"]
    # One broken chain must not fabricate multichain statistics.
    assert doc["multichain"]["psrf"]["status"] == "not_computable"
    assert doc["multichain"]["asdsf"]["status"] == "not_computable"


def test_missing_artefacts_mark_chain_incomplete(tmp_path):
    seed_dir = tmp_path / "3"
    seed_dir.mkdir()
    (seed_dir / "seed_3_trace.log").write_text(SIMPLE_TRACE, encoding="utf-8")
    doc = assessment.assess_workdir(tmp_path, ess=0.1, psrf=1000.0)
    chain = doc["chains"][0]
    assert chain["status"] == "incomplete"
    assert "run_meta.json" in chain["incomplete_reason"]
    assert any(r["id"] == "chain-incomplete" for r in doc["reasons"])


def test_exit_code_mapping():
    assert assessment.exit_code_for({"verdict": "pass"}, strict=False) == 0
    assert assessment.exit_code_for({"verdict": "warn"}, strict=False) == 0
    assert assessment.exit_code_for({"verdict": "warn"}, strict=True) == 1
    assert assessment.exit_code_for({"verdict": "fail"}, strict=True) == 3
    assert assessment.exit_code_for({"verdict": "fail"}, strict=False) == 3


# ---------------------------------------------------------------------------
# HTML rendering and escaping
# ---------------------------------------------------------------------------


def test_html_escapes_hostile_column_names(tmp_path):
    hostile = SIMPLE_TRACE.replace("posterior", '<script>alert(1)</script>&"<>x').replace(
        "# PARSER FIXTURE (hand-written, synthetic): exercises the assess plumbing",
        "# PARSER FIXTURE (hand-written): escaping test only, not BEAST output",
    )
    _make_seed_dir(
        tmp_path,
        1,
        trace_text=hostile,
        run_meta={"seed": 1, "status": "ok", "note": "<img src=x onerror=alert(2)>"},
    )
    doc = assessment.assess_workdir(tmp_path, ess=0.1, psrf=1000.0)
    page = assessment.render_html(doc)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "onerror=" not in page or "&lt;img" in page
    out = tmp_path / "report.html"
    assessment.write_html(doc, out)
    assert out.read_text(encoding="utf-8") == page
    assert "<!DOCTYPE html>" in page
    assert "http://" not in page and "https://" not in page  # self-contained


def test_text_summary_is_console_safe(tmp_path):
    """Summary lines never raise on the console they are printed to."""
    doc = assessment.assess_workdir(MULTI, ess=0.1, psrf=1000.0)
    encoding = sys.stdout.encoding or "utf-8"
    for line in assessment.render_text_summary(doc):
        line.encode(encoding)  # must not raise for the attached console


def test_console_text_escapes_only_unencodable(monkeypatch):
    """GBK/ASCII consoles get backslash escapes; UTF-8 consoles keep CJK."""
    import types

    from beastxpy import _contracts as contracts

    class _FakeOut:
        def __init__(self, encoding):
            self.encoding = encoding

    monkeypatch.setattr(sys, "stdout", _FakeOut("ascii"))
    assert contracts.console_text("path/\u4e2d\u6587") == "path/\\u4e2d\\u6587"
    monkeypatch.setattr(sys, "stdout", _FakeOut("utf-8"))
    assert contracts.console_text("path/\u4e2d\u6587") == "path/\u4e2d\u6587"
    monkeypatch.setattr(sys, "stdout", _FakeOut(None))
    assert contracts.console_text("path/\u4e2d\u6587") == "path/\u4e2d\u6587"
    assert isinstance(_FakeOut("x"), types.SimpleNamespace) is False  # sanity


# ---------------------------------------------------------------------------
# CLI (subprocess) tests
# ---------------------------------------------------------------------------


def test_cli_help_documents_exit_codes_and_limits():
    proc = _cli("--help")
    assert proc.returncode == 0
    assert "Exit codes" in proc.stdout
    assert "convergence" in proc.stdout
    assert "model selection" in proc.stdout


def test_cli_multi_fixture_exit_zero_and_json(tmp_path):
    out = tmp_path / "assess.json"
    proc = _cli(str(MULTI), "--ess", "0.1", "--psrf", "1000", "--json", str(out))
    assert proc.returncode == 0, proc.stderr
    assert "verdict" in proc.stdout
    doc = json.loads(out.read_text(encoding="utf-8"))
    for key in CONTRACT_TOP_LEVEL_KEYS:
        assert key in doc
    assert doc["schema_version"] == C.SCHEMA_ASSESSMENT
    assert doc["multichain"]["n_chains"] == 2


def test_cli_single_fixture_not_computable_in_json(tmp_path):
    out = tmp_path / "single.json"
    proc = _cli(str(SINGLE), "--ess", "0.1", "--psrf", "1000", "--json", str(out))
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["multichain"]["psrf"]["status"] == "not_computable"
    assert doc["multichain"]["psrf"]["values"] == {}
    assert doc["multichain"]["asdsf"]["status"] == "not_computable"
    assert doc["multichain"]["asdsf"]["value"] is None


def test_cli_nonexistent_workdir_exit_two_with_next_step():
    proc = _cli("Z:/definitely/not/here")
    assert proc.returncode == 2
    assert "error:" in proc.stderr
    assert "next step:" in proc.stderr


def test_cli_strict_warning_exit_one(tmp_path):
    proc = _cli(str(MULTI), "--ess", "0.1", "--psrf", "1000", "--strict")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "warn" in proc.stdout


def test_cli_high_ess_threshold_exit_three(tmp_path):
    out = tmp_path / "fail.json"
    proc = _cli(str(MULTI), "--ess", "100000", "--json", str(out))
    assert proc.returncode == 3, proc.stdout + proc.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["verdict"] == "fail"
    assert any(r["id"] == "ess-below-threshold" for r in doc["reasons"])


def test_cli_quiet_prints_nothing_on_stdout(tmp_path):
    proc = _cli(str(MULTI), "--ess", "0.1", "--psrf", "1000", "-q")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_cli_html_written(tmp_path):
    page = tmp_path / "nested" / "assess.html"
    proc = _cli(str(MULTI), "--ess", "0.1", "--psrf", "1000", "--html", str(page), "-q")
    assert proc.returncode == 0, proc.stderr
    assert page.exists()
    assert "<!DOCTYPE html>" in page.read_text(encoding="utf-8")


def test_cli_bad_burnin_exit_two():
    proc = _cli(str(MULTI), "--burnin", "2")
    assert proc.returncode == 2
    assert "next step:" in proc.stderr


def test_assess_bare_trace_files_without_a_workdir(tmp_path):
    traces = [
        MULTI / "1" / "seed_1_trace.log",
        MULTI / "2" / "seed_2_trace.log",
    ]
    trees = [
        MULTI / "1" / "seed_1_tree.log",
        MULTI / "2" / "seed_2_tree.log",
    ]
    doc = assessment.assess_trace_files(traces, trees, burnin=0.1, ess=0.1, psrf=1000.0)
    assert doc["multichain"]["n_chains"] == 2
    assert doc["multichain"]["psrf"]["status"] == "ok"
    assert doc["multichain"]["asdsf"]["status"] == "ok"
    for chain in doc["chains"]:
        assert chain["external"] is True
        assert chain["status"] == "ok"
        assert chain["parameters"]


def test_assess_trace_cli_accepts_bare_logs():
    result = _cli(
        "--trace",
        str(MULTI / "1" / "seed_1_trace.log"),
        "--trace",
        str(MULTI / "2" / "seed_2_trace.log"),
        "--trees",
        str(MULTI / "1" / "seed_1_tree.log"),
        "--trees",
        str(MULTI / "2" / "seed_2_tree.log"),
        "--ess",
        "0.1",
        "--psrf",
        "1000",
        "-q",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_assess_trace_and_workdir_are_mutually_exclusive():
    result = _cli(str(MULTI), "--trace", str(MULTI / "1" / "seed_1_trace.log"))
    assert result.returncode == 2
    assert "either a workdir or --trace" in (result.stdout + result.stderr)


def test_assess_trace_requires_matching_tree_count():
    result = _cli(
        "--trace",
        str(MULTI / "1" / "seed_1_trace.log"),
        "--trace",
        str(MULTI / "2" / "seed_2_trace.log"),
        "--trees",
        str(MULTI / "1" / "seed_1_tree.log"),
    )
    assert result.returncode == 2
    assert "one tree log per trace" in (result.stdout + result.stderr)


def test_assess_html_prints_the_thresholds_actually_used():
    doc = assessment.assess_workdir(MULTI, ess=0.1, psrf=1000.0)
    page = assessment.render_html(doc)
    assert "Thresholds used" in page
    assert "burnin" in page and "0.1" in page
    assert "psrf" in page and "1000" in page
    assert "not a proof of convergence" in page


def test_auto_burnin_not_computable_downgrades_verdict(tmp_path):
    """M7: an uncomputable auto burn-in is a warning, not a silent burn-in 0."""
    trace = tmp_path / "short.log"
    lines = ["state\tshape"]
    lines += [f"{i}\t1.0" for i in range(1, 51)]
    trace.write_text("\n".join(lines) + "\n", encoding="utf-8")
    doc = assessment.assess_trace_files([trace], burnin="auto", ess=1.0, psrf=1.01)
    assert doc["chains"][0]["burnin_status"] == "not_computable"
    assert doc["chains"][0]["burnin_samples"] == 0
    assert doc["verdict"] == "warn"
    assert any(reason["id"] == "burnin-not-computable" for reason in doc["reasons"])
