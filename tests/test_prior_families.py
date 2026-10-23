"""Regression tests for the prior families.

Covers the remaining major prior families:

* root calibrations (``taxa: null``) reuse the global ``<taxa>`` set
  and produce exactly one statistic, prior and starting-tree constraint;
* ``mcmc.pre_burnin`` / ``mcmc.operator_weights`` reach the XML and
  are accepted by the official BEAST jars;
* ``calibration.children_of`` returns the direct clade children;
* skyline / EBSP vector dimensions follow the configuration;
* distribution offsets, explicit per-partition
  priors, integer/category priors and UCLN rate categories;
* logger rejection and tree-log rate traits.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from beastxpy.calibration import children_of, distribution_model_fragment
from beastxpy.config import ConfigError, ConfigParser
from beastxpy.model import ModelAssembler, ModelAssemblyError
from beastxpy.models import CalibrationPoint, DistributionConfig
from beastxpy.validator import (
    GATE3_ENVIRONMENT_MISSING,
    find_java_runtime,
    lookup_jar_details,
    run_beast_validation,
)
from beastxpy.xml_writer import write_xml

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
VERSIONS = ("1.10.4", "10.5.0")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _basic_payload():
    """A basic example without calibrations or explicit parameter priors."""
    data = yaml.safe_load((EXAMPLES / "config_basic.yaml").read_text(encoding="utf-8"))
    data["alignments"][0]["file"] = str(EXAMPLES / "primates.fasta")
    data["calibrations"] = []
    data["parameter_priors"] = []
    return data


def _write_yaml(tmp_path: Path, name: str, data) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _generate(config_path: Path, version: str):
    config = ConfigParser().parse(str(config_path))
    config.control.beast_version = version
    xml = write_xml(ModelAssembler(config).assemble(), beast_version=version)
    return xml, config


def _assert_beast_ok(tmp_path: Path, xml: str, version: str, stem: str) -> None:
    """Run the official BEAST jar; never skip a missing environment silently."""
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


def _root_calibration_payload():
    data = _basic_payload()
    data["calibrations"] = [
        {
            "name": "RootCal",
            "taxa": None,
            "monophyletic": True,
            "distribution": {
                "type": "lognormal",
                "parameters": {"mean": 1.0, "stdev": 0.5},
                "offset": 20.0,
            },
        }
    ]
    return data


def _find_parameter(root: ET.Element, param_id: str) -> ET.Element:
    matches = [node for node in root.iter("parameter") if node.get("id") == param_id]
    assert len(matches) == 1, f"expected one parameter {param_id!r}, got {len(matches)}"
    return matches[0]


# ---------------------------------------------------------------------------
# 1. root calibration
# ---------------------------------------------------------------------------


@pytest.mark.java
@pytest.mark.parametrize("version", VERSIONS)
def test_root_calibration_single_statistic_prior_and_constraint(tmp_path, version):
    config_path = _write_yaml(tmp_path, "rootcal.yaml", _root_calibration_payload())
    xml, _ = _generate(config_path, version)
    root = ET.fromstring(xml)

    stats = [node for node in root.iter("tmrcaStatistic") if node.get("id") == "tmrca(RootCal)"]
    assert len(stats) == 1
    assert [node.get("idref") for node in stats[0].iter("taxa")] == ["taxa"]

    # Exactly one prior element references the root statistic.
    prior_refs = [
        node
        for node in root.iter()
        if any(
            child.tag == "statistic" and child.get("idref") == "tmrca(RootCal)"
            for child in list(node)
        )
    ]
    assert len(prior_refs) == 1
    assert prior_refs[0].tag == "logNormalPrior"
    assert prior_refs[0].get("offset") == "20"
    assert [node.get("idref") for node in prior_refs[0].iter("statistic")] == ["tmrca(RootCal)"]

    # Root calibration reuses the global taxa set: no duplicate taxa id.
    taxa_ids = [node.get("id") for node in root.iter("taxa") if node.get("id")]
    assert taxa_ids == ["taxa"]

    # Exactly one starting-tree definition and one reference to it; its root
    # age is constrained beyond the calibration offset.
    starts = [node for node in root.iter() if node.get("id") == "startingTree"]
    assert len(starts) == 1
    start = starts[0]
    if start.tag == "coalescentSimulator":
        assert float(start.get("height")) > 20.0
    else:
        assert start.tag == "coalescentTree"
        constraints = [node for node in start.iter("tmrca")]
        assert len(constraints) == 1
        assert [node.get("idref") for node in constraints[0].iter("taxa")] == ["taxa"]
    assert len([node for node in root.iter() if node.get("idref") == "startingTree"]) == 1

    # The trace log carries the root statistic column exactly once.
    logs = [node for node in root.iter("log")]
    assert len(logs) == 2
    trace_columns = [
        node
        for log in logs
        if log.get("id") == "fileLog"
        for node in log.iter("tmrcaStatistic")
        if node.get("idref") == "tmrca(RootCal)"
    ]
    assert len(trace_columns) == 1
    assert (
        len([node for node in root.iter("tmrcaStatistic") if node.get("idref") == "tmrca(RootCal)"])
        == 1
    )

    _assert_beast_ok(tmp_path, xml, version, "rootcal")


# ---------------------------------------------------------------------------
# 2. preBurnin and operator weights
# ---------------------------------------------------------------------------


@pytest.mark.java
@pytest.mark.parametrize("version", VERSIONS)
def test_pre_burnin_reaches_xml_and_beast(tmp_path, version):
    data = _basic_payload()
    data["mcmc"] = {"chain_length": 1000000, "pre_burnin": 100}
    config_path = _write_yaml(tmp_path, "preburnin.yaml", data)
    xml, _ = _generate(config_path, version)
    root = ET.fromstring(xml)
    mcmc = root.find("mcmc")
    assert mcmc is not None
    assert mcmc.get("preBurnin") == "100"
    assert mcmc.get("chainLength") == "1000000"
    _assert_beast_ok(tmp_path, xml, version, "preburnin")


def test_operator_weights_change_named_operator(tmp_path):
    data = _basic_payload()
    data["mcmc"] = {
        "chain_length": 1000000,
        "operator_weights": {"kappa": 2.5, "subtreeSlide": 7.0},
    }
    config_path = _write_yaml(tmp_path, "weights.yaml", data)
    xml, _ = _generate(config_path, "1.10.4")
    root = ET.fromstring(xml)
    operators = list(root.find("operators"))
    kappa_ops = [
        op for op in operators if any(ref.get("idref") == "kappa" for ref in op.iter("parameter"))
    ]
    assert len(kappa_ops) == 1
    assert kappa_ops[0].get("weight") == "2.5"
    slide = [op for op in operators if op.tag == "subtreeSlide"]
    assert len(slide) == 1 and slide[0].get("weight") == "7"


def test_unmatched_operator_weight_is_a_clear_error(tmp_path):
    data = _basic_payload()
    data["mcmc"] = {
        "chain_length": 1000000,
        "operator_weights": {"no.such.parameter": 2.0},
    }
    config_path = _write_yaml(tmp_path, "bad_weights.yaml", data)
    with pytest.raises(ModelAssemblyError, match="does not match any"):
        _generate(config_path, "1.10.4")


# ---------------------------------------------------------------------------
# 3. children_of
# ---------------------------------------------------------------------------


def _clade_pool():
    return [
        CalibrationPoint(name="root", taxa=None),
        CalibrationPoint(name="A", taxa=["a", "b", "c", "d"]),
        CalibrationPoint(name="B", taxa=["a", "b"]),
        CalibrationPoint(name="C", taxa=["a", "b", "c"]),
        CalibrationPoint(name="D", taxa=["e", "f"]),
    ]


def test_children_of_returns_direct_clade_children():
    pool = _clade_pool()
    by_name = {cal.name: cal for cal in pool}
    assert children_of(by_name["root"], pool) == ["A", "D"]
    assert children_of(by_name["A"], pool) == ["C"]
    assert children_of(by_name["C"], pool) == ["B"]
    assert children_of(by_name["B"], pool) == []
    assert children_of(by_name["D"], pool) == []
    # A stub returning [] would pass none of the three non-empty assertions.


def test_children_of_uses_attached_calibration_context():
    pool = _clade_pool()
    for cal in pool:
        cal.calibration_context = tuple(pool)
    by_name = {cal.name: cal for cal in pool}
    assert children_of(by_name["root"]) == ["A", "D"]
    assert children_of(by_name["A"]) == ["C"]


# ---------------------------------------------------------------------------
# 4. skyline / EBSP dimensions
# ---------------------------------------------------------------------------


def test_skyline_dimensions_follow_config(tmp_path):
    data = _basic_payload()
    data["tree_prior"] = {
        "type": "skyline",
        "population_size": {"value": 1.0, "lower": 0.0, "estimate": True},
        "skyline_groups": 5,
        "skyline_pop_sizes": [1.0, 2.0, 3.0, 4.0, 5.0],
    }
    config_path = _write_yaml(tmp_path, "skyline.yaml", data)
    xml, _ = _generate(config_path, "1.10.4")
    root = ET.fromstring(xml)
    pops = _find_parameter(root, "skyline.popSize")
    groups = _find_parameter(root, "skyline.groupSize")
    assert pops.get("dimension") == "5"
    assert len(pops.get("value").split()) == 5
    assert groups.get("dimension") == "5"


def test_ebsp_dimensions_follow_config(tmp_path):
    data = _basic_payload()
    data["tree_prior"] = {
        "type": "ebsp",
        "ebsp_pop_sizes": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    }
    config_path = _write_yaml(tmp_path, "ebsp.yaml", data)
    xml, _ = _generate(config_path, "1.10.4")
    root = ET.fromstring(xml)
    pops = _find_parameter(root, "demographic.popSize")
    indicators = _find_parameter(root, "demographic.indicators")
    assert pops.get("dimension") == "6"
    assert len(pops.get("value").split()) == 6
    assert indicators.get("dimension") == "5"


# ---------------------------------------------------------------------------
# 5. spot checks
# ---------------------------------------------------------------------------


def test_distribution_offsets_supported_and_rejected(tmp_path):
    # Gamma supports an offset in both the prior and the model fragment.
    gamma = DistributionConfig(type="gamma", parameters={"shape": 2.0, "scale": 1.0}, offset=5.0)
    model = distribution_model_fragment(gamma)
    assert model["attrib"]["offset"] == "5"

    data = _basic_payload()
    data["calibrations"] = [
        {
            "name": "GammaCal",
            "taxa": ["Homo_sapiens", "Pan"],
            "monophyletic": True,
            "distribution": {
                "type": "gamma",
                "parameters": {"shape": 2.0, "scale": 1.0},
                "offset": 5.0,
            },
        }
    ]
    config_path = _write_yaml(tmp_path, "gamma_offset.yaml", data)
    xml, _ = _generate(config_path, "1.10.4")
    root = ET.fromstring(xml)
    priors = [node for node in root.iter("gammaPrior")]
    assert len(priors) == 1 and priors[0].get("offset") == "5"

    # Normal and uniform have no BEAST offset attribute; Gate 0 rejects them.
    for kind, params in (
        ("normal", {"mean": 1.0, "stdev": 0.5}),
        ("uniform", {"lower": 1.0, "upper": 5.0}),
    ):
        bad = _basic_payload()
        bad["calibrations"] = [
            {
                "name": "BadCal",
                "taxa": ["Homo_sapiens", "Pan"],
                "monophyletic": True,
                "distribution": {"type": kind, "parameters": params, "offset": 5.0},
            }
        ]
        path = _write_yaml(tmp_path, f"bad_{kind}.yaml", bad)
        with pytest.raises(ConfigError, match="no BEAST offset attribute"):
            ConfigParser().parse(str(path))


def test_per_partition_explicit_parameter_priors_resolve(tmp_path):
    data = yaml.safe_load((EXAMPLES / "config_multi_partition.yaml").read_text(encoding="utf-8"))
    data["alignments"][0]["file"] = str(EXAMPLES / "primates.fasta")
    data["parameter_priors"] = [
        {
            "parameter": "gene1.kappa",
            "distribution": {
                "type": "uniform",
                "parameters": {"lower": 0.0, "upper": 50.0},
            },
        },
        {
            "parameter": "gene2.siteModel.alpha",
            "distribution": {"type": "exponential", "parameters": {"mean": 2.0}},
        },
    ]
    config_path = _write_yaml(tmp_path, "partition_priors.yaml", data)
    xml, _ = _generate(config_path, "1.10.4")
    root = ET.fromstring(xml)
    kappa_priors = [
        node
        for node in root.iter("uniformPrior")
        if any(ref.get("idref") == "kappa" for ref in node.iter("parameter"))
    ]
    assert len(kappa_priors) == 1
    assert kappa_priors[0].get("upper") == "50"
    alpha_priors = [
        node
        for node in root.iter("exponentialPrior")
        if any(ref.get("idref") == "gene2..siteModel.alpha" for ref in node.iter("parameter"))
    ]
    assert len(alpha_priors) == 1
    assert alpha_priors[0].get("mean") == "2"

    bad = dict(data)
    bad["parameter_priors"] = [
        {
            "parameter": "gene1.no_such_parameter",
            "distribution": {
                "type": "uniform",
                "parameters": {"lower": 0.0, "upper": 1.0},
            },
        }
    ]
    bad_path = _write_yaml(tmp_path, "bad_partition_prior.yaml", bad)
    with pytest.raises(ModelAssemblyError, match="does not match any parameter"):
        _generate(bad_path, "1.10.4")


def test_no_one_on_x_for_integer_category_or_tree_parameters(tmp_path):
    skyline = _basic_payload()
    skyline["tree_prior"] = {
        "type": "skyline",
        "population_size": {"value": 1.0, "lower": 0.0, "estimate": True},
        "skyline_groups": 5,
        "skyline_pop_sizes": [1.0, 2.0, 3.0, 4.0, 5.0],
    }
    xml, _ = _generate(_write_yaml(tmp_path, "skyline_oneonx.yaml", skyline), "1.10.4")
    root = ET.fromstring(xml)
    targets = {
        ref.get("idref") for prior in root.iter("oneOnXPrior") for ref in prior.iter("parameter")
    }
    assert "skyline.groupSize" not in targets
    assert not any(target.startswith("treeModel.") for target in targets)
    assert "clock.rate" in targets  # continuous, allowed

    multi = yaml.safe_load((EXAMPLES / "config_multi_partition.yaml").read_text(encoding="utf-8"))
    multi["alignments"][0]["file"] = str(EXAMPLES / "primates.fasta")
    xml, _ = _generate(_write_yaml(tmp_path, "multi_oneonx.yaml", multi), "1.10.4")
    root = ET.fromstring(xml)
    targets = {
        ref.get("idref") for prior in root.iter("oneOnXPrior") for ref in prior.iter("parameter")
    }
    assert "branchRates.categories" not in targets
    assert not any(target.startswith("treeModel.") for target in targets)


def test_ucln_categories_start_nonzero_and_use_swap(tmp_path):
    config_path = EXAMPLES / "config_multi_partition.yaml"
    xml, config = _generate(config_path, "1.10.4")
    root = ET.fromstring(xml)
    categories = _find_parameter(root, "branchRates.categories")
    values = categories.get("value").split()
    assert values and any(value != "0" for value in values)
    n_taxa = len({seq.taxon for p in config.partitions for seq in p.alignment.sequences})
    assert categories.get("dimension") == str(2 * n_taxa - 2)
    swaps = [
        op
        for op in root.find("operators")
        if op.tag == "swapOperator"
        and any(ref.get("idref") == "branchRates.categories" for ref in op.iter("parameter"))
    ]
    assert len(swaps) == 1


# ---------------------------------------------------------------------------
# 6. loggers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("section", ["trace_log", "tree_log", "screen_log"])
def test_log_every_zero_is_rejected(tmp_path, section):
    data = _basic_payload()
    data["loggers"][section]["log_every"] = 0
    path = _write_yaml(tmp_path, f"log0_{section}.yaml", data)
    with pytest.raises(ConfigError, match=r"log_every.*>= 1"):
        ConfigParser().parse(str(path))


def test_rlc_tree_log_has_rate_trait_and_consistent_columns():
    config_path = EXAMPLES / "config_rlc.yaml"
    xml, _ = _generate(config_path, "1.10.4")
    root = ET.fromstring(xml)
    tree_log = root.find(".//logTree")
    assert tree_log is not None
    rate_models = [
        node
        for node in tree_log
        if node.tag in ("randomLocalClockModel", "strictClockBranchRates", "discretizedBranchRates")
    ]
    assert len(rate_models) == 1
    assert rate_models[0].tag == "randomLocalClockModel"
    assert rate_models[0].get("idref") == "branchRates"

    declared = {node.get("id") for node in root.iter() if node.get("id")}
    for logger in list(root.iter("log")) + [tree_log]:
        refs = [node.get("idref") for node in logger.iter() if node.get("idref")]
        assert refs and set(refs) <= declared
        parameter_refs = [
            node.get("idref") for node in logger.iter("parameter") if node.get("idref")
        ]
        assert len(parameter_refs) == len(set(parameter_refs))


# ---------------------------------------------------------------------------
# 7. coalescent logistic / expansion (BEAST schema regressions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize(
    "prior, required",
    [
        ("coalescent_logistic", ("populationSize", "growthRate", "t50")),
        ("coalescent_expansion", ("populationSize", "ancestralPopulationProportion", "growthRate")),
    ],
)
@pytest.mark.java
def test_coalescent_logistic_and_expansion_emit_beast_schema(tmp_path, version, prior, required):
    data = _basic_payload()
    params = {"type": prior, "population_size": {"value": 0.5}}
    if prior == "coalescent_logistic":
        params["t50"] = {"value": 0.1}
    else:
        params["ancestral_population_proportion"] = {"value": 0.1}
    data["tree_prior"] = params
    path = _write_yaml(tmp_path, f"{prior}.yaml", data)
    xml, _ = _generate(path, version)
    root = ET.fromstring(xml)
    model = root.find(f".//{ 'logisticGrowth' if prior == 'coalescent_logistic' else 'expansion'}")
    assert model is not None
    child_tags = [child.tag for child in model]
    for tag in required:
        assert tag in child_tags
    assert "ancestralPopulationSize" not in child_tags
    _assert_beast_ok(tmp_path, xml, version, prior)


@pytest.mark.java
@pytest.mark.parametrize("version", VERSIONS)
def test_coalescent_logistic_defaults_are_beast_valid(tmp_path, version):
    data = _basic_payload()
    data["tree_prior"] = {"type": "coalescent_logistic"}
    path = _write_yaml(tmp_path, "logistic_defaults.yaml", data)
    xml, _ = _generate(path, version)
    _assert_beast_ok(tmp_path, xml, version, "logistic_defaults")


@pytest.mark.parametrize("version", VERSIONS)
def test_coalescent_expansion_rejects_ancestral_population_size(tmp_path, version):
    data = _basic_payload()
    data["tree_prior"] = {
        "type": "coalescent_expansion",
        "ancestral_population_size": {"value": 1.0},
    }
    path = _write_yaml(tmp_path, "expansion_bad_key.yaml", data)
    with pytest.raises(ConfigError, match="ancestral_population_size"):
        ConfigParser().parse(str(path))


@pytest.mark.java
@pytest.mark.parametrize("version", VERSIONS)
def test_unbounded_exponential_growth_uses_random_walk(tmp_path, version):
    data = _basic_payload()
    data["tree_prior"] = {
        "type": "coalescent_exponential",
        "population_size": {"value": 0.5},
        "growth_rate": {"value": 0.1},
    }
    path = _write_yaml(tmp_path, "exp_growth_unbounded.yaml", data)
    xml, _ = _generate(path, version)
    root = ET.fromstring(xml)
    operator = [
        op
        for op in root.iter("randomWalkOperator")
        if any(ref.get("idref") == "expGrowth.growthRate" for ref in op.iter("parameter"))
    ]
    assert len(operator) == 1
    _assert_beast_ok(tmp_path, xml, version, "exp_growth_unbounded")


# ---------------------------------------------------------------------------
# 8. independent clocks
# ---------------------------------------------------------------------------


@pytest.mark.java
@pytest.mark.parametrize("version", VERSIONS)
def test_separate_clocks_all_log_refs_resolve(tmp_path, version):
    data = _basic_payload()
    part = data["partitions"][0]
    part["from"] = 1
    part["to"] = 449
    part["clock"] = "separate"
    part["clock_model"] = {
        "type": "ucln",
        "ucld_mean": {"value": 0.01, "lower": 0.0, "estimate": True},
        "ucld_stdev": {"value": 0.2, "lower": 0.0, "estimate": True},
    }
    data["partitions"].append(
        {
            "id": "p2",
            "alignment_id": part["alignment_id"],
            "from": 450,
            "to": 898,
            "site_model": {"substitution_model": {"type": "gtr"}},
            "clock_model": {"type": "strict", "clock_rate": {"value": 0.01, "lower": 0.0}},
            "clock": "separate",
        }
    )
    path = _write_yaml(tmp_path, "separate_clocks.yaml", data)
    xml, _ = _generate(path, version)
    root = ET.fromstring(xml)
    declared = {node.get("id") for node in root.iter() if node.get("id")}
    refs = {node.get("idref") for node in root.iter() if node.get("idref")}
    assert refs <= declared
    _assert_beast_ok(tmp_path, xml, version, "separate_clocks")


# ---------------------------------------------------------------------------
# 9. beta priors (1.10.4 attribute spelling and calibration offsets)
# ---------------------------------------------------------------------------


@pytest.mark.java
def test_beta_parameter_prior_uses_shape_shapeb(tmp_path):
    data = _basic_payload()
    data["partitions"][0]["site_model"]["substitution_model"]["kappa"] = {
        "value": 0.5,
        "lower": 0.0,
        "upper": 100.0,
        "estimate": True,
    }
    data["parameter_priors"] = [
        {
            "parameter": "kappa",
            "distribution": {"type": "beta", "parameters": {"alpha": 2.0, "beta": 2.0}},
        }
    ]
    path = _write_yaml(tmp_path, "beta_param.yaml", data)
    for version in VERSIONS:
        xml, _ = _generate(path, version)
        assert "<betaPrior alpha=" not in xml
        assert "<betaPrior beta=" not in xml
        assert re.search(r'<betaPrior shape="2(?:\.0)?" shapeB="2(?:\.0)?">', xml)
        _assert_beast_ok(tmp_path, xml, version, "beta_param")


@pytest.mark.java
@pytest.mark.parametrize("version", VERSIONS)
def test_beta_calibration_offset_uses_coalescent_simulator(tmp_path, version):
    from beastxpy.calibration import calibration_start_age, distribution_support

    data = _basic_payload()
    data["calibrations"] = [
        {
            "name": "BetaCal",
            "taxa": ["Homo_sapiens", "Pan", "Gorilla"],
            "distribution": {
                "type": "beta",
                "parameters": {"alpha": 2.0, "beta": 2.0},
                "offset": 20.0,
            },
        }
    ]
    path = _write_yaml(tmp_path, "beta_cal.yaml", data)
    config = ConfigParser().parse(str(path))
    cal = config.calibrations[0]
    assert distribution_support(cal) == (20.0, 21.0)
    assert 20.0 < calibration_start_age(cal) < 21.0
    xml, _ = _generate(path, version)
    assert '<coalescentSimulator id="startingTree"' in xml
    assert "<constrainedTaxa>" not in xml
    assert re.search(r'<betaPrior shape="2(?:\.0)?" shapeB="2(?:\.0)?" offset="20(?:\.0)?">', xml)
    _assert_beast_ok(tmp_path, xml, version, "beta_cal")

    root = ET.fromstring(xml)
    starts = [node for node in root.iter() if node.get("id") == "startingTree"]
    assert len(starts) == 1
    assert float(starts[0].get("height")) > 20.0


# ---------------------------------------------------------------------------
# 10. MCMC options that must reach the XML
# ---------------------------------------------------------------------------


@pytest.mark.java
@pytest.mark.parametrize("version", VERSIONS)
def test_auto_optimize_false_reaches_xml_and_beast(tmp_path, version):
    data = _basic_payload()
    data["mcmc"] = {"chain_length": 1000000, "auto_optimize": False}
    path = _write_yaml(tmp_path, "auto_off.yaml", data)
    xml, _ = _generate(path, version)
    root = ET.fromstring(xml)
    assert root.find("mcmc").get("autoOptimize") == "false"
    _assert_beast_ok(tmp_path, xml, version, "auto_off")

    data["mcmc"]["auto_optimize"] = True
    path = _write_yaml(tmp_path, "auto_on.yaml", data)
    xml, _ = _generate(path, version)
    assert ET.fromstring(xml).find("mcmc").get("autoOptimize") == "true"


def test_ambiguous_operator_tag_error_lists_operator_targets(tmp_path):
    """A duplicate tag error must identify what each operator moves (Minor-10)."""
    data = _basic_payload()
    data["mcmc"] = {
        "chain_length": 1000000,
        "operator_weights": {"scaleOperator": 2.0},
    }
    config_path = _write_yaml(tmp_path, "ambiguous_weights.yaml", data)
    with pytest.raises(ModelAssemblyError) as excinfo:
        _generate(config_path, "1.10.4")
    message = str(excinfo.value)
    assert "ambiguous operator tag" in message
    assert " -> " in message
    assert "kappa" in message or "alpha" in message or "birthRate" in message
