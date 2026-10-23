"""Tests for F2 ``check-data``: datacheck.py, cli_check_data.py and the
append-only inspectors in sequence.py / partition.py / calibration.py.

Fixture configs live in ``tests/fixtures/datacheck/<case>/config.yaml``; the
CLI is exercised in a subprocess exactly like the rest of the suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from beastxpy import _contracts as C
from beastxpy.calibration import (
    calibration_age_interval,
    empty_taxa_calibrations,
    missing_calibration_taxa,
)
from beastxpy.config import ConfigParser
from beastxpy.datacheck import (
    DEFAULT_AMBIGUITY_THRESHOLD,
    FINDING_SEVERITIES,
    build_lenient_config,
    check_config,
    check_config_file,
    collect_findings,
    envelope_exit_code,
    findings_exit_code,
    run_precheck,
)
from beastxpy.models import (
    Alignment,
    BEASTConfig,
    CalibrationPoint,
    ClockModelConfig,
    ControlConfig,
    DataType,
    DistributionConfig,
    Partition,
    Sequence,
    SiteModelConfig,
)
from beastxpy.partition import (
    overlapping_window_pairs,
    resolve_partition_window,
    taxon_set_mismatches,
    uncovered_site_ranges,
)
from beastxpy.sequence import (
    AlignmentError,
    ambiguity_fractions,
    codon_length_defect,
    definite_chars,
    duplicate_sequence_groups,
    duplicate_taxa,
    empty_sequence_taxa,
    illegal_characters_by_taxon,
    internal_stop_positions,
    is_codon_data_type,
    read_alignment_lenient,
    stop_codon_taxa,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "datacheck"

# The contract table (CONTRACT.md §5 F2): finding id -> required severity.
CONTRACT_TABLE = {
    "duplicate-taxon": "error",
    "empty-taxon": "error",
    "duplicate-sequence": "warning",
    "illegal-character": "error",
    "high-ambiguity": "warning",
    "partition-overlap": "error",
    "partition-gap": "warning",
    "partition-out-of-range": "error",
    "partition-taxon-mismatch": "error",
    "calibration-taxon-missing": "error",
    "calibration-empty-taxa": "error",
    "tip-date-format": "error",
    "tip-date-order": "warning",
    "root-height-conflict": "error",
    "codon-length": "error",
    "codon-stop": "warning",
    "empirical-tree-taxa": "error",
}


def fixture_config(name: str) -> Path:
    return FIXTURES / name / "config.yaml"


def run_cli(args, timeout=120):
    return subprocess.run(
        [sys.executable, "-m", "beastxpy.main", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(ROOT),
    )


def make_alignment(pairs, data_type=DataType.NUCLEOTIDE, aid="aln", source=None):
    return Alignment(
        id=aid,
        data_type=data_type,
        sequences=[Sequence(taxon=t, sequence=s) for t, s in pairs],
        source_file=source,
    )


def make_config(
    pairs,
    *,
    data_type=DataType.NUCLEOTIDE,
    calibrations=None,
    tip_dates=None,
    newick=None,
    site_model_type="jc69",
    partitions=None,
):
    """A minimal hand-built BEASTConfig over one 4-taxon alignment."""
    alignment = make_alignment(pairs, data_type=data_type)
    if partitions is None:
        partitions = [
            Partition(
                id="p1",
                alignment=alignment,
                site_model=SiteModelConfig(substitution_model={"type": site_model_type}),
                clock_model=ClockModelConfig(),
            )
        ]
    return BEASTConfig(
        metadata={"analysis_name": "handbuilt"},
        partitions=partitions,
        all_alignments=[alignment],
        calibrations=list(calibrations or []),
        tip_dates=dict(tip_dates or {}),
        control=ControlConfig(initial_tree="newick" if newick else "coalescent", newick=newick),
    )


CLEAN_PAIRS = [
    ("t1", "ACGTACGTACGT"),
    ("t2", "ACGTACGAACGT"),
    ("t3", "ACGAACGTACGT"),
    ("t4", "ACGTACGTACGA"),
]


def ids(findings):
    return [f["id"] for f in findings]


# ---------------------------------------------------------------------------
# sequence.py inspectors
# ---------------------------------------------------------------------------


class TestSequenceInspectors:
    def test_duplicate_taxa(self):
        alignment = make_alignment([("t1", "ACGT"), ("t2", "ACGA"), ("t2", "ACGC"), ("t3", "ACGG")])
        assert duplicate_taxa(alignment) == ["t2"]
        assert duplicate_taxa(make_alignment(CLEAN_PAIRS)) == []

    def test_empty_sequence_taxa(self):
        alignment = make_alignment([("t1", "ACGT"), ("t2", ""), ("t3", "   ")])
        assert empty_sequence_taxa(alignment) == ["t2", "t3"]

    def test_duplicate_sequence_groups(self):
        alignment = make_alignment(
            [("t1", "ACGT"), ("t2", "acgt"), ("t3", "GGGG"), ("t4", ""), ("t5", "")]
        )
        # Case-insensitive match; empty sequences are excluded.
        assert duplicate_sequence_groups(alignment) == [["t1", "t2"]]

    def test_illegal_characters_by_taxon(self):
        alignment = make_alignment([("t1", "ACGTQCGT"), ("t2", "ACGT-?N"), ("t3", "12ACGTAC")])
        result = illegal_characters_by_taxon(alignment)
        assert result["t1"]["chars"] == ["Q"]
        assert result["t1"]["sites"] == [5]
        assert "t2" not in result  # gaps and IUPAC codes are legal
        assert result["t3"]["chars"] == ["1", "2"]
        assert result["t3"]["sites"] == [1, 2]

    def test_ambiguity_fractions(self):
        alignment = make_alignment([("t1", "ACGT"), ("t2", "NNNN"), ("t3", "AC-N"), ("t4", "")])
        fractions = ambiguity_fractions(alignment)
        assert fractions["t1"] == 0.0
        assert fractions["t2"] == 1.0
        assert fractions["t3"] == pytest.approx(0.5)
        assert fractions["t4"] == 0.0

    def test_definite_chars(self):
        assert "A" in definite_chars(DataType.NUCLEOTIDE)
        assert "N" not in definite_chars(DataType.NUCLEOTIDE)
        assert "W" in definite_chars(DataType.AMINOACID)
        assert "*" in definite_chars(DataType.AMINOACID)

    def test_is_codon_data_type(self):
        assert is_codon_data_type("codon")
        assert is_codon_data_type("  Protein-Coding ")
        assert is_codon_data_type("cds")
        assert not is_codon_data_type("nucleotide")
        assert not is_codon_data_type(None)
        assert not is_codon_data_type(42)

    def test_codon_length_defect(self):
        assert codon_length_defect(make_alignment([("t1", "ATGAAA"), ("t2", "ATGCCC")])) == 0
        assert codon_length_defect(make_alignment([("t1", "ATGAA"), ("t2", "CCCAA")])) == 2
        # Ragged and empty alignments are not applicable.
        assert codon_length_defect(make_alignment([("t1", "ATG"), ("t2", "ATGA")])) is None
        assert codon_length_defect(make_alignment([("t1", ""), ("t2", "")])) is None

    def test_internal_stop_positions(self):
        # ATG TAA CGT TAA: the internal stop is codon 2; the final TAA is
        # terminal, not internal.
        assert internal_stop_positions("ATGTAAACGT") == [2]
        # ATG TAA TAA CGT TAA: internal stops at codons 2 and 3.
        assert internal_stop_positions("ATGTAATAACGTTAA") == [2, 3]
        # The final codon is never internal, even when it is itself a stop.
        assert internal_stop_positions("ATGNNNTAA") == []
        # Ambiguity that resolves to a stop is reported (TAR is TAA or TAG,
        # both stops); genuine ambiguity is not (TAN could be a sense codon).
        assert internal_stop_positions("ATGTARACGTAA") == [2]
        assert internal_stop_positions("ATGTANACGTAA") == []
        assert internal_stop_positions("ATG") == []  # single codon: nothing internal
        assert internal_stop_positions("") == []

    def test_stop_codon_taxa(self):
        alignment = make_alignment([("t1", "ATGTAAACGT"), ("t2", "ATGCGTACGT")])
        assert stop_codon_taxa(alignment) == {"t1": [2]}

    def test_read_alignment_lenient_keeps_bad_data(self, tmp_path):
        fasta = tmp_path / "bad.fasta"
        fasta.write_text(">t1\nACGTQCGT\n>t1\nACGTACGT\n>t2\n\n", encoding="utf-8")
        # Auto-detection would read Q as an amino acid; a declared nucleotide
        # type makes Q illegal (exactly what check-data must report).
        alignment = read_alignment_lenient(fasta, data_type="nucleotide")
        assert alignment.taxa_names == ["t1", "t1", "t2"]
        assert duplicate_taxa(alignment) == ["t1"]
        assert empty_sequence_taxa(alignment) == ["t2"]
        assert "Q" in illegal_characters_by_taxon(alignment)["t1"]["chars"]

    def test_read_alignment_lenient_unknown_data_type(self, tmp_path):
        fasta = tmp_path / "codon.fasta"
        fasta.write_text(">t1\nATGAAA\n>t2\nATGCCC\n", encoding="utf-8")
        alignment = read_alignment_lenient(fasta, data_type="codon")
        assert alignment.data_type is DataType.NUCLEOTIDE

    def test_read_alignment_lenient_missing_and_empty(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_alignment_lenient(tmp_path / "nope.fasta")
        empty = tmp_path / "empty.fasta"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(AlignmentError):
            read_alignment_lenient(empty)


# ---------------------------------------------------------------------------
# partition.py inspectors
# ---------------------------------------------------------------------------


class TestPartitionInspectors:
    def test_resolve_partition_window_ok(self):
        alignment = make_alignment(CLEAN_PAIRS)
        window, issue = resolve_partition_window(alignment, 1, 12)
        assert window == (1, 12) and issue is None
        window, issue = resolve_partition_window(alignment, None, None)
        assert window == (1, 12) and issue is None

    def test_resolve_partition_window_bad(self):
        alignment = make_alignment(CLEAN_PAIRS)
        window, issue = resolve_partition_window(alignment, 5, 99)
        assert window is None and "exceeds" in issue
        window, issue = resolve_partition_window(alignment, 8, 3)
        assert window is None and issue
        window, issue = resolve_partition_window(alignment, 1, "x")
        assert window is None and issue

    def test_resolve_partition_window_ragged(self):
        alignment = make_alignment([("t1", "ACGT"), ("t2", "ACGTA")])
        window, issue = resolve_partition_window(alignment, 1, 4)
        assert window is None and "ragged" in issue

    def test_overlapping_window_pairs_adjacent_boundary(self):
        # Adjacent windows (to=10 / from=11) touch but do NOT overlap.
        adjacent = [("p1", 1, 10), ("p2", 11, 20)]
        assert overlapping_window_pairs(adjacent) == []
        # A single shared site IS an overlap.
        overlapping = [("p1", 1, 10), ("p2", 10, 20)]
        pairs = overlapping_window_pairs(overlapping)
        assert pairs == [(("p1", 1, 10), ("p2", 10, 20))]

    def test_overlapping_window_pairs_multiple(self):
        windows = [("a", 1, 5), ("b", 3, 8), ("c", 9, 12), ("d", 4, 6)]
        pairs = overlapping_window_pairs(windows)
        pair_ids = {tuple(sorted((x[0], y[0]))) for x, y in pairs}
        assert ("a", "b") in pair_ids
        assert ("a", "d") in pair_ids
        assert ("b", "d") in pair_ids
        assert ("b", "c") not in pair_ids

    def test_uncovered_site_ranges(self):
        assert uncovered_site_ranges([(1, 10), (11, 20)], 20) == []
        assert uncovered_site_ranges([(1, 10)], 20) == [(11, 20)]
        assert uncovered_site_ranges([(5, 10)], 20) == [(1, 4), (11, 20)]
        assert uncovered_site_ranges([(1, 5), (6, 10)], 10) == []  # touching merges
        assert uncovered_site_ranges([], 5) == [(1, 5)]
        assert uncovered_site_ranges([(1, 5)], 0) == []

    def test_taxon_set_mismatches(self):
        alignment_a = make_alignment(CLEAN_PAIRS, aid="a")
        alignment_b = make_alignment([("t1", "ACGT"), ("t2", "ACGA"), ("t5", "ACGC")], aid="b")
        p1 = Partition(
            id="p1",
            alignment=alignment_a,
            site_model=SiteModelConfig(substitution_model={"type": "jc69"}),
            clock_model=ClockModelConfig(),
        )
        p2 = Partition(
            id="p2",
            alignment=alignment_b,
            site_model=SiteModelConfig(substitution_model={"type": "jc69"}),
            clock_model=ClockModelConfig(),
        )
        mismatches = taxon_set_mismatches([p1, p2])
        assert mismatches == [("p1", "p2", ["t3", "t4"], ["t5"])]
        assert taxon_set_mismatches([p1]) == []


# ---------------------------------------------------------------------------
# calibration.py inspectors
# ---------------------------------------------------------------------------


class TestCalibrationInspectors:
    def test_missing_calibration_taxa(self):
        cals = [
            CalibrationPoint(name="c1", taxa=["t1", "ghost"]),
            CalibrationPoint(name="c2", taxa=["t1", "t2"]),
            CalibrationPoint(name="root", taxa=None),
        ]
        assert missing_calibration_taxa(cals, {"t1", "t2", "t3"}) == [("c1", ["ghost"])]

    def test_empty_taxa_calibrations(self):
        cals = [
            CalibrationPoint(name="c1", taxa=[]),
            CalibrationPoint(name="root", taxa=None),
            CalibrationPoint(name="c2", taxa=["t1"]),
        ]
        assert empty_taxa_calibrations(cals) == ["c1"]

    def test_calibration_age_interval_uniform(self):
        cal = CalibrationPoint(
            name="c1",
            taxa=["t1"],
            distribution=DistributionConfig(
                type="uniform", parameters={"lower": 10.0, "upper": 20.0}
            ),
        )
        low, high = calibration_age_interval(cal)
        assert low == pytest.approx(10.25)
        assert high == pytest.approx(19.75)

    def test_calibration_age_interval_normal(self):
        cal = CalibrationPoint(
            name="c1",
            taxa=["t1"],
            distribution=DistributionConfig(type="normal", parameters={"mean": 7.0, "stdev": 1.0}),
        )
        low, high = calibration_age_interval(cal)
        assert low is not None and low < 7.0
        assert high is not None and high > 7.0

    def test_calibration_age_interval_without_distribution(self):
        cal = CalibrationPoint(name="c1", taxa=["t1"], distribution=None)
        assert calibration_age_interval(cal) == (None, None)


# ---------------------------------------------------------------------------
# datacheck: severities, envelope, precheck API
# ---------------------------------------------------------------------------


class TestDatacheckAPI:
    def test_severity_table_matches_contract(self):
        """The 17 contract findings keep their fixed severities (never downgraded)."""
        for finding_id, severity in CONTRACT_TABLE.items():
            assert FINDING_SEVERITIES[finding_id] == severity
        # Extra internal ids are errors too.
        assert FINDING_SEVERITIES["config-parse-error"] == "error"
        assert FINDING_SEVERITIES["internal-error"] == "error"

    def test_check_config_envelope_shape(self):
        config = make_config(CLEAN_PAIRS)
        doc = check_config(config, config_path=Path("mem.yaml"))
        assert doc["schema_version"] == C.SCHEMA_DATACHECK
        assert set(doc) == {
            "schema_version",
            "generated_at",
            "tool_version",
            "config_path",
            "verdict",
            "counts",
            "findings",
        }
        assert doc["verdict"] == "pass"
        assert doc["counts"] == {"error": 0, "warning": 0, "info": 0}
        assert doc["findings"] == []
        assert doc["config_path"].endswith("mem.yaml")

    def test_check_config_finds_duplicate_taxon(self):
        config = make_config([("t1", "ACGTACGT"), ("t1", "ACGAACGT"), ("t2", "ACGTACGA")])
        doc = check_config(config)
        assert "duplicate-taxon" in ids(doc["findings"])
        finding = next(f for f in doc["findings"] if f["id"] == "duplicate-taxon")
        assert finding["severity"] == "error"
        assert finding["location"]["taxon"] == "t1"
        assert finding["fix"]
        assert doc["verdict"] == "fail"
        assert doc["counts"]["error"] >= 1

    def test_every_finding_has_fix_and_location(self):
        config = make_config(
            [
                ("t1", "ACGTQCGTACGT"),
                ("t1", "NNNNNNNNNNNN"),
                ("t3", "ACGTACGTACGT"),
                ("t4", ""),
            ]
        )
        doc = check_config(config)
        assert doc["findings"]
        for finding in doc["findings"]:
            assert finding["fix"], f"{finding['id']} has no fix"
            assert finding["location"], f"{finding['id']} has no location"

    def test_run_precheck_disabled_returns_empty(self):
        config = make_config([("t1", "ACGT"), ("t1", "ACGA")])
        assert run_precheck(config, enabled=False) == []

    def test_run_precheck_returns_findings_only(self):
        config = make_config([("t1", "ACGTACGT"), ("t1", "ACGAACGT"), ("t2", "ACGTACGA")])
        findings = run_precheck(config, config_path=Path("x.yaml"))
        assert isinstance(findings, list)
        assert "duplicate-taxon" in ids(findings)
        assert all("schema_version" not in item for item in findings)

    def test_findings_exit_code_mapping(self):
        assert findings_exit_code({"error": 0, "warning": 0, "info": 0}) == C.EXIT_CHECK_PASS
        assert findings_exit_code({"error": 0, "warning": 2, "info": 0}) == C.EXIT_CHECK_WARNING
        assert findings_exit_code({"error": 1, "warning": 5, "info": 0}) == C.EXIT_CHECK_ERROR
        doc = check_config(make_config(CLEAN_PAIRS))
        assert envelope_exit_code(doc) == C.EXIT_CHECK_PASS

    def test_internal_error_recorded_not_raised(self):
        def raiser(config, **kwargs):
            raise RuntimeError("boom")

        config = make_config(CLEAN_PAIRS)
        findings = collect_findings(config, checks=[("boom", raiser)])
        assert ids(findings) == ["internal-error"]
        assert findings[0]["severity"] == "error"
        assert "boom" in findings[0]["message"]
        assert "RuntimeError" in findings[0]["message"]
        assert findings[0]["fix"]

    def test_ambiguity_threshold_boundary(self):
        # t2 is 4/12 = 0.3333... ambiguous.
        pairs = list(CLEAN_PAIRS)
        pairs[1] = ("t2", "NNNNACGAACGT")
        config = make_config(pairs)
        exact = check_config(config, ambiguity_threshold=4.0 / 12.0)
        assert "high-ambiguity" not in ids(exact["findings"])  # strict > comparison
        below = check_config(config, ambiguity_threshold=0.25)
        assert "high-ambiguity" in ids(below["findings"])
        finding = next(f for f in below["findings"] if f["id"] == "high-ambiguity")
        assert finding["severity"] == "warning"
        assert finding["value"] == pytest.approx(0.3333, abs=1e-3)
        assert finding["threshold"] == 0.25

    def test_default_threshold_constant(self):
        assert DEFAULT_AMBIGUITY_THRESHOLD == 0.10

    @pytest.mark.parametrize("bad", [1.5, -0.1, "abc", float("nan")])
    def test_invalid_threshold_raises_contract_error(self, bad):
        config = make_config(CLEAN_PAIRS)
        with pytest.raises(C.ContractError):
            check_config(config, ambiguity_threshold=bad)


# ---------------------------------------------------------------------------
# datacheck: individual check semantics on hand-built configs
# ---------------------------------------------------------------------------


class TestCheckSemantics:
    def test_no_root_calibration_emits_nothing(self):
        config = make_config(
            CLEAN_PAIRS,
            tip_dates={"t1": 0.0, "t2": 10.0},
            calibrations=[
                CalibrationPoint(
                    name="c1",
                    taxa=["t1", "t2"],
                    distribution=DistributionConfig(
                        type="uniform", parameters={"lower": 1.0, "upper": 2.0}
                    ),
                )
            ],
        )
        assert "root-height-conflict" not in ids(collect_findings(config))

    def test_root_conflict_with_tip_dates(self):
        config = make_config(
            CLEAN_PAIRS,
            tip_dates={"t1": 0.0, "t2": 10.0},
            calibrations=[
                CalibrationPoint(
                    name="root_cal",
                    taxa=None,
                    distribution=DistributionConfig(
                        type="uniform", parameters={"lower": 1.0, "upper": 2.0}
                    ),
                )
            ],
        )
        findings = collect_findings(config)
        assert "root-height-conflict" in ids(findings)
        finding = next(f for f in findings if f["id"] == "root-height-conflict")
        assert finding["severity"] == "error"
        assert finding["location"]["against"] == "tip_dates"
        assert finding["value"]["tip_date_span"] == pytest.approx(10.0)

    def test_root_conflict_with_clade_calibration(self):
        config = make_config(
            CLEAN_PAIRS,
            calibrations=[
                CalibrationPoint(
                    name="root_cal",
                    taxa=None,
                    distribution=DistributionConfig(
                        type="uniform", parameters={"lower": 1.0, "upper": 2.0}
                    ),
                ),
                CalibrationPoint(
                    name="c1",
                    taxa=["t1", "t2"],
                    distribution=DistributionConfig(
                        type="uniform", parameters={"lower": 10.0, "upper": 20.0}
                    ),
                ),
            ],
        )
        findings = collect_findings(config)
        conflict = next(f for f in findings if f["id"] == "root-height-conflict")
        assert conflict["location"]["against"] == "c1"
        assert conflict["value"]["clade_age_min"] == pytest.approx(10.25)

    def test_root_conflict_absent_when_root_is_old_enough(self):
        config = make_config(
            CLEAN_PAIRS,
            tip_dates={"t1": 0.0, "t2": 10.0},
            calibrations=[
                CalibrationPoint(
                    name="root_cal",
                    taxa=None,
                    distribution=DistributionConfig(
                        type="uniform", parameters={"lower": 50.0, "upper": 100.0}
                    ),
                )
            ],
        )
        assert "root-height-conflict" not in ids(collect_findings(config))

    def test_no_empirical_tree_emits_nothing(self):
        config = make_config(CLEAN_PAIRS)
        assert "empirical-tree-taxa" not in ids(collect_findings(config))

    def test_empirical_tree_mismatch(self):
        config = make_config(CLEAN_PAIRS, newick="((t1,t2),(t3,WRONG));")
        findings = collect_findings(config)
        assert "empirical-tree-taxa" in ids(findings)
        finding = next(f for f in findings if f["id"] == "empirical-tree-taxa")
        assert finding["severity"] == "error"
        assert finding["value"]["missing_from_tree"] == ["t4"]
        assert finding["value"]["not_in_alignment"] == ["WRONG"]

    def test_empirical_tree_matching_emits_nothing(self):
        config = make_config(CLEAN_PAIRS, newick="((t1,t2),(t3,t4));")
        assert "empirical-tree-taxa" not in ids(collect_findings(config))

    def test_empirical_tree_unparsable(self):
        config = make_config(CLEAN_PAIRS, newick="((t1,t2;")
        findings = collect_findings(config)
        assert "empirical-tree-taxa" in ids(findings)

    def test_codon_checks_only_for_codon_data(self):
        # Nucleotide data with stops-in-frame never triggers codon findings.
        config = make_config([("t1", "ATGTAAACGTAA"), ("t2", "ATGCGTACGTAA")])
        findings = collect_findings(config)
        assert "codon-length" not in ids(findings)
        assert "codon-stop" not in ids(findings)

    def test_codon_checks_for_codon_site_model(self):
        # Length 10 is not a multiple of 3 and t1 carries an internal TAA.
        config = make_config([("t1", "ATGTAAACGT"), ("t2", "ATGCGTACGA")], site_model_type="gy94")
        findings = collect_findings(config)
        assert "codon-length" in ids(findings)  # 10 % 3 == 1
        assert "codon-stop" in ids(findings)

    def test_tip_date_format_error_on_raw_garbage(self):
        config = make_config(CLEAN_PAIRS)
        config.tip_dates = {"t1": "recent"}
        findings = collect_findings(config)
        assert "tip-date-format" in ids(findings)
        finding = next(f for f in findings if f["id"] == "tip-date-format")
        assert finding["severity"] == "error"
        assert finding["location"]["taxon"] == "t1"

    def test_tip_date_order_negative_backwards(self):
        config = make_config(
            CLEAN_PAIRS,
            tip_dates={
                "t1": {"value": -5.0, "direction": "backwards", "units": "years"},
                "t2": {"value": 10.0, "direction": "backwards", "units": "years"},
            },
        )
        findings = collect_findings(config)
        assert "tip-date-order" in ids(findings)
        finding = next(f for f in findings if f["id"] == "tip-date-order")
        assert finding["severity"] == "warning"

    def test_tip_date_order_all_identical(self):
        config = make_config(CLEAN_PAIRS, tip_dates={"t1": 5.0, "t2": 5.0, "t3": 5.0})
        findings = collect_findings(config)
        assert "tip-date-order" in ids(findings)

    def test_calibration_empty_taxa_finding(self):
        config = make_config(
            CLEAN_PAIRS,
            calibrations=[
                CalibrationPoint(
                    name="c1",
                    taxa=[],
                    distribution=DistributionConfig(
                        type="uniform", parameters={"lower": 1.0, "upper": 2.0}
                    ),
                )
            ],
        )
        findings = collect_findings(config)
        assert "calibration-empty-taxa" in ids(findings)

    def test_calibration_taxon_missing_finding(self):
        config = make_config(
            CLEAN_PAIRS,
            calibrations=[
                CalibrationPoint(
                    name="c1",
                    taxa=["t1", "ghost"],
                    distribution=DistributionConfig(
                        type="uniform", parameters={"lower": 1.0, "upper": 2.0}
                    ),
                )
            ],
        )
        findings = collect_findings(config)
        assert "calibration-taxon-missing" in ids(findings)
        finding = next(f for f in findings if f["id"] == "calibration-taxon-missing")
        assert finding["location"]["taxa"] == ["ghost"]

    def test_partition_gap_and_overlap_on_handbuilt_config(self):
        alignment = make_alignment(
            [("t1", "A" * 20), ("t2", "C" * 20), ("t3", "G" * 20)], aid="aln"
        )

        def windowed(start, end, pid):
            part_alignment = Alignment(
                id=alignment.id,
                data_type=alignment.data_type,
                sequences=alignment.sequences,
                source_file=None,
                from_pos=start,
                to_pos=end,
            )
            return Partition(
                id=pid,
                alignment=part_alignment,
                site_model=SiteModelConfig(substitution_model={"type": "jc69"}),
                clock_model=ClockModelConfig(),
            )

        config = make_config([], partitions=[windowed(1, 10, "p1"), windowed(10, 15, "p2")])
        config.all_alignments = [alignment]
        findings = collect_findings(config)
        assert "partition-overlap" in ids(findings)
        assert "partition-gap" in ids(findings)  # 16-20 uncovered
        gap = next(f for f in findings if f["id"] == "partition-gap")
        assert gap["location"]["sites"] == [16, 20]

    def test_taxon_name_with_whitespace_warns(self):
        # BEAST rewrites such labels in the tree log (the translator truncates
        # at the first space), so the warning names the affected taxon.
        pairs = list(CLEAN_PAIRS)
        pairs[0] = ("taxon one", "ACGTACGTACGT")
        findings = collect_findings(make_config(pairs))
        assert "taxon-name-format" in ids(findings)
        finding = next(f for f in findings if f["id"] == "taxon-name-format")
        assert finding["severity"] == "warning"
        assert finding["location"]["taxa"] == ["taxon one"]
        assert finding["fix"]

    def test_taxon_name_with_newick_delimiter_warns(self):
        pairs = list(CLEAN_PAIRS)
        pairs[1] = ("t2 (clone)", "ACGTACGAACGT")
        finding = next(
            f for f in collect_findings(make_config(pairs)) if f["id"] == "taxon-name-format"
        )
        assert finding["location"]["taxa"] == ["t2 (clone)"]

    def test_conventional_taxon_names_not_flagged(self):
        findings = collect_findings(make_config(CLEAN_PAIRS))
        assert "taxon-name-format" not in ids(findings)


# ---------------------------------------------------------------------------
# check_config_file: strict + lenient paths on the fixture set
# ---------------------------------------------------------------------------


class TestCheckConfigFile:
    def test_examples_basic_passes(self):
        doc = check_config_file(ROOT / "examples" / "config_basic.yaml")
        assert doc["verdict"] == "pass"
        assert doc["findings"] == []

    def test_examples_advanced_passes(self):
        doc = check_config_file(ROOT / "examples" / "config_advanced.yaml")
        assert doc["verdict"] == "pass"
        assert doc["findings"] == []

    def test_clean_fixture(self):
        doc = check_config_file(fixture_config("clean"))
        assert doc["verdict"] == "pass" and doc["counts"]["error"] == 0

    def test_adjacent_partitions_not_flagged(self):
        """Boundary: ranges that touch (to=10 / from=11) are not an overlap."""
        doc = check_config_file(fixture_config("adjacent"))
        assert "partition-overlap" not in ids(doc["findings"])
        assert doc["verdict"] == "pass"

    def test_lenient_path_reports_gate0_rejection_and_data_findings(self):
        doc = check_config_file(fixture_config("dup_taxon"))
        found = ids(doc["findings"])
        assert "config-parse-error" in found
        assert "duplicate-taxon" in found
        assert doc["verdict"] == "fail"
        assert doc["counts"]["error"] == 2
        taxon_finding = next(f for f in doc["findings"] if f["id"] == "duplicate-taxon")
        assert taxon_finding["location"]["taxon"] == "t2"
        assert taxon_finding["location"]["alignment"] == "aln1"
        assert taxon_finding["fix"]

    def test_lenient_path_illegal_character_location(self):
        doc = check_config_file(fixture_config("illegal_char"))
        finding = next(f for f in doc["findings"] if f["id"] == "illegal-character")
        assert finding["location"]["taxon"] == "t2"
        assert finding["location"]["sites"] == [5]
        assert finding["value"]["chars"] == ["Q"]

    def test_lenient_path_survives_missing_alignment_file(self):
        doc = check_config_file(fixture_config("missing_alignment"))
        assert ids(doc["findings"]) == ["config-parse-error"]
        assert doc["verdict"] == "fail"

    def test_missing_config_raises_contract_error(self):
        with pytest.raises(C.ContractError) as excinfo:
            check_config_file(FIXTURES / "clean" / "no_such_config.yaml")
        assert excinfo.value.fix

    def test_build_lenient_config_on_unreadable_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{{not: [valid: yaml", encoding="utf-8")
        assert build_lenient_config(bad) is None

    def test_build_lenient_config_shape(self):
        config = build_lenient_config(fixture_config("codon"))
        assert config is not None
        assert config.partitions and config.partitions[0].id == "p1"
        assert getattr(config.all_alignments[0], "bxp_codon", False) is True
        doc = check_config(config)
        found = ids(doc["findings"])
        assert "codon-length" in found and "codon-stop" in found

    def test_strict_path_root_conflict(self):
        doc = check_config_file(fixture_config("root_conflict_tips"))
        assert ids(doc["findings"]) == ["root-height-conflict"]
        assert doc["verdict"] == "fail"

    def test_strict_path_empirical_tree(self):
        doc = check_config_file(fixture_config("empirical_tree"))
        assert ids(doc["findings"]) == ["empirical-tree-taxa"]

    def test_warning_only_fixtures(self):
        for name in ("dup_seq", "high_ambiguity", "gap", "tip_order"):
            doc = check_config_file(fixture_config(name))
            assert doc["counts"]["error"] == 0, name
            assert doc["counts"]["warning"] >= 1, name
            assert doc["verdict"] == "warn", name


# ---------------------------------------------------------------------------
# CLI (subprocess)
# ---------------------------------------------------------------------------


class TestCheckDataCLI:
    def test_help(self):
        result = run_cli(["check-data", "--help"])
        assert result.returncode == 0
        assert "--ambiguity-threshold" in result.stdout
        assert "--json" in result.stdout
        assert "0 = pass" in result.stdout

    def test_registered_in_main_help(self):
        result = run_cli(["--help"])
        assert "check-data" in result.stdout

    def test_clean_exit_0(self):
        result = run_cli(["check-data", "--config", str(fixture_config("clean"))])
        assert result.returncode == C.EXIT_CHECK_PASS
        assert "verdict: pass" in result.stdout

    def test_examples_basic_exit_0(self):
        result = run_cli(["check-data", "--config", str(ROOT / "examples" / "config_basic.yaml")])
        assert result.returncode == C.EXIT_CHECK_PASS

    def test_warning_only_exit_1(self):
        result = run_cli(["check-data", "--config", str(fixture_config("dup_seq"))])
        assert result.returncode == C.EXIT_CHECK_WARNING
        assert "[warning] duplicate-sequence" in result.stdout
        assert "fix:" in result.stdout

    def test_error_exit_2(self):
        result = run_cli(["check-data", "--config", str(fixture_config("dup_taxon"))])
        assert result.returncode == C.EXIT_CHECK_ERROR
        assert "[error] duplicate-taxon" in result.stdout

    def test_errors_never_downgraded_to_warnings(self):
        """A fixture with BOTH findings keeps codon-length an error (exit 2)
        while codon-stop stays a warning."""
        result = run_cli(["check-data", "--config", str(fixture_config("codon"))])
        assert result.returncode == C.EXIT_CHECK_ERROR
        assert "[error] codon-length" in result.stdout
        assert "[warning] codon-stop" in result.stdout

    def test_nonexistent_config_clean_error(self):
        result = run_cli(["check-data", "--config", str(FIXTURES / "nope" / "config.yaml")])
        assert result.returncode != 0
        assert "error:" in result.stderr
        assert "next step:" in result.stderr

    def test_json_output(self, tmp_path):
        out = tmp_path / "datacheck.json"
        result = run_cli(
            [
                "check-data",
                "--config",
                str(fixture_config("dup_taxon")),
                "--json",
                str(out),
            ]
        )
        assert result.returncode == C.EXIT_CHECK_ERROR
        assert out.exists()
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert list(doc)[:3] == ["schema_version", "generated_at", "tool_version"]
        assert set(doc) == {
            "schema_version",
            "generated_at",
            "tool_version",
            "config_path",
            "verdict",
            "counts",
            "findings",
        }
        assert doc["schema_version"] == "beastxpy.datacheck/v1"
        finding = next(f for f in doc["findings"] if f["id"] == "duplicate-taxon")
        assert set(finding) >= {
            "id",
            "severity",
            "category",
            "message",
            "fix",
            "location",
        }
        assert finding["severity"] == "error"
        assert finding["location"]["taxon"] == "t2"
        assert finding["fix"]

    def test_quiet_suppresses_stdout(self):
        result = run_cli(["check-data", "--config", str(fixture_config("clean")), "-q"])
        assert result.returncode == C.EXIT_CHECK_PASS
        assert result.stdout == ""

    def test_ambiguity_threshold_option(self):
        # t2 in high_ambiguity is 33% ambiguous: threshold 0.5 silences it.
        result = run_cli(
            [
                "check-data",
                "--config",
                str(fixture_config("high_ambiguity")),
                "--ambiguity-threshold",
                "0.5",
            ]
        )
        assert result.returncode == C.EXIT_CHECK_PASS
        result = run_cli(
            [
                "check-data",
                "--config",
                str(fixture_config("high_ambiguity")),
                "--ambiguity-threshold",
                "0.01",
            ]
        )
        assert result.returncode == C.EXIT_CHECK_WARNING

    def test_invalid_threshold_reports_next_step(self):
        result = run_cli(
            [
                "check-data",
                "--config",
                str(fixture_config("clean")),
                "--ambiguity-threshold",
                "5",
            ]
        )
        assert result.returncode != 0
        assert "next step:" in result.stderr

    def test_stdout_is_console_safe(self, tmp_path):
        """Non-ASCII taxon names never crash a GBK console summary.

        The summary passes non-ASCII text through when the console can encode
        it and escapes only what the console cannot represent.  Simulate the
        worst common case (a GBK console) with ``PYTHONIOENCODING`` and assert
        the command still succeeds without an encode traceback.
        """
        work = tmp_path / "nonascii"
        work.mkdir()
        (work / "aln.fasta").write_text(
            ">taxon_\u4e2d\u6587\nACGTACGTACGT\n>taxon_\u00e9\nACGTACGTACGT\n"
            ">t3\nACGAACGTACGT\n>t4\nACGTACGTACGA\n",
            encoding="utf-8",
        )
        template = fixture_config("dup_seq").read_text(encoding="utf-8")
        (work / "config.yaml").write_text(template, encoding="utf-8")
        out = tmp_path / "na.json"
        env = dict(os.environ, PYTHONIOENCODING="gbk")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "beastxpy.main",
                "check-data",
                "--config",
                str(work / "config.yaml"),
                "--json",
                str(out),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            env=env,
            timeout=120,
        )
        assert result.returncode == C.EXIT_CHECK_WARNING
        assert "UnicodeEncodeError" not in result.stderr
        doc = json.loads(out.read_text(encoding="utf-8"))
        finding = next(f for f in doc["findings"] if f["id"] == "duplicate-sequence")
        assert "taxon_\u4e2d\u6587" in finding["location"]["taxa"]


# ---------------------------------------------------------------------------
# Gate 0 baseline: example configs still parse (append-only edits are inert)
# ---------------------------------------------------------------------------


class TestNoRegression:
    def test_examples_still_parse(self):
        parser = ConfigParser()
        parser.parse(str(ROOT / "examples" / "config_basic.yaml"))
        parser.parse(str(ROOT / "examples" / "config_advanced.yaml"))

    def test_check_config_never_raises_on_valid_config(self):
        parser = ConfigParser()
        for name in ("config_basic", "config_advanced", "config_tip_dates"):
            config = parser.parse(str(ROOT / "examples" / f"{name}.yaml"))
            doc = check_config(config)
            assert doc["verdict"] in ("pass", "warn", "fail")
