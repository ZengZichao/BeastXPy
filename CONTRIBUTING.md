# Contributing to BeastXPy

Thank you for improving BeastXPy. This project is a scientific tool: a change that makes the XML, a statistic or a documented claim less trustworthy is a regression even if the tests pass.

## Setup

```
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r requirements-test.txt
```

A JDK is required for Gate 3 and for the full test suite. Native BEAGLE is required to run or compare chains (not for XML generation).

## Tests

```
python -m pytest -q                 # full suite, needs a JDK
python -m pytest -q -m "not java"   # Java-free subset
```

Gate-3 tests fail rather than skip when no JDK is found; this is deliberate (a validation claim must never quietly disappear). The `java` marker is the escape hatch for local work and is deselected by `-m "not java"`.

## Quality gates

The CI runs exactly these commands:

```
python -m flake8 beastxpy tools
python -m black --check --line-length 100 beastxpy tests tools
python -m mypy beastxpy
python -m pytest -q -m "not java" --cov=beastxpy --cov-fail-under=70
```

Format with `python -m black --line-length 100 beastxpy tests tools` before opening a pull request. Do not add blanket `# type: ignore` or `# noqa` comments; fix the type or document why the suppression is unavoidable.

## Scientific invariants

- Never fabricate a statistic. When a value is not computable, report `not_computable`/`None` with a reason. In particular, do not turn a not-computable diagnostic into zero or into a guessed number.
- Keep the validation evidence layered. `java-core` is construct-only and uses the project `TreeLikelihood` shim; only `beagle` is full-parser, real-likelihood validation. Update `capabilities.yaml` reasons and the README together if the evidence changes.
- Add a reference-value or analytic test for every new numerical estimator. "It runs" is not a numerical test.
- Golden XML files under `tests/golden/` are regression artefacts. Refresh them only with the documented refresh mechanism, and explain why the XML changed.

## Bundled BEAST jars

`beastxpy/tools/jars/` must contain the unmodified official release artifacts. The SHA-256 values are recorded in `NOTICE`, `tools/jars/MANIFEST.txt` and `data/xml_spec.json`; `tools/build_jars.py` checks them when it writes a manifest. Never commit a locally rebuilt jar as a release artifact, and never record absolute paths, user names or host names in a build manifest.

## Documentation

English and Chinese pages are kept in one-to-one correspondence. If you change a default, a flag, a threshold or a validation claim, update both `docs/en/` and `docs/zh/`, plus the command reference. Reference DOIs should be checked against the publisher before a release.

## Releases

Versions are cut with `tools/bump_version.py` and published by pushing a `vX.Y.Z` tag; the release workflow builds, checks and publishes the package and the release record. The full procedure, including the one-time PyPI and Zenodo setup, is in [docs/en/release.md](docs/en/release.md).

## Repository URL and archive DOI

`CITATION.cff` does not assert a repository URL; add the real URL and the Zenodo DOI once the repository is public and the first release is archived (also relax the corresponding guards in `tests/test_release_contract.py`). This is a release task, not something a contributor can infer from the code.
