"""Generation and Gate-0 tests for BEAST X 10.5 / StarBEAST coverage.

Fixtures live in ``examples/coverage``; the official-jar checks are exercised
by ``tools/official_validate.py`` (both 1.10.4 and 10.5.0 for ``starbeast``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from beastxpy.config import ConfigError, ConfigParser
from beastxpy.coverage import supported_analysis_types, validate_analysis
from beastxpy.model import ModelAssembler, ModelAssemblyError
from beastxpy.xml_writer import write_xml

FIXTURES = Path(__file__).resolve().parent.parent / "examples" / "coverage"
DATA = FIXTURES / "nuc.fasta"


def _write_config(tmp_path: Path, name: str, mutate=None) -> Path:
    raw = yaml.safe_load((FIXTURES / f"config_{name}.yaml").read_text(encoding="utf-8"))
    raw["alignments"][0]["file"] = str(DATA)
    if mutate is not None:
        mutate(raw)
    target = tmp_path / f"config_{name}.yaml"
    target.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return target


def _generate(path: Path, version: str) -> str:
    config = ConfigParser().parse(str(path))
    config.control.beast_version = version
    return write_xml(ModelAssembler(config).assemble(), beast_version=version)


def _elements(xml: str) -> set:
    import xml.etree.ElementTree as ET

    return {element.tag for element in ET.fromstring(xml).iter()}


class TestPluginDiscovery:
    def test_types_registered(self):
        types = supported_analysis_types()
        assert "age_statistic" in types
        assert "tree_report" in types
        assert "starbeast" in types


class TestAgeStatistic:
    def test_generation(self, tmp_path):
        xml = _generate(_write_config(tmp_path, "age_statistic"), "10.5.0")
        tags = _elements(xml)
        assert "ageStatistic" in tags
        assert "treeHeightStatistic" in tags
        assert 'idref="treeReport.age"' in xml
        # The age statistic must be logged (screen + trace), not just declared.
        assert xml.count('<ageStatistic idref="treeReport.age"') == 2

    def test_rejects_1_10_4(self, tmp_path):
        path = _write_config(
            tmp_path,
            "age_statistic",
            lambda raw: raw["analysis"].update({"beast_version": "1.10.4"}),
        )
        with pytest.raises(ConfigError, match="10.5.0"):
            ConfigParser().parse(str(path))

    def test_rejects_1_10_4_at_assembly(self, tmp_path):
        # Gate 0 only sees analysis.beast_version; the control block is a
        # second, independent gate.
        path = _write_config(tmp_path, "age_statistic")
        config = ConfigParser().parse(str(path))
        config.control.beast_version = "1.10.4"
        with pytest.raises(ModelAssemblyError, match="10.5.0"):
            ModelAssembler(config).assemble()

    def test_requires_tip_dates(self, tmp_path):
        path = _write_config(tmp_path, "age_statistic", lambda raw: raw.pop("tip_dates"))
        config = ConfigParser().parse(str(path))
        with pytest.raises(ModelAssemblyError, match="tip_dates"):
            ModelAssembler(config).assemble()

    def test_validate_message(self):
        errors: list[str] = []
        validate_analysis(
            "age_statistic",
            {"type": "age_statistic", "beast_version": "1.10.4"},
            errors,
        )
        assert errors and "10.5.0" in errors[0]
        assert "ageStatistic" in errors[0]


class TestTreeReport:
    def test_generation(self, tmp_path):
        xml = _generate(_write_config(tmp_path, "tree_report"), "10.5.0")
        tags = _elements(xml)
        assert "branchLengthStatistic" in tags
        assert "mrca" in tags
        assert 'id="treeReport.taxa"' in xml
        assert "TaxA" in xml and "TaxB" in xml

    def test_rejects_1_10_4(self, tmp_path):
        path = _write_config(
            tmp_path,
            "tree_report",
            lambda raw: raw["analysis"].update({"beast_version": "1.10.4"}),
        )
        with pytest.raises(ConfigError, match="10.5.0"):
            ConfigParser().parse(str(path))

    def test_rejects_unknown_mrca_taxon(self, tmp_path):
        path = _write_config(
            tmp_path,
            "tree_report",
            lambda raw: raw["analysis"].update({"mrca_taxa": ["TaxA", "Nope"]}),
        )
        config = ConfigParser().parse(str(path))
        with pytest.raises(ModelAssemblyError, match="Nope"):
            ModelAssembler(config).assemble()

    def test_validate_message(self):
        errors: list[str] = []
        validate_analysis(
            "tree_report",
            {"type": "tree_report", "beast_version": "1.10.4"},
            errors,
        )
        assert errors and "10.5.0" in errors[0]
        assert "branchLengthStatistic" in errors[0]


class TestStarBeast:
    @pytest.mark.parametrize("version", ["1.10.4", "10.5.0"])
    def test_generation(self, tmp_path, version):
        xml = _generate(_write_config(tmp_path, "starbeast"), version)
        tags = _elements(xml)
        for tag in (
            "species",
            "sp",
            "speciesTree",
            "speciesCoalescent",
            "nodeReHeight",
            "newick",
            "sppSplitPopulations",
        ):
            assert tag in tags
        assert 'id="speciesCoalescent"' in xml
        assert '<speciesCoalescent idref="speciesCoalescent"/>' in xml
        assert 'idref="speciesTree"' in xml

    def test_rejects_unmapped_taxon(self, tmp_path):
        path = _write_config(
            tmp_path,
            "starbeast",
            lambda raw: raw["analysis"]["species"].pop("TaxE"),
        )
        config = ConfigParser().parse(str(path))
        with pytest.raises(ModelAssemblyError, match="TaxE"):
            ModelAssembler(config).assemble()

    def test_rejects_both_versions(self, tmp_path):
        path = _write_config(
            tmp_path,
            "starbeast",
            lambda raw: raw["control"].update({"beast_version": "both"}),
        )
        config = ConfigParser().parse(str(path))
        with pytest.raises(ModelAssemblyError, match="exactly"):
            ModelAssembler(config).assemble()
