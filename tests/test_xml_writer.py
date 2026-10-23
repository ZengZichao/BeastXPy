"""Unit tests for XML writer."""

from __future__ import annotations

import pytest

from beastxpy.model import ModelAssembler
from beastxpy.xml_writer import XMLWriterError, XmlWriter, write_xml


class TestXmlWriter:
    """XmlWriter tests."""

    def test_write_simple(self):
        writer = XmlWriter()
        xml = writer.write({"tag": "beast"})
        assert "<beast" in xml
        assert "</beast>" in xml or "<beast/>" in xml

    def test_write_with_children(self):
        writer = XmlWriter()
        node = {
            "tag": "beast",
            "children": [
                {"tag": "mcmc", "attrib": {"id": "mcmc", "chainLength": "1000"}},
                {"tag": "operators"},
            ],
        }
        xml = writer.write(node)
        assert '<mcmc id="mcmc" chainLength="1000"' in xml
        assert "<operators" in xml
        assert "</beast>" in xml

    def test_write_with_text(self):
        writer = XmlWriter()
        node = {"tag": "beast", "children": [{"tag": "newick", "text": "(A:1,B:2);"}]}
        xml = writer.write(node)
        assert "(A:1,B:2);" in xml

    def test_write_self_closing(self):
        writer = XmlWriter()
        node = {"tag": "beast", "children": [{"tag": "empty", "attrib": {"id": "x"}}]}
        xml = writer.write(node)
        assert '<empty id="x"/>' in xml

    def test_write_comment(self):
        writer = XmlWriter()
        node = {"tag": "beast", "comment": "this is a comment"}
        xml = writer.write(node)
        assert "this is a comment" in xml


class TestXmlWriterInvariants:
    """Writer-level invariant checks."""

    def test_generated_xml_valid(self, config_basic):
        asm = ModelAssembler(config_basic)
        xml = write_xml(asm.assemble())
        # Should be well-formed XML
        import xml.etree.ElementTree as ET

        ET.fromstring(xml)

    def test_generated_xml_all_examples(self, examples_dir):
        """All example configs should generate well-formed XML."""
        import glob
        import xml.etree.ElementTree as ET

        from beastxpy.config import ConfigParser

        parser = ConfigParser()
        configs = sorted(glob.glob(str(examples_dir / "config_*.yaml")))
        assert len(configs) > 0
        for cfg_path in configs:
            cfg = parser.parse(cfg_path)
            asm = ModelAssembler(cfg)
            tree = asm.assemble()
            xml = write_xml(tree)
            ET.fromstring(xml)


class TestXMLWriterErrors:
    """Error cases."""

    def test_missing_tag(self):
        writer = XmlWriter()
        with pytest.raises(XMLWriterError):
            writer.write({"children": []})
