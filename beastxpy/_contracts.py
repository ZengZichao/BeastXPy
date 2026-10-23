"""Shared contracts for BeastXPy commands and their JSON outputs.

Every JSON document BeastXPy emits carries a ``schema_version`` of the form
``beastxpy.<name>/v<N>``.  This module is the single place where those
identifiers, the exit-code tables and the JSON envelope helpers live, so the
command modules, the public API and the tests cannot drift apart.

Command modules must not invent their own exit codes or envelope shapes; they
import them from here.  Documentation describes the same tables.

Exit-code conventions
---------------------

``EXIT_OK`` (0) means the command did its job and found nothing that blocks the
user.  ``EXIT_USAGE`` (1) means bad arguments, an unreadable input or a failed
write.  ``EXIT_UNAVAILABLE`` (2) means a required external tool (Java, a BEAST
jar, native BEAGLE) is missing, so the command refused to produce a result
rather than guessing one.  ``EXIT_FAILED`` (3) means the command ran and the
answer is a negative verdict (validation failed, assessment failed, models not
comparable).

``check-data`` uses the table the user-facing specification fixes for it
(0 = pass, 1 = warnings only, 2 = errors found); those three constants are
exported separately so the distinction stays explicit instead of being buried
in a command module.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import __version__
from .xml_writer import atomic_write_text

# ---------------------------------------------------------------------------
# Schema identifiers
# ---------------------------------------------------------------------------

SCHEMA_ASSESSMENT = "beastxpy.assessment/v1"
SCHEMA_DATACHECK = "beastxpy.datacheck/v1"
SCHEMA_REPORT = "beastxpy.report/v1"
SCHEMA_COMPARE = "beastxpy.compare/v1"
SCHEMA_LOCK = "beastxpy.lock/v1"
SCHEMA_REPRODUCE = "beastxpy.reproduce/v1"
SCHEMA_CAPABILITIES = "beastxpy.capabilities/v1"
SCHEMA_FRAGMENTS = "beastxpy.fragments/v1"
SCHEMA_TRACE = "beastxpy.trace/v1"

ALL_SCHEMAS: Dict[str, str] = {
    "assessment": SCHEMA_ASSESSMENT,
    "datacheck": SCHEMA_DATACHECK,
    "report": SCHEMA_REPORT,
    "compare": SCHEMA_COMPARE,
    "lock": SCHEMA_LOCK,
    "reproduce": SCHEMA_REPRODUCE,
    "capabilities": SCHEMA_CAPABILITIES,
    "fragments": SCHEMA_FRAGMENTS,
    "trace": SCHEMA_TRACE,
}

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_UNAVAILABLE = 2
EXIT_FAILED = 3
EXIT_INTERRUPTED = 130

# ``check-data`` keeps the table fixed by its specification.
EXIT_CHECK_PASS = 0
EXIT_CHECK_WARNING = 1
EXIT_CHECK_ERROR = 2

# ---------------------------------------------------------------------------
# Severities and verdicts
# ---------------------------------------------------------------------------

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

SEVERITIES: Sequence[str] = (SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO)
_SEVERITY_RANK = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}

VERDICT_PASS = "pass"
VERDICT_WARN = "warn"
VERDICT_FAIL = "fail"

STATUS_OK = "ok"
STATUS_NOT_COMPUTABLE = "not_computable"
STATUS_FAILED = "failed"
STATUS_NOT_RUN = "not_run"


def severity_rank(severity: str) -> int:
    """Sort key for a severity label (errors first)."""
    return _SEVERITY_RANK.get(str(severity).lower(), 9)


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def tool_version() -> str:
    """The BeastXPy version every envelope records."""
    return __version__


def envelope(schema_version: str, **fields: Any) -> Dict[str, Any]:
    """Build a JSON envelope.

    ``schema_version``, ``generated_at`` and ``tool_version`` are always
    present and always come first so documents diff readably.  Extra fields
    are appended in the order given.
    """
    doc: Dict[str, Any] = {
        "schema_version": schema_version,
        "generated_at": now_iso(),
        "tool_version": tool_version(),
    }
    doc.update(fields)
    return doc


def json_dumps(obj: Any) -> str:
    """Serialise with stable key order, explicit UTF-8 and a trailing newline."""
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def console_text(value: Any) -> str:
    """Make text safe for the console without gratuitous escaping.

    Console summaries may carry paths and messages with non-ASCII characters.
    The text passes through untouched when the console encoding can represent
    it (a UTF-8 terminal keeps readable CJK paths); only text the console
    cannot represent is escaped with ``backslashreplace``, so a GBK or ASCII
    console never raises ``UnicodeEncodeError``.
    """
    text = str(value)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return text.encode("ascii", "backslashreplace").decode("ascii")
    return text


def write_json(path: Any, obj: Any) -> Path:
    """Atomically write ``obj`` as UTF-8 JSON to ``path``; return the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(str(target), json_dumps(obj))
    return target


def emit_json(obj: Any, json_path: Optional[Any] = None, *, to_stdout: bool = True) -> None:
    """Write the envelope to ``--json`` and/or stdout.

    ``to_stdout`` is disabled by commands whose stdout carries the primary
    artefact (an XML document, an HTML report) so the JSON never corrupts it.
    """
    if json_path:
        write_json(json_path, obj)
    if to_stdout:
        sys.stdout.write(json_dumps(obj))
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def finding(
    finding_id: str,
    severity: str,
    message: str,
    *,
    category: Optional[str] = None,
    location: Optional[Dict[str, Any]] = None,
    fix: Optional[str] = None,
    code: Optional[str] = None,
    value: Any = None,
    threshold: Any = None,
) -> Dict[str, Any]:
    """Build one finding/observation record.

    ``message`` states what was observed, ``fix`` states the next concrete
    action.  Both are required by the contract: a finding without a fix is not
    actionable, and BeastXPy never reports a problem it cannot help with.
    """
    severity = str(severity).lower()
    if severity not in _SEVERITY_RANK:
        raise ValueError(f"unknown severity {severity!r}; expected one of {list(SEVERITIES)}")
    record: Dict[str, Any] = {
        "id": str(code or finding_id),
        "severity": severity,
        "category": category,
        "message": str(message),
        "fix": str(fix) if fix else None,
        "location": dict(location) if location else None,
    }
    if value is not None:
        record["value"] = value
    if threshold is not None:
        record["threshold"] = threshold
    return record


def sort_findings(findings: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order findings errors-first, then warnings, then info; stable inside."""
    return sorted(findings, key=lambda f: (severity_rank(f.get("severity", "")), str(f.get("id"))))


def count_severities(findings: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Count findings per severity, always reporting all three keys."""
    counts = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 0, SEVERITY_INFO: 0}
    for item in findings:
        severity = str(item.get("severity", "")).lower()
        if severity in counts:
            counts[severity] += 1
    return counts


def verdict_from_counts(counts: Dict[str, int]) -> str:
    """``fail`` on any error, ``warn`` on any warning, otherwise ``pass``."""
    if counts.get(SEVERITY_ERROR, 0) > 0:
        return VERDICT_FAIL
    if counts.get(SEVERITY_WARNING, 0) > 0:
        return VERDICT_WARN
    return VERDICT_PASS


# ---------------------------------------------------------------------------
# Path hygiene
# ---------------------------------------------------------------------------


def display_path(path: Any) -> str:
    """Render a path for JSON output using forward slashes.

    Absolute paths are preserved: they are the user's own working directory and
    are needed to make a report actionable.  Lock files use
    :func:`redacted_path` instead, because those are meant to be shared.
    """
    return str(Path(path)).replace("\\", "/")


def redacted_path(path: Any) -> str:
    """Reduce a path to its final component, for shareable artefacts.

    Used by ``lock`` so a lock file never leaks a user-specific directory
    layout.  Verification relies on the recorded SHA-256, not on the path.
    """
    return Path(path).name


def resolve_existing(path: Any, *, what: str = "path") -> Path:
    """Resolve ``path`` and raise :class:`ContractError` if it does not exist."""
    target = Path(path).expanduser()
    if not target.exists():
        raise ContractError(
            f"{what} not found: {display_path(target)}",
            fix=(
                f"Check the {what} and pass an existing one; "
                f"list candidates in the current directory to confirm the name."
            ),
        )
    return target.resolve()


def resolve_existing_dir(path: Any, *, what: str = "directory") -> Path:
    """Resolve ``path`` and require it to be a directory."""
    target = resolve_existing(path, what=what)
    if not target.is_dir():
        raise ContractError(
            f"{what} is not a directory: {display_path(target)}",
            fix=f"Pass a directory, not a file ({what}).",
        )
    return target


class ContractError(RuntimeError):
    """A user-facing error that always carries the next step to take."""

    def __init__(self, message: str, *, fix: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = str(message)
        self.fix = fix

    def as_dict(self) -> Dict[str, str]:
        payload = {"error": self.message}
        if self.fix:
            payload["fix"] = self.fix
        return payload


def format_error(exc: ContractError) -> str:
    """Render a :class:`ContractError` the way every command prints it."""
    text = f"error: {exc.message}"
    if exc.fix:
        text += f"\n  next step: {exc.fix}"
    return text


# ---------------------------------------------------------------------------
# CLI plumbing shared by every command module
# ---------------------------------------------------------------------------


def add_json_argument(parser: Any, *, help_text: Optional[str] = None) -> None:
    """Attach the standard ``--json PATH`` option to a sub-parser."""
    parser.add_argument(
        "--json",
        dest="json_path",
        default=None,
        metavar="PATH",
        help=help_text or "write the machine-readable result to PATH (UTF-8 JSON)",
    )


def add_quiet_argument(parser: Any) -> None:
    """Attach ``--quiet``: suppress the human summary on stdout."""
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="print only the JSON envelope (or nothing when --json is used)",
    )


def log(message: str = "") -> None:
    """Progress/diagnostic line on stderr, keeping stdout free for artefacts."""
    print(message, file=sys.stderr)


def print_summary(lines: Sequence[str], *, quiet: bool = False) -> None:
    """Print the human-readable summary on stdout unless ``--quiet``."""
    if quiet:
        return
    for line in lines:
        print(line)


__all__ = [
    "ALL_SCHEMAS",
    "ContractError",
    "EXIT_CHECK_ERROR",
    "EXIT_CHECK_PASS",
    "EXIT_CHECK_WARNING",
    "EXIT_FAILED",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_UNAVAILABLE",
    "EXIT_USAGE",
    "SCHEMA_ASSESSMENT",
    "SCHEMA_CAPABILITIES",
    "SCHEMA_COMPARE",
    "SCHEMA_DATACHECK",
    "SCHEMA_FRAGMENTS",
    "SCHEMA_LOCK",
    "SCHEMA_REPRODUCE",
    "SCHEMA_REPORT",
    "SCHEMA_TRACE",
    "SEVERITIES",
    "SEVERITY_ERROR",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "STATUS_FAILED",
    "STATUS_NOT_COMPUTABLE",
    "STATUS_NOT_RUN",
    "STATUS_OK",
    "VERDICT_FAIL",
    "VERDICT_PASS",
    "VERDICT_WARN",
    "add_json_argument",
    "add_quiet_argument",
    "count_severities",
    "display_path",
    "emit_json",
    "envelope",
    "finding",
    "format_error",
    "json_dumps",
    "log",
    "now_iso",
    "print_summary",
    "redacted_path",
    "resolve_existing",
    "resolve_existing_dir",
    "severity_rank",
    "sort_findings",
    "tool_version",
    "verdict_from_counts",
    "write_json",
]
