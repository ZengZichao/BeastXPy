# Development

[English](development.md) | [中文](../zh/development.md)

## Layout

```
beastxpy/            package
  main.py            CLI registration and dispatch only
  api.py             the facade
  _contracts.py      schema ids, exit codes, JSON helpers
  config.py          Gate 0
  validator.py       Gate 1 and Gate 3
  model.py           XML assembly
  registry.py        the element/model registry
  runner.py          the seed matrix
  coverage/          one module per non-standard analysis family
  data/              capabilities.yaml, spec data
  tools/jars/        the official BEAST jars
deploy/              container definitions
docs/en, docs/zh     this documentation
examples/            the config matrices
tests/               pytest suite
tools/               validation matrices and helpers
```

`main.py` holds no business logic. A command is a `beastxpy/cli_<name>.py` module exposing `register(subparsers)`; discovery is automatic.

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
