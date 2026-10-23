#!/usr/bin/env python
"""Generate one config and report gate-1 findings (for debugging)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy.api import BeastXPy  # noqa: E402
from beastxpy.validator import validate_xml_string  # noqa: E402

cfg = sys.argv[1] if len(sys.argv) > 1 else "examples/config_basic.yaml"
bxp = BeastXPy(force=True)
config = bxp.parse_config(cfg)
from beastxpy.model import ModelAssembler  # noqa: E402
from beastxpy.reproducibility import fingerprint_comment  # noqa: E402
from beastxpy.xml_writer import write_xml  # noqa: E402

root = ModelAssembler(config).assemble()
xml_str = write_xml(
    root, beast_version=config.control.beast_version, header_comments=[fingerprint_comment(config)]
)
findings = validate_xml_string(
    xml_str, beast_version=config.control.beast_version, strict=config.control.strict
)
for f in findings:
    print(f"[{f.severity}] {f.path}: {f.message}")
print(f"\n{len([f for f in findings if f.severity=='error'])} errors")
