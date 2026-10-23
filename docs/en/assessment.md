# Assessment

[English](assessment.md) | [中文](../zh/assessment.md)

`beastxpy assess` reads the output of `beastxpy run` and reports what the samples look like.

## Step 1 — Run at least two seeds

```
beastxpy run --config demo.yaml --workdir runs/demo --seeds 1,2
```

Two chains are the minimum for PSRF and ASDSF; four or more is the norm for publication work.

## Step 2 — Assess

```
beastxpy assess runs/demo --json runs/demo/assessment.json
```

On a deliberately short chain:

```
assess: runs/demo -> verdict fail (2 chain(s), strict=False)
thresholds: burnin=0.1 ess=200.0 psrf=1.01 operator=[0.01, 0.95]
  seed 1: status=ok samples=201 burnin=20 parameters=12 operators=10
  seed 2: status=ok samples=201 burnin=20 parameters=12 operators=10
  multichain: 2 usable chain(s)
    PSRF (split-chain Gelman-Rubin ...): max R-hat 1.0306
    ASDSF: 0.0000
  reasons: 24 error(s), 11 warning(s), 4 info
    [error] ess-below-threshold: seed 1: ESS 81.8 for 'posterior' is below the requested threshold 200
    ...
```

Exit code 3 = fail, 0 = pass/warn (1 = warn under `--strict`, 2 = unreadable workdir). A `fail` on a short chain is the correct answer, not a tool malfunction.

## Step 3 — Read the errors top-down

The `ess-below-threshold` errors name the parameters that did not move enough. The cure is a longer chain (and possibly better-tuned operators), not a higher `--ess` threshold. `psrf-above-threshold` means the chains disagree about a parameter's location. `bulk-ess-below-threshold` and `tail-ess-below-threshold` mean the location or the tails are poorly sampled. `asdsf-high` (warn threshold 0.05) means the chains disagree about topology.

## What it reports

* **Per parameter** - ESS, ACT, mean, median and 95% HPD (ESS via Geyer's initial monotone sequence, Geyer 1992). Every column also carries `per_chain`: one entry per chain with its own `act`, `ess`, `act_max_lag` and `act_truncated`, so a truncated chain can be identified even when the combined `act_truncated` flag is true.
* **Multi-chain PSRF** - classic split-chain R-hat (Gelman & Rubin 1992; split-chain per Gelman et al. 2013) **and** rank-normalized split R-hat (Vehtari et al. 2021), together with bulk and tail ESS per parameter. The threshold check uses the rank-normalized value when it is computable and falls back to the classic R-hat otherwise; both columns are shown in the HTML report and the JSON (`multichain.rank_diagnostics`).
* **Topology ASDSF** - average standard deviation of split frequencies (MrBayes convention, Ronquist & Huelsenbeck 2003).
* **Operators** - acceptance rate per move, flagging below 1%, above 95% and never-triggered moves.
* **Tree summaries** - root height, clock rate, `ucld.mean`, `ucld.stdev` with HPDs, when those columns exist.

## The rule that matters

With fewer than two usable chains, PSRF and ASDSF are reported as `not_computable`, with a reason. There is no value to report, so none is invented. The same applies to a statistic whose column is absent from the log.

## Verdicts

| Verdict | Exit (default) | Exit (`--strict`) |
|---|---|---|
| `pass` | 0 | 0 |
| `warn` | 0 | 1 |
| `fail` | 3 | 3 |

`fail` means a configured threshold was crossed. `warn` means something is worth looking at but did not cross an error threshold.

## What a verdict is not

A `pass` means no threshold you configured was crossed. It does **not** mean the chain converged. ESS and R-hat are finite-sample estimates and are noisy on short chains; they can look fine on a chain that has not explored the posterior.

`assess` is diagnostics. It does not select models - `compare` does marginal likelihoods - and it does not tell you whether your model is appropriate.

## Thresholds and outputs

The defaults - ESS 200, PSRF 1.01, ASDSF 0.05, operator acceptance 0.01-0.95 - are conventions, not laws: the ESS and PSRF guidance follows the BEAST book (Drummond & Bouckaert 2015), the ASDSF band follows MrBayes practice. Every one is adjustable on the command line; the primary sources are collected in [references](references.md).

```
beastxpy assess runs/demo --burnin 0.1 --ess 200 --psrf 1.01 --html report.html
```

`--burnin` is a fraction (0..1) discarded from the start of each chain, or `--burnin auto` to use the same Geweke-based automatic burn-in as `beastxpy trace --burnin auto`; the effective fraction is recorded per chain. If no Geweke score is computable (fewer than 100 samples, or every parameter constant), the run is marked `burnin_status: not_computable`, a warning finding is emitted, and the verdict is downgraded to `warn`; burn-in 0 is used only because no samples can be discarded, and is explicitly not an estimate of convergence. Pass an explicit fraction in that case. The defaults differ on purpose: `trace` analyses one log and defaults to the automatic probe, while `assess` defaults to a fixed 0.1 so every seed is discarded identically and the choice is reproducible from the JSON alone. Pass `--burnin auto` when you want the probe across seeds. `--html` writes a self-contained summary page next to the JSON and prints the thresholds actually used, so a pass cannot be mistaken for a convergence proof independent of its settings. The JSON envelope (`beastxpy.assessment/v1`) carries `verdict`, `counts`, `chains`, `multichain`, `reasons` and `limitations`.

## Agreement with reference implementations

Two fixed, simulated fixtures in `tests/golden/reference_diagnostics/` pin the numerical behaviour to the community tools (tested by `tests/test_reference_diagnostics.py`):

* **ArviZ 0.23.4** (`rank` R-hat, `bulk` and `tail` ESS): the rank-normalized split R-hat, bulk ESS and split-chain tail ESS reproduce ArviZ to the printed precision (relative difference < 1e-9) on four fixed AR(1) chains. ESS is deliberately **not capped at `m*n`**: the stored anti-correlated fixture has 1,000 draws and an ESS of 3,000 in both ArviZ and BeastXPy, because anti-correlated draws can carry more information than independent draws. This is the reference behaviour, not an upper-bound claim.
* **Tracer 1.7.2** on a fixed BEAST-style log: the mean and 95% HPD endpoints agree to < 1e-9 relative, ESS and ACT to < 0.1%, and the median to < 0.002 (Tracer interpolates the median differently). ACT is reported in samples; Tracer reports it in logged states, so divide Tracer's ACT by the logging interval before comparing them.

The 95% HPD is the shortest window containing `ceil((1-alpha) * n)` observations. Tracer's HPD may select a window one order statistic different on small samples; the difference is documented rather than hidden. The single-chain ACT estimator keeps the unbiased `n/(n-lag)` autocovariance correction; on the fixed fixture this agrees with Tracer to 0.06%, and the multi-chain ESS path uses the biased autocovariance required by the Vehtari et al. (2021) marginal-variance decomposition.
