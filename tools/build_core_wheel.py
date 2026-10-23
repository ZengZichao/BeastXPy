#!/usr/bin/env python3
"""Build a jar-free ``beastxpy-core`` wheel.

The default distribution bundles the two official BEAST jars (about 22 MB) so
Gate 3 works offline.  Users who already have BEAST, or who want a smaller
wheel and a simpler LGPL surface, can build the same code as
``beastxpy-core`` without the jars:

    python tools/build_core_wheel.py
    pip install dist-core/beastxpy_core-0.1.0-py3-none-any.whl

The resulting package has every Python command, but Gate 3 is unavailable
until the user points ``BEASTX_JAR`` (or ``BEAST_*_HOME``) at their own BEAST
installation. Signal ``beastxpy check-environment`` reports that clearly.

``--dry-run`` stages the tree and prints what would be built without invoking
the wheel builder.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "build" / "core-wheel-src"
DIST = ROOT / "dist-core"


def stage_tree(stage: Path = STAGE) -> Path:
    """Copy the wheel inputs to *stage* and remove the bundled jars."""
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copytree(
        ROOT / "beastxpy",
        stage / "beastxpy",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE"):
        source = ROOT / name
        if source.is_file():
            shutil.copyfile(source, stage / name)
    jars = stage / "beastxpy" / "tools" / "jars"
    removed = sorted(path.name for path in jars.glob("*.jar"))
    for path in jars.glob("*.jar"):
        path.unlink()
    pyproject = stage / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    text = re.sub(r'^name\s*=\s*"beastxpy"', 'name = "beastxpy-core"', text, count=1, flags=re.M)
    text = re.sub(
        r'^description\s*=\s*"[^"]*"',
        'description = "A Python framework for reproducible BEAST 1 divergence-time '
        "estimation with automated model specification, validation and diagnostics "
        '(jar-free core variant; MIT-only)"',
        text,
        count=1,
        flags=re.M,
    )
    text = re.sub(
        r'^license\s*=\s*\{\s*text\s*=\s*"MIT AND LGPL-2\.1-only"\s*\}',
        'license = { text = "MIT" }',
        text,
        count=1,
        flags=re.M,
    )
    text = "\n".join(line for line in text.splitlines() if '"tools/jars/*.jar"' not in line) + "\n"
    pyproject.write_text(text, encoding="utf-8")
    print(f"staged {stage} (removed {len(removed)} jar(s): {', '.join(removed) or 'none'})")
    return stage


def build_wheel(stage: Path = STAGE, outdir: Path = DIST) -> Optional[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    commands = [
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir), str(stage)],
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(outdir),
            str(stage),
        ],
    ]
    for command in commands:
        print("+", " ".join(command), flush=True)
        proc = subprocess.run(command)
        if proc.returncode == 0:
            wheels = sorted(outdir.glob("beastxpy_core-*.whl"))
            return wheels[-1] if wheels else None
        print(f"builder exited {proc.returncode}; trying the next option", file=sys.stderr)
    return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="stage only, do not build")
    parser.add_argument("--stage", default=str(STAGE))
    parser.add_argument("--outdir", default=str(DIST))
    args = parser.parse_args(argv)
    stage = stage_tree(Path(args.stage).expanduser().resolve())
    jars = list((stage / "beastxpy" / "tools" / "jars").glob("*.jar"))
    if jars:
        print("error: jars remain in the staged tree", file=sys.stderr)
        return 1
    if args.dry_run:
        print("dry run: no wheel built")
        return 0
    wheel = build_wheel(stage, Path(args.outdir).expanduser().resolve())
    if wheel is None:
        print("error: no wheel builder produced beastxpy_core-*.whl", file=sys.stderr)
        return 1
    print(f"built {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
