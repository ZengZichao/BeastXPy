# Validation

[English](validation.md) | [中文](../zh/validation.md)

BeastXPy runs four gates. Each answers a different question, and the difference matters when you read a result.

| Gate | Question | Engine |
|---|---|---|
| 0 | Is the YAML a valid, self-consistent analysis? | pure Python |
| 1 | Is the generated XML structurally valid BEAST 1? | pure Python |
| 2 | Are the calibrations usable and non-contradictory? | pure Python |
| 3 | Does the official BEAST jar accept and instantiate it? | Java |

(BEAST: Drummond & Rambaut 2007, Drummond et al. 2012 - see [references](references.md).)

## Step 1 — Read the gate lines

`beastxpy generate --config demo.yaml --output demo.xml --beast-validate` prints one line per gate on stderr. This is the real output for the [quickstart](quickstart.md) demo config:

```
Gate 0 (parse-time, before the 3 generation gates): demo.yaml
Gate 0: OK
Gate 1/3: OK (0 warning(s), 0 error(s))
Gate 2/3: OK (4 finding(s))
  Gate 2 [info] LOGNORMAL_LOG_SPACE_MEAN calibration root: calibration 'root' does not set meanInRealSpace, so BEAST interprets mean=3.4 and stdev=0.5 in log space (BEAST's default; BEAUti defaults to a real-space mean). With offset 0 the actual prior median is 29.96 and the 95% interval is [11.25, 79.84]. Set parameters.meanInRealSpace: true if you intended mean to be the real-space mean (e.g. a date in Ma).
  Gate 2 [info] AUTO_PRIOR clock.rate: automatic prior applied: clock.rate ~ oneOnXPrior (BeastXPy convention); override under parameter_priors
  Gate 2 [info] AUTO_PRIOR frequencies: automatic prior applied: frequencies ~ uniformPrior(lower=0, upper=1) (BeastXPy convention); override under parameter_priors
  Gate 2 [info] AUTO_PRIOR yule.birthRate: automatic prior applied: yule.birthRate ~ logNormalPrior(mean=2, stdev=1.5, offset=0.0, meanInRealSpace=false) (BEAUti-1.10.4 convention); override under parameter_priors
Gate 3/3: BEAST 1.10.4 validation: OK [mode=java-core (construct-only shim; likelihood NOT evaluated by the official core); tree_likelihood_rule_source=shim]
  WARNING: this was NOT a full official validation: the likelihood was not evaluated (mode=java-core (construct-only shim; likelihood NOT evaluated by the official core); tree_likelihood_rule_source=shim). Install native BEAGLE and re-run for the stronger `beagle` mode.
```

How to read it:

* `Gate 2 [info]` lines are informational, not faults: they restate what the log-space calibration mean actually means and name every automatic prior BeastXPy added (overridable under `parameter_priors`).
* The bracketed field on the Gate 3 line names the mode actually used. On a machine with native BEAGLE it reads `[mode=beagle ...]`, the WARNING line disappears, and the evidence is full official validation.

Exit code 0 means **every requested gate actually ran and passed**. The distinct exit codes: 1 = Gate 0-2 failure or bad config; 2 = Gate 3 could not run (no Java/jar); 3 = Gate 3 ran and BEAST rejected the document.

Gate 1 matches element names **case-sensitively**, because BEAST's parser registry is case-sensitive. A document containing `<TreeModel>` is rejected with `ELEMENT_CASE_MISMATCH` (the finding names the canonical `<treeModel>`), rather than silently accepted.

## Step 2 — Validate an existing XML

```
beastxpy validate --xml demo.xml --beast --beast-version 1.10.4
```

Here the labels differ slightly: `Gate 1/2` is the pure-Python structural validation, `Gate 2/2` the BEAST validation. Exit codes: 0 ok; 1 structural errors; 2 BEAST validation could not run; 3 BEAST rejected it.

## Step 3 — Reproduce the repository matrices

```
python tools/official_validate.py --matrix "examples/config_*.yaml" --mode java-core
python tools/validate_coverage.py
```

The three steps above are the everyday surface. The rest of this page explains what the Gate 3 evidence does and does not prove.

## What Gate 3 actually runs

`--beast-validate` invokes the BEAST jar with an XML parse, MCMC object construction and a finite initial posterior evaluation. A `passed` status means the official parser accepted the document and the model graph could be built.

The low-level result has four states, never two: `executed-pass`, `executed-fail` (BEAST ran and rejected it), `not-run` (validation never started: timeout, missing XML, missing validator class) and `environment-missing` (no usable Java or jar). Every state carries a reason. The public CLI/API facade collapses `environment-missing` into `not-run` and keeps the distinction in `raw_status` and `environment_error` in the JSON, so the four-state detail is never lost. A gate that did not run is never rendered as `OK`.

## Validation modes

Gate 3 runs in the strongest mode the machine supports:

| Mode | What it does | Parser used for the likelihood |
|---|---|---|
| `parse` | XML parse only | official set + project `treeLikelihood` shim (see below) |
| `instantiate` | parse + build the MCMC object graph | official |
| `java-core` | parse + instantiate + initial posterior | official set + project `treeLikelihood` shim (see below) |
| `beagle` | parse + instantiate + real native likelihood | official |

**The `parse` and `java-core` modes substitute a BeastXPy-supplied parser for the `<treeLikelihood>` subtree only.** BEAST's bundled pure-Java BEAGLE never implements the site log-likelihood entry point, so the legacy Java likelihood core is used instead. That parser is a validation harness component, not BEAST functionality and not a supported analysis path. Its syntax rules are a line-for-line mirror of `dr.evomodelxml.treelikelihood.BeagleTreeLikelihoodParser.rules` from both bundled BEAST versions, and a reflection test (`tests/test_beast_validation.py::test_shim_syntax_rules_equal_official_parser`) fails if they diverge. Gate 3 also records `tree_likelihood_rule_source=shim` or `official` in the environment stage and in the JSON result. A `<branchModel>` child is accepted by the mirrored rules but the shim cannot evaluate it; in that case it emits a `SHIM_BRANCH_MODEL` warning and uses only `<siteModel>`. Only `beagle` mode runs the stock official parser set, which is why native BEAGLE validation is the stronger evidence. The selection is automatic per BEAST version: `beagle` when a native hmsbeagle library is discoverable (`BEASTX_BEAGLE_<version>_LIB`, `BEASTX_BEAGLE_LIB`, `BEAGLE_HOME`, or `lib/beagle` next to the jar), otherwise `java-core`. A library that exists but fails to load is reported and the weaker core is used — an unusable BEAGLE is an environment fault, not a defect in the generated XML.

### Evidence strength (what a claim means)

| Mode | Parser set | Likelihood | Claim the evidence supports |
|---|---|---|---|
| `parse` | official parsers; project shim for the `treeLikelihood` subtree | none | the XML is well formed, its elements/ids are accepted at parse time, and the `treeLikelihood` subtree satisfies the shim's mirrored rule surface |
| `java-core` | official parsers + project `TreeLikelihood` shim | none (construct-only) | the model graph can be built and an initial posterior object exists; the likelihood is not evaluated by the official core |
| `beagle` | full official parser set | real native hmsbeagle likelihood | full official validation under a real likelihood |

Every "supported" row in `beastxpy capabilities`, every reason string and every README statement refers to `java-core` unless it explicitly says `beagle`. When publication or a release note claims "validated against BEAST X.Y", state the mode and report the matrix actually run: platform × BEAST version × number of example configurations. The command below runs that matrix; it is not a claim until the output for your platform is recorded.

```
python tools/official_validate.py --matrix "examples/config_*.yaml" \
    --mode beagle --versions 1.10.4 10.5.0
```

`beastxpy capabilities` prints the source-jar SHA-256 recorded in `data/xml_spec.json` for each BEAST version, so a stale element whitelist is visible next to the installed BEAST version.

### Recorded evidence

The following matrix was run from this checkout on 2026-10-22 (Linux x86_64, Temurin JDK 17.0.20.1, bundled official jars; native BEAGLE built from the pinned source archives for each BEAST version). Each row is the complete `examples/config_*.yaml` set:

| BEAST version | Mode | Native BEAGLE | Examples passed |
|---|---|---|---|
| 1.10.4 | `beagle` | 3.1.2 (built from source, SHA-256 `dd872b48...`) | 16/16 |
| 10.5.0 | `beagle` | 4.0.1 (built from source, SHA-256 `9d258cd9...`) | 16/16 |

Both rows are full-parser, real-likelihood validation. The same checkout also passed every version-applicable coverage fixture in `examples/coverage/` (12/12 for BEAST 1.10.4 and 15/15 for BEAST X 10.5.0; three fixtures are gated to 10.5.0). The `beagle` rows are replayable: `tools/run_beagle_evidence.py` downloads the pinned BEAGLE archive, verifies its SHA-256, builds it (BEAGLE 4.0.1 with CMake, BEAGLE 3.1.2 with autotools), runs the matrix and writes `evidence.json` with commands, hashes and pass counts. The CI job `beagle-evidence` runs that script once per BEAST version on every push and uploads both artifacts. Building automatically requires a JDK; on a non-ASCII checkout path, build BEAGLE 3.1.2 from an ASCII directory (its autotools `javac` probe runs under `LC_ALL=C`).

A `java-core`-only run remains the construct-only fallback when native BEAGLE is unavailable; as the *Evidence strength* table shows, it is weaker evidence than a `beagle` row.

### BEAUti-convention reference comparison

Golden files lock this generator's behaviour; they are not external truth. `tests/golden/beauti_reference/` adds hand-transcribed reference subsets for three representative analyses (JC69/strict/Yule, HKY+Gamma+UCLN with a calibration, and two GTR partitions). Their values come from the BEAUti 1.10.4 option classes that write the XML templates, corroborated by the BEAUti-generated `examples/Benchmarks/benchmark1.xml` in the BEAST source tree. `tests/test_beauti_reference.py` compares elements and attributes and asserts every deliberate difference explicitly: clock-rate prior family, gamma-shape prior family, GTR rate prior family, base-frequency prior family, and the calibration `meanInRealSpace` default. The references are not live BEAUti output (BEAUti is a GUI with no headless export in this environment), so this is structural equivalence evidence, not a posterior comparison.

### Reference-data case study (workflow demonstration)

`examples/empirical/` runs the complete workflow on the shipped `examples/primates.fasta` reference/tutorial alignment (12 taxa, 898 bp). This is not a new empirical study and not a biological conclusion; it shows that the pipeline can produce a converged, reproducible result on reference data. The config has two partitions (HKY+G each), a strict clock, a Yule tree prior and two fossil calibrations. Four seeds, 2,000,000 steps, logging every 1,000:

```
export JAVA_HOME=/path/to/jdk-17
export BEASTX_BEAGLE_10_5_0_LIB=/path/to/beagle-4.0.1/lib
python tools/run_reference_case.py \
    --workdir validation_runs/primates_reference \
    --results examples/empirical/results \
    --beast-version 10.5.0 --seeds 1,2,3,4 --parallel 4
```

The recorded run (BEAST X 10.5.0, BEAGLE 4.0.1, Temurin JDK 17.0.20.1, Linux x86_64, 2026-10-22):

| Metric | Result |
|---|---|
| `assess` verdict | `pass` (ESS 200, PSRF 1.01 thresholds) |
| chains / samples | 4 chains x 2,001 samples, 10% burn-in |
| minimum ESS per chain | 698.7 / 775.4 / 748.9 / 767.0 |
| max classic split R-hat | 1.00138 (posterior) |
| max rank-normalized split R-hat | 1.00145 (posterior) |
| ASDSF | 0.000115 |
| root height | median 49.83, 95% HPD [42.86, 60.19] |
| Catarrhini tmrca | median 26.10, 95% HPD [25.29, 27.85] |
| Homininae tmrca | median 7.56, 95% HPD [7.17, 8.07] |
| MCMC wall time | 192.8 s (four seeds in parallel) |
| total workflow time | 216.0 s |

The posterior summaries are in tree units as configured (calibration offsets are in Ma); they are a demonstration, not a dating result. The artefacts (config, generated XML, lock, assessment JSON, HTML/JSON report and the summary with commands/timings) are in `examples/empirical/results/`. A posterior comparison against a live BEAUti run is not claimed; the structural comparison is the BEAUti section above.

### Simulated end-to-end recovery

`tools/simulate_end_to_end.py` is the scientific companion to the parser matrix. It simulates a four-taxon JC69 alignment on a known rooted tree ((A:0.03,B:0.05):0.05,C:0.05,D:0.05; A sampled at age 0.02, so the root height is 0.10) with a strict clock fixed at rate 1.0, runs two independent seeds, and checks whether the 95% HPD of the root height contains 0.10.

```
python tools/simulate_end_to_end.py --out validation_runs/simulated_e2e \
    --beagle-lib /path/to/libhmsbeagle
```

The run recorded from this checkout (5000 sites, 500,000 steps, BEAST X 10.5.0 with native BEAGLE 4.0.1) gave:

| seed | root-height 95% HPD | median | contains 0.10 |
|---|---|---|---|
| 1 | [0.0903, 0.1020] | 0.0961 | yes |
| 2 | [0.0904, 0.1013] | 0.0964 | yes |

The assess verdict for that deliberately short run was `fail` (some ESS values were below 200); the script's pass criterion is only HPD coverage of the known truth, not convergence.

### Parameter-recovery study (`--study recovery`)

The same script also runs an eight-taxon parameter-recovery study on data simulated under an uncorrelated lognormal clock. The tree is fixed with six true clades; branch rates are lognormal with the stated log standard deviation and are normalised to a time-weighted mean of 1. The root is calibrated with a normal(1.05, 0.15) prior while the true root age is 1.0. Each cell uses four seeds, 2,000 sites and 1,000,000 MCMC steps:

```
python tools/simulate_end_to_end.py --study recovery \
    --out validation_runs/recovery --seeds 1,2,3,4 \
    --stdev-truth 0.3 --stdev-truth 0.15 \
    --beagle-lib /path/to/libhmsbeagle
```

The run recorded from this checkout (BEAST X 10.5.0, native BEAGLE 4.0.1, four seeds per row) gave:

| true ucld.stdev | calibration age HPD covers 1.0 | ucld.stdev HPD covers truth | ucld.stdev posterior mean | median relative error | minimum ESS | true-clade support (minimum) | ASDSF |
|---|---|---|---|---|---|---|---|
| 0.30 | 4/4 | 3/4 | 0.523 | 0.452 | 19.7 | 0.966 | 0.000 |
| 0.15 | 4/4 | 4/4 | 0.128 | 0.260 | 105.1 | 1.000 | 0.000 |

These are complete, per-seed results from `recovery_summary.json`, not a selected subset. Calibration coverage is 8/8 across both cells and the true topology is recovered (all six clades have posterior support >= 0.966), but ESS is below the 200 screening threshold for every `ucld.stdev` estimate at true 0.30 and for all four at true 0.15; the `assess` verdict is therefore `fail`. The 0.30 cell also contains one seed whose ucld.stdev posterior is far above the truth, which is why its coverage is 3/4 and its mean is inflated. Longer chains (or a more informative design) are required for a publication-grade recovery table; the script reports ESS rather than hiding this limitation.

## `--allow-unvalidated`

It exists for debugging. Output produced under it is labelled `UNVALIDATED` and is never printed as `OK`. It does not apply to custom fragments: a spliced document must pass Gate 3 or generation is refused.
