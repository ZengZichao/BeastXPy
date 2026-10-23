"""Environment lock files: record a run's environment, then verify or replay it.

``beastxpy lock`` writes a small, human-readable JSON document that pins
everything a divergence-time result depends on *besides* the model itself:
the BeastXPy and Python versions, the platform, the BEAST version and the
SHA-256 of the exact jar that was resolved, the Java version, the native
BEAGLE library and its SHA-256, the versions of the key scientific
dependencies, the SHA-256 of the configuration file and the RNG seeds.

``beastxpy reproduce --check`` re-probes the machine and reports, field by
field, what still matches and what does not.  ``--run`` re-executes the
analysis through :class:`~beastxpy.runner.BeastRunner` with the recorded seeds.

Privacy and security
--------------------

A lock file is meant to be committed and shared, so it is deliberately
**not** a description of one person's machine layout:

* no absolute paths -- file identities are ``C.redacted_path()`` basenames
  plus SHA-256 digests, and verification uses the digest, never the path;
* no configuration content -- only its size, digest and derived fingerprints;
* no environment variables, no user names, no host names;
* discovery *sources* are reduced to their label (``env:BEASTX_JAR`` keeps its
  name; ``install:F:\\...`` keeps only ``install``);
* before anything is written, :func:`assert_shareable` walks the finished
  document and refuses to emit it if any string looks like an absolute path or
  a credential.  The check is a hard gate, not a warning.

Tamper detection
----------------

The document carries ``integrity.field_digests`` (a SHA-256 per tracked field)
and ``integrity.payload_sha256`` (a SHA-256 over the whole document minus the
integrity block).  Editing any recorded value without resealing is reported as
a tampered lock (exit 3) and the offending field is named; a lock that is
intact but describes a different machine is reported as a mismatch (exit 2).

What a lock does NOT guarantee: matching digests prove the *inputs* are the
same, not that a stochastic run reproduced a particular trace.  ``reproduce
--run`` re-executes with the recorded seeds, which is deterministic only for
the same jar, JVM, BEAGLE build and platform.
"""

from __future__ import annotations

import importlib
import json
import os
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import _contracts as C
from .compare import (
    BeagleEnvironment,
    beagle_runtime_environment,
    probe_beagle_environment,
)
from .config import ConfigError, ConfigParser
from .reproducibility import analysis_fingerprint, content_fingerprint, data_digest
from .runner import BeastRunner, RunError
from .utils import canonical_json, sha256_hex
from .validator import lookup_jar_details

LOCK_VERSION = 1

INTEGRITY_KEY = "integrity"

#: Key dependencies whose versions are pinned.  ``distribution`` is the PyPI
#: name used by :mod:`importlib.metadata`, ``module`` the import name that is
#: tried as a fallback.
TRACKED_DEPENDENCIES: Tuple[Tuple[str, str, str], ...] = (
    ("pyyaml", "PyYAML", "yaml"),
    ("numpy", "numpy", "numpy"),
    ("scipy", "scipy", "scipy"),
    ("biopython", "biopython", "Bio"),
    ("matplotlib", "matplotlib", "matplotlib"),
    ("defusedxml", "defusedxml", "defusedxml"),
)

#: Fields compared by ``reproduce --check``.  Every one of them is also
#: digest-protected, so a hand edit is reported as tampering rather than as an
#: environment change.
TRACKED_FIELDS: Tuple[str, ...] = (
    "tool.version",
    "python.version",
    "python.platform",
    "python.machine",
    "beast.version",
    "beast.jar_name",
    "beast.jar_sha256",
    "java.version",
    "java.vendor",
    "java.arch_data_model",
    "java.executable_sha256",
    "beagle.available",
    "beagle.expected_version",
    "beagle.observed_version",
    "beagle.library_name",
    "beagle.library_sha256",
    "dependencies.pyyaml",
    "dependencies.numpy",
    "dependencies.scipy",
    "dependencies.biopython",
    "dependencies.matplotlib",
    "dependencies.defusedxml",
)

#: Fields that can only be verified when the configuration file is supplied.
CONFIG_FIELDS: Tuple[str, ...] = (
    "config.content_sha256",
    "config.size_bytes",
    "config.analysis_fingerprint",
)

REDACTION_POLICY = (
    "no absolute paths, no user names, no host names, no environment variable "
    "values and no configuration content; every file is identified by its "
    "basename and SHA-256 digest"
)

_SECRET_KEY_RE = re.compile(
    r"(?i)(password|passwd|pwd|token|api[_-]?key|apikey|secret|credential|"
    r"authorization|private[_-]?key|access[_-]?key|bearer)"
)
_SECRET_VALUE_RE = re.compile(
    r"(ghp_[A-Za-z0-9]{10,}|gho_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,}|"
    r"sk-[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"Bearer\s+[A-Za-z0-9._~+/-]+=*|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_ABSOLUTE_PATH_RE = re.compile(
    r"[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._$-]+|(?:^|[\s\"'(=,;:])/(?:[A-Za-z0-9._+-]+/)+"
)


class LockError(C.ContractError):
    """A ``lock``/``reproduce`` problem that carries the next step to take."""


class LockTamperedError(LockError):
    """The lock file does not match its own integrity digests."""

    def __init__(
        self, message: str, *, fields: Optional[Sequence[str]] = None, fix: Optional[str] = None
    ) -> None:
        super().__init__(message, fix=fix)
        self.fields: List[str] = list(fields or [])


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def get_path(document: Any, dotted: str) -> Any:
    """Read ``a.b.c`` out of nested mappings (``None`` when absent)."""
    current = document
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _sha256_file(path: Path) -> Optional[str]:
    """SHA-256 of a file, or ``None`` when it cannot be read."""
    try:
        from .validator import _sha256_file as validator_sha256

        return validator_sha256(path)
    except OSError:
        return None
    except Exception:  # pragma: no cover - defensive
        return None


def redact_source(source: Optional[str]) -> Optional[str]:
    """Reduce a discovery-source label to something shareable.

    ``env:BEASTX_JAR_1_10_4`` is already free of paths and is kept;
    ``install:F:\\Program Files\\BEAST v1.10.4`` is reduced to ``install``.
    """
    if not source:
        return None
    text = str(source)
    if ":" not in text:
        return text
    label, _, rest = text.partition(":")
    if not rest.strip():
        return label
    if _ABSOLUTE_PATH_RE.search(rest) or "/" in rest or "\\" in rest:
        return label
    return text


def dependency_versions(
    tracked: Sequence[Tuple[str, str, str]] = TRACKED_DEPENDENCIES,
) -> Dict[str, Optional[str]]:
    """Versions of the key dependencies (``None`` when not installed)."""
    found: Dict[str, Optional[str]] = {}
    for key, distribution, module in tracked:
        version: Optional[str] = None
        try:
            metadata = importlib.import_module("importlib.metadata")
            version = metadata.version(distribution)
        except Exception:
            version = None
        if version is None:
            try:
                loaded = importlib.import_module(module)
                candidate = getattr(loaded, "__version__", None)
                version = str(candidate) if candidate else None
            except Exception:
                version = None
        found[key] = version
    return found


# ---------------------------------------------------------------------------
# Privacy gate
# ---------------------------------------------------------------------------


def _walk_strings(value: Any, prefix: str = "") -> List[Tuple[str, str]]:
    """Yield ``(dotted path, string)`` for every string in the document."""
    found: List[Tuple[str, str]] = []
    if isinstance(value, str):
        found.append((prefix or "<root>", value))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.append((f"{prefix}.{key}" if prefix else str(key), str(key)))
            found.extend(_walk_strings(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_walk_strings(item, f"{prefix}[{index}]"))
    return found


def find_privacy_problems(document: Any) -> List[Dict[str, str]]:
    """Every string in ``document`` that looks like a path or a credential."""
    problems: List[Dict[str, str]] = []
    for where, text in _walk_strings(document):
        # The lock's own ``redaction`` block describes what was deliberately
        # left out ("secrets: none", "absolute_paths: none").  Its field names
        # look like the very thing it promises not to contain, so scanning it
        # would make every lock reject itself.
        if where == "redaction" or where.startswith("redaction."):
            continue
        leaf = where.rsplit(".", 1)[-1].split("[")[0]
        if _SECRET_KEY_RE.search(leaf):
            problems.append({"path": where, "kind": "secret-key", "value": text[:64]})
            continue
        if _SECRET_VALUE_RE.search(text):
            problems.append({"path": where, "kind": "secret-value", "value": text[:64]})
            continue
        if _ABSOLUTE_PATH_RE.search(text):
            problems.append({"path": where, "kind": "absolute-path", "value": text[:120]})
    return problems


def assert_shareable(document: Any) -> None:
    """Refuse to emit a lock that leaks a path or a credential."""
    problems = find_privacy_problems(document)
    if problems:
        detail = "; ".join(
            "%s: %s (%r)" % (item["path"], item["kind"], item["value"]) for item in problems[:5]
        )
        raise LockError(
            "the lock file would not be shareable: " + detail,
            fix=(
                "record file identities as basenames plus SHA-256 digests "
                "(C.redacted_path) and never copy a configuration value, an "
                "environment variable or an absolute path into the lock."
            ),
        )


# ---------------------------------------------------------------------------
# Environment collection
# ---------------------------------------------------------------------------


def _java_facts(beagle_env: BeagleEnvironment) -> Dict[str, Any]:
    from .runner import probe_java

    facts: Dict[str, Any] = {
        "version": None,
        "vendor": None,
        "arch_data_model": beagle_env.jvm_arch_data_model,
        "executable_name": None,
        "executable_sha256": None,
        "source": None,
    }
    info = probe_java()
    if info is None:
        return facts
    path = Path(info["path"])
    facts["version"] = info.get("version")
    facts["source"] = info.get("source")
    facts["executable_name"] = C.redacted_path(path)
    facts["executable_sha256"] = _sha256_file(path)
    try:
        from .validator import find_java_runtime

        runtime = find_java_runtime()
        facts["vendor"] = runtime.vendor
    except Exception:  # pragma: no cover - diagnostics only
        facts["vendor"] = None
    return facts


def _beagle_facts(beagle_env: BeagleEnvironment, threads: int) -> Dict[str, Any]:
    return {
        "required": bool(beagle_env.required),
        "available": bool(beagle_env.available),
        "expected_version": beagle_env.expected_version,
        "observed_version": beagle_env.observed_version,
        "library_name": (
            C.redacted_path(beagle_env.library_name) if beagle_env.library_name else None
        ),
        "library_sha256": beagle_env.library_sha256,
        "source": redact_source(beagle_env.source),
        "threads": int(threads),
        "reason": beagle_env.reason or None,
    }


def _beast_facts(beast_version: str) -> Dict[str, Any]:
    details = lookup_jar_details(beast_version)
    if details is None:
        return {
            "version": beast_version,
            "jar_name": None,
            "jar_sha256": None,
            "jar_size_bytes": None,
            "jar_source": None,
            "jar_resolved": False,
            "note": (
                "no beast-%s.jar was resolved on this machine; the lock records "
                "the requested version only" % beast_version
            ),
        }
    path = Path(details["path"])
    return {
        "version": beast_version,
        "jar_name": C.redacted_path(path),
        "jar_sha256": details.get("sha256") or _sha256_file(path),
        "jar_size_bytes": int(path.stat().st_size) if path.is_file() else None,
        "jar_source": redact_source(details.get("source")),
        "jar_resolved": True,
        "note": (
            "the digest is of the jar BeastXPy actually resolved (%s), which may "
            "differ from an official BEAST distribution rebuild"
            % redact_source(details.get("source"))
        ),
    }


def _config_facts(config_path: Path, config: Any) -> Dict[str, Any]:
    raw = config_path.read_bytes()
    taxa = set()
    for alignment in config.all_alignments:
        taxa.update(alignment.taxa_names)
    return {
        "file_name": C.redacted_path(config_path),
        "size_bytes": len(raw),
        "content_sha256": sha256_hex(raw.decode("utf-8", errors="surrogateescape")),
        "beast_version": str(config.control.beast_version),
        "analysis_fingerprint": analysis_fingerprint(config),
        "content_fingerprint": content_fingerprint(config),
        "data_digest": data_digest(config),
        "n_alignments": len(config.all_alignments),
        "n_partitions": len(config.partitions),
        "n_taxa": len(taxa),
        "n_sites": sum(a.n_sites for a in config.all_alignments),
    }


def config_content_sha256(config_path: Path) -> str:
    """SHA-256 of a configuration file's UTF-8 text (the lock's key digest)."""
    raw = Path(config_path).read_bytes()
    return sha256_hex(raw.decode("utf-8", errors="surrogateescape"))


def resolve_lock_beast_version(config: Any, requested: Optional[str]) -> str:
    """One concrete BEAST version for the lock, or a clear refusal."""
    if requested:
        if requested not in ("1.10.4", "10.5.0"):
            raise LockError(
                f"unsupported BEAST version {requested!r}",
                fix="pass --beast-version 1.10.4 or --beast-version 10.5.0.",
            )
        return requested
    version = str(config.control.beast_version)
    if version not in ("1.10.4", "10.5.0"):
        raise LockError(
            f"the configuration does not name one concrete BEAST version (got {version!r})",
            fix=(
                "pass --beast-version 1.10.4 or --beast-version 10.5.0; a lock "
                "must pin a single jar digest."
            ),
        )
    return version


# ---------------------------------------------------------------------------
# Building and sealing
# ---------------------------------------------------------------------------


def collect_lock_document(
    config_path: Any,
    *,
    beast_version: Optional[str] = None,
    seeds: Optional[Sequence[int]] = None,
    beagle_dir: Optional[str] = None,
    beagle_threads: int = 1,
) -> Dict[str, Any]:
    """Build the (unsealed) lock document for one configuration."""
    path = C.resolve_existing(config_path, what="configuration")
    try:
        config = ConfigParser().parse(str(path))
    except ConfigError as exc:
        raise LockError(
            f"{path.name}: configuration error: {exc}",
            fix="fix the configuration first ('beastxpy generate --config ...' shows Gate 0).",
        ) from exc
    version = resolve_lock_beast_version(config, beast_version)
    environment = probe_beagle_environment(version, beagle_dir=beagle_dir, threads=beagle_threads)
    resolved_seeds: List[int]
    if seeds:
        resolved_seeds = [int(s) for s in seeds]
    elif config.control.seed is not None:
        resolved_seeds = [int(config.control.seed)]
    else:
        resolved_seeds = [1]

    document: Dict[str, Any] = {
        "lock_version": LOCK_VERSION,
        "tool": {"name": "beastxpy", "version": C.tool_version()},
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": sys.platform,
            "machine": platform.machine(),
        },
        "beast": _beast_facts(version),
        "java": _java_facts(environment),
        "beagle": _beagle_facts(environment, beagle_threads),
        "dependencies": dependency_versions(),
        "config": _config_facts(path, config),
        "seeds": resolved_seeds,
        "redaction": {
            "policy": REDACTION_POLICY,
            "absolute_paths": "none",
            "secrets": "none",
        },
    }
    return C.envelope(C.SCHEMA_LOCK, **document)


def compute_field_digests(document: Dict[str, Any], fields: Sequence[str]) -> Dict[str, str]:
    """SHA-256 of each tracked field's canonical value."""
    digests: Dict[str, str] = {}
    for name in fields:
        value = get_path(document, name)
        digests[name] = sha256_hex(canonical_json(value))
    return digests


def payload_digest(document: Dict[str, Any]) -> str:
    """SHA-256 over the document with the integrity block removed."""
    body = {key: value for key, value in document.items() if key != INTEGRITY_KEY}
    return sha256_hex(canonical_json(body))


def seal_lock(document: Dict[str, Any]) -> Dict[str, Any]:
    """Attach (or refresh) the integrity block; returns the sealed document."""
    sealed = {key: value for key, value in document.items() if key != INTEGRITY_KEY}
    fields = tuple(dict.fromkeys(list(TRACKED_FIELDS) + list(CONFIG_FIELDS)))
    sealed[INTEGRITY_KEY] = {
        "algorithm": "sha256",
        "tracked_fields": list(fields),
        "field_digests": compute_field_digests(sealed, fields),
        "payload_sha256": payload_digest(sealed),
    }
    return sealed


def build_lock(
    config_path: Any,
    *,
    beast_version: Optional[str] = None,
    seeds: Optional[Sequence[int]] = None,
    beagle_dir: Optional[str] = None,
    beagle_threads: int = 1,
) -> Dict[str, Any]:
    """Build, privacy-check and seal a lock document."""
    document = collect_lock_document(
        config_path,
        beast_version=beast_version,
        seeds=seeds,
        beagle_dir=beagle_dir,
        beagle_threads=beagle_threads,
    )
    sealed = seal_lock(document)
    assert_shareable(sealed)
    return sealed


def write_lock(path: Any, document: Dict[str, Any]) -> Path:
    """Write a sealed lock as human-readable UTF-8 JSON."""
    assert_shareable(document)
    return C.write_json(Path(path), document)


# ---------------------------------------------------------------------------
# Loading and integrity verification
# ---------------------------------------------------------------------------


def verify_integrity(document: Dict[str, Any]) -> Tuple[bool, List[str], Optional[str]]:
    """Return ``(ok, tampered_fields, payload_problem)``."""
    integrity = document.get(INTEGRITY_KEY)
    if not isinstance(integrity, dict):
        return False, [], "the lock has no integrity block"
    recorded_payload = integrity.get("payload_sha256")
    if not isinstance(recorded_payload, str) or not recorded_payload:
        return False, [], "the lock has no payload_sha256"
    digests = integrity.get("field_digests")
    tampered: List[str] = []
    if isinstance(digests, dict):
        for name, recorded in digests.items():
            actual = sha256_hex(canonical_json(get_path(document, name)))
            if actual != recorded:
                tampered.append(str(name))
    if payload_digest(document) != recorded_payload:
        if not tampered:
            return (
                False,
                [],
                (
                    "the lock's payload_sha256 does not match its content (an "
                    "untracked field was edited)"
                ),
            )
        return False, tampered, None
    return True, [], None


def load_lock(path: Any) -> Dict[str, Any]:
    """Load a lock file, rejecting anything corrupt, foreign or tampered with."""
    target = C.resolve_existing(path, what="lock file")
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LockError(
            f"the lock file {C.redacted_path(target)} is not readable JSON: {exc}",
            fix="regenerate it with 'beastxpy lock --config ... --output ...'.",
        ) from exc
    if not isinstance(document, dict):
        raise LockTamperedError(
            "the lock file is not a JSON object",
            fix="regenerate it with 'beastxpy lock --config ... --output ...'.",
        )
    schema = document.get("schema_version")
    if schema != C.SCHEMA_LOCK:
        raise LockTamperedError(
            f"not a BeastXPy lock file: schema_version is {schema!r}, expected "
            f"{C.SCHEMA_LOCK!r}",
            fix="pass the file written by 'beastxpy lock'.",
        )
    ok, tampered, problem = verify_integrity(document)
    if not ok:
        names = ", ".join(tampered) if tampered else (problem or "unknown")
        raise LockTamperedError(
            f"the lock file has been modified: {names}",
            fields=tampered,
            fix=(
                "a lock is immutable evidence; do not edit it. Regenerate it "
                "with 'beastxpy lock --config ... --output ...' if the "
                "environment really changed."
            ),
        )
    return document


# ---------------------------------------------------------------------------
# reproduce --check
# ---------------------------------------------------------------------------


@dataclass
class ReproduceReport:
    """Field-by-field comparison of the machine against a lock."""

    mode: str
    matches: List[Dict[str, Any]] = field(default_factory=list)
    mismatches: List[Dict[str, Any]] = field(default_factory=list)
    unchecked: List[Dict[str, Any]] = field(default_factory=list)
    verdict: str = "match"
    reason: Optional[str] = None
    fix: Optional[str] = None
    lock: Dict[str, Any] = field(default_factory=dict)
    run: Optional[Dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.verdict in ("match", "match_partial", "ran") and not self.mismatches

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "verdict": self.verdict,
            "matches": list(self.matches),
            "mismatches": list(self.mismatches),
            "unchecked": list(self.unchecked),
            "reason": self.reason,
            "fix": self.fix,
            "run": self.run,
        }


def current_environment(
    *,
    beast_version: str,
    beagle_dir: Optional[str] = None,
    beagle_threads: int = 1,
) -> Dict[str, Any]:
    """Re-probe the machine in the same shape the lock records it."""
    environment = probe_beagle_environment(
        beast_version, beagle_dir=beagle_dir, threads=beagle_threads
    )
    return {
        "tool": {"version": C.tool_version()},
        "python": {
            "version": platform.python_version(),
            "platform": sys.platform,
            "machine": platform.machine(),
        },
        "beast": _beast_facts(beast_version),
        "java": _java_facts(environment),
        "beagle": _beagle_facts(environment, beagle_threads),
        "dependencies": dependency_versions(),
    }


def check_lock(
    document: Dict[str, Any],
    *,
    config_path: Optional[Any] = None,
    beagle_dir: Optional[str] = None,
    beagle_threads: int = 1,
) -> ReproduceReport:
    """Compare the current environment against a sealed lock document."""
    report = ReproduceReport(mode="check", lock=document)
    beast_version = str(get_path(document, "beast.version") or "1.10.4")
    actual = current_environment(
        beast_version=beast_version, beagle_dir=beagle_dir, beagle_threads=beagle_threads
    )
    for name in TRACKED_FIELDS:
        expected = get_path(document, name)
        observed = get_path(actual, name)
        record = {"field": name, "expected": expected, "actual": observed}
        if expected == observed:
            report.matches.append(record)
        else:
            report.mismatches.append(record)

    if config_path is not None:
        path = C.resolve_existing(config_path, what="configuration")
        try:
            config = ConfigParser().parse(str(path))
        except ConfigError as exc:
            report.mismatches.append(
                {
                    "field": "config.parse",
                    "expected": get_path(document, "config.content_sha256"),
                    "actual": f"unparsable: {exc}",
                }
            )
            config = None
        if config is not None:
            facts = _config_facts(path, config)
            for name in CONFIG_FIELDS:
                key = name.split(".", 1)[1]
                expected = get_path(document, name)
                observed = facts.get(key)
                record = {"field": name, "expected": expected, "actual": observed}
                if expected == observed:
                    report.matches.append(record)
                else:
                    report.mismatches.append(record)
    else:
        for name in CONFIG_FIELDS:
            report.unchecked.append(
                {
                    "field": name,
                    "expected": get_path(document, name),
                    "actual": None,
                    "reason": (
                        "no configuration was supplied; pass --config PATH to "
                        "verify the recorded content hash"
                    ),
                }
            )

    if report.mismatches:
        report.verdict = "mismatch"
        report.reason = "%d recorded value(s) differ: %s" % (
            len(report.mismatches),
            ", ".join(str(item["field"]) for item in report.mismatches),
        )
        report.fix = (
            "restore the recorded environment (same jar, JVM, native BEAGLE and "
            "dependency versions), or regenerate the lock with 'beastxpy lock' "
            "if the change was intentional."
        )
    else:
        if report.unchecked:
            report.verdict = "match_partial"
            report.reason = (
                "all checked fields match, but %d field(s) were not checked "
                "(no configuration was supplied): %s"
                % (
                    len(report.unchecked),
                    ", ".join(str(item["field"]) for item in report.unchecked),
                )
            )
            report.fix = (
                "Pass --config PATH to verify the recorded configuration "
                "content hash; only then is the comparison complete."
            )
        else:
            report.verdict = "match"
    return report


def run_from_lock(
    document: Dict[str, Any],
    *,
    config_path: Any,
    workdir: Any,
    beagle_dir: Optional[str] = None,
    beagle_threads: int = 1,
    timeout: int = 86400,
    force: bool = True,
    log: Any = None,
) -> ReproduceReport:
    """Re-execute the locked analysis through :class:`BeastRunner`."""
    emit = log if callable(log) else (lambda _message: None)
    report = check_lock(
        document,
        config_path=config_path,
        beagle_dir=beagle_dir,
        beagle_threads=beagle_threads,
    )
    report.mode = "run"
    if report.mismatches:
        report.verdict = "mismatch"
        return report

    path = C.resolve_existing(config_path, what="configuration")
    beast_version = str(get_path(document, "beast.version") or "1.10.4")
    seeds = [int(s) for s in (document.get("seeds") or [1])]
    environment = probe_beagle_environment(
        beast_version, beagle_dir=beagle_dir, threads=beagle_threads
    )
    extra = beagle_runtime_environment(environment)
    saved: Dict[str, Optional[str]] = {key: os.environ.get(key) for key in extra} if extra else {}
    target = Path(workdir)
    target.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        for key, value in extra.items():
            os.environ[key] = value
        runner = BeastRunner(
            config_path=str(path),
            workdir=str(target),
            seeds=seeds,
            force=force,
            beast_version=beast_version,
            timeout=timeout,
        )
        try:
            summary = runner.run()
        except RunError as exc:
            report.verdict = "failed"
            report.reason = f"BEAST could not be started: {exc}"
            report.fix = (
                "check the Java runtime and BEAST jar recorded in the lock are "
                "still installed, then re-run."
            )
            report.run = {"status": "not_run", "reason": str(exc)}
            return report
    finally:
        for key, saved_value in saved.items():
            if saved_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved_value
    payload = summary.as_dict()
    for result in summary.results:
        emit(
            "  seed %d: %s (exit=%s, %.1f s)"
            % (result.seed, result.status, result.exit_code, result.elapsed_seconds or 0.0)
        )
    report.run = {
        "status": "ok" if summary.overall_ok else "failed",
        "workdir": C.display_path(target),
        "seeds": seeds,
        "beast_version": summary.beast_version,
        "fingerprint": summary.fingerprint,
        "content_fingerprint": summary.content_fingerprint,
        "jar_sha256": summary.jar_sha256,
        "overall_ok": bool(summary.overall_ok),
        "results": payload.get("results", []),
        "started_at": started,
    }
    if summary.overall_ok:
        report.verdict = "ran"
        report.reason = None
        report.fix = None
    else:
        failed = [r for r in summary.results if not r.ok]
        report.verdict = "failed"
        report.reason = "%d of %d seed run(s) failed" % (len(failed), len(summary.results))
        report.fix = (
            "read <workdir>/<seed>/run.log for the BEAST error; the environment "
            "matched the lock, so the cause is in the analysis itself."
        )
    return report


__all__ = [
    "CONFIG_FIELDS",
    "INTEGRITY_KEY",
    "LOCK_VERSION",
    "LockError",
    "LockTamperedError",
    "REDACTION_POLICY",
    "ReproduceReport",
    "TRACKED_DEPENDENCIES",
    "TRACKED_FIELDS",
    "assert_shareable",
    "build_lock",
    "check_lock",
    "collect_lock_document",
    "compute_field_digests",
    "config_content_sha256",
    "current_environment",
    "dependency_versions",
    "find_privacy_problems",
    "get_path",
    "load_lock",
    "payload_digest",
    "redact_source",
    "resolve_lock_beast_version",
    "run_from_lock",
    "seal_lock",
    "verify_integrity",
    "write_lock",
]
