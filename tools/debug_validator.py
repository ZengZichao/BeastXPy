#!/usr/bin/env python
"""Debug why treeLikelihood children aren't found by the validator."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy.api import BeastXPy  # noqa: E402
from beastxpy.model import ModelAssembler  # noqa: E402
from beastxpy.reproducibility import fingerprint_comment  # noqa: E402
from beastxpy.xml_writer import write_xml  # noqa: E402

import defusedxml.ElementTree as DET

bxp = BeastXPy(force=True)
config = bxp.parse_config("examples/config_basic.yaml")
root = ModelAssembler(config).assemble()
xml_str = write_xml(
    root, beast_version=config.control.beast_version, header_comments=[fingerprint_comment(config)]
)
print("=== raw treeLikelihood ===")
for line in xml_str.splitlines():
    if "treeLikelihood" in line and "<" in line:
        print(repr(line))

tree = DET.fromstring(xml_str)
for tl in tree.iter("treeLikelihood"):
    print("=== treeLikelihood children tags ===")
    for child in tl:
        print("  tag=", child.tag, "attrib=", dict(child.attrib))
    for ref in ("patterns", "siteModel", "treeModel"):
        kids = list(tl.iter(ref))
        print(f"iter({ref}) ->", [(k.tag, dict(k.attrib)) for k in kids])
