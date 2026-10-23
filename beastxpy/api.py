"""Programmatic API for BeastXPy (:class:`BeastXPy` facade).

The facade runs the same pipeline as the CLI:

* ``generate_xml`` / ``quick_generate`` -- Gate 0 -> 1 -> 2 -> (optional) 3,
  writing the XML only after every gate passes;
* ``diagnose`` -- Gate-2 reports, plots and (optional) sensitivity variants;
* ``validate_xml`` -- Gate 1 (pure Python) and optionally Gate 3 (BEAST);
* ``run_analysis`` / ``analyze_trace`` / ``annotate_tree`` / ``combine_logs``
  -- post-generation pipelines;
* ``generate_methods`` / ``generate_fingerprint`` / ``generate_pipeline``
  -- reproducibility artefacts;
* ``list_models`` / ``check_environment`` -- registry and environment probes.

Gate 3 (BEAST parser instantiation) has four low-level states
(``executed-pass`` / ``executed-fail`` / ``not-run`` /
``environment-missing``).  This facade reports ``passed`` (the requested
version(s) really ran and returned success), ``failed`` (BEAST ran and
reported an error) or ``not-run`` (no Java/jar, validator unavailable, a
missing input, ... with a reason); the un-collapsed value is preserved in
``Gate3Result.raw_status`` and ``environment_error``.  A ``not-run`` result is
never rendered as ``OK``.  The requested validation outcome is surfaced
through :class:`GenerationResult` and through :attr:`GateError.outcomes`.

``beast_version="both"`` means: generate and validate one document per BEAST
version with distinguishable file names (``<stem>_1.10.4.xml`` /
``<stem>_10.5.0.xml``).  The model content of the two documents is identical;
only the embedded fingerprint/version metadata and the per-version validation
result differ.  No version-specific rendering is claimed.

Every method raising :class:`ValueError` semantics: configuration failures are
reported as :class:`ConfigError`, validation failures as
:class:`XMLValidationError`, and gate failures as :class:`GateError` (see
:mod:`beastxpy.main`).
"""

from __future__ import annotations

import sys
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import ConfigError, ConfigParser
from .diagnostics import DiagnosticFinding, diagnose_config, run_diagnostics
from .model import ModelAssembler
from .registry import ModelRegistry
from .reproducibility import (
    analysis_fingerprint,
    content_fingerprint,
    data_digest,
    generate_methods_latex,
    generate_pipeline,
    write_fingerprint_json,
)
from .runner import probe_java, resolve_jar_details, run_analysis_command
from .tracestats import trace_command
from .treetools import annotate_tree_command, combine_logs_command
from .validator import (
    beast_validate_xml,
    lookup_beagle_details,
    validate_xml_file,
    validate_xml_string,
)
from .xml_writer import (
    atomic_copy_file,
    atomic_write_text,
    plan_versioned_outputs,
    write_xml,
    write_xml_file,
)

_GATE_ERROR_MESSAGE = (
    "validation does not prove biological correctness; it only demonstrates "
    "that the XML parses, instantiates and obeys the documented invariants"
)

_REMEDIATION = (
    "Install a JDK (>= 8) and BEAST 1.10.4 / BEAST X 10.5.0, then set "
    "JAVA_HOME (or BEASTX_JAVA) and BEAST_1_10_4_HOME / BEAST_10_5_0_HOME "
    "(or BEASTX_JAR). BEAGLE (hmsbeagle) is required for full MCMC runs."
)

VALID_VERSIONS = ("1.10.4", "10.5.0")

#: BEAST's own wording when a native BEAGLE library is present but unusable.
#: These are environment faults: reporting them as an XML defect would send
#: the user looking for a bug in a document that BEAST never got to evaluate.
_BEAGLE_ENVIRONMENT_FAILURES = (
    "Failed to load BEAGLE library",
    "No acceptable BEAGLE library",
    "UnsatisfiedLinkError",
)


class GateError(RuntimeError):
    """Raised when a gate fails (carries the machine-readable findings)."""

    def __init__(
        self,
        gate_name: str,
        findings: Any,
        message: str = "",
        outcomes: Optional[List["Gate3Result"]] = None,
    ) -> None:
        self.gate_name = gate_name
        self.findings = findings
        self.outcomes = outcomes or []
        super().__init__(message or f"gate {gate_name} failed")


@dataclass
class Gate3Result:
    """Three-state facade result of a Gate-3 BEAST validation for one version.

    ``status`` collapses the low-level four-state Gate-3 status
    (``executed-pass`` / ``executed-fail`` / ``not-run`` /
    ``environment-missing``) into ``passed`` / ``failed`` / ``not-run``;
    ``raw_status`` preserves the un-collapsed value so JSON consumers can see
    that ``not-run`` was specifically an ``environment-missing`` condition.
    """

    version: str
    status: str  # "passed" | "failed" | "not-run"
    reason: str = ""
    exit_code: Optional[int] = None
    command: List[str] = field(default_factory=list)
    stderr_head: List[str] = field(default_factory=list)
    findings: List[Any] = field(default_factory=list)
    environment_error: bool = False
    raw_status: Optional[str] = None
    tree_likelihood_rule_source: Optional[str] = None
    mode: Optional[str] = None
    beagle: Optional[str] = None
    beagle_version: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "raw_status": self.raw_status,
            "tree_likelihood_rule_source": self.tree_likelihood_rule_source,
            "mode": self.mode,
            "beagle": self.beagle,
            "beagle_version": self.beagle_version,
            "ok": self.ok,
            "reason": self.reason,
            "exit_code": self.exit_code,
            "command": list(self.command),
            "stderr_head": list(self.stderr_head),
            "stderr": "\n".join(self.stderr_head),
            "findings": [f.as_dict() if hasattr(f, "as_dict") else str(f) for f in self.findings],
            "environment_error": self.environment_error,
        }


@dataclass
class GeneratedDocument:
    """One generated XML document (one BEAST version)."""

    version: str
    xml: str
    output_path: Optional[str]
    fingerprint: str
    content_fingerprint: str
    gate3: Optional[Gate3Result] = None
    #: portable sidecar name -> source path copied next to the XML
    sidecar_files: Dict[str, str] = field(default_factory=dict)


@dataclass
class GenerationResult:
    """Outcome of a (possibly multi-version) generation run."""

    documents: List[GeneratedDocument] = field(default_factory=list)
    gate1_findings: List[Any] = field(default_factory=list)
    gate2_findings: List[Any] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    beast_validate_requested: bool = False
    #: parameter id -> {"distribution": ..., "source": ...} automatic prior
    auto_priors: Dict[str, Dict[str, str]] = field(default_factory=dict)

    @property
    def primary_xml(self) -> str:
        if not self.documents:
            raise GateError("1", [], "no XML document was generated")
        return self.documents[0].xml

    @property
    def validation_status(self) -> str:
        """Aggregate Gate-3 status: not-requested/passed/failed/not-run."""
        if not self.beast_validate_requested:
            return "not-requested"
        statuses = [d.gate3.status for d in self.documents if d.gate3]
        if not statuses:
            return "not-run"
        if all(s == "passed" for s in statuses):
            return "passed"
        if any(s == "failed" for s in statuses):
            return "failed"
        return "not-run"

    @property
    def passed_versions(self) -> List[str]:
        return [
            d.version for d in self.documents if d.gate3 is not None and d.gate3.status == "passed"
        ]

    @property
    def exit_code(self) -> int:
        status = self.validation_status
        if status in ("not-requested", "passed"):
            return 0
        if status == "failed":
            return 3
        return 2


class BeastXPy:
    """Programmatic facade for the whole BeastXPy pipeline."""

    def __init__(self, verbose: bool = False, force: bool = False) -> None:
        self.verbose = verbose
        self.force = force
        self.registry = ModelRegistry()

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def parse_config(self, config_path: str):
        """Gate 0: parse and strictly validate a YAML configuration."""
        try:
            return ConfigParser().parse(config_path)
        except ConfigError as exc:
            raise GateError("0", [], str(exc)) from exc

    @staticmethod
    def _target_versions(config, beast_version: Optional[str]) -> List[str]:
        target = beast_version or config.control.beast_version
        if target == "both":
            return list(VALID_VERSIONS)
        if target not in VALID_VERSIONS:
            raise GateError(
                "0", [], f"unsupported BEAST version {target!r} (valid: 1.10.4, 10.5.0, both)"
            )
        return [str(target)]

    def generate_full(
        self,
        config,
        output: Optional[str] = None,
        beast_version: Optional[str] = None,
        beast_validate: bool = False,
        allow_unvalidated: bool = False,
        header_comments: Optional[List[str]] = None,
        data_check: bool = True,
        allow_custom_fragments: bool = False,
        config_path: Optional[str] = None,
    ) -> GenerationResult:
        """Shared generation pipeline after Gate 0 (per requested version)."""
        versions = self._target_versions(config, beast_version)
        result = GenerationResult(beast_validate_requested=beast_validate)

        # Data/calibration pre-flight.  Errors stop generation; warnings are
        # surfaced but do not block.  --no-data-check is the explicit escape.
        if data_check:
            from .datacheck import run_precheck

            precheck = run_precheck(config, config_path=config_path, enabled=True)
            result.warnings.extend(
                f"data-check [{item['severity']}] {item['id']}: {item['message']}"
                for item in precheck
                if item.get("severity") != "info"
            )
            errors = [item for item in precheck if item.get("severity") == "error"]
            if errors:
                raise GateError(
                    "0",
                    errors,
                    "data pre-check failed: " + "; ".join(item["message"] for item in errors[:5]),
                )

        # Custom fragments are opt-in.  Turning them on forces Gate 3: a
        # spliced document is never handed out unvalidated.
        fragments_spec = None
        fragments_envelope = None
        if config.custom_fragments:
            from .fragments import fragments_enabled
        else:
            fragments_enabled = None  # type: ignore[assignment]
        if fragments_enabled is not None and fragments_enabled(
            config, cli_flag=allow_custom_fragments
        ):
            from .fragments import parse_spec

            fragments_spec = parse_spec(config.custom_fragments, cli_flag=True)
            beast_validate = True
            # Reflect the forced gate in the result so the CLI reports it rather
            # than silently validating behind the user's back.
            result.beast_validate_requested = True
            result.warnings.append(
                "custom fragments enabled: the document is marked 'custom' and "
                "must pass Gate 3 against the official BEAST jars"
            )

        documents: List[GeneratedDocument] = []
        for version in versions:
            previous_version = config.control.beast_version
            config.control.beast_version = version
            try:
                xml_str, auto_priors, sidecars = self._assemble_document(
                    config, version, header_comments, result.gate1_findings
                )
                if not result.auto_priors:
                    result.auto_priors = auto_priors
                if fragments_spec is not None:
                    from .fragments import splice_document

                    fragments_envelope = splice_document(xml_str, fragments_spec)
                    xml_str = str(fragments_envelope["xml"])
                documents.append(
                    GeneratedDocument(
                        version=version,
                        xml=xml_str,
                        output_path=None,
                        fingerprint=analysis_fingerprint(config),
                        content_fingerprint=content_fingerprint(config),
                        sidecar_files={name: str(source) for name, source in sidecars.items()},
                    )
                )
            finally:
                config.control.beast_version = previous_version

        result.documents = documents

        # Gate 2 is version-independent: run it once over the parsed config.
        report = run_diagnostics(
            config,
            overlap_threshold=float((config.diagnostics or {}).get("overlap_threshold", 0.15)),
            overlap_measure=config.control.overlap_measure,
            strict=config.control.strict,
        )
        result.gate2_findings = list(report.findings)
        for pid, info in sorted(result.auto_priors.items()):
            distribution = info.get("distribution", "")
            source = info.get("source", "BeastXPy convention")
            finding = DiagnosticFinding(
                severity="info",
                kind="AutomaticPrior",
                message=(
                    f"automatic prior applied: {pid} ~ {distribution} "
                    f"({source}); override under parameter_priors"
                ),
                where=pid,
                code="AUTO_PRIOR",
            )
            result.gate2_findings.append(finding)
            report.findings.append(finding)
        if report.has_errors:
            raise GateError(
                "2",
                report.findings,
                "gate 2 failed: "
                + "; ".join(f.message for f in report.findings if f.severity == "error"),
            )

        if beast_validate:
            for document in documents:
                previous_version = config.control.beast_version
                config.control.beast_version = document.version
                try:
                    gate3 = self._gate3_document(
                        document.xml,
                        document.version,
                        allow_unvalidated,
                        document.sidecar_files,
                    )
                finally:
                    config.control.beast_version = previous_version
                document.gate3 = gate3
                if gate3.status != "passed":
                    result.warnings.append(
                        f"BEAST {document.version} validation {gate3.status}: {gate3.reason}"
                    )

        failing = [d.gate3 for d in documents if d.gate3 is not None and d.gate3.status != "passed"]
        if failing and fragments_spec is not None:
            # A spliced document is never handed out unvalidated, not even with
            # --allow-unvalidated: custom fragments must earn their Gate 3 pass.
            raise GateError(
                "3",
                [g.as_dict() for g in failing],
                "custom fragments must pass Gate 3; refusing to generate: "
                + "; ".join(
                    f"BEAST {g.version}: {g.status}" + (f" -- {g.reason}" if g.reason else "")
                    for g in failing
                ),
                outcomes=failing,
            )
        if failing and not allow_unvalidated:
            raise GateError(
                "3",
                [g.as_dict() for g in failing],
                "gate 3 did not pass: "
                + "; ".join(
                    f"BEAST {g.version}: {g.status}" + (f" -- {g.reason}" if g.reason else "")
                    for g in failing
                ),
                outcomes=failing,
            )

        if output:
            self._write_documents(config, documents, output)
        return result

    def _assemble_document(
        self,
        config,
        version: str,
        header_comments: Optional[List[str]],
        gate1_sink: List[Any],
    ) -> tuple:
        from .reproducibility import fingerprint_comment

        assembler = ModelAssembler(config)
        root = assembler.assemble()
        auto_priors = dict(assembler.params.auto_priors)
        sidecars = dict(assembler.sidecar_files)
        comments = list(header_comments or [])
        if config.output.embed_fingerprint is not False:
            comments.append(fingerprint_comment(config))
        xml_str = write_xml(
            root,
            beast_version=version,
            header_comments=comments,
        )
        findings = validate_xml_string(xml_str, beast_version=version, strict=config.control.strict)
        gate1_sink.extend(findings)
        errors = [f for f in findings if f.severity == "error"]
        if errors:
            raise GateError(
                "1",
                findings,
                "gate 1 failed: " + "; ".join(f.message for f in errors),
            )
        return xml_str, auto_priors, sidecars

    def _write_documents(self, config, documents: List[GeneratedDocument], output: str) -> None:
        versions = [document.version for document in documents]
        targets = dict(plan_versioned_outputs(output, versions, config.analysis_name))
        plan = [(document, targets[document.version]) for document in documents]

        # Refuse *before* writing anything when a target exists.
        for _document, target in plan:
            path = Path(target)
            if path.exists() and not self.force:
                raise GateError(
                    "output",
                    [],
                    f"{path} already exists; use force=True to overwrite",
                )
            if path.exists() and path.is_dir():
                raise GateError("output", [], f"{target} is a directory")

        for document, target in plan:
            # Version-specific fingerprint / sidecars.
            previous_version = config.control.beast_version
            config.control.beast_version = document.version
            try:
                write_xml_file(target, document.xml)
                document.output_path = target
                for name, source in document.sidecar_files.items():
                    atomic_copy_file(source, str(Path(target).parent / name))
                self._write_sidecars(
                    config,
                    Path(target),
                    document.gate3,
                    multi=len(documents) > 1,
                )
            finally:
                config.control.beast_version = previous_version

    def _validation_payload(self, gate3: Optional[Gate3Result]) -> Dict[str, Any]:
        if gate3 is None:
            return {"status": "not-requested", "versions": []}
        return {
            "status": gate3.status,
            "raw_status": gate3.raw_status,
            "tree_likelihood_rule_source": gate3.tree_likelihood_rule_source,
            "mode": gate3.mode,
            "beagle": gate3.beagle,
            "beagle_version": gate3.beagle_version,
            "versions": [gate3.version],
            "reason": gate3.reason,
            "exit_code": gate3.exit_code,
            "command": list(gate3.command),
            "stderr_head": list(gate3.stderr_head),
        }

    def _write_sidecars(
        self,
        config,
        xml_path: Path,
        gate3: Optional[Gate3Result],
        *,
        multi: bool,
    ) -> None:
        """Write fingerprint JSON and LaTeX methods next to the XML.

        Parent directories are created and every file is written atomically;
        on failure the XML and any sidecar already written are removed so a
        half-written artefact is never left behind.
        """
        output = config.output
        if multi:
            fingerprint_file = str(xml_path.with_name(xml_path.stem + ".fingerprint.json"))
            methods_file = str(xml_path.with_name(xml_path.stem + "_methods.tex"))
        else:
            fingerprint_file = output.fingerprint_file or str(
                xml_path.with_name(xml_path.stem + ".fingerprint.json")
            )
            methods_file = output.methods_file or str(
                xml_path.with_name(xml_path.stem + "_methods.tex")
            )
        written: List[Path] = []
        try:
            write_fingerprint_json(
                config, fingerprint_file, validation=self._validation_payload(gate3)
            )
            written.append(Path(fingerprint_file))
            validated = gate3 is not None and gate3.status == "passed"
            validation_status = gate3.status if gate3 is not None else "not-run"
            atomic_write_text(
                methods_file,
                generate_methods_latex(
                    config,
                    validated=validated,
                    validation_versions=[gate3.version] if validated and gate3 is not None else [],
                    validation_status=validation_status,
                ),
            )
            written.append(Path(methods_file))
        except BaseException:
            for path in written + [xml_path]:
                try:
                    path.unlink()
                except OSError:
                    pass
            raise

    def _gate3_document(
        self,
        xml_str: str,
        version: str,
        allow_unvalidated: bool,
        sidecar_files: Optional[Dict[str, str]] = None,
    ) -> Gate3Result:
        """Run Gate 3 for one version of an in-memory document.

        The document and every relative sidecar it references are staged in a
        temporary directory, because BEAST resolves ``fileName`` attributes
        against the process working directory.
        """
        with tempfile.TemporaryDirectory(prefix="beastxpy_gate3_") as staging:
            xml_path = Path(staging) / "document.xml"
            atomic_write_text(str(xml_path), xml_str)
            for name, source in (sidecar_files or {}).items():
                atomic_copy_file(source, str(Path(staging) / name))
            return self._gate3_path(str(xml_path), version, allow_unvalidated)

    def _gate3_path(self, xml_path: str, version: str, allow_unvalidated: bool) -> Gate3Result:
        """Run Gate 3 for one version against an existing path.

        Distinguishes environment problems (missing Java/jar -> not-run) from
        XML problems (BEAST ran and failed -> failed) and never fabricates a
        pass.  Exceptions from the validator are recorded, not swallowed into
        an ``ok`` result.
        """
        java = probe_java()
        if java is None:
            outcome = Gate3Result(
                version=version,
                status="not-run",
                reason=(
                    "no usable Java runtime found (set JAVA_HOME or BEASTX_JAVA; "
                    "install a JDK). " + _REMEDIATION
                ),
                environment_error=True,
            )
            return self._note_unvalidated(outcome, allow_unvalidated)

        jar = resolve_jar_details(version)
        if jar is None:
            outcome = Gate3Result(
                version=version,
                status="not-run",
                reason=(f"no beast-{version}.jar found on this machine. " + _REMEDIATION),
                environment_error=True,
            )
            return self._note_unvalidated(outcome, allow_unvalidated)

        try:
            result = self._validate_with_strongest_core(xml_path, version)
        except Exception as exc:  # noqa: BLE001 - surfaced as an explicit not-run
            outcome = Gate3Result(
                version=version,
                status="not-run",
                reason=f"validator raised {type(exc).__name__}: {exc}",
                environment_error=False,
            )
            return self._note_unvalidated(outcome, allow_unvalidated)

        return self._interpret_validation_result(result, version, allow_unvalidated)

    @staticmethod
    def _validate_with_strongest_core(xml_path: str, version: str) -> Any:
        """Gate 3 using BEAST's real (native BEAGLE) likelihood core if there is one.

        ``java_core`` replaces the BEAGLE tree-likelihood parser with BEAST's
        legacy pure-Java one, so it validates less of what BEAST will actually
        build; it stays the choice for machines with no hmsbeagle library, and
        for ones whose library exists but cannot be loaded.
        """
        if lookup_beagle_details(version) is None:
            return beast_validate_xml(
                xml_path, beast_version=version, dry_parse=True, java_core=True
            )
        result = beast_validate_xml(
            xml_path, beast_version=version, dry_parse=True, native_beagle=True
        )
        if getattr(result, "ok", False):
            return result
        report = f"{getattr(result, 'stderr', '') or ''}{getattr(result, 'reason', '') or ''}"
        if not any(marker in report for marker in _BEAGLE_ENVIRONMENT_FAILURES):
            return result
        warnings.warn(
            f"native BEAGLE for BEAST {version} is installed but unusable "
            f"({report.strip().splitlines()[-1][:160] if report.strip() else 'no detail'}); "
            "Gate 3 fell back to BEAST's legacy Java likelihood core. Install a "
            "working hmsbeagle to validate the likelihood BEAST will actually build.",
            RuntimeWarning,
            stacklevel=4,
        )
        return beast_validate_xml(xml_path, beast_version=version, dry_parse=True, java_core=True)

    @staticmethod
    def _stderr_head(text: str, limit: int = 20) -> List[str]:
        lines = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
        return lines[:limit]

    def _interpret_validation_result(
        self, result: Any, version: str, allow_unvalidated: bool
    ) -> Gate3Result:
        """Turn a (possibly rewritten) BeastValidationResult into a Gate3Result."""
        ok = bool(getattr(result, "ok", False))
        exit_code = getattr(result, "exit_code", None)
        stderr = str(getattr(result, "stderr", "") or "")
        stdout = str(getattr(result, "stdout", "") or "")
        command = list(getattr(result, "command", None) or [])
        findings = list(getattr(result, "findings", None) or [])
        skipped = bool(getattr(result, "skipped", False))
        executed = getattr(result, "executed", None)
        raw_status = getattr(result, "status", None)
        rule_source = getattr(result, "tree_likelihood_rule_source", None)
        mode = getattr(result, "mode", None)
        beagle = getattr(result, "beagle", None)
        beagle_version = getattr(result, "beagle_version", None)

        environment_hint = raw_status == "environment-missing" or (
            "no usable Java" in stderr
            or "no beast-" in stderr
            or "BeastXValidator timed out" in stderr
        )
        if ok:
            return Gate3Result(
                version=version,
                status="passed",
                raw_status=raw_status,
                tree_likelihood_rule_source=rule_source,
                mode=mode,
                beagle=beagle,
                beagle_version=beagle_version,
                exit_code=exit_code,
                command=command,
                findings=findings,
                reason="",
            )
        if skipped or executed is False or environment_hint or exit_code is None:
            outcome = Gate3Result(
                version=version,
                status="not-run",
                raw_status=raw_status,
                tree_likelihood_rule_source=rule_source,
                mode=mode,
                beagle=beagle,
                beagle_version=beagle_version,
                reason=stderr.strip()[:500] or "validation did not execute",
                exit_code=exit_code,
                command=command,
                stderr_head=self._stderr_head(stderr),
                findings=findings,
                environment_error=environment_hint,
            )
            return self._note_unvalidated(outcome, allow_unvalidated)

        detail = self._stderr_head(stderr) or self._stderr_head(stdout)
        reason = detail[-1] if detail else "BEAST reported an error"
        if exit_code is not None:
            reason = f"exit code {exit_code}: {reason}"
        outcome = Gate3Result(
            version=version,
            status="failed",
            raw_status=raw_status,
            tree_likelihood_rule_source=rule_source,
            mode=mode,
            beagle=beagle,
            beagle_version=beagle_version,
            reason=reason,
            exit_code=exit_code,
            command=command,
            stderr_head=detail,
            findings=findings,
        )
        if allow_unvalidated:
            warnings.warn(
                f"BEAST {version} validation failed but --allow-unvalidated is " f"set: {reason}",
                RuntimeWarning,
                stacklevel=4,
            )
        return outcome

    def _note_unvalidated(self, outcome: Gate3Result, allow_unvalidated: bool) -> Gate3Result:
        if allow_unvalidated:
            warnings.warn(
                f"BEAST {outcome.version} validation was not completed "
                f"({outcome.status}): {outcome.reason}",
                RuntimeWarning,
                stacklevel=4,
            )
        return outcome

    # Backwards-compatible helpers ------------------------------------
    def _gate3(
        self, xml_str: str, config, output: str, allow_unvalidated: bool
    ) -> List[Gate3Result]:
        """Run Gate 3 for every requested version of ``xml_str``.

        Returns the per-version outcomes; raises :class:`GateError` (gate 3)
        when a requested validation did not pass and ``allow_unvalidated`` is
        false.
        """
        versions = self._target_versions(config, None)
        outcomes: List[Gate3Result] = []
        for version in versions:
            outcome = self._gate3_document(xml_str, version, allow_unvalidated)
            outcomes.append(outcome)
        failing = [o for o in outcomes if o.status != "passed"]
        if failing and not allow_unvalidated:
            raise GateError(
                "3",
                [o.as_dict() for o in failing],
                "; ".join(f"BEAST {o.version}: {o.status} -- {o.reason}" for o in failing),
                outcomes=failing,
            )
        return outcomes

    def _generate(
        self,
        config,
        output: Optional[str] = None,
        beast_version: Optional[str] = None,
        beast_validate: bool = False,
        allow_unvalidated: bool = False,
        header_comments: Optional[List[str]] = None,
    ) -> str:
        """Backwards-compatible wrapper returning the primary XML string."""
        return self.generate_full(
            config,
            output=output,
            beast_version=beast_version,
            beast_validate=beast_validate,
            allow_unvalidated=allow_unvalidated,
            header_comments=header_comments,
        ).primary_xml

    def generate_xml_result(
        self,
        config_path: str,
        output: Optional[str] = None,
        beast_version: Optional[str] = None,
        beast_validate: bool = False,
        allow_unvalidated: bool = False,
        data_check: bool = True,
        allow_custom_fragments: bool = False,
    ) -> GenerationResult:
        """Generate BEAST 1 XML(s) and return the full structured result."""
        config = self.parse_config(config_path)
        return self.generate_full(
            config,
            output=output,
            beast_version=beast_version,
            beast_validate=beast_validate,
            allow_unvalidated=allow_unvalidated,
            data_check=data_check,
            allow_custom_fragments=allow_custom_fragments,
            config_path=config_path,
        )

    def generate_xml(
        self,
        config_path: str,
        output: Optional[str] = None,
        beast_version: Optional[str] = None,
        beast_validate: bool = False,
        allow_unvalidated: bool = False,
    ) -> str:
        """Generate a BEAST 1 XML from a YAML config (gates 0-2, optional 3).

        Returns the XML string of the primary document (1.10.4 when
        ``beast_version="both"``); use :meth:`generate_xml_result` to see all
        documents and the real Gate-3 status.  Writing ``output`` happens only
        after every requested gate passed (or ``allow_unvalidated`` is set).
        """
        return self.generate_xml_result(
            config_path,
            output=output,
            beast_version=beast_version,
            beast_validate=beast_validate,
            allow_unvalidated=allow_unvalidated,
        ).primary_xml

    def quick_generate(
        self,
        alignment: str,
        tree_prior: str = "yule",
        subst_model: str = "hky",
        clock_model: str = "strict",
        chain_length: int = 1_000_000,
        name: str = "quick",
        output: Optional[str] = None,
        calibration_yaml: Optional[str] = None,
        beast_version: str = "1.10.4",
        beast_validate: bool = False,
        allow_unvalidated: bool = False,
    ) -> str:
        """Single-partition quick generation (returns the primary XML string).

        Relative alignment/calibration paths are resolved against the current
        working directory (not the temporary YAML directory), and the default
        clock rate is marked as estimated so the time axis is identifiable
        without calibrations.
        """
        return self.quick_generate_result(
            alignment=alignment,
            tree_prior=tree_prior,
            subst_model=subst_model,
            clock_model=clock_model,
            chain_length=chain_length,
            name=name,
            output=output,
            calibration_yaml=calibration_yaml,
            beast_version=beast_version,
            beast_validate=beast_validate,
            allow_unvalidated=allow_unvalidated,
        ).primary_xml

    def quick_generate_result(
        self,
        alignment: str,
        tree_prior: str = "yule",
        subst_model: str = "hky",
        clock_model: str = "strict",
        chain_length: int = 1_000_000,
        name: str = "quick",
        output: Optional[str] = None,
        calibration_yaml: Optional[str] = None,
        beast_version: str = "1.10.4",
        beast_validate: bool = False,
        allow_unvalidated: bool = False,
    ) -> GenerationResult:
        """Single-partition quick generation with the full gate-3 status."""
        import yaml

        alignment_path = str(Path(alignment).expanduser().resolve())
        if clock_model == "strict":
            clock_spec: Dict[str, Any] = {
                "type": clock_model,
                "clock_rate": {"value": 1.0, "estimate": True},
            }
        else:
            clock_spec = {
                "type": clock_model,
                "ucld_mean": {"value": 1.0, "estimate": True},
            }

        config_data: Dict[str, Any] = {
            "metadata": {"analysis_name": name},
            "alignments": [{"id": "alignment1", "file": alignment_path}],
            "partitions": [
                {
                    "id": "p1",
                    "alignment_id": "alignment1",
                    "site_model": {
                        "substitution_model": {"type": subst_model},
                    },
                    "clock_model": clock_spec,
                }
            ],
            "tree_prior": {"type": tree_prior},
            "mcmc": {"chain_length": chain_length},
            "control": {"beast_version": beast_version},
        }
        if calibration_yaml:
            calibration_path = Path(calibration_yaml).expanduser().resolve()
            cal_data = yaml.safe_load(calibration_path.read_text(encoding="utf-8"))
            config_data["calibrations"] = cal_data.get("calibrations", [])

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", prefix="beastxpy_quick_", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(yaml.safe_dump(config_data, sort_keys=False))
            config_path = handle.name
        try:
            config = self.parse_config(config_path)
        finally:
            Path(config_path).unlink(missing_ok=True)
        return self.generate_full(
            config,
            output=output,
            beast_version=beast_version,
            beast_validate=beast_validate,
            allow_unvalidated=allow_unvalidated,
        )

    # ------------------------------------------------------------------
    # Diagnostics / validation
    # ------------------------------------------------------------------
    def diagnose(
        self,
        config_path: str,
        output_dir: str = "diagnostics",
        report_path: Optional[str] = None,
        visualize: bool = True,
        sensitivity: bool = False,
    ):
        """Run calibration diagnostics (Gate 2) and write reports/plots."""
        config = self.parse_config(config_path)
        xml_path = None
        if sensitivity:
            # Generate the XML in-memory and hand it to the sensitivity builder.
            xml_path = self._materialise_for_sensitivity(config, output_dir)
        report = diagnose_config(
            config,
            output_dir=output_dir,
            report_path=report_path,
            visualize=visualize,
            sensitivity=sensitivity,
            xml_path=xml_path,
        )
        return report

    @staticmethod
    def _safe_stem(name: str) -> str:
        import re

        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
        return safe or "analysis"

    def _materialise_for_sensitivity(self, config, output_dir: str) -> str:
        """Write the current XML for the diagnostics sensitivity builder."""
        from .reproducibility import fingerprint_comment

        assembler = ModelAssembler(config)
        root = assembler.assemble()
        comments = []
        if config.output.embed_fingerprint is not False:
            comments.append(fingerprint_comment(config))
        xml_str = write_xml(
            root,
            beast_version=config.control.beast_version,
            header_comments=comments,
        )
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        xml_path = out_dir / f"{self._safe_stem(config.analysis_name)}.xml"
        write_xml_file(str(xml_path), xml_str)
        return str(xml_path)

    def validate_xml(
        self,
        xml_path: str,
        beast_version: str = "1.10.4",
        beast: bool = False,
        allow_unvalidated: bool = False,
    ) -> Dict[str, Any]:
        """Gate 1 (and optionally Gate 3) against an existing XML file.

        Gate 1 and Gate 3 failures are reported with the correct stage and
        error code (:class:`GateError` ``"1"`` vs ``"3"``); Gate-1 warnings
        are never attached to a Gate-3 error.  Gate-3 entries carry the
        facade ``status`` (three-state), the un-collapsed ``raw_status``
        (four-state), the ``tree_likelihood_rule_source``, plus the reason,
        command, exit code and the head of BEAST's stderr.
        """
        if beast_version is None:
            beast_version = "1.10.4"
        if str(xml_path).lower().endswith((".yaml", ".yml")):
            config = self.parse_config(str(xml_path))
            beast_version = beast_version or config.control.beast_version
            xml_str = write_xml(
                ModelAssembler(config).assemble(),
                beast_version=beast_version if beast_version != "both" else "1.10.4",
            )
            findings = validate_xml_string(xml_str, beast_version=beast_version)
        else:
            findings = validate_xml_file(xml_path, beast_version=beast_version)
        gate1_ok = not any(f.severity == "error" for f in findings)
        result: Dict[str, Any] = {
            "ok": gate1_ok,
            "gate1_findings": [f.as_dict() for f in findings],
            "gate3": None,
        }
        if not gate1_ok:
            if beast and not allow_unvalidated:
                raise GateError("1", findings, "gate 1 failed")
            return result
        if not beast:
            return result

        versions = list(VALID_VERSIONS) if beast_version == "both" else [beast_version]
        gate3_results: List[Gate3Result] = []
        for version in versions:
            gate3_results.append(self._gate3_path(xml_path, version, allow_unvalidated))
        result["gate3"] = [g.as_dict() for g in gate3_results]
        result["ok"] = gate1_ok and all(g.ok for g in gate3_results)
        result["validation_status"] = (
            "passed"
            if all(g.ok for g in gate3_results)
            else ("failed" if any(g.status == "failed" for g in gate3_results) else "not-run")
        )
        failing = [g for g in gate3_results if not g.ok]
        if failing and not allow_unvalidated:
            raise GateError(
                "3",
                [g.as_dict() for g in failing],
                "gate 3 did not pass: "
                + "; ".join(f"BEAST {g.version}: {g.status} -- {g.reason}" for g in failing),
                outcomes=failing,
            )
        return result

    # ------------------------------------------------------------------
    # Run / post-processing
    # ------------------------------------------------------------------
    def run_analysis(
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
    ):
        """Run BEAST on the generated XML (per-seed working directories)."""
        return run_analysis_command(
            config_path,
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

    def analyze_trace(
        self,
        log_path: str,
        burnin: Optional[int] = None,
        ess_threshold: float = 200.0,
        plot: Optional[str] = None,
        as_json: bool = False,
    ):
        """Parse a BEAST .log trace and compute Tracer-inspired statistics."""
        return trace_command(
            log_path, burnin=burnin, ess_threshold=ess_threshold, plot=plot, as_json=as_json
        )

    def annotate_tree(
        self,
        trees_path: str,
        output: str,
        heights: str = "mean",
        burnin: int = 0,
        limit: float = 0.0,
        python_only: bool = False,
        beast_version: str = "1.10.4",
    ):
        """Annotate a tree log (TreeAnnotator or pure-Python fallback)."""
        return annotate_tree_command(
            trees_path,
            output,
            heights=heights,
            burnin=burnin,
            limit=limit,
            python_only=python_only,
            beast_version=beast_version,
        )

    def combine_logs(
        self,
        inputs: List[str],
        output: str,
        burnin: int = 0,
        resample: Optional[int] = None,
        strip: bool = False,
        beast_version: str = "1.10.4",
    ):
        """Combine log/tree files (LogCombiner or fallback)."""
        return combine_logs_command(
            inputs,
            output,
            burnin=burnin,
            resample=resample,
            strip=strip,
            beast_version=beast_version,
        )

    # ------------------------------------------------------------------
    # Reproducibility artefacts
    # ------------------------------------------------------------------
    def generate_methods(self, config_path: str) -> str:
        """Generate the LaTeX methods paragraph for a config.

        No Gate-3 validation is claimed: the paragraph states explicitly that
        BEAST parser validation was not performed.
        """
        config = self.parse_config(config_path)
        return generate_methods_latex(config, validated=False)

    def generate_fingerprint(self, config_path: str, output: Optional[str] = None) -> str:
        """Return (and optionally write) the analysis fingerprint artifacts."""
        config = self.parse_config(config_path)
        fp = analysis_fingerprint(config)
        if output:
            write_fingerprint_json(config, output)
        return fp

    def generate_pipeline(
        self,
        config_path: str,
        pipeline_type: str = "snakemake",
        output_dir: str = "pipeline",
    ) -> List[str]:
        """Generate Snakemake/Nextflow pipeline files."""
        config = self.parse_config(config_path)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        extension = "Snakefile" if pipeline_type == "snakemake" else "main.nf"
        target = out_dir / extension
        config_name = Path(config_path).name
        generate_pipeline(config, pipeline_type, str(target), config_filename=config_name)
        # Also copy the config so the pipeline is self-contained.
        import shutil

        destination = out_dir / config_name
        if destination.resolve() != Path(config_path).resolve():
            shutil.copy(config_path, destination)
        return [str(target)]

    # ------------------------------------------------------------------
    # Environment / models
    # ------------------------------------------------------------------
    def list_models(self) -> Dict[str, List[str]]:
        """List registered models with version-gating annotations."""
        return self.registry.list_models()

    def check_environment(self) -> Dict[str, Any]:
        """Probe Python/JDK/jar availability.

        Returns ``{"ok": bool, "missing": [...], "java", "java_version",
        "jars": {version: {path, sha256, source}}}``.  ``ok`` is true only
        when Java and both versioned jars are usable.
        """
        report: Dict[str, Any] = {
            "python": sys.version.split()[0],
            "beastxpy_version": __version__,
            "java": None,
            "java_version": None,
            "java_source": None,
            "jars": {},
            "beagle": {},
            "beagle_guidance": [],
            "missing": [],
            "ok": False,
        }
        java = probe_java()
        if java:
            report["java"] = java["path"]
            report["java_version"] = java["version"]
            report["java_source"] = java["source"]
        else:
            report["missing"].append("java (set JAVA_HOME or BEASTX_JAVA, or install a JDK)")
        for version in VALID_VERSIONS:
            details = resolve_jar_details(version)
            if details is None:
                report["jars"][version] = None
                report["missing"].append(
                    f"beast-{version}.jar (install BEAST and set "
                    f"BEAST_{version.replace('.', '_')}_HOME or BEASTX_JAR)"
                )
            else:
                report["jars"][version] = {
                    "path": details["path"],
                    "sha256": details.get("sha256", ""),
                    "source": details.get("source", ""),
                }
            beagle = lookup_beagle_details(version)
            report["beagle"][version] = beagle
            if beagle is None:
                tag = version.replace(".", "_")
                expected = "3.1.x" if version == "1.10.4" else "4.0.x"
                report["beagle_guidance"].append(
                    f"native BEAGLE for BEAST {version} not found: install BEAGLE "
                    f"{expected} (build it from source or use a prebuilt package), "
                    f"then export BEASTX_BEAGLE_{tag}_LIB=/path/to/the/directory "
                    "that contains libhmsbeagle-jni.so (Linux), "
                    "libhmsbeagle-jni.jnilib (macOS) or hmsbeagle64.dll (Windows). "
                    "See docs/en/native-beagle.md. Without it Gate 3 falls back "
                    "to java-core (construct-only, likelihood not evaluated) and "
                    "`run`/`compare` are refused."
                )
        report["ok"] = not report["missing"]
        return report


__all__ = [
    "BeastXPy",
    "GateError",
    "Gate3Result",
    "GeneratedDocument",
    "GenerationResult",
    "analysis_fingerprint",
    "content_fingerprint",
    "data_digest",
]
