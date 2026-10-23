"""Regression tests for the C07/C09 codon, Dollo and AA-mixture plugins.

These tests cover both directions of each plugin contract:

* the structure-tree output declares every object it references and keeps the
  Gate-1 (``validate_xml_string``) checks clean, and
* Gate-0 validation rejects obviously invalid parameters instead of emitting
  an unparsable BEAST document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from beastxpy.config import ConfigParser
from beastxpy.coverage import aa_mixture as aa_plugin
from beastxpy.coverage import codon as codon_plugin
from beastxpy.coverage import dollo as dollo_plugin
from beastxpy.model import ModelAssembler
from beastxpy.validator import validate_xml_string
from beastxpy.xml_writer import write_xml

COVERAGE_DIR = Path(__file__).resolve().parents[1] / "examples" / "coverage"


def _generate(name: str, version: str) -> str:
    config = ConfigParser().parse(str(COVERAGE_DIR / f"config_{name}.yaml"))
    config.control.beast_version = version
    return write_xml(ModelAssembler(config).assemble(), beast_version=version)


def _validate(plugin: Any, raw: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    plugin.validate(raw, errors)
    return errors


# -- AA mixture -------------------------------------------------------------


def test_aa_mixture_declares_the_mixture_object() -> None:
    xml = _generate("aa_mixture", "10.5.0")

    assert '<instantaneousMixtureSubstitutionModel id="aaMixture" ' 'dataType="amino acid">' in xml
    assert "<rootFrequencies>" in xml
    assert '<aminoAcidModel id="aaMixture.component0" type="WAG"/>' in xml
    assert '<aminoAcidModel id="aaMixture.component1" type="JTT"/>' in xml
    assert "<mixtureWeights>" in xml
    assert '<instantaneousMixtureSubstitutionModel idref="aaMixture"/>' in xml

    findings = validate_xml_string(xml, beast_version="10.5.0")
    assert findings == [], findings


def test_aa_mixture_rejects_bad_components_and_weight() -> None:
    errors = _validate(
        aa_plugin,
        {
            "type": "aa_mixture",
            "beast_version": "10.5.0",
            "components": ["wag", "nonsense"],
            "weight": 1.5,
        },
    )
    joined = "\n".join(errors)
    assert "components[1]" in joined
    assert "weight" in joined

    errors = _validate(
        aa_plugin,
        {"type": "aa_mixture", "beast_version": "1.10.4"},
    )
    assert any("beast_version" in error for error in errors)


# -- codon / SRD06 ----------------------------------------------------------


def test_codon_is_rejected_due_to_upstream_parser_defect() -> None:
    errors = _validate(codon_plugin, {"type": "codon", "genetic_code": "universal"})
    joined = "\n".join(errors)
    assert "cannot apply" in joined
    assert "Unhandled attribute" in joined
    assert "codon-<geneticCode>" in joined
    assert "no legacy shim is enabled by default" in joined


def test_codon_rejects_bad_genetic_code_and_omega() -> None:
    errors = _validate(
        codon_plugin,
        {
            "type": "srd06",
            "genetic_code": "klingon",
            "omega": 0.0,
        },
    )
    joined = "\n".join(errors)
    assert "genetic_code" in joined
    assert "omega" in joined

    errors = _validate(codon_plugin, {"type": "srd06", "omega": 1.0})
    assert any("only used by analysis.type 'codon'" in error for error in errors)


# -- Dollo ------------------------------------------------------------------


def test_dollo_uses_released_site_model_and_likelihood() -> None:
    xml = _generate("dollo", "1.10.4")

    assert '<alignment id="nuc" dataType="binary">' in xml
    assert '<mutationDeathModel id="dolloModel">' in xml
    assert '<siteModel id="dolloSiteModel">' in xml
    assert '<siteModel idref="dolloSiteModel"/>' in xml
    assert '<treeLikelihood id="dolloLikelihood" useAmbiguities="true">' in xml
    assert '<treeLikelihood idref="dolloLikelihood"/>' in xml
    assert "alsSiteModel" not in xml
    assert "alsTreeLikelihood" not in xml
    assert "exponentialBranchLengthsPrior" not in xml

    findings = validate_xml_string(xml, beast_version="1.10.4")
    assert findings == [], findings


def test_dollo_rejects_bad_state_rate_and_site_limit() -> None:
    errors = _validate(
        dollo_plugin,
        {
            "type": "dollo",
            "derived_state": "Z",
            "death_rate": 0.0,
            "max_sites": 1,
        },
    )
    joined = "\n".join(errors)
    assert "derived_state" in joined
    assert "death_rate" in joined
    assert "max_sites" in joined
