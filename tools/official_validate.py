#!/usr/bin/env python3
"""Validate BeastXPy XML with the official BEAST 1.10.4 / 10.5.0 jars.

Examples:
    python tools/official_validate.py --matrix examples/config_*.yaml
    python tools/official_validate.py out.xml --versions 1.10.4 10.5.0
    python tools/official_validate.py --matrix examples/*.yaml --mode parse

Modes:
    java-core    strict parse + MCMC instantiation + initial posterior using
                 BEAST's legacy pure-Java TreeLikelihood parser (no hmsbeagle)
    instantiate  strict parse + MCMC instantiation with the normal parsers
    parse        strict XML/XMLObject parse only
    beagle       native BEAGLE likelihood (parse + instantiation + initial
                 posterior); combine with --run for a real short chain

Short native chains:
    python tools/official_validate.py --matrix "examples/config_*.yaml" \\
        --mode beagle --run --chain-length 1000 --beagle-lib %BEAGLE_BIN%

The report records the jar path/source/SHA-256, Java version, command, exit
code and the full log for every XML/version pair.  Raw stdout/stderr are
written to ``<out>/logs/<example>_<version>.log``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy.config import ConfigParser  # noqa: E402
from beastxpy.model import ModelAssembler  # noqa: E402
from beastxpy.validator import (  # noqa: E402
    lookup_jar_details,
    run_beast_validation,
)
from beastxpy.xml_writer import write_xml  # noqa: E402

VERSIONS = ("1.10.4", "10.5.0")


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _generate(config_path: Path, version: str, out_dir: Path) -> Path:
    config = ConfigParser().parse(str(config_path))
    config.control.beast_version = version
    assembler = ModelAssembler(config)
    xml = write_xml(assembler.assemble(), beast_version=version)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{config_path.stem}_{version}.xml"
    target.write_text(xml, encoding="utf-8")
    for name, source in assembler.sidecar_files.items():
        shutil.copyfile(source, out_dir / name)
    return target


def _official_jar(version: str, allow_packaged: bool) -> Tuple[Path, Dict[str, str]]:
    details = lookup_jar_details(version)
    if details is None:
        raise SystemExit(
            f"no BEAST {version} jar found; set BEAST_{version.replace('.', '_')}_HOME"
        )
    if details["source"].startswith("packaged") and not allow_packaged:
        raise SystemExit(
            f"only the packaged project jar was found for BEAST {version} "
            f"({details['path']}); point BEAST_{version.replace('.', '_')}_HOME at an "
            "official installation or pass --allow-packaged"
        )
    return Path(details["path"]), details


def _collect(
    xml_args: Iterable[str],
    matrix_args: Iterable[str],
    out_dir: Path,
    versions: Iterable[str],
) -> List[Tuple[str, Path, str]]:
    pairs: List[Tuple[str, Path, str]] = []
    for pattern in matrix_args:
        configs = (
            sorted(Path().glob(pattern)) if not Path(pattern).is_absolute() else [Path(pattern)]
        )
        if not configs:
            raise SystemExit(f"no configs matched {pattern!r}")
        for config_path in configs:
            for version in versions:
                pairs.append(
                    (
                        config_path.stem,
                        _generate(config_path, version, out_dir / "xml"),
                        version,
                    )
                )
    for value in xml_args:
        path = Path(value)
        if not path.is_file():
            raise SystemExit(f"XML file not found: {path}")
        inferred = next((v for v in versions if v in path.stem), versions[0])
        pairs.append((path.stem, path, inferred))
    return pairs


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", nargs="*", help="XML files to validate")
    parser.add_argument("--matrix", nargs="*", default=[], help="config YAML glob(s)")
    parser.add_argument("--versions", nargs="+", default=list(VERSIONS), choices=VERSIONS)
    parser.add_argument(
        "--mode",
        default="java-core",
        choices=("java-core", "instantiate", "parse", "beagle"),
    )
    parser.add_argument("--out", default="validation_runs", help="output directory")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--allow-packaged", action="store_true")
    parser.add_argument(
        "--relaxed",
        action="store_true",
        help="disable BEAST syntax-rule strictness (codon convert workaround)",
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

    if not args.xml and not args.matrix:
        parser.error("provide XML files and/or --matrix")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    jars = {version: _official_jar(version, args.allow_packaged) for version in args.versions}
    pairs = _collect(args.xml, args.matrix, out_dir, args.versions)

    rows: List[Dict[str, Any]] = []
    details: List[Dict[str, Any]] = []
    failures = 0
    logs_dir = out_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for name, xml_path, version in pairs:
        jar, jar_info = jars[version]
        result = run_beast_validation(
            str(xml_path),
            beast_version=version,
            timeout=args.timeout,
            parse_only=args.mode == "parse",
            java_core=args.mode == "java-core",
            relaxed=args.relaxed,
            native_beagle=args.mode == "beagle",
            beagle_dir=args.beagle_lib,
            beagle_threads=args.beagle_threads,
            run_chain=args.run,
            chain_length=args.chain_length,
        )
        log_path = logs_dir / f"{name}_{version}.log"
        command_line = " ".join(result.command or [])
        log_path.write_text(
            "\n".join(
                [
                    f"# example: {name}",
                    f"# version: {version}",
                    f"# mode: {args.mode}",
                    f"# status: {result.status}",
                    f"# exit_code: {result.exit_code}",
                    f"# beagle: {result.beagle or ''}",
                    f"# beagle_version: {result.beagle_version or ''}",
                    f"# command: {command_line}",
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
        status = result.status if result.status else ("pass" if result.ok else "fail")
        if not result.ok:
            failures += 1
        rows.append(
            {
                "example": name,
                "version": version,
                "mode": args.mode,
                "status": status,
                "exit_code": result.exit_code if result.exit_code is not None else "",
                "reason": result.reason.replace("\n", " ")[:500],
                "jar_source": jar_info["source"],
                "jar_sha256": result.jar_sha256 or jar_info["sha256"],
                "java_version": result.java_version or "",
                "beagle": result.beagle or "",
                "beagle_version": result.beagle_version or "",
                "chain_length_seen": (
                    result.chain_length_seen if result.chain_length_seen is not None else ""
                ),
                "log": str(log_path),
                "xml": str(xml_path),
            }
        )
        details.append(
            {
                "example": name,
                "version": version,
                "result": result.as_dict(),
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        print(
            f"{name:36s} {version:7s} {args.mode:12s} {status:18s} " f"{result.reason[:90]}",
            flush=True,
        )

    csv_path = out_dir / "validation_matrix.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["example"])
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "validation_details.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"wrote {csv_path} ({len(rows)} rows, {failures} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
