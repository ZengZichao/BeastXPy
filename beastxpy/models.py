"""Data model definitions for BeastXPy.

This module defines all dataclasses used throughout the tool, representing
alignments, partitions, calibrations, models and the top-level configuration
object for BEAST 1 (BEAST 1.10.4 / BEAST X 10.5.0) analyses.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

# Metadata keys that document an analysis without describing it. They are kept
# out of the fingerprint so that crediting a co-author or updating a prose
# summary cannot make one analysis look like two.
NON_SCIENCE_METADATA = frozenset(
    {"author", "date", "output_file", "analysis_description", "description", "notes"}
)

# BEAST 1 date/direction enumerations.  ``weeks`` is recognised as a token by
# BEAST X 10.5.0's XMLUnits but TimeScale.unitString() throws on it while
# TaxaParser prints dates, so it is rejected for every version with a specific
# Gate-0 message.
DATE_UNITS = frozenset({"years", "months", "days", "weeks"})
DATE_UNITS_1_10_4 = frozenset({"years", "months", "days"})
DATE_DIRECTIONS = frozenset({"forwards", "backwards"})
DEFAULT_DATE_UNITS = "years"
DEFAULT_DATE_DIRECTION = "forwards"

DATA_TYPE_ALIASES = {
    "nucleotide": "nucleotide",
    "nt": "nucleotide",
    "dna": "nucleotide",
    "amino-acid": "amino-acid",
    "aminoacid": "amino-acid",
    "amino acid": "amino-acid",
    "protein": "amino-acid",
    "aa": "amino-acid",
}


# XML ids emitted by BeastXPy for the global/structural elements.  A
# calibration name (or another user identifier) that equals one of these would
# collide with a structural id in the generated document.
RESERVED_BEAST_IDS = frozenset(
    {
        "taxa",
        "treeModel",
        "prior",
        "posterior",
        "likelihood",
        "mcmc",
        "operator",
        "operators",
        "state",
        "run",
        "patterns",
        "alignment",
        "sequence",
        "taxon",
        "statistic",
        "parameter",
        "branchRates",
        "tree",
    }
)

_WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_UNSAFE_FILENAME_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


def is_safe_identifier(name: str) -> bool:
    """Whether ``name`` is usable as an XML id / artifact identifier.

    Accepts letters, digits, underscore, dot and dash, must start with a
    letter or underscore, must not be a reserved BEAST structural id and must
    not be a Windows device name.
    """
    if not isinstance(name, str) or not name:
        return False
    if not _SAFE_IDENTIFIER_RE.match(name):
        return False
    if name in RESERVED_BEAST_IDS:
        return False
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        return False
    return True


def is_safe_filename(name: str) -> bool:
    """Whether ``name`` is a safe, relative file name (no path components)."""
    if not isinstance(name, str) or not name or len(name) > 128:
        return False
    if name in (".", "..") or "/" in name or "\\" in name:
        return False
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", name):
        return False
    stem = name.split(".")[0]
    if stem.upper() in _WINDOWS_RESERVED:
        return False
    return True


def safe_identifier(name: Any, max_length: int = 60, fallback: str = "item") -> str:
    """Return a filesystem- and XML-safe identifier derived from ``name``.

    Used by the diagnostics layer for artifact names such as
    density PNGs or sensitivity XML files.  Unsafe characters become ``_``, a
    leading non-letter is prefixed with ``_``, over-long names are truncated
    and a six-hex digest of the *original* name is appended so that distinct
    names cannot collide after sanitisation.
    """
    text = "" if name is None else str(name)
    digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:6]
    base = _UNSAFE_FILENAME_CHARS_RE.sub("_", text).strip("._-")
    if not base:
        base = fallback
    if not re.match(r"^[A-Za-z_]", base):
        base = "_" + base
    base = base[: max(1, max_length)]
    if base.split(".")[0].upper() in _WINDOWS_RESERVED:
        base = "_" + base
    return f"{base}_{digest}"


class BeastVersion(str, Enum):
    """Target BEAST 1 distribution version."""

    V1_10_4 = "1.10.4"
    V10_5_0 = "10.5.0"
    BOTH = "both"


class DataType(Enum):
    """Sequence data type.

    The enum value is the *internal* spelling used throughout BeastXPy.  It is
    deliberately not the BEAST XML literal for amino acids (``amino acid`` with
    a space); use :attr:`beast_literal` at the XML boundary.
    """

    NUCLEOTIDE = "nucleotide"
    AMINOACID = "amino-acid"

    @classmethod
    def from_config(cls, value: Any) -> "DataType":
        """Normalize a user spelling to the single internal DataType.

        Accepts ``nucleotide``/``nt``/``dna`` and
        ``amino-acid``/``aminoacid``/``amino acid``/``protein``/``aa``
        (case-insensitive).  Anything else - notably ``codon``, which BEAST 1
        XML cannot express here - raises ``ValueError``.
        """
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError(f"data_type must be a string, got {value!r}")
        key = " ".join(value.strip().lower().split())
        canonical = DATA_TYPE_ALIASES.get(key)
        if canonical is None:
            raise ValueError(
                f"unknown data_type {value!r}; use 'nucleotide' or 'amino-acid' "
                "(accepted spellings: nucleotide, nt, dna, amino-acid, "
                "aminoacid, 'amino acid', protein, aa)"
            )
        return cls(canonical)

    @property
    def beast_literal(self) -> str:
        """The literal BEAST 1 XML ``dataType`` attribute value."""
        return "amino acid" if self is DataType.AMINOACID else "nucleotide"


class TreePriorType(Enum):
    """Tree prior model types."""

    YULE = "yule"
    BIRTH_DEATH = "birth_death"
    COALESCENT_CONSTANT = "coalescent_constant"
    COALESCENT_EXPONENTIAL = "coalescent_exponential"
    COALESCENT_LOGISTIC = "coalescent_logistic"
    COALESCENT_EXPANSION = "coalescent_expansion"
    SKYLINE = "skyline"
    EBSP = "ebsp"


class ClockModelType(Enum):
    """Clock model types."""

    STRICT = "strict"
    UCLN = "ucln"
    UCE = "uce"
    RLC = "rlc"


class MCMCType(Enum):
    """MCMC run types (BEAST 1 only supports standard MCMC)."""

    STANDARD = "standard"


class InitTreeType(Enum):
    """Initial tree generation strategy."""

    COALESCENT = "coalescent"
    UPGMA = "upgma"
    NEWICK = "newick"


class SubstitutionModelType(str, Enum):
    """Supported substitution model keys."""

    JC69 = "jc69"
    HKY = "hky"
    TN93 = "tn93"
    GTR = "gtr"
    WAG = "wag"
    JTT = "jtt"
    DAYHOFF = "dayhoff"
    BLOSUM62 = "blosum62"
    CPREV = "cprev"
    MTREV = "mtrev"


@dataclass
class Sequence:
    """A single sequence entry."""

    taxon: str
    sequence: str


@dataclass
class Taxon:
    """A taxon, optionally carrying a tip date."""

    id: str
    date: Optional[float] = None
    date_direction: str = "forwards"  # "forwards" | "backwards"
    date_units: str = "years"

    def __post_init__(self) -> None:
        # A Taxon never carries an empty/unknown time unit.
        if self.date_units not in DATE_UNITS:
            raise ValueError(
                f"taxon {self.id!r}: unknown date units {self.date_units!r} "
                f"(valid: {', '.join(sorted(DATE_UNITS))})"
            )
        if self.date_direction not in DATE_DIRECTIONS:
            raise ValueError(
                f"taxon {self.id!r}: unknown date direction {self.date_direction!r} "
                f"(valid: {', '.join(sorted(DATE_DIRECTIONS))})"
            )


@dataclass
class Alignment:
    """A sequence alignment.

    The alignment always stores the **complete** sequences as read from the
    source file.  A partition selects a 1-based inclusive ``[from_pos,
    to_pos]`` window by *attaching* that range to a copy of the alignment;
    the sequences themselves are never replaced by a slice.  This
    lets the XML layer emit one full ``<alignment>`` plus one
    ``<patterns from=".." to="..">`` per partition.

    Attributes:
        id: Alignment identifier.
        data_type: Nucleotide or amino-acid (internal spelling).
        sequences: List of Sequence objects (always complete).
        source_file: Path the alignment was read from, if any.
        from_pos / to_pos: 1-based inclusive window used by a partition
            (BEAST 1 ``<patterns from="..." to="...">``).  ``None`` means the
            whole alignment.
        data_type_source: ``auto`` or ``declared``.
        data_type_ambiguous: True when an auto-detected type could also be
            the other alphabet (e.g. ACGT-only data) so Gate 0 can warn.
    """

    id: str
    data_type: DataType
    sequences: List[Sequence]
    source_file: Optional[str] = None
    from_pos: Optional[int] = None
    to_pos: Optional[int] = None
    data_type_source: str = "declared"
    data_type_ambiguous: bool = False

    @property
    def taxa_names(self) -> List[str]:
        """Return list of taxon names."""
        return [seq.taxon for seq in self.sequences]

    @property
    def n_taxa(self) -> int:
        """Return number of taxa."""
        return len(self.sequences)

    @property
    def site_lengths(self) -> List[int]:
        """Return the length of every sequence, in file order."""
        return [len(seq.sequence) for seq in self.sequences]

    @property
    def n_sites(self) -> int:
        """Return the full number of sites in the alignment (never sliced)."""
        lengths = set(self.site_lengths)
        if not lengths:
            return 0
        if len(lengths) > 1:
            raise ValueError(
                f"Alignment '{self.id}' is ragged: sequence lengths are {sorted(lengths)}."
            )
        return lengths.pop()

    @property
    def window_length(self) -> int:
        """Number of sites selected by ``from_pos``/``to_pos``."""
        n_sites = self.n_sites
        start = self.from_pos if self.from_pos is not None else 1
        end = self.to_pos if self.to_pos is not None else n_sites
        return max(0, end - start + 1)

    @property
    def beast_data_type(self) -> str:
        """The BEAST XML literal for this alignment's data type."""
        return self.data_type.beast_literal

    def validate_range(self, from_pos: Optional[int], to_pos: Optional[int]) -> Tuple[int, int]:
        """Validate a 1-based inclusive partition window.

        Returns the effective ``(from_pos, to_pos)``.  Raises ``ValueError``
        for non-integers, ``from_pos < 1``, ``to_pos < from_pos`` and
        ``to_pos > n_sites``.  Python negative indexing is
        never used.
        """
        n_sites = self.n_sites
        if from_pos is None and to_pos is None:
            return 1, n_sites
        if from_pos is None or to_pos is None:
            raise ValueError(
                f"alignment {self.id!r}: 'from' and 'to' must be given together "
                f"(got from={from_pos!r}, to={to_pos!r})"
            )
        if (
            isinstance(from_pos, bool)
            or isinstance(to_pos, bool)
            or not isinstance(from_pos, int)
            or not isinstance(to_pos, int)
        ):
            raise ValueError(
                f"alignment {self.id!r}: from/to must be integers, got "
                f"from={from_pos!r}, to={to_pos!r}"
            )
        if from_pos < 1:
            raise ValueError(
                f"alignment {self.id!r}: 'from' must be >= 1 (1-based), got {from_pos}"
            )
        if to_pos < from_pos:
            raise ValueError(
                f"alignment {self.id!r}: 'to' ({to_pos}) must be >= 'from' ({from_pos})"
            )
        if to_pos > n_sites:
            raise ValueError(
                f"alignment {self.id!r}: 'to' ({to_pos}) exceeds the alignment "
                f"length ({n_sites} sites)"
            )
        return from_pos, to_pos

    def with_range(self, from_pos: Optional[int], to_pos: Optional[int]) -> "Alignment":
        """Return a copy of this alignment carrying a validated partition window.

        The returned alignment shares the complete ``sequences`` list; only the
        ``from_pos``/``to_pos`` metadata differs.
        """
        start, end = self.validate_range(from_pos, to_pos)
        return Alignment(
            id=self.id,
            data_type=self.data_type,
            sequences=self.sequences,
            source_file=self.source_file,
            from_pos=start,
            to_pos=end,
            data_type_source=self.data_type_source,
            data_type_ambiguous=self.data_type_ambiguous,
        )

    def slice(self, from_pos: int, to_pos: Optional[int] = None) -> "Alignment":
        """Return a *real* sub-alignment over the 1-based [from_pos, to_pos].

        Coordinates are validated strictly; out-of-range or reversed windows
        raise ``ValueError`` instead of silently using Python slicing rules.
        Config parsing no longer calls this (it uses :meth:`with_range`), but
        the method remains for callers that need materialised sub-sequences.
        """
        start, end = self.validate_range(from_pos, self.n_sites if to_pos is None else to_pos)
        sequences = [
            Sequence(taxon=s.taxon, sequence=s.sequence[start - 1 : end]) for s in self.sequences
        ]
        return Alignment(
            id=self.id,
            data_type=self.data_type,
            sequences=sequences,
            source_file=self.source_file,
            from_pos=start,
            to_pos=end,
            data_type_source=self.data_type_source,
            data_type_ambiguous=self.data_type_ambiguous,
        )

    def content_digest(self) -> str:
        """Return a SHA-256 digest prefix of the alignment *content* (first 16 hex)."""
        import hashlib

        hasher = hashlib.sha256()
        for seq in sorted(self.sequences, key=lambda s: s.taxon):
            hasher.update(seq.taxon.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(seq.sequence.encode("utf-8"))
            hasher.update(b"\0")
        return hasher.hexdigest()[:16]


@dataclass
class RealParameter:
    """A real-valued parameter with optional bounds (BEAST 1 ``<parameter>``)."""

    value: Union[float, str] = 1.0
    lower: Optional[float] = None
    upper: Optional[float] = None
    dimension: int = 1
    estimate: bool = True
    id: Optional[str] = None


@dataclass
class DistributionConfig:
    """Configuration for a calibration / parameter prior distribution.

    Attributes:
        type: Distribution type name (normal, lognormal, uniform, ...).
        parameters: Distribution-specific parameters (mean/stdev, M/S, ...).
        offset: Offset for the distribution (default 0.0).
    """

    type: str
    parameters: Dict[str, Union[float, bool, str]] = field(default_factory=dict)
    offset: float = 0.0


@dataclass
class HyperpriorConfig:
    """Hyperprior configuration for a distribution parameter."""

    type: str
    parameters: Dict[str, Union[float, bool, str]] = field(default_factory=dict)
    offset: float = 0.0


#: Calibration-distribution parameters for which BeastXPy can emit a *real*
#: BEAST 1 hyperprior.  The distribution is then written as a
#: ``<distributionLikelihood>`` whose ``ParametricDistributionModel`` takes the
#: parameter as a ``<parameter idref="..."/>`` input; the parameter itself is a
#: top-level estimated parameter with its own prior and operator.  BEAST's
#: ``exponentialDistributionModel``, ``betaDistributionModel`` and
#: ``inverseGammaDistributionModel`` parsers only accept literal doubles, so
#: those combinations are rejected at Gate 0 instead of being silently dropped.
HYPERPRIOR_EMITTABLE_TARGETS: Dict[str, Tuple[str, ...]] = {
    "lognormal": ("mean", "stdev"),
    "normal": ("mean", "stdev"),
    "gamma": ("shape", "scale"),
    "poisson": ("mean",),
}


@dataclass
class Provenance:
    """Calibration provenance metadata (core innovation)."""

    source: str = ""  # fossil | literature | other
    reference: str = ""  # DOI / literature
    calibration_type: str = "soft"  # soft | hard
    original_age_min: Optional[float] = None
    original_age_max: Optional[float] = None
    notes: str = ""


@dataclass
class CalibrationPoint:
    """A node calibration point on the shared tree.

    Attributes:
        name: Calibration point name (used as ID prefix).
        taxa: List of taxon names; ``None`` explicitly marks a *root*
            calibration (``taxa: null``).
        monophyletic: Enforce monophyly with ``monophylyStatistic`` and a
            ``constrainedTaxa`` starting-tree constraint.
        distribution: Calibration distribution configuration.  Gate 0
            guarantees this is set for every parsed calibration.
        calibration_method: normalized ``clade`` (== crown) or ``stem``.
        provenance: Provenance metadata.
        hyperpriors: Hyperprior configurations keyed by the *canonical*
            parameter name of :attr:`distribution`.
        is_root: True for a root calibration (taxa is ``None``).
        calibration_context: Sibling calibration points of the same config
            (set by :class:`beastxpy.config.ConfigParser` and the model
            assembler) so :func:`beastxpy.calibration.children_of` can build
            the containment tree from a single point.  Excluded from repr,
            equality and the fingerprint.
    """

    name: str
    taxa: Optional[List[str]]
    monophyletic: bool = True
    distribution: Optional[DistributionConfig] = None
    calibration_method: str = "clade"
    provenance: Optional[Provenance] = None
    hyperpriors: Dict[str, HyperpriorConfig] = field(default_factory=dict)
    is_root: bool = False
    calibration_context: Tuple["CalibrationPoint", ...] = field(
        default_factory=tuple, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.taxa is None:
            self.is_root = True

    @property
    def node_type(self) -> str:
        """Explicit stem/crown/root identity for the diagnostics layer.

        The ordering is ``crown(X) < stem(X) < crown(parent(X)) <
        stem(parent(X))``; a calibration's node identity is
        ``(frozenset(taxa), node_type)``.
        """
        if self.is_root:
            return "root"
        return "stem" if self.calibration_method == "stem" else "crown"

    @property
    def taxa_set(self) -> frozenset:
        """Taxon set of this calibration (empty for a root calibration)."""
        return frozenset(self.taxa or [])

    @property
    def node_key(self) -> Tuple[frozenset, str]:
        """Stable identity of the calibrated node ``(taxa_set, node_type)``."""
        return (self.taxa_set, self.node_type)

    @property
    def constrains_root(self) -> bool:
        """Whether this calibration constrains the global root age."""
        return self.is_root


@dataclass
class SiteModelConfig:
    """Site model configuration.

    Attributes:
        substitution_model: Dict with 'type' and model-specific parameters.
        gamma_categories: Number of gamma rate categories (1 = no gamma).
        gamma_shape: Gamma shape parameter (``alpha`` in BEAST 1).
        proportion_invariant: Proportion of invariant sites ('' -> disabled).
    """

    substitution_model: Dict[str, Any]
    gamma_categories: int = 1
    gamma_shape: Optional[RealParameter] = None
    proportion_invariant: Optional[RealParameter] = None


@dataclass
class ClockModelConfig:
    """Clock model configuration.

    Attributes:
        type: Clock model type (strict / ucln / uce / rlc).
        clock_rate: Clock rate parameter (strict).
        ucld_mean: UCLD/UCE mean parameter.
        ucld_stdev: UCLD stdev parameter.
        exponential_mean: Exponential distribution mean (UCE).
        rlc_indicators: Random-local-clock indicator category count.
        separate: Independent clock per partition (``clock: separate``).
    """

    type: ClockModelType = ClockModelType.STRICT
    clock_rate: Optional[RealParameter] = None
    ucld_mean: Optional[RealParameter] = None
    ucld_stdev: Optional[RealParameter] = None
    exponential_mean: Optional[RealParameter] = None
    rlc_indicators: int = 2
    separate: bool = False


@dataclass
class Partition:
    """A partition combining alignment, site model and clock model.

    Attributes:
        id: Partition identifier (used as an id namespace prefix).
        alignment: Alignment object.
        site_model: Site model configuration.
        clock_model: Clock model configuration.
        tree: Tree sharing setting; only ``"shared"`` is supported.
        clock: Clock sharing setting; ``"shared"`` (default) or ``"separate"``.
    """

    id: str
    alignment: Alignment
    site_model: SiteModelConfig
    clock_model: ClockModelConfig
    tree: str = "shared"
    clock: str = "shared"


@dataclass
class MCMCConfig:
    """MCMC run configuration (BEAST 1 standard MCMC).

    Attributes:
        chain_length: ``<mcmc chainLength="...">``.
        pre_burnin: ``<mcmc preBurnin="...">``.
        auto_optimize: ``<mcmc autoOptimize="...">``; must be a real bool.
        operator_weights: Optional operator weight overrides keyed by the
            operator target id (e.g. ``clock.rate``); the model layer applies
            them as ``<operator weight="...">``.
    """

    chain_length: int = 10000000
    pre_burnin: int = 0
    auto_optimize: bool = True
    operator_weights: Dict[str, float] = field(default_factory=dict)


@dataclass
class LoggersConfig:
    """Logger configuration (screen / file log / tree log)."""

    trace_log: Dict[str, Any] = field(default_factory=lambda: {"file_name": "", "log_every": None})
    tree_log: Dict[str, Any] = field(
        default_factory=lambda: {
            "file_name": "",
            "log_every": None,
            "nexus_format": True,
            "sort_translation_table": True,
        }
    )
    screen_log: Dict[str, Any] = field(default_factory=lambda: {"log_every": None})


@dataclass
class ControlConfig:
    """Generation control knobs.

    Attributes:
        beast_version: Target BEAST distribution: 1.10.4 | 10.5.0 | both.
        seed: Default RNG seed for run orchestration (never written to XML).
        strict: Strict mode (False downgrades some Gate-0 rules to warnings).
        overlap_measure: Calibration overlap measure: relative | absolute.
        initial_tree: coalescent (default) | upgma | newick.
        newick: User-supplied Newick starting tree string.
    """

    beast_version: str = "1.10.4"
    seed: Optional[int] = None
    strict: bool = True
    overlap_measure: str = "relative"
    initial_tree: str = "coalescent"
    newick: Optional[str] = None


@dataclass
class TreePriorConfig:
    """Tree prior configuration.

    Attributes:
        type: Tree prior type.
        params: Type-specific parameter dict (birth_rate, birth_minus_death,
            relative_death, population_size, growth_rate, skyline_groups, ...).
        conditional_on_root: birth-death conditioning.
    """

    type: TreePriorType = TreePriorType.YULE
    params: Dict[str, Any] = field(default_factory=dict)
    conditional_on_root: bool = False


@dataclass
class OutputConfig:
    """Output-sidecar configuration."""

    file: Optional[str] = None
    embed_fingerprint: bool = True
    methods_file: Optional[str] = None
    fingerprint_file: Optional[str] = None


@dataclass
class BEASTConfig:
    """Complete BEAST 1 analysis configuration."""

    metadata: Dict[str, Any]
    partitions: List[Partition]
    all_alignments: List[Alignment] = field(default_factory=list)
    tree_prior: TreePriorConfig = field(default_factory=TreePriorConfig)
    calibrations: List[CalibrationPoint] = field(default_factory=list)
    parameter_priors: List[Dict[str, Any]] = field(default_factory=list)
    mcmc: MCMCConfig = field(default_factory=MCMCConfig)
    loggers: LoggersConfig = field(default_factory=LoggersConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    tip_dates: Dict[str, Any] = field(default_factory=dict)
    # Gate-0 warnings that must not be silently dropped (e.g. ambiguous
    # auto-detected data type, soft provenance mismatches).
    warnings: List[str] = field(default_factory=list)
    #: Optional coverage extension selected by the top-level ``analysis``
    #: section (discrete traits, ancestral states, MLE, ...).  The core
    #: generator builds a standard XML first; ``beastxpy.coverage`` then
    #: extends it in place.
    analysis: Dict[str, Any] = field(default_factory=dict)
    #: Optional user-supplied XML fragments (top-level ``custom_fragments``).
    #: Disabled unless explicitly enabled in the config *and* allowed on the
    #: command line.  Parsed, validated and applied by
    #: :mod:`beastxpy.fragments`; anything inserted this way must still pass
    #: Gate 3 against the official BEAST jars, and is marked ``custom`` in the
    #: provenance record.
    custom_fragments: Dict[str, Any] = field(default_factory=dict)

    @property
    def analysis_name(self) -> str:
        """Return the analysis name (default 'analysis')."""
        return str(self.metadata.get("analysis_name", "analysis"))

    @property
    def root_calibration(self) -> Optional[CalibrationPoint]:
        """The single root calibration, if any."""
        roots = [c for c in self.calibrations if c.is_root]
        return roots[0] if roots else None

    @property
    def calibration_nodes(self) -> List[Dict[str, Any]]:
        """Normalized calibration node identities for Gate 1/2 consumers.

        Each entry exposes the ``(taxa_set, node_type)`` identity, the
        normalized ``calibration_method`` and root marker so the diagnostics
        layer never has to re-derive stem/crown semantics from raw YAML.
        ``parent`` is the smallest strict superset clade (structural
        containment only; age ordering remains Gate 2).
        """
        entries: List[Dict[str, Any]] = []
        for cal in self.calibrations:
            entries.append(
                {
                    "name": cal.name,
                    "taxa": list(cal.taxa) if cal.taxa is not None else None,
                    "taxa_set": sorted(cal.taxa_set),
                    "is_root": cal.is_root,
                    "node_type": cal.node_type,
                    "calibration_method": cal.calibration_method,
                    "monophyletic": cal.monophyletic,
                    "distribution": cal.distribution.type if cal.distribution else None,
                    "hyperpriors": sorted(cal.hyperpriors),
                }
            )
        clades = [cal for cal in self.calibrations if not cal.is_root]
        for entry, cal in zip(entries, self.calibrations):
            parent_name: Optional[str] = None
            parent_size: Optional[int] = None
            if not cal.is_root:
                for candidate in clades:
                    if candidate is cal or not (cal.taxa_set < candidate.taxa_set):
                        continue
                    size = len(candidate.taxa_set)
                    if parent_size is None or size < parent_size:
                        parent_name = candidate.name
                        parent_size = size
            entry["parent"] = parent_name
        return entries

    def normalized(self) -> Dict[str, Any]:
        """Return the normalized Gate-0 values the validator can consume.

        Values here are the same objects the model layer will emit; the
        validator must not try to re-parse XML strings or YAML to recover
        data types, partition ranges or tip-date units.
        """
        return {
            "data_types": {a.id: a.beast_data_type for a in self.all_alignments},
            "partitions": [
                {
                    "id": p.id,
                    "alignment_id": p.alignment.id,
                    "from": p.alignment.from_pos,
                    "to": p.alignment.to_pos,
                    "alignment_sites": p.alignment.n_sites,
                    "selected_sites": p.alignment.window_length,
                    "data_type": p.alignment.beast_data_type,
                    "tree": p.tree,
                    "clock": p.clock,
                }
                for p in self.partitions
            ],
            "tip_dates": {
                taxon: {
                    "value": entry["value"],
                    "direction": entry["direction"],
                    "units": entry["units"],
                }
                for taxon, entry in sorted(self.tip_dates.items())
            },
            "calibrations": self.calibration_nodes,
            "mcmc": {
                "chain_length": self.mcmc.chain_length,
                "pre_burnin": self.mcmc.pre_burnin,
                "auto_optimize": self.mcmc.auto_optimize,
                "operator_weights": dict(self.mcmc.operator_weights),
            },
            "loggers": {
                "trace_log": dict(self.loggers.trace_log),
                "tree_log": dict(self.loggers.tree_log),
                "screen_log": dict(self.loggers.screen_log),
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the *scientific* content of the config for fingerprinting."""

        import json

        def _default(obj: Any) -> Any:
            if isinstance(obj, Enum):
                return obj.value
            if hasattr(obj, "__dict__"):
                return {k: v for k, v in obj.__dict__.items()}
            return str(obj)

        metadata = {k: v for k, v in self.metadata.items() if k not in NON_SCIENCE_METADATA}

        def _param_repr(param: Any) -> Any:
            if param is None:
                return None
            if hasattr(param, "__dict__"):
                return dict(param.__dict__)
            return param

        return json.loads(
            json.dumps(
                {
                    "metadata": metadata,
                    "partitions": [
                        {
                            "id": p.id,
                            "alignment_id": p.alignment.id,
                            "data_type": p.alignment.data_type.value,
                            "n_taxa": p.alignment.n_taxa,
                            "n_sites": p.alignment.n_sites,
                            "alignment_digest": p.alignment.content_digest(),
                            "from": p.alignment.from_pos,
                            "to": p.alignment.to_pos,
                            "tree": p.tree,
                            "clock": p.clock,
                            "site_model": {
                                "substitution_model": p.site_model.substitution_model,
                                "gamma_categories": p.site_model.gamma_categories,
                                "gamma_shape": _param_repr(p.site_model.gamma_shape),
                                "proportion_invariant": _param_repr(
                                    p.site_model.proportion_invariant
                                ),
                            },
                            "clock_model": {
                                "type": p.clock_model.type.value,
                                "clock_rate": _param_repr(p.clock_model.clock_rate),
                                "ucld_mean": _param_repr(p.clock_model.ucld_mean),
                                "ucld_stdev": _param_repr(p.clock_model.ucld_stdev),
                                "exponential_mean": _param_repr(p.clock_model.exponential_mean),
                            },
                        }
                        for p in self.partitions
                    ],
                    "tree_prior": {
                        "type": self.tree_prior.type.value,
                        "params": self.tree_prior.params,
                        "conditional_on_root": self.tree_prior.conditional_on_root,
                    },
                    "calibrations": [
                        {
                            "name": c.name,
                            "taxa": c.taxa,
                            "is_root": c.is_root,
                            "node_type": c.node_type,
                            "monophyletic": c.monophyletic,
                            "calibration_method": c.calibration_method,
                            "distribution": c.distribution.__dict__ if c.distribution else None,
                            "hyperpriors": {
                                key: hp.__dict__ for key, hp in sorted(c.hyperpriors.items())
                            },
                            "provenance": c.provenance.__dict__ if c.provenance else None,
                        }
                        for c in self.calibrations
                    ],
                    "parameter_priors": self.parameter_priors,
                    "mcmc": self.mcmc.__dict__,
                    "loggers": {
                        "trace_log": self.loggers.trace_log,
                        "tree_log": self.loggers.tree_log,
                        "screen_log": self.loggers.screen_log,
                    },
                    "control": {
                        "beast_version": self.control.beast_version,
                        "overlap_measure": self.control.overlap_measure,
                        "initial_tree": self.control.initial_tree,
                        "newick": self.control.newick,
                    },
                    "diagnostics": self.diagnostics,
                },
                default=_default,
                sort_keys=True,
            )
        )
