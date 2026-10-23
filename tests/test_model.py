"""Unit tests for model assembly and XML structure."""

from __future__ import annotations

import pytest

from beastxpy.model import ModelAssembler, ModelAssemblyError
from beastxpy.xml_writer import write_xml


class TestModelAssembly:
    """ModelAssembler tests."""

    def test_assemble_basic(self, config_basic):
        asm = ModelAssembler(config_basic)
        tree = asm.assemble()
        assert tree["tag"] == "beast"
        assert "children" in tree
        assert len(tree["children"]) > 0

    def test_assemble_jc69(self, config_jc69):
        asm = ModelAssembler(config_jc69)
        tree = asm.assemble()
        assert tree["tag"] == "beast"

    def test_assemble_calibrated(self, config_calibrated):
        asm = ModelAssembler(config_calibrated)
        tree = asm.assemble()
        assert tree["tag"] == "beast"

    def test_assemble_tip_dates(self, config_tip_dates):
        asm = ModelAssembler(config_tip_dates)
        tree = asm.assemble()
        assert tree["tag"] == "beast"

    def test_assemble_advanced(self, config_advanced):
        asm = ModelAssembler(config_advanced)
        tree = asm.assemble()
        assert tree["tag"] == "beast"

    def test_assemble_rlc(self, config_rlc):
        asm = ModelAssembler(config_rlc)
        tree = asm.assemble()
        assert tree["tag"] == "beast"

    def test_assemble_ebsp(self, config_ebsp):
        asm = ModelAssembler(config_ebsp)
        tree = asm.assemble()
        assert tree["tag"] == "beast"


class TestModelInvariants:
    """Model assembly invariant checks."""

    def test_all_estimated_params_have_operators(self, config_basic):
        asm = ModelAssembler(config_basic)
        asm.assemble()
        for pid in asm.params.estimated:
            assert pid in asm.params.operator_targets

    def test_all_estimated_params_have_priors(self, config_basic):
        asm = ModelAssembler(config_basic)
        asm.assemble()
        for pid in asm.params.estimated:
            assert pid in asm.params.prior_targets

    def test_jc69_no_estimated_kappa(self, config_jc69):
        """JC69 kappa must be fixed, not estimated."""
        asm = ModelAssembler(config_jc69)
        asm.assemble()
        assert "kappa" not in asm.params.estimated

    def test_jc69_frequencies_uniform_not_estimated(self, config_jc69):
        asm = ModelAssembler(config_jc69)
        asm.assemble()
        assert "frequencies" not in asm.params.estimated


class TestXMLStructure:
    """Generated XML structure checks."""

    def test_xml_has_beast_root(self, config_basic):
        asm = ModelAssembler(config_basic)
        xml = write_xml(asm.assemble())
        assert "<beast>" in xml
        assert "</beast>" in xml

    def test_xml_has_mcmc(self, config_basic):
        asm = ModelAssembler(config_basic)
        xml = write_xml(asm.assemble())
        assert "<mcmc" in xml
        assert "</mcmc>" in xml

    def test_xml_has_operators(self, config_basic):
        asm = ModelAssembler(config_basic)
        xml = write_xml(asm.assemble())
        assert '<operators id="operators">' in xml

    def test_xml_has_tree_likelihood(self, config_basic):
        asm = ModelAssembler(config_basic)
        xml = write_xml(asm.assemble())
        assert "treeLikelihood" in xml

    def test_xml_has_prior(self, config_basic):
        asm = ModelAssembler(config_basic)
        xml = write_xml(asm.assemble())
        assert '<prior id="prior">' in xml

    def test_xml_has_posterior(self, config_basic):
        asm = ModelAssembler(config_basic)
        xml = write_xml(asm.assemble())
        assert '<posterior id="posterior">' in xml

    def test_xml_has_loggers(self, config_basic):
        asm = ModelAssembler(config_basic)
        xml = write_xml(asm.assemble())
        assert "<log " in xml
        assert "<logTree" in xml


class TestModelAssemblyErrors:
    """Error cases in model assembly."""

    def test_duplicate_parameter_id(self):
        """Registering the same parameter id twice should fail."""
        from beastxpy.model import _ParamRegistry

        reg = _ParamRegistry()
        reg.register("test.param", True)
        with pytest.raises(ModelAssemblyError):
            reg.register("test.param", True)

    def test_missing_operator_detected(self):
        """Estimated parameter without operator should be caught."""
        from beastxpy.model import _ParamRegistry

        reg = _ParamRegistry()
        reg.register("orphan.param", True)
        issues = reg.check_invariants()
        assert any("operator" in i for i in issues)
        assert any("prior" in i for i in issues)
