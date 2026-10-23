#!/usr/bin/env python
"""Dump the generated XML for a config to stdout (skips gate 1 validation)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy.api import BeastXPy  # noqa: E402
from beastxpy.model import ModelAssembler  # noqa: E402
from beastxpy.reproducibility import fingerprint_comment  # noqa: E402
from beastxpy.xml_writer import write_xml  # noqa: E402

cfg = sys.argv[1] if len(sys.argv) > 1 else "examples/config_basic.yaml"
bxp = BeastXPy(force=True)
config = bxp.parse_config(cfg)
root = ModelAssembler(config).assemble()
xml_str = write_xml(
    root, beast_version=config.control.beast_version, header_comments=[fingerprint_comment(config)]
)
print(xml_str)
