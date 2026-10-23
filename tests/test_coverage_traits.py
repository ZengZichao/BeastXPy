"""Tests for the discrete and continuous trait coverage plugins (C01/C02)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from beastxpy.config import ConfigError, ConfigParser
from beastxpy.model import ModelAssembler, ModelAssemblyError
from beastxpy.validator import validate_xml_string
from beastxpy.xml_writer import write_xml

COVERAGE_DIR = Path(__file__).resolve().parents[1] / "examples" / "coverage"


def _generate(config_path: Path) -> str:
    config = ConfigParser().parse(str(config_path))
    return write_xml(ModelAssembler(config).assemble(), beast_version="1.10.4")


def _mutated(tmp_path: Path, source: Path, old: str, new: str) -> Path:
    text = source.read_text(encoding="utf-8")
    assert old in text
    shutil.copyfile(COVERAGE_DIR / "nuc.fasta", tmp_path / "nuc.fasta")
    target = tmp_path / source.name
    target.write_text(text.replace(old, new), encoding="utf-8")
    return target


def test_discrete_trait_generates_coherent_xml() -> None:
    xml = _generate(COVERAGE_DIR / "config_discrete_trait.yaml")

    assert '<generalDataType id="trait.dataType">' in xml
    assert '<state code="north"/>' in xml
    assert '<attributePatterns id="trait.pattern" attribute="region">' in xml
    assert '<generalSubstitutionModel id="trait.model">' in xml
    assert '<parameter id="trait.rates" value="1" lower="0" dimension="3"/>' in xml
    assert '<strictClockBranchRates id="trait.branchRates">' in xml
    assert (
        '<ancestralTreeLikelihood id="trait.treeLikelihood" ' 'stateTagName="region.states">' in xml
    )
    assert '<treeModel idref="treeModel"/>' in xml
    assert '<strictClockBranchRates idref="trait.branchRates"/>' in xml
    assert '<ancestralTreeLikelihood idref="trait.treeLikelihood"/>' in xml
    assert '<parameter idref="trait.clock.rate"/>' in xml
    assert '<parameter idref="trait.rates"/>' in xml
    assert '<attr name="region">' in xml

    findings = validate_xml_string(xml)
    assert findings == [], findings


def test_continuous_trait_generates_coherent_xml() -> None:
    xml = _generate(COVERAGE_DIR / "config_continuous_trait.yaml")

    assert '<multivariateDiffusionModel id="trait.diffusionModel">' in xml
    assert '<matrixParameter id="trait.precision">' in xml
    assert '<multivariateTraitLikelihood id="trait.treeLikelihood" ' 'traitName="bodySize"' in xml
    assert '<multivariateDiffusionModel idref="trait.diffusionModel"/>' in xml
    assert '<treeModel idref="treeModel"/>' in xml
    assert "<traitParameter>" in xml
    assert '<parameter id="trait.values"/>' in xml
    assert "<conjugateRootPrior>" in xml
    assert '<multivariateWishartPrior id="trait.precisionPrior" df="1">' in xml
    assert '<precisionGibbsOperator weight="10">' in xml
    assert '<matrixParameter idref="trait.precision"/>' in xml
    assert '<attr name="bodySize">' in xml

    findings = validate_xml_string(xml)
    assert findings == [], findings


def test_discrete_trait_rejects_unknown_state(tmp_path: Path) -> None:
    source = COVERAGE_DIR / "config_discrete_trait.yaml"
    bad = _mutated(tmp_path, source, "TaxE: east", "TaxE: west")
    with pytest.raises(ConfigError, match="unknown state"):
        ConfigParser().parse(str(bad))


def test_discrete_trait_rejects_missing_taxon(tmp_path: Path) -> None:
    source = COVERAGE_DIR / "config_discrete_trait.yaml"
    bad = _mutated(tmp_path, source, "    TaxE: east\n", "")
    config = ConfigParser().parse(str(bad))
    with pytest.raises(ModelAssemblyError, match="no state supplied for taxon"):
        ModelAssembler(config).assemble()


def test_continuous_trait_rejects_missing_taxon(tmp_path: Path) -> None:
    source = COVERAGE_DIR / "config_continuous_trait.yaml"
    bad = _mutated(tmp_path, source, "    TaxE: 2.75\n", "")
    config = ConfigParser().parse(str(bad))
    with pytest.raises(ModelAssemblyError, match="no trait value supplied for taxon"):
        ModelAssembler(config).assemble()


def test_continuous_trait_rejects_non_numeric_value(tmp_path: Path) -> None:
    source = COVERAGE_DIR / "config_continuous_trait.yaml"
    bad = _mutated(tmp_path, source, "TaxE: 2.75", "TaxE: large")
    with pytest.raises(ConfigError, match="trait value must be numeric"):
        ConfigParser().parse(str(bad))
