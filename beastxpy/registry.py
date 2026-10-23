"""Model registry for BeastXPy.

Implements a Registry Pattern that maps configuration model names to their
BEAST 1 XML specs (element name, attributes, children, parameters, default
operators, default priors) and applies BEAST-version gating (``since``).

The element whitelist consulted by the validator comes from
``beastxpy/data/xml_spec.json``, which is generated from the reference source
trees by ``tools/extract_xml_spec.py`` and committed to the repository.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import BeastVersion, TreePriorType


class RegistryError(ValueError):
    """Raised when a model name is unknown or version-gated."""


# ---------------------------------------------------------------------------
# Calibration/parameter prior distributions
#
# These tables are the single source of truth shared by Gate 0 (config.py)
# and the XML boundary (``distribution_attrs``).  Keys are canonical parameter
# names; aliases are normalised in config.py before they reach this module.
# ---------------------------------------------------------------------------

DISTRIBUTION_PARAMETERS = {
    "normal": ("mean", "stdev"),
    "lognormal": ("mean", "stdev", "meanInRealSpace"),
    "uniform": ("lower", "upper"),
    "exponential": ("mean",),
    "gamma": ("shape", "scale"),
    "inverse_gamma": ("shape", "scale"),
    "beta": ("alpha", "beta"),
    "poisson": ("mean",),
    "one_on_x": (),
    "half_normal": ("mean", "stdev"),
    "laplace": ("mean", "scale"),
    "half_t": ("scale", "df"),
}

#: Accepted user spellings -> canonical parameter name.
DISTRIBUTION_PARAMETER_ALIASES = {
    "normal": {"mean": "mean", "stdev": "stdev", "sigma": "stdev"},
    "lognormal": {
        "mean": "mean",
        "mu": "mean",
        "M": "mean",
        "stdev": "stdev",
        "sigma": "stdev",
        "S": "stdev",
        "meanInRealSpace": "meanInRealSpace",
    },
    "uniform": {"lower": "lower", "upper": "upper"},
    "exponential": {"mean": "mean", "rate": "rate", "scale": "mean"},
    "gamma": {"shape": "shape", "scale": "scale", "alpha": "shape", "beta": "scale"},
    "inverse_gamma": {
        "shape": "shape",
        "scale": "scale",
        "alpha": "shape",
        "beta": "scale",
    },
    "beta": {
        "alpha": "alpha",
        "beta": "beta",
        "a": "alpha",
        "b": "beta",
        "shape": "alpha",
        "shapeB": "beta",
    },
    "poisson": {"mean": "mean", "mu": "mean"},
    "one_on_x": {},
    "half_normal": {"mean": "mean", "mu": "mean", "stdev": "stdev", "sigma": "stdev"},
    "laplace": {"mean": "mean", "mu": "mean", "scale": "scale", "b": "scale"},
    "half_t": {"scale": "scale", "sigma": "scale", "df": "df", "degrees_of_freedom": "df"},
}

#: Parameters that must be present (no silent defaults that would change the
#: prior shape).  ``exponential`` defaults its mean to 1 only when neither
#: ``mean`` nor ``rate``/``scale`` is given.
DISTRIBUTION_REQUIRED_PARAMETERS = {
    "normal": ("mean", "stdev"),
    "lognormal": ("mean", "stdev"),
    "uniform": ("lower", "upper"),
    "gamma": ("shape", "scale"),
    "inverse_gamma": ("shape", "scale"),
    "beta": ("alpha", "beta"),
    "poisson": ("mean",),
    "half_normal": ("mean", "stdev"),
    "laplace": ("mean", "scale"),
    "half_t": ("scale", "df"),
}

#: Distributions whose XML prior element accepts an ``offset`` attribute.
#: Based on BEAST 1.10.4 / BEAST X 10.5.0 ``PriorParsers.java``.
DISTRIBUTION_OFFSET_SUPPORTED = frozenset(
    {"lognormal", "exponential", "gamma", "inverse_gamma", "beta"}
)

#: Poisson's ``offset`` attribute is mandatory in BEAST (no default).
DISTRIBUTION_OFFSET_REQUIRED = frozenset({"poisson"})

#: Distributions accepted by configuration but not emittable by BEAST 1 XML.
#: ``chi_square`` has no ``chiSquarePrior`` parser in either 1.10.4 or 10.5.0.
DISTRIBUTION_UNSUPPORTED = {
    "chi_square": ("BEAST 1 has no chiSquarePrior XML parser; use gamma or normal instead")
}

#: Canonical distribution parameters that can carry a hyperprior.
DISTRIBUTION_HYPERPRIOR_TARGETS = {
    "normal": ("mean", "stdev"),
    "lognormal": ("mean", "stdev"),
    "exponential": ("mean",),
    "gamma": ("shape", "scale"),
    "inverse_gamma": ("shape", "scale"),
    "beta": ("alpha", "beta"),
    "poisson": ("mean",),
    "uniform": (),
    "one_on_x": (),
    "half_normal": (),
    "laplace": (),
    "half_t": (),
}


def distribution_parameter_name(dist_type: str, key: str) -> Optional[str]:
    """Return the canonical parameter name for ``key`` or ``None``."""
    return DISTRIBUTION_PARAMETER_ALIASES.get(dist_type, {}).get(key)


@dataclass
class ParamSpec:
    """Specification of a model parameter.

    Attributes:
        name: Parameter name (also used for default id suffixes).
        element: XML child element that wraps the parameter, if any.
        default: Default value.
        lower / upper: Default bounds.
        estimate: Whether the parameter is estimated by default.
        dimension: Parameter dimension.
    """

    name: str
    element: Optional[str] = None
    default: Any = 1.0
    lower: Optional[float] = None
    upper: Optional[float] = None
    estimate: bool = True
    dimension: int = 1


@dataclass
class OperatorSpec:
    """Specification of a default operator.

    Attributes:
        operator: XML operator element name.
        weight: Default weight.
        attrs: Extra attributes (scaleFactor etc.).
        target: How the operator references its target: "param:<name>" or
            "treemodel".
    """

    operator: str
    weight: float = 1.0
    attrs: Dict[str, Any] = field(default_factory=dict)
    target: str = ""


@dataclass
class PriorSpec:
    """Specification of a default (weakly informative) prior.

    Attributes:
        distribution: BEAST 1 prior element name (e.g. ``logNormalPrior``).
        attrs: Distribution attributes.
        target: How the prior references its target: "param:<name>" or
            "statistic:<element>".
    """

    distribution: str
    attrs: Dict[str, Any] = field(default_factory=dict)
    target: str = ""


@dataclass
class ChildSpec:
    """Specification of a child element.

    Attributes:
        element: XML child element name.
        required: Whether the child is mandatory.
        parameter: Name of the parameter the child wraps, if any.
    """

    element: str
    required: bool = True
    parameter: Optional[str] = None


@dataclass
class ModelSpec:
    """Complete XML spec of a model.

    Attributes:
        key: YAML model name.
        element: XML element name.
        attributes: Fixed/default attributes.
        children: Child element specs.
        params: Parameter specs.
        operators: Default operators.
        priors: Default priors.
        since: Earliest BEAST distribution providing the element.
        data_allowed: Data types the model may be used with
            (None = any, else subset of {nucleotide, amino-acid}).
    """

    key: str
    element: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    children: List[ChildSpec] = field(default_factory=list)
    params: List[ParamSpec] = field(default_factory=list)
    operators: List[OperatorSpec] = field(default_factory=list)
    priors: List[PriorSpec] = field(default_factory=list)
    since: str = "1.10.4"
    data_allowed: Optional[List[str]] = None


class ModelRegistry:
    """Registry mapping model names to BEAST 1 XML specs."""

    # -- Substitution models -------------------------------------------------
    _substitution_models: Dict[str, ModelSpec] = {
        "jc69": ModelSpec(
            key="jc69",
            element="HKYModel",
            attributes={},
            params=[
                ParamSpec(
                    "kappa",
                    element="kappa",
                    default=1.0,
                    lower=0.0,
                    upper=1000.0,
                    estimate=False,
                )
            ],
            data_allowed=["nucleotide"],
        ),
        "hky": ModelSpec(
            key="hky",
            element="HKYModel",
            attributes={},
            params=[ParamSpec("kappa", element="kappa", default=2.0, lower=0.0, upper=100.0)],
            data_allowed=["nucleotide"],
        ),
        "tn93": ModelSpec(
            key="tn93",
            element="TN93Model",
            attributes={},
            params=[
                ParamSpec("kappa1", element="kappa1", default=1.0, lower=0.0, upper=100.0),
                ParamSpec("kappa2", element="kappa2", default=1.0, lower=0.0, upper=100.0),
            ],
            data_allowed=["nucleotide"],
        ),
        "gtr": ModelSpec(
            key="gtr",
            element="gtrModel",
            attributes={},
            params=[
                ParamSpec(name, element=name, default=1.0, lower=0.0, upper=100.0)
                for name in ("rateAC", "rateAG", "rateAT", "rateCG", "rateCT", "rateGT")
            ],
            data_allowed=["nucleotide"],
        ),
        # Empirical amino-acid models; the XML ``type`` attribute values are
        # the parser's registered names (AminoAcidModelType.getXMLName()).
        "wag": ModelSpec(key="wag", element="aminoAcidModel", attributes={"type": "WAG"}),
        "jtt": ModelSpec(key="jtt", element="aminoAcidModel", attributes={"type": "JTT"}),
        "dayhoff": ModelSpec(
            key="dayhoff", element="aminoAcidModel", attributes={"type": "dayhoff"}
        ),
        "blosum62": ModelSpec(
            key="blosum62", element="aminoAcidModel", attributes={"type": "blosum62"}
        ),
        "cprev": ModelSpec(key="cprev", element="aminoAcidModel", attributes={"type": "cpREV"}),
        "mtrev": ModelSpec(key="mtrev", element="aminoAcidModel", attributes={"type": "mtREV"}),
    }

    # -- Clock models ---------------------------------------------------------
    _clock_models: Dict[str, ModelSpec] = {
        "strict": ModelSpec(
            key="strict",
            element="strictClockBranchRates",
            children=[ChildSpec("rate", required=True, parameter="clock.rate")],
            params=[ParamSpec("clock.rate", default=1.0, lower=0.0, upper=None)],
            operators=[
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:clock.rate",
                )
            ],
        ),
        "ucln": ModelSpec(
            key="ucln",
            element="discretizedBranchRates",
            children=[],
            params=[
                ParamSpec("ucld.mean", default=1.0, lower=0.0),
                ParamSpec("ucld.stdev", default=0.3333333333333333, lower=0.0),
            ],
            operators=[],
        ),
        "uce": ModelSpec(
            key="uce",
            element="discretizedBranchRates",
            children=[],
            params=[
                ParamSpec("ucld.mean", default=1.0, lower=0.0),
                ParamSpec("ucld.stdev", default=0.3333333333333333, lower=0.0),
            ],
            operators=[],
        ),
        "rlc": ModelSpec(
            key="rlc",
            element="randomLocalClockModel",
            children=[],
            params=[
                ParamSpec("rates", default=1.0, lower=0.0, dimension=1),
                ParamSpec("indicators", default=1.0, dimension=1),
            ],
            operators=[],
        ),
    }

    # -- Tree priors ----------------------------------------------------------
    _tree_priors: Dict[str, ModelSpec] = {
        "yule": ModelSpec(
            key="yule",
            element="yuleModel",
            attributes={},
            children=[ChildSpec("birthRate", required=True, parameter="yule.birthRate")],
            params=[ParamSpec("yule.birthRate", default=1.0, lower=0.0, upper=1000.0)],
            operators=[
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:yule.birthRate",
                )
            ],
        ),
        "birth_death": ModelSpec(
            key="birth_death",
            element="birthDeathModel",
            children=[
                ChildSpec("birthMinusDeathRate", required=True, parameter="birthDeath.bMinusD"),
                ChildSpec(
                    "relativeDeathRate", required=True, parameter="birthDeath.relativeDeathRate"
                ),
            ],
            params=[
                ParamSpec("birthDeath.bMinusD", default=0.5, lower=0.0),
                ParamSpec("birthDeath.relativeDeathRate", default=0.5, lower=0.0, upper=1.0),
            ],
            operators=[
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:birthDeath.bMinusD",
                ),
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:birthDeath.relativeDeathRate",
                ),
            ],
        ),
        "coalescent_constant": ModelSpec(
            key="coalescent_constant",
            element="constantSize",
            children=[ChildSpec("populationSize", required=True, parameter="constant.popSize")],
            params=[ParamSpec("constant.popSize", default=1.0, lower=0.0, upper=1e8)],
            operators=[
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:constant.popSize",
                )
            ],
        ),
        "coalescent_exponential": ModelSpec(
            key="coalescent_exponential",
            element="exponentialGrowth",
            children=[
                ChildSpec("populationSize", required=True, parameter="expGrowth.popSize"),
                ChildSpec("growthRate", required=True, parameter="expGrowth.growthRate"),
            ],
            params=[
                ParamSpec("expGrowth.popSize", default=1.0, lower=0.0, upper=1e8),
                ParamSpec("expGrowth.growthRate", default=0.1, lower=-100.0, upper=100.0),
            ],
            operators=[
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:expGrowth.popSize",
                ),
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:expGrowth.growthRate",
                ),
            ],
        ),
        "coalescent_logistic": ModelSpec(
            key="coalescent_logistic",
            element="logisticGrowth",
            children=[
                ChildSpec("populationSize", required=True, parameter="logistic.popSize"),
                ChildSpec("growthRate", required=True, parameter="logistic.growthRate"),
                ChildSpec("t50", required=True, parameter="logistic.t50"),
            ],
            params=[
                ParamSpec("logistic.popSize", default=1.0, lower=0.0, upper=1e8),
                ParamSpec("logistic.growthRate", default=0.1, lower=0.0),
                ParamSpec("logistic.t50", default=0.1, lower=0.0),
            ],
            operators=[
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:logistic.popSize",
                ),
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:logistic.growthRate",
                ),
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:logistic.t50",
                ),
            ],
        ),
        "coalescent_expansion": ModelSpec(
            key="coalescent_expansion",
            element="expansion",
            children=[
                ChildSpec("populationSize", required=True, parameter="expansion.popSize"),
                ChildSpec(
                    "ancestralPopulationProportion",
                    required=True,
                    parameter="expansion.ancestralProportion",
                ),
                ChildSpec("growthRate", required=True, parameter="expansion.growthRate"),
            ],
            params=[
                ParamSpec("expansion.popSize", default=1.0, lower=0.0, upper=1e8),
                ParamSpec("expansion.ancestralProportion", default=0.1, lower=0.0, upper=1.0),
                ParamSpec("expansion.growthRate", default=0.1, lower=0.0),
            ],
            operators=[
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:expansion.popSize",
                ),
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:expansion.ancestralProportion",
                ),
                OperatorSpec(
                    "scaleOperator",
                    weight=3.0,
                    attrs={"scaleFactor": 0.75},
                    target="param:expansion.growthRate",
                ),
            ],
        ),
        "skyline": ModelSpec(
            key="skyline",
            element="generalizedSkyLineLikelihood",
            children=[
                ChildSpec("populationSizes", required=True, parameter="skyline.popSize"),
                ChildSpec("groupSizes", required=True, parameter="skyline.groupSize"),
                ChildSpec("populationTree", required=True),
            ],
            params=[
                ParamSpec("skyline.popSize", default=1.0, lower=0.0, upper=1e8, dimension=5),
                ParamSpec("skyline.groupSize", default=1.0, lower=1.0, dimension=5),
            ],
            operators=[],
        ),
        "ebsp": ModelSpec(
            key="ebsp",
            element="variableDemographic",
            children=[
                ChildSpec("populationSizes", required=True, parameter="demographic.popSize"),
                ChildSpec("indicators", required=True, parameter="demographic.indicators"),
                ChildSpec("trees", required=True),
            ],
            params=[
                ParamSpec("demographic.popSize", default=0.5, lower=0.0, dimension=10),
                ParamSpec("demographic.indicators", default=0.0, lower=0.0, upper=1.0, dimension=9),
            ],
            operators=[],
        ),
    }

    # -- Calibration distributions -------------------------------------------
    _distributions: Dict[str, ModelSpec] = {
        "normal": ModelSpec(key="normal", element="normalPrior"),
        "lognormal": ModelSpec(key="lognormal", element="logNormalPrior"),
        "uniform": ModelSpec(key="uniform", element="uniformPrior"),
        "exponential": ModelSpec(key="exponential", element="exponentialPrior"),
        "gamma": ModelSpec(key="gamma", element="gammaPrior"),
        "beta": ModelSpec(key="beta", element="betaPrior"),
        "one_on_x": ModelSpec(key="one_on_x", element="oneOnXPrior"),
        "inverse_gamma": ModelSpec(key="inverse_gamma", element="inverseGammaPrior"),
        "poisson": ModelSpec(key="poisson", element="poissonPrior"),
        "half_normal": ModelSpec(key="half_normal", element="halfNormalPrior"),
        "laplace": ModelSpec(key="laplace", element="laplacePrior"),
        "half_t": ModelSpec(key="half_t", element="halfTPrior"),
        "chi_square": ModelSpec(key="chi_square", element="chiSquarePrior"),
    }

    # -- 10.5.0-only elements (gated when targeting 1.10.4) ------------------
    _extended_elements = {
        "ageStatistic",
        "reportTree",
        "branchLengthStatistic",
        "cladeRelationshipStatistic",
        "mixedEffectsRateStatistic",
        "subtreeRateStatistic",
        "aminoAcidMixtureModel",
        "instantaneousMixtureSubstitutionModel",
        "substitutionRateMatrixMixtureModel",
        "timeVaryingRates",
        "piecewiseLinearTimeDependentModel",
        "experimentalGlmSubstitutionModel",
        "logRateSubstitutionModel",
        "birthDeathSubstitutionModel",
    }

    # -----------------------------------------------------------------------
    def __init__(self) -> None:
        self._spec = self._load_xml_spec()
        elem_map = self._element_map()
        self._case_index = {str(k).lower(): k for k in elem_map}

    # -- xml_spec.json -------------------------------------------------------
    @staticmethod
    def _xml_spec_path() -> Path:
        return Path(__file__).parent / "data" / "xml_spec.json"

    @classmethod
    def _load_xml_spec(cls) -> Dict[str, Any]:
        try:
            with open(cls._xml_spec_path(), "r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            # Fall back to the built-in vocabulary when the data file is
            # missing (e.g. running from a source checkout without the file).
            return {}

    @property
    def xml_spec(self) -> Dict[str, Any]:
        """Element whitelist: {element_name: {"since": ...}}."""
        return self._spec

    # -- Public lookups ------------------------------------------------------
    def substitution_spec(self, key: str) -> ModelSpec:
        """Return the substitution model spec, or raise RegistryError."""
        spec = self._substitution_models.get(key)
        if spec is None:
            raise RegistryError(
                f"Unknown substitution model: {key!r} "
                f"(valid: {', '.join(sorted(self._substitution_models))})"
            )
        return spec

    def clock_spec(self, key: str) -> ModelSpec:
        """Return the clock model spec, or raise RegistryError."""
        spec = self._clock_models.get(key)
        if spec is None:
            raise RegistryError(
                f"Unknown clock model: {key!r} " f"(valid: {', '.join(sorted(self._clock_models))})"
            )
        return spec

    def tree_prior_spec(self, key: str) -> ModelSpec:
        """Return the tree-prior model spec, or raise RegistryError."""
        spec = self._tree_priors.get(key)
        if spec is None:
            raise RegistryError(
                f"Unknown tree prior: {key!r} " f"(valid: {', '.join(sorted(self._tree_priors))})"
            )
        return spec

    def distribution_spec(self, key: str) -> ModelSpec:
        """Return the calibration-distribution spec, or raise RegistryError."""
        spec = self._distributions.get(key)
        if spec is None:
            raise RegistryError(
                f"Unknown distribution: {key!r} "
                f"(valid: {', '.join(sorted(self._distributions))})"
            )
        return spec

    # -- Version gating ------------------------------------------------------
    def gate_version(self, element: str, beast_version: str) -> Optional[str]:
        """Return an error message if ``element`` is unavailable in the target.

        Elements unknown to the spec are treated as 1.10.4-native (the core
        dialect), so only explicitly registered 10.5.0-only elements gate.
        """
        if beast_version == BeastVersion.V1_10_4.value:
            if element in self._extended_elements:
                return (
                    f"Element <{element}> requires BEAST X 10.5.0; it cannot be "
                    f"emitted when control.beast_version is '1.10.4'."
                )
        return None

    # -- Validation support --------------------------------------------------
    def _element_map(self) -> Dict[str, Any]:
        """Return the ``elements`` sub-map of the loaded XML spec."""
        raw = self._spec
        if isinstance(raw, dict) and isinstance(raw.get("elements"), dict):
            return raw["elements"]
        return raw

    def is_known_element(self, element: str) -> bool:
        """Whether the element name appears in the whitelist spec.

        BEAST 1's ``XMLParser.parserStore`` compares element names
        case-insensitively (``ParserComparator``), so lookups are matched in a
        case-insensitive manner here too.
        """
        return element in self._element_map() or element.lower() in self._case_index

    def element_since(self, element: str) -> str:
        """Return the 'since' version recorded for an element."""
        key = element if element in self._element_map() else element.lower()
        entry = self._element_map().get(key)
        if isinstance(entry, dict):
            return str(entry.get("since", "1.10.4"))
        return "1.10.4"

    def list_models(self) -> Dict[str, List[str]]:
        """Return a summary of registered models for ``list-models``."""
        return {
            "substitution_models": sorted(self._substitution_models),
            "clock_models": sorted(self._clock_models),
            "tree_priors": sorted(self._tree_priors),
            "distributions": sorted(self._distributions),
            "extended_10_5_0_elements": sorted(self._extended_elements),
        }

    # -- Model-name helpers --------------------------------------------------
    @staticmethod
    def distribution_attrs(dist_type: str, params: Dict[str, Any], offset: float) -> Dict[str, Any]:
        """Map distribution type + canonical parameters onto BEAST attributes.

                ``offset`` is written only for the distributions whose BEAST prior
                parser accepts it, and always for Poisson (whose ``offset`` attribute
                is mandatory).  Unsupported combinations are rejected by Gate 0
        .
        """
        attrs: Dict[str, Any] = {}
        if dist_type == "normal":
            attrs["mean"] = params.get("mean", 1.0)
            attrs["stdev"] = params.get("stdev", 1.0)
        elif dist_type == "lognormal":
            mrs = params.get("meanInRealSpace", False)
            if isinstance(mrs, str):
                mrs = mrs.lower() == "true"
            attrs["mean"] = params.get("mean", 0.0)
            attrs["stdev"] = params.get("stdev", 1.0)
            attrs["meanInRealSpace"] = "true" if mrs else "false"
            if offset:
                attrs["offset"] = offset
        elif dist_type == "uniform":
            attrs["lower"] = params.get("lower", 0.0)
            attrs["upper"] = params.get("upper", 1.0)
        elif dist_type == "exponential":
            attrs["mean"] = params.get("mean", 1.0)
            if offset:
                attrs["offset"] = offset
        elif dist_type == "gamma":
            attrs["shape"] = params.get("shape", 1.0)
            attrs["scale"] = params.get("scale", 1.0)
            if offset:
                attrs["offset"] = offset
        elif dist_type == "beta":
            # BEAST 1.10.4's betaPrior parser only knows shape/shapeB;
            # BEAST X 10.5.0 accepts both spellings, so the intersection is
            # shape/shapeB.
            attrs["shape"] = params.get("alpha", 1.0)
            attrs["shapeB"] = params.get("beta", 1.0)
            if offset:
                attrs["offset"] = offset
        elif dist_type == "inverse_gamma":
            attrs["shape"] = params.get("shape", 2.0)
            attrs["scale"] = params.get("scale", 1.0)
            if offset:
                attrs["offset"] = offset
        elif dist_type == "poisson":
            attrs["mean"] = params.get("mean", 1.0)
            # Mandatory in BEAST's poissonPrior parser.
            attrs["offset"] = offset
        elif dist_type == "half_normal":
            # HalfNormalDistribution(mean, stdev): a normal density truncated
            # at zero, normalised by the mass above zero.
            attrs["mean"] = params.get("mean", 0.0)
            attrs["stdev"] = params.get("stdev", 1.0)
        elif dist_type == "laplace":
            attrs["mean"] = params.get("mean", 0.0)
            attrs["scale"] = params.get("scale", 1.0)
        elif dist_type == "half_t":
            attrs["scale"] = params.get("scale", 1.0)
            attrs["df"] = params.get("df", 1.0)
        elif dist_type == "one_on_x":
            pass
        else:
            raise RegistryError(f"Distribution {dist_type!r} has no BEAST 1 prior XML mapping")
        return attrs


def tree_prior_element(tp_type: TreePriorType) -> str:
    """Return the BEAST 1 population/tree model element for a tree prior type."""
    mapping = {
        TreePriorType.YULE: "yuleModel",
        TreePriorType.BIRTH_DEATH: "birthDeathModel",
        TreePriorType.COALESCENT_CONSTANT: "constantSize",
        TreePriorType.COALESCENT_EXPONENTIAL: "exponentialGrowth",
        TreePriorType.COALESCENT_LOGISTIC: "logisticGrowth",
        TreePriorType.COALESCENT_EXPANSION: "expansion",
        TreePriorType.SKYLINE: "generalizedSkyLineLikelihood",
        TreePriorType.EBSP: "variableDemographic",
    }
    return mapping[tp_type]
