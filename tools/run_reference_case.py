#!/usr/bin/env python3
"""Run the shipped reference-data case study and record its evidence.

The alignment is ``examples/primates.fasta``: a short published/reference
tutorial alignment (12 taxa, 898 bp) already shipped with this repository.
No new empirical data are generated or downloaded, and the result must not be
read as a biological conclusion; the case study demonstrates the workflow on
reference data.

Steps, each executed through the public CLI and recorded with its exit code,
wall time and command:

1. ``check-data``
2. ``generate --beast-validate`` (Gate 3)
3. ``run`` over a seed matrix
4. ``assess --json``
5. ``report`` (HTML + JSON)
6. ``lock``

The generated ``case_study_summary.json`` contains the environment, commands,
timings, verdict, per-chain ESS and multi-chain diagnostics. It never invents
a number: missing diagnostics are copied as ``not_computable``.

Usage::

    export JAVA_HOME=/path/to/jdk-17
    export BEASTX_BEAGLE_1_10_4_LIB=/path/to/beagle-3.1.x/lib
    python tools/run_reference_case.py --workdir validation_runs/primates \
        --seeds 1,2,3,4

Exit codes: 0 = all steps completed, 1 = a step failed, 2 = environment
missing (no JDK or BEAST).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_CONFIG = ROOT / "examples" / "empirical" / "config_primates_reference.yaml"
DEFAULT_RESULTS = ROOT / "examples" / "empirical" / "results"


def _run(command: List[str], *, env: Dict[str, str], cwd: Path) -> Dict[str, Any]:
    print("+", " ".join(command), flush=True)
    started = time.monotonic()
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.monotonic() - started
    if proc.stdout.strip():
        print(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr, file=sys.stderr)
    return {
        "command": command,
        "exit_code": proc.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "stdout_tail": proc.stdout.strip().splitlines()[-20:],
        "stderr_tail": proc.stderr.strip().splitlines()[-20:],
    }


def _cli(*args: str) -> List[str]:
    return [sys.executable, "-m", "beastxpy.main", *args]


def _environment(beast_version: str, beagle_lib: Optional[str]) -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if beagle_lib:
        tag = beast_version.replace(".", "_")
        env[f"BEASTX_BEAGLE_{tag}_LIB"] = beagle_lib
        loader = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
        env[loader] = beagle_lib + (os.pathsep + env[loader] if env.get(loader) else "")
    return env


def _assessment_summary(path: Path) -> Dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    chains = []
    for chain in document.get("chains") or []:
        parameters = chain.get("parameters") or {}
        if isinstance(parameters, dict):
            stats_items = [value for value in parameters.values() if isinstance(value, dict)]
        else:
            stats_items = [value for value in parameters if isinstance(value, dict)]
        ess_values = [
            float(stats["ess"])
            for stats in stats_items
            if isinstance(stats.get("ess"), (int, float))
        ]
        chains.append(
            {
                "seed": chain.get("seed"),
                "status": chain.get("status"),
                "samples": chain.get("samples"),
                "burnin_fraction": chain.get("burnin_fraction"),
                "parameters": len(stats_items),
                "min_ess": min(ess_values) if ess_values else None,
            }
        )
    multichain = document.get("multichain") or {}
    return {
        "verdict": document.get("verdict"),
        "strict": document.get("strict"),
        "thresholds": document.get("thresholds"),
        "chains": chains,
        "psrf": multichain.get("psrf"),
        "rank_diagnostics": multichain.get("rank_diagnostics"),
        "asdsf": multichain.get("asdsf"),
        "reasons": document.get("reasons"),
        "limitations": document.get("limitations"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--workdir", default=str(ROOT / "validation_runs" / "primates_reference"))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    parser.add_argument("--seeds", default="1,2,3,4")
    parser.add_argument("--parallel", type=int, default=1, help="concurrent seeds")
    parser.add_argument("--beast-version", default="1.10.4", choices=("1.10.4", "10.5.0"))
    parser.add_argument("--beagle-lib", default=None)
    parser.add_argument("--timeout", type=int, default=86400)
    args = parser.parse_args(argv)

    config = Path(args.config).expanduser().resolve()
    workdir = Path(args.workdir).expanduser().resolve()
    results = Path(args.results).expanduser().resolve()
    if not config.is_file():
        print(f"error: config not found: {config}", file=sys.stderr)
        return 1
    workdir.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    env = _environment(args.beast_version, args.beagle_lib)
    steps: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {
        "case_study": "shipped reference alignment (primates.fasta)",
        "intended_use": (
            "workflow demonstration on published/reference tutorial data; "
            "not a biological inference or a new empirical dataset"
        ),
        "config": str(config),
        "workdir": str(workdir),
        "results": str(results),
        "beast_version": args.beast_version,
        "seeds": args.seeds,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "python": sys.version.split()[0],
            "java_home": env.get("JAVA_HOME"),
            "beagle_lib": args.beagle_lib,
        },
        "steps": steps,
    }

    generated = workdir / "generated.xml"
    run_dir = workdir / "run"
    assessment = results / "assessment.json"
    report_html = results / "report.html"
    report_json = results / "report.json"
    lock = results / "beastxpy.lock"

    plan = [
        (_cli("check-data", "--config", str(config)), {0}),
        (
            _cli(
                "generate",
                "--config",
                str(config),
                "--output",
                str(generated),
                "--beast-validate",
                "--beast-version",
                args.beast_version,
                "--force",
            ),
            {0},
        ),
        (
            _cli(
                "run",
                "--config",
                str(config),
                "--workdir",
                str(run_dir),
                "--seeds",
                args.seeds,
                "--beast-version",
                args.beast_version,
                "--timeout",
                str(args.timeout),
                "--parallel",
                str(args.parallel),
                "--force",
            ),
            {0},
        ),
        # A `fail` assessment is a valid diagnostic outcome: keep the JSON,
        # report and lock so the negative result is recorded too. Exit 2
        # (unreadable input / no chains) is still fatal.
        (_cli("assess", str(run_dir), "--json", str(assessment)), {0, 3}),
        (
            _cli(
                "report",
                str(run_dir),
                "--output",
                str(report_html),
                "--json",
                str(report_json),
                "--assessment",
                str(assessment),
                "--lang",
                "en",
            ),
            {0},
        ),
        (_cli("lock", "--config", str(config), "--output", str(lock)), {0}),
    ]
    for index, (command, allowed) in enumerate(plan, start=1):
        step = _run(command, env=env, cwd=ROOT)
        step["step"] = index
        steps.append(step)
        if step["exit_code"] not in allowed:
            print(f"error: step {index} failed with exit {step['exit_code']}", file=sys.stderr)
            summary["status"] = "failed"
            summary["failed_step"] = index
            (results / "case_study_summary.json").write_text(
                json.dumps(summary, indent=2) + "\n", encoding="utf-8"
            )
            return 1

    summary["status"] = "completed"
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary["total_elapsed_seconds"] = round(sum(step["elapsed_seconds"] for step in steps), 3)
    summary["assessment"] = _assessment_summary(assessment)
    if generated.is_file():
        shutil.copyfile(generated, results / "generated.xml")
        for extra in (
            generated.with_name(generated.stem + ".fingerprint.json"),
            generated.with_name(generated.stem + "_methods.tex"),
        ):
            if extra.is_file():
                shutil.copyfile(extra, results / extra.name)
    shutil.copyfile(config, results / "config.yaml")
    (results / "case_study_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(f"case study completed; summary: {results / 'case_study_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
