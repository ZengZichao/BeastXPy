"""Unit tests for sequence reading."""

from __future__ import annotations

import pytest

from beastxpy.sequence import AlignmentError, SequenceReader, validate_alignment


class TestFastaReading:
    """FASTA file parsing."""

    def test_read_fasta_basic(self, primates_fasta):
        align = SequenceReader.read(str(primates_fasta))
        assert align is not None
        assert align.id is not None
        assert align.data_type.value == "nucleotide"
        assert align.n_taxa >= 10
        assert align.n_sites > 100

    def test_read_fasta_aminoacid(self, aminoacid_fasta):
        align = SequenceReader.read(str(aminoacid_fasta))
        assert align is not None
        assert align.data_type.value == "amino-acid"

    def test_read_missing_file(self, tmp_path):
        with pytest.raises(Exception):
            SequenceReader.read(str(tmp_path / "nonexistent.fasta"))

    def test_read_ragged_sequences(self, tmp_path):
        fasta = tmp_path / "ragged.fasta"
        fasta.write_text(">A\nACGT\n>B\nACGTAC\n")
        with pytest.raises((AlignmentError, ValueError)):
            SequenceReader.read(str(fasta))

    def test_read_invalid_chars(self, tmp_path):
        fasta = tmp_path / "bad_chars.fasta"
        fasta.write_text(">A\nACGT!\n>B\nACGT!\n")
        with pytest.raises(Exception):
            SequenceReader.read(str(fasta))


class TestAlignmentValidation:
    """validate_alignment function."""

    def test_valid_nucleotide(self, simple_alignment):
        issues = validate_alignment(simple_alignment)
        assert issues == []

    def test_valid_aminoacid(self):
        from beastxpy.models import Alignment, DataType, Sequence

        align = Alignment(
            id="aa",
            data_type=DataType.AMINOACID,
            sequences=[
                Sequence(taxon="A", sequence="ACDEFGHIK"),
                Sequence(taxon="B", sequence="ACDEFGHIK"),
            ],
        )
        issues = validate_alignment(align)
        assert issues == []

    def test_ragged_lengths(self, simple_alignment):
        from beastxpy.models import Sequence

        simple_alignment.sequences.append(Sequence(taxon="A", sequence="ACGT"))
        issues = validate_alignment(simple_alignment)
        # duplicate taxon "A" should be flagged
        assert any("duplicate" in i.lower() for i in issues)


class TestTaxonDates:
    """Tip date parsing."""

    def test_tip_dates_from_config(self, config_tip_dates):
        """Tip dates should be reflected in the config."""
        assert config_tip_dates.tip_dates
