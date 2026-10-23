#!/usr/bin/env python3
"""Bump the package version everywhere it is declared.

One command updates every place that must agree before a release:

    python tools/bump_version.py 0.2.0
    python tools/bump_version.py 0.2.0 --date 2026-11-01

Updated files: ``pyproject.toml`` (``version``), ``beastxpy/__init__.py``
(``__version__``), ``CITATION.cff`` (``version`` and ``date-released``), both
READMEs, every page under ``docs/en`` and ``docs/zh``, and the jar-free wheel
example in ``tools/build_core_wheel.py``.

The replacement is token-exact: only the old version string is replaced, and
never inside a longer number such as ``3.11.0`` or ``10.5.0``. Run the test
suite afterwards; ``tests/test_release_contract.py`` fails when any metadata
file disagrees.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def current_version() -> str:
    init = (ROOT / "beastxpy" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__ = "(\d+\.\d+\.\d+)"', init)
    if not match:
        raise SystemExit("cannot find __version__ in beastxpy/__init__.py")
    return match.group(1)


def replace_token(text: str, old: str, new: str) -> str:
    # Token-exact: not preceded by a digit/dot, and not followed by a digit or
    # by ".<digit>" (so "0.1.0.1" is never matched, but "0.1.0." at the end of
    # a sentence is).
    pattern = re.compile(r"(?<![\d.])" + re.escape(old) + r"(?!\.?\d)")
    return pattern.sub(new, text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_version", help="new version, e.g. 0.2.0")
    parser.add_argument(
        "--date",
        default=datetime.date.today().isoformat(),
        help="date-released value for CITATION.cff (default: today)",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.new_version):
        parser.error("version must be X.Y.Z")

    old = current_version()
    if old == args.new_version:
        print(f"already at {old}; nothing to do")
        return 0

    changed: list[Path] = []

    def sub_file(path: Path, *operations) -> None:
        text = path.read_text(encoding="utf-8")
        for op in operations:
            text = op(text)
        path.write_text(text, encoding="utf-8")
        changed.append(path.relative_to(ROOT))

    sub_file(
        ROOT / "beastxpy" / "__init__.py",
        lambda s: re.sub(r'__version__ = "[^"]+"', f'__version__ = "{args.new_version}"', s),
    )
    sub_file(
        ROOT / "pyproject.toml",
        lambda s: re.sub(r'^version = "[^"]+"', f'version = "{args.new_version}"', s, count=1, flags=re.M),
    )
    sub_file(
        ROOT / "CITATION.cff",
        lambda s: re.sub(r"^version: .+$", f"version: {args.new_version}", s, count=1, flags=re.M),
        lambda s: re.sub(r'^date-released: "[^"]*"$', f'date-released: "{args.date}"', s, count=1, flags=re.M),
    )

    doc_files = [ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "tools" / "build_core_wheel.py"]
    doc_files += sorted((ROOT / "docs").glob("*/*.md"))
    for path in doc_files:
        text = path.read_text(encoding="utf-8")
        new_text = replace_token(text, old, args.new_version)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changed.append(path.relative_to(ROOT))

    print(f"bumped {old} -> {args.new_version} in:")
    for name in changed:
        print("  ", name)
    print("\nnext steps: run the test suite, commit, then tag v" + args.new_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
