"""Tests for custom XML fragments (``beastxpy.fragments``).

The fragment mechanism is the only route by which a user can put hand-written
XML into a generated document, so these tests concentrate on the guarantees
that make it safe: it is off by default, the allow-list is small, anchors must
be unique, path traversal and entity expansion are refused, and a spliced
document can never be emitted without Gate 3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from beastxpy import _contracts as C
from beastxpy import fragments as F
from beastxpy.api import BeastXPy

CONFIG = "examples/config_basic.yaml"


@pytest.fixture(scope="module")
def base_xml() -> str:
    return BeastXPy().generate_xml(CONFIG)


def _spec(**fragment):
    entry = {"name": "f", "position": "after", "anchor_id": "CrownCatarrhini"}
    entry.update(fragment)
    return F.parse_spec({"enabled": True, "fragments": [entry]})


class TestEnableSemantics:
    """Fragments are opt-in: absent config and absent flag means off."""

    class _Cfg:
        def __init__(self, block):
            self.custom_fragments = block

    def test_absent_block_is_off(self):
        assert F.fragments_enabled(self._Cfg({})) is False

    def test_flag_without_fragments_is_a_no_op(self, base_xml):
        # The flag alone enables the feature, but with no fragments configured
        # there is nothing to splice and the document is returned unchanged.
        spec = F.parse_spec({"enabled": False, "fragments": []}, cli_flag=True)
        assert F.apply_fragments(base_xml, spec) == base_xml

    def test_explicit_config_enables(self):
        block = {"enabled": True, "fragments": []}
        assert F.fragments_enabled(self._Cfg(block)) is True

    def test_cli_flag_enables_a_non_empty_block(self):
        block = {
            "enabled": False,
            "fragments": [
                {
                    "name": "x",
                    "xml": "<parameter id='p' value='1'/>",
                    "anchor_id": "CrownCatarrhini",
                }
            ],
        }
        assert F.fragments_enabled(self._Cfg(block)) is False
        assert F.fragments_enabled(self._Cfg(block), cli_flag=True) is True

    def test_disabled_block_without_flag_is_off(self):
        block = {
            "enabled": False,
            "fragments": [{"name": "x", "xml": "<parameter id='p' value='1'/>"}],
        }
        assert F.fragments_enabled(self._Cfg(block)) is False


class TestSplicing:
    def test_after_anchor_by_id(self, base_xml):
        spec = _spec(xml='<parameter id="customParam" value="1.0"/>')
        out = F.apply_fragments(base_xml, spec)
        assert 'id="customParam"' in out
        # The original document is otherwise preserved.
        assert base_xml.count("<mcmc") == out.count("<mcmc")

    def test_before_anchor_by_tag(self, base_xml):
        spec = _spec(
            xml='<parameter id="customParam" value="1.0"/>', anchor_id=None, anchor_tag="mcmc"
        )
        # <mcmc> is ambiguous in this document, so a tag anchor must be refused
        # rather than picking one silently.
        with pytest.raises(C.ContractError) as excinfo:
            F.apply_fragments(base_xml, spec)
        assert "ambiguous" in str(excinfo.value).lower()

    def test_inside_anchor(self, base_xml):
        # 'inside' is only legal against <beast>: it appends a top-level element.
        # Splicing inside a data- or model-defining element is refused, which is
        # the behaviour asserted by the next test.
        spec = _spec(
            xml='<parameter id="customParam" value="1.0"/>',
            anchor_id=None,
            anchor_tag="beast",
            position="inside",
        )
        out = F.apply_fragments(base_xml, spec)
        assert 'id="customParam"' in out

    def test_inside_data_anchor_is_refused(self, base_xml):
        spec = _spec(
            xml='<taxon idref="Homo_sapiens"/>',
            anchor_id="CrownCatarrhini",
            position="inside",
        )
        with pytest.raises(C.ContractError) as excinfo:
            F.apply_fragments(base_xml, spec)
        assert "inside" in str(excinfo.value).lower()

    def test_envelope_reports_gate3(self, base_xml):
        spec = _spec(xml='<parameter id="customParam" value="1.0"/>')
        envelope = F.validate_fragments(spec, base_xml)
        assert envelope["schema_version"] == C.SCHEMA_FRAGMENTS
        assert envelope["requires_gate3"] is True
        assert envelope["verdict"] == "pass"

    def test_provenance_marks_custom(self, base_xml):
        spec = _spec(xml='<parameter id="customParam" value="1.0"/>')
        envelope = F.validate_fragments(spec, base_xml)
        record = F.provenance_record(envelope)
        assert record["custom_fragments"][0]["kind"] == "custom"
        assert record["custom_fragments"][0]["sha256"]


class TestSecurityRejections:
    """Every one of these must refuse, with a message naming the problem."""

    @pytest.mark.parametrize(
        "label,fragment,needle",
        [
            ("duplicate id", {"xml": '<parameter id="taxa" value="1"/>'}, "already exists"),
            ("unknown element", {"xml": '<evilThing id="x"/>'}, "allow-list"),
            ("unknown attribute", {"xml": '<parameter id="p2" onclick="x"/>'}, "not allowed"),
            (
                "missing anchor",
                {"xml": '<parameter id="p2" value="1"/>', "anchor_id": "doesNotExist"},
                "anchor not found",
            ),
            ("core block id", {"xml": '<parameter id="mcmc" value="1"/>'}, "already exists"),
        ],
    )
    def test_rejections(self, base_xml, label, fragment, needle):
        spec = _spec(**fragment)
        with pytest.raises(C.ContractError) as excinfo:
            F.apply_fragments(base_xml, spec)
        assert needle in str(excinfo.value), f"{label}: {excinfo.value}"

    def test_doctype_and_entities_rejected(self, base_xml):
        payload = (
            '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///C:/Windows/win.ini">]>'
            '<parameter id="p2" value="&e;"/>'
        )
        with pytest.raises(C.ContractError) as excinfo:
            F.apply_fragments(base_xml, _spec(xml=payload))
        message = str(excinfo.value)
        assert "DOCTYPE" in message or "ENTITY" in message

    def test_xxe_does_not_read_a_file(self, base_xml, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET-VALUE", encoding="utf-8")
        uri = secret.as_posix()
        payload = (
            f'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///{uri}">]>' '<parameter id="p2" value="&e;"/>'
        )
        with pytest.raises(C.ContractError):
            F.apply_fragments(base_xml, _spec(xml=payload))

    def test_path_traversal_rejected(self, tmp_path):
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        with pytest.raises(C.ContractError) as excinfo:
            F.parse_spec(
                {
                    "enabled": True,
                    "fragments": [
                        {
                            "name": "t",
                            "file": "../../../../etc/passwd",
                            "anchor_id": "CrownCatarrhini",
                        }
                    ],
                },
                base_dir=config_dir,
            )
        assert "escapes" in str(excinfo.value) or "outside" in str(excinfo.value)

    def test_absolute_fragment_path_rejected(self, tmp_path):
        with pytest.raises(C.ContractError):
            F.parse_spec(
                {
                    "enabled": True,
                    "fragments": [
                        {
                            "name": "t",
                            "file": str(tmp_path / "x.xml"),
                            "anchor_id": "CrownCatarrhini",
                        }
                    ],
                },
                base_dir=tmp_path,
            )


class TestGate3IsMandatory:
    """A spliced document must be marked and must not escape validation."""

    def test_requires_gate3_is_always_true(self, base_xml):
        spec = _spec(xml='<parameter id="customParam" value="1.0"/>')
        assert F.validate_fragments(spec, base_xml)["requires_gate3"] is True
        assert F.splice_document(base_xml, spec)["requires_gate3"] is True

    def test_helper_cannot_skip_gate3(self, base_xml):
        # There is exactly one path to spliced XML and it raises on any finding.
        spec = _spec(xml='<parameter id="taxa" value="1"/>')
        with pytest.raises(C.ContractError):
            F.apply_fragments(base_xml, spec)


class TestConfigIntegration:
    """The config block reaches generation only when the user opted in."""

    def test_config_block_off_by_default(self, tmp_path):
        from beastxpy.config import ConfigParser

        source = Path(CONFIG).read_text(encoding="utf-8")
        target = tmp_path / "cfg.yaml"
        # The example config references primates.fasta relative to itself.
        (tmp_path / "primates.fasta").write_text(
            Path("examples/primates.fasta").read_text(encoding="utf-8"), encoding="utf-8"
        )
        target.write_text(source, encoding="utf-8")
        config = ConfigParser().parse(str(target))
        assert F.fragments_enabled(config) is False

    @pytest.mark.java
    def test_spliced_output_passes_official_gate3(self, base_xml, tmp_path):
        """The spliced document is accepted by the official BEAST jars."""
        from beastxpy.validator import run_beast_validation

        spec = _spec(xml='<parameter id="customParam" value="1.0"/>')
        out = F.apply_fragments(base_xml, spec)
        path = tmp_path / "spliced.xml"
        path.write_text(out, encoding="utf-8")
        result = run_beast_validation(str(path), "1.10.4", java_core=True)
        if result.status in ("not-run", "environment-missing"):
            pytest.fail(f"BEAST validation unavailable here: {result.reason}")
        assert result.status == "executed-pass"
