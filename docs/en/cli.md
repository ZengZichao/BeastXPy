# Command-line reference

[English](cli.md) | [中文](../zh/cli.md)

Every command is registered by `beastxpy.main`. Synopses and argument tables are generated from the argument parser, so they match `--help`.

Global options:

```
beastxpy --help
beastxpy --version
```

Default exit codes: 0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

On this page: [assess](#beastxpy-assess) · [capabilities](#beastxpy-capabilities) · [check-data](#beastxpy-check-data) · [check-environment](#beastxpy-check-environment) · [compare](#beastxpy-compare) · [container](#beastxpy-container) · [diagnose](#beastxpy-diagnose) · [fingerprint](#beastxpy-fingerprint) · [generate](#beastxpy-generate) · [list-models](#beastxpy-list-models) · [lock](#beastxpy-lock) · [logcombine](#beastxpy-logcombine) · [methods](#beastxpy-methods) · [pipeline](#beastxpy-pipeline) · [quick](#beastxpy-quick) · [report](#beastxpy-report) · [reproduce](#beastxpy-reproduce) · [run](#beastxpy-run) · [trace](#beastxpy-trace) · [tree-annotate](#beastxpy-tree-annotate) · [validate](#beastxpy-validate) · [JSON schemas](#json-schemas)

## Commands

### `beastxpy assess`

Diagnose MCMC output: ESS/ACT/HPD, PSRF and ASDSF, operators, tree summaries. Diagnostics only.

**Synopsis**

```
beastxpy assess [WORKDIR] [--trace PATH ...] [--trees PATH ...] [--burnin FRACTION|auto] [--ess MIN] [--psrf MAX] [--html PATH] [--strict] [--json PATH] [-q]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `WORKDIR` | `--` | required unless `--trace` is used |
| `--trace` | `PATH` | no (repeat for multiple chains) |
| `--trees` | `PATH` | no (one per `--trace`, same order) |
| `--burnin` | `FRACTION` or `auto` | no |
| `--ess` | `MIN` | no |
| `--psrf` | `MAX` | no |
| `--html` | `PATH` | no |
| `--strict` | `flag` | no |
| `--json` | `PATH` | no |
| `-q, --quiet` | `flag` | no |

**Example**

Run `beastxpy run --seeds 1,2 --workdir runs/demo` first; two seeds are needed for PSRF.

```
beastxpy assess runs/demo --json runs/demo/assessment.json
```

Bare BEAST logs from outside a BeastXPy workdir can be assessed directly; each `--trace` is one chain (labelled 1..N), and `--trees` supplies the matching tree logs for ASDSF:

```
beastxpy assess --trace seed_1.log --trace seed_2.log \
    --trees seed_1.trees --trees seed_2.trees --burnin auto
```

**Output**

A summary on stdout; --json and --html write files. Diagnostics only.

**Exit codes**

0 pass or warn, 1 warn under --strict, 2 unreadable inputs, 3 fail verdict.

### `beastxpy capabilities`

Print the feature x BEAST-version support matrix.

**Synopsis**

```
beastxpy capabilities [--beast-version {1.10.4,10.5.0,both}] [--json PATH] [-q]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--beast-version` | `{1.10.4,10.5.0,both}` | no |
| `--json` | `PATH` | no |
| `-q, --quiet` | `flag` | no |

**Example**

```
beastxpy capabilities
```

**Output**

An aligned table on stdout; --json writes the envelope.

**Exit codes**

0 ok, 2 the manifest is missing or unparsable.

### `beastxpy check-data`

Pre-flight the data and calibrations a config points at.

**Synopsis**

```
beastxpy check-data [--config PATH] [--ambiguity-threshold FRACTION] [--json PATH] [-q]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--config` | `PATH` | yes |
| `--ambiguity-threshold` | `FRACTION` | no |
| `--json` | `PATH` | no |
| `-q, --quiet` | `flag` | no |

**Example**

```
beastxpy check-data --config examples/config_basic.yaml
```

**Output**

Findings with location and fix on stdout; --json writes the envelope.

**Exit codes**

0 pass, 1 warnings only, 2 errors found. Errors are never downgraded.

### `beastxpy check-environment`

Probe Python, Java and the BEAST jars.

**Synopsis**

```
beastxpy check-environment
```

**Arguments**

| Argument | Value | Required |
|---|---|---|

**Example**

```
beastxpy check-environment
```

**Output**

Resolved Java, jars and Python on stdout.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy compare`

Compare models by marginal likelihood with real PS/SS runs; fails closed without BEAGLE.

`--path-steps` defaults to 8 — a quick estimate that prints a one-time warning; use at least 50 for a publication-grade result. The per-model `--timeout` defaults to 86400 seconds (24 h).

**Synopsis**

```
beastxpy compare [--configs PATH] [--method {stepping-stone,path-sampling}] [--workdir DIR] [--seeds 1,2] [--parallel N] [--chain-length N] [--path-steps N] [--path-scheme {linear,oneSidedBeta,beta,betaQuantile,sigmoid}] [--log-every N] [--beast-version {1.10.4,10.5.0}] [--beagle-dir DIR] [--beagle-threads N] [--timeout SECONDS] [--csv PATH] [--html PATH] [--json PATH] [-q]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--configs` | `PATH` | yes |
| `--method` | `{stepping-stone,path-sampling}` | yes |
| `--workdir` | `DIR` | yes |
| `--seeds` | `1,2` | no |
| `--parallel` | `N` | no (seeds of each model run concurrently) |
| `--chain-length` | `N` | no |
| `--path-steps` | `N` | no |
| `--path-scheme` | `{linear,oneSidedBeta,beta,betaQuantile,sigmoid}` | no |
| `--log-every` | `N` | no |
| `--beast-version` | `{1.10.4,10.5.0}` | no |
| `--beagle-dir` | `DIR` | no |
| `--beagle-threads` | `N` | no |
| `--timeout` | `SECONDS` | no |
| `--csv` | `PATH` | no |
| `--html` | `PATH` | no |
| `--json` | `PATH` | no |
| `-q, --quiet` | `flag` | no |

**Example**

Needs native BEAGLE; the run itself takes minutes.

```
beastxpy compare --configs examples/config_subst_jc69.yaml examples/config_subst_gtr.yaml --method stepping-stone --workdir compare_runs
```

**Output**

A table on stdout; --json/--csv/--html write files.

**Exit codes**

0 compared, 1 bad arguments, 2 environment unavailable, 3 not comparable or BEAST failed.

### `beastxpy container`

Print the bundled Docker or Apptainer definition byte for byte.

**Synopsis**

```
beastxpy container [--print {apptainer,docker}]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--print` | `{apptainer,docker}` | yes |

**Example**

```
beastxpy container --print docker
```

**Output**

The definition file bytes on stdout; nothing is built.

**Exit codes**

0 printed, 1 missing --print, 2 definition not available.

### `beastxpy diagnose`

Calibration diagnostics (Gate 2 detail), optionally with a sensitivity sweep.

**Synopsis**

```
beastxpy diagnose [--config CONFIG] [--output-dir OUTPUT_DIR] [--report REPORT] [--sensitivity] [--no-visualize]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--config` | `CONFIG` | yes |
| `--output-dir` | `OUTPUT_DIR` | no |
| `--report` | `REPORT` | no |
| `--sensitivity` | `flag` | no |
| `--no-visualize` | `flag` | no |

**Example**

```
beastxpy diagnose --config examples/config_advanced.yaml --output-dir diagnostics
```

**Output**

Calibration findings on stdout, plus optional plot files.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy fingerprint`

Print the analysis fingerprint, content fingerprint and data digest.

**Synopsis**

```
beastxpy fingerprint [--config CONFIG] [--output OUTPUT]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--config` | `CONFIG` | yes |
| `--output` | `OUTPUT` | no |

**Example**

```
beastxpy fingerprint --config examples/config_basic.yaml
```

**Output**

Fingerprints on stdout; optional JSON file.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy generate`

Generate a BEAST 1 XML from a YAML config; Gate 3 runs with `--beast-validate`.

**Synopsis**

```
beastxpy generate [--config CONFIG] [--output OUTPUT] [--beast-validate] [--allow-unvalidated] [--no-data-check] [--allow-custom-fragments] [--beast-version {1.10.4,10.5.0,both}] [--force]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--config` | `CONFIG` | yes |
| `--output` | `OUTPUT` | no |
| `--beast-validate` | `flag` | no |
| `--allow-unvalidated` | `flag` | no |
| `--no-data-check` | `flag` | no |
| `--allow-custom-fragments` | `flag` | no |
| `--beast-version` | `{1.10.4,10.5.0,both}` | no |
| `--force` | `flag` | no |

**Example**

```
beastxpy generate --config examples/config_basic.yaml --output out.xml --beast-validate
```

**Output**

The XML goes to stdout unless --output is given; gate progress goes to stderr.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy list-models`

List registered models, clocks, tree priors and distributions.

**Synopsis**

```
beastxpy list-models
```

**Arguments**

| Argument | Value | Required |
|---|---|---|

**Example**

```
beastxpy list-models
```

**Output**

A table on stdout.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy lock`

Write an environment lock: digests, config hash and seeds.

**Synopsis**

```
beastxpy lock [--config PATH] [--output PATH] [--beast-version {1.10.4,10.5.0}] [--seeds 1,2,3] [--beagle-lib DIR] [--beagle-threads N] [--json PATH] [-q]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--config` | `PATH` | yes |
| `--output` | `PATH` | yes |
| `--beast-version` | `{1.10.4,10.5.0}` | no |
| `--seeds` | `1,2,3` | no |
| `--beagle-lib` | `DIR` | no |
| `--beagle-threads` | `N` | no |
| `--json` | `PATH` | no |
| `-q, --quiet` | `flag` | no |

**Example**

```
beastxpy lock --config examples/config_basic.yaml --output beastxpy.lock
```

**Output**

A sealed JSON lock at --output; no paths or credentials.

**Exit codes**

0 lock written, 1 bad arguments or unreadable config, 2 write failure.

### `beastxpy logcombine`

Combine trace or tree logs from several seeds. Note: `--burnin` counts **log rows** on the pure-Python path but **states** on the Java LogCombiner path; the Java path is used whenever the selected jar is available.

**Synopsis**

```
beastxpy logcombine [--logs LOGS] [--output OUTPUT] [--burnin BURNIN] [--resample RESAMPLE] [--strip] [--beast-version {1.10.4,10.5.0}]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--logs` | `LOGS` | yes |
| `--output` | `OUTPUT` | yes |
| `--burnin` | `BURNIN` | no |
| `--resample` | `RESAMPLE` | no |
| `--strip` | `flag` | no |
| `--beast-version` | `{1.10.4,10.5.0}` | no |

**Example**

Run `beastxpy run --seeds 1,2 --workdir runs/demo` first.

```
beastxpy logcombine --logs runs/demo/1/seed_1_trace.log runs/demo/2/seed_2_trace.log --output combined.log
```

**Output**

A combined log file at --output.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy methods`

Generate a LaTeX methods paragraph.

**Synopsis**

```
beastxpy methods [--config CONFIG] [--output OUTPUT]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--config` | `CONFIG` | yes |
| `--output` | `OUTPUT` | no |

**Example**

```
beastxpy methods --config examples/config_basic.yaml
```

**Output**

A LaTeX paragraph on stdout.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy pipeline`

Write a Snakemake or Nextflow pipeline.

**Synopsis**

```
beastxpy pipeline [--config CONFIG] [--type {snakemake,nextflow}] [--output-dir OUTPUT_DIR]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--config` | `CONFIG` | yes |
| `--type` | `{snakemake,nextflow}` | no |
| `--output-dir` | `OUTPUT_DIR` | no |

**Example**

```
beastxpy pipeline --config examples/config_basic.yaml --type snakemake --output Snakefile
```

**Output**

A pipeline file at --output.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy quick`

Single-partition generation without writing a YAML file first. The chain length defaults to 1,000,000, not a long production default.

**Synopsis**

```
beastxpy quick [--alignment ALIGNMENT] [--tree-prior TREE_PRIOR] [--subst-model SUBST_MODEL] [--clock-model CLOCK_MODEL] [--calibration-yaml CALIBRATION_YAML] [--chain-length CHAIN_LENGTH] [--name NAME] [--output OUTPUT] [--beast-version {1.10.4,10.5.0}] [--beast-validate] [--allow-unvalidated] [--force]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--alignment` | `ALIGNMENT` | yes |
| `--tree-prior` | `TREE_PRIOR` | no |
| `--subst-model` | `SUBST_MODEL` | no |
| `--clock-model` | `CLOCK_MODEL` | no |
| `--calibration-yaml` | `CALIBRATION_YAML` | no |
| `--chain-length` | `CHAIN_LENGTH` | no |
| `--name` | `NAME` | no |
| `--output` | `OUTPUT` | no |
| `--beast-version` | `{1.10.4,10.5.0}` | no |
| `--beast-validate` | `flag` | no |
| `--allow-unvalidated` | `flag` | no |
| `--force` | `flag` | no |

**Example**

```
beastxpy quick --alignment examples/primates.fasta --subst-model hky --output quick.xml
```

**Output**

Same as generate, for a config built from flags.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy report`

Render a single offline HTML report for a run directory.

**Synopsis**

```
beastxpy report WORKDIR [--output PATH] [--lang {en,zh}] [--include-config] [--assessment PATH] [--ess-threshold N] [--no-plot] [--json PATH] [-q]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `WORKDIR` | `--` | yes |
| `--output` | `PATH` | yes |
| `--lang` | `{en,zh}` | no |
| `--include-config` | `flag` | no |
| `--assessment` | `PATH` | no |
| `--ess-threshold` | `N` | no |
| `--no-plot` | `flag` | no |
| `--json` | `PATH` | no |
| `-q, --quiet` | `flag` | no |

**Example**

Run `beastxpy run --workdir runs/demo` first.

```
beastxpy report runs/demo --output runs/demo/report.html --lang en
```

**Output**

One self-contained HTML file; --json writes the same data.

**Exit codes**

0 written, 1 bad arguments or write failure, 2 work directory unusable.

### `beastxpy reproduce`

Verify a lock field by field (`--check`) or replay it (`--run`). For `--run`, `--timeout` defaults to 86400 seconds (24 h) per seed.

**Synopsis**

```
beastxpy reproduce [--lock PATH] [--check] [--run] [--config PATH] [--workdir DIR] [--beagle-lib DIR] [--beagle-threads N] [--timeout SECONDS] [--json PATH] [-q]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--lock` | `PATH` | yes |
| `--check` | `flag` | no |
| `--run` | `flag` | no |
| `--config` | `PATH` | no |
| `--workdir` | `DIR` | no |
| `--beagle-lib` | `DIR` | no |
| `--beagle-threads` | `N` | no |
| `--timeout` | `SECONDS` | no |
| `--json` | `PATH` | no |
| `-q, --quiet` | `flag` | no |

**Example**

Run `beastxpy lock --config examples/config_basic.yaml --output beastxpy.lock` first.

```
beastxpy reproduce --lock beastxpy.lock --check --config examples/config_basic.yaml
```

**Output**

A field-by-field comparison on stdout; --run re-executes BEAST.

**Exit codes**

0 match or ran, 1 bad arguments, 2 mismatch, 3 corrupt or tampered lock.

### `beastxpy run`

Run BEAST over a seed matrix; needs native BEAGLE. `--timeout` defaults to 86400 seconds (24 h) per seed; raise it or run in the background for long divergence-time analyses. A discovered BEAGLE directory is exposed to the BEAST process automatically (`-Djava.library.path` plus the loader path). `--java-only` maps to BEAST's `-java` flag; because BEAST's bundled pure-Java BEAGLE never implemented the site-likelihood entry point, it is refused up front (exit 1) for any document containing `<treeLikelihood>` - which is every document BeastXPy generates.

**Synopsis**

```
beastxpy run [--config CONFIG] [--workdir WORKDIR] [--seeds SEEDS] [--parallel N] [--beast-version {1.10.4,10.5.0}] [--timeout TIMEOUT] [--resume] [--rerun-successful] [--checkpoint-every CHECKPOINT_EVERY] [--java-only] [--no-data-check] [--force]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--config` | `CONFIG` | yes |
| `--workdir` | `WORKDIR` | no |
| `--seeds` | `SEEDS` | no |
| `--parallel` | `N` | no (default 1; seeds run concurrently) |
| `--beast-version` | `{1.10.4,10.5.0}` | no |
| `--timeout` | `TIMEOUT` | no |
| `--resume` | `flag` | no |
| `--rerun-successful` | `flag` | no |
| `--checkpoint-every` | `CHECKPOINT_EVERY` | no |
| `--java-only` | `flag` | no |
| `--no-data-check` | `flag` | no |
| `--force` | `flag` | no |

**Example**

```
beastxpy run --config examples/config_basic.yaml --workdir runs/demo --seeds 1,2
```

**Output**

One sub-directory per seed under the workdir.

**Exit codes**

0 all seeds ok; 1 pre-flight failure (including the `--java-only` refusal) or any seed failed.

### `beastxpy trace`

Trace-file statistics in pure Python: ESS, ACT, HPD.

**Synopsis**

```
beastxpy trace [--log LOG] [--burnin BURNIN] [--ess-threshold ESS_THRESHOLD] [--plot PLOT] [--json]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--log` | `LOG` | yes |
| `--burnin` | `BURNIN` | no |
| `--ess-threshold` | `ESS_THRESHOLD` | no |
| `--plot` | `PLOT` | no |
| `--json` | `flag` | no |

**Example**

Run `beastxpy run --config examples/config_basic.yaml --workdir runs/demo` first.

```
beastxpy trace --log runs/demo/1/seed_1_trace.log
```

**Output**

A statistics table on stdout; optional plot and JSON.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy tree-annotate`

Annotate a tree log with clade support. Note: `--heights ca` (common-ancestor heights) requires the Java TreeAnnotator; it is refused under `--python-only` and when no usable jar is found.

**Synopsis**

```
beastxpy tree-annotate [--trees TREES] [--output OUTPUT] [--heights {keep,median,mean,ca}] [--burnin BURNIN] [--limit LIMIT] [--python-only] [--beast-version {1.10.4,10.5.0}]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--trees` | `TREES` | yes |
| `--output` | `OUTPUT` | yes |
| `--heights` | `{keep,median,mean,ca}` | no |
| `--burnin` | `BURNIN` | no |
| `--limit` | `LIMIT` | no |
| `--python-only` | `flag` | no |
| `--beast-version` | `{1.10.4,10.5.0}` | no |

**Example**

Run `beastxpy run --workdir runs/demo` first.

```
beastxpy tree-annotate --trees runs/demo/1/seed_1_tree.log --output tree.tre
```

**Output**

An annotated tree file at --output.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

### `beastxpy validate`

Validate an existing XML file; `--beast` adds Gate 3.

**Synopsis**

```
beastxpy validate [--xml XML] [--beast] [--beast-version {1.10.4,10.5.0,both}] [--allow-unvalidated]
```

**Arguments**

| Argument | Value | Required |
|---|---|---|
| `--xml` | `XML` | yes |
| `--beast` | `flag` | no |
| `--beast-version` | `{1.10.4,10.5.0,both}` | no |
| `--allow-unvalidated` | `flag` | no |

**Example**

Run `beastxpy generate ... --output out.xml` first.

```
beastxpy validate --xml out.xml --beast
```

**Output**

Findings on stdout; the XML is not modified.

**Exit codes**

0 success, 1 configuration or usage error, 2 environment unavailable, 3 validation or verdict failure.

## JSON schemas

| Command | `schema_version` |
|---|---|
| `assess` | `beastxpy.assessment/v1` |
| `check-data` | `beastxpy.datacheck/v1` |
| `report` | `beastxpy.report/v1` |
| `compare` | `beastxpy.compare/v1` |
| `lock` | `beastxpy.lock/v1` |
| `reproduce` | `beastxpy.reproduce/v1` |
| `capabilities` | `beastxpy.capabilities/v1` |

The authoritative mapping is `beastxpy/_contracts.py`.
