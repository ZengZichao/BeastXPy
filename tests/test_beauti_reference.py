"""Element-level comparison with BEAUti-convention reference XML.

The references in ``tests/golden/beauti_reference/`` are hand-transcribed
subsets of what BEAUti 1.10.4 emits for the same model choices. They are
external truth in the sense that their values come from the BEAUti code paths
(and the BEAUti-generated ``examples/Benchmarks/benchmark1.xml`` shipped with
BEAST), not from this generator's output. Every deliberate difference is
asserted below as a documented deviation; nothing is silently skipped.

The references are not runnable BEAST documents (they omit ids/taxa); only the
model, prior and likelihood fragments are compared.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as DT
from pathlib import Path

import pytest

from beastxpy.config import ConfigParser
from beastxpy.model import ModelAssembler
from beastxpy.xml_writer import write_xml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "tests" / "fixtures" / "beauti_reference"
REFERENCE_DIR = ROOT / "tests" / "golden" / "beauti_reference"


def _generate(config_name: str) -> ET.Element:
    config = ConfigParser().parse(str(CONFIG_DIR / config_name))
    config.control.beast_version = "1.10.4"
    xml = write_xml(ModelAssembler(config).assemble(), beast_version="1.10.4")
    return DT.fromstring(xml)


def _reference(name: str) -> ET.Element:
    return DT.parse(REFERENCE_DIR / name).getroot()


def _prior_for(root: ET.Element, idref: str) -> ET.Element:
    """Return the prior element whose parameter/statistic refers to ``idref``."""
    found = None
    for prior in root.iter("prior"):
        for child in prior:
            if any(node.get("idref") == idref for node in child.iter()):
                assert found is None, f"more than one prior for {idref}"
                found = child
    assert found is not None, f"no prior for {idref}"
    return found


def _parameter(root: ET.Element, id_: str) -> ET.Element:
    found = [node for node in root.iter("parameter") if node.get("id") == id_]
    assert len(found) == 1, f"expected one parameter {id_}, found {len(found)}"
    return found[0]


def _same_value(actual, expected) -> bool:
    if actual == expected:
        return True
    try:
        return math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-15)
    except (TypeError, ValueError):
        return False


def _assert_attributes(actual: ET.Element, expected: ET.Element, attributes) -> None:
    for name in attributes:
        if name == "tag":
            assert (
                actual.tag == expected.tag
            ), f"tag: generated {actual.tag!r} != BEAUti reference {expected.tag!r}"
            continue
        assert _same_value(actual.get(name), expected.get(name)), (
            f"{actual.tag}[{name}]: generated {actual.get(name)!r} != "
            f"BEAUti reference {expected.get(name)!r}"
        )


class TestReferences:
    @pytest.mark.parametrize(
        "name",
        [
            "jc69_strict_yule_reference.xml",
            "hky_gamma_ucln_calibration_reference.xml",
            "gtr_partition_reference.xml",
        ],
    )
    def test_reference_is_well_formed_and_documents_deviations(self, name):
        path = REFERENCE_DIR / name
        DT.parse(path)  # must be well formed
        text = path.read_text(encoding="utf-8")
        assert "BEAUti-convention reference" in text
        assert "Documented deviations" in text

    def test_jc69_strict_yule_matches_beauti_conventions(self):
        reference = _reference("jc69_strict_yule_reference.xml")
        generated = _generate("config_jc69_strict_yule.yaml")

        # Yule birth rate prior: identical distribution and attributes.
        _assert_attributes(
            _prior_for(generated, "yule.birthRate"),
            _prior_for(reference, "yule.birthRate"),
            ("tag", "mean", "stdev", "offset", "meanInRealSpace"),
        )

        # JC69 is HKY with kappa fixed to 1 in both documents; no kappa prior.
        assert _parameter(generated, "kappa").get("value") == "1"
        assert _parameter(generated, "kappa").get("lower") is None
        with pytest.raises(AssertionError):
            _prior_for(generated, "kappa")

        # Strict-clock rate and gamma-shape parameter bounds.
        _assert_attributes(
            _parameter(generated, "clock.rate"),
            _parameter(reference, "clock.rate"),
            ("value", "lower"),
        )
        _assert_attributes(
            _parameter(generated, "siteModel.alpha"),
            _parameter(reference, "siteModel.alpha"),
            ("value", "lower"),
        )
        generated_shape = next(generated.iter("gammaShape"))
        reference_shape = next(reference.iter("gammaShape"))
        _assert_attributes(generated_shape, reference_shape, ("gammaCategories",))

        # The tree likelihood is wired to the same model objects.
        likelihood = next(generated.iter("treeLikelihood"))
        ref_likelihood = next(reference.iter("treeLikelihood"))
        assert {child.tag for child in likelihood} == {child.tag for child in ref_likelihood}

        # Documented deviations (comments in the reference file):
        # 1. BEAUti uses ctmcScalePrior for the clock rate; BeastXPy uses oneOnX.
        assert _prior_for(reference, "clock.rate").tag == "ctmcScalePrior"
        assert _prior_for(generated, "clock.rate").tag == "oneOnXPrior"
        # 2. BEAUti uses exponential(0.5) for the gamma shape.
        assert _prior_for(reference, "siteModel.alpha").tag == "exponentialPrior"
        assert _prior_for(generated, "siteModel.alpha").tag == "logNormalPrior"
        # 3. Both documents fix base frequencies at 0.25 (a match).
        assert _parameter(reference, "frequencies").get("value") == "0.25 0.25 0.25 0.25"
        generated_frequencies = _parameter(generated, "frequencies")
        assert generated_frequencies.get("value") == "0.25 0.25 0.25 0.25"
        assert generated_frequencies.get("lower") is None

    def test_hky_gamma_ucln_calibration_matches_beauti_conventions(self):
        reference = _reference("hky_gamma_ucln_calibration_reference.xml")
        generated = _generate("config_hky_gamma_ucln.yaml")

        # Kappa and relaxed-clock variance priors are exact BEAUti matches.
        _assert_attributes(
            _prior_for(generated, "kappa"),
            _prior_for(reference, "kappa"),
            ("tag", "mean", "stdev", "offset", "meanInRealSpace"),
        )
        generated_stdev = _prior_for(generated, "ucld.stdev")
        reference_stdev = _prior_for(reference, "ucld.stdev")
        assert generated_stdev.tag == reference_stdev.tag == "exponentialPrior"
        assert math.isclose(
            float(generated_stdev.get("mean")),
            float(reference_stdev.get("mean")),
            rel_tol=1e-12,
        )

        # UCLN distribution: meanInRealSpace=true and the same parameters.
        generated_ln = next(generated.iter("logNormalDistributionModel"))
        reference_ln = next(reference.iter("logNormalDistributionModel"))
        _assert_attributes(generated_ln, reference_ln, ("meanInRealSpace",))
        assert _parameter(generated, "ucld.mean").get("lower") == "0"
        assert _parameter(generated, "ucld.stdev").get("lower") == "0"

        # Gamma categories and calibration offset/stdev.
        _assert_attributes(
            next(generated.iter("gammaShape")),
            next(reference.iter("gammaShape")),
            ("gammaCategories",),
        )
        generated_cal = _prior_for(generated, "tmrca(Catarrhini)")
        reference_cal = _prior_for(reference, "tmrca(Catarrhini)")
        _assert_attributes(generated_cal, reference_cal, ("offset", "stdev"))

        # Documented deviations:
        # 1. calibration meanInRealSpace: BEAUti true, BeastXPy config false.
        assert reference_cal.get("meanInRealSpace") == "true"
        assert generated_cal.get("meanInRealSpace") == "false"
        # 2. alpha prior family.
        assert _prior_for(reference, "siteModel.alpha").tag == "exponentialPrior"
        assert _prior_for(generated, "siteModel.alpha").tag == "logNormalPrior"
        # 3. ucld.mean prior family.
        assert _prior_for(reference, "ucld.mean").tag == "ctmcScalePrior"
        assert _prior_for(generated, "ucld.mean").tag == "oneOnXPrior"
        # 4. base-frequency prior family.
        assert _prior_for(reference, "frequencies").tag == "dirichletPrior"
        assert _prior_for(generated, "frequencies").tag == "uniformPrior"

    def test_gtr_partition_matches_beauti_conventions(self):
        reference = _reference("gtr_partition_reference.xml")
        generated = _generate("config_gtr_partition.yaml")

        # Both partitions carry a GTR model with five rateXX values and a
        # frequency model, and both tree likelihoods reference one clock.
        generated_gtr = [model for model in generated.iter("gtrModel") if model.get("id")]
        assert {model.get("id") for model in generated_gtr} == {"gtr", "gene2..gtr"}
        for model in generated_gtr:
            assert [child.tag for child in model].count("rateAC") == 1 and len(
                [child for child in model if child.tag.startswith("rate")]
            ) == 5
            assert any(child.tag == "frequencies" for child in model)

        likelihoods = [lk for lk in generated.iter("treeLikelihood") if lk.get("id")]
        assert len(likelihoods) == 2
        assert (
            len(
                {
                    next(node.get("idref") for node in lk.iter("discretizedBranchRates"))
                    for lk in likelihoods
                }
            )
            == 1
        )

        # Rate parameter bounds (BEAUti leaves the upper bound implicit).
        for rate in ("rateAC", "rateAG", "rateAT", "rateCG", "rateCT"):
            _assert_attributes(
                _parameter(generated, rate),
                _parameter(reference, rate),
                ("value", "lower"),
            )
            assert _parameter(generated, rate).get("upper") == "100"
            assert _parameter(reference, rate).get("upper") is None

        # UCLN block and gamma categories are structurally identical.
        _assert_attributes(
            next(generated.iter("logNormalDistributionModel")),
            next(reference.iter("logNormalDistributionModel")),
            ("meanInRealSpace",),
        )
        _assert_attributes(
            next(generated.iter("gammaShape")),
            next(reference.iter("gammaShape")),
            ("gammaCategories",),
        )

        # Documented deviations:
        # 1. GTR rate priors: BEAUti gamma(0.05, 20), BeastXPy uniform(0, 100).
        assert _prior_for(reference, "rateAC").tag == "gammaPrior"
        assert _prior_for(generated, "rateAC").tag == "uniformPrior"
        # 2. alpha prior family (both partitions).
        for alpha in ("siteModel.alpha", "gene2..siteModel.alpha"):
            assert _prior_for(reference, alpha).tag == "exponentialPrior"
            assert _prior_for(generated, alpha).tag == "logNormalPrior"
        # 3. ucld.mean prior family.
        assert _prior_for(reference, "ucld.mean").tag == "ctmcScalePrior"
        assert _prior_for(generated, "ucld.mean").tag == "oneOnXPrior"
        # 4. base-frequency prior family.
        assert _prior_for(reference, "frequencies").tag == "dirichletPrior"
        assert _prior_for(generated, "frequencies").tag == "uniformPrior"
