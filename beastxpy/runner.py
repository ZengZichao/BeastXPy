"""Run orchestration for BeastXPy (``run`` subcommand).

Executes a generated BEAST 1 XML against the reference ``beast.jar`` with a
per-seed working directory, seed management, checkpoint/resume support and
robust process/exit-code handling:

```console
$ beastxpy run --config config.yaml --workdir runs/demo --seeds 1,2,3
```

Steps:

1. Parse the configuration (Gate 0), then assemble the XML and run Gate 1
   (structure/semantics) and Gate 2 (calibration diagnostics) before Java is
   ever started.  A failed gate aborts the run.
2. Write a per-seed XML into ``<workdir>/<seed>/`` (with logger files renamed
   to ``trace.log`` / ``tree.log``) and launch
   ``java -cp beast-<ver>.jar dr.app.beast.BeastMain -seed S -prefix seed_S_ <xml>``
   with ``cwd=<workdir>/<seed>``.  ``-working`` is *not* used: in BEAST it is a
   boolean switch, not a value option.  ``-prefix`` is prepended to the logger
   file names from the XML, so the trace/tree logs land in the seed directory
   as ``seed_<S>_trace.log`` / ``seed_<S>_tree.log`` (recorded on
   :class:`RunResult`).
3. Parse the exit code: 0 = success, non-zero = failure; a timeout is
   recorded distinctly (``timed_out=True``, ``status="timeout"``).
4. ``--resume`` skips seeds whose ``run_meta.json`` records a successful run
   with the same *content* fingerprint, loads the newest checkpoint through
   ``-load_state`` + ``-force_resume``, appends to ``run.log`` instead of
   truncating it, and consults ``resume.state`` for the checkpoint interval.
5. ``--checkpoint-every N`` adds ``-save_every N -save_stem <dir>/checkpoint``
   so a crash can be resumed from ``checkpoint_<state>``.

Runtime premise (see ``docs/en/native-beagle.md``): analyses containing a
``<treeLikelihood>`` require a native BEAGLE (hmsbeagle) to run past the first
evaluation step.  The runner exposes a discovered native BEAGLE directory to
the JVM exactly like Gate 3 does (``-Djava.library.path`` plus
``LD_LIBRARY_PATH``/``DYLD_LIBRARY_PATH``/``PATH``); without a native BEAGLE
the BEAST process aborts on the first likelihood evaluation.  ``--java-only``
is refused outright for documents containing ``<treeLikelihood>``: BEAST's
bundled pure-Java BEAGLE does not implement ``getSiteLogLikelihoods``, so
``-java`` always aborts on them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .config import ConfigError, ConfigParser
from .models import BEASTConfig
from .diagnostics import run_diagnostics
from .model import ModelAssembler
from .reproducibility import content_fingerprint, analysis_fingerprint, fingerprint_comment
from .validator import _beagle_env, lookup_beagle_details, validate_xml_string
from .xml_writer import atomic_copy_file, atomic_write_text, write_xml

_LOGGER = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_CAPTURE = 20_000


class RunError(RuntimeError):
    """Raised when a run orchestration step fails."""


# ---------------------------------------------------------------------------
# Tool discovery (tolerant against a concurrently-rewritten validator)
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> Optional[str]:
    try:
        hasher = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


def _java_candidates() -> List[str]:
    candidates: List[str] = []
    explicit = os.environ.get("BEASTX_JAVA")
    if explicit:
        candidates.append(explicit)
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(str(Path(java_home) / "bin" / "java"))
    found = shutil.which("java")
    if found:
        candidates.append(found)
    candidates.extend(
        [
            r"C:\Program Files\Java\jdk-17\bin\java.exe",
            r"C:\Program Files\Eclipse Adoptium\jdk-17\bin\java.exe",
            "/usr/bin/java",
            "/usr/local/bin/java",
            "/opt/homebrew/bin/java",
        ]
    )
    return candidates


def _parse_java_version(output: str) -> Optional[str]:
    match = re.search(r'version "([^"]+)"', output)
    if match:
        return match.group(1)
    match = re.search(r"openjdk\s+([0-9][^\s]*)", output)
    if match:
        return match.group(1)
    return None


_SYSTEM_LOADER_DIRS = (
    "/usr/lib",
    "/usr/local/lib",
    "/lib",
    "/opt/homebrew/lib",
)
_SYSTEM_BEAGLE_NAMES = {
    "posix": ("libhmsbeagle-jni.so", "libhmsbeagle.so", "libhmsbeagle.dylib"),
    "nt": ("hmsbeagle64.dll", "hmsbeagle-jni64.dll", "hmsbeagle.dll", "hmsbeagle-jni.dll"),
}


def _system_beagle_present() -> bool:
    """True when a native hmsbeagle library sits in the default loader path.

    :func:`validator.lookup_beagle_details` follows explicit configuration
    only.  A library installed under the system loader path (``/usr/lib``,
    ``/usr/local/lib``, Windows ``PATH``) works without any environment
    variable, so the runner must not warn about a missing BEAGLE in that
    case.  Purely a filesystem scan: no subprocess is spawned, so tooling
    that intercepts ``subprocess.run`` is unaffected.
    """
    names = _SYSTEM_BEAGLE_NAMES["nt" if os.name == "nt" else "posix"]
    if os.name == "nt":
        roots = [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    else:
        roots = [Path(directory) for directory in _SYSTEM_LOADER_DIRS]
    for root in roots:
        try:
            if any((root / name).is_file() for name in names):
                return True
        except OSError:
            continue
    return False


def probe_java() -> Optional[Dict[str, str]]:
    """Locate a usable Java runtime and return ``{path, version, source}``.

    Uses :func:`beastxpy.validator.find_java` first (when it returns a usable
    path) and falls back to ``JAVA_HOME``/``BEASTX_JAVA``/``PATH``.  The probe
    accepts any JDK whose ``-version`` succeeds and mentions a version --
    vendor strings (OpenJDK/Temurin/Oracle) are not used as a gate.
    """
    candidates: List[str] = []
    try:  # pragma: no cover - depends on the validator version installed
        from .validator import find_java

        discovered = find_java()
        if discovered:
            candidates.append(str(discovered))
    except Exception:
        pass
    candidates.extend(_java_candidates())

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        resolved = candidate
        if os.path.sep not in candidate and not (os.path.altsep and os.path.altsep in candidate):
            resolved = shutil.which(candidate) or candidate
        if not Path(resolved).exists() and not shutil.which(resolved):
            continue
        try:
            proc = subprocess.run(
                [resolved, "-version"], capture_output=True, text=True, timeout=20
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            continue
        version = _parse_java_version(output)
        if version is None:
            continue
        source = _java_source(resolved)
        return {"path": resolved, "version": version, "source": source}
    return None


def _same_executable(candidate: str, expected: Optional[str]) -> bool:
    """True when two paths name the same executable (symlinks resolved)."""
    if not expected:
        return False
    try:
        return os.path.samefile(candidate, expected)
    except OSError:
        try:
            return Path(candidate).resolve() == Path(expected).resolve()
        except OSError:
            return False


def _java_source(resolved: str) -> str:
    """Attribute a resolved Java path without prefix guessing."""
    explicit = os.environ.get("BEASTX_JAVA")
    if explicit and _same_executable(resolved, explicit):
        return "BEASTX_JAVA"
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        executable = "java.exe" if os.name == "nt" else "java"
        if _same_executable(resolved, str(Path(java_home) / "bin" / executable)):
            return "JAVA_HOME"
    return "PATH"


def resolve_java_executable() -> Optional[str]:
    """Convenience wrapper returning only the Java executable path."""
    info = probe_java()
    return info["path"] if info else None


def _jar_candidates(version: str) -> List[Tuple[str, str]]:
    """Ordered ``(path, source)`` candidates for a BEAST version."""
    tag = version.replace(".", "_")
    candidates: List[Tuple[str, str]] = []

    # 1. Packaged jar (validator lookup, tolerant).
    try:
        from .validator import lookup_jar

        packaged = lookup_jar(version)
        if packaged:
            candidates.append((str(packaged), "packaged"))
    except Exception:
        pass

    # 2. Version-specific environment variables.
    for env_key in (f"BEASTX_JAR_{tag}", f"BEAST_{tag}_JAR", "BEASTX_JAR"):
        value = os.environ.get(env_key)
        if value:
            path = Path(value)
            if path.is_dir():
                path = path / "lib" / "beast.jar"
            candidates.append((str(path), env_key))

    # 3. Installation directories (BEAST 1.10.4 / BEAST X 10.5.0).
    home_dirs: List[Path] = []
    for env_key in (f"BEAST_{tag}_HOME", f"BEASTX_{tag}_HOME", "BEAST_HOME"):
        value = os.environ.get(env_key)
        if value:
            home_dirs.append(Path(value))
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    home = Path.home()
    for root in (program_files, program_files_x86):
        home_dirs.extend(
            [
                Path(root) / f"BEAST v{version}",
                Path(root) / f"BEAST X v{version}",
                Path(root) / "BEAST" / f"v{version}",
            ]
        )
    home_dirs.extend(
        [
            home / f"BEAST v{version}",
            home / f"BEAST X v{version}",
            Path("/Applications") / f"BEAST v{version}",
            Path("/Applications") / f"BEAST X v{version}",
            Path("/opt") / f"BEAST v{version}",
        ]
    )
    for directory in home_dirs:
        candidates.append((str(directory / "lib" / "beast.jar"), f"install:{directory}"))

    # 4. Source checkout fallback.
    ref_dir = os.environ.get("BEASTX_REF_DIR")
    if ref_dir:
        candidates.append(
            (
                str(Path(ref_dir) / f"beast-mcmc-{version}" / "build" / "dist" / "beast.jar"),
                "BEASTX_REF_DIR",
            )
        )

    # 5. Packaged jar path itself (even if lookup_jar was replaced).
    candidates.append(
        (str(Path(__file__).parent / "tools" / "jars" / f"beast-{version}.jar"), "packaged")
    )
    return candidates


def resolve_jar_details(version: str) -> Optional[Dict[str, str]]:
    """Return ``{path, source, sha256}`` for the BEAST jar of ``version``."""
    for path_str, source in _jar_candidates(version):
        path = Path(path_str)
        if path.is_file():
            return {
                "path": str(path),
                "source": source,
                "sha256": _sha256_file(path) or "",
            }
    return None


def resolve_jar(version: str) -> Optional[Path]:
    """Return the BEAST jar path for ``version`` (or ``None``)."""
    details = resolve_jar_details(version)
    return Path(details["path"]) if details else None


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Result of one ``beast`` invocation."""

    seed: int
    workdir: str
    exit_code: Optional[int]
    command: List[str]
    log_path: str
    ok: bool
    stdout: str = ""
    stderr: str = ""
    elapsed_seconds: float = 0.0
    timed_out: bool = False
    status: str = "ok"  # ok | failed | timeout | skipped
    trace_log_path: Optional[str] = None
    tree_log_path: Optional[str] = None
    checkpoint_files: List[str] = field(default_factory=list)
    archived_logs: List[str] = field(default_factory=list)


@dataclass
class RunSummary:
    """Aggregated result of a multi-seed run."""

    results: List[RunResult] = field(default_factory=list)
    workdir: str = ""
    fingerprint: str = ""
    beast_version: str = "1.10.4"
    overall_ok: bool = True
    content_fingerprint: str = ""
    seeds: List[int] = field(default_factory=list)
    java_path: str = ""
    jar_path: str = ""
    jar_sha256: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "workdir": self.workdir,
            "fingerprint": self.fingerprint,
            "content_fingerprint": self.content_fingerprint,
            "beast_version": self.beast_version,
            "seeds": self.seeds,
            "overall_ok": self.overall_ok,
            "java_path": self.java_path,
            "jar_path": self.jar_path,
            "jar_sha256": self.jar_sha256,
            "results": [
                {
                    "seed": r.seed,
                    "workdir": r.workdir,
                    "exit_code": r.exit_code,
                    "command": r.command,
                    "log_path": r.log_path,
                    "ok": r.ok,
                    "status": r.status,
                    "timed_out": r.timed_out,
                    "elapsed_seconds": r.elapsed_seconds,
                    "trace_log_path": r.trace_log_path,
                    "tree_log_path": r.tree_log_path,
                    "checkpoint_files": r.checkpoint_files,
                    "archived_logs": r.archived_logs,
                }
                for r in self.results
            ],
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class BeastRunner:
    """Orchestrate BEAST runs against the packaged beast jar."""

    def __init__(
        self,
        config_path: str,
        workdir: str = "runs",
        seeds: Optional[List[int]] = None,
        force: bool = False,
        java_only: bool = False,
        resume: bool = False,
        checkpoint_every: Optional[int] = None,
        beast_version: Optional[str] = None,
        timeout: int = 86400,
        rerun_successful: bool = False,
        parallel: int = 1,
    ) -> None:
        self.config_path = Path(config_path)
        self.workdir = Path(workdir)
        self._requested_seeds = list(seeds) if seeds is not None else None
        self.seeds: List[int] = []
        self.force = force
        self.java_only = java_only
        self.resume = resume
        self.checkpoint_every = checkpoint_every
        self.requested_beast_version = beast_version
        self.timeout = int(timeout)
        self.rerun_successful = rerun_successful
        self.parallel = int(parallel)
        self.config: Optional[BEASTConfig] = None
        self.beast_version = "1.10.4"
        self.analysis_name = "analysis"
        self.java_path = ""
        self.beagle_details: Optional[Dict[str, str]] = None

    # ------------------------------------------------------------------
    def run(self) -> RunSummary:
        """Execute the full run pipeline; returns a summary."""
        self._prepare_config()
        assert self.config is not None

        java_info = probe_java()
        if java_info is None:
            raise RunError(
                "no usable Java runtime found; install a JDK (>= 8), set "
                "JAVA_HOME or BEASTX_JAVA, or put 'java' on PATH"
            )
        self.java_path = java_info["path"]
        jar_info = resolve_jar_details(self.beast_version)
        if jar_info is None:
            raise RunError(
                f"no beast-{self.beast_version}.jar found; install BEAST "
                f"v{self.beast_version} (set BEAST_{self.beast_version.replace('.', '_')}_HOME) "
                "or set BEASTX_JAR and run tools/build_jars.py"
            )

        xml_content, sidecars = self._assemble()
        if self.java_only and "<treeLikelihood" in xml_content:
            raise RunError(
                "--java-only cannot run this analysis: BEAST's bundled "
                "pure-Java BEAGLE does not implement getSiteLogLikelihoods, "
                "so every document containing <treeLikelihood> aborts under "
                "-java. Install a native hmsbeagle library (set "
                f"BEASTX_BEAGLE_{self.beast_version.replace('.', '_')}_LIB) "
                "and drop --java-only."
            )
        self.workdir.mkdir(parents=True, exist_ok=True)
        fingerprint = analysis_fingerprint(self.config)
        content = content_fingerprint(self.config)
        xml_path = self.workdir / f"{self.analysis_name}.xml"
        if xml_path.exists() and not self.force and not self.resume:
            raise RunError(
                f"{xml_path} already exists; use --force to overwrite or --resume to continue"
            )
        atomic_write_text(str(xml_path), xml_content)
        for name, source in sidecars.items():
            atomic_copy_file(str(source), str(self.workdir / name))

        # Expose a discovered native BEAGLE to the run exactly like Gate 3
        # does: the JVM needs -Djava.library.path to load the JNI library,
        # and the loader (dlopen/LoadLibrary) needs the directory on
        # LD_LIBRARY_PATH/DYLD_LIBRARY_PATH/PATH to resolve the BEAGLE
        # resource plugins, which are dlopened by bare soname.
        self.beagle_details = (
            lookup_beagle_details(self.beast_version) if not self.java_only else None
        )
        beagle_env: Dict[str, str] = {}
        beagle_flags: List[str] = []
        if self.beagle_details is not None:
            beagle_env, beagle_flags = _beagle_env(Path(self.beagle_details["path"]))
        elif not _system_beagle_present():
            _LOGGER.warning(
                "no native hmsbeagle library discovered for BEAST %s; if the "
                "run fails to load BEAGLE, set BEASTX_BEAGLE_%s_LIB to the "
                "directory holding the library (see docs, native-beagle).",
                self.beast_version,
                self.beast_version.replace(".", "_"),
            )

        summary = RunSummary(
            workdir=str(self.workdir),
            fingerprint=fingerprint,
            content_fingerprint=content,
            beast_version=self.beast_version,
            seeds=list(self.seeds),
            java_path=java_info["path"],
            jar_path=jar_info["path"],
            jar_sha256=jar_info.get("sha256", ""),
        )

        def run_seed(seed: int) -> RunResult:
            run_dir = self.workdir / str(seed)
            run_dir.mkdir(parents=True, exist_ok=True)

            if self.resume and not self.rerun_successful:
                previous = self._read_meta(run_dir)
                if previous and self._meta_matches(previous, fingerprint, content):
                    if previous.get("exit_code") == 0:
                        skipped = self._skipped_result(seed, run_dir, previous)
                        self._write_run_meta(run_dir, skipped, fingerprint, content, jar_info)
                        return skipped

            result = self._run_one(
                seed,
                run_dir,
                java_info["path"],
                Path(jar_info["path"]),
                beagle_env,
                beagle_flags,
                sidecars,
            )
            self._write_run_meta(run_dir, result, fingerprint, content, jar_info)
            self._write_resume_state(run_dir, result)
            return result

        if self.parallel == 1 or len(self.seeds) <= 1:
            results = [run_seed(seed) for seed in self.seeds]
        else:
            workers = min(self.parallel, len(self.seeds))
            _LOGGER.info(
                "running %d seed(s) with %d parallel worker(s)",
                len(self.seeds),
                workers,
            )
            with ThreadPoolExecutor(max_workers=workers) as executor:
                # map preserves the seed order in summary.results.
                results = list(executor.map(run_seed, self.seeds))

        summary.results = results
        summary.overall_ok = all(r.ok for r in results)
        return summary

    # ------------------------------------------------------------------
    def _prepare_config(self) -> None:
        if not self.config_path.exists():
            raise RunError(f"configuration file not found: {self.config_path}")
        try:
            parser = ConfigParser()
            config = parser.parse(str(self.config_path))
            self.config = config
        except ConfigError as exc:
            raise RunError(f"configuration error: {exc}") from exc

        requested = self.requested_beast_version or config.control.beast_version
        if requested == "both":
            raise RunError(
                "control.beast_version is 'both' but a run needs one concrete "
                "BEAST version; pass --beast-version 1.10.4 or --beast-version 10.5.0 "
                "(the generated documents are identical in model content)"
            )
        if requested not in ("1.10.4", "10.5.0"):
            raise RunError(f"unsupported BEAST version {requested!r} " "(valid: 1.10.4, 10.5.0)")
        self.beast_version = str(requested)
        # Keep the config, fingerprint and metadata on the *same* concrete
        # version (no silent downgrade that disagrees with the meta).
        config.control.beast_version = self.beast_version

        name = config.analysis_name
        if not _SAFE_NAME_RE.match(name) or Path(name).name != name:
            raise RunError(
                f"analysis_name {name!r} is not a safe file name; it must match "
                f"{_SAFE_NAME_RE.pattern} (no path separators or '..')"
            )
        self.analysis_name = name

        if self.checkpoint_every is not None:
            if self.checkpoint_every <= 0:
                raise RunError(
                    f"checkpoint_every must be a positive integer, got {self.checkpoint_every}"
                )
        self.seeds = self._resolve_seeds()
        if self.parallel < 1:
            raise RunError(
                f"--parallel must be >= 1, got {self.parallel}; use 1 for " "sequential execution"
            )

    def _resolve_seeds(self) -> List[int]:
        assert self.config is not None
        if self._requested_seeds is None:
            default = self.config.control.seed
            if default is None:
                return [1]
            return [int(default)]
        if not self._requested_seeds:
            raise RunError("empty seed list: pass at least one seed")
        seeds: List[int] = []
        for raw in self._requested_seeds:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise RunError(f"invalid seed {raw!r} (expected an integer)")
            if raw < 0 or raw > 2**31 - 1:
                raise RunError(f"seed {raw} out of range 0..2**31-1")
            if raw not in seeds:
                seeds.append(raw)
        return seeds

    def _assemble(self) -> Tuple[str, Dict[str, Path]]:
        """Assemble the XML and run Gate 1 + Gate 2."""
        assert self.config is not None
        assembler = ModelAssembler(self.config)
        root = assembler.assemble()
        comments: List[str] = []
        if self.config.output.embed_fingerprint is not False:
            comments.append(fingerprint_comment(self.config))
        xml_str = write_xml(
            root,
            beast_version=self.beast_version,
            header_comments=comments,
        )

        findings = validate_xml_string(
            xml_str,
            beast_version=self.beast_version,
            strict=self.config.control.strict,
        )
        errors = [f for f in findings if f.severity == "error"]
        if errors:
            raise RunError("Gate 1 failed: " + "; ".join(f"{f.path}: {f.message}" for f in errors))

        report = run_diagnostics(
            self.config,
            overlap_threshold=float((self.config.diagnostics or {}).get("overlap_threshold", 0.15)),
            overlap_measure=self.config.control.overlap_measure,
            strict=self.config.control.strict,
        )
        if report.has_errors:
            raise RunError(
                "Gate 2 failed: "
                + "; ".join(f.message for f in report.findings if f.severity == "error")
            )
        return xml_str, dict(assembler.sidecar_files)

    # ------------------------------------------------------------------
    @staticmethod
    def _read_meta(run_dir: Path) -> Optional[Dict[str, Any]]:
        meta_path = run_dir / "run_meta.json"
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _meta_matches(previous: Dict[str, Any], fingerprint: str, content: str) -> bool:
        previous_content = previous.get("content_fingerprint")
        if previous_content:
            return previous_content == content
        # Backward compatibility: old metadata only stored the versioned hash.
        return previous.get("fingerprint") == fingerprint

    def _skipped_result(self, seed: int, run_dir: Path, previous: Dict[str, Any]) -> RunResult:
        return RunResult(
            seed=seed,
            workdir=str(run_dir),
            exit_code=0,
            command=[],
            log_path=str(run_dir / "run.log"),
            ok=True,
            status="skipped",
            trace_log_path=previous.get("trace_log_path"),
            tree_log_path=previous.get("tree_log_path"),
            checkpoint_files=list(previous.get("checkpoint_files") or []),
        )

    @staticmethod
    def _rewrite_logger_names(xml_text: str) -> str:
        """Rename BeastXPy's own loggers to ``trace.log``/``tree.log``.

        BEAST prepends ``-prefix`` to these names; with ``-prefix seed_S_``
        the actual artefacts are deterministic (``seed_S_trace.log`` /
        ``seed_S_tree.log``).  Only the loggers BeastXPy itself emits
        (``id="fileLog"`` and ``id="treeFileLog"``) are touched: loggers
        injected through ``--allow-custom-fragments`` keep their own file
        names, so several custom loggers can never be collapsed onto one
        file.  Gate 1 separately rejects duplicate output file names.
        """
        own_outputs = {"fileLog": "trace.log", "treeFileLog": "tree.log"}

        def rewrite(match: "re.Match[str]") -> str:
            tag = match.group(0)
            id_match = re.search(r'\bid="([^"]*)"', tag)
            if not id_match:
                return tag
            target = own_outputs.get(id_match.group(1))
            if target is None:
                return tag
            return re.sub(
                r'(\bfileName=")[^"]*(")',
                lambda file_match: file_match.group(1) + target + file_match.group(2),
                tag,
                count=1,
            )

        # Quote-aware so a literal '>' inside an attribute value (legal XML)
        # cannot truncate the tag before its real closing bracket.
        return re.sub(r"<(?:log|logTree)\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", rewrite, xml_text)

    def _checkpoint_files(self, run_dir: Path) -> List[str]:
        return sorted(str(path) for path in run_dir.glob("checkpoint_*") if path.is_file())

    def _latest_checkpoint(self, run_dir: Path) -> Optional[Path]:
        states = [path for path in run_dir.glob("checkpoint_*") if path.is_file()]
        if not states:
            return None
        return max(states, key=lambda p: p.stat().st_mtime)

    def _run_one(
        self,
        seed: int,
        run_dir: Path,
        java: str,
        jar: Path,
        beagle_env: Dict[str, str],
        beagle_flags: List[str],
        sidecars: Optional[Dict[str, Path]] = None,
    ) -> RunResult:
        name = self.analysis_name
        prefix = f"seed_{seed}_"
        run_xml = run_dir / f"{name}.xml"
        log_path = run_dir / "run.log"
        trace_path = run_dir / f"{prefix}trace.log"
        tree_path = run_dir / f"{prefix}tree.log"

        # Per-seed XML with deterministic logger names (never BEAST's
        # "-working"; it is a boolean switch, not a directory option).
        header = self.workdir / f"{name}.xml"
        xml_text = header.read_text(encoding="utf-8")
        atomic_write_text(str(run_xml), self._rewrite_logger_names(xml_text))
        for name, source in (sidecars or {}).items():
            atomic_copy_file(str(source), str(run_dir / name))

        args: List[str] = list(beagle_flags)
        args += ["-cp", str(jar), "dr.app.beast.BeastMain"]
        args += ["-seed", str(seed), "-overwrite", "-prefix", prefix]
        if self.java_only:
            args.append("-java")

        checkpoint_every = self.checkpoint_every
        if checkpoint_every is None:
            state_meta = self._read_resume_state(run_dir)
            if state_meta and isinstance(state_meta.get("checkpoint_every"), int):
                checkpoint_every = state_meta["checkpoint_every"]
        if checkpoint_every is not None:
            args += [
                "-save_every",
                str(checkpoint_every),
                "-save_stem",
                str((run_dir / "checkpoint").resolve()),
            ]
        if self.resume:
            latest = self._latest_checkpoint(run_dir)
            if latest is not None:
                args += ["-load_state", str(latest.resolve()), "-force_resume"]

        # Never truncate previous artefacts when resuming: move the old logs
        # aside so LogCombiner can merge part1/part2 chains later.
        archived: List[str] = []
        if self.resume:
            for existing in (trace_path, tree_path):
                if existing.exists():
                    index = 1
                    while True:
                        candidate = existing.with_name(f"{existing.name}.part{index}")
                        if not candidate.exists():
                            break
                        index += 1
                    existing.replace(candidate)
                    archived.append(str(candidate))

        args.append(str(run_xml.resolve()))

        mode = "a" if self.resume and log_path.exists() else "w"
        start = time.monotonic()
        timed_out = False
        exit_code: Optional[int] = None
        stdout = ""
        stderr = ""
        with open(log_path, mode, encoding="utf-8", newline="\n") as log_handle:
            if mode == "a":
                log_handle.write(f"\n===== resume at {time.strftime('%Y-%m-%dT%H:%M:%S')} =====\n")
            try:
                child_env = dict(os.environ)
                child_env.update(beagle_env)
                proc = subprocess.run(
                    [java] + args,
                    capture_output=True,
                    text=True,
                    cwd=str(run_dir),
                    timeout=self.timeout,
                    env=child_env,
                )
                exit_code = proc.returncode
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
                log_handle.write(stdout)
                log_handle.write(stderr)
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                exit_code = 124
                stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
                message = (
                    f"\n[BeastXPy] TIMEOUT after {self.timeout} s; the BEAST "
                    "process was killed and any partial logs are incomplete.\n"
                )
                log_handle.write(stdout)
                log_handle.write(stderr)
                log_handle.write(message)
                stderr += message
        elapsed = time.monotonic() - start

        status = "timeout" if timed_out else ("ok" if exit_code == 0 else "failed")
        checkpoint_files = self._checkpoint_files(run_dir)
        return RunResult(
            seed=seed,
            workdir=str(run_dir),
            exit_code=exit_code,
            command=[java] + args,
            log_path=str(log_path),
            ok=exit_code == 0 and not timed_out,
            stdout=stdout[-_MAX_CAPTURE:],
            stderr=stderr[-_MAX_CAPTURE:],
            elapsed_seconds=elapsed,
            timed_out=timed_out,
            status=status,
            trace_log_path=str(trace_path) if trace_path.exists() else None,
            tree_log_path=str(tree_path) if tree_path.exists() else None,
            checkpoint_files=checkpoint_files,
            archived_logs=archived,
        )

    def _write_run_meta(
        self,
        run_dir: Path,
        result: RunResult,
        fingerprint: str,
        content: str,
        jar_info: Dict[str, str],
    ) -> None:
        payload = {
            "fingerprint": fingerprint,
            "content_fingerprint": content,
            "seed": result.seed,
            "seeds": list(self.seeds),
            "status": result.status,
            "timed_out": result.timed_out,
            "beast_version": self.beast_version,
            "beastxpy_version": __version__,
            "java_path": self.java_path,
            "jar_path": jar_info.get("path", ""),
            "jar_sha256": jar_info.get("sha256", ""),
            "command": result.command,
            "exit_code": result.exit_code,
            "elapsed_seconds": round(result.elapsed_seconds, 3),
            "trace_log_path": result.trace_log_path,
            "tree_log_path": result.tree_log_path,
            "checkpoint_files": result.checkpoint_files,
            "archived_logs": result.archived_logs,
            "wall_clock": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        atomic_write_text(str(run_dir / "run_meta.json"), json.dumps(payload, indent=2) + "\n")

    @staticmethod
    def _read_resume_state(run_dir: Path) -> Optional[Dict[str, Any]]:
        path = run_dir / "resume.state"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_resume_state(self, run_dir: Path, result: RunResult) -> None:
        payload = {
            "checkpoint_every": self.checkpoint_every,
            "last_state": (result.checkpoint_files[-1] if result.checkpoint_files else None),
            "seed": result.seed,
            "status": result.status,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        atomic_write_text(str(run_dir / "resume.state"), json.dumps(payload, indent=2) + "\n")


def run_analysis_command(
    config_path: str,
    workdir: str = "runs",
    seeds: Optional[List[int]] = None,
    force: bool = False,
    java_only: bool = False,
    resume: bool = False,
    checkpoint_every: Optional[int] = None,
    beast_version: Optional[str] = None,
    timeout: int = 86400,
    rerun_successful: bool = False,
    parallel: int = 1,
) -> RunSummary:
    """Convenience entry point for the ``run`` subcommand."""
    runner = BeastRunner(
        config_path=config_path,
        workdir=workdir,
        seeds=seeds,
        force=force,
        java_only=java_only,
        resume=resume,
        checkpoint_every=checkpoint_every,
        beast_version=beast_version,
        timeout=timeout,
        rerun_successful=rerun_successful,
        parallel=parallel,
    )
    return runner.run()
