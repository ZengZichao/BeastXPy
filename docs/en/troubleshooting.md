# Troubleshooting

[English](troubleshooting.md) | [中文](../zh/troubleshooting.md)

Symptom, cause, fix. Entries are ordered the way you are likely to hit them: environment first, then config, then runs.

## `no usable Java runtime found` / `NOT RUN`

Gate 3 or the runner could not start a JVM. `beastxpy check-environment` shows what resolved and from which source. Fix by setting `JAVA_HOME` (or `BEASTX_JAVA`) to a JDK and putting its `bin` on `PATH`.

## `jar ... NOT FOUND`

No BEAST jar for that version. Either rely on the bundled jars (do nothing) or set `BEAST_1_10_4_HOME` / `BEAST_10_5_0_HOME` / `BEASTX_JAR`. The `check-environment` `source:` field tells you which mechanism won.

## `native hmsbeagle library not found in java.library.path`

The library exists but the JVM cannot load it. Almost always a **bitness** mismatch: a 32-bit JVM with 64-bit `hmsbeagle64.dll`.

```
java -XshowSettings:properties -version 2>&1 | grep os.arch
```

`amd64` is 64-bit, `x86` is 32-bit. Point `JAVA_HOME` at a 64-bit JDK, then set `BEASTX_BEAGLE_1_10_4_LIB` or `BEASTX_BEAGLE_10_5_0_LIB` at the directory holding the library itself (not BEAST's `lib/beagle` wrapper folder).

## `Unable to load CPU plugin!` then `error code -6 (No resource matches requirements)`

The JNI library loaded, but BEAGLE's resource plugins (`libhmsbeagle-cpu.so`, ...) did not. Plugins are `dlopen`ed by bare name, which follows the **loader path**, not `java.library.path`. When you run BEAST through `beastxpy run` this is handled for you; when you run BEAST by hand, export `LD_LIBRARY_PATH=<beagle lib dir>` (Linux), `DYLD_LIBRARY_PATH` (macOS) or prepend to `PATH` (Windows) yourself.

## A run stops almost immediately with `No acceptable BEAGLE library plugins found`

No native BEAGLE was available to the JVM at all. Check `beastxpy check-environment`, set `BEASTX_BEAGLE_<version>_LIB`, re-run. A failed seed leaves its `run.log`, `run_meta.json` (status `failed`) and any partial logs in place; after fixing the environment, re-run with `--force` (or `--resume` with `--rerun-successful`).

## `run: --java-only cannot run this analysis`

`--java-only` maps to BEAST's `-java` flag, and BEAST's bundled pure-Java BEAGLE does not implement `getSiteLogLikelihoods` - so any document with a `<treeLikelihood>` (which is every BeastXPy document) aborts under it. Install native BEAGLE and drop the flag.

## `Gate 0: unknown key`

Configuration parsing is strict on purpose. The message names the key and the section; either fix the spelling or remove the key. Quoted booleans (`auto_optimize: "true"`) are also rejected.

## `calibrations[...].distribution.parameters.mean: must be > 0 for lognormal`

The lognormal `mean` lives on the log scale but must still be positive. For a root height of about 30 time units use `mean: 3.4` (e^3.4 ≈ 30), or give a real-space mean with `meanInRealSpace: true`.

## `Gate 2 failed` on calibrations

Calibrations contradict each other or the tree prior. Run `beastxpy diagnose --config config.yaml --output-dir diag` for the detail, including prior density plots and an overlap heatmap.

## `Gate 3/3: ... FAILED` with a BEAST parse error

BEAST itself rejected the document. The message names the XML path and the parser complaint; this is a BeastXPy bug or a custom fragment problem - please report it with the config attached. If you must ship the document while debugging, `--allow-unvalidated` writes it labelled `UNVALIDATED` (never for custom fragments, which must pass Gate 3).

## Nextflow fails on Windows

Nextflow's SIGHUP process registration does not work natively on Windows. Use WSL or Linux. Snakemake works natively.

## `assess` says `not_computable`

It is telling the truth. PSRF and ASDSF need at least two chains; run more seeds. A missing statistic means the column is absent from the log, not that the number is zero.

## A report says "not assessed"

`assess` was not run, or its JSON is not where the report looked. Run `beastxpy assess <workdir> --json <workdir>/assessment.json`, or pass `--assessment PATH`.

## `reproduce --check` fails

A field differs from the lock. The output names it. If the environment really changed, regenerate the lock; do not edit it - an edited lock is rejected outright with exit 3.
