"""Tests for the ancestral-state (C03) and empirical-tree (C06) plugins."""

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
    assert config.control.beast_version == "1.10.4"
    return write_xml(ModelAssembler(config).assemble(), beast_version="1.10.4")


def _mutated(tmp_path: Path, source: Path, old: str, new: str) -> Path:
    text = source.read_text(encoding="utf-8")
    assert old in text
    for name in ("nuc.fasta", "empirical_trees.nexus"):
        shutil.copyfile(COVERAGE_DIR / name, tmp_path / name)
    target = tmp_path / source.name
    target.write_text(text.replace(old, new), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# C03 - ancestral state reconstruction
# ---------------------------------------------------------------------------
def test_ancestral_generates_coherent_xml() -> None:
    xml = _generate(COVERAGE_DIR / "config_ancestral.yaml")

    assert '<patterns id="patterns" from="1" to="66" unique="false">' in xml
    assert '<ancestralTreeLikelihood id="ancestralLikelihood" stateTagName="states">' in xml
    assert '<trait name="states" tag="states">' in xml
    assert '<ancestralTreeLikelihood idref="ancestralLikelihood"/>' in xml

    # The core likelihood stays the data likelihood; the ancestral object is
    # only a trait provider and must not be added to the joint likelihood.
    assert '<likelihood id="likelihood">' in xml
    assert xml.count('<treeLikelihood idref="treeLikelihood"/>') == 1
    assert xml.count('<ancestralTreeLikelihood idref="ancestralLikelihood"/>') == 1

    findings = validate_xml_string(xml)
    assert findings == [], findings


def test_ancestral_map_mode_is_rejected_upstream(tmp_path: Path) -> None:
    # The 10.5 parser reads useMAP but omits it from its syntax rules, so the
    # strict official parser rejects the attribute as unhandled.
    source = COVERAGE_DIR / "config_ancestral.yaml"
    mapped = _mutated(tmp_path, source, "  states: all", "  states: map")
    with pytest.raises(ConfigError, match="useMAP"):
        ConfigParser().parse(str(mapped))


def test_ancestral_rejects_unknown_state_mode(tmp_path: Path) -> None:
    source = COVERAGE_DIR / "config_ancestral.yaml"
    bad = _mutated(tmp_path, source, "  states: all", "  states: tips")
    with pytest.raises(ConfigError, match="unknown reconstruction mode"):
        ConfigParser().parse(str(bad))


def test_ancestral_rejects_unknown_option(tmp_path: Path) -> None:
    source = COVERAGE_DIR / "config_ancestral.yaml"
    bad = _mutated(
        tmp_path, source, "  state_tag_name: states\n", "  state_tag_name: states\n  window: 3\n"
    )
    with pytest.raises(ConfigError, match="unknown option"):
        ConfigParser().parse(str(bad))


# ---------------------------------------------------------------------------
# C06 - empirical tree distribution
# ---------------------------------------------------------------------------
def test_empirical_tree_generates_coherent_xml() -> None:
    xml = _generate(COVERAGE_DIR / "config_empirical_tree.yaml")

    assert '<empiricalTreeDistributionModel id="treeModel"' in xml
    assert 'fileName="' in xml and "empirical_trees.nexus" in xml
    assert 'startingTree="0"' in xml
    assert '<taxa idref="taxa"/>' in xml
    assert '<empiricalTreeDistributionOperator weight="1" metropolisHastings="true">' in xml
    assert '<statistic id="treeModel.currentTree" name="Current Tree">' in xml
    assert '<statistic idref="treeModel.currentTree"/>' in xml

    # The fixed tree no longer receives topology/height operators, and the
    # removed height parameters are not logged any more.
    for tag in (
        "subtreeSlide",
        "narrowExchange",
        "wideExchange",
        "wilsonBalding",
        "upDownOperator",
    ):
        assert f"<{tag}" not in xml
    assert '<parameter idref="treeModel.rootHeight"/>' not in xml

    assert '<treeLikelihood idref="treeLikelihood"/>' in xml
    assert xml.count('<treeModel idref="treeModel"/>') >= 2  # likelihood + tree log

    findings = validate_xml_string(xml)
    assert findings == [], findings


def test_empirical_tree_rejects_missing_tree_set(tmp_path: Path) -> None:
    source = COVERAGE_DIR / "config_empirical_tree.yaml"
    bad = _mutated(
        tmp_path,
        source,
        "tree_file: empirical_trees.nexus",
        "tree_file: absent_trees.nexus",
    )
    config = ConfigParser().parse(str(bad))
    with pytest.raises(ModelAssemblyError, match="tree set not found"):
        ModelAssembler(config).assemble()


def test_empirical_tree_rejects_non_nexus_tree_set(tmp_path: Path) -> None:
    source = COVERAGE_DIR / "config_empirical_tree.yaml"
    bad = _mutated(
        tmp_path,
        source,
        "tree_file: empirical_trees.nexus",
        "tree_file: bad_trees.nexus",
    )
    (tmp_path / "bad_trees.nexus").write_text("not a tree set\n", encoding="utf-8")
    config = ConfigParser().parse(str(bad))
    with pytest.raises(ModelAssemblyError, match="not a NEXUS file"):
        ModelAssembler(config).assemble()


def test_empirical_tree_rejects_unknown_option(tmp_path: Path) -> None:
    source = COVERAGE_DIR / "config_empirical_tree.yaml"
    bad = _mutated(
        tmp_path,
        source,
        "  metropolis_hastings: true\n",
        "  metropolis_hastings: true\n  burnin: 100\n",
    )
    with pytest.raises(ConfigError, match="unknown option"):
        ConfigParser().parse(str(bad))
