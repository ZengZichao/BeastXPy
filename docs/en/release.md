# Release

[English](release.md) | [中文](../zh/release.md)

How BeastXPy versions are cut and published. The whole pipeline runs on GitHub Actions: the machine that triggers a release only needs `git` and push access, never a JDK, BEAST or BEAGLE. You can therefore cut a release from any computer, including one that only holds a downloaded copy of the repository.

## One-time setup

1. **GitHub repository.** Push this repository to GitHub and make sure Actions are enabled for it.
2. **PyPI trusted publishing.** The package is published to PyPI without any stored token. On pypi.org open *Account → Publishing* and add a pending publisher with exactly these values:
- PyPI project name: `beastxpy`
- Owner: your GitHub user or organisation name
- Repository: the repository name
- Workflow filename: `release.yml`
- Environment name: `pypi`
3. **Zenodo.** Sign in at zenodo.org with the GitHub account, authorise the Zenodo–GitHub integration, and switch the repository on. From then on every GitHub release is archived automatically and receives its own version DOI plus a concept DOI covering all versions. Zenodo reads the release record and `CITATION.cff`.

## Cutting a release

One command updates every place the version is declared, and the test suite verifies that nothing was missed:

```
python tools/bump_version.py 0.2.0
python -m pytest -q
git add pyproject.toml beastxpy/__init__.py CITATION.cff README.md README.zh-CN.md docs tools
git commit -m "Release 0.2.0"
git tag v0.2.0
git push origin main --tags
```

The tag push starts the release workflow (`.github/workflows/release.yml`), which performs, in order:

1. verifies that the tag equals the version declared in `pyproject.toml`, `beastxpy/__init__.py` and `CITATION.cff` (`tools/check_release_tag.py`);
2. builds the sdist and the wheel and validates both with `twine check --strict`;
3. builds the jar-free `beastxpy-core` wheel;
4. installs the wheel and runs `beastxpy --version` and `beastxpy capabilities` as a smoke check;
5. publishes the package to PyPI (trusted publishing, no token);
6. creates the GitHub release carrying the sdist, the wheel and the core wheel — this is the record Zenodo archives.

## Verifying a release

```
python -m pip install beastxpy==0.2.0
beastxpy --version
beastxpy check-environment
```

* PyPI shows the new version on the project page within a few minutes.
* The GitHub Releases page lists the tag with the three distribution files.
* Zenodo reserves the version DOI within a few minutes of the release; the DOI is visible on your Zenodo dashboard and on the release page.

## After archiving

When Zenodo has issued the first DOI, record it in `CITATION.cff` (add a `doi:` field and update the message), and relax the corresponding guards in `tests/test_release_contract.py` so the test asserts the real DOI instead of forbidding one. Commit that change and it travels with the next release.

## Version numbering

Versions follow semantic versioning: `MAJOR.MINOR.PATCH`. Bump the minor number for new models or commands, the patch number for fixes. Never edit version strings by hand; `tools/bump_version.py` exists so that `pyproject.toml`, the package, `CITATION.cff`, the READMEs and the docs stay consistent, and `tests/test_release_contract.py` fails when they do not.
