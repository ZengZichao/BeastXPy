"""Gate-0 configuration validation tests.

Every positive test asserts the normalized value that the generator consumes;
every negative test asserts Gate 0 rejects the configuration with an
actionable message instead of silently ignoring the field.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from beastxpy.config import CONFIG_FIELD_AUDIT, ConfigError, ConfigParser
from beastxpy.models import (
    Alignment,
    DataType,
    Sequence,
    Taxon,
    is_safe_filename,
    is_safe_identifier,
    safe_identifier,
)
from beastxpy.partition import PartitionError, collect_partitions
from beastxpy.registry import ModelRegistry
from beastxpy.sequence import (
    AlignmentError,
    SequenceReader,
    normalize_tip_dates,
    validate_alignment,
)

NUC_FASTA = ">A\nACGTACGT\n>B\nACGTACGA\n>C\nACGTACGC\n>D\nACGTACGG\n"
PROT_FASTA = ">A\nACDEFGHIK\n>B\nACDEFGHIK\n>C\nACDEFGHIK\n"


def base_config():
    return {
        "metadata": {"analysis_name": "t"},
        "alignments": [{"id": "a1", "file": "seqs.fasta", "data_type": "nucleotide"}],
        "partitions": [
            {
                "id": "p1",
                "alignment_id": "a1",
                "site_model": {"substitution_model": {"type": "hky"}},
                "clock_model": {
                    "type": "strict",
                    "clock_rate": {"value": 0.01, "estimate": True},
                },
            }
        ],
        "tree_prior": {"type": "yule"},
        "mcmc": {"chain_length": 1000},
        "loggers": {
            "trace_log": {"log_every": 10},
            "tree_log": {"log_every": 10},
        },
    }


def write_config(tmp_path: Path, data: dict) -> Path:
    fasta = tmp_path / "seqs.fasta"
    if not fasta.exists():
        fasta.write_text(NUC_FASTA, encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def write_raw_config(tmp_path: Path, text: str, fasta: str = NUC_FASTA) -> Path:
    (tmp_path / "seqs.fasta").write_text(fasta, encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def parse_ok(tmp_path: Path, data: dict):
    return ConfigParser().parse(str(write_config(tmp_path, data)))


def parse_fail(tmp_path: Path, data: dict, needle: str = ""):
    with pytest.raises(ConfigError) as excinfo:
        ConfigParser().parse(str(write_config(tmp_path, data)))
    message = str(excinfo.value)
    assert needle.lower() in message.lower(), message
    return message


def add_partition(data: dict, **partition) -> dict:
    data = copy.deepcopy(data)
    data["partitions"].append(partition)
    return data


# ---------------------------------------------------------------------------
# partitions
# ---------------------------------------------------------------------------


class TestPartitionWindows:
    def test_from_zero_rejected(self, tmp_path):
        data = base_config()
        data["partitions"][0].update({"from": 0, "to": 4})
        parse_fail(tmp_path, data, "from")

    def test_to_beyond_alignment_rejected(self, tmp_path):
        data = base_config()
        data["partitions"][0].update({"from": 1, "to": 999})
        parse_fail(tmp_path, data, "exceeds")

    def test_from_after_to_rejected(self, tmp_path):
        data = base_config()
        data["partitions"][0].update({"from": 5, "to": 3})
        parse_fail(tmp_path, data, "to")

    def test_to_must_be_strict_int(self, tmp_path):
        data = base_config()
        data["partitions"][0].update({"from": 1, "to": "4"})
        parse_fail(tmp_path, data, "integer")

    def test_from_requires_to(self, tmp_path):
        data = base_config()
        data["partitions"][0]["from"] = 1
        parse_fail(tmp_path, data, "together")

    def test_to_requires_from(self, tmp_path):
        data = base_config()
        data["partitions"][0]["to"] = 4
        parse_fail(tmp_path, data, "together")

    def test_valid_range_preserves_complete_alignment(self, tmp_path):
        data = base_config()
        data["partitions"][0].update({"from": 2, "to": 5})
        cfg = parse_ok(tmp_path, data)
        partition = cfg.partitions[0]
        assert partition.alignment.n_sites == 8
        assert partition.alignment.from_pos == 2
        assert partition.alignment.to_pos == 5
        assert partition.alignment.window_length == 4
        # Sequences are complete, not sliced.
        assert all(len(seq.sequence) == 8 for seq in partition.alignment.sequences)
        # The full alignment is retained for the one <alignment> element.
        assert cfg.all_alignments[0].n_sites == 8
        assert cfg.all_alignments[0].from_pos is None

    def test_no_range_sets_full_window(self, tmp_path):
        cfg = parse_ok(tmp_path, base_config())
        alignment = cfg.partitions[0].alignment
        assert (alignment.from_pos, alignment.to_pos) == (1, 8)

    def test_overlapping_ranges_rejected(self, tmp_path):
        data = add_partition(
            base_config(),
            id="p2",
            alignment_id="a1",
            **{"from": 4, "to": 8},
        )
        data["partitions"][0].update({"from": 1, "to": 5})
        parse_fail(tmp_path, data, "overlap")

    def test_disjoint_ranges_accepted(self, tmp_path):
        data = add_partition(
            base_config(),
            id="p2",
            alignment_id="a1",
            **{"from": 5, "to": 8},
        )
        data["partitions"][0].update({"from": 1, "to": 4})
        cfg = parse_ok(tmp_path, data)
        assert [p.alignment.window_length for p in cfg.partitions] == [4, 4]

    def test_alignment_slice_rejects_invalid_coordinates(self):
        alignment = Alignment(
            id="a",
            data_type=DataType.NUCLEOTIDE,
            sequences=[
                Sequence(taxon="A", sequence="ACGTACGT"),
                Sequence(taxon="B", sequence="ACGTACGA"),
            ],
        )
        for start, end in ((0, 4), (5, 3), (1, 99), (-1, 4)):
            with pytest.raises(ValueError):
                alignment.slice(start, end)
        sliced = alignment.slice(2, 5)
        assert sliced.n_sites == 4
        assert sliced.from_pos == 2 and sliced.to_pos == 5

    def test_partition_module_range_validation_runs(self):
        alignment = Alignment(
            id="a",
            data_type=DataType.NUCLEOTIDE,
            sequences=[Sequence(taxon="A", sequence="ACGT"), Sequence(taxon="B", sequence="ACGT")],
        )
        from beastxpy.partition import validate_partition_range

        with pytest.raises(PartitionError):
            validate_partition_range(alignment, 0, 4, partition_id="p")
        with pytest.raises(PartitionError):
            validate_partition_range(alignment, 3, 2, partition_id="p")
        assert validate_partition_range(alignment, 1, 4) == (1, 4)


class TestPartitionIdentifiers:
    def test_duplicate_alignment_ids_rejected(self, tmp_path):
        data = base_config()
        data["alignments"].append({"id": "a1", "file": "seqs.fasta", "data_type": "nucleotide"})
        parse_fail(tmp_path, data, "duplicate alignment id")

    def test_duplicate_partition_ids_rejected(self, tmp_path):
        data = add_partition(
            base_config(),
            id="p1",
            alignment_id="a1",
            **{"from": 5, "to": 8},
        )
        data["partitions"][0].update({"from": 1, "to": 4})
        message = parse_fail(tmp_path, data, "duplicate partition id")
        assert "partitions[1].id" in message

    def test_unsafe_alignment_and_partition_ids_rejected(self, tmp_path):
        data = base_config()
        data["alignments"][0]["id"] = "bad id"
        data["partitions"][0]["alignment_id"] = "bad id"
        parse_fail(tmp_path, data, "safe")
        data = base_config()
        data["partitions"][0]["id"] = "bad/id"
        parse_fail(tmp_path, data, "safe")

    def test_mismatched_taxon_sets_rejected(self, tmp_path):
        # Second alignment omits taxon D but uses the same tree.
        (tmp_path / "seqs.fasta").write_text(NUC_FASTA, encoding="utf-8")
        (tmp_path / "gene2.fasta").write_text(
            ">A\nACGTACGT\n>B\nACGTACGA\n>C\nACGTACGC\n", encoding="utf-8"
        )
        data = base_config()
        data["alignments"].append({"id": "a2", "file": "gene2.fasta", "data_type": "nucleotide"})
        data["partitions"].append(
            {
                "id": "p2",
                "alignment_id": "a2",
                "site_model": {"substitution_model": {"type": "hky"}},
                "clock_model": {"type": "strict"},
            }
        )
        parse_fail(tmp_path, data, "taxon set")

    def test_partition_collection_rejects_duplicate_ids(self, tmp_path):
        cfg = parse_ok(tmp_path, base_config())
        partition = cfg.partitions[0]
        duplicate = copy.copy(partition)
        with pytest.raises(PartitionError):
            collect_partitions([partition, duplicate])


# ---------------------------------------------------------------------------
# data types
# ---------------------------------------------------------------------------


class TestDataTypes:
    @pytest.mark.parametrize("spelling", ["amino-acid", "aminoacid", "amino acid", "AA", "protein"])
    def test_amino_acid_spellings_normalized(self, tmp_path, spelling):
        path = tmp_path / "prot.fasta"
        path.write_text(PROT_FASTA, encoding="utf-8")
        alignment = SequenceReader.read(str(path), data_type=spelling)
        assert alignment.data_type is DataType.AMINOACID
        assert alignment.beast_data_type == "amino acid"
        assert alignment.data_type_ambiguous is False

    @pytest.mark.parametrize("spelling", ["nucleotide", "NT", "dna"])
    def test_nucleotide_spellings_normalized(self, tmp_path, spelling):
        path = tmp_path / "nuc.fasta"
        path.write_text(NUC_FASTA, encoding="utf-8")
        alignment = SequenceReader.read(str(path), data_type=spelling)
        assert alignment.data_type is DataType.NUCLEOTIDE
        assert alignment.beast_data_type == "nucleotide"

    def test_codon_rejected(self, tmp_path):
        path = tmp_path / "nuc.fasta"
        path.write_text(NUC_FASTA, encoding="utf-8")
        with pytest.raises(AlignmentError, match="codon|unknown data_type"):
            SequenceReader.read(str(path), data_type="codon")

    def test_config_codon_rejected(self, tmp_path):
        data = base_config()
        data["alignments"][0]["data_type"] = "codon"
        parse_fail(tmp_path, data, "codon")

    def test_auto_acgt_data_type_is_reported(self, tmp_path):
        path = tmp_path / "nuc.fasta"
        path.write_text(NUC_FASTA, encoding="utf-8")
        alignment = SequenceReader.read(str(path))
        assert alignment.data_type is DataType.NUCLEOTIDE
        assert alignment.data_type_ambiguous is True

        data = base_config()
        data["alignments"][0].pop("data_type")
        cfg = parse_ok(tmp_path, data)
        assert any("auto-detected" in warning for warning in cfg.warnings)

    def test_explicit_amino_acid_overrides_ambiguous_alphabet(self, tmp_path):
        import tempfile

        path = Path(tempfile.mkdtemp()) / "acgt.fasta"
        path.write_text(NUC_FASTA, encoding="utf-8")
        alignment = SequenceReader.read(str(path), data_type="amino acid")
        assert alignment.data_type is DataType.AMINOACID
        assert alignment.data_type_ambiguous is False


# ---------------------------------------------------------------------------
# tip dates
# ---------------------------------------------------------------------------


class TestTipDates:
    def test_scalar_date_gets_default_units(self, tmp_path):
        data = base_config()
        data["tip_dates"] = {"A": 0.0}
        cfg = parse_ok(tmp_path, data)
        assert cfg.tip_dates["A"] == {
            "value": 0.0,
            "direction": "forwards",
            "units": "years",
        }

    @pytest.mark.parametrize("units", ["years", "months", "days"])
    def test_supported_units_preserved(self, tmp_path, units):
        data = base_config()
        data["tip_dates"] = {"A": {"value": 2, "direction": "backwards", "units": units}}
        cfg = parse_ok(tmp_path, data)
        assert cfg.tip_dates["A"]["units"] == units
        assert cfg.tip_dates["A"]["direction"] == "backwards"

    def test_weeks_rejected_for_beast_x_upstream_defect(self, tmp_path):
        # BEAST X 10.5.0 accepts the token in XMLUnits but TaxaParser prints
        # dates through TimeScale.unitString(), which throws on weeks.
        data = base_config()
        data["control"] = {"beast_version": "10.5.0"}
        data["tip_dates"] = {"A": {"value": 2, "units": "weeks"}}
        parse_fail(tmp_path, data, "TimeScale.unitString")

    def test_weeks_rejected_for_beast_1(self, tmp_path):
        data = base_config()
        data["tip_dates"] = {"A": {"value": 2, "units": "weeks"}}
        parse_fail(tmp_path, data, "weeks")

    def test_unknown_units_rejected(self, tmp_path):
        data = base_config()
        data["tip_dates"] = {"A": {"value": 2, "units": "sideways"}}
        parse_fail(tmp_path, data, "units")

    def test_unknown_direction_rejected(self, tmp_path):
        data = base_config()
        data["tip_dates"] = {"A": {"value": 2, "direction": "up"}}
        parse_fail(tmp_path, data, "direction")

    def test_unknown_taxon_rejected(self, tmp_path):
        data = base_config()
        data["tip_dates"] = {"NotATaxon": 1.0}
        parse_fail(tmp_path, data, "unknown taxon")

    def test_conflicting_duplicate_yaml_keys_rejected(self, tmp_path):
        text = """
metadata:
  analysis_name: t
alignments:
  - id: a1
    file: seqs.fasta
    data_type: nucleotide
tip_dates:
  A: {value: 1, units: years}
  A: {value: 2, units: years}
partitions:
  - id: p1
    alignment_id: a1
    site_model:
      substitution_model:
        type: hky
    clock_model:
      type: strict
      clock_rate: {value: 0.01, estimate: true}
tree_prior:
  type: yule
mcmc:
  chain_length: 1000
loggers:
  trace_log: {log_every: 10}
  tree_log: {log_every: 10}
"""
        path = write_raw_config(tmp_path, text)
        with pytest.raises(ConfigError, match="duplicate YAML key"):
            ConfigParser().parse(str(path))

    def test_taxon_date_units_always_valid(self):
        with pytest.raises(ValueError):
            Taxon(id="A", date=1.0, date_units="sideways")

    def test_normalize_tip_dates_is_the_single_implementation(self):
        normalized = normalize_tip_dates({"A": {"value": "1.5", "units": "days"}})
        assert normalized["A"] == {
            "value": 1.5,
            "direction": "forwards",
            "units": "days",
        }


# ---------------------------------------------------------------------------
# hyperpriors
# ---------------------------------------------------------------------------


class TestHyperpriors:
    def test_hyperpriors_exposed(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            {
                "name": "CladeA",
                "taxa": ["A", "B"],
                "distribution": {
                    "type": "lognormal",
                    "parameters": {"mean": 1.0, "stdev": 0.5},
                },
                "hyperpriors": {
                    "mean": {
                        "type": "uniform",
                        "parameters": {"lower": 0.0, "upper": 10.0},
                    }
                },
            }
        ]
        cfg = parse_ok(tmp_path, data)
        hyper = cfg.calibrations[0].hyperpriors["mean"]
        assert hyper.type == "uniform"
        assert hyper.parameters == {"lower": 0.0, "upper": 10.0}

    def test_unknown_hyperprior_target_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            {
                "name": "CladeA",
                "taxa": ["A", "B"],
                "distribution": {
                    "type": "lognormal",
                    "parameters": {"mean": 1.0, "stdev": 0.5},
                },
                "hyperpriors": {
                    "offset": {
                        "type": "uniform",
                        "parameters": {"lower": 0.0, "upper": 1.0},
                    }
                },
            }
        ]
        parse_fail(tmp_path, data, "not a hyperprior-able parameter")

    def test_invalid_hyperprior_distribution_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            {
                "name": "CladeA",
                "taxa": ["A", "B"],
                "distribution": {
                    "type": "lognormal",
                    "parameters": {"mean": 1.0, "stdev": 0.5},
                },
                "hyperpriors": {"mean": {"type": "gamma", "parameters": {"shape": 1.0}}},
            }
        ]
        parse_fail(tmp_path, data, "missing required parameter")

    def test_hyperprior_requires_distribution(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            {
                "name": "CladeA",
                "taxa": ["A", "B"],
                "hyperpriors": {
                    "mean": {
                        "type": "uniform",
                        "parameters": {"lower": 0.0, "upper": 1.0},
                    }
                },
            }
        ]
        parse_fail(tmp_path, data, "distribution")


# ---------------------------------------------------------------------------
# distributions and offset
# ---------------------------------------------------------------------------


class TestDistributions:
    def _calibration(self, distribution):
        return {
            "name": "CladeA",
            "taxa": ["A", "B"],
            "distribution": distribution,
        }

    def test_normal_offset_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._calibration(
                {"type": "normal", "parameters": {"mean": 1.0, "stdev": 1.0}, "offset": 3.0}
            )
        ]
        parse_fail(tmp_path, data, "no BEAST offset")

    def test_uniform_offset_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._calibration(
                {"type": "uniform", "parameters": {"lower": 0.0, "upper": 1.0}, "offset": 2.0}
            )
        ]
        parse_fail(tmp_path, data, "no BEAST offset")

    def test_supported_offsets_emitted(self):
        registry = ModelRegistry()
        beta = registry.distribution_attrs("beta", {"alpha": 2.0, "beta": 3.0}, 5.0)
        assert beta["offset"] == 5.0
        invgamma = registry.distribution_attrs("inverse_gamma", {"shape": 2.0, "scale": 1.0}, 4.0)
        assert invgamma["offset"] == 4.0
        lognormal = registry.distribution_attrs(
            "lognormal", {"mean": 1.0, "stdev": 0.5, "meanInRealSpace": False}, 2.5
        )
        assert lognormal["offset"] == 2.5

    def test_poisson_always_emits_offset(self):
        # BEAST's poissonPrior requires the offset attribute (no default).
        attrs = ModelRegistry().distribution_attrs("poisson", {"mean": 2.0}, 0.0)
        assert attrs["offset"] == 0.0
        assert attrs["mean"] == 2.0

    def test_exponential_rate_converted_to_mean(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._calibration({"type": "exponential", "parameters": {"rate": 0.5}})
        ]
        cfg = parse_ok(tmp_path, data)
        assert cfg.calibrations[0].distribution.parameters["mean"] == 2.0

    def test_exponential_mean_and_rate_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._calibration({"type": "exponential", "parameters": {"mean": 2.0, "rate": 0.5}})
        ]
        parse_fail(tmp_path, data, "not both")

    def test_chi_square_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._calibration({"type": "chi_square", "parameters": {"df": 2.0}})
        ]
        parse_fail(tmp_path, data, "not supported")

    def test_uniform_requires_bounds(self, tmp_path):
        data = base_config()
        data["calibrations"] = [self._calibration({"type": "uniform"})]
        parse_fail(tmp_path, data, "missing required parameter")

    def test_unknown_parameter_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._calibration(
                {"type": "gamma", "parameters": {"shape": 1.0, "scale": 1.0, "nope": 2}}
            )
        ]
        parse_fail(tmp_path, data, "unknown parameter")

    def test_lognormal_aliases_normalized(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._calibration({"type": "log-normal", "parameters": {"M": 1.0, "S": 0.5}})
        ]
        cfg = parse_ok(tmp_path, data)
        distribution = cfg.calibrations[0].distribution
        assert distribution.type == "lognormal"
        assert distribution.parameters == {"mean": 1.0, "stdev": 0.5}

    def test_gamma_aliases_normalized(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._calibration({"type": "gamma", "parameters": {"alpha": 2.0, "beta": 0.5}})
        ]
        cfg = parse_ok(tmp_path, data)
        assert cfg.calibrations[0].distribution.parameters == {
            "shape": 2.0,
            "scale": 0.5,
        }


# ---------------------------------------------------------------------------
# calibrations
# ---------------------------------------------------------------------------


class TestCalibrations:
    def _cal(self, name="CladeA", taxa=("A", "B"), **extra):
        entry = {
            "name": name,
            "taxa": list(taxa) if taxa is not None else None,
            "distribution": {
                "type": "lognormal",
                "parameters": {"mean": 1.0, "stdev": 0.5},
                "offset": 10.0,
            },
        }
        entry.update(extra)
        return entry

    def test_missing_distribution_rejected(self, tmp_path):
        cal = self._cal()
        cal.pop("distribution")
        data = base_config()
        data["calibrations"] = [cal]
        parse_fail(tmp_path, data, "distribution")

    def test_missing_taxa_key_rejected(self, tmp_path):
        cal = self._cal()
        cal.pop("taxa")
        data = base_config()
        data["calibrations"] = [cal]
        parse_fail(tmp_path, data, "taxa")

    def test_root_calibration_normalized(self, tmp_path):
        data = base_config()
        data["calibrations"] = [self._cal(name="RootAge", taxa=None)]
        cfg = parse_ok(tmp_path, data)
        root = cfg.calibrations[0]
        assert root.is_root is True
        assert root.taxa is None
        assert root.node_type == "root"
        assert cfg.root_calibration is root
        assert cfg.calibration_nodes[0]["is_root"] is True

    def test_root_requires_distribution(self, tmp_path):
        cal = self._cal(name="RootAge", taxa=None)
        cal.pop("distribution")
        data = base_config()
        data["calibrations"] = [cal]
        parse_fail(tmp_path, data, "distribution")

    def test_root_cannot_be_stem(self, tmp_path):
        data = base_config()
        data["calibrations"] = [self._cal(name="RootAge", taxa=None, calibration_method="stem")]
        parse_fail(tmp_path, data, "root")

    def test_at_most_one_root(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._cal(name="RootAge", taxa=None),
            self._cal(name="RootAge2", taxa=None),
        ]
        parse_fail(tmp_path, data, "at most one root")

    def test_duplicate_calibration_names_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._cal(),
            self._cal(taxa=("C", "D")),
        ]
        parse_fail(tmp_path, data, "duplicate calibration name")

    def test_duplicate_clade_definition_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._cal(name="A"),
            self._cal(name="B"),
        ]
        parse_fail(tmp_path, data, "duplicate clade definition")

    def test_unknown_taxon_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [self._cal(taxa=("A", "NotATaxon"))]
        parse_fail(tmp_path, data, "unknown taxon")

    def test_unsafe_calibration_name_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [self._cal(name="Crown/Catarrhini")]
        parse_fail(tmp_path, data, "safe")

    def test_reserved_calibration_name_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [self._cal(name="taxa")]
        parse_fail(tmp_path, data, "safe")

    def test_crown_alias_normalized(self, tmp_path):
        data = base_config()
        data["calibrations"] = [self._cal(calibration_method="crown")]
        cfg = parse_ok(tmp_path, data)
        cal = cfg.calibrations[0]
        assert cal.calibration_method == "clade"
        assert cal.node_type == "crown"

    def test_stem_exposed_explicitly(self, tmp_path):
        data = base_config()
        data["calibrations"] = [self._cal(calibration_method="stem")]
        cfg = parse_ok(tmp_path, data)
        cal = cfg.calibrations[0]
        assert cal.calibration_method == "stem"
        assert cal.node_type == "stem"
        assert cal.node_key == (frozenset({"A", "B"}), "stem")

    def test_provenance_upper_alias_accepted(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._cal(
                provenance={
                    "source": "fossil",
                    "calibration_type": "upper",
                    "original_age_min": 9.0,
                    "original_age_max": 12.0,
                }
            )
        ]
        cfg = parse_ok(tmp_path, data)
        assert cfg.calibrations[0].provenance.calibration_type == "soft"

    def test_nested_clades_accepted(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._cal(name="Outer", taxa=("A", "B", "C")),
            self._cal(name="Inner", taxa=("A", "B")),
        ]
        cfg = parse_ok(tmp_path, data)
        assert [c.name for c in cfg.calibrations] == ["Outer", "Inner"]
        nodes = {node["name"]: node for node in cfg.calibration_nodes}
        assert nodes["Inner"]["parent"] == "Outer"
        assert nodes["Outer"]["parent"] is None

    def test_partially_overlapping_clades_rejected(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._cal(name="Left", taxa=("A", "B")),
            self._cal(name="Right", taxa=("B", "C")),
        ]
        parse_fail(tmp_path, data, "partially overlap")

    def test_root_and_clade_coexist(self, tmp_path):
        data = base_config()
        data["calibrations"] = [
            self._cal(name="RootAge", taxa=None),
            self._cal(name="CladeA", taxa=("A", "B")),
        ]
        cfg = parse_ok(tmp_path, data)
        assert [c.is_root for c in cfg.calibrations] == [True, False]


# ---------------------------------------------------------------------------
# loggers
# ---------------------------------------------------------------------------


class TestLoggers:
    def test_unknown_logger_key_rejected(self, tmp_path):
        data = base_config()
        data["loggers"]["trace_log"]["screen"] = True
        parse_fail(tmp_path, data, "unknown key")

    def test_unknown_logger_section_rejected(self, tmp_path):
        data = base_config()
        data["loggers"]["stdout_log"] = {"log_every": 10}
        parse_fail(tmp_path, data, "unknown key")

    def test_zero_log_every_rejected(self, tmp_path):
        data = base_config()
        data["loggers"]["trace_log"]["log_every"] = 0
        parse_fail(tmp_path, data, ">= 1")

    def test_log_every_above_chain_length_rejected(self, tmp_path):
        data = base_config()
        data["loggers"]["tree_log"]["log_every"] = 5000
        parse_fail(tmp_path, data, "chain_length")

    def test_string_boolean_rejected(self, tmp_path):
        data = base_config()
        data["loggers"]["tree_log"]["nexus_format"] = "true"
        parse_fail(tmp_path, data, "boolean")

    def test_unsafe_log_file_name_rejected(self, tmp_path):
        data = base_config()
        data["loggers"]["trace_log"]["file_name"] = "../escape.log"
        parse_fail(tmp_path, data, "file name")

    def test_trace_and_tree_file_must_differ(self, tmp_path):
        data = base_config()
        data["loggers"]["trace_log"]["file_name"] = "same.log"
        data["loggers"]["tree_log"]["file_name"] = "same.log"
        parse_fail(tmp_path, data, "must differ")

    def test_valid_loggers_preserved(self, tmp_path):
        data = base_config()
        data["loggers"]["trace_log"]["file_name"] = "run.log"
        data["loggers"]["tree_log"].update({"file_name": "run.trees", "nexus_format": False})
        cfg = parse_ok(tmp_path, data)
        assert cfg.loggers.trace_log["file_name"] == "run.log"
        assert cfg.loggers.tree_log["nexus_format"] is False


# ---------------------------------------------------------------------------
# pre-burnin / operator weights / auto-optimize
# ---------------------------------------------------------------------------


class TestMCMC:
    def test_pre_burnin_and_weights_exposed(self, tmp_path):
        data = base_config()
        data["mcmc"].update({"pre_burnin": 100, "operator_weights": {"clock.rate": 2.5}})
        cfg = parse_ok(tmp_path, data)
        assert cfg.mcmc.pre_burnin == 100
        assert cfg.mcmc.operator_weights == {"clock.rate": 2.5}
        assert cfg.normalized()["mcmc"]["pre_burnin"] == 100

    def test_pre_burnin_not_below_chain(self, tmp_path):
        data = base_config()
        data["mcmc"]["pre_burnin"] = 1000
        parse_fail(tmp_path, data, "smaller than chain_length")

    def test_operator_weight_must_be_positive(self, tmp_path):
        data = base_config()
        data["mcmc"]["operator_weights"] = {"clock.rate": 0}
        parse_fail(tmp_path, data, "> 0")

    def test_operator_weights_must_be_mapping(self, tmp_path):
        data = base_config()
        data["mcmc"]["operator_weights"] = ["clock.rate"]
        parse_fail(tmp_path, data, "mapping")

    def test_auto_optimize_parsed(self, tmp_path):
        data = base_config()
        data["mcmc"]["autoOptimize"] = False
        cfg = parse_ok(tmp_path, data)
        assert cfg.mcmc.auto_optimize is False

    def test_auto_optimize_string_rejected(self, tmp_path):
        data = base_config()
        data["mcmc"]["auto_optimize"] = "false"
        parse_fail(tmp_path, data, "boolean")


# ---------------------------------------------------------------------------
# skyline / EBSP population sizes
# ---------------------------------------------------------------------------


class TestSkylineParameters:
    def test_skyline_groups_exposed(self, tmp_path):
        data = base_config()
        data["tree_prior"] = {"type": "skyline", "skyline_groups": 5}
        cfg = parse_ok(tmp_path, data)
        assert cfg.tree_prior.params["skyline_groups"] == 5
        assert len(cfg.tree_prior.params["skyline_pop_sizes"]) == 5

    def test_skyline_pop_sizes_list_exposed(self, tmp_path):
        data = base_config()
        data["tree_prior"] = {
            "type": "skyline",
            "skyline_pop_sizes": [0.5, 1.0, 2.0],
        }
        cfg = parse_ok(tmp_path, data)
        assert cfg.tree_prior.params["skyline_pop_sizes"] == [0.5, 1.0, 2.0]
        assert cfg.tree_prior.params["skyline_groups"] == 3

    def test_skyline_group_count_mismatch_rejected(self, tmp_path):
        data = base_config()
        data["tree_prior"] = {
            "type": "skyline",
            "skyline_groups": 5,
            "skyline_pop_sizes": [1.0, 2.0],
        }
        parse_fail(tmp_path, data, "does not match")

    def test_non_positive_population_size_rejected(self, tmp_path):
        data = base_config()
        data["tree_prior"] = {
            "type": "skyline",
            "skyline_pop_sizes": [1.0, 0.0],
        }
        parse_fail(tmp_path, data, "> 0")

    def test_ebsp_pop_sizes_exposed(self, tmp_path):
        data = base_config()
        data["tree_prior"] = {"type": "ebsp", "ebsp_pop_sizes": 4}
        cfg = parse_ok(tmp_path, data)
        assert cfg.tree_prior.params["ebsp_pop_sizes"] == [0.5, 0.5, 0.5, 0.5]

    def test_skyline_keys_rejected_for_yule(self, tmp_path):
        data = base_config()
        data["tree_prior"] = {"type": "yule", "skyline_groups": 4}
        parse_fail(tmp_path, data, "not used")

    def test_tree_prior_alias_accepted(self, tmp_path):
        data = base_config()
        data["tree_prior"] = {"type": "birthDeath", "birth_minus_death": 0.5, "relative_death": 0.2}
        cfg = parse_ok(tmp_path, data)
        assert cfg.tree_prior.type.value == "birth_death"


# ---------------------------------------------------------------------------
# sequence reading
# ---------------------------------------------------------------------------


class TestSequenceReading:
    def test_zero_length_alignment_rejected(self, tmp_path):
        path = tmp_path / "empty.fasta"
        path.write_text(">A\n\n>B\n\n", encoding="utf-8")
        with pytest.raises(AlignmentError, match="zero sites"):
            SequenceReader.read(str(path))

    def test_single_sequence_alignment_rejected(self, tmp_path):
        path = tmp_path / "single.fasta"
        path.write_text(">A\nACGT\n", encoding="utf-8")
        with pytest.raises(AlignmentError, match="single sequence"):
            SequenceReader.read(str(path))

    def test_validate_alignment_reports_zero_length(self):
        alignment = Alignment(
            id="empty",
            data_type=DataType.NUCLEOTIDE,
            sequences=[Sequence(taxon="A", sequence=""), Sequence(taxon="B", sequence="")],
        )
        findings = validate_alignment(alignment)
        assert any("zero sites" in finding for finding in findings)

    def test_nexus_match_character_expanded(self, tmp_path):
        path = tmp_path / "dots.nex"
        path.write_text(
            "#NEXUS\nBEGIN DATA;\n"
            "    DIMENSIONS NTAX=3 NCHAR=8;\n"
            "    FORMAT DATATYPE=DNA MISSING=? GAP=- INTERLEAVE=YES;\n"
            "    MATRIX\n"
            "    A ACGT\n    B .C.T\n    C A..T\n\n"
            "    A ACGT\n    B ACGT\n    C ACGT\n"
            "    ;\nEND;\n",
            encoding="utf-8",
        )
        alignment = SequenceReader.read(str(path), format="nexus")
        sequences = {seq.taxon: seq.sequence for seq in alignment.sequences}
        assert sequences["B"] == "ACGTACGT"
        assert sequences["C"] == "ACGTACGT"

    def test_nexus_without_dots_still_parses(self, tmp_path):
        path = tmp_path / "plain.nex"
        path.write_text(
            "#NEXUS\nBEGIN DATA;\n"
            "    DIMENSIONS NTAX=2 NCHAR=4;\n"
            "    FORMAT DATATYPE=DNA GAP=- MISSING=?;\n"
            "    MATRIX\n    A ACGT\n    B ACG?\n    ;\nEND;\n",
            encoding="utf-8",
        )
        alignment = SequenceReader.read(str(path), format="nexus")
        assert [seq.sequence for seq in alignment.sequences] == ["ACGT", "ACG?"]

    def test_utf8_taxon_names_read_correctly(self, tmp_path):
        path = tmp_path / "utf8.fasta"
        path.write_text(">Čáp\nACGT\n>B\nACGT\n", encoding="utf-8")
        alignment = SequenceReader.read(str(path), data_type="nucleotide")
        assert alignment.taxa_names == ["Čáp", "B"]

    def test_non_utf8_file_rejected(self, tmp_path):
        path = tmp_path / "gbk.fasta"
        path.write_bytes(">中\nACGT\n>B\nACGT\n".encode("gbk"))
        with pytest.raises(AlignmentError, match="UTF-8"):
            SequenceReader.read(str(path))


# ---------------------------------------------------------------------------
# safe identifiers
# ---------------------------------------------------------------------------


class TestSafeIdentifiers:
    def test_invalid_names_rejected(self):
        for name in ("", "1abc", "a b", "a/b", "a:b", "taxa", "treeModel", ".."):
            assert not is_safe_identifier(name), name

    def test_safe_identifier_sanitizes_artifacts(self):
        first = safe_identifier("<script>alert(1)</script>&")
        second = safe_identifier("Crown/Catarrhini")
        third = safe_identifier("Crown:Catarrhini")
        assert all(" " not in value for value in (first, second, third))
        assert all("/" not in value and ":" not in value for value in (first, second, third))
        assert all("<" not in value and ">" not in value for value in (first, second, third))
        assert len({first, second, third}) == 3
        assert second.startswith("Crown_Catarrhini_")
        assert second != third  # digest keeps distinct originals distinct

    def test_safe_filename(self):
        assert is_safe_filename("basic.log")
        assert not is_safe_filename("../basic.log")
        assert not is_safe_filename("sub/basic.log")
        assert not is_safe_filename("CON.log")


# ---------------------------------------------------------------------------
# Audit + normalized exposure
# ---------------------------------------------------------------------------


class TestExposure:
    def test_field_audit_covers_required_fields(self):
        required = {
            "calibrations[].hyperpriors",
            "calibrations[].distribution",
            "calibrations[].is_root",
            "mcmc.pre_burnin",
            "mcmc.operator_weights",
            "mcmc.auto_optimize",
            "control.seed",
        }
        assert required <= set(CONFIG_FIELD_AUDIT)
        for entry in CONFIG_FIELD_AUDIT.values():
            assert entry["status"] in {
                "implemented",
                "validated",
                "exposed",
                "rejected",
            }

    def test_normalized_view_exposes_gate1_values(self, tmp_path):
        data = base_config()
        data["tip_dates"] = {"A": 1.0}
        data["partitions"][0].update({"from": 1, "to": 4})
        data["calibrations"] = [
            {
                "name": "CladeA",
                "taxa": ["A", "B"],
                "distribution": {
                    "type": "lognormal",
                    "parameters": {"mean": 1.0, "stdev": 0.5},
                },
            }
        ]
        cfg = parse_ok(tmp_path, data)
        normalized = cfg.normalized()
        assert normalized["data_types"]["a1"] == "nucleotide"
        assert normalized["partitions"][0]["from"] == 1
        assert normalized["partitions"][0]["to"] == 4
        assert normalized["partitions"][0]["alignment_sites"] == 8
        assert normalized["tip_dates"]["A"]["units"] == "years"
        assert normalized["calibrations"][0]["node_type"] == "crown"
        assert normalized["mcmc"]["auto_optimize"] is True

    def test_output_fields_validated(self, tmp_path):
        data = base_config()
        data["output"] = {"embed_fingerprint": "yes"}
        parse_fail(tmp_path, data, "boolean")
        data = base_config()
        data["output"] = {"file": "../escape.xml"}
        parse_fail(tmp_path, data, "file name")


# ---------------------------------------------------------------------------
# Examples must keep passing Gate 0
# ---------------------------------------------------------------------------


class TestExamples:
    def test_all_examples_parse(self, examples_dir):
        configs = sorted(Path(examples_dir).glob("config_*.yaml"))
        assert len(configs) == 16
        parser = ConfigParser()
        for path in configs:
            config = parser.parse(str(path))
            assert config.partitions
            for partition in config.partitions:
                assert partition.alignment.n_sites > 0
                assert partition.alignment.from_pos >= 1
                assert partition.alignment.to_pos >= partition.alignment.from_pos
            for cal in config.calibrations:
                assert cal.distribution is not None
            for taxon, entry in config.tip_dates.items():
                assert entry["units"] in {"years", "months", "days"}


class TestSubstitutionOptionStrictness:
    @pytest.mark.parametrize(
        "freq,needle",
        [
            ({"mode": "fixed", "value": [0.1, 0.9]}, "four numbers"),
            ({"mode": "estimated", "dimension": 2}, "exactly four"),
            ({"mode": "estimated", "value": [0.1, 0.2, 0.3]}, "four numbers"),
            ({"mode": "fixed", "value": [0.1, 0.2, 0.3, 0.5]}, "sum to 1"),
            ({"mode": "uniform", "value": [0.25] * 4}, "computes its own vector"),
        ],
    )
    def test_bad_nucleotide_frequencies_rejected(self, tmp_path, freq, needle):
        data = base_config()
        data["partitions"][0]["site_model"]["substitution_model"] = {
            "type": "hky",
            "frequencies": freq,
        }
        parse_fail(tmp_path, data, needle)

    @pytest.mark.parametrize("model", ["jc69", "tn93", "gtr"])
    def test_kappa_only_for_hky(self, tmp_path, model):
        data = base_config()
        data["partitions"][0]["site_model"]["substitution_model"] = {
            "type": model,
            "kappa": 2.0,
        }
        parse_fail(tmp_path, data, "only valid for substitution_model.type 'hky'")

    def test_amino_acid_frequencies_must_be_empirical_noop(self, tmp_path):
        data = base_config()
        data["alignments"][0]["data_type"] = "amino-acid"
        data["partitions"][0]["site_model"]["substitution_model"] = {
            "type": "wag",
            "frequencies": {"mode": "estimated"},
        }
        parse_fail(tmp_path, data, "carries its own frequencies")

        data["partitions"][0]["site_model"]["substitution_model"] = {
            "type": "wag",
            "frequencies": {"mode": "empirical"},
        }
        cfg = parse_ok(tmp_path, data)
        assert cfg.partitions[0].site_model.substitution_model["type"] == "wag"

    @pytest.mark.parametrize("mode", ["upgma", "newick"])
    def test_calibrations_reject_unconstrained_start_tree(self, tmp_path, mode):
        data = base_config()
        data["control"] = {
            "initial_tree": mode,
            "newick": "(((A,B),C),D);" if mode == "newick" else None,
        }
        data["calibrations"] = [
            {
                "name": "Cal",
                "taxa": ["A", "B"],
                "distribution": {"type": "uniform", "parameters": {"lower": 1.0, "upper": 2.0}},
            }
        ]
        parse_fail(tmp_path, data, "cannot be combined with node calibrations")


class TestAnalysisVersionConsistency:
    @pytest.mark.parametrize("analysis_type", ["aa_mixture", "age_statistic", "tree_report"])
    def test_single_version_analysis_requires_matching_control(self, tmp_path, analysis_type):
        data = base_config()
        data["control"] = {"beast_version": "1.10.4"}
        data["analysis"] = {"type": analysis_type, "beast_version": "10.5.0"}
        parse_fail(tmp_path, data, "conflicts with control.beast_version")

    def test_both_analysis_accepts_either_control(self, tmp_path):
        data = base_config()
        data["analysis"] = {"type": "ancestral", "beast_version": "both"}
        for version in ("1.10.4", "10.5.0"):
            data["control"] = {"beast_version": version}
            cfg = parse_ok(tmp_path, data)
            assert cfg.analysis["beast_version"] == "both"


def test_invalid_tree_prior_type_echoes_the_user_value():
    from beastxpy.config import ConfigParser

    errors = []
    ConfigParser()._parse_tree_prior({"type": "nonexistent_prior"}, errors)
    assert errors
    assert "nonexistent_prior" in errors[0]
    assert "None" not in errors[0]
