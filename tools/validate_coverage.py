#!/usr/bin/env python3
"""Generate and validate every coverage fixture with the official BEAST jars.

Unlike ``official_validate.py --matrix`` this understands the per-analysis
version gate (``analysis.beast_version``) and only checks the versions each
fixture claims to support.

    python tools/validate_coverage.py --out validation_runs/coverage
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy.config import ConfigParser  # noqa: E402
from beastxpy.model import ModelAssembler  # noqa: E402
from beastxpy.validator import run_beast_validation  # noqa: E402
from beastxpy.xml_writer import write_xml  # noqa: E402


def versions_for(config: Any) -> List[str]:
    analysis = getattr(config, "analysis", {}) or {}
    declared = analysis.get("beast_version") or config.control.beast_version or "1.10.4"
    if declared == "both":
        return ["1.10.4", "10.5.0"]
    return [str(declared)]


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--configs", default="examples/coverage/config_*.yaml", help="glob of coverage configs"
    )
    parser.add_argument("--out", default="validation_runs/coverage")
    parser.add_argument(
        "--mode", default="java-core", choices=("java-core", "instantiate", "parse", "beagle")
    )
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--versions",
        nargs="+",
        choices=("1.10.4", "10.5.0"),
        default=None,
        help="only run fixtures that declare one of these BEAST versions",
    )
    parser.add_argument(
        "--run", action="store_true", help="run a real MCMC chain (requires --mode beagle)"
    )
    parser.add_argument(
        "--chain-length", type=int, default=None, help="rewrite chainLength before validation"
    )
    parser.add_argument(
        "--beagle-lib", default=None, help="directory with the native hmsbeagle JNI library"
    )
    parser.add_argument(
        "--beagle-threads",
        type=int,
        default=None,
        help="value of beagle.thread.count (Windows 4.0.x builds crash with >1)",
    )
    args = parser.parse_args(argv)
    if args.run and args.mode != "beagle":
        parser.error("--run requires --mode beagle (java-core has no native likelihood core)")

    out_dir = Path(args.out)
    xml_dir = out_dir / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = out_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    failures = 0
    configs = sorted(Path().glob(args.configs))
    if not configs:
        print(f"no coverage configs matched {args.configs}", file=sys.stderr)
        return 2

    for config_path in configs:
        config = ConfigParser().parse(str(config_path))
        declared_versions = versions_for(config)
        if args.versions:
            declared_versions = [v for v in declared_versions if v in args.versions]
            if not declared_versions:
                continue
        for version in declared_versions:
            local = ConfigParser().parse(str(config_path))
            local.control.beast_version = version
            try:
                assembler = ModelAssembler(local)
                xml = write_xml(assembler.assemble(), beast_version=version)
            except Exception as exc:  # noqa: BLE001 - reported as a generation failure
                rows.append(
                    {
                        "analysis": config_path.stem,
                        "version": version,
                        "status": "generation-fail",
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
                failures += 1
                print(f"{config_path.stem:32s} {version:7s} generation-fail", flush=True)
                continue
            xml_path = xml_dir / f"{config_path.stem}_{version}.xml"
            xml_path.write_text(xml, encoding="utf-8")
            # MDS (and any future data-file plugin) references a relative
            # sidecar; BEAST resolves it against the XML's directory (cwd).
            for name, source in assembler.sidecar_files.items():
                shutil.copyfile(source, xml_dir / name)
            result = run_beast_validation(
                str(xml_path),
                beast_version=version,
                timeout=args.timeout,
                parse_only=args.mode == "parse",
                java_core=args.mode == "java-core",
                native_beagle=args.mode == "beagle",
                beagle_dir=args.beagle_lib,
                beagle_threads=args.beagle_threads,
                run_chain=args.run,
                chain_length=args.chain_length,
            )
            log_path = logs_dir / f"{config_path.stem}_{version}.log"
            log_path.write_text(
                "\n".join(
                    [
                        f"# analysis: {config_path.stem}",
                        f"# version: {version}",
                        f"# mode: {args.mode}",
                        f"# status: {result.status}",
                        f"# exit_code: {result.exit_code}",
                        f"# beagle: {result.beagle or ''}",
                        f"# beagle_version: {result.beagle_version or ''}",
                        f"# command: {' '.join(result.command or [])}",
                        "",
                        "## stdout",
                        result.stdout,
                        "",
                        "## stderr",
                        result.stderr,
                    ]
                ),
                encoding="utf-8",
            )
            rows.append(
                {
                    "analysis": config_path.stem,
                    "version": version,
                    "status": result.status,
                    "reason": result.reason.replace("\n", " ")[:400],
                    "exit_code": result.exit_code,
                    "jar_source": (result.jar_path or ""),
                    "jar_sha256": result.jar_sha256 or "",
                    "beagle": result.beagle or "",
                    "beagle_version": result.beagle_version or "",
                    "chain_length_seen": (
                        result.chain_length_seen if result.chain_length_seen is not None else ""
                    ),
                    "log": str(log_path),
                }
            )
            if not result.ok:
                failures += 1
            print(
                f"{config_path.stem:32s} {version:7s} {result.status:18s} " f"{result.reason[:80]}",
                flush=True,
            )

    csv_path = out_dir / "coverage_matrix.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "analysis",
                "version",
                "status",
                "reason",
                "exit_code",
                "jar_source",
                "jar_sha256",
                "beagle",
                "beagle_version",
                "chain_length_seen",
                "log",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {csv_path} ({len(rows)} rows, {failures} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
