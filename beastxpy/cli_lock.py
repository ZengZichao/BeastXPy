"""``beastxpy lock`` - record the environment an analysis was pinned to.

Writes a human-readable, stable JSON lock holding the tool, Python, Java, BEAST
jar (by SHA-256), native BEAGLE (by version and SHA-256), dependency versions,
the configuration's content hash and the seeds.  The file is sealed with field
digests, so a later edit is detectable.

The lock deliberately records **no** credentials and **no** user-specific
absolute paths: every file is identified by basename plus digest, and the
document is checked for leaks before it is written.
"""

from __future__ import annotations

import argparse
from typing import Any, List, Optional

from . import _contracts as C
from .lockfile import build_lock, write_lock


def register(sub: Any) -> None:
    """Attach the ``lock`` command. Called automatically by main.py."""
    p = sub.add_parser(
        "lock",
        help="write an environment lock for a configuration",
    )
    p.description = (
        "Record the exact environment an analysis depends on: BeastXPy, Python "
        "and platform, Java, the BEAST jar and native BEAGLE library by "
        "SHA-256, key dependency versions, the configuration content hash and "
        "the seeds. The lock contains no credentials and no user-specific "
        "absolute paths, and is sealed so later edits are detectable."
    )
    p.epilog = (
        "Exit codes: 0 = lock written, 1 = bad arguments or the config cannot be "
        "parsed, 2 = the lock could not be written."
    )
    p.add_argument("--config", required=True, metavar="PATH", help="YAML config to lock")
    p.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="path of the lock file to write (conventionally beastxpy.lock)",
    )
    p.add_argument(
        "--beast-version",
        choices=("1.10.4", "10.5.0"),
        default=None,
        help="BEAST version to lock (default: the config's control.beast_version)",
    )
    p.add_argument(
        "--seeds",
        default=None,
        metavar="1,2,3",
        help="comma-separated seeds to record (default: the config's seed setting)",
    )
    p.add_argument(
        "--beagle-lib",
        default=None,
        metavar="DIR",
        help="directory holding the native hmsbeagle library",
    )
    p.add_argument(
        "--beagle-threads",
        type=int,
        default=1,
        metavar="N",
        help="BEAGLE thread count recorded in the lock (use 1 on Windows)",
    )
    C.add_json_argument(p)
    C.add_quiet_argument(p)
    p.set_defaults(handler=cmd_lock)


def _parse_seeds(raw: Optional[str]) -> Optional[List[int]]:
    if raw is None:
        return None
    seeds: List[int] = []
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        try:
            seeds.append(int(token))
        except ValueError:
            raise C.ContractError(
                f"invalid seed {token!r}",
                fix="Pass comma-separated integers, for example --seeds 1,2,3.",
            ) from None
    if not seeds:
        raise C.ContractError(
            "no seeds given",
            fix="Pass comma-separated integers, for example --seeds 1,2,3.",
        )
    return seeds


def _summary(document: dict, output: str) -> List[str]:
    beast = document.get("beast") or {}
    beagle = document.get("beagle") or {}
    java = document.get("java") or {}
    lines = [f"lock: {C.display_path(output)}"]
    lines.append(
        f"  BEAST {beast.get('version')} jar {str(beast.get('jar_sha256'))[:16]} "
        f"({beast.get('jar_source')})"
    )
    lines.append(
        f"  Java {java.get('version')} {java.get('arch_data_model')}-bit " f"({java.get('source')})"
    )
    if beagle.get("available"):
        lines.append(
            f"  native BEAGLE {beagle.get('expected_version')} "
            f"(expected pairing) "
            f"{str(beagle.get('library_sha256'))[:16]} threads={beagle.get('threads')}"
        )
    else:
        lines.append(f"  native BEAGLE: not found -- {beagle.get('reason')}")
    dependencies = document.get("dependencies") or {}
    missing = [name for name, version in dependencies.items() if not version]
    lines.append(f"  dependencies: {len(dependencies) - len(missing)} recorded")
    if missing:
        lines.append("  dependencies missing: " + ", ".join(missing))
    config = document.get("config") or {}
    lines.append(
        f"  config {config.get('file_name')} sha256 {str(config.get('content_sha256'))[:16]}"
    )
    lines.append(f"  seeds: {document.get('seeds')}")
    lines.append("  the lock records basenames and digests only; no paths or credentials")
    return [C.console_text(line) for line in lines]


def cmd_lock(args: argparse.Namespace) -> int:
    """Build, seal, privacy-check and write the lock."""
    seeds = _parse_seeds(args.seeds)
    document = build_lock(
        args.config,
        beast_version=args.beast_version,
        seeds=seeds,
        beagle_dir=args.beagle_lib,
        beagle_threads=args.beagle_threads,
    )
    path = write_lock(args.output, document)
    if args.json_path:
        C.emit_json(document, args.json_path, to_stdout=False)
        if not args.quiet:
            C.log(f"wrote {C.display_path(args.json_path)}")
    C.print_summary(_summary(document, str(path)), quiet=args.quiet)
    return C.EXIT_OK


__all__ = ["cmd_lock", "register"]
