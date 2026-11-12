# Development

[English](development.md) | [中文](../zh/development.md)

## Layout

Every module under `beastxpy/` appears below, grouped by the stage it serves; the `cli_<name>.py` entry names the eight modules that self-register.

```
beastxpy/
  __init__.py        the version string
  main.py            CLI registration and dispatch, no analysis logic
  api.py             the facade every command goes through
  _contracts.py      schema ids, exit codes, JSON helpers
  utils.py           shared helpers
  config.py          Gate 0: strict YAML parsing
  partition.py       Gate 0: partition checks
  datacheck.py       pre-flight data and calibration checks
  sequence.py        sequence reading and validation
  models.py          data model definitions
  calibration.py     calibration assembly
  validator.py       Gate 1 (pure Python) and Gate 3 (official jars)
  diagnostics.py     Gate 2 calibration diagnostics and diagnose
  model.py           XML assembly
  xml_writer.py      canonical BEAST 1 XML serialisation
  fragments.py       user-supplied XML spliced into the document
  registry.py        the element/model registry
  runner.py          the seed matrix
  assessment.py      ESS/ACT/PSRF/ASDSF for assess
  tracestats.py      trace statistics for trace
  treetools.py       tree-annotate and logcombine
  compare.py         marginal-likelihood model comparison
  report.py          the self-contained HTML report
  lockfile.py        environment lock files
  reproducibility.py fingerprints, LaTeX methods, pipelines
  capabilities.py    the capability matrix
  coverage/          one module per non-standard analysis family
  cli_<name>.py      the commands that self-register: assess,
                     capabilities, check-data, compare, container, lock,
                     report, reproduce
  data/              capabilities.yaml, xml_spec.json
  tools/             the BEAST validator harness and the official jars
deploy/              container definitions
docs/en, docs/zh     this documentation
examples/            the config matrices
tests/               pytest suite
tools/               validation matrices and helpers
```

`main.py` holds no analysis logic: it parses arguments, calls `api.py` or one module, and prints. A command is registered either in `main.py` or by a `beastxpy/cli_<name>.py` module exposing `register(subparsers)`; the latter are discovered automatically, so adding one never requires editing `main.py`.

## Tests

```
python -m pytest -q
python tools/official_validate.py --matrix "examples/config_*.yaml" --mode java-core
python tools/validate_coverage.py
```

## Rules the code follows

* The behavioural contract in [`CONTRACT.md`](../../CONTRACT.md) is normative: gate states, `assess` honesty rules and data-check severities.
* Python 3.10-compatible typing, `from __future__ import annotations`.
* Every file read or written with an explicit `encoding="utf-8"`.
* `subprocess` always with an argument list, never `shell=True`.
* `pathlib.Path` for every path.
* Errors that reach a user carry a next step.
* A diagnostic that cannot be computed is reported as unavailable, never guessed.

## Adding a capability

Add a row to `beastxpy/data/capabilities.yaml` and a test that exercises it. Do not restate the matrix anywhere else; the docs, the CLI and the tests read that file.
