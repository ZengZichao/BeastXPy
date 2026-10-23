"""Unit tests for partition management."""

from __future__ import annotations

import pytest

from beastxpy.models import (
    ClockModelConfig,
    ClockModelType,
    Partition,
    SiteModelConfig,
)
from beastxpy.partition import collect_partitions, separate_clock_partitions, shared_clock


class TestPartitionCollection:
    """collect_partitions function."""

    def test_collect_single(self, simple_partition):
        result = collect_partitions([simple_partition])
        assert len(result) == 1
        assert result[0].id == "gene1"

    def test_collect_multiple(self, simple_partition, simple_alignment):
        p2 = Partition(
            id="gene2",
            alignment=simple_alignment,
            site_model=SiteModelConfig(
                substitution_model={"type": "hky"},
            ),
            clock_model=ClockModelConfig(type=ClockModelType.STRICT),
        )
        result = collect_partitions([simple_partition, p2])
        assert len(result) == 2

    def test_duplicate_alignment_id(self, simple_partition):
        """Two partitions sharing the same alignment should be allowed."""
        p2 = Partition(
            id="gene2",
            alignment=simple_partition.alignment,
            site_model=SiteModelConfig(substitution_model={"type": "hky"}),
            clock_model=ClockModelConfig(type=ClockModelType.STRICT),
        )
        result = collect_partitions([simple_partition, p2])
        assert len(result) == 2


class TestClockSharing:
    """Clock sharing logic."""

    def test_shared_clock(self, simple_partition):
        p2 = Partition(
            id="gene2",
            alignment=simple_partition.alignment,
            site_model=SiteModelConfig(substitution_model={"type": "hky"}),
            clock_model=ClockModelConfig(type=ClockModelType.STRICT),
        )
        parts = [simple_partition, p2]
        assert shared_clock(parts) is True

    def test_separate_clock(self, simple_partition):
        p2 = Partition(
            id="gene2",
            alignment=simple_partition.alignment,
            site_model=SiteModelConfig(substitution_model={"type": "hky"}),
            clock_model=ClockModelConfig(type=ClockModelType.STRICT),
            clock="separate",
        )
        parts = [simple_partition, p2]
        assert shared_clock(parts) is False
        assert len(separate_clock_partitions(parts)) == 1


class TestTaxaUnique:
    """unique_taxa function."""

    def test_unique_taxa_names(self, simple_partition):
        from beastxpy.partition import unique_taxa

        taxa = unique_taxa([simple_partition])
        names = [getattr(t, "taxon", t) for t in taxa]
        assert "A" in names
        assert "B" in names
        assert "C" in names
        assert "D" in names


class TestAlignmentSlices:
    """alignment_slices function."""

    def test_single_partition(self, simple_partition):
        from beastxpy.partition import alignment_slices

        slices = alignment_slices([simple_partition])
        assert len(slices) == 1
        assert slices[0].n_sites == simple_partition.alignment.n_sites


def test_single_partition_cannot_have_separate_clock(simple_partition):
    from beastxpy.partition import PartitionError

    lone = Partition(
        id=simple_partition.id,
        alignment=simple_partition.alignment,
        site_model=simple_partition.site_model,
        clock_model=simple_partition.clock_model,
        clock="separate",
    )
    with pytest.raises(PartitionError, match="at least two"):
        collect_partitions([lone])
