"""``beastxpy assess`` -- diagnostics for a BeastXPy run workdir.

Reads the ``<workdir>/<seed>/`` artefacts written by ``beastxpy run`` and
reports per-parameter ESS/ACT/mean/median/HPD, multi-seed PSRF
(Gelman-Rubin, split-chain estimator), operator acceptance-rate anomalies,
tree-height / clock-rate / ucld.* trajectories and multi-chain topology
ASDSF, followed by a pass/warn/fail verdict with actionable reasons.

What this command does **NOT** claim (hard rule, CONTRACT.md §5 F1):
``assess`` is diagnostics only.  BeastXPy does not assert convergence -- a
``pass`` verdict means no configured threshold was crossed -- and BeastXPy
does not perform model selection.  Statistics that cannot be computed (fewer
than two usable chains for PSRF/ASDSF, a missing trace column) are reported
as ``not_computable`` with a plain-language reason and are never replaced by
a fabricated value.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import _contracts as C
from .assessment import (
    DEFAULT_BURNIN_FRACTION,
    DEFAULT_ESS_THRESHOLD,
    DEFAULT_PSRF_THRESHOLD,
    assess_trace_files,
    assess_workdir,
    exit_code_for,
    render_text_summary,
    write_html,
)

_EPILOG = """\
Exit codes: 0 pass/warn, 1 warn under --strict, 2 unreadable inputs or no
chains found, 3 fail verdict.

Diagnostics only: BeastXPy does not assert convergence -- a 'pass' verdict
means no configured threshold was crossed -- and BeastXPy does not perform
model selection. Statistics that cannot be computed (PSRF/ASDSF with fewer
than two usable chains, or any parameter whose trace column is absent) are
reported as "not_computable" with a reason, never as a fabricated number.
"""


def _burnin_value(raw: str) -> Any:
    """argparse converter: burn-in fraction or the literal ``auto``."""
    if str(raw).strip().lower() == "auto":
        return "auto"
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"expected a fraction in [0, 1) or 'auto', got {raw!r}"
        ) from None


def register(sub: Any) -> None:
    """Attach the ``assess`` command to the CLI. Called automatically by main.py."""
    p = sub.add_parser(
        "assess",
        help="diagnose MCMC output in a BeastXPy run workdir (diagnostics only)",
    )
    p.description = (
        "Assess the chains in a BeastXPy run workdir (<workdir>/<seed>/): "
        "ESS/ACT/HPD per parameter, multi-seed PSRF, operator acceptance-rate "
        "anomalies, tree-height/clock-rate/ucld.* trajectories and "
        "multi-chain ASDSF, with a pass/warn/fail verdict and reasons."
    )
    p.epilog = _EPILOG
    p.formatter_class = argparse.RawDescriptionHelpFormatter
    p.add_argument(
        "workdir",
        nargs="?",
        default=None,
        help="directory containing <seed>/ run subdirectories (omit with --trace)",
    )
    p.add_argument(
        "--trace",
        action="append",
        default=[],
        metavar="PATH",
        help="assess a bare BEAST trace log outside a BeastXPy workdir; "
        "repeat for multiple chains (label order 1..N)",
    )
    p.add_argument(
        "--trees",
        action="append",
        default=[],
        metavar="PATH",
        help="matching tree log for each --trace (same order); needed for ASDSF",
    )
    p.add_argument(
        "--burnin",
        type=_burnin_value,
        default=DEFAULT_BURNIN_FRACTION,
        metavar="FRACTION|auto",
        help=(
            "burn-in fraction discarded from every chain, or 'auto' for the "
            "Geweke-based automatic burn-in (default: 0.1). The same option "
            "is available in `beastxpy trace` as --burnin auto."
        ),
    )
    p.add_argument(
        "--ess",
        type=float,
        default=DEFAULT_ESS_THRESHOLD,
        metavar="MIN",
        help="minimum acceptable ESS per parameter (default: 200)",
    )
    p.add_argument(
        "--psrf",
        type=float,
        default=DEFAULT_PSRF_THRESHOLD,
        metavar="MAX",
        help="maximum acceptable Gelman-Rubin R-hat (default: 1.01)",
    )
    p.add_argument(
        "--html",
        default=None,
        metavar="PATH",
        help="write a self-contained HTML summary to PATH",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 instead of 0 when the verdict is 'warn'",
    )
    C.add_json_argument(p)
    C.add_quiet_argument(p)
    p.set_defaults(handler=cmd_assess)


def cmd_assess(args: argparse.Namespace) -> int:
    """Run the assessment and return the documented exit code."""
    try:
        if args.trace:
            if args.workdir:
                raise C.ContractError(
                    "give either a workdir or --trace, not both",
                    fix="Run 'beastxpy assess runs/demo' for a BeastXPy run, or "
                    "'beastxpy assess --trace a.log [--trace b.log]' for bare logs.",
                )
            doc = assess_trace_files(
                [Path(item) for item in args.trace],
                [Path(item) for item in (args.trees or [])] or None,
                burnin=args.burnin,
                ess=args.ess,
                psrf=args.psrf,
                strict=args.strict,
            )
        else:
            if not args.workdir:
                raise C.ContractError(
                    "no input given",
                    fix="Pass a workdir (beastxpy assess runs/demo) or one or more "
                    "--trace logs.",
                )
            doc = assess_workdir(
                Path(args.workdir),
                burnin=args.burnin,
                ess=args.ess,
                psrf=args.psrf,
                strict=args.strict,
            )
    except C.ContractError as exc:
        # Unreadable workdir / no chains / bad thresholds: exit 2 per spec.
        C.log(C.format_error(exc))
        return C.EXIT_UNAVAILABLE

    try:
        if args.html:
            written = write_html(doc, Path(args.html))
            if not args.quiet:
                C.log(f"wrote {C.display_path(written)}")
        if args.json_path:
            C.emit_json(doc, args.json_path, to_stdout=False)
            if not args.quiet:
                C.log(f"wrote {C.display_path(args.json_path)}")
    except OSError as exc:
        C.log(
            C.format_error(
                C.ContractError(
                    f"could not write output file: {exc}",
                    fix="Choose a writable --json/--html path and try again.",
                )
            )
        )
        return C.EXIT_USAGE

    C.print_summary(render_text_summary(doc), quiet=args.quiet)
    return exit_code_for(doc, strict=args.strict)
