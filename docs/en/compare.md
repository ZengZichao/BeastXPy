# Model comparison

[English](compare.md) | [中文](../zh/compare.md)

`beastxpy compare` estimates log marginal likelihoods with BEAST's own path sampling or stepping stone, then reports pairwise log Bayes factors.

## Step 1 — Prepare one config per model

Every config must describe the **same data** and differ only in the model. Name the files after the model they carry:

```
demo.yaml       JC69 + strict clock + Yule
demo_hky.yaml   HKY  + strict clock + Yule
```

Copy the config and change exactly one thing (here: only `substitution_model`). `compare` refuses (exit 3, before running BEAST) when alignment content, taxon set, partition layout or target version differ.

## Step 2 — Run the comparison

```
beastxpy compare --configs demo.yaml demo_hky.yaml --method stepping-stone \
    --workdir compare_runs --seeds 1 --chain-length 10000 --path-steps 4
```

`--chain-length` is per path step and `--path-steps` defaults to 8; the values above smoke-test the machinery in a few seconds. For a publishable estimate use `--path-steps 50` to `100` and a chain length appropriate for your data. Expected shape of the output:

```
probing environment for BEAST 1.10.4 ...
checking comparability of 2 configurations ...
  alignment.files              match
  ...
environment: java 17.0.20.1 (64-bit), jar beast.jar, BEAGLE libhmsbeagle-jni.so
running demo (1 seeds, chainLength=10000, pathSteps=4) ...
  seed 1: BEAST exit=0 (3.4 s)
  seed 1: ok
running demo_hky (1 seeds, chainLength=10000, pathSteps=4) ...
  seed 1: BEAST exit=0 (3.3 s)
  seed 1: ok
BeastXPy model comparison -- method: stepping_stone
...
models:
  demo                     ok       logML = -6393.838363  SE = 1.268922
  demo_hky                 ok       logML = -6074.787979  SE = 0.819432

pairwise log Bayes factors (a - b):
  demo vs demo_hky: logBF = -319.050384 (SE 1.510508)
      2 ln BF = 638.1008: very strong evidence for the second model (b) (Kass & Raftery band; a reporting convention, not a decision rule)
```

Each seed is an independent estimate. The text summary, CSV and HTML list the per-seed log marginal likelihoods and the within-model range (`max - min`), so seed sensitivity is visible even when the combined mean looks precise. Run seeds concurrently with `--parallel N` (default 1); the models themselves stay sequential because they share one BEAGLE environment.

## What it does internally

1. Checks the configurations are comparable: same alignment content, same taxon set, same partition layout. If not, it stops with exit 3 before running anything.
2. Derives one config per model with the marginal-likelihood analysis block and generates the XML.
3. Runs real BEAST, then reads the marginal likelihood and standard error from the results file - every number is cross-checked against the log BEAST actually wrote.
4. Writes the pairwise log Bayes factor.

## Why it fails closed

A path-sampling run needs native BEAGLE. Without it `compare` exits 2 with no numbers. It never substitutes the placeholder log or an estimate for a measurement.

## Reading a Bayes factor

The output is `2 ln BF`, following the usual convention (Kass & Raftery 1995); the marginal likelihoods come from BEAST's own path sampling and stepping-stone estimators (Xie et al. 2011; Baele et al. 2013 - see [references](references.md)). Larger positive values favour the first model, larger negative values favour the second, and the Kass-Raftery bands are a **reporting convention, not a decision rule**.

Three warnings:

* A Bayes factor compares the models you gave it. It says nothing about a model you did not try.
* Short chains, few path steps or poor mixing make the estimate unstable. The standard error is reported so you can see this; the interpretation string states whether `|log BF|` exceeds `2 * SE`. A log BF of 2 with a standard error of 3 is printed as "not distinguishable from zero" and is not evidence at all.
* The reported SE is a delta-method lower bound: path steps are treated as independent and residual dependence between them is not modelled. The true Monte-Carlo error can be larger.

## Outputs

`--json` writes `beastxpy.compare/v1` (models, pairwise, beagle probe, settings); `--csv` and `--html` write tables.
