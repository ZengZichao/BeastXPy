"""``beastxpy compare`` -- marginal-likelihood model comparison.

Runs a real path-sampling or stepping-stone analysis for each configuration
through BEAST and reports the log marginal likelihood, its standard error and
the pairwise log Bayes factors.

What the command does **not** claim: it does not assess convergence of the
underlying chains, it does not test model adequacy, and it never reports a
number that BEAST did not actually produce.  When the environment cannot run
a real chain (no Java, no jar, no native BEAGLE, or a JVM whose bitness cannot
load the BEAGLE library) the command fails closed with exit code 2 and no
numbers.  When the configurations do not describe the same data it refuses
with exit code 3 and does not start BEAST at all.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, List, Optional

from . import _contracts as C
from . import compare as M

_EPILOG = """\
Exit codes:
  0  compared (every model produced a verified log marginal likelihood)
  1  bad arguments, or fewer than two configurations
  2  environment unavailable (Java, BEAST jar, native BEAGLE) -- no numbers
  3  configurations not comparable, or BEAST failed / did not verify

Interpretation limits (also in the JSON `limitations` array):
  * A log marginal likelihood is a Monte-Carlo estimate.  Read it together
    with its standard error; a log Bayes factor that is not clearly larger
    than the combined standard errors is not evidence.
  * The Kass & Raftery bands (2 ln BF of 2 / 6 / 10) are a reporting
    convention, not a theorem.  They do not license a decision on their own
    and they say nothing about whether either model is adequate.
  * Too-short chains, too-few path steps and inadequate sampling inside a path
    step all bias the estimator, usually optimistically.  The chain length,
    path-step count and per-step sample counts are recorded in the JSON so the
    run can be judged; a short chain is not made trustworthy by being reported.
  * Model comparison only: convergence diagnostics belong to `beastxpy assess`.
"""


def register(sub: Any) -> None:
    """Attach the ``compare`` command.  Called automatically by ``main.py``."""
    parser = sub.add_parser(
        "compare",
        help="compare models by marginal likelihood (path sampling / stepping stone)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.description = (
        "Estimate the log marginal likelihood of two or more configurations with "
        "a real BEAST path-sampling or stepping-stone run, then report pairwise "
        "log Bayes factors.  A placeholder log value is never reported as a "
        "measurement: every number is cross-checked against the path-sampling "
        "log BEAST actually wrote."
    )
    parser.epilog = _EPILOG
    parser.add_argument(
        "--configs",
        nargs="+",
        required=True,
        metavar="PATH",
        help="two or more BeastXPy configuration files to compare",
    )
    parser.add_argument(
        "--method",
        required=True,
        choices=["stepping-stone", "path-sampling"],
        help="which BEAST estimator to report (both are always computed)",
    )
    parser.add_argument(
        "--workdir",
        required=True,
        metavar="DIR",
        help="directory for derived configs, XML, BEAST logs and results",
    )
    parser.add_argument(
        "--seeds",
        default="1",
        metavar="1,2",
        help="comma-separated RNG seeds; each seed is an independent estimate " "(default: 1)",
    )
    parser.add_argument(
        "--chain-length",
        type=int,
        default=M.DEFAULT_CHAIN_LENGTH,
        metavar="N",
        help="MCMC length of each path step (default: %(default)s)",
    )
    parser.add_argument(
        "--path-steps",
        type=int,
        default=M.DEFAULT_PATH_STEPS,
        metavar="N",
        help="number of path steps (default: %(default)s; 50-100 for publication)",
    )
    parser.add_argument(
        "--path-scheme",
        default=M.DEFAULT_PATH_SCHEME,
        choices=["linear", "oneSidedBeta", "beta", "betaQuantile", "sigmoid"],
        help="power-path scheme (default: %(default)s)",
    )
    parser.add_argument(
        "--log-every",
        type=int,
        default=M.DEFAULT_LOG_EVERY,
        metavar="N",
        help="path-sampling log cadence (default: %(default)s)",
    )
    parser.add_argument(
        "--beast-version",
        choices=list(M.VALID_BEAST_VERSIONS),
        default=None,
        help="BEAST version to run (default: the configurations' own setting)",
    )
    parser.add_argument(
        "--beagle-dir",
        default=None,
        metavar="DIR",
        help="native BEAGLE directory (default: BEASTX_BEAGLE_<version>_LIB / " "BEAGLE_HOME)",
    )
    parser.add_argument(
        "--beagle-threads",
        type=int,
        default=1,
        metavar="N",
        help="value of BEAST's beagle.thread.count (default: %(default)s; " "always 1 on Windows)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=86400,
        metavar="SECONDS",
        help="per-model BEAST timeout (default: %(default)s)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help="run up to N seeds of each model concurrently (default: 1 = "
        "sequential); models themselves stay sequential because they "
        "share the BEAGLE environment",
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        default=None,
        metavar="PATH",
        help="write a CSV table of models and pairwise results",
    )
    parser.add_argument(
        "--html",
        dest="html_path",
        default=None,
        metavar="PATH",
        help="write a self-contained HTML report (all text escaped)",
    )
    C.add_json_argument(parser)
    C.add_quiet_argument(parser)
    parser.set_defaults(handler=cmd_compare)


def _format_number(value: Optional[float], spec: str = "%.4f") -> str:
    return "n/a" if value is None else spec % value


def _summary_lines(envelope: dict) -> List[str]:
    settings = envelope.get("settings") or {}
    lines = [
        "BeastXPy model comparison -- method: %s" % envelope.get("method"),
        "BEAST %s | chain length %s | path steps %s | seeds %s"
        % (
            settings.get("beast_version"),
            settings.get("chain_length"),
            settings.get("path_steps"),
            ",".join(str(s) for s in (settings.get("seeds") or [])),
        ),
        "workdir: %s" % envelope.get("workdir"),
        "comparable: %s" % envelope.get("comparable"),
    ]
    beagle = envelope.get("beagle") or {}
    lines.append(
        "native BEAGLE: available=%s expected=%s observed=%s library=%s"
        % (
            beagle.get("available"),
            beagle.get("expected_version"),
            beagle.get("observed_version"),
            beagle.get("library_name"),
        )
    )
    lines.append("")
    lines.append("models:")
    for model in envelope.get("models") or []:
        lines.append(
            "  %-24s %-8s logML = %s  SE = %s"
            % (
                model.get("name"),
                model.get("status"),
                _format_number(model.get("log_ml"), "%.6f"),
                _format_number(model.get("se"), "%.6f"),
            )
        )
        if model.get("reason"):
            lines.append("      reason: %s" % model.get("reason"))
        estimates = model.get("seed_estimates") or []
        if estimates:
            lines.append(
                "      per-seed logML: "
                + ", ".join(
                    "%s=%s"
                    % (
                        entry.get("seed"),
                        _format_number(entry.get("log_ml"), "%.6f"),
                    )
                    for entry in estimates
                )
            )
            seed_range = model.get("seed_log_ml_range")
            if seed_range is not None:
                lines.append("      seed range (max - min): %.6f log units" % seed_range)
    lines.append("")
    lines.append("pairwise log Bayes factors (a - b):")
    for pair in envelope.get("pairwise") or []:
        lines.append(
            "  %s vs %s: logBF = %s (SE %s)"
            % (
                pair.get("a"),
                pair.get("b"),
                _format_number(pair.get("log_bf"), "%.6f"),
                _format_number(pair.get("se_log_bf"), "%.6f"),
            )
        )
        lines.append("      %s" % pair.get("interpretation"))
    if envelope.get("reason"):
        lines.append("")
        lines.append("verdict: %s -- %s" % (envelope.get("verdict"), envelope.get("reason")))
    if envelope.get("fix"):
        lines.append("  next step: %s" % envelope.get("fix"))
    return lines


def cmd_compare(args: argparse.Namespace) -> int:
    """Run the comparison; return the exit code."""
    method = M.normalise_method(args.method)
    seeds = M.parse_seeds(args.seeds)
    workdir = Path(args.workdir).expanduser()
    configs = [Path(item).expanduser() for item in (args.configs or [])]
    if args.path_steps == M.DEFAULT_PATH_STEPS and not getattr(args, "quiet", False):
        C.log(
            "warning: --path-steps %d is a quick estimate; use at least 50 for a "
            "publication-grade marginal likelihood (the bias at low step counts is "
            "usually optimistic)." % args.path_steps
        )

    envelope, exit_code = M.run_comparison(
        configs,
        method=method,
        workdir=workdir,
        seeds=seeds,
        chain_length=args.chain_length,
        path_steps=args.path_steps,
        path_scheme=args.path_scheme,
        log_every=args.log_every,
        beast_version=args.beast_version,
        beagle_dir=args.beagle_dir,
        beagle_threads=args.beagle_threads,
        timeout=args.timeout,
        parallel=args.parallel,
        log=C.log,
    )

    if args.json_path:
        C.write_json(Path(args.json_path), envelope)
    if args.csv_path:
        M.write_text_artifact(Path(args.csv_path), M.render_csv(envelope))
    if args.html_path:
        M.write_text_artifact(Path(args.html_path), M.render_html(envelope))
    if exit_code != C.EXIT_OK:
        C.log("compare: %s (%s)" % (envelope.get("verdict"), envelope.get("reason")))
        if envelope.get("fix"):
            C.log("  next step: %s" % envelope.get("fix"))
    C.print_summary(_summary_lines(envelope), quiet=bool(getattr(args, "quiet", False)))
    return exit_code


__all__ = ["cmd_compare", "register"]
