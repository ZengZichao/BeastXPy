"""Marginal-likelihood estimation (path sampling / stepping stone).

The standard analysis is left untouched: BEAUti's MLE component adds a
``<marginalLikelihoodEstimator>`` that re-uses the existing ``<mcmc>`` as its
sampler, a ``<pathLikelihood>`` from the joint posterior to the prior, and two
estimators that read the path-sampling log
(``<pathSamplingAnalysis>`` and ``<steppingStoneSamplingAnalysis>``).

BEAST constructs *all* top-level objects when it parses a document, and the
two analysis blocks open their log file at construction time.  A freshly
generated document therefore needs a log file that already exists; BEAUti has
exactly the same requirement (its MLE block is normally run after the chain
has produced the log).  BeastXPy writes a small, correctly headed placeholder
log next to the generated document's log target so the document is
constructible, and marks the estimator log ``overwrite="true"`` so a real run
replaces the placeholder.  The placeholder is never used as a scientific
result: it is only what lets BEAST instantiate the analysis blocks.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

from ..model import el
from . import find_all

ANALYSIS_TYPES = ("mle", "path_sampling", "stepping_stone", "marginal_likelihood")

_PATH_SCHEMES = ("linear", "oneSidedBeta", "beta", "betaQuantile", "sigmoid")


def _declaration(root: Dict[str, Any], tag: str, id_: str) -> Any:
    for node in find_all(root, tag):
        attrib = node.get("attrib") or {}
        if attrib.get("id") == id_:
            return node
    return None


def _int(raw: Dict[str, Any], key: str, default: int, errors: List[str], where: str) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{where}.{key}: must be an integer (got {value!r})")
        return default
    return value


def _float(raw: Dict[str, Any], key: str, default: float, errors: List[str], where: str) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where}.{key}: must be numeric (got {value!r})")
        return default
    return float(value)


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    where = "analysis"
    chain_length = _int(raw, "chain_length", 5000, errors, where)
    if chain_length < 1:
        errors.append(f"{where}.chain_length: must be >= 1 (got {chain_length})")
    path_steps = _int(raw, "path_steps", 8, errors, where)
    if path_steps < 2:
        errors.append(f"{where}.path_steps: a path needs at least 2 steps (got {path_steps})")
    scheme = raw.get("path_scheme", "oneSidedBeta")
    if not isinstance(scheme, str) or scheme not in _PATH_SCHEMES:
        errors.append(
            f"{where}.path_scheme: unknown {scheme!r}; supported: " + ", ".join(_PATH_SCHEMES)
        )
    log_every = _int(raw, "log_every", 50, errors, where)
    if log_every < 1:
        errors.append(f"{where}.log_every: must be >= 1 (got {log_every})")
    if "alpha" in raw:
        alpha = _float(raw, "alpha", 0.3, errors, where)
        if not 0.0 < alpha < 1.0:
            errors.append(f"{where}.alpha: must be in (0, 1) (got {alpha})")
    if "beta" in raw:
        beta = _float(raw, "beta", 0.3, errors, where)
        if not 0.0 < beta < 1.0:
            errors.append(f"{where}.beta: must be in (0, 1) (got {beta})")
    for key in ("ps_file", "results_file"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{where}.{key}: must be a non-empty path string")


def _placeholder_log(path: Path, steps: int) -> None:
    """Create a valid path-sampling log if one does not exist yet."""
    header = "state\tpathLikelihood.delta\tpathLikelihood.theta"
    if path.exists():
        try:
            first = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            first = []
        if any(header in line for line in first[:10]):
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["# BeastXPy placeholder path-sampling log (replaced by a real run)"]
    rows.append(header)
    for index in range(max(2, steps + 1)):
        beta = index / max(1, steps)
        rows.append(f"{index}\t{-0.01 * index:.6f}\t{beta:.6f}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _attribute_value(raw: Dict[str, Any], key: str, default: float) -> str:
    value = raw.get(key, default)
    return f"{float(value):g}"


def apply(
    root: Dict[str, Any], analysis: Dict[str, Any], config: Any, assembler: Any
) -> Dict[str, Any]:
    mcmc = _declaration(root, "mcmc", "mcmc")
    posterior = _declaration(root, "posterior", "posterior")
    prior = _declaration(root, "prior", "prior")
    if mcmc is None or posterior is None or prior is None:
        raise ValueError(
            "mle requires the standard <mcmc>/<posterior>/<prior> objects; "
            "the core analysis was not assembled"
        )

    name = str(getattr(config, "analysis_name", "analysis"))
    scheme = str(analysis.get("path_scheme", "oneSidedBeta"))
    chain_length = int(analysis.get("chain_length", 5000))
    path_steps = int(analysis.get("path_steps", 8))
    log_every = int(analysis.get("log_every", 50))

    temp_root = Path(tempfile.gettempdir()) / "beastxpy_mle"
    ps_file = analysis.get("ps_file")
    if ps_file:
        ps_path = Path(str(ps_file)).expanduser()
        if not ps_path.is_absolute():
            ps_path = temp_root / ps_path
    else:
        ps_path = temp_root / f"{name}.ps.log"
    results_path = analysis.get("results_file")
    if results_path:
        results = Path(str(results_path)).expanduser()
        if not results.is_absolute():
            results = temp_root / results
    else:
        results = temp_root / f"{name}.mle.txt"

    _placeholder_log(ps_path, path_steps)
    ps_file_xml = str(ps_path).replace("\\", "/")
    results_xml = str(results).replace("\\", "/")

    attributes: Dict[str, Any] = {
        "chainLength": str(chain_length),
        "pathSteps": str(path_steps),
        "pathScheme": scheme,
    }
    if scheme in ("oneSidedBeta", "beta"):
        attributes["beta"] = _attribute_value(analysis, "beta", 0.3)
    if scheme in ("beta", "betaQuantile", "sigmoid"):
        attributes["alpha"] = _attribute_value(analysis, "alpha", 0.3)

    estimator = el(
        "marginalLikelihoodEstimator",
        attrib=attributes,
        children=[
            el("samplers", children=[el("mcmc", attrib={"idref": "mcmc"})]),
            el(
                "pathLikelihood",
                attrib={"id": "pathLikelihood"},
                children=[
                    el("source", children=[el("posterior", attrib={"idref": "posterior"})]),
                    el("destination", children=[el("prior", attrib={"idref": "prior"})]),
                ],
            ),
            el(
                "log",
                attrib={
                    "id": "pathSamplingLog",
                    "logEvery": str(log_every),
                    "fileName": ps_file_xml,
                    "overwrite": "true",
                },
                children=[el("pathLikelihood", attrib={"idref": "pathLikelihood"})],
            ),
        ],
    )

    def analysis_block(tag: str) -> Dict[str, Any]:
        return el(
            tag,
            attrib={"fileName": ps_file_xml, "resultsFileName": results_xml},
            children=[
                el("likelihoodColumn", attrib={"name": "pathLikelihood.delta"}),
                el("thetaColumn", attrib={"name": "pathLikelihood.theta"}),
            ],
        )

    # BEAST builds these blocks in document order; place the estimator before
    # the estimators that consume its log.
    mcmc_index = root.setdefault("children", []).index(mcmc)
    insertion = [
        estimator,
        analysis_block("pathSamplingAnalysis"),
        analysis_block("steppingStoneSamplingAnalysis"),
    ]
    for offset, node in enumerate(insertion):
        root["children"].insert(mcmc_index + 1 + offset, node)
    return root
