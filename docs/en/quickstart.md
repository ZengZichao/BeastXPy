# Quick start

[English](quickstart.md) | [中文](../zh/quickstart.md)

This walkthrough runs one complete analysis on the shipped primates alignment (898 bp, 12 taxa): JC69 + strict clock + Yule + one root calibration. Every step shows the command, the output you should see, and what to check before moving on.

**Before you start:** finish [installation](installation.md). You need `beastxpy --version` to work, and `beastxpy check-environment` to find a JDK and both BEAST jars. Running step 5 additionally needs a native BEAGLE library (see [native-beagle](native-beagle.md)); on this walkthrough's reference machine the two environment variables below point at it:

```
export JAVA_HOME=/path/to/jdk-17
export BEASTX_BEAGLE_1_10_4_LIB=/path/to/beagle-3.1.x/lib
export BEASTX_BEAGLE_10_5_0_LIB=/path/to/beagle-4.0.x/lib
```

Create a working directory and copy the example data into it:

```
mkdir -p ~/beastxpy-demo && cd ~/beastxpy-demo
cp <repo>/examples/primates.fasta .
```

## Step 1 — Write the analysis configuration

Create a file named `demo.yaml`:

```yaml
metadata:
  analysis_name: demo
alignments:
  - id: a1
    file: primates.fasta
    data_type: nucleotide
partitions:
  - id: p1
    alignment_id: a1
    from: 1
    to: 898
    site_model:
      substitution_model: {type: jc69}
    clock_model: {type: strict}
tree_prior: {type: yule}
calibrations:
  - name: root
    taxa: null
    distribution: {type: lognormal, parameters: {mean: 3.4, stdev: 0.5}}
mcmc: {chain_length: 100000, pre_burnin: 0}
loggers:
  trace_log: {file_name: demo.log, log_every: 500}
  tree_log: {file_name: demo.trees, log_every: 500}
output: {file: demo.xml}
control: {beast_version: "1.10.4", seed: 1}
```

Field by field:

| Block | Meaning |
|---|---|
| `metadata.analysis_name` | becomes the file stem of every artefact; must match `[A-Za-z0-9][A-Za-z0-9._-]*` |
| `alignments` | input FASTA; path is resolved **relative to this YAML file**; `data_type: nucleotide` pins the alphabet |
| `partitions` | which alignment range gets which models; `from`/`to` are 1-based and inclusive |
| `tree_prior` | the tree model; `yule` has one parameter (`birth_rate`) |
| `calibrations` | `taxa: null` calibrates the root. `mean` is on the **log scale** here (add `meanInRealSpace: true` to give a real-space mean) and must be positive |
| `mcmc.chain_length` | MCMC steps. 100000 is a smoke-test value; real analyses need millions |
| `loggers` | output log names; `log_every: 500` gives `chain_length / 500 = 200` samples |
| `control` | target BEAST version and the default RNG seed |

For a real project you would normally start from a copy of an example config (`examples/config_*.yaml`) and edit it.

## Step 2 — Check the data (cheap, catches most mistakes)

```
beastxpy check-data --config demo.yaml
```

Expected output:

```
check-data: demo.yaml
verdict: pass (0 error(s), 0 warning(s))
no data or calibration problems found
```

Exit code `0` means clean, `1` warnings only, `2` errors. If you made the classic lognormal mistake (`mean: -3.5`, a negative log-scale mean), Gate 0 rejects the config before any check runs:

```
verdict: fail (1 error(s), 0 warning(s))
[error] config-parse-error: Gate 0 rejected this config: calibrations[0].distribution.parameters.mean: must be > 0 for lognormal
```

Fix what the finding names, then re-run until the verdict is `pass`.

## Step 3 — Generate the BEAST XML and validate it

```
beastxpy generate --config demo.yaml --output demo.xml --beast-validate
```

Expected output (progress goes to stderr, the "wrote" line to stdout):

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
wrote demo.xml
```

What each line proves:

* **Gate 0** - the YAML parsed strictly and is self-consistent.
* **Gate 1** - the generated XML is structurally valid BEAST 1.
* **Gate 2** - the calibrations are usable and non-contradictory. The `Gate 2 [info]` lines are informational, not faults: they restate what the log-space calibration mean actually means and name every automatic prior BeastXPy added (overridable under `parameter_priors`).
* **Gate 3** - the *official* BEAST 1.10.4 jar parsed the document, built the model graph and evaluated a finite initial posterior. The bracketed field names the strongest mode the machine supports: `beagle` with native BEAGLE (and no WARNING line), otherwise `java-core` (see [validation](validation.md)).

Exit code 0 means **every requested gate actually ran and passed**. The command also writes two sidecars next to the XML: `demo.fingerprint.json` and `demo_methods.tex`.

## Step 4 — Run BEAST (two seeds)

```
beastxpy run --config demo.yaml --workdir runs/demo --seeds 1,2
```

Expected output:

```
  seed 1: OK in 3.9s [log: runs/demo/1/run.log]
    trace: runs/demo/1/seed_1_trace.log
    trees: runs/demo/1/seed_1_tree.log
  seed 2: OK in 4.6s [log: runs/demo/2/run.log]
    trace: runs/demo/2/seed_2_trace.log
    trees: runs/demo/2/seed_2_tree.log
fingerprint: BXP-a836c87ba7de-0.1.0 (content: BXP-a836c87ba7de6055)
```

Two seeds are the minimum for the between-chain statistics in step 5; real analyses use four or more. The workdir layout:

```
runs/demo/
├── demo.xml                 master XML
├── assessment.json          (step 5)
├── report.html              (step 6)
├── 1/
│   ├── demo.xml             per-seed copy with logger names rewritten
│   ├── run.log              BEAST stdout+stderr
│   ├── run_meta.json        machine-readable run record
│   ├── seed_1_trace.log     parameter log
│   └── seed_1_tree.log      tree log
└── 2/
    └── ...                  same layout for seed 2
```

**If the run stops almost immediately** with `No acceptable BEAGLE library plugins found` in the per-seed `run.log`, no native BEAGLE was available to the JVM. Check `beastxpy check-environment`, set `BEASTX_BEAGLE_1_10_4_LIB`, and re-run. BeastXPy refuses to produce a chain that only looks real: BEAST 1 XML needs native BEAGLE for the likelihood. (`--java-only` is refused outright for documents containing `<treeLikelihood>` - see [cli](cli.md).)

The BEAST process gets a discovered native BEAGLE exposed automatically (`-Djava.library.path` plus the loader path), so the environment variables from the installation are enough.

## Step 5 — Assess the MCMC output

```
beastxpy assess runs/demo --json runs/demo/assessment.json
```

On this deliberately short chain the verdict is `fail`, and that is the correct answer (ESS of a few dozen on a 100000-step chain):

```
assess: runs/demo -> verdict fail (2 chain(s), strict=False)
thresholds: burnin=0.1 ess=200.0 psrf=1.01 operator=[0.01, 0.95]
  seed 1: status=ok samples=201 burnin=20 parameters=12 operators=10
  seed 2: status=ok samples=201 burnin=20 parameters=12 operators=10
  multichain: 2 usable chain(s)
    PSRF (...): max R-hat 1.0306
    ASDSF: 0.0000
  reasons: 24 error(s), 11 warning(s), 4 info
    [error] ess-below-threshold: seed 1: ESS 81.8 for 'posterior' is below the requested threshold 200
    ...
```

How to read it:

* **Per-parameter ESS/ACT/HPD** - the `ess-below-threshold` errors name the parameters that moved too little. The cure is a longer chain, not a higher threshold.
* **PSRF** - needs at least two usable chains; with one seed it is `not_computable` with a reason, never a fabricated number.
* **ASDSF** - topological agreement between chains; `0.0000` here means both chains agree on every split.
* **Operators** - acceptance rates; moves outside 1-95% are flagged.

Exit codes: `0` pass/warn, `1` warn under `--strict`, `2` unreadable inputs, `3` fail. A `fail` here describes the samples; it is not a statement that the model is wrong (see [assessment](assessment.md) and [limitations](limitations.md)).

## Step 6 — Render the offline HTML report

```
beastxpy report runs/demo --output runs/demo/report.html --lang en
```

Expected output:

```
report: runs/demo/report.html
conclusion: completed
chains: 2
assessment: present (verdict fail)
```

(The full output also echoes `language: en`; with `--lang zh` it echoes `language: zh`.)

One self-contained HTML file: no external CSS/JS/fonts, figures inlined, opens from `file://` with no network. `--lang zh` produces the complete Chinese report. If you re-order steps and call `report` before `assess`, the report says **not assessed** instead of inventing numbers.

## Step 7 — Pin the environment and verify it

```
beastxpy lock --config demo.yaml --output beastxpy.lock --seeds 1,2
```

```
lock: beastxpy.lock
  BEAST 1.10.4 jar 0f6eb4fdf7832c51 (reference-install)
  Java 17.0.20.1 64-bit (JAVA_HOME)
  native BEAGLE 3.1.0 7fcbc7d572bdcde0 threads=1
  dependencies: 6 recorded
  config demo.yaml sha256 8e831900a5682404
  seeds: [1, 2]
  the lock records basenames and digests only; no paths or credentials
```

Then verify the machine still matches the lock:

```
beastxpy reproduce --lock beastxpy.lock --check --config demo.yaml
```

```
reproduce: mode=check verdict=match
  24 field(s) match
```

Exit 0 = the environment is the one the lock describes. Any drift (different jar, different Java, edited lock) exits non-zero; an edited lock is rejected outright with exit 3. See [reproducibility](reproducibility.md).

## Where to go next

* [configuration](configuration.md) - every YAML key, model names, priors.
* [cli](cli.md) - all 21 commands with exact arguments and exit codes.
* [validation](validation.md) - what the four gates do and do not prove.
* [assessment](assessment.md) - reading ESS/PSRF/ASDSF honestly.
* [compare](compare.md) - marginal likelihoods and Bayes factors.
* [references](references.md) - primary literature for every statistic, threshold and model used in this walkthrough.
* [troubleshooting](troubleshooting.md) - symptom, cause, fix.
