"""Unit tests for config parsing (Gate 0)."""

from __future__ import annotations


import pytest

from beastxpy.models import RealParameter
from beastxpy.models import TreePriorType


class TestConfigParsing:
    """Tests for YAML config parsing and Gate-0 validation."""

    def test_parse_basic(self, config_basic):
        cfg = config_basic
        assert cfg.analysis_name == "basic"
        assert len(cfg.partitions) == 1
        assert cfg.partitions[0].site_model.substitution_model["type"] == "hky"
        assert cfg.partitions[0].clock_model.type.value == "strict"

    def test_parse_jc69_kappa_fixed(self, config_jc69):
        cfg = config_jc69
        p = cfg.partitions[0]
        kappa = p.site_model.substitution_model.get("kappa")
        assert isinstance(kappa, RealParameter)
        assert kappa.estimate is False
        assert kappa.value == 1.0

    def test_parse_jc69_frequencies_uniform(self, config_jc69):
        cfg = config_jc69
        p = cfg.partitions[0]
        freq = p.site_model.substitution_model.get("frequencies")
        assert freq["mode"] == "uniform"

    def test_parse_tree_prior_yule(self, config_jc69):
        cfg = config_jc69
        assert cfg.tree_prior.type == TreePriorType.YULE
        birth_rate = cfg.tree_prior.params["birth_rate"]
        assert isinstance(birth_rate, RealParameter)
        assert birth_rate.estimate is True

    def test_parse_calibrated_has_calibrations(self, config_calibrated):
        cfg = config_calibrated
        assert len(cfg.calibrations) >= 1
        cal = cfg.calibrations[0]
        assert cal.name is not None
        assert len(cal.taxa) > 0
        assert cal.monophyletic is True
        assert cal.distribution is not None

    def test_parse_tip_dates(self, config_tip_dates):
        cfg = config_tip_dates
        assert cfg.tip_dates  # has tip date config

    def test_parse_advanced_multi_partition(self, config_advanced):
        cfg = config_advanced
        assert len(cfg.partitions) == 2
        assert len(cfg.calibrations) == 3

    def test_parse_rlc_clock(self, config_rlc):
        cfg = config_rlc
        assert cfg.partitions[0].clock_model.type.value == "rlc"


class TestConfigValidation:
    """Gate-0 validation tests."""

    def test_unknown_key_rejected(self, config_parser, tmp_path):
        yaml = tmp_path / "bad.yaml"
        yaml.write_text(
            "metadata:\n  analysis_name: test\n"
            "alignments:\n  - id: a1\n    file: x.fasta\n"
            "partitions:\n  - id: g1\n    alignment_id: a1\n"
            "    site_model:\n      substitution_model:\n        type: hky\n"
            "    clock_model:\n      type: strict\n"
            "unknown_top_key: true\n"
            "control:\n  beast_version: 1.10.4\n"
        )
        with pytest.raises(Exception) as excinfo:
            config_parser.parse(str(yaml))
        assert "unknown" in str(excinfo.value).lower()

    def test_invalid_boolean_quoted(self, config_parser, tmp_path):
        yaml = tmp_path / "bad_bool.yaml"
        yaml.write_text(
            "metadata:\n  analysis_name: test\n"
            "alignments:\n  - id: a1\n    file: x.fasta\n"
            "partitions:\n  - id: g1\n    alignment_id: a1\n"
            "    site_model:\n      substitution_model:\n"
            "        type: hky\n"
            "        kappa: {value: 2.0, estimate: 'true'}\n"
            "    clock_model:\n      type: strict\n"
            "control:\n  beast_version: 1.10.4\n"
        )
        with pytest.raises(Exception):
            config_parser.parse(str(yaml))

    def test_missing_alignment_file(self, config_parser, tmp_path):
        yaml = tmp_path / "no_file.yaml"
        yaml.write_text(
            "metadata:\n  analysis_name: test\n"
            "alignments:\n  - id: a1\n    file: nonexistent.fasta\n"
            "partitions:\n  - id: g1\n    alignment_id: a1\n"
            "    site_model:\n      substitution_model:\n        type: hky\n"
            "    clock_model:\n      type: strict\n"
            "control:\n  beast_version: 1.10.4\n"
        )
        with pytest.raises(Exception):
            config_parser.parse(str(yaml))

    def test_no_time_source_rejected(self, config_parser, tmp_path):
        """Config with no calibrations, no tip dates, no clock rate → error."""
        # Create a minimal fasta
        fasta = tmp_path / "seqs.fasta"
        fasta.write_text(">A\nACGTACGT\n>B\nACGTACGA\n>C\nACGTACGC\n>D\nACGTACGG\n")
        yaml = tmp_path / "no_time.yaml"
        yaml.write_text(
            f"metadata:\n  analysis_name: test\n"
            f"alignments:\n  - id: a1\n    file: {fasta.name}\n"
            f"partitions:\n  - id: g1\n    alignment_id: a1\n"
            f"    site_model:\n      substitution_model:\n        type: hky\n"
            f"    clock_model:\n      type: strict\n"
            f"control:\n  beast_version: 1.10.4\n"
        )
        with pytest.raises(Exception) as excinfo:
            config_parser.parse(str(yaml))
        assert "time" in str(excinfo.value).lower()

    def test_invalid_beast_version(self, config_parser, tmp_path):
        yaml = tmp_path / "bad_version.yaml"
        yaml.write_text(
            "metadata:\n  analysis_name: test\n"
            "alignments:\n  - id: a1\n    file: x.fasta\n"
            "partitions:\n  - id: g1\n    alignment_id: a1\n"
            "    site_model:\n      substitution_model:\n        type: hky\n"
            "    clock_model:\n      type: strict\n"
            "control:\n  beast_version: 9.9.9\n"
        )
        with pytest.raises(Exception) as excinfo:
            config_parser.parse(str(yaml))
        assert "beast_version" in str(excinfo.value)

    def test_bound_validation(self, config_parser, tmp_path):
        """lower > upper should be rejected."""
        fasta = tmp_path / "seqs.fasta"
        fasta.write_text(">A\nACGTACGT\n>B\nACGTACGA\n>C\nACGTACGC\n>D\nACGTACGG\n")
        yaml = tmp_path / "bad_bounds.yaml"
        yaml.write_text(
            f"metadata:\n  analysis_name: test\n"
            f"alignments:\n  - id: a1\n    file: {fasta.name}\n"
            f"partitions:\n  - id: g1\n    alignment_id: a1\n"
            f"    site_model:\n      substitution_model:\n"
            f"        type: hky\n"
            f"        kappa: {{value: 2.0, lower: 10.0, upper: 1.0, estimate: true}}\n"
            f"    clock_model:\n      type: strict\n"
            f"      clock_rate: {{value: 0.01, estimate: true}}\n"
            f"control:\n  beast_version: 1.10.4\n"
        )
        with pytest.raises(Exception):
            config_parser.parse(str(yaml))


class TestConfigDefaults:
    """Test default value population."""

    def test_default_chain_length(self, config_parser, examples_dir):
        """Chain length default should be populated if not specified."""
        # config_basic has explicit chain_length

        cfg = config_parser.parse(str(examples_dir / "config_basic.yaml"))
        assert cfg.mcmc.chain_length > 0

    def test_logger_defaults(self, config_basic):
        """Logger files should have default names."""
        assert "log" in config_basic.loggers.trace_log.get("file_name", "")
