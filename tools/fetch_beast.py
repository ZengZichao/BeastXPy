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


def _verify_bundled_jar(version: str, archive: Path) -> None:
    """Prove the shipped ``tools/jars/beast-<version>.jar`` is byte-identical
    to the ``lib/beast.jar`` inside the freshly downloaded official archive."""
    official = OFFICIAL_RELEASES[version]
    with zipfile.ZipFile(archive) as bundle:
        inner = [name for name in bundle.namelist() if name.endswith("lib/beast.jar")]
        if len(inner) != 1:
            raise SystemExit(f"expected exactly one lib/beast.jar in {archive}, got {inner}")
        inner_digest = _sha256_stream(bundle.open(inner[0]))
    expected = official.get("jar_sha256")
    if expected is not None and inner_digest != expected:
        raise SystemExit(
            f"BEAST {version} inner jar SHA-256 mismatch: got {inner_digest}, "
            f"expected {expected} ({inner[0]})"
        )
    bundled = ROOT / "beastxpy" / "tools" / "jars" / f"beast-{version}.jar"
    bundled_digest = _sha256(bundled)
    if bundled_digest != inner_digest:
        raise SystemExit(
            f"bundled {bundled} SHA-256 {bundled_digest} differs from the "
            f"official {inner[0]} SHA-256 {inner_digest}"
        )
    print(f"bundled beast-{version}.jar is byte-identical to {inner[0]} (sha256 {inner_digest})")


def _sha256_stream(handle) -> str:
    hasher = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        hasher.update(chunk)
    return hasher.hexdigest()


def fetch(
    version: str, dest: Path, *, github_env: Path | None = None, verify_bundled_jar: bool = False
) -> Path:
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
    if verify_bundled_jar:
        _verify_bundled_jar(version, archive)
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
    parser.add_argument(
        "--verify-bundled-jar",
        action="store_true",
        help="also prove the shipped jar is byte-identical to lib/beast.jar "
        "inside the downloaded archive",
    )
    parser.add_argument("--print-home", action="store_true")
    args = parser.parse_args(argv)
    home = fetch(
        args.version,
        args.dest,
        github_env=args.github_env,
        verify_bundled_jar=args.verify_bundled_jar,
    )
    if args.print_home:
        print(home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
