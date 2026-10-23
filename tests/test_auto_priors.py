"""Automatic-prior transparency: XML, Gate 2 and report."""

from __future__ import annotations

from pathlib import Path

from beastxpy.api import BeastXPy
from beastxpy.config import ConfigParser
from beastxpy.model import ModelAssembler
from beastxpy.report import collect_report_data, render_html, to_envelope
from beastxpy.xml_writer import write_xml

ROOT = Path(__file__).resolve().parent.parent
BASIC = ROOT / "examples" / "config_basic.yaml"


def _generate_xml() -> tuple[str, dict]:
    config = ConfigParser().parse(str(BASIC))
    config.control.beast_version = "1.10.4"
    assembler = ModelAssembler(config)
    xml = write_xml(assembler.assemble(), beast_version="1.10.4")
    return xml, assembler.params.auto_priors


def test_xml_comments_name_parameter_distribution_and_provenance():
    xml, auto_priors = _generate_xml()
    assert auto_priors
    assert xml.count("BXP auto prior") == len(auto_priors)
    for parameter, info in auto_priors.items():
        assert f"parameter={parameter};" in xml
        assert f"distribution={info['distribution']};" in xml
        assert info["source"] in xml
    # BEAUti-verified defaults and explicit BeastXPy choices are distinguished.
    assert auto_priors["yule.birthRate"]["source"] == "BEAUti-1.10.4 convention"
    assert auto_priors["siteModel.alpha"]["source"].startswith("BeastXPy convention")
    assert auto_priors["frequencies"]["source"].startswith("BeastXPy convention")


def test_gate2_lists_every_auto_prior(tmp_path):
    config = ConfigParser().parse(str(BASIC))
    result = BeastXPy(force=True).generate_full(
        config,
        output=str(tmp_path / "basic.xml"),
        beast_version="1.10.4",
        beast_validate=False,
        allow_unvalidated=True,
        data_check=False,
        config_path=str(BASIC),
    )
    findings = [f for f in result.gate2_findings if getattr(f, "code", "") == "AUTO_PRIOR"]
    assert {f.where for f in findings} == set(result.auto_priors)
    for finding in findings:
        assert finding.severity == "info"
        assert "override under parameter_priors" in finding.message
        assert result.auto_priors[finding.where]["distribution"] in finding.message


def test_report_has_automatic_prior_section(tmp_path):
    config = ConfigParser().parse(str(BASIC))
    BeastXPy(force=True).generate_full(
        config,
        output=str(tmp_path / "basic.xml"),
        beast_version="1.10.4",
        beast_validate=False,
        allow_unvalidated=True,
        data_check=False,
        config_path=str(BASIC),
    )
    data = collect_report_data(tmp_path, with_plot=False)
    assert data["auto_priors"]
    html = render_html(data)
    assert "Automatic priors" in html
    assert "BXP auto prior" in html
    envelope = to_envelope(data)
    assert len(envelope["auto_priors"]) == len(data["auto_priors"])
    assert {item["parameter"] for item in envelope["auto_priors"]} == {
        "frequencies",
        "siteModel.alpha",
        "yule.birthRate",
    }
