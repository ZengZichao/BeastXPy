#!/usr/bin/env python3
"""Deprecated alias for ``tools/patch_xml_spec.py``.

Earlier revisions generated a schema-v1 element list here.  The authoritative
generator now lives in :mod:`patch_xml_spec`: it records parser, syntax-rule
and official-example evidence, the version reverse table, and the project-only
audit in a schema-v2 document.  This wrapper keeps the old command spelling
working without ever writing the incompatible schema-v1 format.

Usage:
    python tools/extract_xml_spec.py [same arguments as patch_xml_spec.py]
    python tools/extract_xml_spec.py --check
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from patch_xml_spec import main as patch_main  # noqa: E402


if __name__ == "__main__":
    print(
        "extract_xml_spec.py is deprecated; using tools/patch_xml_spec.py "
        "(the authoritative schema-v2 generator)",
        file=sys.stderr,
    )
    raise SystemExit(patch_main())
