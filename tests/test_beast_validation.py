"""Gate 3 integration tests against the official BEAST installations.

These tests never skip a real validation failure: if the environment is
missing they fail with an explicit reason.  The
``java-core`` mode uses BEAST's legacy pure-Java TreeLikelihood parser so the
initial posterior can be evaluated on machines without native hmsbeagle.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from beastxpy.config import ConfigParser
from beastxpy.model import ModelAssembler
from beastxpy.validator import (
    GATE3_ENVIRONMENT_MISSING,
    GATE3_NOT_RUN,
    _native_library_markers,
    find_java_runtime,
    lookup_beagle_details,
    lookup_jar_details,
    run_beast_validation,
)
from beastxpy.xml_writer import write_xml

VERSIONS = ("1.10.4", "10.5.0")


pytestmark = pytest.mark.java


def _generate_xml(config_path: str, version: str) -> str:
    cfg = ConfigParser().parse(config_path)
    cfg.control.beast_version = version
    return write_xml(ModelAssembler(cfg).assemble(), beast_version=version)


def _require_environment(version: str) -> None:
    runtime = find_java_runtime()
    if not runtime.usable:
        pytest.fail(f"no usable Java runtime: {runtime.reason}")
    details = lookup_jar_details(version)
    if details is None:
        pytest.fail(f"no BEAST {version} jar found")


def _validate(tmp_path: Path, examples_dir: Path, config: str, version: str, **kwargs):
    _require_environment(version)
    xml = _generate_xml(str(examples_dir / config), version)
    xml_path = tmp_path / f"{Path(config).stem}_{version}.xml"
    xml_path.write_text(xml, encoding="utf-8")
    result = run_beast_validation(str(xml_path), beast_version=version, java_core=True, **kwargs)
    assert result.status != GATE3_ENVIRONMENT_MISSING, result.reason
    assert result.executed, result.reason
    return result, xml_path


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize(
    "config",
    ["config_basic.yaml", "config_subst_jc69.yaml", "config_calibrated_yule.yaml"],
)
def test_validate_generated_xml(tmp_path, examples_dir, config, version):
    result, _ = _validate(tmp_path, examples_dir, config, version)
    assert result.ok, f"{config} {version}: {result.status}: {result.reason}"
    stages = {stage["stage"]: stage for stage in result.stages}
    assert stages["parse"]["status"] == "pass"
    assert stages["instantiate"]["status"] == "pass"
    assert stages["posterior"]["status"] == "pass"
    assert result.chain_length_seen is None


@pytest.mark.parametrize("version", VERSIONS)
def test_chain_length_rewrite_is_effective(tmp_path, examples_dir, version):
    result, _ = _validate(
        tmp_path, examples_dir, "config_basic.yaml", version, chain_length=1234, timeout=180
    )
    assert result.ok, result.reason
    assert result.chain_length_seen == 1234


@pytest.mark.parametrize("version", VERSIONS)
def test_parse_only_mode(tmp_path, examples_dir, version):
    _require_environment(version)
    xml = _generate_xml(str(examples_dir / "config_basic.yaml"), version)
    xml_path = tmp_path / f"parse_{version}.xml"
    xml_path.write_text(xml, encoding="utf-8")
    result = run_beast_validation(str(xml_path), beast_version=version, parse_only=True)
    assert result.ok, result.reason
    assert {stage["stage"] for stage in result.stages} >= {"environment", "parse"}
    assert "instantiate" not in {stage["stage"] for stage in result.stages}
    assert result.tree_likelihood_rule_source == "shim"
    assert result.as_dict()["tree_likelihood_rule_source"] == "shim"


@pytest.mark.parametrize("version", VERSIONS)
def test_missing_xml_is_not_run_not_executed_fail(tmp_path, version):
    """A missing input never reached BEAST, so it must not be executed-fail (Minor-2)."""
    _require_environment(version)
    missing = tmp_path / f"absent_{version}.xml"
    result = run_beast_validation(str(missing), beast_version=version, java_core=True)
    assert result.status == GATE3_NOT_RUN, result.reason
    assert result.exit_code == 3, result.reason
    assert not result.ok
    assert not result.executed
    assert result.skipped


@pytest.mark.parametrize("version", VERSIONS)
def test_invalid_xml_fails_and_is_not_reported_ok(tmp_path, version):
    _require_environment(version)
    broken = tmp_path / f"broken_{version}.xml"
    broken.write_text("<beast><mcmc chainLength='10'></beast>", encoding="utf-8")
    result = run_beast_validation(str(broken), beast_version=version, java_core=True)
    assert not result.ok
    assert result.status == "executed-fail"
    assert result.reason


def test_native_beagle_discovery_requires_a_real_library(tmp_path, monkeypatch):
    """A directory is only accepted when it contains an hmsbeagle binary."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("BEASTX_BEAGLE_LIB", str(empty))
    assert lookup_beagle_details("1.10.4") is None

    native = tmp_path / "native"
    native.mkdir()
    (native / _native_library_markers()[0]).write_bytes(b"not a real library")
    monkeypatch.setenv("BEASTX_BEAGLE_LIB", str(native))
    details = lookup_beagle_details("1.10.4")
    assert details is not None
    assert details["source"] == "env:BEASTX_BEAGLE_LIB"
    assert Path(details["path"]) == native.resolve()


def test_native_beagle_request_fails_closed_without_library(tmp_path, examples_dir, monkeypatch):
    """Requesting native BEAGLE must never silently fall back to another core."""
    _require_environment("1.10.4")
    for key in ("BEASTX_BEAGLE_LIB", "BEASTX_BEAGLE_1_10_4_LIB", "BEAGLE_HOME", "BEAGLE_LIB"):
        monkeypatch.delenv(key, raising=False)
    xml = _generate_xml(str(examples_dir / "config_basic.yaml"), "1.10.4")
    xml_path = tmp_path / "native_missing.xml"
    xml_path.write_text(xml, encoding="utf-8")
    empty = tmp_path / "no-beagle"
    empty.mkdir()
    result = run_beast_validation(
        str(xml_path),
        beast_version="1.10.4",
        native_beagle=True,
        beagle_dir=str(empty),
    )
    assert not result.ok
    assert result.status == GATE3_ENVIRONMENT_MISSING
    assert "hmsbeagle" in result.reason
    assert not result.executed


def test_official_validate_rejects_run_without_native_mode(tmp_path):
    script = Path(__file__).resolve().parent.parent / "tools" / "official_validate.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--run",
            "--matrix",
            "examples/config_basic.yaml",
            "--out",
            str(tmp_path),
        ],
        cwd=str(script.parent.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 2
    assert "--run requires --mode beagle" in proc.stderr


@pytest.mark.parametrize("version", VERSIONS)
def test_heavy_tail_prior_fixture_runs(tmp_path, version):
    """The P3 prior fixture emits valid half-normal/Laplace/half-T priors."""
    _require_environment(version)
    fixture = Path(__file__).resolve().parent / "fixtures" / "config_priors_half.yaml"
    cfg = ConfigParser().parse(str(fixture))
    cfg.control.beast_version = version
    xml = write_xml(ModelAssembler(cfg).assemble(), beast_version=version)
    for element in ("halfNormalPrior", "laplacePrior", "halfTPrior"):
        assert f"<{element}" in xml
    xml_path = tmp_path / f"priors_half_{version}.xml"
    xml_path.write_text(xml, encoding="utf-8")
    result = run_beast_validation(
        str(xml_path),
        beast_version=version,
        java_core=True,
        chain_length=50,
        run_chain=True,
        timeout=180,
    )
    assert result.ok, result.reason
    assert result.chain_length_seen == 50


class TestGate3CoreSelection:
    """Default Gate 3 must use BEAST's real likelihood core whenever one runs.

    The validator is stubbed: what is under test is which core the API asks
    for, and that a broken hmsbeagle install is never reported as an XML defect.
    """

    def _run(self, monkeypatch, *, beagle, native_ok=True, native_stderr=""):
        import types

        from beastxpy import api as api_module

        calls = []

        def fake_validate(xml_path, beast_version="1.10.4", **kwargs):
            calls.append(kwargs)
            legacy = "java_core" in kwargs
            return types.SimpleNamespace(
                ok=True if legacy else native_ok,
                exit_code=0 if legacy or native_ok else 1,
                command=["--java-core" if legacy else "--beagle"],
                stderr="" if legacy else native_stderr,
                stdout="",
                reason="" if legacy else native_stderr,
                findings=[],
                skipped=False,
                executed=True,
            )

        monkeypatch.setattr(api_module, "beast_validate_xml", fake_validate)
        monkeypatch.setattr(
            api_module, "probe_java", lambda *a, **k: types.SimpleNamespace(path="java")
        )
        monkeypatch.setattr(api_module, "resolve_jar_details", lambda v: {"path": "beast.jar"})
        monkeypatch.setattr(
            api_module,
            "lookup_beagle_details",
            lambda version, explicit=None: (
                {"path": "/beagle", "source": "test"} if beagle else None
            ),
        )
        return calls, api_module.BeastXPy()._gate3_path("doc.xml", "1.10.4", False)

    def test_without_beagle_the_legacy_java_core_is_used(self, monkeypatch):
        calls, outcome = self._run(monkeypatch, beagle=False)
        assert calls == [{"dry_parse": True, "java_core": True}]
        assert outcome.status == "passed"

    def test_with_beagle_the_native_core_is_used(self, monkeypatch):
        calls, outcome = self._run(monkeypatch, beagle=True)
        assert len(calls) == 1
        assert calls[0]["native_beagle"] is True
        assert "java_core" not in calls[0]
        assert outcome.status == "passed"

    def test_unusable_beagle_falls_back_with_a_warning(self, monkeypatch):
        with pytest.warns(RuntimeWarning, match="installed but unusable"):
            calls, outcome = self._run(
                monkeypatch,
                beagle=True,
                native_ok=False,
                native_stderr=(
                    "No acceptable BEAGLE library plugins found. Make sure that "
                    "BEAGLE is properly installed"
                ),
            )
        assert len(calls) == 2
        assert calls[1] == {"dry_parse": True, "java_core": True}
        assert outcome.status == "passed"

    def test_a_real_model_failure_is_not_masked_by_the_fallback(self, monkeypatch):
        calls, outcome = self._run(
            monkeypatch,
            beagle=True,
            native_ok=False,
            native_stderr="java.lang.IllegalArgumentException: bad prior",
        )
        assert len(calls) == 1
        assert outcome.status == "failed"
        assert "bad prior" in outcome.reason


@pytest.mark.parametrize("version", VERSIONS)
def test_shim_accepts_official_scaling_scheme(tmp_path, examples_dir, version):
    """A legal scalingScheme attribute must not be rejected by the shim."""
    _require_environment(version)
    xml = _generate_xml(str(examples_dir / "config_basic.yaml"), version)
    assert '<treeLikelihood id="treeLikelihood">' in xml
    variant = xml.replace(
        '<treeLikelihood id="treeLikelihood">',
        '<treeLikelihood id="treeLikelihood" scalingScheme="none">',
        1,
    )
    xml_path = tmp_path / f"scaling_{version}.xml"
    xml_path.write_text(variant, encoding="utf-8")
    result = run_beast_validation(str(xml_path), beast_version=version, parse_only=True)
    assert result.ok, f"{result.status}: {result.reason}\n{result.stderr[-2000:]}"


@pytest.mark.parametrize("version", VERSIONS)
def test_shim_rejects_shim_only_attributes(tmp_path, examples_dir, version):
    """Attributes absent from the official parser rules must not pass."""
    _require_environment(version)
    xml = _generate_xml(str(examples_dir / "config_basic.yaml"), version)
    variant = xml.replace(
        '<treeLikelihood id="treeLikelihood">',
        '<treeLikelihood id="treeLikelihood" allowMissingTaxa="true">',
        1,
    )
    xml_path = tmp_path / f"shim_attr_{version}.xml"
    xml_path.write_text(variant, encoding="utf-8")
    result = run_beast_validation(str(xml_path), beast_version=version, parse_only=True)
    assert not result.ok
    assert result.status == "executed-fail"
    assert "allowMissingTaxa" in (result.reason + result.stderr)


def _javac_path() -> str:
    import shutil

    home = os.environ.get("JAVA_HOME")
    if home:
        candidate = Path(home) / "bin" / ("javac.exe" if os.name == "nt" else "javac")
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("javac")
    if found:
        return found
    pytest.fail("javac is required for the TreeLikelihood rule-parity test")


@pytest.mark.parametrize("version", VERSIONS)
def test_shim_syntax_rules_equal_official_parser(tmp_path, version):
    """Reflection comparison of shim vs official BeagleTreeLikelihoodParser rules.

    The source snapshot of the official parser is pinned by the bundled jars;
    this test compares the *compiled* rule objects, not a hand-maintained list.
    """
    _require_environment(version)
    javac = _javac_path()
    details = lookup_jar_details(version)
    assert details is not None
    jar = Path(details["path"])
    classes = Path(__file__).resolve().parent.parent / "beastxpy" / "tools" / "classes" / version
    assert (
        classes / "bxpshim" / "BxpShimTreeLikelihoodParser.class"
    ).is_file(), "compiled shim missing; run python beastxpy/tools/build_jars.py --skip-ant"
    source = tmp_path / "RuleParityCheck.java"
    source.write_text(
        """
import dr.evomodelxml.treelikelihood.BeagleTreeLikelihoodParser;
import dr.xml.XMLSyntaxRule;

public class RuleParityCheck {
    private static String canonical(XMLSyntaxRule[] rules) {
        StringBuilder out = new StringBuilder();
        for (XMLSyntaxRule rule : rules) {
            out.append(rule.ruleString().replaceAll("\\\\s+", " ").trim()).append('\\n');
        }
        return out.toString();
    }

    public static void main(String[] args) {
        String official = canonical(new BeagleTreeLikelihoodParser().getSyntaxRules());
        String shim = canonical(new bxpshim.BxpShimTreeLikelihoodParser().getSyntaxRules());
        if (!official.equals(shim)) {
            System.err.println("--- official ---\\n" + official);
            System.err.println("--- shim ---\\n" + shim);
            System.exit(1);
        }
        System.out.println("RULES-EQUAL");
        System.out.println(official);
    }
}
""",
        encoding="utf-8",
    )
    compile_dir = tmp_path / "classes"
    compile_dir.mkdir()
    classpath = os.pathsep.join([str(jar), str(classes)])
    compile_proc = subprocess.run(
        [javac, "-cp", classpath, "-d", str(compile_dir), str(source)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert compile_proc.returncode == 0, compile_proc.stderr
    run_proc = subprocess.run(
        [
            find_java_runtime().path,
            "-cp",
            os.pathsep.join([str(compile_dir), classpath]),
            "RuleParityCheck",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run_proc.returncode == 0, run_proc.stderr
    assert "RULES-EQUAL" in run_proc.stdout
