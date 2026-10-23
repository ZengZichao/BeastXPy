"""Regression tests for skyride dimensions and sequence-error taxon filters."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from beastxpy.config import ConfigParser
from beastxpy.model import ModelAssembler, ModelAssemblyError
from beastxpy.validator import (
    GATE3_ENVIRONMENT_MISSING,
    find_java_runtime,
    lookup_jar_details,
    run_beast_validation,
    validate_xml_string,
)
from beastxpy.xml_writer import write_xml

COVERAGE_DIR = Path(__file__).resolve().parents[1] / "examples" / "coverage"
VERSIONS = ("1.10.4", "10.5.0")


pytestmark = pytest.mark.java


def _copy_assets(tmp_path: Path) -> None:
    for name in ("nuc.fasta",):
        shutil.copyfile(COVERAGE_DIR / name, tmp_path / name)


def _mutated(tmp_path: Path, name: str, old: str, new: str) -> Path:
    _copy_assets(tmp_path)
    source = COVERAGE_DIR / f"config_{name}.yaml"
    text = source.read_text(encoding="utf-8")
    assert old in text
    target = tmp_path / source.name
    target.write_text(text.replace(old, new), encoding="utf-8")
    return target


def _generate(path: Path, version: str) -> str:
    config = ConfigParser().parse(str(path))
    config.control.beast_version = version
    return write_xml(ModelAssembler(config).assemble(), beast_version=version)


def _assert_beast_ok(tmp_path: Path, xml: str, version: str, stem: str) -> None:
    runtime = find_java_runtime()
    if not runtime.usable:
        pytest.fail(f"no usable Java runtime: {runtime.reason}")
    if lookup_jar_details(version) is None:
        pytest.fail(f"no BEAST {version} jar found")
    path = tmp_path / f"{stem}_{version}.xml"
    path.write_text(xml, encoding="utf-8")
    result = run_beast_validation(str(path), beast_version=version, java_core=True)
    assert result.status != GATE3_ENVIRONMENT_MISSING, result.reason
    assert result.ok, f"{stem} {version}: {result.status}: {result.reason}"


@pytest.mark.parametrize("version", VERSIONS)
def test_skyride_default_dimension_is_beast_valid(tmp_path: Path, version: str) -> None:
    cfg = COVERAGE_DIR / "config_skyride.yaml"
    xml = _generate(cfg, version)
    # 5 taxa -> the GMRF requires exactly 4 population sizes.
    assert 'dimension="4"' in xml
    assert validate_xml_string(xml, beast_version=version) == []
    _assert_beast_ok(tmp_path, xml, version, "skyride_default")


def test_skyride_rejects_non_interval_dimension(tmp_path: Path) -> None:
    path = _mutated(tmp_path, "skyride", "population_sizes: 4", "population_sizes: 3")
    config = ConfigParser().parse(str(path))
    with pytest.raises(ModelAssemblyError, match="exactly one entry per coalescent interval"):
        ModelAssembler(config).assemble()


@pytest.mark.parametrize("version", VERSIONS)
def test_sequence_error_include_taxa_uses_include_wrapper(tmp_path: Path, version: str) -> None:
    path = _mutated(
        tmp_path,
        "sequence_error",
        "  partition: 1\n",
        "  partition: 1\n  include_taxa: [TaxA, TaxB]\n",
    )
    xml = _generate(path, version)
    assert "<include>" in xml
    assert '<taxa id="errorModel.taxa">' in xml
    assert '<taxa idref="errorModel.taxa"/>' in xml
    assert "<exclude>" not in xml
    assert validate_xml_string(xml, beast_version=version) == []
    _assert_beast_ok(tmp_path, xml, version, "seq_include")


@pytest.mark.parametrize("version", VERSIONS)
def test_sequence_error_exclude_taxa_uses_exclude_wrapper(tmp_path: Path, version: str) -> None:
    path = _mutated(
        tmp_path,
        "sequence_error",
        "  partition: 1\n",
        "  partition: 1\n  exclude_taxa: [TaxA]\n",
    )
    xml = _generate(path, version)
    assert "<exclude>" in xml
    assert '<taxa idref="errorModel.taxa"/>' in xml
    assert "<include>" not in xml
    _assert_beast_ok(tmp_path, xml, version, "seq_exclude")


def test_sequence_error_rejects_unknown_filter_taxon(tmp_path: Path) -> None:
    path = _mutated(
        tmp_path,
        "sequence_error",
        "  partition: 1\n",
        "  partition: 1\n  include_taxa: [NotATaxon]\n",
    )
    config = ConfigParser().parse(str(path))
    with pytest.raises(ValueError, match="unknown taxon"):
        ModelAssembler(config).assemble()
