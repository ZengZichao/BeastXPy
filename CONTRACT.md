# BeastXPy behavioural contract

This document is the public statement of the interfaces and honesty rules that the code and tests enforce. It is referenced from source comments as `CONTRACT.md §<section>`. Section numbers are stable; prose may be clarified without renumbering.

## §1 Scope

BeastXPy is a workflow layer for BEAST 1.10.4 and BEAST X 10.5.0. It does not replace BEAST, does not assert that a model is biologically adequate, and does not declare MCMC convergence. Every number it reports is either computed from the named input artefact or reported as `not_computable` with a reason.

## §2 Gates

| Gate | Question | Engine | Failure behaviour |
|---|---|---|---|
| 0 | Is the YAML a valid, self-consistent analysis? | pure Python | generation/run aborts, exit 1 |
| 1 | Is the generated XML structurally valid BEAST 1? | pure Python | generation/run aborts, exit 1 |
| 2 | Are calibrations and data usable and non-contradictory? | pure Python | errors abort; warnings/info are reported |
| 3 | Does the official BEAST jar accept and instantiate it? | Java | exit 2 when not run, exit 3 when BEAST rejects it |

Gate 3 has four low-level states: `executed-pass`, `executed-fail`, `not-run` and `environment-missing`. The public API collapses `environment-missing` into `not-run` while preserving `raw_status` and `environment_error`. A gate that did not execute is never rendered as `OK`.

The Gate-3 mode is always recorded and shown in the CLI output:

| Mode | Likelihood |
|---|---|
| `parse` | not evaluated (project `treeLikelihood` shim) |
| `instantiate` | not evaluated |
| `java-core` | not evaluated (construct-only shim) |
| `beagle` | evaluated by the native official parser set |

Only `beagle` is full official validation. Claims in the README, `beastxpy capabilities` and reports name the mode when it is not `beagle`.

## §5 F1 -- assessment honesty rules

`beastxpy assess` is diagnostics only. The following are hard requirements:

1. With fewer than two usable chains, PSRF and ASDSF are reported as `status: "not_computable"` with a plain-language reason. No value is fabricated, imputed or carried over from another run.
2. A statistic whose input column is absent is `not_computable` and names the missing column. It is never replaced by a guessed number.
3. Bulk/tail ESS require at least two input chains. A single chain is not split in half to manufacture a second chain; single-chain runs use the Geyer ESS.
4. Automatic burn-in returns `not_computable` when no Geweke score exists; it never silently treats an unassessed chain as having zero burn-in.
5. A `pass` verdict means "no configured threshold was crossed", not "the chain converged". The report says so in every language.
6. The JSON envelope always carries the top-level keys listed in §7.

## §5 F2 -- data-check finding severities

`beastxpy check-data` builds findings through one table. Errors are never downgraded to warnings.

| Finding id | Severity |
|---|---|
| `duplicate-taxon` | error |
| `empty-taxon` | error |
| `duplicate-sequence` | warning |
| `taxon-name-format` | warning |
| `illegal-character` | error |
| `high-ambiguity` | warning |
| `partition-overlap` | error |
| `partition-gap` | warning |
| `partition-out-of-range` | error |
| `partition-taxon-mismatch` | error |
| `calibration-taxon-missing` | error |
| `calibration-empty-taxa` | error |
| `tip-date-format` | error |
| `tip-date-order` | warning |
| `root-height-conflict` | error |
| `codon-length` | error |
| `codon-stop` | warning |
| `empirical-tree-taxa` | error |
| `config-parse-error` | error |
| `internal-error` | error |

Exit codes: 0 = no findings or info only, 1 = warnings, 2 = errors.

## §7 -- `assess` command surface

```
beastxpy assess <workdir> [--trace PATH ...] [--trees PATH ...]
                [--burnin FRACTION|auto] [--ess THRESHOLD]
                [--psrf THRESHOLD] [--strict] [--json PATH]
                [--html PATH] [--no-plots] [-q]
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | verdict `pass` or `warn` (non-strict) |
| 1 | verdict `warn` under `--strict` |
| 2 | unreadable inputs or no chains found |
| 3 | verdict `fail` |

The JSON document has schema `beastxpy.assessment/v1` and always contains these top-level keys:

```
schema_version, generated_at, tool_version, workdir, verdict, strict,
thresholds, chains, multichain, reasons, limitations
```

Per-chain entries always include `seed`, `status`, `trace_log`, `tree_log`, `run_log`, `run_meta`, `samples`, `burnin_fraction`, `burnin_samples` and `parameters`. The `multichain` object always contains `available`, `n_chains`, `psrf` and `asdsf`; each statistic object carries `status`, and either `values`/`value` or a `reason`.

## §8 Automatic priors

Every prior that BeastXPy adds because the configuration did not specify one is:

1. marked in the generated XML with a comment beginning `BXP auto prior (BEAUti-1.10.4 convention)`;
2. reported as a Gate-2 `info` finding with code `AUTO_PRIOR`, visible in `generate --beast-validate` output;
3. listed in the `report` HTML section "Automatic priors";
4. overridable under `parameter_priors` in the YAML.

## §9 Related documents

- [Validation modes and evidence](docs/en/validation.md)
- [Assessment rules](docs/en/assessment.md)
- [Data checks](docs/en/cli.md#beastxpy-check-data)
- [Native BEAGLE](docs/en/native-beagle.md)
