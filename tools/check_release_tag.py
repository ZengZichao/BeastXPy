#!/usr/bin/env python3
"""Verify that a release tag matches the declared package version.

Used by the release workflow and safe to run locally:

    python tools/check_release_tag.py v0.1.0

The tag must be ``vX.Y.Z`` and ``X.Y.Z`` must equal the version in
``pyproject.toml``, ``beastxpy/__init__.py`` and ``CITATION.cff``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def declared_version() -> str:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "(\d+\.\d+\.\d+)"', pyproject, re.M)
    if not match:
        raise SystemExit("pyproject.toml does not declare version = \"X.Y.Z\"")
    return match.group(1)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_release_tag.py vX.Y.Z", file=sys.stderr)
        return 2
    tag = sys.argv[1]
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag)
    if not match:
        print(f"error: tag {tag!r} is not of the form vX.Y.Z", file=sys.stderr)
        return 1
    tag_version = match.group(1)
    problems = []

    package = declared_version()
    if package != tag_version:
        problems.append(f"pyproject.toml version {package} != tag {tag_version}")

    init = (ROOT / "beastxpy" / "__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'__version__ = "(\d+\.\d+\.\d+)"', init)
    if not init_match or init_match.group(1) != tag_version:
        found = init_match.group(1) if init_match else "missing"
        problems.append(f"beastxpy/__init__.py __version__ {found} != tag {tag_version}")

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    cff_match = re.search(r"^version:\s*(\d+\.\d+\.\d+)", citation, re.M)
    if not cff_match or cff_match.group(1) != tag_version:
        found = cff_match.group(1) if cff_match else "missing"
        problems.append(f"CITATION.cff version {found} != tag {tag_version}")

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        print(
            "\nfix: run 'python tools/bump_version.py " + tag_version + "' and commit the result",
            file=sys.stderr,
        )
        return 1
    print(f"ok: tag {tag} matches pyproject.toml, beastxpy/__init__.py and CITATION.cff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
