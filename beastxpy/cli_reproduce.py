"""``beastxpy reproduce`` - verify or replay a locked environment.

``--check`` compares the machine against a lock field by field and fails when
the jar, Java, native BEAGLE or a dependency has moved.  ``--run`` re-executes
the locked analysis through the normal runner, so a reproduction uses exactly
the code path an ordinary run uses.

A lock is evidence, not a suggestion: an edited lock is rejected outright
rather than silently honoured.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, List

from . import _contracts as C
from .lockfile import (
    LockError,
    LockTamperedError,
    ReproduceReport,
    check_lock,
    load_lock,
    run_from_lock,
)


def register(sub: Any) -> None:
    """Attach the ``reproduce`` command. Called automatically by main.py."""
    p = sub.add_parser(
        "reproduce",
        help="verify or replay an environment lock",
    )
    p.description = (
        "Compare the current machine with a lock written by 'beastxpy lock' "
        "(--check), or re-execute the analysis it pins (--run). --check fails "
        "when the BEAST jar, Java, native BEAGLE or a key dependency differs. "
        "A lock whose field digests do not match is rejected as tampered."
    )
    p.epilog = (
        "Exit codes: 0 = environment matches / analysis ran, 1 = bad arguments, "
        "2 = mismatch or missing environment, 3 = corrupt or tampered lock."
    )
    p.add_argument("--lock", required=True, metavar="PATH", help="lock file to verify")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="compare the machine against the lock without running anything",
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="re-execute the locked analysis (needs --config and --workdir)",
    )
    p.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="config to re-check against the lock's content hash (required by --run)",
    )
    p.add_argument(
        "--workdir",
        default=None,
        metavar="DIR",
        help="directory for the reproduced run (required by --run)",
    )
    p.add_argument("--beagle-lib", default=None, metavar="DIR", help="native hmsbeagle directory")
    p.add_argument(
        "--beagle-threads",
        type=int,
        default=1,
        metavar="N",
        help="BEAGLE thread count (use 1 on Windows)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=86400,
        metavar="SECONDS",
        help="per-seed timeout for --run (default: 86400 = 24 h)",
    )
    C.add_json_argument(p)
    C.add_quiet_argument(p)
    p.set_defaults(handler=cmd_reproduce)


def _ascii(text: Any) -> str:
    """Console-safe rendering: escape only what the console cannot encode."""
    return C.console_text(text)


def summary_lines(report: ReproduceReport) -> List[str]:
    """Human-readable comparison report (ASCII-safe)."""
    lines = [f"reproduce: mode={report.mode} verdict={report.verdict}"]
    lines.append(f"  {len(report.matches)} field(s) match")
    for item in report.mismatches:
        field = item.get("field", "?")
        expected = item.get("expected")
        actual = item.get("actual")
        lines.append(f"  MISMATCH {field}: lock={_ascii(expected)!r} now={_ascii(actual)!r}")
    for item in report.unchecked:
        lines.append(f"  unchecked {item.get('field', '?')}: {_ascii(item.get('reason'))}")
    if report.reason:
        lines.append(f"  reason: {_ascii(report.reason)}")
    if report.fix:
        lines.append(f"  next step: {_ascii(report.fix)}")
    return lines


def cmd_reproduce(args: argparse.Namespace) -> int:
    """Verify or replay the lock; return the documented exit code."""
    if args.run and not args.config:
        raise C.ContractError(
            "--run needs --config",
            fix="Pass the configuration the lock was built from: --config config.yaml.",
        )
    if args.run and not args.workdir:
        raise C.ContractError(
            "--run needs --workdir",
            fix="Pass a directory for the reproduced run: --workdir runs/reproduced.",
        )

    try:
        document = load_lock(args.lock)
    except LockTamperedError as exc:
        C.log(C.format_error(exc))
        return C.EXIT_FAILED
    except LockError as exc:
        C.log(C.format_error(exc))
        return C.EXIT_FAILED

    if args.run:
        report = run_from_lock(
            document,
            config_path=args.config,
            workdir=Path(args.workdir).expanduser(),
            beagle_dir=args.beagle_lib,
            beagle_threads=args.beagle_threads,
            timeout=args.timeout,
            force=True,
            log=C.log,
        )
    else:
        config_path = args.config
        if config_path is None:
            # The lock stores only the config basename, so fall back to a config
            # of that name next to the lock when the user did not pass one.
            candidate = Path(args.lock).expanduser().parent / str(
                (document.get("config") or {}).get("file_name") or ""
            )
            config_path = candidate if candidate.is_file() else None
        report = check_lock(
            document,
            config_path=config_path,
            beagle_dir=args.beagle_lib,
            beagle_threads=args.beagle_threads,
        )

    if args.json_path:
        payload = C.envelope(C.SCHEMA_REPRODUCE, **report.as_dict())
        C.emit_json(payload, args.json_path, to_stdout=False)
        if not args.quiet:
            C.log(f"wrote {C.display_path(args.json_path)}")
    C.print_summary(summary_lines(report), quiet=args.quiet)
    return C.EXIT_OK if report.ok else C.EXIT_UNAVAILABLE


__all__ = ["cmd_reproduce", "register", "summary_lines"]
