#!/usr/bin/env python3
"""Produce replayable ``beagle``-mode Gate-3 evidence.

This is the one-command companion to the CI ``beagle-evidence`` job:

* downloads the pinned BEAGLE source release and verifies its SHA-256,
* builds it locally (or accepts ``--beagle-lib`` for an existing install),
* runs ``tools/official_validate.py --mode beagle`` over the example matrix,
* runs ``tools/validate_coverage.py --mode beagle`` over the coverage fixtures,
* writes ``evidence.json`` with every command, hash and pass/fail count.

The result is an artifact a third party can rebuild from the same inputs.  It
never downloads BEAST itself: the jars are the committed official artifacts.

Usage::

    python tools/run_beagle_evidence.py --out validation_runs/beagle_evidence
    python tools/run_beagle_evidence.py --out runs/beagle --beagle-lib /usr/local/lib
    python tools/run_beagle_evidence.py --out runs/beagle --run --chain-length 1000
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beastxpy.validator import lookup_jar_details  # noqa: E402

BEAGLE_RELEASES: Dict[str, Dict[str, str]] = {
    "10.5.0": {
        "version": "4.0.1",
        "url": "https://codeload.github.com/beagle-dev/beagle-lib/tar.gz/refs/tags/v4.0.1",
        "sha256": "9d258cd9bedd86d7c28b91587acd1132f4e01d4f095c657ad4dc93bd83d4f120",
    },
    "1.10.4": {
        "version": "3.1.2",
        "url": "https://codeload.github.com/beagle-dev/beagle-lib/tar.gz/refs/tags/v3.1.2",
        "sha256": "dd872b484a3a9f0bce369465e60ccf4e4c0cd7bd5ce41499415366019f236275",
    },
}


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _run(
    command: List[str],
    *,
    env: Dict[str, str] | None = None,
    cwd: Path | None = None,
) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, env=env, cwd=str(cwd) if cwd else None)


def _library_dir(prefix: Path) -> Path:
    for candidate in (prefix / "lib", prefix / "lib64", prefix / "lib" / "beagle"):
        if candidate.is_dir():
            return candidate
    raise SystemExit(f"no library directory under {prefix}")


def _jdk_home() -> Path:
    explicit = os.environ.get("JAVA_HOME")
    if explicit and (Path(explicit) / "bin" / "javac").is_file():
        return Path(explicit)
    javac = shutil.which("javac")
    if javac:
        return Path(javac).resolve().parent.parent
    raise SystemExit("a JDK is required to build the BEAGLE JNI library; set JAVA_HOME")


def build_beagle(out: Path, beast_version: str) -> Path:
    """Download, verify and build the BEAGLE release paired with *beast_version*."""
    release = BEAGLE_RELEASES[beast_version]
    version = release["version"]
    src_dir = out / f"beagle-src-{version}"
    prefix = out / f"beagle-prefix-{version}"
    archive = out / f"beagle-{version}.tar.gz"
    if not archive.is_file():
        out.mkdir(parents=True, exist_ok=True)
        print(f"downloading BEAGLE {version}: {release['url']}", flush=True)
        urllib.request.urlretrieve(release["url"], archive)
    digest = _sha256(archive)
    if digest != release["sha256"]:
        archive.unlink(missing_ok=True)
        raise SystemExit(
            f"BEAGLE {version} archive SHA-256 mismatch: got {digest}, "
            f"expected {release['sha256']}"
        )
    if not src_dir.is_dir():
        src_dir.mkdir()
        with tarfile.open(archive, "r:gz") as handle:
            handle.extractall(src_dir)
        roots = [path for path in src_dir.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise SystemExit(f"unexpected BEAGLE archive layout: {roots}")
        roots[0].rename(out / f"beagle-source-{version}")
        src_dir.rmdir()
        (out / f"beagle-source-{version}").rename(src_dir)
    jdk = _jdk_home()
    env = dict(os.environ)
    env["JAVA_HOME"] = str(jdk)
    env["PATH"] = str(jdk / "bin") + os.pathsep + env.get("PATH", "")
    jobs = str(os.cpu_count() or 1)
    if (src_dir / "CMakeLists.txt").is_file():
        build_dir = out / f"beagle-build-{version}"
        _run(
            [
                "cmake",
                "-S",
                str(src_dir),
                "-B",
                str(build_dir),
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_CUDA=OFF",
                f"-DJAVA_HOME={jdk}",
                f"-DCMAKE_INSTALL_PREFIX={prefix}",
            ],
            env=env,
        )
        _run(["cmake", "--build", str(build_dir), "--parallel", jobs], env=env)
        _run(["cmake", "--install", str(build_dir)], env=env)
    elif (src_dir / "configure.ac").is_file():
        # BEAGLE 3.1.x is autotools-only.
        autogen = (src_dir / "autogen.sh").resolve()
        if autogen.is_file():
            _run(["sh", str(autogen)], env=env, cwd=src_dir)
        _run(
            [
                str((src_dir / "configure").resolve()),
                f"--prefix={prefix.resolve()}",
                f"--with-jdk={jdk.resolve()}",
                "--enable-shared",
            ],
            env=env,
            cwd=src_dir,
        )
        _run(["make", "-j", jobs], env=env, cwd=src_dir)
        _run(["make", "install"], env=env, cwd=src_dir)
    else:
        raise SystemExit(f"unrecognised BEAGLE source layout in {src_dir}")
    return _library_dir(prefix)


def _environment(lib_dir: Path, version: str) -> Dict[str, str]:
    env = dict(os.environ)
    key = f"BEASTX_BEAGLE_{version.replace('.', '_')}_LIB"
    env[key] = str(lib_dir)
    loader = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    env[loader] = str(lib_dir) + (os.pathsep + env[loader] if env.get(loader) else "")
    return env


def _matrix(
    out: Path, versions: List[str], lib_dir: Path, run: bool, chain_length: int | None
) -> Tuple[int, int]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "official_validate.py"),
        "--matrix",
        "examples/config_*.yaml",
        "--mode",
        "beagle",
        "--versions",
        *versions,
        "--beagle-lib",
        str(lib_dir),
        "--out",
        str(out / "validation"),
    ]
    if run:
        command.append("--run")
    if chain_length is not None:
        command += ["--chain-length", str(chain_length)]
    env = _environment(lib_dir, versions[0])
    try:
        _run(command, env=env)
    except subprocess.CalledProcessError as exc:
        print(f"official_validate failed with exit {exc.returncode}", file=sys.stderr)
    rows: List[Dict[str, str]] = []
    csv_path = out / "validation" / "validation_matrix.csv"
    if csv_path.is_file():
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    passed = sum(1 for row in rows if row.get("status") == "executed-pass")
    return passed, len(rows)


def _coverage(
    out: Path, versions: List[str], lib_dir: Path, run: bool, chain_length: int | None
) -> Tuple[int, int]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "validate_coverage.py"),
        "--mode",
        "beagle",
        "--versions",
        *versions,
        "--beagle-lib",
        str(lib_dir),
        "--out",
        str(out / "coverage"),
    ]
    if run:
        command.append("--run")
    if chain_length is not None:
        command += ["--chain-length", str(chain_length)]
    env = _environment(lib_dir, versions[0])
    try:
        _run(command, env=env)
    except subprocess.CalledProcessError as exc:
        print(f"validate_coverage failed with exit {exc.returncode}", file=sys.stderr)
    rows: List[Dict[str, str]] = []
    csv_path = out / "coverage" / "coverage_matrix.csv"
    if csv_path.is_file():
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    passed = sum(1 for row in rows if row.get("status") == "executed-pass")
    return passed, len(rows)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="validation_runs/beagle_evidence")
    parser.add_argument("--versions", nargs="+", default=["10.5.0"], choices=("1.10.4", "10.5.0"))
    parser.add_argument("--beagle-lib", default=None, help="existing native BEAGLE directory")
    parser.add_argument("--run", action="store_true", help="run a short real chain per example")
    parser.add_argument("--chain-length", type=int, default=None)
    parser.add_argument("--skip-coverage", action="store_true")
    args = parser.parse_args(argv)
    if args.run and args.chain_length is None:
        parser.error("--run requires --chain-length (keep it short, e.g. 1000)")

    versions = [version for version in args.versions]
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if args.beagle_lib:
        lib_dir = Path(args.beagle_lib).expanduser().resolve()
        if not lib_dir.is_dir():
            raise SystemExit(f"--beagle-lib is not a directory: {lib_dir}")
        build_record: Dict[str, Any] = {"mode": "existing", "path": str(lib_dir)}
    else:
        if len(versions) != 1:
            raise SystemExit(
                "automatic BEAGLE builds are paired per BEAST version; run this "
                "script once per version or pass --beagle-lib"
            )
        if os.name == "nt":
            raise SystemExit(
                "automatic BEAGLE builds are supported on POSIX only; pass --beagle-lib"
            )
        release = BEAGLE_RELEASES[versions[0]]
        lib_dir = build_beagle(out, versions[0])
        build_record = {
            "mode": "built-from-source",
            "beast_version": versions[0],
            "version": release["version"],
            "url": release["url"],
            "sha256": release["sha256"],
        }
    libs = sorted(lib_dir.glob("*hmsbeagle*"))
    if not libs:
        raise SystemExit(f"no hmsbeagle library in {lib_dir}")

    jars = {version: lookup_jar_details(version) for version in versions}
    matrix_passed, matrix_total = _matrix(out, versions, lib_dir, args.run, args.chain_length)
    coverage_passed = coverage_total = 0
    if not args.skip_coverage:
        coverage_passed, coverage_total = _coverage(
            out, versions, lib_dir, args.run, args.chain_length
        )

    evidence = {
        "generated_at": started,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "beagle": build_record,
        "beagle_library_sha256": _sha256(libs[0]),
        "beast": {
            "versions": versions,
            "jars": {
                version: {
                    "sha256": details["sha256"] if details else None,
                    "source": details["source"] if details else None,
                }
                for version, details in jars.items()
            },
        },
        "matrix": {"passed": matrix_passed, "total": matrix_total},
        "coverage": {"passed": coverage_passed, "total": coverage_total},
        "commands": {
            "official_validate": "python tools/official_validate.py --matrix 'examples/config_*.yaml' "
            f"--mode beagle --versions {' '.join(versions)} --beagle-lib {lib_dir}",
            "validate_coverage": "python tools/validate_coverage.py --mode beagle "
            f"--versions {' '.join(versions)} --beagle-lib {lib_dir}",
        },
    }
    (out / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(
        f"beagle evidence: {matrix_passed}/{matrix_total} examples passed; "
        f"wrote {out / 'evidence.json'}"
    )
    ok = bool(matrix_total) and matrix_passed == matrix_total
    if not args.skip_coverage:
        ok = ok and bool(coverage_total) and coverage_passed == coverage_total
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
