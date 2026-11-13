"""Unit tests for the Gate-1 XML validator."""

from __future__ import annotations

import pytest

from beastxpy.model import ModelAssembler
from beastxpy.validator import validate_xml_string
from beastxpy.xml_writer import write_xml


class TestValidatorBasic:
    """Basic validator functionality."""

    def test_valid_generated_xml(self, config_basic):
        asm = ModelAssembler(config_basic)
        xml = write_xml(asm.assemble())
        findings = validate_xml_string(xml)
        errors = [f for f in findings if f.severity == "error"]
        assert not errors, f"Expected no errors, got: {errors}"

    def test_valid_jc69_xml(self, config_jc69):
        asm = ModelAssembler(config_jc69)
        xml = write_xml(asm.assemble())
        findings = validate_xml_string(xml)
        errors = [f for f in findings if f.severity == "error"]
        assert not errors, f"Expected no errors, got: {errors}"

    def test_valid_calibrated_xml(self, config_calibrated):
        asm = ModelAssembler(config_calibrated)
        xml = write_xml(asm.assemble())
        findings = validate_xml_string(xml)
        errors = [f for f in findings if f.severity == "error"]
        assert not errors, f"Expected no errors, got: {errors}"

    def test_valid_tip_dates_xml(self, config_tip_dates):
        asm = ModelAssembler(config_tip_dates)
        xml = write_xml(asm.assemble())
        findings = validate_xml_string(xml)
        errors = [f for f in findings if f.severity == "error"]
        assert not errors, f"Expected no errors, got: {errors}"

    def test_valid_advanced_xml(self, config_advanced):
        asm = ModelAssembler(config_advanced)
        xml = write_xml(asm.assemble())
        findings = validate_xml_string(xml)
        errors = [f for f in findings if f.severity == "error"]
        assert not errors, f"Expected no errors, got: {errors}"

    def test_valid_rlc_xml(self, config_rlc):
        asm = ModelAssembler(config_rlc)
        xml = write_xml(asm.assemble())
        findings = validate_xml_string(xml)
        errors = [f for f in findings if f.severity == "error"]
        assert not errors, f"Expected no errors, got: {errors}"


class TestValidatorCoverage:
    """Parameter operator/prior coverage invariants."""

    def test_fixed_kappa_not_estimated(self, config_jc69):
        """JC69 kappa with estimate=false should not trigger coverage error."""
        asm = ModelAssembler(config_jc69)
        xml = write_xml(asm.assemble())
        findings = validate_xml_string(xml)
        # No NO_OPERATOR or NO_PRIOR errors for kappa
        for f in findings:
            assert "kappa" not in getattr(f, "message", "")

    def test_fixed_frequencies_not_estimated(self, config_jc69):
        asm = ModelAssembler(config_jc69)
        xml = write_xml(asm.assemble())
        findings = validate_xml_string(xml)
        for f in findings:
            if getattr(f, "level", "") == "error":
                assert "frequencies" not in getattr(f, "message", "")


class TestValidatorAllExamples:
    """Run Gate-1 validator on all example configs."""

    def test_all_examples_valid(self, examples_dir):
        import glob

        from beastxpy.config import ConfigParser

        parser = ConfigParser()
        configs = sorted(glob.glob(str(examples_dir / "config_*.yaml")))
        assert len(configs) >= 16
        for cfg_path in configs:
            cfg = parser.parse(cfg_path)
            asm = ModelAssembler(cfg)
            xml = write_xml(asm.assemble())
            findings = validate_xml_string(xml)
            errors = [f for f in findings if f.severity == "error"]
            assert not errors, f"{cfg_path}: Gate-1 errors: {errors}"


class TestValidatorMalformed:
    """Malformed XML detection."""

    def test_missing_tree_likelihood(self):
        """An XML without treeLikelihood should be flagged."""
        xml = (
            "<beast>"
            "<mcmc id='mcmc' chainLength='1000'>"
            "<posterior id='posterior'/>"
            "</mcmc>"
            "</beast>"
        )
        findings = validate_xml_string(xml)
        # Should have some errors (e.g., no likelihood found)
        assert any(f.severity == "error" for f in findings)

    def test_invalid_parameter_value(self):
        """A parameter with non-numeric value should error."""
        xml = "<beast>" "<parameter id='bad' value='abc'/>" "</beast>"
        findings = validate_xml_string(xml)
        assert any(f.severity == "error" for f in findings)


class TestCoalescentChildRules:
    @pytest.mark.parametrize(
        "prior, child",
        [
            ("coalescent_logistic", "t50"),
            ("coalescent_expansion", "ancestralPopulationProportion"),
        ],
    )
    def test_missing_required_child_is_a_gate1_error(self, config_basic, prior, child):
        import xml.etree.ElementTree as ET
        from defusedxml import ElementTree as DT

        from beastxpy.models import TreePriorType

        config_basic.tree_prior.type = TreePriorType(prior)
        config_basic.tree_prior.params = {}
        xml = write_xml(ModelAssembler(config_basic).assemble())
        root = DT.fromstring(xml)
        model_tag = "logisticGrowth" if prior == "coalescent_logistic" else "expansion"
        model = root.find(f".//{model_tag}")
        assert model is not None
        target = model.find(child)
        assert target is not None
        model.remove(target)
        broken = ET.tostring(root, encoding="unicode")
        findings = validate_xml_string(broken)
        assert any(f.severity == "error" and f.code == "COALESCENT_CHILD" for f in findings)


class TestReferenceJarDiscovery:
    def test_official_install_spellings_are_all_probed(self, tmp_path):
        from beastxpy.validator import _reference_install_dirs

        root = tmp_path / "checkout"
        reference = root / "BeastXPy-参考软件"
        for spelling in ("BEASTv1.10.4", "BEAST.X.v10.5.0"):
            (reference / spelling / "lib").mkdir(parents=True)
            (reference / spelling / "lib" / "beast.jar").write_bytes(b"jar")
        names_104 = {p.name for p in _reference_install_dirs("1.10.4", root)}
        names_105 = {p.name for p in _reference_install_dirs("10.5.0", root)}
        assert "BEASTv1.10.4" in names_104
        assert "BEASTv10.5.0" in names_105
        assert "BEAST.X.v10.5.0" in names_105

    def test_lookup_jar_does_not_prefer_packaged_when_reference_exists(self, tmp_path, monkeypatch):
        import beastxpy.validator as V

        for key in (
            "BEASTX_JAR",
            "BEASTX_JAR_1_10_4",
            "BEAST_1_10_4_JAR",
            "BEAST_1_10_4_HOME",
            "BEASTX_1_10_4_HOME",
            "BEASTX_REF_DIR",
        ):
            monkeypatch.delenv(key, raising=False)
        checkout = tmp_path / "checkout"
        reference = checkout / "BeastXPy-参考软件" / "BEASTv1.10.4"
        (reference / "lib").mkdir(parents=True)
        official = reference / "lib" / "beast.jar"
        official.write_bytes(b"official")
        candidates = V.jar_candidates("1.10.4", search_root=checkout)
        paths = [path for path, _source in candidates]
        packaged = V._packaged_jar("1.10.4")
        assert official in paths
        assert paths.index(official) < paths.index(packaged)
        sources = dict(paths and [(p, s) for p, s in candidates])
        assert sources[official] == "reference-install"


class TestElementWhitelistCaseSensitivity:
    """BEAST parser names are case-sensitive; Gate 1 must not fold case."""

    def test_element_index_is_exact(self):
        from beastxpy.validator import _element_entry

        assert _element_entry("treeModel") is not None
        assert _element_entry("TreeModel") is None
        assert _element_entry("TREEMODEL") is None

    def test_case_variant_is_a_gate1_error_with_hint(self, config_basic):
        xml = write_xml(ModelAssembler(config_basic).assemble())
        assert "<treeModel " in xml
        variant = xml.replace("<treeModel ", "<TreeModel ").replace("</treeModel>", "</TreeModel>")
        findings = validate_xml_string(variant)
        errors = [f for f in findings if f.severity == "error"]
        codes = {f.code for f in errors}
        assert "ELEMENT_CASE_MISMATCH" in codes
        message = "\n".join(f.message for f in errors if f.code == "ELEMENT_CASE_MISMATCH")
        assert "<treeModel>" in message
        assert "case-sensitive" in message


class TestParameterDimensionValidation:
    def test_symbolic_dimension_is_legal(self):
        xml = '<beast version="2.0"><parameter id="x" value="1 2" ' 'dimension="2n-2"/></beast>'
        findings = validate_xml_string(xml, beast_version="1.10.4", strict=False)
        assert not [f for f in findings if f.code == "PARAM_DIMENSION"]

    def test_invalid_dimension_is_reported(self):
        xml = '<beast version="2.0"><parameter id="x" value="1 2" ' 'dimension="two-n"/></beast>'
        findings = validate_xml_string(xml, beast_version="1.10.4", strict=False)
        assert [f for f in findings if f.code == "PARAM_DIMENSION"]


def test_run_java_uses_the_supplied_runtime_without_reprobing(monkeypatch):
    """Gate 3 must probe Java once and reuse that runtime (Minor-4)."""
    from types import SimpleNamespace

    from beastxpy import validator as V

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(V.subprocess, "run", fake_run)
    monkeypatch.setattr(
        V,
        "find_java_runtime",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-probe Java")),
    )
    runtime = V.JavaRuntime(path="/fake/bin/java", version="17", usable=True)
    result = V._run_java(["BeastXValidator", "x.xml"], runtime=runtime)
    assert result.returncode == 0
    assert calls == [["/fake/bin/java", "BeastXValidator", "x.xml"]]
