"""Recombination/MDS coverage contract tests."""

from __future__ import annotations

from pathlib import Path

from beastxpy.config import ConfigParser
from beastxpy.coverage import mds as mds_plugin
from beastxpy.coverage import recombination as rec_plugin
from beastxpy.model import ModelAssembler
from beastxpy.validator import validate_xml_string
from beastxpy.xml_writer import write_xml

COVERAGE = Path(__file__).resolve().parents[1] / "examples" / "coverage"


def _generate(name: str, version: str) -> str:
    config = ConfigParser().parse(str(COVERAGE / f"config_{name}.yaml"))
    config.control.beast_version = version
    return write_xml(ModelAssembler(config).assemble(), beast_version=version)


def test_mds_generates_and_passes_gate1() -> None:
    xml = _generate("mds", "1.10.4")
    assert "<multidimensionalScaling" in xml
    findings = validate_xml_string(xml, beast_version="1.10.4")
    assert [f for f in findings if f.severity == "error"] == []


def test_mds_distance_table_is_a_portable_sidecar(tmp_path) -> None:
    from beastxpy.api import BeastXPy

    config_path = COVERAGE / "config_mds.yaml"
    config = ConfigParser().parse(str(config_path))
    xml = write_xml(ModelAssembler(config).assemble(), beast_version="1.10.4")
    assert 'fileName="mds_distances.txt"' in xml
    assert str(COVERAGE) not in xml, "the absolute fixture path must not enter the XML"

    output = tmp_path / "mds.xml"
    BeastXPy(force=True).generate_full(
        config,
        output=str(output),
        beast_version="1.10.4",
        beast_validate=False,
        allow_unvalidated=True,
        data_check=False,
        config_path=str(config_path),
    )
    assert output.is_file()
    copied = tmp_path / "mds_distances.txt"
    assert copied.is_file()
    assert copied.read_bytes() == (COVERAGE / "mds_distances.txt").read_bytes()
    assert 'fileName="mds_distances.txt"' in output.read_text(encoding="utf-8")


def test_mds_requires_distance_file() -> None:
    errors: list[str] = []
    mds_plugin.validate({"type": "mds", "distance_file": ""}, errors)
    assert any("distance_file" in error for error in errors)


def test_recombination_is_rejected_with_upstream_reason() -> None:
    errors: list[str] = []
    rec_plugin.validate({"type": "recombination", "partitions": 2}, errors)
    joined = "\n".join(errors)
    assert "argTreeLikelihood" in joined
    assert "legacy SiteModel" in joined
    assert "GammaSiteRateModel" in joined
    assert "Exactly one ELEMENT of type SiteModel REQUIRED" in joined
    assert "No opt-in legacy-parser mode" in joined
