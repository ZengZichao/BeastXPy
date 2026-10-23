"""Strict YAML configuration parsing for BeastXPy.

``ConfigParser`` implements Gate 0 of the four-check scheme: every rule that
can be applied while the YAML and the alignment files are being read is applied
here, fail-fast, before any XML exists. Unknown keys, quoted booleans,
out-of-domain priors, illegal characters, provenance violations, `tree:
separate`, version-gated models and unidentifiable time axes are all rejected
at this stage.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import AbstractSet, Any, Dict, List, Optional, Set, Tuple

import yaml

from .models import (
    BEASTConfig,
    CalibrationPoint,
    ClockModelConfig,
    ClockModelType,
    ControlConfig,
    DATA_TYPE_ALIASES,
    DistributionConfig,
    HYPERPRIOR_EMITTABLE_TARGETS,
    HyperpriorConfig,
    LoggersConfig,
    MCMCConfig,
    OutputConfig,
    Partition,
    Provenance,
    RealParameter,
    SiteModelConfig,
    TreePriorConfig,
    TreePriorType,
    is_safe_filename,
    is_safe_identifier,
)
from .partition import (
    PartitionError,
    check_partition_ranges,
    collect_partitions,
    validate_partition_range,
)
from .registry import (
    DISTRIBUTION_HYPERPRIOR_TARGETS,
    DISTRIBUTION_OFFSET_REQUIRED,
    DISTRIBUTION_OFFSET_SUPPORTED,
    DISTRIBUTION_PARAMETERS,
    DISTRIBUTION_PARAMETER_ALIASES,
    DISTRIBUTION_REQUIRED_PARAMETERS,
    DISTRIBUTION_UNSUPPORTED,
    ModelRegistry,
    RegistryError,
)
from .sequence import (
    AlignmentError,
    SequenceReader,
    normalize_tip_dates,
    validate_alignment,
)
from .utils import distribution_quantiles, parse_number
from . import __version__

TOP_LEVEL_KEYS = frozenset(
    {
        "metadata",
        "alignments",
        "partitions",
        "tree_prior",
        "calibrations",
        "parameter_priors",
        "mcmc",
        "loggers",
        "output",
        "control",
        "tip_dates",
        "diagnostics",
        "analysis",
        "custom_fragments",
    }
)

VALID_DATA_TYPES = tuple(sorted(DATA_TYPE_ALIASES))
VALID_BEAST_VERSIONS = ("1.10.4", "10.5.0", "both")
VALID_CLOCK_MODELS = ("strict", "ucln", "uce", "rlc")
VALID_TREE_PRIORS = (
    "yule",
    "birth_death",
    "coalescent_constant",
    "coalescent_exponential",
    "coalescent_logistic",
    "coalescent_expansion",
    "skyline",
    "ebsp",
)
VALID_DISTRIBUTIONS = tuple(list(DISTRIBUTION_PARAMETERS) + ["chi_square"])
VALID_SUBSTITUTION_MODELS = (
    "jc69",
    "hky",
    "tn93",
    "gtr",
    "wag",
    "jtt",
    "dayhoff",
    "blosum62",
    "cprev",
    "mtrev",
)
AMINO_ACID_MODELS = ("wag", "jtt", "dayhoff", "blosum62", "cprev", "mtrev")
VALID_INIT_TREES = ("coalescent", "upgma", "newick")

# documented spellings that map safely onto the internal vocabulary.
TREE_PRIOR_ALIASES = {
    "yule": "yule",
    "birth_death": "birth_death",
    "birth-death": "birth_death",
    "birthdeath": "birth_death",
    "coalescent_constant": "coalescent_constant",
    "constant": "coalescent_constant",
    "constant_size": "coalescent_constant",
    "coalescent_exponential": "coalescent_exponential",
    "exponential": "coalescent_exponential",
    "coalescent_logistic": "coalescent_logistic",
    "logistic": "coalescent_logistic",
    "coalescent_expansion": "coalescent_expansion",
    "expansion": "coalescent_expansion",
    "skyline": "skyline",
    "bayesian_skyline": "skyline",
    "ebsp": "ebsp",
    "extended_bayesian_skyline": "ebsp",
}

CALIBRATION_METHOD_ALIASES = {
    "clade": "clade",
    "crown": "clade",
    "stem": "stem",
}

PROVENANCE_TYPE_ALIASES = {
    "soft": "soft",
    "hard": "hard",
    # docs/configuration.md historically advertised one-sided bounds; they are
    # still soft (metadata-only) provenance annotations.
    "upper": "soft",
    "lower": "soft",
}

FREQUENCY_MODE_ALIASES = {
    "estimated": "estimated",
    "uniform": "uniform",
    "equal": "uniform",
    "empirical": "empirical",
    "fixed": "fixed",
}

_DISTRIBUTION_ALIASES = {
    "normal": "normal",
    "gaussian": "normal",
    "lognormal": "lognormal",
    "log_normal": "lognormal",
    "uniform": "uniform",
    "exponential": "exponential",
    "gamma": "gamma",
    "beta": "beta",
    "one_on_x": "one_on_x",
    "oneonx": "one_on_x",
    "inverse_gamma": "inverse_gamma",
    "inversegamma": "inverse_gamma",
    "poisson": "poisson",
    "half_normal": "half_normal",
    "halfnormal": "half_normal",
    "half-normal": "half_normal",
    "laplace": "laplace",
    "half_t": "half_t",
    "halft": "half_t",
    "half-t": "half_t",
    "chi_square": "chi_square",
    "chisquare": "chi_square",
}

#: Gate-0 audit of configuration field -> consumer.  Every accepted
#: field must be listed here with either a consumer or an explicit rejection.
CONFIG_FIELD_AUDIT: Dict[str, Dict[str, str]] = {
    "metadata.analysis_name": {"consumer": "runner/api (artifact names)", "status": "validated"},
    "alignments[].file": {"consumer": "sequence.SequenceReader", "status": "implemented"},
    "alignments[].data_type": {
        "consumer": "Alignment.data_type / beast_data_type",
        "status": "implemented",
    },
    "partitions[].from/to": {
        "consumer": "Alignment.from_pos/to_pos -> <patterns from/to>",
        "status": "implemented",
    },
    "partitions[].site_model": {"consumer": "model.py site model", "status": "implemented"},
    "partitions[].clock_model": {"consumer": "model.py clock model", "status": "implemented"},
    "tree_prior.params": {"consumer": "model.py tree prior", "status": "implemented"},
    "tree_prior.skyline_groups": {
        "consumer": "model.py skyline populationSizes/groupSizes dimension",
        "status": "implemented",
    },
    "tree_prior.skyline_pop_sizes": {
        "consumer": "model.py skyline populationSizes values/dimension",
        "status": "implemented",
    },
    "tree_prior.ebsp_pop_sizes": {
        "consumer": "model.py EBSP populationSizes/indicators dimension",
        "status": "implemented",
    },
    "calibrations[].distribution": {
        "consumer": "calibration.prior_fragment",
        "status": "implemented",
    },
    "calibrations[].hyperpriors": {
        "consumer": "model.py distributionLikelihood + hyperparameter",
        "status": "implemented",
    },
    "calibrations[].taxa": {
        "consumer": "CalibrationPoint.is_root / root calibration (taxa: null)",
        "status": "implemented",
    },
    "calibrations[].is_root": {
        "consumer": "CalibrationPoint.is_root -> global taxa tmrca/prior/constraint/log",
        "status": "implemented",
    },
    "calibrations[].provenance": {"consumer": "provenance comment + Gate 0", "status": "validated"},
    "parameter_priors": {"consumer": "model.py explicit priors", "status": "implemented"},
    "mcmc.chain_length": {"consumer": "model.py <mcmc chainLength>", "status": "implemented"},
    "mcmc.pre_burnin": {"consumer": "model.py <mcmc preBurnin>", "status": "implemented"},
    "mcmc.auto_optimize": {"consumer": "model.py <mcmc autoOptimize>", "status": "implemented"},
    "mcmc.autoOptimize": {"consumer": "alias of mcmc.auto_optimize", "status": "implemented"},
    "mcmc.operator_weights": {"consumer": "model.py <operator weight>", "status": "implemented"},
    "loggers.*": {"consumer": "model.py loggers", "status": "validated"},
    "output.file": {
        "consumer": "accepted/validated only; the CLI --output selects the target",
        "status": "exposed",
    },
    "output.embed_fingerprint": {"consumer": "api._generate", "status": "implemented"},
    "output.methods_file": {"consumer": "api._write_sidecars", "status": "implemented"},
    "output.fingerprint_file": {"consumer": "api._write_sidecars", "status": "implemented"},
    "control.seed": {"consumer": "runner.BeastRunner._resolve_seeds", "status": "implemented"},
    "control.beast_version": {"consumer": "api/validator/writer", "status": "implemented"},
    "control.initial_tree": {"consumer": "model.py starting tree", "status": "implemented"},
    "control.newick": {"consumer": "model.py starting tree", "status": "implemented"},
    "control.overlap_measure": {"consumer": "diagnostics", "status": "implemented"},
    "tip_dates": {"consumer": "calibration.build_taxa_sets", "status": "validated"},
    "diagnostics.*": {"consumer": "api/diagnostics", "status": "validated"},
}

RECOGNIZED_PARAMETER_KEYS = (
    "clock_rate",
    "kappa",
    "kappa1",
    "kappa2",
    "ucld_mean",
    "ucld_stdev",
    "exponential_mean",
    "birth_rate",
    "birth_minus_death",
    "relative_death",
    "population_size",
    "growth_rate",
    "skyline_pop_sizes",
    "ebsp_pop_sizes",
    "rlc_rates",
    "rates",
    "frequencies",
)


class ConfigError(ValueError):
    """Raised when a configuration file fails Gate 0 checks."""


def _fail(errors: List[str], message: str) -> None:
    errors.append(message)


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys (conflicting dates)."""


def _construct_unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConfigError(f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _check_known_keys(
    mapping: Dict[str, Any],
    allowed: AbstractSet[str],
    where: str,
    errors: List[str],
) -> None:
    for key in mapping:
        if key not in allowed:
            errors.append(f"{where}: unknown key: {key}")


def _strict_bool(value: Any, name: str, errors: List[str]) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        errors.append(f"{name}: boolean values must be unquoted (true/false), got string {value!r}")
        return None
    errors.append(f"{name}: expected a boolean, got {value!r}")
    return None


def _strict_float(value: Any, name: str, errors: List[str]) -> Optional[float]:
    if isinstance(value, bool):
        errors.append(f"{name}: expected a number, got boolean {value!r}")
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"{name}: expected a number, got {value!r}")
        return None


def _bound_check(
    value: float,
    lower: Optional[float],
    upper: Optional[float],
    name: str,
    errors: List[str],
) -> None:
    if lower is not None and value < lower:
        errors.append(f"{name}: value {value} violates lower bound {lower}")
    if upper is not None and value > upper:
        errors.append(f"{name}: value {value} violates upper bound {upper}")


def _parse_real_parameter(
    raw: Any,
    name: str,
    errors: List[str],
    default_lower: Optional[float] = None,
    default_upper: Optional[float] = None,
    default_estimate: bool = True,
    default_value: float = 1.0,
) -> Optional[RealParameter]:
    """Parse a parameter dict: {value, lower, upper, estimate}."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return RealParameter(value=float(raw), lower=default_lower, upper=default_upper)
    if not isinstance(raw, dict):
        errors.append(f"{name}: parameter must be a dict or a number, got {raw!r}")
        return None
    _check_known_keys(raw, {"value", "lower", "upper", "estimate", "dimension"}, name, errors)
    value = _strict_float(raw.get("value", default_value), f"{name}.value", errors)
    lower = (
        _strict_float(raw["lower"], f"{name}.lower", errors) if "lower" in raw else default_lower
    )
    upper = (
        _strict_float(raw["upper"], f"{name}.upper", errors) if "upper" in raw else default_upper
    )
    estimate = raw.get("estimate", default_estimate)
    if estimate is not None and not isinstance(estimate, bool):
        est = _strict_bool(estimate, f"{name}.estimate", errors)
        estimate = est if est is not None else default_estimate
    if value is not None and lower is not None and value < lower:
        errors.append(f"{name}: value {value} < lower {lower}")
    if value is not None and upper is not None and value > upper:
        errors.append(f"{name}: value {value} > upper {upper}")
    if lower is not None and upper is not None and lower > upper:
        errors.append(f"{name}: lower {lower} > upper {upper}")
    return RealParameter(
        value=value if value is not None else default_value,
        lower=lower,
        upper=upper,
        estimate=bool(estimate) if estimate is not None else default_estimate,
    )


class ConfigParser:
    """Parse and validate a BeastXPy YAML configuration (Gate 0)."""

    VALID_DATA_TYPES = VALID_DATA_TYPES
    VALID_SUBSTITUTION_MODELS = VALID_SUBSTITUTION_MODELS
    VALID_CLOCK_MODELS = VALID_CLOCK_MODELS
    VALID_TREE_PRIORS = VALID_TREE_PRIORS
    VALID_DISTRIBUTIONS = VALID_DISTRIBUTIONS

    def __init__(self) -> None:
        self.registry = ModelRegistry()

    # ------------------------------------------------------------------
    def parse(self, config_path: str) -> BEASTConfig:
        """Parse and validate a YAML configuration file.

        Raises:
            ConfigError: Any Gate-0 failure.
            FileNotFoundError: The configuration file is missing.
        """
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = yaml.load(handle, Loader=_UniqueKeySafeLoader)
        except ConfigError:
            raise
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ConfigError(f"Top level of {config_path} must be a mapping")

        errors: List[str] = []
        warnings: List[str] = []

        _check_known_keys(raw, TOP_LEVEL_KEYS, "config", errors)
        if errors:
            raise ConfigError("; ".join(errors))

        metadata = self._parse_metadata(raw.get("metadata", {}), path, errors)
        control = self._parse_control(raw.get("control", {}), errors)
        alignments = self._parse_alignments(raw.get("alignments", []), path, errors)
        partitions = self._parse_partitions(raw.get("partitions", []), alignments, path, errors)
        known_taxa = set().union(*(a.taxa_names for a in alignments)) if alignments else set()
        calibrations = self._parse_calibrations(raw.get("calibrations", []), known_taxa, errors)
        tree_prior = self._parse_tree_prior(raw.get("tree_prior", {}), errors)
        parameter_priors = self._parse_parameter_priors(raw.get("parameter_priors", []), errors)
        mcmc = self._parse_mcmc(raw.get("mcmc", {}), errors)
        loggers = self._parse_loggers(raw.get("loggers", {}), mcmc, metadata, errors)
        output = self._parse_output(raw.get("output", {}), metadata, errors)
        diagnostics = self._parse_diagnostics(raw.get("diagnostics", {}), errors)
        analysis = self._parse_analysis(raw.get("analysis", {}), errors)
        if analysis:
            analysis_version = analysis.get("beast_version")
            if analysis_version in ("1.10.4", "10.5.0") and (
                control.beast_version != analysis_version
            ):
                errors.append(
                    f"analysis.beast_version: {analysis_version!r} conflicts with "
                    f"control.beast_version {control.beast_version!r}; a "
                    "single-version analysis requires a matching control target"
                )
        custom_fragments = self._parse_custom_fragments(raw.get("custom_fragments", {}), errors)

        # ---- cross-checks -------------------------------------------------
        if not alignments:
            errors.append("config: at least one alignment is required")
        if not partitions:
            errors.append("config: at least one partition is required")

        if calibrations and control.initial_tree in ("upgma", "newick"):
            errors.append(
                f"control.initial_tree: {control.initial_tree!r} cannot be "
                "combined with node calibrations; an unconstrained UPGMA/user "
                "tree generally violates the calibration priors and BEAST "
                "starts at a zero posterior. Remove initial_tree/newick to use "
                "the calibration-aware coalescent starting tree."
            )

        # The partition validation layer actually runs (ids, tree/clock
        # sharing, shared taxon set) and calibration coordinates are re-checked on
        # the assembled Partition objects.
        try:
            collect_partitions(partitions)
        except PartitionError as exc:
            errors.append(f"partitions: {exc}")
        try:
            check_partition_ranges(partitions)
        except PartitionError as exc:
            errors.append(f"partitions: {exc}")

        # Report ambiguous auto-detected data types.
        for alignment in alignments:
            if alignment.data_type_source == "auto" and alignment.data_type_ambiguous:
                warnings.append(
                    f"alignments: alignment {alignment.id!r} was auto-detected as "
                    f"{alignment.data_type.value!r}, but its alphabet is also valid "
                    "for the other data type; set data_type explicitly to silence "
                    "this warning"
                )

        # Time-axis identifiability: no calibrations, no tip dates and a fixed
        # clock rate has no meaningful scale.
        taxa_names = known_taxa
        tip_dates = self._collect_tip_dates(
            raw.get("tip_dates", {}), taxa_names, control.beast_version, errors
        )
        has_tip_dates = bool(tip_dates)
        has_calibrations = bool(calibrations)
        has_clock_rate = any(
            (
                (p.clock_model.clock_rate is not None and p.clock_model.clock_rate.estimate)
                or (p.clock_model.ucld_mean is not None and p.clock_model.ucld_mean.estimate)
            )
            for p in partitions
        )
        if not has_calibrations and not has_tip_dates and not has_clock_rate:
            errors.append(
                "config: the time axis is unidentifiable: no calibrations, no tip "
                "dates, and no estimated clock rate. Provide at least one source "
                "of time information."
            )

        # Provenance hard violations: original fossil age range must intersect
        # the prior's 95% interval when calibration_type is 'hard'.
        for cal in calibrations:
            if cal.provenance and cal.distribution:
                self._check_provenance(cal, warnings, errors)

        # Version gating on model elements
        for part in partitions:
            element = self.registry.substitution_spec(
                part.site_model.substitution_model.get("type", "hky")
            ).element
            gate = self.registry.gate_version(element, control.beast_version)
            if gate:
                errors.append(gate)

        if errors:
            raise ConfigError("; ".join(errors))

        return BEASTConfig(
            metadata=metadata,
            partitions=partitions,
            all_alignments=alignments,
            tree_prior=tree_prior,
            calibrations=calibrations,
            parameter_priors=parameter_priors,
            mcmc=mcmc,
            loggers=loggers,
            output=output,
            control=control,
            diagnostics=diagnostics,
            tip_dates=tip_dates,
            warnings=warnings,
            analysis=analysis,
            custom_fragments=custom_fragments,
        )

    # ------------------------------------------------------------------
    # Section parsers
    # ------------------------------------------------------------------

    def _parse_metadata(self, raw: Any, path: Path, errors: List[str]) -> Dict[str, Any]:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append("metadata: must be a mapping")
            return {}
        metadata = dict(raw)
        metadata.setdefault("analysis_name", path.stem)
        metadata.setdefault("tool_version", __version__)
        name = metadata.get("analysis_name")
        if not isinstance(name, str) or not name.strip():
            errors.append("metadata.analysis_name: must be a non-empty string")
        elif not is_safe_filename(name):
            errors.append(
                f"metadata.analysis_name: {name!r} is not a safe artifact name "
                "(letters/digits/underscore/dot/dash; no path separators)"
            )
        return metadata

    def _parse_control(self, raw: Any, errors: List[str]) -> ControlConfig:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append("control: must be a mapping")
            return ControlConfig()
        _check_known_keys(
            raw,
            {"beast_version", "strict", "seed", "overlap_measure", "initial_tree", "newick"},
            "control",
            errors,
        )
        beast_version = raw.get("beast_version", "1.10.4")
        if not isinstance(beast_version, str) or beast_version not in VALID_BEAST_VERSIONS:
            errors.append(
                f"control.beast_version: invalid {beast_version!r} "
                f"(valid: {', '.join(VALID_BEAST_VERSIONS)})"
            )
            beast_version = "1.10.4"
        strict = raw.get("strict", True)
        if not isinstance(strict, bool):
            strict = _strict_bool(strict, "control.strict", errors)
        seed = raw.get("seed")
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int):
                errors.append(f"control.seed: must be an integer, got {seed!r}")
            elif seed < 0 or seed > 2**31 - 1:
                errors.append(f"control.seed: must be in 0..{2**31 - 1}, got {seed}")
        overlap = raw.get("overlap_measure", "relative")
        if overlap not in ("relative", "absolute"):
            errors.append(
                f"control.overlap_measure: invalid {overlap!r} (valid: relative, absolute)"
            )
        initial_tree = raw.get("initial_tree", "coalescent")
        if initial_tree not in VALID_INIT_TREES:
            errors.append(
                f"control.initial_tree: invalid {initial_tree!r} "
                f"(valid: {', '.join(VALID_INIT_TREES)})"
            )
        newick = raw.get("newick")
        if newick is not None and not isinstance(newick, str):
            errors.append("control.newick: must be a Newick string")
        if initial_tree == "newick" and not newick:
            errors.append("control.initial_tree: 'newick' requires control.newick to be set")
        return ControlConfig(
            beast_version=str(beast_version),
            seed=seed,
            strict=strict if strict is not None else True,
            overlap_measure=str(overlap),
            initial_tree=str(initial_tree),
            newick=newick,
        )

    def _parse_alignments(self, raw: Any, path: Path, errors: List[str]) -> List:
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            errors.append("alignments: must be a list")
            return []
        result = []
        seen_ids: Dict[str, int] = {}
        for i, entry in enumerate(raw):
            where = f"alignments[{i}]"
            if not isinstance(entry, dict):
                errors.append(f"{where}: must be a mapping")
                continue
            _check_known_keys(entry, {"id", "file", "format", "data_type"}, where, errors)
            file_name = entry.get("file")
            if not isinstance(file_name, str) or not file_name:
                errors.append(f"{where}.file: is required")
                continue
            alignment_id = entry.get("id") or Path(file_name).stem
            if not isinstance(alignment_id, str) or not is_safe_identifier(alignment_id):
                errors.append(
                    f"{where}.id: {alignment_id!r} is not a safe XML identifier "
                    "(letters/digits/underscore/dot/dash, not a reserved BEAST id)"
                )
                continue
            if alignment_id in seen_ids:
                errors.append(
                    f"{where}.id: duplicate alignment id {alignment_id!r} "
                    f"(already declared at alignments[{seen_ids[alignment_id]}])"
                )
                continue
            seen_ids[alignment_id] = i
            data_type = entry.get("data_type", "auto")
            if data_type is not None and not isinstance(data_type, str):
                errors.append(f"{where}.data_type: must be a string, got {data_type!r}")
                continue
            try:
                alignment = SequenceReader.read(
                    str(Path(path.parent) / file_name),
                    format=entry.get("format", "auto"),
                    data_type=data_type or "auto",
                    alignment_id=alignment_id,
                )
            except FileNotFoundError as exc:
                errors.append(f"{where}: {exc}")
                continue
            except (AlignmentError, KeyError, TypeError, ValueError) as exc:
                errors.append(f"{where}: {exc}")
                continue
            findings = validate_alignment(alignment)
            for finding in findings:
                errors.append(f"{where}: {finding}")
            result.append(alignment)
        return result

    def _parse_partitions(
        self, raw: Any, alignments: List, path: Path, errors: List[str]
    ) -> List[Partition]:
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            errors.append("partitions: must be a list")
            return []
        alignment_map = {a.id: a for a in alignments}
        result: List[Partition] = []
        seen_ids: Dict[str, int] = {}
        for i, entry in enumerate(raw):
            where = f"partitions[{i}]"
            if not isinstance(entry, dict):
                errors.append(f"{where}: must be a mapping")
                continue
            _check_known_keys(
                entry,
                {
                    "id",
                    "alignment_id",
                    "alignment",
                    "from",
                    "to",
                    "site_model",
                    "clock_model",
                    "tree",
                    "clock",
                },
                where,
                errors,
            )
            partition_id = entry.get("id", f"partition{i + 1}")
            if not isinstance(partition_id, str) or not is_safe_identifier(partition_id):
                errors.append(
                    f"{where}.id: {partition_id!r} is not a safe XML identifier "
                    "(letters/digits/underscore/dot/dash, not a reserved BEAST id)"
                )
                continue
            if partition_id in seen_ids:
                errors.append(
                    f"{where}.id: duplicate partition id {partition_id!r} "
                    f"(already declared at partitions[{seen_ids[partition_id]}])"
                )
                continue
            seen_ids[partition_id] = i

            alignment_id = entry.get("alignment_id") or entry.get("alignment")
            if alignment_id is None:
                errors.append(f"{where}: alignment_id is required")
                continue
            if alignment_id not in alignment_map:
                errors.append(f"{where}.alignment_id: {alignment_id!r} not declared in alignments")
                continue
            alignment = alignment_map[alignment_id]
            from_pos = entry.get("from")
            to_pos = entry.get("to")
            if (from_pos is None) != (to_pos is None):
                errors.append(
                    f"{where}: 'from' and 'to' must be given together "
                    f"(got from={from_pos!r}, to={to_pos!r})"
                )
                continue
            if from_pos is not None:
                from_int = _strict_int(from_pos, f"{where}.from", errors)
                to_int = _strict_int(to_pos, f"{where}.to", errors)
                if from_int is None or to_int is None:
                    continue
                try:
                    validate_partition_range(alignment, from_int, to_int, partition_id=partition_id)
                except PartitionError as exc:
                    errors.append(f"{where}: {exc}")
                    continue
                windowed = alignment.with_range(from_int, to_int)
            else:
                # No explicit window: the whole alignment.  Keep explicit 1..n
                # so the model layer always emits <patterns from/to>.
                try:
                    windowed = alignment.with_range(None, None)
                except ValueError as exc:
                    errors.append(f"{where}: {exc}")
                    continue

            site_model = self._parse_site_model(
                entry.get("site_model", {}), where, windowed, errors
            )
            clock_model = self._parse_clock_model(entry.get("clock_model", {}), where, errors)
            tree = entry.get("tree", "shared")
            clock = entry.get("clock", "shared")
            if tree != "shared":
                errors.append(f"{where}: tree: {tree!r} is not supported (only 'shared')")
            if clock not in ("shared", "separate"):
                errors.append(f"{where}: clock: invalid {clock!r} (valid: shared, separate)")
            elif clock == "separate" and tree != "shared":
                errors.append(f"{where}: clock: separate requires tree: shared")
            if (
                site_model is not None
                and clock_model is not None
                and tree == "shared"
                and clock in ("shared", "separate")
            ):
                result.append(
                    Partition(
                        id=partition_id,
                        alignment=windowed,
                        site_model=site_model,
                        clock_model=clock_model,
                        tree=tree,
                        clock=str(clock),
                    )
                )
        return result

    def _parse_site_model(
        self, raw: Any, where: str, alignment, errors: List[str]
    ) -> Optional[SiteModelConfig]:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append(f"{where}.site_model: must be a mapping")
            return None
        _check_known_keys(
            raw,
            {"substitution_model", "gamma_categories", "gamma_shape", "proportion_invariant"},
            f"{where}.site_model",
            errors,
        )
        subst_raw = raw.get("substitution_model", {})
        if not isinstance(subst_raw, dict):
            errors.append(f"{where}.site_model.substitution_model: must be a mapping")
            return None
        _check_known_keys(
            subst_raw,
            {"type", "kappa", "kappa1", "kappa2", "rates", "frequencies"},
            f"{where}.site_model.substitution_model",
            errors,
        )
        subst_type = subst_raw.get("type", "hky")
        if isinstance(subst_type, str):
            subst_type = subst_type.strip().lower()
        if subst_type not in VALID_SUBSTITUTION_MODELS:
            errors.append(
                f"{where}.site_model.substitution_model.type: invalid {subst_type!r} "
                f"(valid: {', '.join(VALID_SUBSTITUTION_MODELS)})"
            )
        try:
            spec = self.registry.substitution_spec(subst_type)
        except RegistryError as exc:
            errors.append(str(exc))
            return None
        if spec.data_allowed and alignment is not None:
            dt = (
                alignment.data_type.value
                if hasattr(alignment.data_type, "value")
                else str(alignment.data_type)
            )
            if dt not in spec.data_allowed:
                errors.append(
                    f"{where}.site_model.substitution_model.type: {subst_type!r} cannot be "
                    f"used with data type {dt!r}"
                )

        substitution_model: Dict[str, Any] = {"type": subst_type}
        kappa = _parse_real_parameter(
            subst_raw.get("kappa"), f"{where}.kappa", errors, default_lower=0.0
        )
        if "kappa" in subst_raw and subst_type != "hky":
            errors.append(
                f"{where}.kappa: is only valid for substitution_model.type 'hky' "
                f"(not {subst_type!r}; it would be silently ignored)"
            )
        if kappa is not None and subst_type == "hky":
            substitution_model["kappa"] = kappa
        if subst_type == "jc69":
            substitution_model["kappa"] = RealParameter(
                value=1.0, lower=0.0, upper=1000.0, estimate=False
            )
        if subst_type != "tn93":
            for ignored in ("kappa1", "kappa2"):
                if ignored in subst_raw:
                    errors.append(
                        f"{where}.{ignored}: only valid for substitution_model.type "
                        "'tn93' (it would be silently ignored)"
                    )
        if subst_type != "gtr" and "rates" in subst_raw:
            errors.append(f"{where}.rates: only valid for substitution_model.type 'gtr'")
        for key in ("kappa1", "kappa2"):
            if key in subst_raw and subst_type == "tn93":
                param = _parse_real_parameter(
                    subst_raw[key], f"{where}.{key}", errors, default_lower=0.0
                )
                if param is not None:
                    substitution_model[key] = param
        if "rates" in subst_raw and subst_type == "gtr":
            rates_raw = subst_raw["rates"]
            if not isinstance(rates_raw, dict):
                errors.append(f"{where}.rates: must be a mapping")
            else:
                _check_known_keys(rates_raw, {"mode", "value"}, f"{where}.rates", errors)
                mode = rates_raw.get("mode", "estimated")
                if mode not in ("estimated", "fixed"):
                    errors.append(f"{where}.rates.mode: invalid {mode!r} (valid: estimated, fixed)")
                value = rates_raw.get("value")
                default = [1.0] * 6
                if value is not None:
                    if not isinstance(value, (list, tuple)) or len(value) != 6:
                        errors.append(
                            f"{where}.rates.value: must be a list of exactly six "
                            "relative rates (BEAST drops one to normalise the matrix)"
                        )
                    else:
                        parsed = [
                            _strict_float(v, f"{where}.rates.value[{j}]", errors)
                            for j, v in enumerate(value)
                        ]
                        if all(v is not None for v in parsed):
                            default = [float(v) for v in parsed if v is not None]
                params = []
                for name, init in zip(
                    ("rateAC", "rateAG", "rateAT", "rateCG", "rateCT", "rateGT"), default
                ):
                    params.append(
                        RealParameter(
                            value=init,
                            lower=0.0,
                            upper=100.0,
                            estimate=(mode != "fixed"),
                        )
                    )
                substitution_model["rates"] = {"mode": mode, "params": params}
        if "frequencies" in subst_raw:
            freq_raw = subst_raw["frequencies"]
            if not isinstance(freq_raw, dict):
                errors.append(f"{where}.frequencies: must be a mapping")
            else:
                _check_known_keys(
                    freq_raw, {"mode", "value", "dimension"}, f"{where}.frequencies", errors
                )
                raw_mode = freq_raw.get("mode", "estimated")
                mode = (
                    FREQUENCY_MODE_ALIASES.get(str(raw_mode).strip().lower())
                    if isinstance(raw_mode, str)
                    else None
                )
                if mode is None:
                    errors.append(
                        f"{where}.frequencies.mode: invalid {raw_mode!r} "
                        "(valid: estimated, uniform/equal, empirical, fixed)"
                    )
                    mode = "estimated"
                value = freq_raw.get("value")
                if subst_type in AMINO_ACID_MODELS:
                    # The empirical amino-acid models carry their own
                    # frequencies.  ``mode: empirical`` without a value is the
                    # documented no-op spelling used by the examples.
                    if mode != "empirical" or value is not None or "dimension" in freq_raw:
                        errors.append(
                            f"{where}.frequencies: empirical amino-acid model "
                            f"{subst_type!r} carries its own frequencies; only "
                            "'mode: empirical' without value/dimension is accepted"
                        )
                else:
                    dimension = freq_raw.get("dimension", 4)
                    if (
                        isinstance(dimension, bool)
                        or not isinstance(dimension, int)
                        or dimension != 4
                    ):
                        errors.append(
                            f"{where}.frequencies.dimension: nucleotide "
                            f"frequencies have exactly four components "
                            f"(got {dimension!r})"
                        )
                    parsed_value: Optional[List[float]] = None
                    if value is not None:
                        tokens: Optional[List[Any]]
                        if isinstance(value, str):
                            tokens = value.split()
                        elif isinstance(value, (list, tuple)):
                            tokens = list(value)
                        else:
                            tokens = None
                        if tokens is None or len(tokens) != 4:
                            errors.append(
                                f"{where}.frequencies.value: must be four numbers "
                                "(nucleotide frequencies)"
                            )
                        else:
                            numbers = [
                                _strict_float(token, f"{where}.frequencies.value[{i}]", errors)
                                for i, token in enumerate(tokens)
                            ]
                            if all(number is not None for number in numbers):
                                parsed_frequencies = [
                                    float(number) for number in numbers if number is not None
                                ]
                                if any(number < 0.0 for number in parsed_frequencies):
                                    errors.append(
                                        f"{where}.frequencies.value: components "
                                        "must be non-negative"
                                    )
                                elif abs(sum(parsed_frequencies) - 1.0) > 1e-6:
                                    errors.append(
                                        f"{where}.frequencies.value: components "
                                        f"must sum to 1 (got {sum(parsed_frequencies):.12g})"
                                    )
                                else:
                                    parsed_value = parsed_frequencies
                        if mode in ("uniform", "empirical"):
                            errors.append(
                                f"{where}.frequencies.value: is only used with "
                                "mode 'estimated' (initial values) or 'fixed'; "
                                f"mode {mode!r} computes its own vector"
                            )
                    substitution_model["frequencies"] = {
                        "mode": mode,
                        "value": parsed_value if parsed_value is not None else value,
                        "dimension": 4,
                    }
        elif subst_type not in ("wag", "jtt", "dayhoff", "blosum62", "cprev", "mtrev"):
            substitution_model["frequencies"] = {"mode": "estimated", "dimension": 4}

        gamma_categories = subst_raw_cats(raw.get("gamma_categories", 1), where, errors)
        gamma_shape = _parse_real_parameter(
            raw.get("gamma_shape"),
            f"{where}.gamma_shape",
            errors,
            default_lower=0.0,
            default_value=0.5,
        )
        if gamma_shape is not None and gamma_categories <= 1:
            errors.append(
                f"{where}.gamma_shape: requires gamma_categories > 1 "
                "(it would otherwise be silently ignored)"
            )
            gamma_shape = None
        pi_raw = raw.get("proportion_invariant")
        proportion_invariant = None
        if pi_raw is not None:
            proportion_invariant = _parse_real_parameter(
                pi_raw,
                f"{where}.proportion_invariant",
                errors,
                default_lower=0.0,
                default_upper=1.0,
                default_value=0.1,
            )
        return SiteModelConfig(
            substitution_model=substitution_model,
            gamma_categories=gamma_categories,
            gamma_shape=gamma_shape,
            proportion_invariant=proportion_invariant,
        )

    def _parse_clock_model(
        self, raw: Any, where: str, errors: List[str]
    ) -> Optional[ClockModelConfig]:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append(f"{where}.clock_model: must be a mapping")
            return None
        _check_known_keys(
            raw,
            {"type", "clock_rate", "ucld_mean", "ucld_stdev", "exponential_mean", "rlc_indicators"},
            f"{where}.clock_model",
            errors,
        )
        if "rlc_rate" in raw:
            errors.append(
                f"{where}.clock_model.rlc_rate: not supported; use rlc_indicators "
                "(the number of rate categories), not a rate"
            )
        clock_type = raw.get("type", "strict")
        if isinstance(clock_type, str):
            clock_type = clock_type.strip().lower()
        if clock_type not in VALID_CLOCK_MODELS:
            errors.append(
                f"{where}.clock_model.type: invalid {clock_type!r} "
                f"(valid: {', '.join(VALID_CLOCK_MODELS)})"
            )
            clock_type = "strict"
        clock_rate = _parse_real_parameter(
            raw.get("clock_rate"), f"{where}.clock_rate", errors, default_lower=0.0
        )
        ucld_mean = _parse_real_parameter(
            raw.get("ucld_mean"), f"{where}.ucld_mean", errors, default_lower=0.0
        )
        ucld_stdev = _parse_real_parameter(
            raw.get("ucld_stdev"), f"{where}.ucld_stdev", errors, default_lower=0.0
        )
        exponential_mean = _parse_real_parameter(
            raw.get("exponential_mean"), f"{where}.exponential_mean", errors, default_lower=0.0
        )
        rlc_indicators = raw.get("rlc_indicators", 2)
        if (
            isinstance(rlc_indicators, bool)
            or not isinstance(rlc_indicators, int)
            or rlc_indicators < 2
        ):
            errors.append(f"{where}.rlc_indicators: must be an integer >= 2")
            rlc_indicators = 2

        # Reject parameters that the selected clock type would ignore,
        # and normalise the documented synonyms onto the emitted names.
        if clock_type != "strict":
            if clock_rate is not None and ucld_mean is not None:
                errors.append(f"{where}.clock_model: give either clock_rate or ucld_mean, not both")
            if clock_rate is not None:
                ucld_mean = clock_rate
                clock_rate = None
        else:
            if ucld_mean is not None or ucld_stdev is not None:
                errors.append(
                    f"{where}.clock_model: ucld_mean/ucld_stdev require type 'ucln' or 'uce'"
                )
            if exponential_mean is not None:
                errors.append(f"{where}.clock_model.exponential_mean requires type 'uce'")
            if "rlc_indicators" in raw:
                errors.append(f"{where}.clock_model.rlc_indicators requires type 'rlc'")
        if clock_type == "ucln":
            if exponential_mean is not None:
                errors.append(f"{where}.clock_model.exponential_mean requires type 'uce'")
            if "rlc_indicators" in raw:
                errors.append(f"{where}.clock_model.rlc_indicators requires type 'rlc'")
        if clock_type == "uce":
            if ucld_stdev is not None:
                errors.append(f"{where}.clock_model.ucld_stdev is not used by type 'uce'")
            if exponential_mean is not None:
                if ucld_mean is not None:
                    errors.append(
                        f"{where}.clock_model: give either exponential_mean or "
                        "ucld_mean, not both"
                    )
                else:
                    ucld_mean = exponential_mean
            if "rlc_indicators" in raw:
                errors.append(f"{where}.clock_model.rlc_indicators requires type 'rlc'")
        if clock_type == "rlc":
            if ucld_stdev is not None:
                errors.append(f"{where}.clock_model.ucld_stdev is not used by type 'rlc'")
            if exponential_mean is not None:
                errors.append(f"{where}.clock_model.exponential_mean is not used by type 'rlc'")
        return ClockModelConfig(
            type=ClockModelType(str(clock_type)),
            clock_rate=clock_rate,
            ucld_mean=ucld_mean,
            ucld_stdev=ucld_stdev,
            exponential_mean=exponential_mean,
            rlc_indicators=int(rlc_indicators),
        )

    def _parse_tree_prior(self, raw: Any, errors: List[str]) -> TreePriorConfig:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append("tree_prior: must be a mapping")
            return TreePriorConfig()
        _check_known_keys(
            raw,
            {
                "type",
                "birth_rate",
                "birth_minus_death",
                "relative_death",
                "population_size",
                "growth_rate",
                "ancestral_population_size",
                "ancestral_population_proportion",
                "t50",
                "skyline_groups",
                "skyline_pop_sizes",
                "ebsp_pop_sizes",
                "conditional_on_root",
            },
            "tree_prior",
            errors,
        )
        if "ancestral_population_size" in raw:
            errors.append(
                "tree_prior.ancestral_population_size: not a BEAST coalescent "
                "parameter; use population_size and, for the expansion "
                "prior, ancestral_population_proportion (N1/N0 in [0, 1])"
            )
        tp_type = raw.get("type", "yule")
        requested_type = tp_type
        if isinstance(tp_type, str):
            tp_type = TREE_PRIOR_ALIASES.get(tp_type.strip().lower())
        if tp_type not in VALID_TREE_PRIORS:
            errors.append(
                f"tree_prior.type: invalid {requested_type!r} "
                f"(valid: {', '.join(VALID_TREE_PRIORS)}; "
                "documented aliases such as birthDeath/constant/exponential are accepted)"
            )
            tp_type = "yule"

        # Which parameters does each tree prior consume?  Anything else would
        # be silently ignored by the generator, so reject it.
        applicable = {
            "yule": {"birth_rate"},
            "birth_death": {"birth_minus_death", "relative_death", "conditional_on_root"},
            "coalescent_constant": {"population_size"},
            "coalescent_exponential": {"population_size", "growth_rate"},
            "coalescent_logistic": {
                "population_size",
                "growth_rate",
                "t50",
            },
            "coalescent_expansion": {
                "population_size",
                "ancestral_population_proportion",
                "growth_rate",
            },
            "skyline": {"population_size", "skyline_groups", "skyline_pop_sizes"},
            "ebsp": {"population_size", "ebsp_pop_sizes"},
        }[tp_type]
        for key in raw:
            if key == "type":
                continue
            if key not in applicable:
                errors.append(
                    f"tree_prior.{key}: is not used by tree prior type {tp_type!r} "
                    f"(applicable: {', '.join(sorted(applicable))})"
                )

        params: Dict[str, Any] = {}
        for key, lower in (
            ("birth_rate", 0.0),
            ("birth_minus_death", 0.0),
            ("relative_death", 0.0),
            ("population_size", 0.0),
            (
                "growth_rate",
                0.0 if tp_type in ("coalescent_logistic", "coalescent_expansion") else None,
            ),
            ("t50", 0.0),
        ):
            if key in raw:
                param = _parse_real_parameter(
                    raw[key], f"tree_prior.{key}", errors, default_lower=lower
                )
                if param is not None:
                    params[key] = param
        if "ancestral_population_proportion" in raw:
            proportion = _parse_real_parameter(
                raw["ancestral_population_proportion"],
                "tree_prior.ancestral_population_proportion",
                errors,
                default_lower=0.0,
                default_upper=1.0,
                default_value=0.1,
            )
            if proportion is not None:
                params["ancestral_population_proportion"] = proportion
        groups: Optional[int] = None
        if "skyline_groups" in raw:
            groups = raw["skyline_groups"]
            if isinstance(groups, bool) or not isinstance(groups, int) or groups < 1:
                errors.append("tree_prior.skyline_groups: must be a positive integer")
                groups = None
            else:
                params["skyline_groups"] = groups
        if "skyline_pop_sizes" in raw:
            sizes = self._parse_population_sizes(
                raw["skyline_pop_sizes"], "tree_prior.skyline_pop_sizes", errors, default=1.0
            )
            if sizes is not None:
                if groups is not None and len(sizes) != groups:
                    errors.append(
                        "tree_prior.skyline_pop_sizes: length "
                        f"{len(sizes)} does not match skyline_groups={groups}"
                    )
                elif groups is None:
                    params["skyline_groups"] = len(sizes)
                params["skyline_pop_sizes"] = sizes
        elif groups is not None:
            params["skyline_pop_sizes"] = [1.0] * groups
        if "ebsp_pop_sizes" in raw:
            sizes = self._parse_population_sizes(
                raw["ebsp_pop_sizes"], "tree_prior.ebsp_pop_sizes", errors, default=0.5
            )
            if sizes is not None:
                params["ebsp_pop_sizes"] = sizes
        if "conditional_on_root" in raw:
            cond = _strict_bool(
                raw["conditional_on_root"], "tree_prior.conditional_on_root", errors
            )
            if cond is not None:
                params["conditional_on_root"] = cond
        conditional = bool(params.get("conditional_on_root", False))
        return TreePriorConfig(
            type=TreePriorType(str(tp_type)),
            params=params,
            conditional_on_root=conditional,
        )

    @staticmethod
    def _parse_population_sizes(
        raw: Any, where: str, errors: List[str], default: float
    ) -> Optional[List[float]]:
        """Normalize a skyline/EBSP population-size spec to a positive list.

        A positive integer is a dimension (expanded with ``default`` initial
        values); a list supplies the initial values directly.
        """
        if isinstance(raw, bool):
            errors.append(f"{where}: must be a positive integer or a list of numbers")
            return None
        if isinstance(raw, int):
            if raw < 1:
                errors.append(f"{where}: dimension must be a positive integer")
                return None
            return [float(default)] * raw
        if not isinstance(raw, (list, tuple)):
            errors.append(f"{where}: must be a positive integer or a list of numbers")
            return None
        if not raw:
            errors.append(f"{where}: list must not be empty")
            return None
        sizes: List[float] = []
        for index, value in enumerate(raw):
            parsed = _strict_float(value, f"{where}[{index}]", errors)
            if parsed is None:
                continue
            if parsed <= 0:
                errors.append(f"{where}[{index}]: population size must be > 0")
                continue
            sizes.append(parsed)
        if len(sizes) != len(raw):
            return None
        return sizes

    def _parse_calibrations(
        self, raw: Any, known_taxa: Set[str], errors: List[str]
    ) -> List[CalibrationPoint]:
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            errors.append("calibrations: must be a list")
            return []

        # Pre-scan names so duplicates are rejected with both YAML paths
        # before any expensive distribution validation.
        seen_names: Dict[str, int] = {}
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            if name in seen_names:
                errors.append(
                    f"calibrations[{i}].name: duplicate calibration name {name!r} "
                    f"(already declared at calibrations[{seen_names[name]}])"
                )
            else:
                seen_names[name] = i

        result: List[CalibrationPoint] = []
        node_keys: Dict[Tuple[frozenset, str], str] = {}
        root_name: Optional[str] = None
        for i, entry in enumerate(raw):
            where = f"calibrations[{i}]"
            cal = self._parse_calibration(entry, errors, known_taxa, where)
            if cal is None:
                continue
            if cal.name in seen_names and seen_names[cal.name] != i:
                # Already reported; still keep the first entry only.
                continue
            if cal.is_root:
                if root_name is not None:
                    errors.append(
                        f"{where}.taxa: at most one root calibration (taxa: null) is "
                        f"allowed; {root_name!r} is already the root"
                    )
                    continue
                root_name = cal.name
            if cal.node_key in node_keys:
                errors.append(
                    f"{where}: duplicate clade definition: {cal.name!r} calibrates "
                    f"the same {cal.node_type} node as {node_keys[cal.node_key]!r} "
                    "(same taxon set and calibration_method)"
                )
            else:
                node_keys[cal.node_key] = cal.name
            result.append(cal)

        # Partial taxon-set overlap makes it impossible for both clades to be
        # monophyletic on one tree; nested/disjoint clades are fine.  Age
        # ordering itself is Gate 2 (diagnostics) territory.
        for index, left in enumerate(result):
            if left.is_root:
                continue
            for right in result[index + 1 :]:
                if right.is_root:
                    continue
                if not (left.monophyletic and right.monophyletic):
                    continue
                intersection = left.taxa_set & right.taxa_set
                if (
                    intersection
                    and not left.taxa_set <= right.taxa_set
                    and not right.taxa_set <= left.taxa_set
                ):
                    errors.append(
                        f"calibrations: clades {left.name!r} and {right.name!r} "
                        "partially overlap but their taxon sets are not nested; "
                        "both cannot be monophyletic on one tree"
                    )
        # Give every point its sibling list so calibration.children_of() and
        # diagnostics can build the containment tree from a single point
        # .
        context = tuple(result)
        for cal in result:
            cal.calibration_context = context
        return result

    def _parse_calibration(
        self,
        cal_raw: Any,
        errors: List[str],
        known_taxa: Set[str],
        where: str,
    ) -> Optional[CalibrationPoint]:
        if not isinstance(cal_raw, dict):
            errors.append(f"{where}: must be a mapping")
            return None
        _check_known_keys(
            cal_raw,
            {
                "name",
                "taxa",
                "monophyletic",
                "distribution",
                "calibration_method",
                "provenance",
                "hyperpriors",
            },
            where,
            errors,
        )
        if "tipsonly" in cal_raw:
            errors.append(
                f"{where}.tipsonly: not implemented; remove it (tip dates belong in "
                "the top-level tip_dates section)"
            )
        name = cal_raw.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{where}: 'name' is required")
            return None
        if not is_safe_identifier(name):
            errors.append(
                f"{where}.name: {name!r} is not a safe XML/artifact identifier "
                "(letters/digits/underscore/dot/dash, not a reserved BEAST id)"
            )
            return None
        if "taxa" not in cal_raw:
            errors.append(f"{where}.taxa: is required; use 'taxa: null' for a root calibration")
            return None
        taxa_raw = cal_raw.get("taxa")
        taxa: Optional[List[str]] = None
        if taxa_raw is None:
            #: explicit root calibration.
            taxa = None
        elif not isinstance(taxa_raw, list) or not all(isinstance(t, str) for t in taxa_raw):
            errors.append(f"{where}.taxa: must be a list of taxon names or null")
            return None
        elif not taxa_raw:
            errors.append(f"{where}.taxa: must contain at least one taxon (or be null for root)")
            return None
        else:
            unknown = [t for t in taxa_raw if t not in known_taxa]
            if unknown:
                errors.append(f"{where}: unknown taxon name(s) in calibration {name!r}: {unknown}")
                return None
            duplicates = sorted({t for t in taxa_raw if taxa_raw.count(t) > 1})
            if duplicates:
                errors.append(
                    f"{where}.taxa: duplicate taxon name(s) in calibration "
                    f"{name!r}: {duplicates}"
                )
                return None
            taxa = list(taxa_raw)
        monophyletic = cal_raw.get("monophyletic", True)
        if not isinstance(monophyletic, bool):
            value = _strict_bool(monophyletic, f"{where}.monophyletic", errors)
            monophyletic = value if value is not None else True
        method_raw = cal_raw.get("calibration_method", "clade")
        method = (
            CALIBRATION_METHOD_ALIASES.get(str(method_raw).strip().lower())
            if isinstance(method_raw, str)
            else None
        )
        if method is None:
            errors.append(
                f"{where}.calibration_method: invalid {method_raw!r} " "(valid: clade/crown, stem)"
            )
            method = "clade"
        if taxa is None and method == "stem":
            errors.append(f"{where}.calibration_method: a root calibration cannot be 'stem'")
        if "distribution" not in cal_raw:
            errors.append(
                f"{where}.distribution: is required for every calibration "
                "(no Uniform(0,1) fallback; add a distribution or remove the calibration)"
            )
            distribution = None
        else:
            distribution = self._parse_distribution(
                cal_raw["distribution"], f"{where}.distribution", errors
            )
            if distribution is None:
                return None
        provenance = None
        if "provenance" in cal_raw:
            prov_raw = cal_raw["provenance"]
            if not isinstance(prov_raw, dict):
                errors.append(f"{where}.provenance: must be a mapping")
            else:
                _check_known_keys(
                    prov_raw,
                    {
                        "source",
                        "reference",
                        "calibration_type",
                        "original_age_min",
                        "original_age_max",
                        "notes",
                    },
                    f"{where}.provenance",
                    errors,
                )
                src = prov_raw.get("source", "other")
                if src not in ("fossil", "literature", "other"):
                    errors.append(
                        f"{where}.provenance.source: invalid {src!r} "
                        "(valid: fossil, literature, other)"
                    )
                cal_type_raw = prov_raw.get("calibration_type", "soft")
                cal_type = (
                    PROVENANCE_TYPE_ALIASES.get(str(cal_type_raw).strip().lower())
                    if isinstance(cal_type_raw, str)
                    else None
                )
                if cal_type is None:
                    errors.append(
                        f"{where}.provenance.calibration_type: invalid {cal_type_raw!r} "
                        "(valid: soft, hard; upper/lower are accepted as soft aliases)"
                    )
                    cal_type = "soft"
                age_min = prov_raw.get("original_age_min")
                age_max = prov_raw.get("original_age_max")
                if age_min is not None:
                    age_min = parse_number(age_min, f"{where}.provenance.original_age_min")
                if age_max is not None:
                    age_max = parse_number(age_max, f"{where}.provenance.original_age_max")
                if age_min is not None and age_max is not None and age_min > age_max:
                    errors.append(
                        f"{where}.provenance: original_age_min {age_min} > original_age_max {age_max}"
                    )
                provenance = Provenance(
                    source=str(src),
                    reference=str(prov_raw.get("reference", "")),
                    calibration_type=str(cal_type),
                    original_age_min=age_min,
                    original_age_max=age_max,
                    notes=str(prov_raw.get("notes", "")),
                )
        hyperpriors: Dict[str, HyperpriorConfig] = {}
        if "hyperpriors" in cal_raw:
            hp_raw = cal_raw["hyperpriors"]
            if not isinstance(hp_raw, dict):
                errors.append(f"{where}.hyperpriors: must be a mapping")
            elif distribution is None:
                errors.append(f"{where}.hyperpriors: requires a calibration distribution")
            else:
                allowed_targets = DISTRIBUTION_HYPERPRIOR_TARGETS.get(distribution.type, ())
                emittable_targets = HYPERPRIOR_EMITTABLE_TARGETS.get(distribution.type, ())
                for pname, hpc in hp_raw.items():
                    hp_where = f"{where}.hyperpriors.{pname}"
                    if pname not in allowed_targets:
                        errors.append(
                            f"{hp_where}: {pname!r} is not a hyperprior-able "
                            f"parameter of distribution {distribution.type!r} "
                            f"(allowed: {', '.join(allowed_targets) or 'none'})"
                        )
                        continue
                    if pname not in emittable_targets:
                        errors.append(
                            f"{hp_where}: hyperpriors for "
                            f"{distribution.type}.{pname} are not implemented; BEAST 1 "
                            "can only parameterize "
                            f"{', '.join(f'{k}.{v}' for k, targets in HYPERPRIOR_EMITTABLE_TARGETS.items() for v in targets) or 'none'}"
                            " -- remove the hyperprior or provide an explicit prior"
                        )
                        continue
                    hyper = self._parse_distribution(hpc, hp_where, errors)
                    if hyper is not None:
                        hyperpriors[str(pname)] = HyperpriorConfig(
                            type=hyper.type,
                            parameters=hyper.parameters,
                            offset=hyper.offset,
                        )
        return CalibrationPoint(
            name=str(name),
            taxa=taxa,
            monophyletic=bool(monophyletic),
            distribution=distribution,
            calibration_method=str(method),
            provenance=provenance,
            hyperpriors=hyperpriors,
            is_root=taxa is None,
        )

    def _parse_distribution(
        self, raw: Any, where: str, errors: List[str]
    ) -> Optional[DistributionConfig]:
        """Parse + normalize a prior distribution (calibration/hyper/parameter).

        Normalization happens exactly once here so the model layer only ever
        sees canonical parameter names.
        """
        if not isinstance(raw, dict):
            errors.append(f"{where}: must be a mapping")
            return None
        _check_known_keys(raw, {"type", "parameters", "offset"}, where, errors)
        raw_type = raw.get("type")
        if not isinstance(raw_type, str) or not raw_type.strip():
            errors.append(f"{where}.type: is required")
            return None
        key = "_".join(raw_type.strip().lower().replace("-", "_").split())
        dist_type = _DISTRIBUTION_ALIASES.get(key, key)
        if dist_type in DISTRIBUTION_UNSUPPORTED:
            errors.append(
                f"{where}.type: {raw_type!r} is not supported: "
                f"{DISTRIBUTION_UNSUPPORTED[dist_type]}"
            )
            return None
        if dist_type not in DISTRIBUTION_PARAMETERS:
            errors.append(
                f"{where}.type: invalid {raw_type!r} "
                f"(valid: {', '.join(sorted(DISTRIBUTION_PARAMETERS))})"
            )
            return None

        params_raw = raw.get("parameters", {})
        if params_raw is None:
            params_raw = {}
        if not isinstance(params_raw, dict):
            errors.append(f"{where}.parameters: must be a mapping")
            return None
        aliases = DISTRIBUTION_PARAMETER_ALIASES.get(dist_type, {})
        canonical: Dict[str, Any] = {}
        for key_name, value in params_raw.items():
            target = aliases.get(key_name)
            if target is None:
                errors.append(
                    f"{where}.parameters: unknown parameter {key_name!r} for "
                    f"distribution {dist_type!r} "
                    f"(valid: {', '.join(sorted(aliases)) or 'none'})"
                )
                continue
            if target in canonical:
                errors.append(
                    f"{where}.parameters: {key_name!r} duplicates the canonical "
                    f"parameter {target!r}"
                )
                continue
            canonical[target] = value

        # Exponential: ``rate`` is a documented spelling; convert it once to
        # the canonical mean, never leave two competing keys.
        if dist_type == "exponential":
            if "rate" in canonical:
                if "mean" in canonical:
                    errors.append(
                        f"{where}.parameters: give either 'mean' (preferred) or " "'rate', not both"
                    )
                    canonical.pop("rate", None)
                else:
                    rate = _strict_float(canonical.pop("rate"), f"{where}.parameters.rate", errors)
                    if rate is None or rate <= 0:
                        errors.append(f"{where}.parameters.rate: must be > 0")
                    else:
                        canonical["mean"] = 1.0 / rate
            canonical.setdefault("mean", 1.0)

        for param_name, value in list(canonical.items()):
            if param_name == "meanInRealSpace":
                if not isinstance(value, bool):
                    errors.append(f"{where}.parameters.meanInRealSpace: must be a boolean")
                    canonical.pop(param_name, None)
                continue
            parsed = _strict_float(value, f"{where}.parameters.{param_name}", errors)
            if parsed is None:
                canonical.pop(param_name, None)
                continue
            canonical[param_name] = parsed

        required = DISTRIBUTION_REQUIRED_PARAMETERS.get(dist_type, ())
        missing = [name for name in required if name not in canonical]
        if missing:
            errors.append(
                f"{where}.parameters: missing required parameter(s) for "
                f"{dist_type}: {', '.join(missing)}"
            )

        # Positivity / ordering.
        positive = {
            "normal": ("stdev",),
            "lognormal": ("stdev",),
            "exponential": ("mean",),
            "gamma": ("shape", "scale"),
            "inverse_gamma": ("shape", "scale"),
            "beta": ("alpha", "beta"),
            "poisson": ("mean",),
            "half_normal": ("stdev",),
            "laplace": ("scale",),
            "half_t": ("scale", "df"),
        }.get(dist_type, ())
        for param_name in positive:
            value = canonical.get(param_name)
            if isinstance(value, float) and value <= 0:
                errors.append(f"{where}.parameters.{param_name}: must be > 0, got {value}")
        if dist_type == "lognormal" and canonical.get("mean", 1.0) <= 0:
            errors.append(f"{where}.parameters.mean: must be > 0 for lognormal")
        if dist_type == "uniform":
            lower = canonical.get("lower")
            upper = canonical.get("upper")
            if isinstance(lower, float) and isinstance(upper, float) and lower >= upper:
                errors.append(f"{where}.parameters: uniform lower {lower} >= upper {upper}")

        offset_raw = raw.get("offset", 0.0)
        try:
            offset = parse_number(offset_raw, f"{where}.offset") if offset_raw is not None else 0.0
        except ValueError as exc:
            errors.append(str(exc))
            offset = 0.0
        if offset < 0:
            errors.append(f"{where}.offset: must be >= 0, got {offset}")
        if offset and dist_type not in DISTRIBUTION_OFFSET_SUPPORTED | DISTRIBUTION_OFFSET_REQUIRED:
            errors.append(
                f"{where}.offset: distribution {dist_type!r} has no BEAST offset "
                "attribute; remove offset or use "
                f"{', '.join(sorted(DISTRIBUTION_OFFSET_SUPPORTED))}"
            )
            offset = 0.0

        return DistributionConfig(
            type=dist_type,
            parameters=canonical,
            offset=float(offset),
        )

    def _check_provenance(
        self, cal: CalibrationPoint, warnings: List[str], errors: List[str]
    ) -> None:
        prov = cal.provenance
        assert prov is not None
        if prov.original_age_min is None or prov.original_age_max is None:
            return
        assert cal.distribution is not None
        q = distribution_quantiles(
            cal.distribution.type, cal.distribution.parameters, cal.distribution.offset
        )
        if q is None:
            return
        q025, q975 = q
        overlap = min(prov.original_age_max, q975) - max(prov.original_age_min, q025)
        if overlap >= 0:
            return
        message = (
            f"calibration {cal.name!r}: original fossil age range "
            f"[{prov.original_age_min}, {prov.original_age_max}] does not intersect "
            f"the prior 95% interval [{q025:.4g}, {q975:.4g}]"
        )
        if prov.calibration_type == "hard":
            errors.append(f"{message} (hard violation)")
        else:
            warnings.append(f"{message} (soft violation, warning)")

    def _parse_parameter_priors(self, raw: Any, errors: List[str]) -> List[Dict[str, Any]]:
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            errors.append("parameter_priors: must be a list")
            return []
        result = []
        for i, entry in enumerate(raw):
            where = f"parameter_priors[{i}]"
            if not isinstance(entry, dict):
                errors.append(f"{where}: must be a mapping")
                continue
            _check_known_keys(entry, {"parameter", "distribution"}, where, errors)
            parameter = entry.get("parameter")
            # Recognized short aliases plus exact emitted parameter ids
            # (e.g. ``gene2.clock.rate``, ``skyline.popSize``).  Exact ids are
            # resolved by the model layer, which raises a clear
            # ModelAssemblyError when nothing matches.
            if not isinstance(parameter, str) or not parameter:
                errors.append(f"{where}.parameter: must be a non-empty string")
                continue
            if parameter not in RECOGNIZED_PARAMETER_KEYS and not is_safe_identifier(parameter):
                errors.append(
                    f"{where}: unknown parameter {parameter!r} "
                    f"(recognized aliases: {', '.join(RECOGNIZED_PARAMETER_KEYS)}; "
                    "exact ids such as gene2.clock.rate or skyline.popSize are "
                    "also accepted)"
                )
                continue
            dist_raw = entry.get("distribution")
            if not isinstance(dist_raw, dict):
                errors.append(f"{where}.distribution: must be a mapping")
                continue
            dist = self._parse_distribution(dist_raw, f"{where}.distribution", errors)
            if dist is None:
                continue
            result.append({"parameter": parameter, "distribution": dist})
        return result

    def _parse_mcmc(self, raw: Any, errors: List[str]) -> MCMCConfig:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append("mcmc: must be a mapping")
            return MCMCConfig()
        _check_known_keys(
            raw,
            {
                "type",
                "chain_length",
                "pre_burnin",
                "operator_weights",
                "auto_optimize",
                "autoOptimize",
            },
            "mcmc",
            errors,
        )
        mtype = raw.get("type", "standard")
        if mtype != "standard":
            errors.append(f"mcmc.type: invalid {mtype!r}; BEAST 1 supports only 'standard' MCMC")
        chain_length = 10000000
        if "chain_length" in raw:
            cl = raw["chain_length"]
            if isinstance(cl, bool) or not isinstance(cl, int) or cl <= 0:
                errors.append("mcmc.chain_length: must be a positive integer")
            else:
                chain_length = cl
        pre_burnin = 0
        if "pre_burnin" in raw:
            pb = raw["pre_burnin"]
            if isinstance(pb, bool) or not isinstance(pb, int) or pb < 0:
                errors.append("mcmc.pre_burnin: must be a non-negative integer")
            elif pb >= chain_length:
                errors.append(
                    f"mcmc.pre_burnin: {pb} must be smaller than chain_length " f"{chain_length}"
                )
            else:
                pre_burnin = pb
        if "auto_optimize" in raw and "autoOptimize" in raw:
            errors.append("mcmc: give either auto_optimize or autoOptimize, not both")
        auto_optimize = True
        auto_key = "auto_optimize" if "auto_optimize" in raw else "autoOptimize"
        if auto_key in raw:
            value = raw[auto_key]
            if not isinstance(value, bool):
                errors.append(
                    f"mcmc.{auto_key}: must be an unquoted boolean (true/false), " f"got {value!r}"
                )
            else:
                auto_optimize = value
        operator_weights: Dict[str, float] = {}
        if "operator_weights" in raw:
            weights_raw = raw["operator_weights"]
            if not isinstance(weights_raw, dict):
                errors.append(
                    "mcmc.operator_weights: must be a mapping of operator target -> weight"
                )
            else:
                for target, weight in weights_raw.items():
                    if (
                        not isinstance(target, str)
                        or not target
                        or not re.match(r"^[A-Za-z_][A-Za-z0-9_.:()\-]*$", target)
                    ):
                        errors.append(f"mcmc.operator_weights: invalid operator target {target!r}")
                        continue
                    parsed = _strict_float(weight, f"mcmc.operator_weights.{target}", errors)
                    if parsed is None:
                        continue
                    if parsed <= 0:
                        errors.append(f"mcmc.operator_weights.{target}: weight must be > 0")
                        continue
                    operator_weights[target] = parsed
        return MCMCConfig(
            chain_length=chain_length,
            pre_burnin=pre_burnin,
            auto_optimize=auto_optimize,
            operator_weights=operator_weights,
        )

    def _parse_loggers(
        self,
        raw: Any,
        mcmc: MCMCConfig,
        metadata: Dict[str, Any],
        errors: List[str],
    ) -> LoggersConfig:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append("loggers: must be a mapping")
            return LoggersConfig()
        _check_known_keys(raw, {"trace_log", "tree_log", "screen_log"}, "loggers", errors)
        allowed_keys = {
            "trace_log": {"file_name", "log_every"},
            "tree_log": {"file_name", "log_every", "nexus_format", "sort_translation_table"},
            "screen_log": {"log_every"},
        }
        sections: Dict[str, Dict[str, Any]] = {}
        for section, keys in allowed_keys.items():
            entry = raw.get(section, {})
            where = f"loggers.{section}"
            if entry is None:
                entry = {}
            if not isinstance(entry, dict):
                errors.append(f"{where}: must be a mapping")
                entry = {}
            _check_known_keys(entry, keys, where, errors)
            parsed: Dict[str, Any] = {}
            if "file_name" in keys:
                file_name = entry.get("file_name")
                if file_name is None:
                    parsed["file_name"] = None  # filled with the default below
                elif not isinstance(file_name, str) or not is_safe_filename(file_name):
                    errors.append(
                        f"{where}.file_name: {file_name!r} is not a safe file name "
                        "(non-empty, letters/digits/underscore/dot/dash, no path)"
                    )
                    parsed["file_name"] = None
                else:
                    parsed["file_name"] = file_name
            if "log_every" in keys:
                if "log_every" in entry:
                    log_every = _strict_int(entry["log_every"], f"{where}.log_every", errors)
                    if log_every is not None:
                        if log_every < 1:
                            errors.append(f"{where}.log_every: must be >= 1, got {log_every}")
                        elif log_every > mcmc.chain_length:
                            errors.append(
                                f"{where}.log_every: {log_every} exceeds "
                                f"chain_length {mcmc.chain_length}"
                            )
                        else:
                            parsed["log_every"] = log_every
                else:
                    parsed["log_every"] = None
            for flag in ("nexus_format", "sort_translation_table"):
                if flag in keys:
                    if flag in entry:
                        value = _strict_bool(entry[flag], f"{where}.{flag}", errors)
                        parsed[flag] = True if value is None else value
                    else:
                        parsed[flag] = True
            sections[section] = parsed

        name = str(metadata.get("analysis_name", "analysis"))
        default_every = max(1, mcmc.chain_length // 1000)
        trace_log = {
            "file_name": sections["trace_log"].get("file_name") or f"{name}.log",
            "log_every": sections["trace_log"].get("log_every") or default_every,
        }
        tree_log = {
            "file_name": sections["tree_log"].get("file_name") or f"{name}.trees",
            "log_every": sections["tree_log"].get("log_every") or default_every,
            "nexus_format": sections["tree_log"].get("nexus_format", True),
            "sort_translation_table": sections["tree_log"].get("sort_translation_table", True),
        }
        screen_log = {
            "log_every": sections["screen_log"].get("log_every") or max(1, mcmc.chain_length // 100)
        }
        if trace_log["file_name"] == tree_log["file_name"]:
            errors.append("loggers: trace_log.file_name and tree_log.file_name must differ")
        return LoggersConfig(trace_log=trace_log, tree_log=tree_log, screen_log=screen_log)

    def _parse_output(self, raw: Any, metadata: Dict[str, Any], errors: List[str]) -> OutputConfig:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append("output: must be a mapping")
            return OutputConfig()
        _check_known_keys(
            raw,
            {"file", "embed_fingerprint", "methods_file", "fingerprint_file"},
            "output",
            errors,
        )

        def _file(key: str) -> Optional[str]:
            value = raw.get(key)
            if value is None:
                return None
            if not isinstance(value, str) or not value:
                errors.append(f"output.{key}: must be a non-empty string")
                return None
            if not is_safe_filename(value):
                errors.append(
                    f"output.{key}: {value!r} is not a safe file name "
                    "(no directories, letters/digits/underscore/dot/dash)"
                )
                return None
            return value

        embed = raw.get("embed_fingerprint", True)
        parsed_embed = _strict_bool(embed, "output.embed_fingerprint", errors)
        return OutputConfig(
            file=_file("file"),
            embed_fingerprint=True if parsed_embed is None else parsed_embed,
            methods_file=_file("methods_file"),
            fingerprint_file=_file("fingerprint_file"),
        )

    def _parse_analysis(self, raw: Any, errors: List[str]) -> Dict[str, Any]:
        if raw is None or raw == {}:
            return {}
        if not isinstance(raw, dict):
            errors.append("analysis: must be a mapping")
            return {}
        analysis_type = raw.get("type")
        if not analysis_type or not isinstance(analysis_type, str):
            errors.append("analysis.type: is required (e.g. 'discrete_trait')")
            return {}
        try:
            from .coverage import validate_analysis
        except ImportError as exc:  # pragma: no cover - packaging error
            errors.append(f"analysis: coverage subsystem unavailable: {exc}")
            return {}
        try:
            validate_analysis(analysis_type, raw, errors)
        except Exception as exc:  # noqa: BLE001 - reported as a Gate 0 error
            errors.append(f"analysis: {type(exc).__name__}: {exc}")
        return dict(raw)

    def _parse_custom_fragments(self, raw: Any, errors: List[str]) -> Dict[str, Any]:
        """Gate-0 shape check for the optional ``custom_fragments`` block.

        Only the shape is checked here (mapping, known keys, ``enabled`` is a
        real boolean, ``fragments`` is a list).  The security-relevant
        validation -- XML well-formedness, unique ids, resolvable ``idref``s,
        the element/attribute allow-list, exactly-one anchor matches and the
        refusal to override core blocks -- lives in :mod:`beastxpy.fragments`
        and runs when the fragments are actually applied.

        Fragments are **off by default**: they take effect only when this block
        sets ``enabled: true`` or the command line passes
        ``--allow-custom-fragments``, and whatever is inserted must still pass
        Gate 3 against the official BEAST jars.
        """
        if raw is None or raw == {}:
            return {}
        if not isinstance(raw, dict):
            errors.append("custom_fragments: must be a mapping")
            return {}
        _check_known_keys(raw, {"enabled", "fragments"}, "custom_fragments", errors)
        result: Dict[str, Any] = {"enabled": False, "fragments": []}
        if "enabled" in raw:
            value = _strict_bool(raw["enabled"], "custom_fragments.enabled", errors)
            result["enabled"] = bool(value)
        fragments = raw.get("fragments", [])
        if fragments is None:
            fragments = []
        if not isinstance(fragments, list):
            errors.append("custom_fragments.fragments: must be a list")
            return result
        for index, entry in enumerate(fragments):
            where = f"custom_fragments.fragments[{index}]"
            if not isinstance(entry, dict):
                errors.append(f"{where}: must be a mapping")
                continue
            _check_known_keys(
                entry,
                {"name", "file", "xml", "anchor", "anchor_id", "anchor_tag", "position"},
                where,
                errors,
            )
            if entry.get("file") is None and entry.get("xml") is None:
                errors.append(f"{where}: needs either 'file' or inline 'xml'")
            elif entry.get("file") is not None and entry.get("xml") is not None:
                errors.append(f"{where}: 'file' and 'xml' are mutually exclusive")
            position = entry.get("position", "after")
            if position not in ("before", "after", "inside"):
                errors.append(
                    f"{where}.position: unknown {position!r}; "
                    "expected 'before', 'after' or 'inside'"
                )
            if entry.get("anchor_id") is None and entry.get("anchor_tag") is None:
                errors.append(f"{where}: needs either 'anchor_id' or 'anchor_tag'")
            elif entry.get("anchor_id") is not None and entry.get("anchor_tag") is not None:
                errors.append(f"{where}: 'anchor_id' and 'anchor_tag' are mutually exclusive")
        result["fragments"] = [dict(f) for f in fragments if isinstance(f, dict)]
        return result

    def _parse_diagnostics(self, raw: Any, errors: List[str]) -> Dict[str, Any]:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append("diagnostics: must be a mapping")
            return {}
        _check_known_keys(
            raw,
            {"enabled", "sensitivity", "visualize", "overlap_threshold", "report"},
            "diagnostics",
            errors,
        )
        result: Dict[str, Any] = {
            "enabled": True,
            "sensitivity": True,
            "visualize": True,
            "overlap_threshold": 0.15,
            "report": "report.html",
        }
        for flag in ("enabled", "sensitivity", "visualize"):
            if flag in raw:
                value = _strict_bool(raw[flag], f"diagnostics.{flag}", errors)
                result[flag] = True if value is None else value
        threshold = raw.get("overlap_threshold", 0.15)
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            errors.append("diagnostics.overlap_threshold: must be a number")
        elif not (0.0 <= float(threshold) <= 1.0):
            errors.append("diagnostics.overlap_threshold: must be between 0 and 1")
        else:
            result["overlap_threshold"] = float(threshold)
        report = raw.get("report", "report.html")
        if not isinstance(report, str) or not report:
            errors.append("diagnostics.report: must be a non-empty path")
        else:
            result["report"] = report
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _collect_tip_dates(
        raw: Any,
        known_taxa: Set[str],
        beast_version: str,
        errors: List[str],
    ) -> Dict[str, Any]:
        try:
            return normalize_tip_dates(raw, known_taxa=known_taxa, beast_version=beast_version)
        except AlignmentError as exc:
            errors.append(str(exc))
            return {}


def _strict_int(value: Any, name: str, errors: List[str]) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{name}: expected an integer, got {value!r}")
        return None
    return value


def subst_raw_cats(raw_value: Any, where: str, errors: List[str]) -> int:
    """Parse gamma category count.

    ``0`` is a documented alias for "no gamma" and is normalized to ``1``
    (BEAST emits no discrete-gamma site model for one category); negative
    values, booleans and non-integers are rejected.
    """
    if raw_value is None:
        return 1
    if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value < 0:
        errors.append(f"{where}.gamma_categories: must be an integer >= 0")
        return 1
    return 1 if raw_value == 0 else raw_value
