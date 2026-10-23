#!/usr/bin/env python3
"""Download and verify an official BEAST distribution for CI and offline use.

The archive URL and SHA-256 come from ``beastxpy/tools/build_jars.py`` (the
same hashes recorded in NOTICE).  Nothing is trusted until the digest matches.

Usage::

    python tools/fetch_beast.py --version 10.5.0 --dest /tmp/beast
    python tools/fetch_beast.py --version 1.10.4 --dest /tmp/beast \
        --github-env "$GITHUB_ENV"

With ``--github-env`` the script appends ``BEAST_<version>_HOME=<root>`` to the
given file (the GitHub Actions environment file).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy.tools.build_jars import OFFICIAL_RELEASES  # noqa: E402


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _find_home(root: Path) -> Path:
    jars = sorted(root.rglob("lib/beast.jar"))
    if not jars:
        jars = sorted(root.rglob("beast.jar"))
    if not jars:
        raise SystemExit(f"no beast.jar under {root}")
    return jars[0].parent.parent


def fetch(version: str, dest: Path, *, github_env: Path | None = None) -> Path:
    official = OFFICIAL_RELEASES[version]
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / f"beast-{version}.zip"
    if not archive.is_file():
        print(f"downloading BEAST {version}: {official['url']}", file=sys.stderr)
        urllib.request.urlretrieve(official["url"], archive)
    digest = _sha256(archive)
    if digest != official["sha256"]:
        archive.unlink(missing_ok=True)
        raise SystemExit(
            f"BEAST {version} archive SHA-256 mismatch: got {digest}, "
            f"expected {official['sha256']}"
        )
    extract_dir = dest / version
    if not extract_dir.exists():
        extract_dir.mkdir()
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(extract_dir)
    home = _find_home(extract_dir)
    print(f"BEAST {version} verified (sha256 {digest}) at {home}")
    if github_env is not None:
        key = f"BEAST_{version.replace('.', '_')}_HOME"
        with github_env.open("a", encoding="utf-8") as handle:
            handle.write(f"{key}={home}\n")
    return home


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, choices=sorted(OFFICIAL_RELEASES))
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--github-env", type=Path, default=None)
    parser.add_argument("--print-home", action="store_true")
    args = parser.parse_args(argv)
    home = fetch(args.version, args.dest, github_env=args.github_env)
    if args.print_home:
        print(home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
