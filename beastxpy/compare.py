"""Marginal-likelihood model comparison (path sampling / stepping stone).

``beastxpy compare`` answers one question: *given the same alignment, the same
taxon set and the same partition layout, which of these models does the data
prefer?*  It answers it by **running BEAST**, not by arithmetic on paper.

How it works
------------

1. Every input configuration is re-materialised with an injected ``analysis``
   block (``type: path_sampling`` or ``type: stepping_stone``) and run through
   the existing coverage plugin :mod:`beastxpy.coverage.mle`, which emits
   ``<marginalLikelihoodEstimator>``, ``<pathSamplingAnalysis>`` and
   ``<steppingStoneSamplingAnalysis>``.  All three elements are always present
   in the document, whatever ``--method`` was asked for; ``--method`` only
   selects which of BEAST's two results files is reported.
2. The document is executed by a :class:`~beastxpy.runner.BeastRunner` subclass
   (:class:`MarginalLikelihoodRunner`) against the official BEAST jar with the
   native BEAGLE likelihood core.
3. BEAST's own results file is parsed for the log marginal likelihood, and the
   number is **independently recomputed** from the path-sampling log with a
   re-implementation of BEAST's estimators.  Both must agree.
4. Pairwise ``log Bayes factor = logML(a) - logML(b)`` is reported with a
   Kass & Raftery style interpretation band.

What it does NOT claim
----------------------

* A log marginal likelihood is a Monte-Carlo estimate.  Its standard error is
  reported next to it, and a log Bayes factor smaller than a few standard
  errors is *not* evidence.
* The Kass & Raftery bands (``2 ln BF`` of 2 / 6 / 10) are a widely used
  reporting convention, not a theorem.  They do not turn a small difference
  into a decision and they say nothing about model adequacy.
* Too-short chains, too-few path steps and inadequate within-step sampling all
  bias the estimator.  ``compare`` records the chain length, the number of path
  steps and the per-step sample counts so a reader can judge the run, but it
  cannot make a bad run good.
* ``compare`` performs model *comparison* only.  It does not assess
  convergence of the underlying chains (see ``beastxpy assess``) and it does
  not check that either model is adequate for the data.

Honesty rule: no placeholder is ever reported as a measurement
--------------------------------------------------------------

:mod:`beastxpy.coverage.mle` writes a small **placeholder** path-sampling log
so that BEAST can construct the analysis blocks at parse time.  That
placeholder is not a measurement, and this module treats it as hostile input.
A number is reported only when every one of the following holds
(:func:`verify_run_provenance`):

* the BEAST process exited 0 and did not time out;
* the path-sampling log exists, was written after the run started, carries
  BEAST's own header (``# BEAST v...``, ``[seed=N]``, the command line) and
  does **not** contain the placeholder marker;
* BEAST's results file exists, was written after the run started, and its
  ``log marginal likelihood (using ...)`` line parses;
* the value recomputed from the path-sampling log reproduces BEAST's value;
* the ``chainLength`` / ``pathSteps`` BEAST logged match the request.

If any of those fail the model is reported with ``status != "ok"``,
``log_ml: null``, ``se: null`` and a plain-language reason, and the command
exits non-zero.  There is no fallback value.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from . import _contracts as C
from .config import ConfigError, ConfigParser
from .reproducibility import analysis_fingerprint, content_fingerprint, data_digest
from .runner import BeastRunner, RunError, probe_java, resolve_jar_details
from .utils import canonical_json, sha256_hex
from .validator import lookup_beagle_details

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

METHOD_PATH_SAMPLING = "path_sampling"
METHOD_STEPPING_STONE = "stepping_stone"
METHODS: Tuple[str, str] = (METHOD_PATH_SAMPLING, METHOD_STEPPING_STONE)

METHOD_ALIASES: Dict[str, str] = {
    "path-sampling": METHOD_PATH_SAMPLING,
    "path_sampling": METHOD_PATH_SAMPLING,
    "pathsampling": METHOD_PATH_SAMPLING,
    "ps": METHOD_PATH_SAMPLING,
    "stepping-stone": METHOD_STEPPING_STONE,
    "stepping_stone": METHOD_STEPPING_STONE,
    "steppingstone": METHOD_STEPPING_STONE,
    "ss": METHOD_STEPPING_STONE,
}

METHOD_LABELS: Dict[str, str] = {
    METHOD_PATH_SAMPLING: "path sampling",
    METHOD_STEPPING_STONE: "stepping stone sampling",
}

#: Basename of the path-sampling log inside a seed directory.  BEAST prepends
#: ``-prefix seed_<S>_`` to logger file names, so the artefact that actually
#: appears is ``seed_<S>_path_sampling.log``.
PS_LOG_BASENAME = "path_sampling.log"

#: Results-file basenames.  ``marginalLikelihoodEstimator`` and both analysis
#: blocks are rewritten to these relative names by
#: :class:`MarginalLikelihoodRunner` so BEAST's prefix handling stays
#: consistent between the writer (the estimator's ``<log>``) and the readers
#: (the two analysis blocks).
RESULTS_BASENAMES: Dict[str, str] = {
    METHOD_PATH_SAMPLING: "marginal_likelihood_path.txt",
    METHOD_STEPPING_STONE: "marginal_likelihood_stone.txt",
}

#: Marker written by :func:`beastxpy.coverage.mle._placeholder_log`.  Its
#: presence in a path-sampling log proves the file was never produced by a run.
PLACEHOLDER_MARKER = "BeastXPy placeholder path-sampling log"

#: Native BEAGLE release that pairs with each BEAST version.
EXPECTED_BEAGLE_VERSION: Dict[str, str] = {"1.10.4": "3.1.0", "10.5.0": "4.0.1"}

VALID_BEAST_VERSIONS: Tuple[str, str] = ("1.10.4", "10.5.0")

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_NOT_RUN = "not_run"

#: Tolerance for the independent recomputation of BEAST's estimator.  Both use
#: IEEE-754 doubles over the same samples, so agreement is normally exact; the
#: tolerance only absorbs summation order.
RECOMPUTE_TOLERANCE = 1e-6

DEFAULT_PATH_STEPS = 8
DEFAULT_CHAIN_LENGTH = 100000
DEFAULT_PATH_SCHEME = "oneSidedBeta"
DEFAULT_LOG_EVERY = 50

LIMITATIONS: Tuple[str, ...] = (
    "log marginal likelihood is a Monte-Carlo estimate: report it together "
    "with its standard error, and treat a log Bayes factor that is not "
    "clearly larger than the combined standard errors as no evidence.",
    "the interpretation bands (2 ln BF of 2 / 6 / 10, Kass & Raftery 1995) "
    "are a reporting convention, not a theorem; they do not license a "
    "decision on their own and say nothing about model adequacy.",
    "too-short chains, too-few path steps and inadequate sampling within a "
    "path step all bias the estimator, usually optimistically; the recorded "
    "chain length, path-step count and per-step sample counts must be read "
    "before the number is trusted.",
    "the standard error is a delta-method standard error over the samples in "
    "the path-sampling log; autocorrelation within a path step is handled by "
    "an effective sample size, but residual correlation between path steps is "
    "not, so the standard error should be read as a lower bound.",
    "compare performs model comparison only: it does not assess convergence "
    "(see 'beastxpy assess') and it does not test whether either model is "
    "adequate for the data.",
    "models are only comparable when the alignment, its content digest, the "
    "taxon set and the partition layout are identical; a Bayes factor between "
    "non-comparable analyses is not interpretable and compare refuses to "
    "produce one.",
    "BeastXPy writes a placeholder path-sampling log so BEAST can construct "
    "the analysis blocks; a value is reported only after BEAST has really run "
    "and the recomputation from the produced log agrees with BEAST's own "
    "results file. A placeholder value is never reported.",
)

#: Kass & Raftery (1995) style bands, applied to ``2 * log Bayes factor``.
BF_BANDS: Tuple[Tuple[float, str], ...] = (
    (10.0, "very strong evidence for the first model"),
    (6.0, "strong evidence for the first model"),
    (2.0, "positive evidence for the first model"),
    (0.0, "not worth more than a bare mention"),
)

_MLE_BLOCK_RE = re.compile(
    r"<marginalLikelihoodEstimator\b.*?</marginalLikelihoodEstimator>", re.DOTALL
)
_PS_LOG_ATTR_RE = re.compile(r'(<log\b[^>]*\bid="pathSamplingLog"[^>]*\bfileName=")[^"]*(")')
_LOG_ML_RE = re.compile(
    r"log marginal likelihood \(using (?P<label>[^)]+)\)\s+from\s+"
    r"(?P<column>\S+)\s*=\s*(?P<value>[-+0-9.eE]+)"
)
_MLE_CHAIN_RE = re.compile(r"chainLength=(\d+)\s*\n\s*pathSteps=(\d+)")
_BEAGLE_OBSERVED_RE = re.compile(r"Using BEAGLE library v([0-9][0-9.]*)")
_SENTINEL = "\x00bxpmle{index}\x00"


class CompareError(C.ContractError):
    """A ``compare`` failure that always carries the next step to take."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def normalise_method(raw: Any) -> str:
    """Map a user-supplied method name onto :data:`METHODS`."""
    if isinstance(raw, str):
        key = raw.strip().lower().replace(" ", "-")
        if key in METHOD_ALIASES:
            return METHOD_ALIASES[key]
    raise CompareError(
        f"unknown marginal-likelihood method {raw!r}",
        fix="use --method stepping-stone or --method path-sampling",
    )


def parse_seeds(raw: Any) -> List[int]:
    """Parse ``1,2,3`` (or a list) into a de-duplicated seed list."""
    tokens: List[Any]
    if raw is None:
        tokens = [1]
    elif isinstance(raw, str):
        tokens = [t for t in re.split(r"[,\s]+", raw.strip()) if t]
    elif isinstance(raw, (list, tuple)):
        tokens = list(raw)
    else:
        tokens = [raw]
    seeds: List[int] = []
    for token in tokens:
        try:
            value = int(str(token).strip())
        except (TypeError, ValueError):
            raise CompareError(
                f"invalid seed {token!r}",
                fix="pass comma-separated non-negative integers, e.g. --seeds 1,2",
            ) from None
        if value < 0 or value > 2**31 - 1:
            raise CompareError(
                f"seed {value} is outside 0..2**31-1",
                fix="pass a seed in the range BEAST accepts (0 to 2147483647).",
            )
        if value not in seeds:
            seeds.append(value)
    if not seeds:
        raise CompareError("no seeds given", fix="pass at least one seed, e.g. --seeds 1")
    return seeds


def interpret_log_bayes_factor(log_bf: Optional[float], se_log_bf: Optional[float] = None) -> str:
    """Return the Kass & Raftery style band for ``log_bf`` (natural log).

    The bands are applied to ``2 * ln BF`` and are a reporting convention, not
    a decision rule.  When the standard error is known the interpretation also
    states whether ``|log BF|`` exceeds twice that error; a difference smaller
    than twice its own uncertainty is reported as not distinguishable from zero
    rather than as evidence.
    """
    if log_bf is None:
        return "not computable: at least one log marginal likelihood is missing"
    if not math.isfinite(log_bf):
        return "not computable: the log Bayes factor is not finite"
    magnitude = 2.0 * abs(log_bf)
    favoured = "the first model (a)" if log_bf >= 0 else "the second model (b)"
    if magnitude > 10.0:
        band = "very strong evidence"
    elif magnitude > 6.0:
        band = "strong evidence"
    elif magnitude > 2.0:
        band = "positive evidence"
    else:
        band = "not worth more than a bare mention"
    text = (
        "2 ln BF = %.4f: %s for %s (Kass & Raftery band; a reporting "
        "convention, not a decision rule)" % (magnitude, band, favoured)
    )
    if se_log_bf is not None and math.isfinite(se_log_bf) and se_log_bf > 0:
        ratio = abs(log_bf) / se_log_bf
        if abs(log_bf) <= 2.0 * se_log_bf:
            text += (
                "; |log BF| = %.4f <= 2*SE (%.4f), so the difference is not "
                "distinguishable from zero at 2 SE and is not evidence"
                % (abs(log_bf), 2.0 * se_log_bf)
            )
        else:
            text += "; |log BF| = %.4f > 2*SE (%.4f) (ratio %.2f)" % (
                abs(log_bf),
                2.0 * se_log_bf,
                ratio,
            )
    return text


def _sha256_bytes(path: Path) -> Optional[str]:
    try:
        hasher = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _short(value: Any, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# Environment probe (fail closed)
# ---------------------------------------------------------------------------


@dataclass
class BeagleEnvironment:
    """What ``compare`` found (or failed to find) for one BEAST version."""

    beast_version: str
    required: bool = True
    available: bool = False
    reason: str = ""
    fix: str = ""
    directory: Optional[str] = None
    source: Optional[str] = None
    library_name: Optional[str] = None
    library_sha256: Optional[str] = None
    expected_version: Optional[str] = None
    observed_version: Optional[str] = None
    threads: int = 1
    jvm_arch_data_model: Optional[int] = None
    java_path: Optional[str] = None
    java_version: Optional[str] = None
    jar_path: Optional[str] = None
    jar_sha256: Optional[str] = None
    jar_source: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "required": self.required,
            "available": self.available,
            "beast_version": self.beast_version,
            "expected_version": self.expected_version,
            "observed_version": self.observed_version,
            "directory": self.directory,
            "source": self.source,
            "library_name": self.library_name,
            "library_sha256": self.library_sha256,
            "threads": self.threads,
            "jvm_arch_data_model": self.jvm_arch_data_model,
            "java": {"path": self.java_path, "version": self.java_version},
            "jar": {
                "path": self.jar_path,
                "sha256": self.jar_sha256,
                "source": self.jar_source,
            },
            "reason": self.reason or None,
            "fix": self.fix or None,
        }


def _native_library_names(arch_data_model: Optional[int]) -> List[str]:
    """Native BEAGLE/JNI library names this JVM could actually load."""
    bits = "" if arch_data_model is None else str(arch_data_model)
    if os.name == "nt":
        names = []
        if bits:
            names.append(f"hmsbeagle{bits}.dll")
            names.append(f"hmsbeagle-jni{bits}.dll")
        names.extend(["hmsbeagle64.dll", "hmsbeagle-jni64.dll"])
        return names
    if sys.platform == "darwin":  # pragma: no cover - macOS
        return ["libhmsbeagle-jni.jnilib", "libhmsbeagle.dylib"]
    return ["libhmsbeagle-jni.so", "libhmsbeagle.so"]


def probe_jvm_arch_data_model(java_path: str, timeout: int = 30) -> Optional[int]:
    """Return the JVM's ``sun.arch.data.model`` (32 or 64), or ``None``.

    A 32-bit JVM cannot load a 64-bit ``hmsbeagle`` library and vice versa;
    knowing the width lets ``compare`` fail closed *before* burning a chain.
    """
    try:
        proc = subprocess.run(
            [java_path, "-XshowSettings:properties", "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = re.search(r"sun\.arch\.data\.model\s*=\s*(\d+)", text)
    if match:
        return int(match.group(1))
    arch = re.search(r"os\.arch\s*=\s*(\S+)", text)
    if arch:
        return 64 if "64" in arch.group(1) else 32
    return None


def probe_beagle_environment(
    beast_version: str,
    *,
    beagle_dir: Optional[str] = None,
    threads: int = 1,
    require_beagle: bool = True,
) -> BeagleEnvironment:
    """Collect Java / jar / native-BEAGLE facts, or explain why they are missing.

    The probe never raises and never guesses: an unavailable environment is
    returned as ``available=False`` with a ``reason`` and a ``fix`` so the
    caller can fail closed with exit code 2 and no numbers.
    """
    env = BeagleEnvironment(
        beast_version=beast_version,
        required=require_beagle,
        expected_version=EXPECTED_BEAGLE_VERSION.get(beast_version),
        threads=threads,
    )
    java = probe_java()
    if java is None:
        env.reason = "no usable Java runtime was found"
        env.fix = (
            "install a JDK (>= 8) and point JAVA_HOME or BEASTX_JAVA at it; "
            "a 64-bit JVM is required for native BEAGLE."
        )
        return env
    env.java_path = java["path"]
    env.java_version = java["version"]
    env.jvm_arch_data_model = probe_jvm_arch_data_model(java["path"])

    jar = resolve_jar_details(beast_version)
    if jar is None:
        env.reason = f"no beast-{beast_version}.jar was found"
        env.fix = (
            f"install BEAST v{beast_version} and set "
            f"BEAST_{beast_version.replace('.', '_')}_HOME, or set BEASTX_JAR."
        )
        return env
    env.jar_path = jar["path"]
    env.jar_sha256 = jar.get("sha256") or None
    env.jar_source = jar.get("source")

    if not require_beagle:
        env.available = True
        return env

    details = lookup_beagle_details(beast_version, beagle_dir)
    if details is None:
        tag = beast_version.replace(".", "_")
        env.reason = (
            "native BEAGLE is required to run a real path-sampling/stepping-stone "
            "chain (the model contains a treeLikelihood) but no hmsbeagle JNI "
            "library was found"
        )
        env.fix = (
            f"install BEAGLE {EXPECTED_BEAGLE_VERSION.get(beast_version, '')} "
            "(native, not the beagle.jar wrapper) and set "
            f"BEASTX_BEAGLE_{tag}_LIB to the directory holding "
            + ("hmsbeagle64.dll" if os.name == "nt" else "libhmsbeagle-jni.so")
            + "; the JVM must have the same bitness as that library."
        )
        return env
    directory = Path(details["path"])
    env.directory = str(directory)
    env.source = details.get("source")
    library = _matching_native_library(directory, env.jvm_arch_data_model)
    if library is None:
        env.reason = (
            "the BEAGLE directory %s has no native library this JVM can load "
            "(JVM data model: %s)" % (directory, env.jvm_arch_data_model)
        )
        env.fix = (
            "use a JVM whose bitness matches the BEAGLE build: a 64-bit "
            "hmsbeagle64 library needs a 64-bit JVM (set BEASTX_JAVA to one), "
            "or install the matching BEAGLE build."
        )
        return env
    env.library_name = library.name
    env.library_sha256 = _sha256_bytes(library)
    env.available = True
    return env


def _matching_native_library(directory: Path, arch_data_model: Optional[int]) -> Optional[Path]:
    for name in _native_library_names(arch_data_model):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def beagle_runtime_environment(env: BeagleEnvironment) -> Dict[str, str]:
    """Environment additions that expose the native BEAGLE directory to BEAST.

    Windows resolves plugin DLLs through ``PATH``; macOS/Linux use the loader
    path.  ``JAVA_TOOL_OPTIONS`` carries ``beagle.thread.count`` because
    :class:`~beastxpy.runner.BeastRunner` builds its own JVM argument list and
    must not be modified.
    """
    extra: Dict[str, str] = {}
    if not env.available or not env.directory:
        return extra
    directory = str(Path(env.directory))
    if os.name == "nt":
        key = "PATH"
    elif sys.platform == "darwin":  # pragma: no cover - macOS
        key = "DYLD_LIBRARY_PATH"
    else:  # pragma: no cover - Linux
        key = "LD_LIBRARY_PATH"
    extra[key] = directory + os.pathsep + os.environ.get(key, "")
    options = [f"-Dbeagle.thread.count={int(env.threads or 1)}"]
    existing = os.environ.get("JAVA_TOOL_OPTIONS", "").strip()
    if existing:
        options.append(existing)
    extra["JAVA_TOOL_OPTIONS"] = " ".join(options)
    return extra


# ---------------------------------------------------------------------------
# The runner: BeastRunner plus MLE-aware file naming
# ---------------------------------------------------------------------------


class MarginalLikelihoodRunner(BeastRunner):
    """A :class:`~beastxpy.runner.BeastRunner` that survives an MLE document.

    ``BeastRunner`` renames its own ``fileLog``/``treeFileLog`` to
    ``trace.log``/``tree.log`` and always passes ``-prefix seed_<S>_``.  The
    marginal-likelihood document has additional loggers and analysis blocks
    that need different handling:

    * the estimator's ``<log id="pathSamplingLog">`` must keep its own file,
      otherwise the analysis blocks find nothing to read;
    * BEAST builds the logger's file name by naive ``prefix + fileName``
      concatenation, so an *absolute* ``fileName`` becomes the unopenable
      ``seed_1_F:/...``.

    This subclass therefore rewrites the three MLE file names to bare,
    relative names (resolved by BEAST against the seed working directory),
    still delegates own-logger renaming to the base class, and gives the two
    analysis blocks **distinct** results files so ``--method`` is
    unambiguous.  Nothing in :mod:`beastxpy.runner` is modified.
    """

    @staticmethod
    def _rewrite_logger_names(xml_text: str) -> str:
        blocks: List[str] = []

        def _stash(match: "re.Match[str]") -> str:
            blocks.append(match.group(0))
            return _SENTINEL.format(index=len(blocks) - 1)

        stashed = _MLE_BLOCK_RE.sub(_stash, xml_text)
        rewritten = BeastRunner._rewrite_logger_names(stashed)
        for index, block in enumerate(blocks):
            fixed = _PS_LOG_ATTR_RE.sub(r"\g<1>" + PS_LOG_BASENAME + r"\g<2>", block)
            rewritten = rewritten.replace(_SENTINEL.format(index=index), fixed)
        for method, results_name in RESULTS_BASENAMES.items():
            tag = (
                "pathSamplingAnalysis"
                if method == METHOD_PATH_SAMPLING
                else "steppingStoneSamplingAnalysis"
            )
            rewritten = re.sub(
                r"(<%s\b[^>]*\bfileName=\")[^\"]*(\")" % tag,
                r"\g<1>" + PS_LOG_BASENAME + r"\g<2>",
                rewritten,
            )
            rewritten = re.sub(
                r"(<%s\b[^>]*\bresultsFileName=\")[^\"]*(\")" % tag,
                r"\g<1>" + results_name + r"\g<2>",
                rewritten,
            )
        return rewritten


# ---------------------------------------------------------------------------
# Config materialisation
# ---------------------------------------------------------------------------


def build_analysis_block(
    *,
    method: str,
    chain_length: int,
    path_steps: int,
    path_scheme: str = DEFAULT_PATH_SCHEME,
    log_every: int = DEFAULT_LOG_EVERY,
    ps_file: str,
    results_file: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the ``analysis`` block ``compare`` injects into each config."""
    # Normalise here rather than only in the CLI: the analysis registry keys are
    # path_sampling / stepping_stone, while users type path-sampling /
    # stepping-stone.  Accepting either at this choke point means a library
    # caller cannot write a config the Gate 0 parser will reject.
    method = normalise_method(method)
    block: Dict[str, Any] = {
        "type": method,
        "chain_length": int(chain_length),
        "path_steps": int(path_steps),
        "path_scheme": path_scheme,
        "log_every": int(log_every),
        "ps_file": ps_file,
        "results_file": results_file,
    }
    if path_scheme in ("oneSidedBeta", "beta"):
        block["beta"] = 0.3
    if path_scheme in ("beta", "betaQuantile", "sigmoid"):
        block["alpha"] = 0.3
    if extra:
        block.update(extra)
    return block


def _clamp_cadence(raw: Dict[str, Any], chain_length: int) -> List[str]:
    """Force every ``log_every`` down to at most ``chain_length``.

    Gate 0 rejects a logger cadence longer than the chain, so a short
    comparison chain would otherwise fail before BEAST ever started.  Returns
    the list of adjustments made (recorded for transparency).
    """
    adjusted: List[str] = []
    loggers = raw.get("loggers")
    if isinstance(loggers, dict):
        for name, spec in loggers.items():
            if not isinstance(spec, dict):
                continue
            current = spec.get("log_every")
            if isinstance(current, bool) or not isinstance(current, int):
                continue
            if current > chain_length:
                spec["log_every"] = max(1, chain_length)
                adjusted.append(f"loggers.{name}.log_every: {current} -> {max(1, chain_length)}")
    mcmc = raw.get("mcmc")
    if isinstance(mcmc, dict) and mcmc.get("chain_length") != chain_length:
        adjusted.append("mcmc.chain_length: %s -> %d" % (mcmc.get("chain_length"), chain_length))
        mcmc["chain_length"] = chain_length
    return adjusted


def materialise_compare_config(
    config_path: Path,
    dest: Path,
    *,
    method: str,
    chain_length: int,
    path_steps: int,
    beast_version: str,
    seed: int,
    ps_file: Path,
    results_file: Path,
    path_scheme: str = DEFAULT_PATH_SCHEME,
    log_every: int = DEFAULT_LOG_EVERY,
    analysis_name: Optional[str] = None,
) -> Tuple[Path, List[str]]:
    """Write a derived config whose ``analysis`` block drives a PS/SS run.

    The original file is never touched.  Alignment paths are made absolute so
    the derived config can live in the work directory, and logger cadences are
    clamped to the (usually much shorter) comparison chain.  Returns the path
    written and the list of adjustments made.
    """
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CompareError(
            f"{config_path.name} is not a YAML mapping",
            fix="pass a BeastXPy configuration file (a top-level YAML mapping).",
        )
    adjusted: List[str] = []
    previous_analysis = raw.get("analysis")
    if previous_analysis:
        adjusted.append("analysis: overridden (was %s)" % _short(previous_analysis, 80))

    base = config_path.resolve().parent
    for entry in raw.get("alignments") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("file")
        if isinstance(name, str) and name and not Path(name).is_absolute():
            entry["file"] = str((base / name).resolve())

    adjusted.extend(_clamp_cadence(raw, chain_length))

    name = analysis_name or config_path.stem
    metadata = raw.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        raw["metadata"] = metadata
    if metadata.get("analysis_name") != name:
        adjusted.append("metadata.analysis_name: %s -> %s" % (metadata.get("analysis_name"), name))
        metadata["analysis_name"] = name

    control = raw.setdefault("control", {})
    if not isinstance(control, dict):
        control = {}
        raw["control"] = control
    if control.get("beast_version") != beast_version:
        adjusted.append(
            "control.beast_version: %s -> %s" % (control.get("beast_version"), beast_version)
        )
        control["beast_version"] = beast_version
    if control.get("seed") != seed:
        adjusted.append("control.seed: %s -> %s" % (control.get("seed"), seed))
        control["seed"] = seed

    log_every = max(1, min(int(log_every), int(chain_length)))
    raw["analysis"] = build_analysis_block(
        method=method,
        chain_length=chain_length,
        path_steps=path_steps,
        path_scheme=path_scheme,
        log_every=log_every,
        ps_file=str(ps_file),
        results_file=str(results_file),
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "# BeastXPy compare: derived from %s (do not edit; the original is untouched)\n"
        % config_path.name
        + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return dest, adjusted


# ---------------------------------------------------------------------------
# Comparability
# ---------------------------------------------------------------------------


def _alignment_signature(config: Any) -> Dict[str, Any]:
    return {
        a.id: {
            "file": Path(a.source_file).name if a.source_file else None,
            "digest": a.content_digest(),
            "n_taxa": a.n_taxa,
            "n_sites": a.n_sites,
            "data_type": a.beast_data_type,
        }
        for a in config.all_alignments
    }


def _partition_signature(config: Any) -> List[List[Any]]:
    return sorted(
        [
            p.id,
            p.alignment.id,
            p.alignment.from_pos if p.alignment.from_pos is not None else 1,
            p.alignment.to_pos if p.alignment.to_pos is not None else p.alignment.n_sites,
            p.tree,
            p.clock,
        ]
        for p in config.partitions
    )


def _taxon_signature(config: Any) -> List[str]:
    taxa = set()
    for alignment in config.all_alignments:
        taxa.update(alignment.taxa_names)
    return sorted(taxa)


def check_comparability(configs: Sequence[Any], names: Sequence[str]) -> List[Dict[str, Any]]:
    """Record every comparability check as ``{check, status, detail}``.

    A Bayes factor is only interpretable when the two analyses saw the same
    data.  The checks are: alignment files, alignment content digests, data
    types, alignment dimensions, the taxon set, the partition layout and the
    target BEAST version.  Each check is recorded whether it matches or not.
    """
    checks: List[Dict[str, Any]] = []

    def add(check: str, values: Sequence[Any], *, detail_prefix: str = "") -> bool:
        same = all(value == values[0] for value in values)
        detail = detail_prefix + "; ".join(
            "%s=%s" % (name, _short(value, 120)) for name, value in zip(names, values)
        )
        checks.append(
            {
                "check": check,
                "status": "match" if same else "mismatch",
                "detail": detail,
            }
        )
        return same

    signatures = [_alignment_signature(config) for config in configs]
    add(
        "alignment.files",
        [sorted((v["file"] or "") for v in sig.values()) for sig in signatures],
    )
    add(
        "alignment.content_digest",
        [sorted((k, v["digest"]) for k, v in sig.items()) for sig in signatures],
    )
    add(
        "alignment.data_type",
        [sorted((k, v["data_type"]) for k, v in sig.items()) for sig in signatures],
    )
    add(
        "alignment.dimensions",
        [sorted((k, v["n_taxa"], v["n_sites"]) for k, v in sig.items()) for sig in signatures],
    )
    add("taxon.set", [_taxon_signature(config) for config in configs])
    add("partition.layout", [_partition_signature(config) for config in configs])
    add(
        "control.beast_version",
        [str(config.control.beast_version) for config in configs],
    )
    return checks


def comparability_is_ok(checks: Sequence[Dict[str, Any]]) -> bool:
    """True when every recorded check matched."""
    return all(check.get("status") == "match" for check in checks)


# ---------------------------------------------------------------------------
# Parsing BEAST's own output
# ---------------------------------------------------------------------------


def parse_beast_results(text: str, method: str) -> Dict[str, Any]:
    """Extract the log marginal likelihood line BEAST wrote for ``method``.

    Returns ``{found, log_ml, label, column}``.  ``found`` is False when the
    line is absent; the caller must then report no number.
    """
    label = METHOD_LABELS[method]
    for match in _LOG_ML_RE.finditer(text):
        if match.group("label").strip().lower() != label:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:  # pragma: no cover - regex already constrains it
            continue
        return {
            "found": True,
            "log_ml": value,
            "label": match.group("label").strip(),
            "column": match.group("column"),
        }
    return {"found": False, "log_ml": None, "label": label, "column": None}


def parse_path_sampling_log(text: str) -> Dict[str, Any]:
    """Read a BEAST path-sampling log into per-theta bins.

    Returns ``{header, columns, rows, bins, is_placeholder, beast_header,
    recorded_seed}`` where ``bins`` maps theta -> list of ``delta`` values.
    """
    lines = text.splitlines()
    columns: List[str] = []
    rows: List[Tuple[float, float]] = []
    is_placeholder = PLACEHOLDER_MARKER in text
    beast_header = next((ln for ln in lines if ln.startswith("# BEAST v")), None)
    recorded_seed: Optional[int] = None
    theta_index: Optional[int] = None
    delta_index: Optional[int] = None
    for line in lines:
        if line.startswith("#"):
            match = re.search(r"\[seed=(\d+)\]", line)
            if match:
                recorded_seed = int(match.group(1))
            continue
        fields = [f for f in re.split(r"[\t,]", line.strip())]
        if not columns:
            # BEAST names these columns after the pathLikelihood id, e.g.
            # ``pathLikelihood.fixed.theta``; accept any id prefix.
            theta_index = next(
                (
                    index
                    for index, name in enumerate(fields)
                    if "pathLikelihood" in name and name.endswith("theta")
                ),
                None,
            )
            delta_index = next(
                (
                    index
                    for index, name in enumerate(fields)
                    if "pathLikelihood" in name and name.endswith("delta")
                ),
                None,
            )
            if theta_index is not None and delta_index is not None:
                columns = fields
            continue
        if theta_index is None or delta_index is None:
            continue
        if len(fields) <= max(theta_index, delta_index):
            continue
        try:
            theta = float(fields[theta_index])
            delta = float(fields[delta_index])
        except ValueError:
            continue
        rows.append((theta, delta))
    bins: Dict[float, List[float]] = {}
    for theta, delta in rows:
        bins.setdefault(theta, []).append(delta)
    return {
        "header": columns,
        "columns": columns,
        "rows": rows,
        "bins": bins,
        "is_placeholder": is_placeholder,
        "beast_header": beast_header,
        "recorded_seed": recorded_seed,
    }


def _bin_stats(bins: Dict[float, List[float]]) -> Tuple[List[float], Dict[float, List[float]]]:
    thetas = sorted(bins)
    return thetas, {theta: list(bins[theta]) for theta in thetas}


def _effective_sizes(values: Sequence[float]) -> float:
    """Effective sample size for one path-step bin (>= 1, <= n)."""
    n = len(values)
    if n < 4:
        return float(n)
    try:  # reuse the existing ESS estimator; never invent one
        import numpy as np

        from .tracestats import ess_estimate

        ess = float(ess_estimate(np.asarray(values, dtype=float)))
    except Exception:  # pragma: no cover - defensive: fall back to n
        return float(n)
    if not math.isfinite(ess) or ess <= 0:
        return float(n)
    return float(min(max(ess, 1.0), n))


def estimate_log_ml(bins: Dict[float, List[float]], method: str) -> Optional[float]:
    """Re-implement BEAST's estimator from the raw path-sampling samples.

    Path sampling (BEAST ``PathSamplingAnalysis``): trapezoidal integration of
    the per-theta mean of ``pathLikelihood.delta`` over the ascending theta
    grid.

    Stepping stone (BEAST ``SteppingStoneSamplingAnalysis``):
    ``sum_i [ w_i * M_i + log( mean_j exp(w_i * (delta_ij - M_i)) ) ]`` with
    ``w_i = theta_(i+1) - theta_i`` and ``M_i`` the bin maximum.

    Both reproduce BEAST's printed value bit-for-bit on real logs; that
    agreement is what makes the reported number trustworthy.
    """
    thetas, grouped = _bin_stats(bins)
    if len(thetas) < 2:
        return None
    total = 0.0
    if method == METHOD_PATH_SAMPLING:
        means = {t: sum(grouped[t]) / len(grouped[t]) for t in thetas}
        for index in range(len(thetas) - 1):
            width = thetas[index + 1] - thetas[index]
            total += width * (means[thetas[index]] + means[thetas[index + 1]]) / 2.0
        return total
    if method == METHOD_STEPPING_STONE:
        for index in range(len(thetas) - 1):
            width = thetas[index + 1] - thetas[index]
            values = grouped[thetas[index]]
            maximum = max(values)
            mean_exp = sum(math.exp(width * (v - maximum)) for v in values) / len(values)
            if mean_exp <= 0.0 or not math.isfinite(mean_exp):
                return None
            total += width * maximum + math.log(mean_exp)
        return total
    return None


def estimate_standard_error(bins: Dict[float, List[float]], method: str) -> Optional[float]:
    """Delta-method standard error of :func:`estimate_log_ml`.

    Within a path step the samples come from one MCMC run, so their mean is
    treated as having variance ``s^2 / n_eff`` with ``n_eff`` from
    :func:`beastxpy.tracestats.ess_estimate`.  Path steps are treated as
    independent; residual dependence between them is not modelled, which is
    why the value is documented as a lower bound.
    """
    thetas, grouped = _bin_stats(bins)
    if len(thetas) < 2:
        return None
    widths = [thetas[index + 1] - thetas[index] for index in range(len(thetas) - 1)]
    neff = {t: _effective_sizes(grouped[t]) for t in thetas}

    def _variance(values: Sequence[float]) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        return sum((v - mean) ** 2 for v in values) / (n - 1)

    if method == METHOD_PATH_SAMPLING:
        coefficients: Dict[int, float] = {}
        for index, width in enumerate(widths):
            coefficients[index] = coefficients.get(index, 0.0) + width / 2.0
            coefficients[index + 1] = coefficients.get(index + 1, 0.0) + width / 2.0
        variance = 0.0
        for index, coefficient in coefficients.items():
            theta = thetas[index]
            variance += coefficient**2 * _variance(grouped[theta]) / neff[theta]
        return math.sqrt(variance) if variance > 0 else 0.0

    if method == METHOD_STEPPING_STONE:
        variance = 0.0
        for index, width in enumerate(widths):
            theta = thetas[index]
            values = grouped[theta]
            maximum = max(values)
            transformed = [math.exp(width * (v - maximum)) for v in values]
            mean_exp = sum(transformed) / len(transformed)
            if mean_exp <= 0.0:
                continue
            variance += (width**2) * _variance(transformed) / (neff[theta] * mean_exp**2)
        return math.sqrt(variance) if variance > 0 else 0.0
    return None


def parse_mle_settings(run_log_text: str) -> Dict[str, Optional[int]]:
    """Read ``chainLength`` / ``pathSteps`` BEAST logged for the estimator."""
    match = _MLE_CHAIN_RE.search(run_log_text)
    if not match:
        return {"chain_length": None, "path_steps": None}
    return {"chain_length": int(match.group(1)), "path_steps": int(match.group(2))}


def parse_observed_beagle_version(run_log_text: str) -> Optional[str]:
    """Read the BEAGLE release BEAST actually loaded (``Using BEAGLE library v…``)."""
    match = _BEAGLE_OBSERVED_RE.search(run_log_text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Provenance verification (the honesty gate)
# ---------------------------------------------------------------------------


@dataclass
class SeedResult:
    """Outcome of one model x seed run."""

    seed: int
    status: str = STATUS_NOT_RUN
    log_ml: Optional[float] = None
    se: Optional[float] = None
    exit_code: Optional[int] = None
    elapsed_seconds: float = 0.0
    timed_out: bool = False
    ps_log: Optional[str] = None
    results_file: Optional[str] = None
    run_log: Optional[str] = None
    observed_chain_length: Optional[int] = None
    observed_path_steps: Optional[int] = None
    observed_beagle_version: Optional[str] = None
    samples_per_step: List[int] = field(default_factory=list)
    path_steps_observed: Optional[int] = None
    reason: Optional[str] = None
    checks: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "seed": self.seed,
            "status": self.status,
            "log_ml": self.log_ml,
            "se": self.se,
            "exit_code": self.exit_code,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "timed_out": self.timed_out,
            "ps_log": C.display_path(self.ps_log) if self.ps_log else None,
            "results_file": C.display_path(self.results_file) if self.results_file else None,
            "run_log": C.display_path(self.run_log) if self.run_log else None,
            "observed_chain_length": self.observed_chain_length,
            "observed_path_steps": self.observed_path_steps,
            "observed_beagle_version": self.observed_beagle_version,
            "samples_per_step": list(self.samples_per_step),
            "reason": self.reason,
            "checks": list(self.checks),
        }


def verify_run_provenance(
    *,
    run_dir: Path,
    seed: int,
    method: str,
    started_at: float,
    expected_chain_length: int,
    expected_path_steps: int,
    run_result: Any,
) -> SeedResult:
    """Decide whether a real marginal likelihood was produced.

    Every gate is recorded in ``SeedResult.checks`` so a reader can see
    exactly what was and was not established.  The first gate that fails sets
    ``status``/``reason`` and no number is reported.
    """
    out = SeedResult(seed=seed)
    run_log = run_dir / "run.log"
    out.run_log = str(run_log) if run_log.exists() else None
    ps_candidates = [
        run_dir / f"seed_{seed}_{PS_LOG_BASENAME}",
        run_dir / PS_LOG_BASENAME,
    ]
    results_candidates = [
        run_dir / RESULTS_BASENAMES[method],
        run_dir / f"seed_{seed}_{RESULTS_BASENAMES[method]}",
    ]

    def record(name: str, passed: bool, detail: str) -> bool:
        out.checks.append({"check": name, "status": "pass" if passed else "fail", "detail": detail})
        return passed

    exit_code = getattr(run_result, "exit_code", None)
    out.exit_code = exit_code
    out.timed_out = bool(getattr(run_result, "timed_out", False))
    out.elapsed_seconds = float(getattr(run_result, "elapsed_seconds", 0.0) or 0.0)

    if not record(
        "beast.exit_code",
        exit_code == 0 and not out.timed_out,
        "BEAST exit code %s (timeout=%s); a non-zero exit means the chain did "
        "not finish and no marginal likelihood exists" % (exit_code, out.timed_out),
    ):
        out.status = STATUS_FAILED
        out.reason = "BEAST exited with code %s; no marginal likelihood was produced" % exit_code
        return out

    if not record(
        "run.elapsed",
        out.elapsed_seconds > 0.0,
        "the BEAST subprocess ran for %.3f s" % out.elapsed_seconds,
    ):  # pragma: no cover - a zero-second successful run is not observable
        out.status = STATUS_FAILED
        out.reason = "the BEAST process reported no elapsed time"
        return out

    ps_log = next((p for p in ps_candidates if p.is_file()), None)
    if not record(
        "path_sampling_log.present",
        ps_log is not None,
        "looked for %s" % ", ".join(p.name for p in ps_candidates),
    ):
        out.status = STATUS_FAILED
        out.reason = "BEAST produced no path-sampling log; the estimator never wrote samples"
        return out
    assert ps_log is not None
    out.ps_log = str(ps_log)

    ps_mtime = ps_log.stat().st_mtime
    if not record(
        "path_sampling_log.fresh",
        ps_mtime >= started_at - 2.0,
        "mtime %s vs run start %s"
        % (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ps_mtime)),
            time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started_at)),
        ),
    ):
        out.status = STATUS_FAILED
        out.reason = "the path-sampling log predates this run; it is a stale artefact"
        return out

    ps_text = _read_text(ps_log)
    parsed_log = parse_path_sampling_log(ps_text)
    if not record(
        "path_sampling_log.not_placeholder",
        not parsed_log["is_placeholder"],
        "the BeastXPy placeholder marker is %s"
        % ("present" if parsed_log["is_placeholder"] else "absent"),
    ):
        out.status = STATUS_FAILED
        out.reason = (
            "the path-sampling log is the BeastXPy placeholder, not BEAST output; "
            "its values are not a marginal likelihood"
        )
        return out

    if not record(
        "path_sampling_log.beast_header",
        bool(parsed_log["beast_header"]) and parsed_log["recorded_seed"] == seed,
        "header=%r recorded seed=%r expected seed=%d"
        % (parsed_log["beast_header"], parsed_log["recorded_seed"], seed),
    ):
        out.status = STATUS_FAILED
        out.reason = "the path-sampling log does not carry BEAST's own header for seed %d" % seed
        return out

    if not record(
        "path_sampling_log.samples",
        len(parsed_log["rows"]) > 0 and len(parsed_log["bins"]) >= 2,
        "%d samples over %d path-step values" % (len(parsed_log["rows"]), len(parsed_log["bins"])),
    ):
        out.status = STATUS_FAILED
        out.reason = "the path-sampling log holds too few samples or path steps to integrate"
        return out
    out.samples_per_step = [len(parsed_log["bins"][theta]) for theta in sorted(parsed_log["bins"])]
    out.path_steps_observed = len(parsed_log["bins"])

    results_file = next((p for p in results_candidates if p.is_file()), None)
    if not record(
        "results_file.present",
        results_file is not None,
        "looked for %s" % ", ".join(p.name for p in results_candidates),
    ):
        out.status = STATUS_FAILED
        out.reason = "BEAST wrote no %s results file" % METHOD_LABELS[method]
        return out
    assert results_file is not None
    out.results_file = str(results_file)

    results_mtime = results_file.stat().st_mtime
    if not record(
        "results_file.fresh",
        results_mtime >= started_at - 2.0,
        "mtime %s vs run start %s"
        % (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(results_mtime)),
            time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started_at)),
        ),
    ):
        out.status = STATUS_FAILED
        out.reason = "the results file predates this run; it is a stale artefact"
        return out

    results_text = _read_text(results_file)
    parsed_results = parse_beast_results(results_text, method)
    if not record(
        "results_file.parses",
        bool(parsed_results["found"]) and parsed_results["log_ml"] is not None,
        "looked for 'log marginal likelihood (using %s) from ... = <value>'"
        % METHOD_LABELS[method],
    ):
        out.status = STATUS_FAILED
        out.reason = (
            "BEAST's results file has no '%s' log marginal likelihood line" % METHOD_LABELS[method]
        )
        return out

    recomputed = estimate_log_ml(parsed_log["bins"], method)
    if recomputed is None:
        record("recompute.agrees", False, "the estimator could not be recomputed")
        out.status = STATUS_FAILED
        out.reason = "the path-sampling log could not be integrated independently"
        return out
    beast_value = float(parsed_results["log_ml"])  # type: ignore[arg-type]
    tolerance = max(RECOMPUTE_TOLERANCE, 1e-8 * abs(beast_value))
    agrees = abs(recomputed - beast_value) <= tolerance
    if not record(
        "recompute.agrees",
        agrees,
        "recomputed from the log %.10f vs BEAST results file %.10f (tolerance %.2g)"
        % (recomputed, beast_value, tolerance),
    ):
        out.status = STATUS_FAILED
        out.reason = (
            "the value in BEAST's results file could not be reproduced from the "
            "path-sampling log; the two artefacts do not describe the same run"
        )
        return out

    if run_log.exists():
        run_text = _read_text(run_log)
        settings = parse_mle_settings(run_text)
        out.observed_chain_length = settings["chain_length"]
        out.observed_path_steps = settings["path_steps"]
        out.observed_beagle_version = parse_observed_beagle_version(run_text)
        record(
            "run_log.chain_length",
            out.observed_chain_length == int(expected_chain_length),
            "BEAST logged chainLength=%s (requested %d)"
            % (out.observed_chain_length, expected_chain_length),
        )
        record(
            "run_log.path_steps",
            out.observed_path_steps == int(expected_path_steps),
            "BEAST logged pathSteps=%s (requested %d)"
            % (out.observed_path_steps, expected_path_steps),
        )
        if out.observed_chain_length != int(expected_chain_length) or (
            out.observed_path_steps != int(expected_path_steps)
        ):
            out.status = STATUS_FAILED
            out.reason = (
                "BEAST ran with chainLength/pathSteps that do not match the request; "
                "the marginal likelihood would not describe the requested analysis"
            )
            return out
    else:  # pragma: no cover - BeastRunner always writes run.log
        record("run_log.chain_length", False, "run.log is missing")
        out.status = STATUS_FAILED
        out.reason = "run.log is missing; the run settings cannot be confirmed"
        return out

    se = estimate_standard_error(parsed_log["bins"], method)
    out.log_ml = beast_value
    out.se = None if se is None else float(se)
    out.status = STATUS_OK
    out.reason = None
    return out


# ---------------------------------------------------------------------------
# Model result
# ---------------------------------------------------------------------------


@dataclass
class ModelResult:
    """Everything ``compare`` knows about one candidate model."""

    name: str
    config: str
    method: str
    workdir: str
    chain_length: int
    path_steps: int
    seeds: List[int]
    beast_version: str
    status: str = STATUS_NOT_RUN
    log_ml: Optional[float] = None
    se: Optional[float] = None
    xml: Optional[str] = None
    results_file: Optional[str] = None
    ps_log: Optional[str] = None
    reason: Optional[str] = None
    analysis_fingerprint: Optional[str] = None
    content_fingerprint: Optional[str] = None
    data_digest: Optional[str] = None
    materialisation: List[str] = field(default_factory=list)
    runs: List[SeedResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        seed_estimates = [
            {
                "seed": run.seed,
                "status": run.status,
                "log_ml": run.log_ml,
                "se": run.se,
            }
            for run in self.runs
        ]
        usable = [float(run.log_ml) for run in self.runs if run.log_ml is not None]
        return {
            "name": self.name,
            "config": self.config,
            "status": self.status,
            "log_ml": self.log_ml,
            "se": self.se,
            "chain_length": self.chain_length,
            "path_steps": self.path_steps,
            "seeds": list(self.seeds),
            "seed_estimates": seed_estimates,
            "seed_log_ml_range": (max(usable) - min(usable) if len(usable) >= 2 else None),
            "beast_version": self.beast_version,
            "xml": C.display_path(self.xml) if self.xml else None,
            "results_file": C.display_path(self.results_file) if self.results_file else None,
            "ps_log": C.display_path(self.ps_log) if self.ps_log else None,
            "workdir": C.display_path(self.workdir),
            "analysis_fingerprint": self.analysis_fingerprint,
            "content_fingerprint": self.content_fingerprint,
            "data_digest": self.data_digest,
            "materialisation": list(self.materialisation),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "reason": self.reason,
            "runs": [run.as_dict() for run in self.runs],
        }


def _combine_seed_estimates(
    runs: Sequence[SeedResult],
) -> Tuple[Optional[float], Optional[float]]:
    """Aggregate per-seed estimates into one log ML and one standard error.

    The point estimate is the unweighted mean of the per-seed log marginal
    likelihoods.  The standard error combines the within-chain delta-method
    error with the between-seed spread, so a seed-sensitive result cannot hide
    behind a small single-chain error.
    """
    usable = [r for r in runs if r.status == STATUS_OK and r.log_ml is not None]
    if not usable:
        return None, None
    values = [float(r.log_ml) for r in usable]  # type: ignore[arg-type]
    count = len(values)
    mean = sum(values) / count
    within = [float(r.se) for r in usable if r.se is not None]
    # Standard error of the *mean* across seeds: average of the per-seed
    # variances divided by k (not the variance of a single seed), plus the
    # between-seed variance of the mean (between / k).
    within_term = sum(v * v for v in within) / (count * count) if within else 0.0
    if count > 1:
        between = sum((v - mean) ** 2 for v in values) / (count - 1)
        between_term = between / count
    else:
        between_term = 0.0
    return mean, math.sqrt(within_term + between_term)


def compare_one_model(
    *,
    config_path: Path,
    model_dir: Path,
    method: str,
    chain_length: int,
    path_steps: int,
    seeds: List[int],
    beast_version: str,
    environment: BeagleEnvironment,
    path_scheme: str = DEFAULT_PATH_SCHEME,
    log_every: int = DEFAULT_LOG_EVERY,
    timeout: int = 86400,
    beagle_dir: Optional[str] = None,
    parallel: int = 1,
    log: Any = None,
) -> ModelResult:
    """Run one candidate model and return a verified :class:`ModelResult`.

    ``parallel`` is forwarded to :class:`MarginalLikelihoodRunner` so the
    independent seeds of this model run concurrently.
    """
    emit = log if callable(log) else (lambda _message: None)
    name = config_path.stem
    result = ModelResult(
        name=name,
        config=str(config_path),
        method=method,
        workdir=str(model_dir),
        chain_length=chain_length,
        path_steps=path_steps,
        seeds=list(seeds),
        beast_version=beast_version,
    )
    model_dir.mkdir(parents=True, exist_ok=True)
    derived = model_dir / f"{name}.compare.yaml"
    ps_file = model_dir / "path_sampling_source.log"
    results_file = model_dir / f"marginal_likelihood_{method}.txt"
    try:
        _written, adjustments = materialise_compare_config(
            config_path,
            derived,
            method=method,
            chain_length=chain_length,
            path_steps=path_steps,
            beast_version=beast_version,
            seed=seeds[0],
            ps_file=ps_file,
            results_file=results_file,
            path_scheme=path_scheme,
            log_every=log_every,
            analysis_name=name,
        )
    except (OSError, yaml.YAMLError, CompareError) as exc:
        result.status = STATUS_FAILED
        result.reason = f"the derived configuration could not be written: {exc}"
        return result
    result.materialisation = adjustments

    try:
        parser = ConfigParser()
        original = parser.parse(str(config_path))
        derived_config = parser.parse(str(derived))
    except ConfigError as exc:
        result.status = STATUS_FAILED
        result.reason = f"configuration error: {exc}"
        return result
    if data_digest(original) != data_digest(derived_config):
        result.status = STATUS_FAILED
        result.reason = (
            "the derived configuration no longer describes the same alignment "
            "(data digest changed); refusing to run"
        )
        return result
    result.analysis_fingerprint = analysis_fingerprint(derived_config)
    result.content_fingerprint = content_fingerprint(derived_config)
    result.data_digest = data_digest(derived_config)

    env_extra = beagle_runtime_environment(environment)
    saved: Dict[str, Optional[str]] = {key: os.environ.get(key) for key in env_extra}
    started = time.time()
    try:
        for key, value in env_extra.items():
            os.environ[key] = value
        runner = MarginalLikelihoodRunner(
            config_path=str(derived),
            workdir=str(model_dir),
            seeds=list(seeds),
            force=True,
            beast_version=beast_version,
            timeout=timeout,
            parallel=parallel,
        )
        try:
            summary = runner.run()
        except RunError as exc:
            result.status = STATUS_FAILED
            result.reason = f"BEAST could not be started: {exc}"
            return result
    finally:
        for key, saved_value in saved.items():
            if saved_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved_value
    result.elapsed_seconds = time.time() - started

    by_seed = {r.seed: r for r in summary.results}
    for seed in seeds:
        run_dir = model_dir / str(seed)
        run_result = by_seed.get(seed)
        if run_result is None:  # pragma: no cover - BeastRunner returns one per seed
            result.runs.append(
                SeedResult(
                    seed=seed, status=STATUS_NOT_RUN, reason="BEAST was not run for this seed"
                )
            )
            continue
        xml_path = run_dir / f"{derived_config.analysis_name}.xml"
        if xml_path.is_file():
            result.xml = str(xml_path)
        emit(
            "  seed %d: BEAST exit=%s (%.1f s)"
            % (seed, run_result.exit_code, run_result.elapsed_seconds or 0.0)
        )
        seed_result = verify_run_provenance(
            run_dir=run_dir,
            seed=seed,
            method=method,
            started_at=started,
            expected_chain_length=chain_length,
            expected_path_steps=path_steps,
            run_result=run_result,
        )
        result.runs.append(seed_result)
        if seed_result.status == STATUS_OK and seed_result.results_file:
            result.results_file = seed_result.results_file
            result.ps_log = seed_result.ps_log
        emit(
            "  seed %d: %s%s"
            % (
                seed,
                seed_result.status,
                "" if not seed_result.reason else " -- " + seed_result.reason,
            )
        )

    log_ml, se = _combine_seed_estimates(result.runs)
    result.log_ml = log_ml
    result.se = se
    if log_ml is None:
        result.status = STATUS_FAILED
        first_reason = next(
            (r.reason for r in result.runs if r.reason), "no seed produced a verified result"
        )
        result.reason = "no verified log marginal likelihood for %s: %s" % (name, first_reason)
    else:
        result.status = STATUS_OK
    return result


# ---------------------------------------------------------------------------
# Pairwise Bayes factors
# ---------------------------------------------------------------------------


def pairwise_log_bayes_factors(models: Sequence[ModelResult]) -> List[Dict[str, Any]]:
    """All ordered pairs ``log BF = logML(a) - logML(b)`` with interpretation."""
    pairs: List[Dict[str, Any]] = []
    for index, first in enumerate(models):
        for second in models[index + 1 :]:
            if first.log_ml is None or second.log_ml is None:
                pairs.append(
                    {
                        "a": first.name,
                        "b": second.name,
                        "log_bf": None,
                        "interpretation": interpret_log_bayes_factor(None),
                        "status": STATUS_NOT_RUN,
                        "reason": ("at least one model has no verified log marginal likelihood"),
                    }
                )
                continue
            log_bf = first.log_ml - second.log_ml
            # If either SE is unknown, the combined SE is unknown; do not
            # substitute zero and then claim a precise difference.
            combined: Optional[float] = (
                None
                if first.se is None or second.se is None
                else math.sqrt(first.se * first.se + second.se * second.se)
            )
            pairs.append(
                {
                    "a": first.name,
                    "b": second.name,
                    "log_bf": log_bf,
                    "se_log_bf": combined,
                    "interpretation": interpret_log_bayes_factor(log_bf, combined),
                    "status": STATUS_OK,
                    "reason": None,
                }
            )
    return pairs


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


def parse_config(path: Path) -> Any:
    """Parse one configuration (Gate 0) or raise :class:`CompareError`."""
    try:
        return ConfigParser().parse(str(path))
    except ConfigError as exc:
        raise CompareError(
            f"{path.name}: configuration error: {exc}",
            fix="fix the configuration (run 'beastxpy generate --config ...' to see Gate 0).",
        ) from exc
    except (OSError, yaml.YAMLError) as exc:
        raise CompareError(
            f"{path.name}: unreadable configuration: {exc}",
            fix="check the file exists and is valid UTF-8 YAML.",
        ) from exc


def resolve_beast_version(configs: Sequence[Any], requested: Optional[str]) -> str:
    """Pick one concrete BEAST version, or explain why one must be given."""
    if requested:
        if requested not in VALID_BEAST_VERSIONS:
            raise CompareError(
                f"unsupported BEAST version {requested!r}",
                fix="pass --beast-version 1.10.4 or --beast-version 10.5.0.",
            )
        return requested
    seen = {str(config.control.beast_version) for config in configs}
    concrete = {v for v in seen if v in VALID_BEAST_VERSIONS}
    if len(concrete) == 1 and len(seen) == 1:
        return next(iter(concrete))
    raise CompareError(
        "the configurations do not agree on one concrete BEAST version "
        "(found: %s)" % ", ".join(sorted(seen)),
        fix="pass --beast-version 1.10.4 or --beast-version 10.5.0 explicitly.",
    )


def build_compare_envelope(
    *,
    method: str,
    workdir: Path,
    models: Sequence[ModelResult],
    comparability: Sequence[Dict[str, Any]],
    comparable: bool,
    environment: BeagleEnvironment,
    chain_length: int,
    path_steps: int,
    path_scheme: str,
    seeds: List[int],
    beast_version: str,
    configs: Sequence[Path],
) -> Dict[str, Any]:
    """Assemble the ``beastxpy.compare/v1`` envelope."""
    pairs = pairwise_log_bayes_factors(models)
    if not comparable:
        verdict = "not_comparable"
    elif any(model.status != STATUS_OK for model in models):
        verdict = "failed"
    else:
        verdict = "compared"
    return C.envelope(
        C.SCHEMA_COMPARE,
        method=method,
        workdir=C.display_path(workdir),
        comparable=comparable,
        comparability=list(comparability),
        verdict=verdict,
        settings={
            "chain_length": chain_length,
            "path_steps": path_steps,
            "path_scheme": path_scheme,
            "seeds": list(seeds),
            "beast_version": beast_version,
        },
        models=[model.as_dict() for model in models],
        pairwise=pairs,
        beagle=environment.as_dict(),
        configs=[C.display_path(path) for path in configs],
        limitations=list(LIMITATIONS),
    )


def _stub_models(
    config_paths: Sequence[Path],
    *,
    method: str,
    workdir: Path,
    chain_length: int,
    path_steps: int,
    seeds: List[int],
    beast_version: str,
    reason: str,
) -> List[ModelResult]:
    """Model records for a comparison that never ran (no numbers, only why)."""
    stubs: List[ModelResult] = []
    for path in config_paths:
        stubs.append(
            ModelResult(
                name=path.stem,
                config=str(path),
                method=method,
                workdir=str(workdir / path.stem),
                chain_length=chain_length,
                path_steps=path_steps,
                seeds=list(seeds),
                beast_version=beast_version,
                status=STATUS_NOT_RUN,
                reason=reason,
            )
        )
    return stubs


def run_comparison(
    config_paths: Sequence[Any],
    *,
    method: str,
    workdir: Any,
    seeds: Optional[List[int]] = None,
    chain_length: int = DEFAULT_CHAIN_LENGTH,
    path_steps: int = DEFAULT_PATH_STEPS,
    path_scheme: str = DEFAULT_PATH_SCHEME,
    log_every: int = DEFAULT_LOG_EVERY,
    beast_version: Optional[str] = None,
    beagle_dir: Optional[str] = None,
    beagle_threads: int = 1,
    timeout: int = 86400,
    parallel: int = 1,
    log: Any = None,
) -> Tuple[Dict[str, Any], int]:
    """Run the whole comparison and return ``(envelope, exit_code)``.

    Order of decisions (each one is reported, none is skipped silently):

    1. argument sanity (``>= 2`` configs, positive settings) -> exit 1;
    2. comparability of the configurations -> exit 3, BEAST is not started;
    3. environment (Java, jar, native BEAGLE) -> exit 2, **no numbers**;
    4. real BEAST runs plus provenance verification -> exit 0 or 3.
    """
    emit = log if callable(log) else (lambda _message: None)
    if len(config_paths) < 2:
        raise CompareError(
            "compare needs at least two configurations (got %d)" % len(config_paths),
            fix="pass two or more --configs entries, e.g. --configs a.yaml b.yaml",
        )
    if chain_length < 1:
        raise CompareError(
            f"--chain-length must be >= 1 (got {chain_length})",
            fix="pass a positive chain length; a few thousand steps per path "
            "step is the usual minimum for a usable estimate.",
        )
    if path_steps < 2:
        raise CompareError(
            f"--path-steps must be >= 2 (got {path_steps})",
            fix="a path needs at least two steps; 50-100 is typical for " "publication estimates.",
        )
    resolved_seeds = list(seeds) if seeds else [1]
    if parallel < 1:
        raise CompareError(
            f"--parallel must be >= 1 (got {parallel})",
            fix="pass --parallel 1 for sequential runs or the number of seeds to run concurrently.",
        )
    paths = [C.resolve_existing(path, what="configuration") for path in config_paths]
    workdir_path = Path(workdir)
    workdir_path.mkdir(parents=True, exist_ok=True)

    configs = [parse_config(path) for path in paths]
    names = [path.stem for path in paths]
    version = resolve_beast_version(configs, beast_version)

    emit("probing environment for BEAST %s ..." % version)
    environment = probe_beagle_environment(version, beagle_dir=beagle_dir, threads=beagle_threads)

    emit("checking comparability of %d configurations ..." % len(paths))
    checks = check_comparability(configs, names)
    comparable = comparability_is_ok(checks)
    for check in checks:
        emit("  %-28s %s" % (check["check"], check["status"]))

    if not comparable:
        mismatched = [c["check"] for c in checks if c["status"] != "match"]
        reason = (
            "configurations are not comparable (%s differ); BEAST was not run "
            "and no marginal likelihood is reported" % ", ".join(mismatched)
        )
        envelope = build_compare_envelope(
            method=method,
            workdir=workdir_path,
            models=_stub_models(
                paths,
                method=method,
                workdir=workdir_path,
                chain_length=chain_length,
                path_steps=path_steps,
                seeds=resolved_seeds,
                beast_version=version,
                reason=reason,
            ),
            comparability=checks,
            comparable=False,
            environment=environment,
            chain_length=chain_length,
            path_steps=path_steps,
            path_scheme=path_scheme,
            seeds=resolved_seeds,
            beast_version=version,
            configs=paths,
        )
        envelope["reason"] = reason
        envelope["fix"] = (
            "compare only models built on the same alignment, taxon set and "
            "partition layout; re-run 'beastxpy check-data' on each "
            "configuration and align the differing blocks."
        )
        return envelope, C.EXIT_FAILED

    if not environment.available:
        reason = environment.reason or "the required environment is not available"
        envelope = build_compare_envelope(
            method=method,
            workdir=workdir_path,
            models=_stub_models(
                paths,
                method=method,
                workdir=workdir_path,
                chain_length=chain_length,
                path_steps=path_steps,
                seeds=resolved_seeds,
                beast_version=version,
                reason="BEAST was not run: " + reason,
            ),
            comparability=checks,
            comparable=True,
            environment=environment,
            chain_length=chain_length,
            path_steps=path_steps,
            path_scheme=path_scheme,
            seeds=resolved_seeds,
            beast_version=version,
            configs=paths,
        )
        envelope["verdict"] = "environment_unavailable"
        envelope["reason"] = reason
        envelope["fix"] = environment.fix
        return envelope, C.EXIT_UNAVAILABLE

    emit(
        "environment: java %s (%s-bit), jar %s, BEAGLE %s"
        % (
            environment.java_version,
            environment.jvm_arch_data_model,
            Path(environment.jar_path or "").name,
            environment.library_name,
        )
    )
    models: List[ModelResult] = []
    for path in paths:
        emit(
            "running %s (%d seeds, chainLength=%d, pathSteps=%d) ..."
            % (path.stem, len(resolved_seeds), chain_length, path_steps)
        )
        models.append(
            compare_one_model(
                config_path=path,
                model_dir=workdir_path / path.stem,
                method=method,
                chain_length=chain_length,
                path_steps=path_steps,
                seeds=resolved_seeds,
                beast_version=version,
                environment=environment,
                path_scheme=path_scheme,
                log_every=log_every,
                timeout=timeout,
                parallel=parallel,
                beagle_dir=beagle_dir,
                log=emit,
            )
        )
    envelope = build_compare_envelope(
        method=method,
        workdir=workdir_path,
        models=models,
        comparability=checks,
        comparable=True,
        environment=environment,
        chain_length=chain_length,
        path_steps=path_steps,
        path_scheme=path_scheme,
        seeds=resolved_seeds,
        beast_version=version,
        configs=paths,
    )
    observed = {
        model.name: run.observed_beagle_version
        for model in models
        for run in model.runs
        if run.observed_beagle_version
    }
    if observed:
        envelope["beagle"]["observed_version"] = sorted(set(observed.values()))[-1]
    if envelope["verdict"] == "failed":
        reasons = [model.reason for model in models if model.reason]
        envelope["reason"] = reasons[0] if reasons else "a model run did not verify"
        envelope["fix"] = (
            "read the per-seed provenance checks in the JSON (models[].runs[].checks) "
            "and the BEAST run.log in the model directory; re-run with a longer "
            "chain once the cause is fixed."
        )
        return envelope, C.EXIT_FAILED
    return envelope, C.EXIT_OK


# ---------------------------------------------------------------------------
# Table renderers
# ---------------------------------------------------------------------------


CSV_COLUMNS: Tuple[str, ...] = (
    "record",
    "name",
    "name_b",
    "status",
    "log_ml",
    "se",
    "log_bf",
    "se_log_bf",
    "interpretation",
    "chain_length",
    "path_steps",
    "seeds",
    "beast_version",
)


def render_csv(envelope: Dict[str, Any]) -> str:
    """Render the envelope as a single CSV table (models then pairwise rows)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    settings = envelope.get("settings") or {}
    for model in envelope.get("models") or []:
        writer.writerow(
            [
                "model",
                model.get("name"),
                "",
                model.get("status"),
                "" if model.get("log_ml") is None else repr(round(model["log_ml"], 6)),
                "" if model.get("se") is None else repr(round(model["se"], 6)),
                "",
                "",
                "",
                model.get("chain_length", settings.get("chain_length")),
                model.get("path_steps", settings.get("path_steps")),
                ";".join(str(s) for s in (model.get("seeds") or [])),
                model.get("beast_version", settings.get("beast_version")),
            ]
        )
    for pair in envelope.get("pairwise") or []:
        writer.writerow(
            [
                "pairwise",
                pair.get("a"),
                pair.get("b"),
                pair.get("status"),
                "",
                "",
                "" if pair.get("log_bf") is None else repr(round(pair["log_bf"], 6)),
                "" if pair.get("se_log_bf") is None else repr(round(pair["se_log_bf"], 6)),
                pair.get("interpretation"),
                "",
                "",
                "",
                "",
            ]
        )
    return buffer.getvalue()


_CSS = """
body{font-family:system-ui,Segoe UI,Helvetica,Arial,sans-serif;margin:24px;color:#111;
background:#fff;line-height:1.45}
h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:28px}
table{border-collapse:collapse;width:100%;margin:8px 0 16px}
th,td{border:1px solid #bbb;padding:6px 8px;text-align:left;vertical-align:top;
font-size:.9rem}
th{background:#f2f2f2}
.ok{color:#0a6b1f;font-weight:600}.bad{color:#a11212;font-weight:600}
.meta{color:#444;font-size:.9rem}
ul{margin:6px 0 14px 20px}
code{background:#f5f5f5;padding:1px 4px}
"""


def _esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _status_class(status: Any) -> str:
    return "ok" if status in (STATUS_OK, "match", "pass", "compared") else "bad"


def render_html(envelope: Dict[str, Any]) -> str:
    """Render a self-contained HTML page (no external references, all escaped)."""
    parts: List[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head><meta charset="utf-8">')
    parts.append("<title>BeastXPy model comparison</title>")
    parts.append("<style>%s</style>" % _CSS.strip())
    parts.append("</head><body>")
    parts.append("<h1>BeastXPy marginal-likelihood comparison</h1>")
    settings = envelope.get("settings") or {}
    parts.append(
        '<p class="meta">method <code>%s</code> &middot; BEAST <code>%s</code> &middot; '
        "chain length <code>%s</code> &middot; path steps <code>%s</code> &middot; "
        'seeds <code>%s</code> &middot; verdict <span class="%s">%s</span></p>'
        % (
            _esc(envelope.get("method")),
            _esc(settings.get("beast_version")),
            _esc(settings.get("chain_length")),
            _esc(settings.get("path_steps")),
            _esc(",".join(str(s) for s in (settings.get("seeds") or []))),
            _status_class(envelope.get("verdict")),
            _esc(envelope.get("verdict")),
        )
    )
    parts.append(
        '<p class="meta">generated %s by BeastXPy %s</p>'
        % (_esc(envelope.get("generated_at")), _esc(envelope.get("tool_version")))
    )

    parts.append("<h2>Models</h2><table><thead><tr>")
    for header in (
        "model",
        "status",
        "log marginal likelihood",
        "standard error",
        "chain length",
        "path steps",
        "BEAST",
        "reason",
    ):
        parts.append("<th>%s</th>" % _esc(header))
    parts.append("</tr></thead><tbody>")
    for model in envelope.get("models") or []:
        parts.append("<tr>")
        parts.append("<td>%s</td>" % _esc(model.get("name")))
        parts.append(
            '<td class="%s">%s</td>'
            % (_status_class(model.get("status")), _esc(model.get("status")))
        )
        parts.append(
            "<td>%s</td>" % _esc("" if model.get("log_ml") is None else "%.6f" % model["log_ml"])
        )
        parts.append("<td>%s</td>" % _esc("" if model.get("se") is None else "%.6f" % model["se"]))
        parts.append("<td>%s</td>" % _esc(model.get("chain_length")))
        parts.append("<td>%s</td>" % _esc(model.get("path_steps")))
        parts.append("<td>%s</td>" % _esc(model.get("beast_version")))
        parts.append("<td>%s</td>" % _esc(model.get("reason")))
        parts.append("</tr>")
    parts.append("</tbody></table>")

    parts.append("<h2>Per-seed marginal likelihoods</h2>")
    parts.append(
        '<p class="meta">The seed range is max(logML) - min(logML); a range '
        "that is large relative to the reported SE means the estimate is "
        "seed-sensitive.</p>"
    )
    parts.append("<table><thead><tr>")
    for header in (
        "model",
        "seed",
        "status",
        "log marginal likelihood",
        "SE",
        "within-model range",
    ):
        parts.append("<th>%s</th>" % _esc(header))
    parts.append("</tr></thead><tbody>")
    for model in envelope.get("models") or []:
        estimates = model.get("seed_estimates") or []
        if not estimates:
            parts.append(
                "<tr><td>%s</td><td></td><td>%s</td><td></td><td></td><td></td></tr>"
                % (_esc(model.get("name")), _esc(model.get("status")))
            )
            continue
        for index, entry in enumerate(estimates):
            parts.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    _esc(model.get("name")) if index == 0 else "",
                    _esc(entry.get("seed")),
                    _esc(entry.get("status")),
                    _esc("" if entry.get("log_ml") is None else "%.6f" % entry["log_ml"]),
                    _esc("" if entry.get("se") is None else "%.6f" % entry["se"]),
                    (
                        _esc(
                            ""
                            if model.get("seed_log_ml_range") is None
                            else "%.6f" % model["seed_log_ml_range"]
                        )
                        if index == 0
                        else ""
                    ),
                )
            )
    parts.append("</tbody></table>")
    parts.append("<h2>Pairwise log Bayes factors</h2><table><thead><tr>")
    for header in ("a", "b", "log BF (a-b)", "SE", "interpretation"):
        parts.append("<th>%s</th>" % _esc(header))
    parts.append("</tr></thead><tbody>")
    for pair in envelope.get("pairwise") or []:
        parts.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                _esc(pair.get("a")),
                _esc(pair.get("b")),
                _esc("" if pair.get("log_bf") is None else "%.6f" % pair["log_bf"]),
                _esc("" if pair.get("se_log_bf") is None else "%.6f" % pair["se_log_bf"]),
                _esc(pair.get("interpretation")),
            )
        )
    parts.append("</tbody></table>")

    parts.append("<h2>Comparability</h2><table><thead><tr>")
    for header in ("check", "status", "detail"):
        parts.append("<th>%s</th>" % _esc(header))
    parts.append("</tr></thead><tbody>")
    for check in envelope.get("comparability") or []:
        parts.append(
            '<tr><td>%s</td><td class="%s">%s</td><td>%s</td></tr>'
            % (
                _esc(check.get("check")),
                _status_class(check.get("status")),
                _esc(check.get("status")),
                _esc(check.get("detail")),
            )
        )
    parts.append("</tbody></table>")

    beagle = envelope.get("beagle") or {}
    parts.append("<h2>Environment</h2><table><tbody>")
    for key in (
        "required",
        "available",
        "expected_version",
        "observed_version",
        "library_name",
        "library_sha256",
        "directory",
        "source",
        "threads",
        "reason",
        "fix",
    ):
        parts.append("<tr><th>%s</th><td>%s</td></tr>" % (_esc(key), _esc(beagle.get(key))))
    parts.append("</tbody></table>")

    parts.append("<h2>Limitations</h2><ul>")
    for item in envelope.get("limitations") or []:
        parts.append("<li>%s</li>" % _esc(item))
    parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def write_text_artifact(path: Path, text: str) -> Path:
    """Write a rendered artefact (UTF-8, parents created)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def fingerprint_of_envelope(envelope: Dict[str, Any]) -> str:
    """Stable digest of the comparison result (for logs and lock cross-checks)."""
    payload = {
        "method": envelope.get("method"),
        "models": [
            {
                "name": m.get("name"),
                "log_ml": m.get("log_ml"),
                "se": m.get("se"),
                "status": m.get("status"),
            }
            for m in (envelope.get("models") or [])
        ],
        "pairwise": [
            {"a": p.get("a"), "b": p.get("b"), "log_bf": p.get("log_bf")}
            for p in (envelope.get("pairwise") or [])
        ],
    }
    return sha256_hex(canonical_json(payload))[:16]


__all__ = [
    "BeagleEnvironment",
    "CompareError",
    "DEFAULT_CHAIN_LENGTH",
    "DEFAULT_LOG_EVERY",
    "DEFAULT_PATH_SCHEME",
    "DEFAULT_PATH_STEPS",
    "EXPECTED_BEAGLE_VERSION",
    "LIMITATIONS",
    "METHODS",
    "METHOD_PATH_SAMPLING",
    "METHOD_STEPPING_STONE",
    "MarginalLikelihoodRunner",
    "ModelResult",
    "PLACEHOLDER_MARKER",
    "PS_LOG_BASENAME",
    "RESULTS_BASENAMES",
    "STATUS_FAILED",
    "STATUS_NOT_RUN",
    "STATUS_OK",
    "SeedResult",
    "VALID_BEAST_VERSIONS",
    "beagle_runtime_environment",
    "build_analysis_block",
    "build_compare_envelope",
    "check_comparability",
    "comparability_is_ok",
    "estimate_log_ml",
    "estimate_standard_error",
    "fingerprint_of_envelope",
    "interpret_log_bayes_factor",
    "materialise_compare_config",
    "normalise_method",
    "parse_beast_results",
    "parse_config",
    "parse_mle_settings",
    "parse_observed_beagle_version",
    "parse_path_sampling_log",
    "parse_seeds",
    "pairwise_log_bayes_factors",
    "probe_beagle_environment",
    "probe_jvm_arch_data_model",
    "render_csv",
    "render_html",
    "resolve_beast_version",
    "compare_one_model",
    "run_comparison",
    "verify_run_provenance",
    "write_text_artifact",
]
