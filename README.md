# BeastXPy

BeastXPy generates, validates and diagnoses **BEAST 1** divergence-time analyses. It turns a strict YAML description into a BEAST XML document, checks that document against the packaged official BEAST 1.10.4 and BEAST X 10.5.0 jars (Gate 3; the evidence each mode proves is described under [Verification](#verification)), runs the seed matrix, and reports what the MCMC output actually looks like.

Version 0.1.0. Python 3.10+.

## Install

```
python -m pip install .
beastxpy --version
```

Both BEAST jars ship inside the package, so Gate 3 works without a separate BEAST installation. A JDK is needed for validation and runs (`JAVA_HOME`); native BEAGLE is needed for a real likelihood (`BEASTX_BEAGLE_1_10_4_LIB`, `BEASTX_BEAGLE_10_5_0_LIB`). Step-by-step setup: [docs/en/installation.md](docs/en/installation.md).

## The six-command workflow

```
beastxpy generate --config examples/config_subst_jc69.yaml --output out.xml --beast-validate
beastxpy check-data --config examples/config_subst_jc69.yaml
beastxpy run --config examples/config_subst_jc69.yaml --workdir runs/demo --seeds 1,2
beastxpy assess runs/demo --json runs/demo/assessment.json
beastxpy report runs/demo --output runs/demo/report.html --lang en
beastxpy lock --config examples/config_subst_jc69.yaml --output beastxpy.lock
```

These six are the everyday workflow; `beastxpy --help` lists all 21 commands (`quick`, `validate`, `trace`, `tree-annotate`, `logcombine`, `compare`, `pipeline`, `reproduce`, ...). `beastxpy capabilities` prints the authoritative feature x version matrix.

## What it does not do

BeastXPy does not assert convergence, does not select models, and does not judge whether a model suits your data. `assess` is diagnostics; `compare` computes marginal likelihoods and leaves the interpretation to you. See [docs/en/limitations.md](docs/en/limitations.md).

## Platform scope and related tools

BeastXPy targets the BEAST 1 lineage (1.10.4) and its maintained continuation BEAST X 10.5.0, not BEAST 2. BEAST 2 is a different program with a different XML schema and package ecosystem; existing BEAST 1 analyses and models (EBSP, StarBEAST1, random local clocks, empirical codon models, MDS) require that lineage. The novelty claimed here is the combination of four validation gates, packaged official jars, honest multi-chain diagnostics and lock-file reproducibility — not "YAML to XML" by itself. A comparison with BEAUti, babette (BEAST 2), BEASTifier, RevBayes and BEASTling is in [docs/en/index.md#positioning](docs/en/index.md#positioning); the Chinese overview is in [README.zh-CN.md](README.zh-CN.md) and [docs/zh/index.md](docs/zh/index.md).

## Documentation

English: [docs/en/index.md](docs/en/index.md) · [中文](README.zh-CN.md) · [docs/zh/index.md](docs/zh/index.md)

Installation, quick start, configuration, the full CLI reference, the four validation gates, native BEAGLE, pipelines, assessment, reporting, model comparison, reproducibility, custom XML fragments, containers, troubleshooting, the Python API, development notes, limitations and an FAQ.

## Verification

The repository carries three reproducible checks:

```
python -m pytest -q
python tools/official_validate.py --matrix "examples/config_*.yaml" --mode java-core
python tools/validate_coverage.py
```

Validation evidence is layered, and every claim in this README and in `beastxpy capabilities` uses `java-core` unless it names `beagle`:

| Mode | Parsers | Likelihood | What it proves |
|---|---|---|---|
| `parse`, `java-core` | official BEAST parsers plus BeastXPy's `TreeLikelihood` shim | none (construct-only) | the XML is accepted and the model graph can be built |
| `beagle` | full official parser set | real native hmsbeagle likelihood | full parser + likelihood validation |

Only `beagle` is full official validation. Run the all-example matrix on each platform and BEAST version you intend to claim, and report the platform x BEAST version x example count from that run:

```
python tools/official_validate.py --matrix "examples/config_*.yaml" \
    --mode beagle --versions 1.10.4 10.5.0
```

See [docs/en/validation.md](docs/en/validation.md) for the mode details, including a recorded platform x version x example-count matrix (16/16 in `beagle` mode for both BEAST 1.10.4 with BEAGLE 3.1.2 and BEAST X 10.5.0 with BEAGLE 4.0.1 on the validation host).

`pytest -q` intentionally fails the Gate-3 tests on a machine without a JDK (fail-not-skip is a locked policy). For the Java-free subset use:

```
python -m pytest -q -m "not java"
```

CI runs the full suite (with a JDK and BEAST) on 3 OSes x 2 Python versions x 2 BEAST versions, plus a build-from-source `beagle` evidence job and a byte-level `xml_spec.json` drift guard.

## Data availability

The alignments under `examples/` are short published/reference sequences used only to exercise the workflow; no new empirical data were generated for this repository. They are not intended for biological inference. Validation logs recorded for the paper are in `docs/en/validation.md`. The end-to-end reference-data case study (config, generated XML, lock, assessment and report) is in `examples/empirical/`; it uses the same shipped tutorial alignment and is explicitly a workflow demonstration, not a biological result.

## Author

BeastXPy is developed and maintained by **ZengZichao (曾子超)** —zengzichao@sjtu.edu.cn · ORCID [0000-0001-6553-970X](https://orcid.org/0000-0001-6553-970X).

## Licence

MIT AND LGPL-2.1-only. BEAST itself is LGPL-2.1-only and the jars keep their own `META-INF/LICENSE.txt` and `META-INF/NOTICE.txt`. See `LICENSE` and `NOTICE`.
