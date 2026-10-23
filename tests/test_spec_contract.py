"""Contract tests for hand-maintained data in the xml_spec generator.

``tools/patch_xml_spec.py --check`` rebuilds the element whitelist from parser
and corpus evidence, but its ``REQUIRED_ATTRIBUTES`` / ``ATTRIBUTE_ENUMS``
tables are hand-written.  These tests pin the committed spec to those tables,
so editing one without regenerating the other is a test failure rather than a
silent drift.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "tools" / "patch_xml_spec.py"
SPEC = ROOT / "beastxpy" / "data" / "xml_spec.json"


def _literal_assignment(name: str):
    tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if name in targets:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {GENERATOR}")


def test_required_attributes_table_is_committed_verbatim():
    committed = json.loads(SPEC.read_text(encoding="utf-8"))
    assert committed["structure"]["required_attributes"] == _literal_assignment(
        "REQUIRED_ATTRIBUTES"
    )


def test_attribute_enums_table_is_committed_verbatim():
    committed = json.loads(SPEC.read_text(encoding="utf-8"))
    assert committed["structure"]["attribute_enums"] == _literal_assignment("ATTRIBUTE_ENUMS")
