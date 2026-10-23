"""Command-line interface for BeastXPy.

```
beastxpy generate --config config.yaml [--output out.xml] [--beast-validate] [--force]
beastxpy quick --alignment data.fasta --subst-model hky --clock-model strict ...
beastxpy diagnose --config config.yaml --output-dir diag/ [--sensitivity]
beastxpy validate --xml out.xml [--beast] [--beast-version both]
beastxpy run --config config.yaml --workdir runs/ [--seeds 1,2,3] [--timeout N]
beastxpy trace --log out.log [--plot out.png] [--json]
beastxpy tree-annotate --trees out.trees --output out.tre [--python-only]
beastxpy logcombine --logs a.log b.log --output c.log
beastxpy methods | fingerprint | pipeline | list-models | check-environment
beastxpy assess <workdir> [--burnin 0.1] [--ess 200] [--psrf 1.01] [--strict]
beastxpy check-data --config config.yaml
beastxpy report <workdir> --output report.html [--lang zh|en]
beastxpy compare --configs a.yaml b.yaml --method stepping-stone --workdir DIR
beastxpy lock --config config.yaml --output beastxpy.lock
beastxpy reproduce --lock beastxpy.lock [--check | --run]
beastxpy capabilities [--beast-version both] [--json PATH]
beastxpy container --print docker|apptainer
```

Commands are registered either here or by a ``beastxpy/cli_<name>.py`` module
that exposes ``register(subparsers)``; discovery is automatic, so adding a
command never requires editing this file.

The gates print numbered messages on **stderr** so that ``beastxpy generate``
without ``--output`` can write the complete XML to **stdout**:

* ``Gate 0 (parse-time, before the 3 generation gates)``
* ``Gate 1/3 ...`` ``Gate 2/3 ...`` ``Gate 3/3 ...``

Gate 3 is three-state at the CLI level: ``passed`` only when every requested
BEAST version actually ran and returned success; ``not-run`` (with a reason)
when Java/jars are unavailable or validation never started; ``failed`` when
BEAST ran and reported an error.  The JSON also carries the low-level
four-state ``raw_status`` (including ``environment-missing``) so the
distinction is not lost.  With ``--allow-unvalidated`` the output is always
labelled ``UNVALIDATED`` and is never printed as ``OK``.

``--beast-version both`` writes one document per version
(``<stem>_1.10.4.xml`` / ``<stem>_10.5.0.xml``); the model content is
identical, only the embedded fingerprint/version metadata and the per-version
validation differ.  It requires ``--output``.

Exit codes:

* 0 = success (and, when ``--beast-validate``/``--beast`` was requested, every
  requested BEAST version actually ran and passed);
* 1 = configuration / Gate 0-2 / output failure;
* 2 = validation unavailable or environment missing (no Java/jar, validation
  not run, ``check-environment`` failed);
* 3 = validation failed (BEAST ran and reported an XML/model error).

Two commands refine that table because their whole purpose is to report a
graded verdict, and the refinement is part of their documented contract:

* ``assess``: 0 = pass/warn, 1 = warn under ``--strict``, 2 = unreadable
  inputs, 3 = fail verdict;
* ``check-data``: 0 = pass, 1 = warnings only, 2 = errors found.  Errors are
  never downgraded to warnings.

See ``beastxpy/_contracts.py`` for the authoritative constants and
``docs/en/cli.md`` / ``docs/zh/cli.md`` for the per-command tables.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from ._contracts import ContractError, format_error
from .api import BeastXPy, Gate3Result, GateError, GenerationResult
from .config import ConfigError
from .runner import RunError
from .tracestats import TraceError, format_stats_table, trace_command, trace_to_json
from .treetools import TreeToolsError
from .validator import XMLValidationError

PROG = "beastxpy"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNAVAILABLE = 2
EXIT_VALIDATION_FAILED = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log(message: str = "") -> None:
    """Print a progress/diagnostic line on stderr (stdout stays clean)."""
    print(message, file=sys.stderr)


def _gate3_exit_code(outcome: Gate3Result) -> int:
    return EXIT_VALIDATION_FAILED if outcome.status == "failed" else EXIT_UNAVAILABLE


#: What each Gate-3 mode actually proves, in one short line.
GATE3_MODE_EVIDENCE = {
    "parse": "XML parse only; likelihood NOT evaluated",
    "instantiate": "official parsers; MCMC graph instantiated; likelihood NOT evaluated",
    "java-core": "construct-only shim; likelihood NOT evaluated by the official core",
    "beagle": "full official parsers; native hmsbeagle likelihood evaluated",
}


def _gate3_mode_line(outcome: Gate3Result) -> str:
    mode = outcome.mode or "unknown"
    evidence = GATE3_MODE_EVIDENCE.get(mode, "mode evidence not recorded")
    rule = outcome.tree_likelihood_rule_source
    rule_suffix = f"; tree_likelihood_rule_source={rule}" if rule else ""
    return f"mode={mode} ({evidence}){rule_suffix}"


def _warn_if_construct_only(outcome: Gate3Result) -> None:
    if outcome.mode in ("parse", "instantiate", "java-core"):
        _log(
            "  WARNING: this was NOT a full official validation: the likelihood "
            f"was not evaluated ({_gate3_mode_line(outcome)}). Install native "
            "BEAGLE and re-run for the stronger `beagle` mode."
        )


def _print_gate3_outcome(
    outcome: Gate3Result, *, details: bool = True, gate_label: str = "3/3"
) -> None:
    label = f"BEAST {outcome.version}"
    if outcome.status == "passed":
        _log(f"Gate {gate_label}: {label} validation: OK [{_gate3_mode_line(outcome)}]")
        _warn_if_construct_only(outcome)
        return
    if outcome.status == "not-run":
        _log(
            f"Gate {gate_label}: {label} validation: NOT RUN "
            f"[{_gate3_mode_line(outcome)}] -- {outcome.reason}"
        )
    else:
        _log(
            f"Gate {gate_label}: {label} validation: FAILED "
            f"[{_gate3_mode_line(outcome)}] -- {outcome.reason}"
        )
        _warn_if_construct_only(outcome)
    if not details:
        return
    if outcome.command:
        _log("  command: " + " ".join(str(part) for part in outcome.command))
    if outcome.exit_code is not None:
        _log(f"  exit code: {outcome.exit_code}")
    for line in outcome.stderr_head[:20]:
        _log(f"  stderr: {line}")
    for finding in outcome.findings:
        if hasattr(finding, "as_dict"):
            data = finding.as_dict()
            _log(
                f"  finding [{data.get('severity', '?')}] "
                f"{data.get('path', '?')}: {data.get('message', '')}"
            )
        else:
            _log(f"  finding: {finding}")
    if outcome.environment_error:
        _log(
            "  remediation: install a JDK and BEAST, then set JAVA_HOME "
            "(or BEASTX_JAVA) and BEAST_1_10_4_HOME / BEAST_10_5_0_HOME "
            "(or BEASTX_JAR); BEAGLE (hmsbeagle) is required for MCMC runs."
        )


def _print_gate_error(exc: GateError) -> None:
    outcomes = list(getattr(exc, "outcomes", []) or [])
    if exc.gate_name == "3" and outcomes and not any(o.status == "failed" for o in outcomes):
        _log(f"Gate 3: NOT RUN -- {exc}")
    else:
        _log(f"Gate {exc.gate_name}: FAILED -- {exc}")
    for outcome in outcomes:
        _print_gate3_outcome(outcome)
    if not outcomes and exc.gate_name == "1":
        for finding in exc.findings or []:
            if hasattr(finding, "as_dict"):
                data = finding.as_dict()
                _log(
                    f"  [{data.get('severity', '?')}] {data.get('path', '?')}: "
                    f"{data.get('message', '')}"
                )
            else:
                _log(f"  {finding}")


def _report_generation(result: GenerationResult, *, allow_unvalidated: bool) -> int:
    """Print Gate 1-3 statuses for a generation result and return an exit code."""
    errors = [f for f in result.gate1_findings if getattr(f, "severity", "") == "error"]
    warnings_ = [f for f in result.gate1_findings if getattr(f, "severity", "") == "warning"]
    _log(f"Gate 1/3: OK ({len(warnings_)} warning(s), {len(errors)} error(s))")
    _log(f"Gate 2/3: OK ({len(result.gate2_findings)} finding(s))")
    for finding in result.gate2_findings:
        severity = getattr(finding, "severity", "info")
        if severity not in ("info", "warning"):
            continue
        code = getattr(finding, "code", "") or "-"
        where = getattr(finding, "where", "") or "-"
        message = getattr(finding, "message", str(finding))
        _log(f"  Gate 2 [{severity}] {code} {where}: {message}")

    if not result.beast_validate_requested:
        return EXIT_OK

    exit_code = EXIT_OK
    outcomes = [d.gate3 for d in result.documents if d.gate3 is not None]
    for document in result.documents:
        outcome = document.gate3
        if outcome is None:
            continue
        _print_gate3_outcome(outcome)
        if outcome.status != "passed":
            exit_code = max(exit_code, _gate3_exit_code(outcome))
    # With one document the per-version line above *is* the summary; the
    # aggregate line would only repeat it.
    if len(outcomes) > 1:
        if all(o.status == "passed" for o in outcomes):
            _log("Gate 3/3: BEAST validation: OK")
        elif exit_code == EXIT_VALIDATION_FAILED:
            _log("Gate 3/3: BEAST validation: FAILED")
        else:
            _log(
                "Gate 3/3: BEAST validation: NOT RUN"
                + (" (allowed by --allow-unvalidated)" if allow_unvalidated else "")
            )
    if exit_code != EXIT_OK and allow_unvalidated:
        reasons = "; ".join(
            f"{d.version}: {d.gate3.status}" + (f" ({d.gate3.reason})" if d.gate3.reason else "")
            for d in result.documents
            if d.gate3 is not None and d.gate3.status != "passed"
        )
        _log(
            f"UNVALIDATED -- {'failed' if exit_code == EXIT_VALIDATION_FAILED else 'not run'}: {reasons}"
        )
        _log("The generated XML must not be reported as BEAST-validated.")
    return exit_code


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "BeastXPy -- reproducible BEAST 1 divergence-time estimation "
            "(BEAST 1.10.4 / BEAST X 10.5.0)"
        ),
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # generate -----------------------------------------------------------------
    p = sub.add_parser("generate", help="generate a BEAST 1 XML from a YAML config")
    p.add_argument("--config", required=True)
    p.add_argument(
        "--output",
        default=None,
        help="output XML path; omitted = write the complete XML to stdout. "
        "'both' appends the version tag (<stem>_1.10.4.xml / <stem>_10.5.0.xml)",
    )
    p.add_argument("--beast-validate", action="store_true", help="run BEAST (gate 3)")
    p.add_argument("--allow-unvalidated", action="store_true")
    p.add_argument(
        "--no-data-check",
        action="store_true",
        help="skip the data/calibration pre-check (the explicit escape hatch)",
    )
    p.add_argument(
        "--allow-custom-fragments",
        action="store_true",
        help=(
            "enable custom_fragments from the config; the spliced document is "
            "marked 'custom' and must pass Gate 3, which is forced on"
        ),
    )
    p.add_argument("--beast-version", default=None, choices=["1.10.4", "10.5.0", "both"])
    p.add_argument("--force", action="store_true")
    p.set_defaults(handler=cmd_generate)

    # quick --------------------------------------------------------------------
    p = sub.add_parser("quick", help="quick single-partition generation")
    p.add_argument("--alignment", required=True)
    p.add_argument("--tree-prior", default="yule")
    p.add_argument("--subst-model", default="hky")
    p.add_argument("--clock-model", default="strict")
    p.add_argument("--calibration-yaml", default=None)
    p.add_argument(
        "--chain-length",
        type=int,
        default=1_000_000,
        help="MCMC chain length for this quick single-partition run (default: 1000000)",
    )
    p.add_argument("--name", default="quick")
    p.add_argument("--output", default=None)
    p.add_argument("--beast-version", default="1.10.4", choices=["1.10.4", "10.5.0"])
    p.add_argument("--beast-validate", action="store_true")
    p.add_argument("--allow-unvalidated", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(handler=cmd_quick)

    # diagnose -----------------------------------------------------------------
    p = sub.add_parser("diagnose", help="calibration diagnostics (gate 2)")
    p.add_argument("--config", required=True)
    p.add_argument("--output-dir", default="diagnostics")
    p.add_argument("--report", default=None)
    p.add_argument("--sensitivity", action="store_true")
    p.add_argument("--no-visualize", action="store_true", dest="no_visualize")
    p.set_defaults(handler=cmd_diagnose)

    # validate -----------------------------------------------------------------
    p = sub.add_parser("validate", help="validate an existing XML file")
    p.add_argument("--xml", required=True)
    p.add_argument("--beast", action="store_true", help="include BEAST (gate 3)")
    p.add_argument("--beast-version", default="1.10.4", choices=["1.10.4", "10.5.0", "both"])
    p.add_argument("--allow-unvalidated", action="store_true")
    p.set_defaults(handler=cmd_validate)

    # run ----------------------------------------------------------------------
    p = sub.add_parser("run", help="run BEAST on a generated XML (seed matrix)")
    p.add_argument("--config", required=True)
    p.add_argument("--workdir", default="runs")
    p.add_argument(
        "--seeds",
        default=None,
        help="comma-separated RNG seeds; default = control.seed from the config",
    )
    p.add_argument("--beast-version", default=None, choices=["1.10.4", "10.5.0"])
    p.add_argument(
        "--timeout",
        type=int,
        default=86400,
        help="per-seed timeout in seconds (default: 86400 = 24 h)",
    )
    p.add_argument("--resume", action="store_true")
    p.add_argument("--rerun-successful", action="store_true", dest="rerun_successful")
    p.add_argument("--checkpoint-every", type=int, default=None)
    p.add_argument("--java-only", action="store_true")
    p.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help=(
            "run up to N seeds concurrently (each seed already has its own "
            "subdirectory; default: 1 = sequential)"
        ),
    )
    p.add_argument(
        "--no-data-check",
        action="store_true",
        help="skip the data/calibration pre-check (the explicit escape hatch)",
    )
    p.add_argument("--force", action="store_true")
    p.set_defaults(handler=cmd_run)

    # trace --------------------------------------------------------------------
    p = sub.add_parser("trace", help="trace-file statistics (pure Python)")
    p.add_argument("--log", required=True)
    p.add_argument("--burnin", type=int, default=None)
    p.add_argument("--ess-threshold", type=float, default=200.0)
    p.add_argument("--plot", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=cmd_trace)

    # tree-annotate -------------------------------------------------------------
    p = sub.add_parser("tree-annotate", help="annotate a tree log")
    p.add_argument("--trees", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--heights", default="mean", choices=["keep", "median", "mean", "ca"])
    p.add_argument("--burnin", type=int, default=0)
    p.add_argument("--limit", type=float, default=0.0)
    p.add_argument("--python-only", action="store_true")
    p.add_argument("--beast-version", default="1.10.4", choices=["1.10.4", "10.5.0"])
    p.set_defaults(handler=cmd_tree_annotate)

    # logcombine ----------------------------------------------------------------
    p = sub.add_parser("logcombine", help="combine log/tree files")
    p.add_argument("--logs", nargs="+", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--burnin", type=int, default=0)
    p.add_argument("--resample", type=int, default=None)
    p.add_argument("--strip", action="store_true")
    p.add_argument("--beast-version", default="1.10.4", choices=["1.10.4", "10.5.0"])
    p.set_defaults(handler=cmd_logcombine)

    # methods / fingerprint / pipeline ------------------------------------------
    p = sub.add_parser("methods", help="generate the LaTeX methods paragraph")
    p.add_argument("--config", required=True)
    p.add_argument("--output", default=None)
    p.set_defaults(handler=cmd_methods)

    p = sub.add_parser("fingerprint", help="generate the analysis fingerprint")
    p.add_argument("--config", required=True)
    p.add_argument("--output", default=None)
    p.set_defaults(handler=cmd_fingerprint)

    p = sub.add_parser("pipeline", help="generate Snakemake/Nextflow pipelines")
    p.add_argument("--config", required=True)
    p.add_argument("--type", default="snakemake", choices=["snakemake", "nextflow"])
    p.add_argument("--output-dir", default="pipeline")
    p.set_defaults(handler=cmd_pipeline)

    # list-models / check-environment -------------------------------------------
    p = sub.add_parser("list-models", help="list registered models")
    p.set_defaults(handler=cmd_list_models)

    p = sub.add_parser("check-environment", help="probe Java/jar/Python")
    p.set_defaults(handler=cmd_check_environment)

    _register_extension_commands(sub)

    return parser


def _register_extension_commands(sub: Any) -> List[str]:
    """Attach every ``beastxpy.cli_<name>`` command module to ``sub``.

    Command modules are discovered by filename so adding a command never
    requires editing this file: a module only has to expose
    ``register(subparsers)``.  Discovery is sorted for a stable ``--help``
    order, and import failures propagate -- a broken command must not vanish
    silently from the CLI.

    Returns the list of command names that were registered (used by tests).
    """
    registered: List[str] = []
    package_dir = Path(__file__).resolve().parent
    for module_file in sorted(package_dir.glob("cli_*.py")):
        module_name = module_file.stem
        module = importlib.import_module(f".{module_name}", __package__)
        register = getattr(module, "register", None)
        if register is None:
            continue
        before = set(_subparser_choices(sub))
        register(sub)
        registered.extend(sorted(set(_subparser_choices(sub)) - before))
    return registered


def _subparser_choices(sub: Any) -> List[str]:
    """Current choice names on an ``_SubParsersAction``."""
    return list(getattr(sub, "choices", {}) or {})


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _bxp(args: argparse.Namespace) -> BeastXPy:
    return BeastXPy(verbose=False, force=getattr(args, "force", False))


def cmd_generate(args: argparse.Namespace) -> int:
    _log(f"Gate 0 (parse-time, before the 3 generation gates): {args.config}")
    bxp = _bxp(args)
    try:
        config = bxp.parse_config(args.config)
    except GateError as exc:
        _log(f"Gate 0: FAILED -- {exc}")
        return EXIT_ERROR
    _log("Gate 0: OK")

    version = args.beast_version or config.control.beast_version
    if version == "both" and not args.output:
        _log(
            "error: --beast-version both requires --output (one file per version: "
            "<stem>_1.10.4.xml / <stem>_10.5.0.xml)"
        )
        return EXIT_ERROR

    try:
        result = bxp.generate_full(
            config,
            output=args.output,
            beast_version=args.beast_version,
            beast_validate=args.beast_validate,
            allow_unvalidated=args.allow_unvalidated,
            data_check=not args.no_data_check,
            allow_custom_fragments=args.allow_custom_fragments,
            config_path=args.config,
        )
    except GateError as exc:
        _print_gate_error(exc)
        return _gate_error_exit_code(exc)
    except (ConfigError, ValueError) as exc:
        _log(f"error: {exc}")
        return EXIT_ERROR

    exit_code = _report_generation(result, allow_unvalidated=args.allow_unvalidated)
    if args.output:
        for document in result.documents:
            _log(f"wrote {document.output_path}")
    else:
        sys.stdout.write(result.primary_xml)
        if not result.primary_xml.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
    return exit_code


def _gate_error_exit_code(exc: GateError) -> int:
    outcomes = list(getattr(exc, "outcomes", []) or [])
    if exc.gate_name == "3" or outcomes:
        if any(o.status == "failed" for o in outcomes):
            return EXIT_VALIDATION_FAILED
        return EXIT_UNAVAILABLE
    return EXIT_ERROR


def cmd_quick(args: argparse.Namespace) -> int:
    bxp = _bxp(args)
    _log("Gate 0 (parse-time, before the 3 generation gates): quick")
    try:
        result = bxp.quick_generate_result(
            alignment=args.alignment,
            tree_prior=args.tree_prior,
            subst_model=args.subst_model,
            clock_model=args.clock_model,
            chain_length=args.chain_length,
            name=args.name,
            output=args.output,
            calibration_yaml=args.calibration_yaml,
            beast_version=args.beast_version,
            beast_validate=args.beast_validate,
            allow_unvalidated=args.allow_unvalidated,
        )
    except GateError as exc:
        _print_gate_error(exc)
        return _gate_error_exit_code(exc)
    except (ConfigError, ValueError) as exc:
        _log(f"error: {exc}")
        return EXIT_ERROR
    exit_code = _report_generation(result, allow_unvalidated=args.allow_unvalidated)
    if args.output:
        for document in result.documents:
            _log(f"wrote {document.output_path}")
    else:
        sys.stdout.write(result.primary_xml)
        if not result.primary_xml.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
    return exit_code


def cmd_diagnose(args: argparse.Namespace) -> int:
    bxp = BeastXPy()
    try:
        report = bxp.diagnose(
            args.config,
            output_dir=args.output_dir,
            report_path=args.report,
            visualize=not args.no_visualize,
            sensitivity=args.sensitivity,
        )
    except (ConfigError, ValueError) as exc:
        _log(f"error: {exc}")
        return EXIT_ERROR
    print(
        f"diagnostics: {len(report.findings)} finding(s), "
        f"sensitivity variants: {report.n_sensitivity}"
    )
    for finding in report.findings:
        print(f"  [{finding.severity}] {finding.where}: {finding.message}")
    print(f"report: {report.extra.get('report', args.output_dir + '/report.html')}")
    return EXIT_ERROR if report.has_errors else EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    from .validator import validate_xml_file

    _log("Gate 1/2: structural and semantic XML validation")
    findings = validate_xml_file(args.xml, beast_version=args.beast_version)
    errors = [f for f in findings if f.severity == "error"]
    for finding in findings:
        print(f"  [{finding.severity}] {finding.path}: {finding.message}")
    if errors:
        _log("Gate 1/2: FAILED")
        return EXIT_ERROR
    _log("Gate 1/2: OK")
    if not args.beast:
        return EXIT_OK

    _log("Gate 2/2: BEAST validation")
    bxp = BeastXPy()
    try:
        result = bxp.validate_xml(
            args.xml,
            beast_version=args.beast_version,
            beast=True,
            allow_unvalidated=args.allow_unvalidated,
        )
    except GateError as exc:
        _print_gate_error(exc)
        return _gate_error_exit_code(exc)
    except RuntimeError as exc:
        _log(f"Gate 2/2: FAILED -- {exc}")
        return EXIT_UNAVAILABLE

    entries = result.get("gate3") or []
    exit_code = EXIT_OK
    outcomes = [_outcome_from_dict(entry) for entry in entries]
    for outcome in outcomes:
        # A single passing outcome is summarised by the "Gate 2/2" line below;
        # per-version lines add noise, not information.
        if len(outcomes) > 1 or outcome.status != "passed":
            _print_gate3_outcome(outcome, gate_label="2/2")
        if outcome.status != "passed":
            exit_code = max(exit_code, _gate3_exit_code(outcome))
    if exit_code == EXIT_OK:
        modes = "; ".join(
            f"BEAST {outcome.version} {_gate3_mode_line(outcome)}" for outcome in outcomes
        )
        _log("Gate 2/2: OK" + (f" [{modes}]" if modes else ""))
        for outcome in outcomes:
            _warn_if_construct_only(outcome)
    elif exit_code == EXIT_VALIDATION_FAILED:
        _log("Gate 2/2: FAILED")
    else:
        _log("Gate 2/2: NOT RUN")
    if exit_code != EXIT_OK and args.allow_unvalidated:
        _log("UNVALIDATED -- the XML was not confirmed by BEAST parser instantiation.")
    return exit_code


def _outcome_from_dict(entry: Dict[str, Any]) -> Gate3Result:
    return Gate3Result(
        version=str(entry.get("version", "?")),
        status=str(entry.get("status", "not-run")),
        reason=str(entry.get("reason", "")),
        exit_code=entry.get("exit_code"),
        command=list(entry.get("command") or []),
        stderr_head=list(entry.get("stderr_head") or []),
        findings=list(entry.get("findings") or []),
        environment_error=bool(entry.get("environment_error", False)),
        mode=entry.get("mode"),
        beagle=entry.get("beagle"),
        beagle_version=entry.get("beagle_version"),
        tree_likelihood_rule_source=entry.get("tree_likelihood_rule_source"),
        raw_status=entry.get("raw_status"),
    )


def cmd_run(args: argparse.Namespace) -> int:
    from .runner import BeastRunner

    if not args.no_data_check:
        # Pre-flight the data the run is about to consume.  Errors stop the run;
        # --no-data-check is the explicit escape hatch.
        try:
            from .config import ConfigParser
            from .datacheck import run_precheck

            precheck_config = ConfigParser().parse(args.config)
            findings = run_precheck(precheck_config, config_path=args.config)
        except Exception as exc:  # noqa: BLE001 - Gate 0 reports its own errors
            _log(f"data pre-check skipped: {exc}")
            findings = []
        for item in findings:
            if item.get("severity") != "info":
                _log(f"data-check [{item['severity']}] {item['id']}: {item['message']}")
        errors = [item for item in findings if item.get("severity") == "error"]
        if errors:
            _log(
                "run failed: data pre-check found "
                f"{len(errors)} error(s); fix them or pass --no-data-check"
            )
            return EXIT_ERROR

    try:
        seeds = _parse_seeds(args.seeds)
    except RunError as exc:
        _log(f"run failed: {exc}")
        return EXIT_ERROR
    runner = BeastRunner(
        config_path=args.config,
        workdir=args.workdir,
        seeds=seeds,
        force=args.force,
        java_only=args.java_only,
        resume=args.resume,
        checkpoint_every=args.checkpoint_every,
        beast_version=args.beast_version,
        timeout=args.timeout,
        rerun_successful=args.rerun_successful,
        parallel=args.parallel,
    )
    try:
        summary = runner.run()
    except (RunError, ValueError) as exc:
        _log(f"run failed: {exc}")
        return EXIT_ERROR
    for result in summary.results:
        if result.status == "skipped":
            status = "SKIPPED (already successful)"
        elif result.status == "timeout":
            status = f"TIMEOUT after {args.timeout}s"
        elif result.ok:
            status = "OK"
        else:
            status = f"FAILED (exit {result.exit_code})"
        _log(
            f"  seed {result.seed}: {status} in {result.elapsed_seconds:.1f}s "
            f"[log: {result.log_path}]"
        )
        if result.trace_log_path:
            _log(f"    trace: {result.trace_log_path}")
        if result.tree_log_path:
            _log(f"    trees: {result.tree_log_path}")
    _log(f"fingerprint: {summary.fingerprint} (content: {summary.content_fingerprint})")
    return EXIT_OK if summary.overall_ok else EXIT_ERROR


def cmd_trace(args: argparse.Namespace) -> int:
    try:
        result = trace_command(
            args.log,
            burnin=args.burnin,
            ess_threshold=args.ess_threshold,
            plot=args.plot,
            as_json=args.json,
        )
    except (TraceError, FileNotFoundError, OSError, ValueError, KeyError) as exc:
        _log(f"trace failed: {exc}")
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - never leak a traceback for bad input
        _log(f"trace failed: {type(exc).__name__}: {exc}")
        return EXIT_ERROR
    if args.json:
        print(trace_to_json(result))
        return EXIT_OK
    print(format_stats_table(result, ess_threshold=args.ess_threshold))
    for message in result.get("warnings", []):
        _log(f"warning: {message}")
    return EXIT_OK


def cmd_tree_annotate(args: argparse.Namespace) -> int:
    from .treetools import annotate_tree_command

    try:
        result = annotate_tree_command(
            args.trees,
            args.output,
            heights=args.heights,
            burnin=args.burnin,
            limit=args.limit,
            python_only=args.python_only,
            beast_version=args.beast_version,
        )
    except (TreeToolsError, FileNotFoundError) as exc:
        _log(f"tree-annotate failed: {exc}")
        return EXIT_ERROR
    print(f"method: {result.get('method')}")
    print(f"wrote {args.output}")
    return EXIT_OK


def cmd_logcombine(args: argparse.Namespace) -> int:
    from .treetools import combine_logs_command

    try:
        result = combine_logs_command(
            args.logs,
            args.output,
            burnin=args.burnin,
            resample=args.resample,
            strip=args.strip,
            beast_version=args.beast_version,
        )
    except (TreeToolsError, FileNotFoundError) as exc:
        _log(f"logcombine failed: {exc}")
        return EXIT_ERROR
    print(f"method: {result.get('method')}")
    print(f"wrote {args.output}")
    return EXIT_OK


def cmd_methods(args: argparse.Namespace) -> int:
    bxp = BeastXPy()
    try:
        latex = bxp.generate_methods(args.config)
    except (ConfigError, ValueError) as exc:
        _log(f"error: {exc}")
        return EXIT_ERROR
    if args.output:
        from .xml_writer import atomic_write_text

        atomic_write_text(args.output, latex)
        _log(f"wrote {args.output}")
    else:
        print(latex)
    return EXIT_OK


def cmd_fingerprint(args: argparse.Namespace) -> int:
    from .reproducibility import analysis_fingerprint, data_digest

    bxp = BeastXPy()
    try:
        config = bxp.parse_config(args.config)
    except (ConfigError, ValueError) as exc:
        _log(f"error: {exc}")
        return EXIT_ERROR
    payload = {
        "fingerprint": analysis_fingerprint(config),
        "data_digest": data_digest(config),
        "beastxpy_version": __version__,
    }
    text = json.dumps(payload, indent=2)
    if args.output:
        from .xml_writer import atomic_write_text

        atomic_write_text(args.output, text + "\n")
        _log(f"wrote {args.output}")
    else:
        print(text)
    return EXIT_OK


def cmd_pipeline(args: argparse.Namespace) -> int:
    bxp = BeastXPy()
    try:
        created = bxp.generate_pipeline(
            args.config, pipeline_type=args.type, output_dir=args.output_dir
        )
    except (ConfigError, ValueError) as exc:
        _log(f"error: {exc}")
        return EXIT_ERROR
    for path in created:
        print(f"wrote {path}")
    return EXIT_OK


def cmd_list_models(args: argparse.Namespace) -> int:
    bxp = BeastXPy()
    models = bxp.list_models()
    for category, names in models.items():
        print(f"{category}:")
        for name in names:
            print(f"  {name}")
    return EXIT_OK


def cmd_check_environment(args: argparse.Namespace) -> int:
    bxp = BeastXPy()
    report = bxp.check_environment()
    print(f"python: {report['python']}")
    print(f"beastxpy: {report['beastxpy_version']}")
    if report.get("java"):
        print(
            f"java: {report['java']} "
            f"(version {report.get('java_version') or 'unknown'}, "
            f"source {report.get('java_source') or 'unknown'})"
        )
    else:
        print("java: NOT FOUND")
    for version, jar in report["jars"].items():
        if jar:
            digest = jar.get("sha256") or ""
            suffix = f" sha256:{digest}" if digest else ""
            print(f"jar beast-{version}: {jar['path']}{suffix}")
        else:
            print(f"jar beast-{version}: NOT FOUND")
    for version, beagle in (report.get("beagle") or {}).items():
        if beagle:
            print(f"beagle {version}: {beagle['path']} (source {beagle.get('source', '?')})")
        else:
            print(f"beagle {version}: NOT FOUND")
    for line in report.get("beagle_guidance") or []:
        print(f"  guidance: {line}")
    if report.get("ok"):
        return EXIT_OK
    print("environment: UNUSABLE")
    for item in report.get("missing", []):
        print(f"  missing: {item}")
    return EXIT_UNAVAILABLE


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_seeds(seed_arg: Optional[str]) -> Optional[List[int]]:
    """Parse a seed list like '1,2,3' or a single int; ``None`` keeps config seed."""
    if seed_arg is None:
        return None
    seeds: List[int] = []
    for token in seed_arg.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            seeds.append(int(token))
        except ValueError:
            raise RunError(f"invalid seed {token!r} (expected comma-separated integers)") from None
    if not seeds:
        raise RunError("no seeds given")
    return seeds


def main(argv: Optional[List[str]] = None) -> int:
    # Library code emits warnings through ``logging``; the CLI owns the
    # handler, so embedding callers keep control of their own logging setup.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.WARNING,
            format="[BeastXPy] %(levelname)s: %(message)s",
            stream=sys.stderr,
        )
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ContractError as exc:
        _log(format_error(exc))
        return EXIT_ERROR
    except (
        ConfigError,
        GateError,
        XMLValidationError,
        TreeToolsError,
        TraceError,
        RunError,
        ValueError,
    ) as exc:
        _log(f"error: {exc}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        _log("interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001 - clean message instead of a traceback
        _log(f"error: {type(exc).__name__}: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
