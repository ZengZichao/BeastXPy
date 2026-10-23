#!/usr/bin/env python
"""Verify every example config parses and passes gates 0-2 (no BEAST gate 3)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy.api import BeastXPy, GateError  # noqa: E402

EXAMPLES = ROOT / "examples"
CONFIGS = sorted(EXAMPLES.glob("config_*.yaml"))


def main() -> int:
    bxp = BeastXPy(verbose=False, force=True)
    failures = 0
    for cfg in CONFIGS:
        try:
            xml = bxp.generate_xml(str(cfg), beast_version=None, allow_unvalidated=True)
            print(f"OK   {cfg.name}  ({len(xml)} bytes)")
        except (GateError, ValueError) as exc:
            failures += 1
            print(f"FAIL {cfg.name}: {exc}")
    print(f"\n{len(CONFIGS) - failures}/{len(CONFIGS)} configs passed gates 0-2")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
