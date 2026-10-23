"""Partition management and Gate-0 partition validation for BeastXPy.

BEAST 1 supports one linked tree shared across all partitions. Partitions may
share one clock (default) or each declare an independent clock (``clock:
separate``, which requires ``tree: shared``). Partition ids namespace all
parameter and object ids emitted for that partition.

This module is the validation layer used by :mod:`beastxpy.config`
: unique ids, coordinate windows, non-overlapping ranges on the same
alignment and a shared taxon set across partitions.  The alignment stored on a
partition is always the *complete* alignment; ``from_pos``/``to_pos`` describe
the selected window.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .models import Alignment, Partition


class PartitionError(ValueError):
    """Raised when partition constraints are violated."""


def validate_partition_range(
    alignment: Alignment,
    from_pos: Optional[int],
    to_pos: Optional[int],
    partition_id: Optional[str] = None,
) -> Tuple[int, int]:
    """Validate a 1-based inclusive ``[from, to]`` partition window.

    Both coordinates must be supplied together and must satisfy
    ``1 <= from <= to <= alignment.n_sites``.  Raises :class:`PartitionError`
    (never a raw ``TypeError``/negative-index slice), per.
    """
    label = f"partition {partition_id!r}" if partition_id else "partition"
    try:
        return alignment.validate_range(from_pos, to_pos)
    except ValueError as exc:
        raise PartitionError(f"{label}: {exc}") from exc


def check_partition_ranges(partitions: List[Partition]) -> None:
    """Reject overlapping windows on the same alignment.

    A partition without ``from_pos``/``to_pos`` selects the whole alignment,
    so two full-length partitions on one alignment overlap.  Gaps between
    windows are allowed (BEAST ``<patterns from=... to=...>`` supports them),
    overlaps are not.
    """
    by_alignment: Dict[str, List[Tuple[int, int, str]]] = {}
    for p in partitions:
        alignment = p.alignment
        try:
            start, end = alignment.validate_range(alignment.from_pos, alignment.to_pos)
        except ValueError as exc:
            raise PartitionError(f"partition {p.id!r}: {exc}") from exc
        by_alignment.setdefault(alignment.id, []).append((start, end, p.id))
    for alignment_id, ranges in by_alignment.items():
        ordered = sorted(ranges)
        for (start_a, end_a, id_a), (start_b, end_b, id_b) in zip(ordered, ordered[1:]):
            if start_b <= end_a:
                raise PartitionError(
                    f"alignment {alignment_id!r}: partition ranges overlap: "
                    f"{id_a} = {start_a}-{end_a}, {id_b} = {start_b}-{end_b}; "
                    "split the alignment into disjoint 1-based [from, to] windows"
                )


def check_shared_taxon_sets(partitions: List[Partition]) -> None:
    """Require every partition to expose the same taxon set.

    The generated XML has one global ``<taxa>`` set and one linked tree, so a
    partition whose alignment omits (or adds) a taxon makes the tree and the
    alignment disagree.
    """
    if not partitions:
        return
    reference = frozenset(partitions[0].alignment.taxa_names)
    for p in partitions[1:]:
        current = frozenset(p.alignment.taxa_names)
        if current != reference:
            only_ref = sorted(reference - current)
            only_current = sorted(current - reference)
            raise PartitionError(
                f"partition {p.id!r} taxon set differs from partition "
                f"{partitions[0].id!r}: missing {only_ref}, extra {only_current}. "
                "All partitions share one linked tree and must use the same taxa."
            )


def collect_partitions(partitions: List[Partition]) -> List[Partition]:
    """Validate and return partitions.

    Checks:
      - non-empty;
      - unique partition ids;
      - ``tree`` is always ``shared`` (rejected upstream, defended here);
      - ``clock`` is ``shared``/``separate`` and separate clocks only exist
        on a shared tree;
      - every partition uses the same taxon set (one linked tree).

    Window overlap is checked separately by :func:`check_partition_ranges`,
    which the config parser always runs.
    """
    if not partitions:
        raise PartitionError("at least one partition is required")
    ids = [p.id for p in partitions]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise PartitionError(f"duplicate partition id(s): {duplicates}")
    for p in partitions:
        if p.tree != "shared":
            raise PartitionError(
                f"partition {p.id!r}: tree: {p.tree!r} is not supported; "
                "a single linked tree is shared by all partitions"
            )
        if p.clock not in ("shared", "separate"):
            raise PartitionError(
                f"partition {p.id!r}: clock: invalid {p.clock!r} " "(valid: shared, separate)"
            )
        if p.clock == "separate" and p.tree != "shared":
            raise PartitionError(f"partition {p.id!r}: clock: separate requires tree: shared")
        if p.clock == "separate" and len(partitions) < 2:
            raise PartitionError(
                f"partition {p.id!r}: clock: separate needs at least two "
                "partitions; a lone partition cannot be separated from anything"
            )
    check_shared_taxon_sets(partitions)
    return partitions


def shared_clock(partitions: List[Partition]) -> bool:
    """Whether all partitions share one clock object."""
    return all(p.clock == "shared" for p in partitions)


def separate_clock_partitions(partitions: List[Partition]) -> List[Partition]:
    """Return the partitions that each need an independent clock."""
    return [p for p in partitions if p.clock == "separate"]


def unique_taxa(partitions: List[Partition]) -> list:
    """Return the union of taxa across all partition alignments, in order."""
    seen: dict = {}
    for p in partitions:
        for seq in p.alignment.sequences:
            seen.setdefault(seq.taxon, seq)
    return list(seen.values())


def alignment_slices(partitions: List[Partition]) -> List[Alignment]:
    """Return each partition's alignment, carrying its ``from/to`` window.

    The sequences are complete; use ``alignment.from_pos``/``to_pos`` (or
    ``alignment.window_length``) for the selected sites.  Kept under this name
    for backwards compatibility; :func:`partition_alignments` is the clearer
    alias.
    """
    return [p.alignment for p in partitions]


def partition_alignments(partitions: List[Partition]) -> List[Alignment]:
    """Alias of :func:`alignment_slices` with an accurate name."""
    return alignment_slices(partitions)


# ---------------------------------------------------------------------------
# Read-only data-check inspectors (F2 ``check-data``; append-only additions)
# ---------------------------------------------------------------------------
#
# Non-raising variants of the Gate-0 validators above: they describe every
# window problem instead of aborting on the first, so ``beastxpy.datacheck``
# can report findings with exact sites.


def resolve_partition_window(
    alignment: Alignment,
    from_pos: Optional[int],
    to_pos: Optional[int],
) -> Tuple[Optional[Tuple[int, int]], Optional[str]]:
    """Resolve a 1-based inclusive partition window without raising.

    Returns ``(window, issue)``.  On success ``window`` is the effective
    ``(start, end)`` (the whole alignment when both coordinates are None) and
    ``issue`` is None.  When the window cannot be resolved, ``window`` is None
    and ``issue`` is the message :func:`validate_partition_range` would have
    raised (including the ragged-alignment case, where no site count exists).
    """
    try:
        return alignment.validate_range(from_pos, to_pos), None
    except ValueError as exc:
        return None, str(exc)


def overlapping_window_pairs(
    windows: List[Tuple[str, int, int]],
) -> List[Tuple[Tuple[str, int, int], Tuple[str, int, int]]]:
    """Pairs of ``(partition_id, start, end)`` windows sharing at least one site.

    Windows are 1-based inclusive; adjacent windows (``end_a + 1 == start_b``)
    do NOT overlap.  Each conflicting pair is returned once, ordered by
    ``(start, end, id)``.
    """
    ordered = sorted(windows, key=lambda item: (item[1], item[2], item[0]))
    pairs: List[Tuple[Tuple[str, int, int], Tuple[str, int, int]]] = []
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            if second[1] > first[2]:
                break  # starts are sorted: no later window can overlap ``first``
            pairs.append((first, second))
    return pairs


def uncovered_site_ranges(windows: List[Tuple[int, int]], n_sites: int) -> List[Tuple[int, int]]:
    """Contiguous 1-based ``(start, end)`` site ranges covered by no window.

    ``windows`` are ``(start, end)`` inclusive pairs; parts outside
    ``1..n_sites`` are clipped.  Touching/overlapping windows merge, so a gap
    is reported only for sites that truly belong to no window.
    """
    if n_sites <= 0:
        return []
    covered: List[Tuple[int, int]] = []
    for start, end in sorted(windows):
        start = max(1, int(start))
        end = min(int(end), n_sites)
        if start > end:
            continue
        if covered and start <= covered[-1][1] + 1:
            covered[-1] = (covered[-1][0], max(covered[-1][1], end))
        else:
            covered.append((start, end))
    gaps: List[Tuple[int, int]] = []
    cursor = 1
    for start, end in covered:
        if start > cursor:
            gaps.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= n_sites:
        gaps.append((cursor, n_sites))
    return gaps


def taxon_set_mismatches(
    partitions: List[Partition],
) -> List[Tuple[str, str, List[str], List[str]]]:
    """Partitions whose taxon set differs from the first partition's.

    Returns ``(reference_id, partition_id, missing, extra)`` tuples: ``missing``
    are taxa present only in the reference partition, ``extra`` taxa present
    only in the compared one.  Read-only variant of
    :func:`check_shared_taxon_sets` that reports *every* mismatch instead of
    raising on the first.
    """
    if len(partitions) < 2:
        return []
    reference = partitions[0]
    reference_set = frozenset(reference.alignment.taxa_names)
    mismatches: List[Tuple[str, str, List[str], List[str]]] = []
    for partition in partitions[1:]:
        current = frozenset(partition.alignment.taxa_names)
        if current != reference_set:
            mismatches.append(
                (
                    reference.id,
                    partition.id,
                    sorted(reference_set - current),
                    sorted(current - reference_set),
                )
            )
    return mismatches
