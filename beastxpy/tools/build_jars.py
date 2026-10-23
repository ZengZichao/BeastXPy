#!/usr/bin/env python3
"""Build the packaged BEAST jars and the per-version Gate-3 validator classes.

For each reference version (``1.10.4`` and ``10.5.0``) the script can:

1. run ``ant dist`` inside ``BEASTX_REF_DIR/beast-mcmc-<version>`` using the
   JDK selected by ``JAVA_HOME`` (or ``javac`` on ``PATH``),
2. copy the produced ``build/dist/beast.jar`` to
   ``beastxpy/tools/jars/beast-<version>.jar``,
3. compile ``BeastXValidator.java`` together with every
   ``beastxpy/tools/java/bxpshim/*.java`` source against that jar into the
   version-specific ``beastxpy/tools/classes/<version>/`` directory,
4. record a timestamped build fingerprint (source digest, jar SHA-256,
   official-release URL when the hash matches, and javac version) in
   ``jars/MANIFEST.txt``, without absolute paths or user names.

The script is cross-platform: it never assumes Homebrew, POSIX ``date`` or a
``:`` classpath separator.  ``--skip-ant`` compiles against the jars that are
already present (useful on machines without Ant or the BEAST source trees);
combine it with ``--classes-dir``/``--jars-dir`` to test without touching the
committed class directories.

Environment:

* ``BEASTX_REF_DIR``: parent of ``beast-mcmc-<version>`` (default: the
  ``BeastXPy-参考软件`` directory next to this checkout).
* ``JAVA_HOME``: JDK used to run Ant and ``javac`` (fallback: ``javac`` on
  ``PATH``).  A full JDK is required; a JRE is not sufficient.
* ``ANT``: the Ant launcher (default ``ant`` on ``PATH``).

Usage::

    python beastxpy/tools/build_jars.py
    python beastxpy/tools/build_jars.py --skip-ant \\
        --classes-dir %TEMP%/bxp-classes --manifest %TEMP%/bxp-classes/MANIFEST.txt
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSIONS = ("1.10.4", "10.5.0")

#: Official release artifacts shipped in this package, for provenance reporting.
OFFICIAL_RELEASES = {
    "1.10.4": {
        "url": "https://github.com/beast-dev/beast-mcmc/releases/download/v1.10.4/BEAST.v1.10.4.zip",
        "sha256": "0f6eb4fdf7832c511aa8a1a99c233b88bec844698ae9322dabf8e74b156ae3b0",
    },
    "10.5.0": {
        "url": "https://github.com/beast-dev/beast-mcmc/releases/download/v10.5.0/BEAST.X.v10.5.0.zip",
        "sha256": "df11dea1d7118ff1570ad687621912b776fb683c5023333f3dbf2bc762ee136d",
    },
}

HERE = Path(__file__).resolve().parent
PACKAGE_TOOLS = HERE
DEFAULT_JARS_DIR = PACKAGE_TOOLS / "jars"
DEFAULT_CLASSES_DIR = PACKAGE_TOOLS / "classes"
SHIM_SRC_DIR = PACKAGE_TOOLS / "java" / "bxpshim"
VALIDATOR_SRC = PACKAGE_TOOLS / "BeastXValidator.java"


def utc_timestamp() -> str:
    """Return an RFC-3339 UTC timestamp (no platform ``date`` call)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_ref_dir() -> Path:
    env = os.environ.get("BEASTX_REF_DIR")
    if env:
        return Path(env)
    # BeastXPy-参考软件 sits next to the project directory.
    project = HERE.parents[1]
    for candidate in (
        project / "BeastXPy-参考软件",
        project.parent / "BeastXPy-参考软件",
    ):
        if candidate.exists():
            return candidate
    return project / "BeastXPy-参考软件"


def find_java_home() -> Path | None:
    """Return a JDK home containing ``javac``, or ``None``.

    ``JAVA_HOME`` wins; otherwise the directory of ``javac`` found on
    ``PATH`` is used.  No vendor- or OS-specific default is assumed.
    """
    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        base = Path(env_home)
        for name in ("javac.exe", "javac"):
            candidate = base / "bin" / name
            if candidate.is_file():
                return base
    javac = shutil.which("javac")
    if javac:
        return Path(javac).resolve().parent.parent
    return None


def java_version(java_home: Path) -> str:
    """Return the ``javac -version`` string (best effort)."""
    for name in ("javac.exe", "javac"):
        javac = java_home / "bin" / name
        if not javac.is_file():
            continue
        try:
            proc = subprocess.run(
                [str(javac), "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except OSError:
            continue
        output = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        if output:
            return output
    return "unknown"


def tree_digest(root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            hasher.update(str(path.relative_to(root)).encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(path.read_bytes())
            hasher.update(b"\0")
    return hasher.hexdigest()[:16]


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def run(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    printable = subprocess.list2cmdline([str(part) for part in cmd])
    print(f"  $ {printable}", file=sys.stderr)
    proc = subprocess.run(
        [str(part) for part in cmd],
        cwd=str(cwd),
        env=env,
        shell=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"command failed (exit {proc.returncode}): {printable}")


def clean_class_output(class_out: Path) -> None:
    """Remove stale ``.class`` files from one version directory only."""
    if not class_out.exists():
        return
    for path in class_out.rglob("*.class"):
        path.unlink()


def compile_validator(
    java_home: Path,
    jar: Path,
    class_out: Path,
    env: dict[str, str],
) -> list[Path]:
    """Compile the validator and the bxpshim parsers into ``class_out``."""
    sources = [VALIDATOR_SRC, *sorted(SHIM_SRC_DIR.glob("*.java"))]
    missing = [str(source) for source in sources if not source.is_file()]
    if missing:
        raise SystemExit(f"missing Java source(s): {', '.join(missing)}")
    class_out.mkdir(parents=True, exist_ok=True)
    clean_class_output(class_out)
    javac = None
    for name in ("javac.exe", "javac"):
        candidate = java_home / "bin" / name
        if candidate.is_file():
            javac = candidate
            break
    if javac is None:
        raise SystemExit(f"javac not found under {java_home}")
    run(
        [
            str(javac),
            "-encoding",
            "UTF-8",
            "-source",
            "1.8",
            "-target",
            "1.8",
            "-Xlint:-options",
            "-cp",
            str(jar),
            "-d",
            str(class_out),
            *(str(source) for source in sources),
        ],
        cwd=PACKAGE_TOOLS,
        env=env,
    )
    compiled = sorted(class_out.rglob("*.class"))
    if not compiled:
        raise SystemExit(f"javac produced no classes in {class_out}")
    return compiled


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build BEAST jars and per-version BeastXValidator classes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--versions", nargs="+", choices=VERSIONS, default=list(VERSIONS))
    parser.add_argument("--ref-dir", type=Path, default=None)
    parser.add_argument("--jars-dir", type=Path, default=DEFAULT_JARS_DIR)
    parser.add_argument("--classes-dir", type=Path, default=DEFAULT_CLASSES_DIR)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="build manifest path (default: <jars-dir>/MANIFEST.txt)",
    )
    parser.add_argument("--ant", default=os.environ.get("ANT", "ant"))
    parser.add_argument(
        "--skip-ant",
        action="store_true",
        help="do not run Ant; compile against the jars already in --jars-dir",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    jars_dir: Path = args.jars_dir.resolve()
    classes_dir: Path = args.classes_dir.resolve()
    manifest_path: Path = (
        args.manifest.resolve() if args.manifest is not None else jars_dir / "MANIFEST.txt"
    )
    ref_dir = (args.ref_dir or default_ref_dir()).resolve()

    java_home = find_java_home()
    if java_home is None:
        print(
            "no JDK found: set JAVA_HOME to a JDK (javac required) or put javac on PATH",
            file=sys.stderr,
        )
        return 2

    env = dict(os.environ)
    env["JAVA_HOME"] = str(java_home)
    env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")

    if not args.skip_ant and not ref_dir.exists():
        print(
            f"reference directory not found: {ref_dir} (set BEASTX_REF_DIR)",
            file=sys.stderr,
        )
        return 2

    jars_dir.mkdir(parents=True, exist_ok=True)
    classes_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = [
        "# BeastXPy BEAST jar / validator manifest (generated by build_jars.py)",
        f"# built: {utc_timestamp()}",
        f"# javac: {java_version(java_home)}",
        f"# mode: {'skip-ant' if args.skip_ant else 'ant-dist'}",
        "# This manifest records no absolute paths, user names or host names.",
        "# source=official-release: unmodified official release artifacts from the",
        "# upstream BEAST releases (byte-identical lib/beast.jar, no rebuild).",
    ]

    for version in args.versions:
        target = jars_dir / f"beast-{version}.jar"
        if args.skip_ant:
            if not target.is_file():
                print(f"jar missing (run without --skip-ant): {target}", file=sys.stderr)
                return 2
            current_sha = file_sha256(target)
            official = OFFICIAL_RELEASES.get(version, {})
            if official.get("sha256") == current_sha:
                source = "official-release"
                digest = current_sha
            else:
                source = "existing-jar"
                digest = current_sha[:16]
        else:
            tree = ref_dir / f"beast-mcmc-{version}"
            if not tree.exists():
                print(f"reference tree missing: {tree}", file=sys.stderr)
                return 2
            print(f"== building beast-{version}.jar ==", file=sys.stderr)
            run([args.ant, "dist"], cwd=tree, env=env)
            built = tree / "build" / "dist" / "beast.jar"
            if not built.is_file():
                print(f"ant dist did not produce {built}", file=sys.stderr)
                return 1
            shutil.copy2(built, target)
            source = "locally-built-from-source-tag"
            digest = tree_digest(tree / "src")

        class_out = classes_dir / version
        print(f"== compiling BeastXValidator for {version} ==", file=sys.stderr)
        compiled = compile_validator(java_home, target, class_out, env)
        manifest.append(
            f"beast-{version}.jar source={source} source_digest={digest} "
            f"sha256={file_sha256(target)} url={OFFICIAL_RELEASES.get(version, {}).get('url', 'n/a')}"
        )
        try:
            display_out = class_out.resolve().relative_to(HERE.parents[1])
        except ValueError:
            display_out = Path("beastxpy") / "tools" / "classes" / version
        manifest.append(
            f"BeastXValidator+shim compiled_against=beast-{version}.jar "
            f"out={display_out} classes={len(compiled)}"
        )

    manifest_path.write_text("\n".join(manifest) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}", file=sys.stderr)
    print("done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
