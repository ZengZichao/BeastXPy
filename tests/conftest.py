"""Shared pytest fixtures for BeastXPy tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the package importable from the project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "examples"

from beastxpy.config import ConfigParser  # noqa: E402
from beastxpy.models import (  # noqa: E402
    Alignment,
    BEASTConfig,
    ClockModelConfig,
    ClockModelType,
    ControlConfig,
    DataType,
    LoggersConfig,
    MCMCConfig,
    Partition,
    RealParameter,
    Sequence,
    SiteModelConfig,
    TreePriorConfig,
    TreePriorType,
)


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture(scope="session")
def config_parser() -> ConfigParser:
    return ConfigParser()


@pytest.fixture
def config_basic(config_parser, examples_dir):
    """Parse the basic example config."""
    return config_parser.parse(str(examples_dir / "config_basic.yaml"))


@pytest.fixture
def config_jc69(config_parser, examples_dir):
    """Parse the JC69 substitution model example."""
    return config_parser.parse(str(examples_dir / "config_subst_jc69.yaml"))


@pytest.fixture
def config_calibrated(config_parser, examples_dir):
    """Parse the calibrated Yule example."""
    return config_parser.parse(str(examples_dir / "config_calibrated_yule.yaml"))


@pytest.fixture
def config_tip_dates(config_parser, examples_dir):
    """Parse the tip dates example."""
    return config_parser.parse(str(examples_dir / "config_tip_dates.yaml"))


@pytest.fixture
def config_advanced(config_parser, examples_dir):
    """Parse the advanced multi-calibration example."""
    return config_parser.parse(str(examples_dir / "config_advanced.yaml"))


@pytest.fixture
def config_rlc(config_parser, examples_dir):
    """Parse the random local clock example."""
    return config_parser.parse(str(examples_dir / "config_rlc.yaml"))


@pytest.fixture
def config_ebsp(config_parser, examples_dir):
    """Parse the EBSP example."""
    return config_parser.parse(str(examples_dir / "config_ebsp.yaml"))


@pytest.fixture
def simple_alignment() -> Alignment:
    """A minimal 4-taxon nucleotide alignment."""
    return Alignment(
        id="align1",
        data_type=DataType.NUCLEOTIDE,
        sequences=[
            Sequence(taxon="A", sequence="ACGTACGT"),
            Sequence(taxon="B", sequence="ACGTACGA"),
            Sequence(taxon="C", sequence="ACGTACGC"),
            Sequence(taxon="D", sequence="ACGTACGG"),
        ],
    )


@pytest.fixture
def simple_partition(simple_alignment) -> Partition:
    """A single simple partition."""
    return Partition(
        id="gene1",
        alignment=simple_alignment,
        site_model=SiteModelConfig(
            substitution_model={"type": "hky", "kappa": RealParameter(value=2.0, estimate=True)},
            gamma_categories=4,
            gamma_shape=RealParameter(value=0.5, estimate=True),
        ),
        clock_model=ClockModelConfig(
            type=ClockModelType.STRICT,
            clock_rate=RealParameter(value=0.01, estimate=True),
        ),
    )


@pytest.fixture
def minimal_config(simple_partition) -> BEASTConfig:
    """A minimal BEASTConfig for quick tests."""
    return BEASTConfig(
        metadata={"analysis_name": "test"},
        partitions=[simple_partition],
        all_alignments=[simple_partition.alignment],
        tree_prior=TreePriorConfig(
            type=TreePriorType.YULE,
            params={"birth_rate": RealParameter(value=1.0, estimate=True)},
        ),
        mcmc=MCMCConfig(chain_length=100000),
        loggers=LoggersConfig(
            trace_log={"file_name": "test.log", "log_every": 1000},
            tree_log={"file_name": "test.trees", "log_every": 1000},
        ),
        control=ControlConfig(beast_version="1.10.4"),
    )


@pytest.fixture(scope="session")
def primates_fasta(examples_dir) -> Path:
    return examples_dir / "primates.fasta"


@pytest.fixture(scope="session")
def aminoacid_fasta(examples_dir) -> Path:
    return examples_dir / "aminoacid.fasta"
