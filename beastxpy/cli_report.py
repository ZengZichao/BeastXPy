"""``beastxpy report`` - one self-contained, offline HTML report.

Collects what actually exists in a ``beastxpy run`` work directory (run
metadata, fingerprints, trace summaries, an assessment document if one was
produced) and renders it as a single HTML file with no external CSS, JS, fonts
or images.

If ``assess`` was not run, the report says so explicitly.  It never invents,
estimates or back-fills a diagnostic number, and a Gate that did not run is
shown as ``not-run`` with its recorded reason rather than a tick.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, List

from . import _contracts as C
from .report import build_report

EXIT_WORKDIR = C.EXIT_UNAVAILABLE


def register(sub: Any) -> None:
    """Attach the ``report`` command. Called automatically by main.py."""
    p = sub.add_parser(
        "report",
        help="render a self-contained offline HTML report for a run",
    )
    p.description = (
        "Summarise a run directory as one offline HTML file: conclusion, "
        "provenance and fingerprints, Gate 0-3 status, diagnostics, run "
        "metadata, trace and assessment summary, the file manifest and the "
        "limitations. Images are inlined as data: URIs and all user text is "
        "HTML-escaped. If assess was not run the report says 'not assessed' "
        "rather than showing numbers."
    )
    p.epilog = (
        "Exit codes: 0 = report written, 1 = bad arguments or write failure, "
        "2 = work directory missing or unreadable."
    )
    p.add_argument("workdir", metavar="WORKDIR", help="run directory to report on")
    p.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="path of the HTML file to write",
    )
    p.add_argument(
        "--lang",
        choices=("en", "zh"),
        default="en",
        help="report language (default: en)",
    )
    p.add_argument(
        "--include-config",
        action="store_true",
        help="embed the configuration found in the work directory",
    )
    p.add_argument(
        "--assessment",
        default=None,
        metavar="PATH",
        help=("assessment JSON to use; default: look for assessment.json in the " "work directory"),
    )
    p.add_argument(
        "--ess-threshold",
        type=float,
        default=200.0,
        metavar="N",
        help="ESS below which a trace parameter is flagged (default: 200)",
    )
    p.add_argument(
        "--no-plot",
        action="store_true",
        help="skip the trace figure (smaller report, no matplotlib use)",
    )
    C.add_json_argument(p)
    C.add_quiet_argument(p)
    p.set_defaults(handler=cmd_report)


def _summary(data: dict, html_path: Path) -> List[str]:
    lines = [f"report: {C.display_path(html_path)}"]
    lang = data.get("lang", "en")
    conclusion = data.get("conclusion") or {}
    verdict = conclusion.get("verdict") or conclusion.get("status") or "?"
    lines.append(f"language: {lang}")
    lines.append(f"conclusion: {verdict}")
    chains = (data.get("run_metadata") or {}).get("chains") or []
    lines.append(f"chains: {len(chains)}")
    assessment = data.get("assessment") or {}
    if assessment.get("available"):
        lines.append(f"assessment: present (verdict {assessment.get('verdict')})")
    else:
        lines.append("assessment: not assessed (no assessment document found)")
    return [line.encode("ascii", "backslashreplace").decode("ascii") for line in lines]


def cmd_report(args: argparse.Namespace) -> int:
    """Render the report; return the documented exit code."""
    workdir = Path(args.workdir).expanduser()
    if not workdir.exists() or not workdir.is_dir():
        # The contract gives an unusable work directory its own exit code (2),
        # distinct from a bad argument or a failed write (1).
        detail = "not found" if not workdir.exists() else "not a directory"
        C.log(
            f"error: work directory {detail}: {C.display_path(workdir)}\n"
            "  next step: pass the directory produced by `beastxpy run`; it "
            "contains one sub-directory per seed with run_meta.json and the "
            "trace logs."
        )
        return EXIT_WORKDIR

    html_path, data = build_report(
        workdir,
        args.output,
        lang=args.lang,
        include_config=args.include_config,
        assessment_path=args.assessment,
        ess_threshold=args.ess_threshold,
        with_plot=not args.no_plot,
        json_path=args.json_path,
    )
    if args.json_path and not args.quiet:
        C.log(f"wrote {C.display_path(args.json_path)}")
    C.print_summary(_summary(data, html_path), quiet=args.quiet)
    return C.EXIT_OK


__all__ = ["cmd_report", "register"]
