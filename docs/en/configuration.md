# Configuration

[English](configuration.md) | [中文](../zh/configuration.md)

A BeastXPy analysis is one YAML file. Parsing is strict (Gate 0): unknown keys, wrong types, quoted booleans, duplicate keys and contradictory settings are **errors**, not warnings. A typo stops you before BEAST ever runs.

## Step 1 — Know the top-level keys

| Key | Purpose |
|---|---|
| `metadata` | `analysis_name`, provenance strings |
| `alignments` | input files and their data type |
| `partitions` | site ranges and per-partition models |
| `tree_prior` | Yule, birth-death, coalescent variants, skyline, EBSP |
| `calibrations` | node calibrations with distributions and provenance |
| `parameter_priors` | priors on named parameters |
| `mcmc` | `chain_length`, `pre_burnin`, `auto_optimize`, operator weights |
| `loggers` | trace and tree log files and cadence |
| `output` | output XML name, fingerprint embedding |
| `control` | `beast_version`, `seed`, `strict`, `overlap_measure` |
| `tip_dates` | heterochronous sampling |
| `diagnostics` | calibration diagnostics settings |
| `analysis` | non-standard analysis family (see below) |
| `custom_fragments` | opt-in hand-written XML (see [extensions](extensions.md)) |

Only `alignments` and `partitions` are semantically required; everything else has defaults (`tree_prior: yule`, `control.beast_version: "1.10.4"`, `chain_length: 10000000`, ...).

## Step 2 — Start from the minimal example

```yaml
metadata:
  analysis_name: demo
alignments:
  - id: a1
    file: primates.fasta        # relative to THIS yaml file
    data_type: nucleotide
partitions:
  - id: p1
    alignment_id: a1
    from: 1                     # 1-based, inclusive
    to: 898
    site_model:
      substitution_model: {type: jc69}
    clock_model: {type: strict}
tree_prior: {type: yule}
mcmc: {chain_length: 1000000}
loggers:
  trace_log: {file_name: demo.log, log_every: 1000}
  tree_log: {file_name: demo.trees, log_every: 1000}
output: {file: demo.xml}
control: {beast_version: "1.10.4", seed: 1}
```

Generate without writing anywhere (XML goes to stdout):

```
beastxpy generate --config demo.yaml
```

Add `--output out.xml` to write a file, and `--beast-validate` for Gate 3.

## Step 3 — Pick model names

`beastxpy list-models` prints the registered names; these are the values YAML accepts:

| Category | Names |
|---|---|
| substitution (nucleotide) | `jc69`, `hky`, `tn93`, `gtr` |
| substitution (amino acid) | `wag`, `jtt`, `dayhoff`, `blosum62`, `cprev`, `mtrev` |
| clock | `strict`, `ucln` (lognormal), `uce` (exponential), `rlc` (random local) |
| tree prior | `yule`, `birth_death`, `coalescent_constant`, `coalescent_exponential`, `coalescent_logistic`, `coalescent_expansion`, `skyline`, `ebsp` |
| distributions | `normal`, `lognormal`, `uniform`, `exponential`, `gamma`, `inverse_gamma`, `beta`, `poisson`, `one_on_x`, `half_normal`, `laplace`, `half_t` |

Tree-prior aliases (`birth-death`, `constant`, `exponential`, `bayesian_skyline`, `extended_bayesian_skyline`, ...) are accepted and normalised. The primary literature behind each model is collected in [references](references.md). Amino-acid models reject nucleotide alignments and vice versa. Elements that exist only in BEAST X 10.5.0 are refused when the target version is 1.10.4.

Per-model parameters:

* `hky`: `kappa`; `tn93`: `kappa1`, `kappa2`; `gtr`: `rates` (6 values, `{mode: estimated|fixed, value: [...]}`) and `frequencies`.
* `frequencies`: `{mode: estimated|empirical|fixed, value: [...], dimension: 4}`. Amino-acid models accept only `mode: empirical`. `mode: empirical` counts bases over the partition's own `from`/`to` window (the same window used by `<patterns>`), not over the entire alignment; for a single-partition analysis the two are the same. `<patterns>` uses the same window, so the model and the data stay aligned when the partition covers a subset of sites. Non-ACGT characters are ignored and the four frequencies are normalised to sum to 1.
* `strict`: `clock_rate`; `ucln`: `ucld_mean`, `ucld_stdev`; `uce`: `exponential_mean`; `rlc`: `rlc_indicators` (>= 2).
* Tree priors carry their own parameters (`birth_rate`, `population_size`, `growth_rate`, `t50`, `skyline_groups`, ...); using a parameter that does not belong to the chosen prior is an error.

## Step 4 — Add calibrations

Every calibration needs a distribution — there is no silent uniform fallback:

```yaml
calibrations:
  - name: root
    taxa: null                # null = the root calibration
    distribution:
      type: lognormal
      parameters: {mean: 3.4, stdev: 0.5}   # mean on the LOG scale, must be > 0
  - name: human_chimp
    taxa: [Human, Chimp]
    calibration_method: crown # or stem
    distribution: {type: normal, parameters: {mean: 6.0, stdev: 0.5}}
    provenance:
      source: fossil
      calibration_type: soft  # hard bounds violate -> error, soft -> warning
      original_age_min: 5.0
      original_age_max: 7.0
```

Distribution parameter rules that most often bite:

| Type | Parameters | Notes |
|---|---|---|
| `lognormal` | `mean`, `stdev`, optional `meanInRealSpace` | `mean` must be **> 0** even on the log scale |
| `uniform` | `lower`, `upper` | |
| `exponential` | `mean` (or `rate`, stored as `1/rate`) | |
| `gamma` | `shape`, `scale` | aliases `alpha`/`beta` |
| `poisson` | `mean` | `offset` is **mandatory** |
| others | see `--help` on `check-data` errors | `offset` only for lognormal/exponential/gamma/inverse_gamma/beta |

`chi_square` is a recognised spelling but rejected as unsupported (BEAST has no chi-square prior parser on either version). Conflicting or partially-overlapping monophyletic clades are Gate 0/Gate 2 errors; run `beastxpy diagnose` for the detail.

**`meanInRealSpace` trap.** BEAST reads a lognormal `mean`/`stdev` in log space unless `parameters.meanInRealSpace: true`; BEAUti's calibration builder defaults to a real-space mean. If the flag is omitted, Gate 2 emits an `info` finding (`LOGNORMAL_LOG_SPACE_MEAN`) stating the actual prior median and 95% interval, e.g. offset 25 with `mean: 1.0, stdev: 0.5` has median about 27.7 Ma, not 25 Ma. Set the flag when you mean a date in Ma.

## Step 5 — Set priors, run length and logging

```yaml
parameter_priors:
  - parameter: clock_rate
    distribution: {type: exponential, parameters: {mean: 1.0}}
  - parameter: kappa          # or an exact emitted id like gene2.clock.rate
    distribution: {type: lognormal, parameters: {mean: 1.0, stdev: 1.25}}
mcmc:
  chain_length: 100000000     # real analyses: aim for ESS > 200
  pre_burnin: 0               # must be < chain_length
  auto_optimize: true
loggers:
  trace_log: {file_name: demo.log, log_every: 10000}
  tree_log: {file_name: demo.trees, log_every: 10000}  # names must differ
```

`log_every` defaults to `chain_length // 1000`; a good rule is 1000-10000 logged samples for stable ESS estimates.

`auto_optimize: true` mirrors BEAUti and emits `<mcmc autoOptimize="true">` without a `targetAcceptance` attribute, so BEAST uses its own defaults (about 0.234 for tree operators and 0.3 for scalar operators). BeastXPy does not override them.

**Time units.** The Yule, birth-death and coalescent starting-tree models carry the BEAST attribute `units="substitutions"`. In BEAST 1 this attribute is descriptive only and does not convert node heights; calibrations and tip dates remain in Ma as written. It is emitted for compatibility with the official parser examples, not as a statement that the time axis is in substitutions.

## Where the automatic priors come from

When `parameter_priors` does not name a parameter, `ModelAssembler` emits a default. These defaults are **starting points**, not universal recommendations; the provenance column states whether a value is copied from BEAUti or is a BeastXPy convention. Override them with `parameter_priors` when needed.

| Parameter | Default emitted | Provenance |
|---|---|---|
| `yule.birthRate`, `bMinusD` | lognormal (log mean 2, log sd 1.5) | BEAUti 1.10.4 `PartitionTreePrior` default; Drummond et al. (2012) |
| `kappa`, `kappa1`, `kappa2` | lognormal (log mean 1, log sd 1.25) | BEAUti 1.10.4 `PartitionSubstitutionModel` default; Drummond et al. (2012) |
| `ucld.stdev`, any `*.stdev` | exponential (mean 1/3) | BEAUti relaxed-clock default; Drummond et al. (2006) |
| `siteModel.alpha` | lognormal (log mean 1, log sd 1.25) | BeastXPy convention; BEAUti uses exponential mean 0.5 |
| `clock.rate`, `ucld.mean` | oneOnX | BeastXPy convention; BEAUti uses a CTMC rate reference prior |
| `growthRate` | normal (mean 0, sd 1) | BeastXPy convention; BEAUti uses a Laplace prior |
| `popSize` | uniform (0, 1e8) | BeastXPy convention; BEAUti uses oneOnX |
| `relativeDeathRate` | uniform (0, 1) | BEAUti 1.10.4 `PartitionTreePrior` and BEAST's implicit bound |
| `pInv`, GTR rates | uniform over the declared bounds | BEAST parameter-bound convention |
| simplex frequencies | uniform over the simplex | BeastXPy convention; BEAUti uses a Dirichlet prior |
| any other unconstrained parameter | lognormal (log mean 1, log sd 1.25) | BeastXPy fallback, deliberately weakly informative |

The BEAUti-attributed rows were checked against the 1.10.4 code paths that write the XML templates. The BeastXPy-convention rows are deliberate choices, not BEAUti defaults; the generated XML comment and Gate 2 name the same provenance. The fallback should be replaced for a real analysis.

Automatic priors are visible at every stage:

1. **XML.** Each automatic prior element is preceded by a comment starting `BXP auto prior (BEAUti-1.10.4 convention)` that names the parameter and the emitted distribution.
2. **Gate 2.** `generate --beast-validate` lists every automatic prior as an `info` finding with code `AUTO_PRIOR`.
3. **Report.** `beastxpy report` has an **Automatic priors** section listing every annotation found in the generated XML.
4. **Override.** Any of them can be replaced under `parameter_priors`.

No default is hidden.

## Step 6 — Version, seed, tip dates

* `control.beast_version`: `"1.10.4"` (default), `"10.5.0"`, or `"both"`; `--beast-version` on the CLI overrides it.
* `control.seed`: default RNG seed (0..2^31-1); `run --seeds` overrides.
* `control.initial_tree`: `coalescent` (default), `upgma` or `newick`; the latter two cannot be combined with calibrations.
* `tip_dates`: mapping taxon → age, e.g. `tip_dates: {Bovine: 1.0}` or per-taxon `{value: 1.0, direction: forward, units: years}`. Time must be identifiable: give at least one calibration, tip date, or an estimated clock rate.

## Coverage analyses and fragments

Setting `analysis.type` switches to a non-standard family (discrete traits, ancestral states, BDSS, StarBEAST, MLE, ...). Each is version-gated, and some are blocked outright:

```
beastxpy capabilities
```

prints the authoritative list. `codon` and `recombination` are rejected at Gate 0 because the official BEAST parsers cannot accept them; the reason is recorded per version in the manifest. Hand-written XML goes through `custom_fragments` — see [extensions](extensions.md).

## When Gate 0 rejects your file

The error names the section, the key and the reason:

```
Gate 0: FAILED -- partitions[0].site_model.gamma_shape: gamma_shape requires gamma_categories > 1
```

Fix exactly what is named. `beastxpy check-data --config demo.yaml` runs the data-side pre-checks (duplicate taxa, ambiguous sequences, calibration conflicts) without generating anything.
