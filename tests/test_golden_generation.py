"""Golden XML generation tests.

Every example config is generated for both BEAST targets and compared with a
committed, comment-free, attribute-sorted golden document under
``tests/golden/<version>/``.  Any semantic change to the generator therefore
fails with a readable diff; the goldens can be refreshed explicitly
with ``BEASTX_UPDATE_GOLDEN=1 python -m pytest tests/test_golden_generation.py``.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as DT
from functools import lru_cache
from pathlib import Path

import pytest

from beastxpy.config import ConfigParser
from beastxpy.model import ModelAssembler
from beastxpy.utils import normalize_xml_for_compare
from beastxpy.validator import validate_xml_string
from beastxpy.xml_writer import write_xml

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
VERSIONS = ("1.10.4", "10.5.0")

CONFIGS = (
    "config_aa_jtt.yaml",
    "config_aa_wag.yaml",
    "config_advanced.yaml",
    "config_basic.yaml",
    "config_calibrated_yule.yaml",
    "config_coalescent_exponential.yaml",
    "config_ebsp.yaml",
    "config_multi_partition.yaml",
    "config_relaxed_clock.yaml",
    "config_relaxed_exponential.yaml",
    "config_rlc.yaml",
    "config_skyline.yaml",
    "config_subst_gtr.yaml",
    "config_subst_jc69.yaml",
    "config_subst_tn93.yaml",
    "config_tip_dates.yaml",
)


def _golden_path(config_name: str, version: str) -> Path:
    return GOLDEN_DIR / version / f"{Path(config_name).stem}.xml"


def _strip_formatting(node: ET.Element) -> None:
    """Drop whitespace-only text/tails so indentation is not semantic."""
    if node.text is not None:
        node.text = node.text.strip() or None
    for child in node:
        _strip_formatting(child)
        if child.tail is not None:
            child.tail = child.tail.strip() or None


def _canonical(xml_text: str) -> str:
    """Return a stable, human-readable golden representation.

    Comments (including version/fingerprint comments) are removed by
    ``normalize_xml_for_compare``; attributes are sorted and numeric values
    normalized there.  Whitespace-only formatting is then discarded and the
    tree is re-indented, so a golden diff shows semantic changes only.
    """
    root = DT.fromstring(normalize_xml_for_compare(xml_text))
    _strip_formatting(root)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


@lru_cache(maxsize=None)
def _normalized(config_name: str, version: str) -> str:
    """Generate one config for one BEAST target and canonicalize it."""
    config_path = Path(__file__).resolve().parent.parent / "examples" / config_name
    config = ConfigParser().parse(str(config_path))
    config.control.beast_version = version
    tree = ModelAssembler(config).assemble()
    xml = write_xml(tree, beast_version=version)
    DT.fromstring(xml)  # must be well formed before normalization
    return _canonical(xml)


def _diff(expected: str, actual: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile="golden",
            tofile="generated",
        )
    )


class TestGoldenGeneration:
    """Generate XML for every example config and compare with the goldens."""

    @pytest.mark.parametrize("version", VERSIONS)
    @pytest.mark.parametrize("config_name", CONFIGS)
    def test_golden_matches(self, config_name, version):
        golden_path = _golden_path(config_name, version)
        actual = _normalized(config_name, version)
        if os.environ.get("BEASTX_UPDATE_GOLDEN") == "1":
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            with golden_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(actual)
        assert (
            golden_path.is_file()
        ), f"missing golden {golden_path}; run with BEASTX_UPDATE_GOLDEN=1 to create it"
        expected = golden_path.read_text(encoding="utf-8")
        assert actual == expected, (
            f"generator drift for {config_name} ({version}); "
            "review the diff and refresh goldens only for intended changes\n"
            + _diff(expected, actual)
        )

    @pytest.mark.parametrize("config_name", CONFIGS)
    def test_both_versions_have_the_same_model_content(self, config_name):
        """The current generator emits version-independent model XML.

        Fingerprint metadata and version-gated features are outside this
        model-level comparison; if version-specific model output is added,
        update this assertion deliberately.
        """
        assert _normalized(config_name, "1.10.4") == _normalized(config_name, "10.5.0")

    @pytest.mark.parametrize("version", VERSIONS)
    def test_goldens_are_normalized(self, version):
        directory = GOLDEN_DIR / version
        names = {path.name for path in directory.glob("*.xml")}
        expected = {f"{Path(name).stem}.xml" for name in CONFIGS}
        assert names == expected, f"unexpected golden set in {directory}: {names ^ expected}"
        for name in sorted(expected):
            text = (directory / name).read_text(encoding="utf-8")
            assert _canonical(text) == text, f"{name} is not canonical"
            DT.fromstring(text)

    def test_normalized_output(self, config_basic):
        """Normalized XML should be stable across repeated generation."""
        asm = ModelAssembler(config_basic)
        xml1 = write_xml(asm.assemble())

        asm2 = ModelAssembler(config_basic)
        xml2 = write_xml(asm2.assemble())
        assert normalize_xml_for_compare(xml1) == normalize_xml_for_compare(xml2)

    def test_no_version_attribute(self, config_basic):
        """BEAST XML must not carry a version attribute on <beast>."""
        asm = ModelAssembler(config_basic)
        xml = write_xml(asm.assemble())
        # The XML declaration may have version="1.0", but the <beast> element
        # must NOT have a version attribute.
        import re

        beast_open = re.search(r"<beast([^>]*)>", xml)
        assert beast_open is not None
        attrs = beast_open.group(1)
        assert "version" not in attrs

    def test_all_examples_have_operators_and_priors(self, examples_dir):
        """Every example config produces XML with operators and priors."""
        parser = ConfigParser()
        for cfg_path in sorted(examples_dir.glob("config_*.yaml")):
            cfg = parser.parse(str(cfg_path))
            asm = ModelAssembler(cfg)
            xml = write_xml(asm.assemble())
            assert '<operators id="operators">' in xml
            assert '<prior id="prior">' in xml

    @pytest.mark.parametrize("config_name", CONFIGS)
    def test_generated_xml_passes_gate1(self, config_name, examples_dir):
        config = ConfigParser().parse(str(examples_dir / config_name))
        xml = write_xml(ModelAssembler(config).assemble())
        findings = validate_xml_string(xml)
        errors = [finding for finding in findings if finding.severity == "error"]
        assert not errors, f"{config_name}: Gate-1 errors: {errors}"
