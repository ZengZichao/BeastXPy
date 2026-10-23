"""Multidimensional scaling (MDS) analysis of a pairwise distance table.

The fixture supplies a distance matrix (lower triangle, ``NA`` elsewhere) and
the plugin replaces the assembled phylogenetic document with the MDS model of
``examples/MDS/US_cities.xml``:

* ``<multiDimensionalScalingLikelihood>`` with Gaussian residuals;
* a ``<matrixParameter id="locations">`` whose column parameters are named
  after the taxa (BEAST matches the distance-table labels to these ids);
* a ``<gammaPrior>`` on the residual precision;
* the Oh & Raftery (2001) hierarchical prior: one Gaussian per MDS dimension
  with fixed mean zero and an estimated precision, plus a gamma prior and a
  ``<normalGammaPrecisionGibbsOperator>`` per dimension;
* starting coordinates from classical MDS (principal coordinates) of the
  supplied distances, so the initial posterior is finite and sensible.

Because MDS is treeless, all core tree/alignment likelihood, prior and
operator blocks are discarded.  The distance table is copied next to the
generated XML under the portable relative name ``mds_distances.txt`` (BEAST 1
reads it from the process working directory at parse time).  The absolute
source path never enters the XML, so a run directory can be moved to another
machine together with the XML and the table.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..model import ModelAssemblyError, el, param_node

ANALYSIS_TYPES = ("mds",)


# ---------------------------------------------------------------------------
# Distance table
# ---------------------------------------------------------------------------


def _read_table(path: Path) -> Tuple[List[str], List[List[float]]]:
    try:
        lines = [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    except OSError as exc:
        raise ModelAssemblyError(f"mds: cannot read distance file {path}: {exc}") from exc
    if len(lines) < 3:
        raise ModelAssemblyError(
            f"mds: distance file {path} must contain a header and at least two rows"
        )
    header = lines[0].split()
    if len(header) < 2:
        raise ModelAssemblyError(f"mds: malformed header in {path}")
    taxa = header[1:]
    if len(set(taxa)) != len(taxa):
        raise ModelAssemblyError(f"mds: duplicate taxon labels in {path}")
    size = len(taxa)
    if len(lines) - 1 != size:
        raise ModelAssemblyError(f"mds: {path} has {size} column labels but {len(lines) - 1} rows")
    index = {name: i for i, name in enumerate(taxa)}
    matrix = [[0.0] * size for _ in range(size)]
    seen = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != size + 1:
            raise ModelAssemblyError(f"mds: row {line!r} in {path} has the wrong width")
        label = fields[0]
        if label not in index:
            raise ModelAssemblyError(f"mds: unknown row label {label!r} in {path}")
        row = index[label]
        seen.add(row)
        for column, token in enumerate(fields[1:]):
            if token.upper() == "NA":
                continue
            try:
                value = float(token)
            except ValueError as exc:
                raise ModelAssemblyError(
                    f"mds: non-numeric distance {token!r} in row {label!r}"
                ) from exc
            if value < 0:
                raise ModelAssemblyError(f"mds: negative distance {value} in {path}")
            if column < row:
                if matrix[column][row] and not math.isclose(
                    matrix[column][row], value, rel_tol=1e-9, abs_tol=1e-12
                ):
                    raise ModelAssemblyError(
                        f"mds: asymmetric distances for {taxa[column]}/{label}"
                    )
                matrix[column][row] = value
                matrix[row][column] = value
            elif column == row and value != 0.0:
                raise ModelAssemblyError(f"mds: diagonal distance for {label} is not zero")
            else:
                if matrix[row][column] and not math.isclose(
                    matrix[row][column], value, rel_tol=1e-9, abs_tol=1e-12
                ):
                    raise ModelAssemblyError(
                        f"mds: asymmetric distances for {label}/{taxa[column]}"
                    )
                matrix[row][column] = value
                matrix[column][row] = value
    if len(seen) != size:
        raise ModelAssemblyError(f"mds: distance file {path} is missing rows")
    return taxa, matrix


# ---------------------------------------------------------------------------
# Classical MDS (principal coordinates)
# ---------------------------------------------------------------------------


def _jacobi(matrix: List[List[float]]) -> Tuple[List[float], List[List[float]]]:
    """Eigen-decomposition of a small symmetric matrix (cyclic Jacobi)."""
    size = len(matrix)
    a = [row[:] for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]
    for _ in range(100):
        off = sum(a[i][j] * a[i][j] for i in range(size) for j in range(i + 1, size))
        if off < 1e-18:
            break
        for p in range(size - 1):
            for q in range(p + 1, size):
                if abs(a[p][q]) < 1e-18:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = 1.0 / (abs(theta) + math.sqrt(theta * theta + 1.0))
                if theta < 0:
                    t = -t
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(size):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(size):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(size):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    return [a[i][i] for i in range(size)], v


def _classical_mds(matrix: List[List[float]], dimensions: int):
    size = len(matrix)
    squared = [[matrix[i][j] ** 2 for j in range(size)] for i in range(size)]
    row_means = [sum(row) / size for row in squared]
    grand = sum(row_means) / size
    centered = [
        [-0.5 * (squared[i][j] - row_means[i] - row_means[j] + grand) for j in range(size)]
        for i in range(size)
    ]
    values, vectors = _jacobi(centered)
    order = sorted(range(size), key=lambda i: values[i], reverse=True)
    coords = [[0.0] * size for _ in range(dimensions)]
    for axis in range(dimensions):
        if axis >= size:
            continue
        source = order[axis]
        scale = math.sqrt(max(values[source], 0.0))
        for i in range(size):
            coords[axis][i] = vectors[i][source] * scale
    return coords


def _stress(coords: List[List[float]], matrix: List[List[float]]) -> float:
    size = len(matrix)
    fitted = [
        [
            math.dist(
                [coords[d][i] for d in range(len(coords))],
                [coords[d][j] for d in range(len(coords))],
            )
            for j in range(size)
        ]
        for i in range(size)
    ]
    return sum((fitted[i][j] - matrix[i][j]) ** 2 for i in range(size) for j in range(i + 1, size))


# ---------------------------------------------------------------------------
# Validation / application
# ---------------------------------------------------------------------------


def validate(raw: Dict[str, Any], errors: List[str]) -> None:
    file_name = raw.get("distance_file")
    if not isinstance(file_name, str) or not file_name.strip():
        errors.append(
            "analysis.distance_file: a distance-table file (lower triangle, "
            "'NA' elsewhere) is required for MDS"
        )
    dimensions = raw.get("dimensions", 2)
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 2:
        errors.append(f"analysis.dimensions: must be an integer >= 2 (got {dimensions!r})")
    precision = raw.get("residual_precision", 1.0)
    if isinstance(precision, bool) or not isinstance(precision, (int, float)) or precision <= 0:
        errors.append(f"analysis.residual_precision: must be a positive number (got {precision!r})")
    for key in ("residual_shape", "residual_scale"):
        if key not in raw:
            continue
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"analysis.{key}: must be a positive number (got {value!r})")


def _resolve_distance_file(analysis: Dict[str, Any], config: Any) -> Path:
    raw_name = str(analysis["distance_file"])
    candidate = Path(raw_name)
    if candidate.is_absolute():
        return candidate
    partitions = getattr(config, "partitions", [])
    source = None
    if partitions:
        source = getattr(partitions[0].alignment, "source_file", None)
    if source:
        return Path(source).resolve().parent / candidate
    return candidate.resolve()


def apply(
    root: Dict[str, Any],
    analysis: Dict[str, Any],
    config: Any,
    assembler: Any,
) -> Dict[str, Any]:
    partitions = getattr(config, "partitions", [])
    if not partitions:
        raise ModelAssemblyError("mds: at least one alignment is required (for taxon names)")
    alignment = getattr(assembler, "_alignment_sources", {}).get(
        partitions[0].alignment.id, partitions[0].alignment
    )
    alignment_taxa = [seq.taxon for seq in alignment.sequences]
    distance_path = _resolve_distance_file(analysis, config)
    if not distance_path.is_file():
        raise ModelAssemblyError(f"mds: distance file not found: {distance_path}")
    # BEAST's parser only accepts fileName; inline data are not supported.
    # Copy the table next to the XML instead of embedding an absolute path.
    portable_name = "mds_distances.txt"
    try:
        assembler._sidecar_files[portable_name] = distance_path.resolve()
    except AttributeError as exc:  # pragma: no cover - assembler contract
        raise ModelAssemblyError("mds: the model assembler cannot register sidecar files") from exc
    taxa, matrix = _read_table(distance_path)
    if set(taxa) != set(alignment_taxa):
        missing = sorted(set(alignment_taxa) - set(taxa))
        extra = sorted(set(taxa) - set(alignment_taxa))
        raise ModelAssemblyError(
            "mds: distance-table labels must match the alignment taxa "
            f"(missing: {missing or 'none'}; extra: {extra or 'none'})"
        )

    dimensions = int(analysis.get("dimensions", 2))
    if dimensions >= len(taxa):
        raise ModelAssemblyError(
            f"mds: dimensions ({dimensions}) must be smaller than the number of "
            f"taxa ({len(taxa)})"
        )
    coords = _classical_mds(matrix, dimensions)
    pairs = len(taxa) * (len(taxa) - 1) // 2
    ssr = _stress(coords, matrix)
    residual_precision = float(analysis.get("residual_precision", 1.0))
    residual_shape = float(analysis.get("residual_shape", 1.0))
    if "residual_scale" in analysis:
        residual_scale = float(analysis["residual_scale"])
    else:
        residual_scale = max(pairs / ssr, 1e-8) if ssr > 1e-12 else 1.0

    nodes: List[Dict[str, Any]] = []
    nodes.append(
        param_node(
            "mds.residual.precision",
            residual_precision,
            lower=1e-9,
        )
    )
    for axis in range(1, dimensions + 1):
        nodes.append(param_node(f"mds.fitted.mean.{axis}", 0.0, estimate=False))
        nodes.append(
            param_node(
                f"mds.fitted.precision.{axis}",
                1.0,
                lower=1e-9,
            )
        )

    column_nodes = []
    for column, taxon in enumerate(taxa):
        values = " ".join(f"{coords[axis][column]:.10g}" for axis in range(dimensions))
        column_nodes.append(param_node(taxon, values, dimension=dimensions))
    nodes.append(el("matrixParameter", attrib={"id": "locations"}, children=column_nodes))
    for axis in range(1, dimensions + 1):
        nodes.append(
            el(
                "maskedParameter",
                attrib={
                    "id": f"locations.{axis}",
                    "from": str(axis),
                    "every": str(dimensions),
                },
                children=[el("matrixParameter", attrib={"idref": "locations"})],
            )
        )
    for axis in range(1, dimensions + 1):
        nodes.append(
            el(
                "normalDistributionModel",
                attrib={"id": f"normalDistribution.{axis}"},
                children=[
                    el(
                        "mean",
                        children=[el("parameter", attrib={"idref": f"mds.fitted.mean.{axis}"})],
                    ),
                    el(
                        "precision",
                        children=[
                            el(
                                "parameter",
                                attrib={"idref": f"mds.fitted.precision.{axis}"},
                            )
                        ],
                    ),
                ],
            )
        )
    for axis in range(1, dimensions + 1):
        nodes.append(
            el(
                "distributionLikelihood",
                attrib={"id": f"hierarchicalPrior.{axis}"},
                children=[
                    el(
                        "distribution",
                        children=[
                            el(
                                "normalDistributionModel",
                                attrib={"idref": f"normalDistribution.{axis}"},
                            )
                        ],
                    ),
                    el(
                        "data",
                        children=[el("maskedParameter", attrib={"idref": f"locations.{axis}"})],
                    ),
                ],
            )
        )

    nodes.append(
        el(
            "multiDimensionalScalingLikelihood",
            attrib={
                "id": "mdsLikelihood",
                "fileName": portable_name,
                "mdsDimension": str(dimensions),
            },
            children=[
                el(
                    "locations",
                    children=[el("matrixParameter", attrib={"idref": "locations"})],
                ),
                el(
                    "mdsPrecision",
                    children=[el("parameter", attrib={"idref": "mds.residual.precision"})],
                ),
            ],
        )
    )
    nodes.append(
        el(
            "gammaPrior",
            attrib={
                "id": "prior.residual",
                "shape": f"{residual_shape:g}",
                "scale": f"{residual_scale:.10g}",
                "offset": "0.0",
            },
            children=[el("parameter", attrib={"idref": "mds.residual.precision"})],
        )
    )
    for axis in range(1, dimensions + 1):
        mean_square = sum(coords[axis - 1][i] ** 2 for i in range(len(taxa))) / len(taxa)
        scale = 1.0 / mean_square if mean_square > 1e-12 else 1.0
        nodes.append(
            el(
                "gammaPrior",
                attrib={
                    "id": f"prior.precision.{axis}",
                    "shape": "1.0",
                    "scale": f"{scale:.10g}",
                    "offset": "0.0",
                },
                children=[el("parameter", attrib={"idref": f"mds.fitted.precision.{axis}"})],
            )
        )

    operators: List[Dict[str, Any]] = [
        el(
            "randomWalkOperator",
            attrib={"windowSize": "1.0", "weight": "20"},
            children=[el("parameter", attrib={"idref": "locations"})],
        ),
        el(
            "scaleOperator",
            attrib={"scaleFactor": "0.75", "weight": "1"},
            children=[el("parameter", attrib={"idref": "mds.residual.precision"})],
        ),
    ]
    for axis in range(1, dimensions + 1):
        operators.append(
            el(
                "normalGammaPrecisionGibbsOperator",
                attrib={"weight": "1"},
                children=[
                    el(
                        "likelihood",
                        children=[
                            el(
                                "distributionLikelihood",
                                attrib={"idref": f"hierarchicalPrior.{axis}"},
                            )
                        ],
                    ),
                    el(
                        "prior",
                        children=[el("gammaPrior", attrib={"idref": f"prior.precision.{axis}"})],
                    ),
                ],
            )
        )
    nodes.append(el("operators", attrib={"id": "operators"}, children=operators))

    prior_children: List[Dict[str, Any]] = [el("gammaPrior", attrib={"idref": "prior.residual"})]
    for axis in range(1, dimensions + 1):
        prior_children.append(el("gammaPrior", attrib={"idref": f"prior.precision.{axis}"}))
        prior_children.append(
            el("distributionLikelihood", attrib={"idref": f"hierarchicalPrior.{axis}"})
        )
    mcmc_children: List[Dict[str, Any]] = [
        el(
            "posterior",
            attrib={"id": "posterior"},
            children=[
                el("prior", attrib={"id": "prior"}, children=prior_children),
                el(
                    "likelihood",
                    attrib={"id": "likelihood"},
                    children=[
                        el(
                            "multidimensionalScalingLikelihood",
                            attrib={"idref": "mdsLikelihood"},
                        )
                    ],
                ),
            ],
        ),
        el("operators", attrib={"idref": "operators"}),
    ]

    loggers = getattr(config, "loggers", None)
    trace_name = ""
    trace_every = None
    screen_every = None
    if loggers is not None:
        trace_name = str(loggers.trace_log.get("file_name") or "")
        trace_every = loggers.trace_log.get("log_every")
        screen_every = loggers.screen_log.get("log_every")
    mcmc_children.append(
        el(
            "log",
            attrib={"id": "screenLog", "logEvery": str(screen_every or 1000)},
            children=[
                el(
                    "column",
                    attrib={"label": "Posterior", "dp": "4", "width": "12"},
                    children=[el("posterior", attrib={"idref": "posterior"})],
                ),
                el(
                    "column",
                    attrib={"label": "Prior", "dp": "4", "width": "12"},
                    children=[el("prior", attrib={"idref": "prior"})],
                ),
                el(
                    "column",
                    attrib={"label": "Likelihood", "dp": "4", "width": "12"},
                    children=[el("likelihood", attrib={"idref": "likelihood"})],
                ),
                el(
                    "column",
                    attrib={"label": "MDSPrecision", "sf": "6", "width": "12"},
                    children=[el("parameter", attrib={"idref": "mds.residual.precision"})],
                ),
            ],
        )
    )
    file_children: List[Dict[str, Any]] = [
        el("posterior", attrib={"idref": "posterior"}),
        el("prior", attrib={"idref": "prior"}),
        el("likelihood", attrib={"idref": "likelihood"}),
        el("parameter", attrib={"idref": "mds.residual.precision"}),
    ]
    for axis in range(1, dimensions + 1):
        file_children.append(el("parameter", attrib={"idref": f"mds.fitted.precision.{axis}"}))
    file_children.append(el("matrixParameter", attrib={"idref": "locations"}))
    mcmc_children.append(
        el(
            "log",
            attrib={
                "id": "fileLog",
                "logEvery": str(trace_every or 1000),
                "fileName": trace_name or "mds.log",
            },
            children=file_children,
        )
    )
    nodes.append(
        el(
            "mcmc",
            attrib={
                "id": "mcmc",
                "chainLength": str(int(getattr(config.mcmc, "chain_length", 10000000))),
                "autoOptimize": "true",
            },
            children=mcmc_children,
        )
    )

    nodes.append(
        el(
            "report",
            children=[
                el(
                    "property",
                    attrib={"name": "timer"},
                    children=[el("object", attrib={"idref": "mcmc"})],
                )
            ],
        )
    )

    root["children"] = nodes
    return root
