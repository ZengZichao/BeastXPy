"""Unit tests for tree tools (NEXUS parsing, clade statistics, MCC trees)."""

from __future__ import annotations

import pytest

from beastxpy.treetools import (
    NexusTree,
    clade_statistics,
    parse_nexus_trees,
)


def _make_nexus(trees_str, translate=None):
    """Build a BEAST-style NEXUS file content string."""
    lines = ["#NEXUS", "Begin trees;"]
    if translate:
        lines.append("  Translate")
        for num, name in translate.items():
            lines.append(f"    {num} {name},")
        lines.append("  ;")
    for i, t in enumerate(trees_str):
        lines.append(f"  Tree STATE_{i} = {t};")
    lines.append("End;")
    return "\n".join(lines)


class TestNexusParsing:
    """NEXUS tree file parsing."""

    def test_parse_simple_nexus(self, tmp_path):
        nexus = tmp_path / "trees.nexus"
        nexus.write_text(
            _make_nexus(
                [
                    "((A:1.0,B:1.0):0.5,C:2.0)",
                    "((A:0.8,B:1.2):0.6,C:1.9)",
                ]
            )
        )
        trees = parse_nexus_trees(str(nexus))
        assert len(trees) == 2
        assert all(isinstance(t, NexusTree) for t in trees)

    def test_trees_have_translation(self, tmp_path):
        nexus = tmp_path / "trees.nexus"
        nexus.write_text(
            _make_nexus(
                ["(A:1.0,B:1.0)"],
                translate={"1": "A", "2": "B"},
            )
        )
        trees = parse_nexus_trees(str(nexus))
        assert trees[0].labels == {"1": "A", "2": "B"}

    def test_missing_file(self):
        with pytest.raises(Exception):
            parse_nexus_trees("/nonexistent/file.nexus")


class TestCladeStatistics:
    """Clade support and height statistics."""

    def test_clade_statistics_basic(self, tmp_path):
        nexus = tmp_path / "trees.nexus"
        nexus.write_text(
            _make_nexus(
                [
                    "((A:1.0,B:1.0):0.5,C:2.0)",
                    "((A:1.0,B:1.0):0.5,C:2.0)",
                    "(A:1.0,(B:1.0,C:1.0):0.5)",
                ]
            )
        )
        trees = parse_nexus_trees(str(nexus))
        stats = clade_statistics(trees)
        assert stats is not None

    def test_clade_support(self, tmp_path):
        """Clade {A,B} should have 2/3 support in the trees above."""
        nexus = tmp_path / "trees.nexus"
        nexus.write_text(
            _make_nexus(
                [
                    "((A:1.0,B:1.0):0.5,C:2.0)",
                    "((A:1.0,B:1.0):0.5,C:2.0)",
                    "(A:1.0,(B:1.0,C:1.0):0.5)",
                ]
            )
        )
        trees = parse_nexus_trees(str(nexus))
        stats = clade_statistics(trees)
        assert stats is not None


class TestAnnotatedTreeLogs:
    """BEAST X writes [&lnP=...] between STATE_N and '='."""

    def test_inline_metadata_is_parsed(self, tmp_path):
        nexus = tmp_path / "trees.nexus"
        nexus.write_text(
            "#NEXUS\nBegin taxa;\nDimensions ntax=3;\nTaxlabels A B C;\nEnd;\n"
            "Begin trees;\n"
            "tree STATE_100 [&lnP=-12.5,branchRates=0.0,posterior=-12.5] "
            "= [&R] ((A:1.0,B:1.0):0.5,C:2.0);\n"
            "tree STATE_200 [&lnP=-11.5,branchRates=0.0,posterior=-11.5] "
            "= [&R] (A:1.0,(B:1.0,C:1.0):0.5);\n"
            "End;\n",
            encoding="utf-8",
        )
        trees = parse_nexus_trees(str(nexus))
        assert [tree.metadata["state"] for tree in trees] == ["100", "200"]
        assert trees[0].metadata["lnP"] == "-12.5"
        assert trees[0].metadata["rooted"] == "true"
        assert trees[0].newick.startswith("((A:1.0,B:1.0)")


def test_beast_tool_class_names_are_version_specific():
    from beastxpy.treetools import _log_combiner_class, _tree_annotator_class

    assert _tree_annotator_class("1.10.4") == "dr.app.tools.TreeAnnotator"
    assert _log_combiner_class("1.10.4") == "dr.app.tools.LogCombiner"
    assert _tree_annotator_class("10.5.0") == "dr.app.tools.treeannotator.TreeAnnotator"
    assert _log_combiner_class("10.5.0") == "dr.app.tools.logcombiner.LogCombiner"
