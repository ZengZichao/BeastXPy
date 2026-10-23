"""``beastxpy check-data`` - pre-flight data and calibration checks.

Inspects the alignment files, partition layout, tip dates, calibrations and
empirical starting tree referenced by a BeastXPy YAML config and reports
findings with severity, precise location and an actionable fix.  It does NOT
generate XML, run BEAST, or make any claim about model adequacy: it is a data
and calibration sanity check only.

Exit codes (fixed by the command specification): 0 = pass, 1 = warnings only,
2 = errors found.  Errors are never downgraded to warnings.
"""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from . import _contracts as C
from .datacheck import (
    DEFAULT_AMBIGUITY_THRESHOLD,
    check_config_file,
    envelope_exit_code,
)


def register(sub: Any) -> None:
    """Attach the ``check-data`` command. Called automatically by main.py."""
    p = sub.add_parser(
        "check-data",
        help="pre-flight data and calibration checks on a config",
    )
    p.description = (
        "Check the data and calibrations a BeastXPy config points at: duplicate "
        "or empty taxa, illegal characters, ambiguous sequences, duplicate "
        "sequences, partition site overlaps/gaps/ranges/taxon sets, calibration "
        "taxa, tip dates, root-height consistency, codon integrity and "
        "empirical-tree taxa. Every finding carries a severity, a location and "
        "a concrete fix. This command does not generate XML or run BEAST."
    )
    p.epilog = (
        "Exit codes: 0 = pass, 1 = warnings only, 2 = errors found "
        "(errors are never downgraded to warnings)."
    )
    p.add_argument(
        "--config",
        required=True,
        metavar="PATH",
        help="BeastXPy YAML config to check",
    )
    p.add_argument(
        "--ambiguity-threshold",
        type=float,
        default=DEFAULT_AMBIGUITY_THRESHOLD,
        metavar="FRACTION",
        help=(
            "per-taxon ambiguous/gap fraction above which 'high-ambiguity' is "
            f"reported; a number in [0, 1] (default {DEFAULT_AMBIGUITY_THRESHOLD})"
        ),
    )
    C.add_json_argument(p)
    C.add_quiet_argument(p)
    p.set_defaults(handler=cmd_check_data)


def _ascii(text: str) -> str:
    """Console-safe rendering: escape only what the console cannot encode."""
    return C.console_text(text)


def _location_text(location: Dict[str, Any]) -> str:
    """Render a location dict as ``key=value`` pairs (ASCII-safe)."""
    if not location:
        return ""
    parts = [f"{key}={value}" for key, value in location.items() if value is not None]
    return _ascii("; ".join(parts))


def summary_lines(envelope: Dict[str, Any]) -> List[str]:
    """Human-readable summary of a datacheck envelope (stdout)."""
    counts = envelope.get("counts") or {}
    verdict = envelope.get("verdict", "?")
    lines = [f"check-data: {_ascii(envelope.get('config_path') or '<config>')}"]
    lines.append(
        f"verdict: {verdict} "
        f"({counts.get('error', 0)} error(s), {counts.get('warning', 0)} warning(s))"
    )
    for finding in envelope.get("findings") or []:
        severity = finding.get("severity", "?")
        lines.append(f"[{severity}] {finding.get('id')}: {_ascii(finding.get('message') or '')}")
        location = _location_text(finding.get("location") or {})
        if location:
            lines.append(f"    at: {location}")
        if finding.get("fix"):
            lines.append(f"    fix: {_ascii(finding['fix'])}")
    if verdict == C.VERDICT_PASS:
        lines.append("no data or calibration problems found")
    return lines


def cmd_check_data(args: argparse.Namespace) -> int:
    """Run the checks; return the fixed check-data exit code."""
    envelope = check_config_file(args.config, ambiguity_threshold=args.ambiguity_threshold)
    if args.json_path:
        C.emit_json(envelope, args.json_path, to_stdout=False)
        if not args.quiet:
            C.log(f"wrote {C.display_path(args.json_path)}")
    C.print_summary(summary_lines(envelope), quiet=args.quiet)
    return envelope_exit_code(envelope)
