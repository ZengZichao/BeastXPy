#!/usr/bin/env python
"""Report every XML element the model emits that is missing from xml_spec.json.

Usage: python tools/check_spec_coverage.py [config ...]
Runs over all example configs by default.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy.api import BeastXPy  # noqa: E402
from beastxpy.model import ModelAssembler  # noqa: E402
from beastxpy.xml_writer import write_xml  # noqa: E402

import defusedxml.ElementTree as DET  # noqa: E402

SPEC_PATH = ROOT / "beastxpy" / "data" / "xml_spec.json"
spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
elements = spec.get("elements", {})
known = set(elements)
known_lower = {k.lower(): k for k in elements}

configs = sys.argv[1:] or sorted(str(p) for p in (ROOT / "examples").glob("config_*.yaml"))

all_emitted = {}
for cfg in configs:
    try:
        bxp = BeastXPy(force=True)
        config = bxp.parse_config(cfg)
        root = ModelAssembler(config).assemble()
        xml_str = write_xml(root, beast_version=config.control.beast_version, header_comments=[])
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP {cfg}: {exc}")
        continue
    tree = DET.fromstring(xml_str)
    for el in tree.iter():
        all_emitted[el.tag] = all_emitted.get(el.tag, 0) + 1

missing = {tag for tag in all_emitted if tag not in known and tag.lower() not in known_lower}
print("Elements emitted but MISSING from xml_spec.json:")
for tag in sorted(missing):
    print(f"  <{tag}>")
print(f"\nTotal emitted: {len(all_emitted)}, missing: {len(missing)}")
