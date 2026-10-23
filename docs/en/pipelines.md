# Pipelines

[English](pipelines.md) | [中文](../zh/pipelines.md)

`beastxpy pipeline` writes a Snakemake or Nextflow file that runs the seed matrix and the post-processing steps.

## Step 1 — Generate the pipeline

```
beastxpy pipeline --config examples/config_basic.yaml --type snakemake --output-dir pipeline
```

Expected output:

```
wrote pipeline/Snakefile
wrote pipeline/config_basic.yaml
```

The generated text is syntax-checked by the command itself, and the config is copied next to it so the directory is self-contained. `--type nextflow` writes `pipeline/main.nf` instead.

## Step 2 — Run it

Snakemake works natively on Linux, macOS and Windows:

```
cd pipeline
snakemake -s Snakefile --cores 4
```

Nextflow is **not** natively supported on Windows: its SIGHUP process registration fails there. Run the generated pipeline under WSL or Linux.

Both pipelines assume `beastxpy` is on `PATH` and inherit the BEAST and BEAGLE environment variables described in [native-beagle](native-beagle.md) and [installation](installation.md). A pipeline is a convenience wrapper around the same commands you can type - if you need to inspect intermediate state, or you are running one analysis, the direct commands from the [quickstart](quickstart.md) are easier to reason about.
