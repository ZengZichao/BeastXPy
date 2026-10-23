# BeastXPy 0.1.0

[English](index.md) | [中文](../zh/index.md)

BeastXPy generates, validates and diagnoses **BEAST 1** divergence-time analyses. It turns a strict YAML description of an analysis into a BEAST XML document, checks that document with the official BEAST 1.10.4 and BEAST X 10.5.0 jars, runs the seed matrix, and helps you judge what came back out of MCMC.

It is a thin, opinionated layer, not a replacement for BEAST:

* **Strict configuration.** Unknown keys are errors, not ignored. A typo in a prior name stops you before BEAST ever runs.
* **Four gates.** Gate 0 parses the config, Gate 1 validates the XML structurally, Gate 2 checks the calibrations, Gate 3 runs the official BEAST jars on the result.
* **Honest diagnostics.** `assess` reports ESS, ACT, PSRF and ASDSF, and says `not_computable` rather than inventing a number when the inputs are missing.
* **Reproducibility artefacts.** Fingerprints, an environment lock, a self-contained HTML report and Snakemake/Nextflow pipelines.

## Author

BeastXPy is developed and maintained by **ZengZichao (曾子超)** (zengzichao@sjtu.edu.cn, ORCID [0000-0001-6553-970X](https://orcid.org/0000-0001-6553-970X)).

## What BeastXPy does not do

It does not decide whether your model is right for your data, it does not declare convergence, and it does not choose a model for you. `compare` computes marginal likelihoods, but interpreting a Bayes factor is your job. See [limitations](limitations.md).

## Positioning

BeastXPy is an independent workflow layer around BEAST 1 / BEAST X; it is not produced by, and not affiliated with, the BEAST developers. How it relates to the tools you may already use:

| Tool | Target platform | What it does | BeastXPy's relationship |
|---|---|---|---|
| [BEAUti](https://github.com/beast-dev/beast-mcmc) (Drummond et al. 2012) | BEAST 1 and BEAST 2 | GUI that builds a BEAST XML interactively | replaces the GUI with a strict, reviewable YAML file; every BEAUti-adjustable model BeastXPy supports is listed by `list-models` |
| [babette](https://cran.r-project.org/package=babette) | BEAST 2 | R package that writes BEAST 2 XML through beautier and runs beastier | same automation goal on a different platform; its XML schema, packages and parsers are not interchangeable with BEAST 1 |
| [BEASTifier](https://github.com/josephwb/BEASTifier) | BEAST 1 | C++ generator that enumerates XML files for simulation experiments | complementary batch generator; no validation gates, diagnostics, locking or reporting |
| [RevBayes](https://revbayes.github.io/) (Höhna et al. 2016) | RevBayes | interactive model-specification language and inference engine | an alternative inference platform, not a BEAST XML generator |
| [BEASTling](https://github.com/lmaurits/BEASTling) | BEAST 2 | config-file XML generation aimed at linguistics | same config-file idea, but for BEAST 1 / BEAST X, phylogenetics-first, with validation gates |
| [Tracer](https://github.com/beast-dev/tracer) (Rambaut et al. 2018) | BEAST 1 and BEAST 2 | desktop app for summarising MCMC logs | `assess`/`trace` reproduce ESS/ACT/HPD headlessly and add PSRF/ASDSF across seeds; see [references](references.md) |
| LogCombiner / TreeAnnotator (BEAST) | BEAST 1 | log/tree post-processors | wrapped by `logcombine`/`tree-annotate` so seed matrices need no manual steps |
| [BEAGLE](https://github.com/beagle-dev/beagle-lib) (Ayres et al. 2012) | BEAST 1 and BEAST 2 | likelihood library | detected, version-checked and exposed to `run` automatically |

No canonical public repository could be verified for the name "SnowBEAST", so it is not listed as a comparable tool; the table above names only tools with verifiable public documentation.

### Why BEAST 1 / BEAST X

BEAST 2 is the mainstream platform for new analyses, but it is a different program: a different XML schema, a package manager, and a different parser and operator ecosystem. A BEAST 1 XML cannot be loaded by BEAST 2, and reimplementing BEAST 2 packages would be a different project. BeastXPy targets the BEAST 1 lineage deliberately:

* **Existing analyses.** Many published divergence-time studies used BEAST 1.10.4 or its maintained continuation BEAST X 10.5.0. Re-running, auditing or extending them requires that lineage, not a translation to BEAST 2.
* **Models that remain BEAST 1 packages.** EBSP, StarBEAST1, random local clocks, empirical codon models and MDS are part of the BEAST 1 corpus; BeastXPy's coverage matrix names the exact subset it supports.
* **Auditable equivalence.** The packaged official jars let Gate 3 test the generated document against the same BEAST build a user will run, without a separate installation.

The novelty claimed here is not "YAML to XML". It is the combination of four validation gates, packaged official jars, honest multi-chain diagnostics and lock-file reproducibility in one auditable pipeline. Target users are groups with existing BEAST 1 / BEAST X analyses, labs that need auditable validation evidence for journal submissions, and method developers reproducing legacy BEAST 1 models. BEAST 2 support is out of scope and is not promised; see [limitations](limitations.md).

Primary literature for every statistic and model named here: [references](references.md).

## Documentation

Read in this order for the full workflow; the reference pages stand alone.

| Page | What it covers |
|---|---|
| [installation](installation.md) | step-by-step setup: Python, JDK, jars, native BEAGLE |
| [quickstart](quickstart.md) | the full workflow with commands and expected output: config -> check-data -> generate -> run -> assess -> report -> lock |
| [configuration](configuration.md) | every YAML key, model names, calibration distributions |
| [cli](cli.md) | all 21 commands, arguments, exit codes and JSON schemas |
| [validation](validation.md) | the four gates and what each one proves |
| [native-beagle](native-beagle.md) | BEAGLE setup, version pairing, how `run` exposes it |
| [pipelines](pipelines.md) | Snakemake and Nextflow output |
| [assessment](assessment.md) | ESS/ACT/PSRF/ASDSF and how to read them |
| [reporting](reporting.md) | the offline HTML report |
| [compare](compare.md) | path sampling, stepping stone and Bayes factors |
| [reproducibility](reproducibility.md) | fingerprints, lock files and replay |
| [extensions](extensions.md) | custom XML fragments |
| [container](container.md) | Docker and Apptainer |
| [troubleshooting](troubleshooting.md) | symptom, cause, fix |
| [api](api.md) | the Python facade |
| [development](development.md) | tests, layout, contribution rules |
| [release](release.md) | cutting a release: version bump, PyPI, Zenodo |
| [limitations](limitations.md) | current capability boundaries and roadmap |
| [references](references.md) | primary literature for every statistic, threshold and model |
| [faq](faq.md) | short answers |

The command line is the primary interface. Every claim about what works is backed by the capability matrix, which you can print yourself:

```
beastxpy capabilities
```
