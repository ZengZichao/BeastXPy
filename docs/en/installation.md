# Installation

[English](installation.md) | [中文](../zh/installation.md)

Follow the steps in order; each one ends with a check you can run.

## Step 1 — Check the requirements

| Component | Needed for | Notes |
|---|---|---|
| Python 3.10+ | everything | 3.10 and 3.11 are tested |
| JDK 8+ (17 recommended) | Gate 3, running BEAST | 64-bit if you use native BEAGLE |
| BEAST jars | Gate 3, running BEAST | 1.10.4 and 10.5.0 ship with the repo |
| Native BEAGLE | real MCMC likelihoods | 3.1.x for 1.10.4, 4.0.x for 10.5.0 |

`generate`, `validate`, `check-data`, `assess`, `report`, `lock` and most other commands work without native BEAGLE; `run` and `compare` need it (see [native-beagle](native-beagle.md)).

## Step 2 — Install the package

From a checkout of this repository:

```
python -m pip install .
beastxpy --version
```

Expected:

```
beastxpy 0.1.0
```

If the `beastxpy` executable is not on your `PATH` (common with `pip install --user`), check `~/.local/bin`, or run the same code as `python3 -m beastxpy.main` — that works straight from a source checkout without installing.

## Step 3 — Provide a JDK

BeastXPy finds a JVM through `BEASTX_JAVA`, then `JAVA_HOME`, then `PATH`. Pick one way and set it for every shell that runs BeastXPy:

```
export JAVA_HOME="/path/to/jdk-17"
export PATH="$JAVA_HOME/bin:$PATH"
```

Verify:

```
java -version
```

Any JDK whose `-version` works is accepted; 17 is what the containers ship.

## Step 4 — Decide where the BEAST jars come from

By default the two jars committed under `beastxpy/tools/jars/` are used, so Gate 3 works with no external installation. To use your own BEAST builds instead:

```
export BEAST_1_10_4_HOME="/path/to/BEAST v1.10.4"
export BEAST_10_5_0_HOME="/path/to/BEAST X v10.5.0"
```

`BEASTX_JAR` selects a single jar explicitly. Discovery order: explicit environment (`BEASTX_JAR_<tag>`, `BEAST_<tag>_JAR`, `BEASTX_JAR`) → installation homes (`BEAST_<tag>_HOME`) → a `beast-mcmc-<version>/build/dist/beast.jar` under `BEASTX_REF_DIR` → official installs found next to the checkout → bundled jar last.

**Wheel size and archives.** The two bundled jars are the unmodified official BEAST v1.10.4 and BEAST X v10.5.0 release artifacts (SHA-256 values are in `NOTICE` and `tools/jars/MANIFEST.txt`). They add about 22 MB to the wheel and sdist. If you already have BEAST installed, point `BEASTX_JAR` (or `BEAST_<tag>_HOME`) at it; the bundled copy is then unused, though it is still present in the package. When preparing a Zenodo or journal archive, account for that payload and follow the repository's per-file size guidance; the source archive without jars is much smaller.

**Jar-free variant.** If you already have BEAST installed, build the `beastxpy-core` wheel, which contains the same Python code but no jars:

```
python tools/build_core_wheel.py
python -m pip install dist-core/beastxpy_core-0.1.0-py3-none-any.whl
```

In that installation Gate 3 and `run` require `BEASTX_JAR` (or `BEAST_<tag>_HOME`); `beastxpy check-environment` reports the missing jar and the next step. The LGPL-2.1 obligation applies only to the bundled BEAST binaries, so the core wheel is MIT-only.

## Step 5 — Point at a native BEAGLE (needed for `run`)

BEAST's JNI library is version-specific. Set the directory that holds `hmsbeagle64.dll` (Windows) or `libhmsbeagle-jni.so` / `libhmsbeagle.so` (Linux):

```
export BEASTX_BEAGLE_1_10_4_LIB="/path/to/beagle-3.1.x"
export BEASTX_BEAGLE_10_5_0_LIB="/path/to/beagle-4.0.x"
```

The directory must contain the native library itself, not BEAST's `lib/beagle` folder, which only contains a Java wrapper. `run` exposes the discovered directory to the JVM automatically (`-Djava.library.path` plus the loader path), so nothing else is needed. Details and version pairing: [native-beagle](native-beagle.md).

## Step 6 — Verify the environment

```
beastxpy check-environment
```

Expected on a working machine:

```
python: 3.11.0
beastxpy: 0.1.0
java: /path/to/jdk-17/bin/java (version 17.0.2, source JAVA_HOME)
jar beast-1.10.4: /path/to/beast.jar sha256:0f6eb4fd...
jar beast-10.5.0: /path/to/beast.jar sha256:df11dea1...
beagle 1.10.4: /path/to/beagle-3.1.x/lib (source env:BEASTX_BEAGLE_1_10_4_LIB)
beagle 10.5.0: /path/to/beagle-4.0.x/lib (source env:BEASTX_BEAGLE_10_5_0_LIB)
```

* `source` tells you which mechanism resolved each piece (`JAVA_HOME`, `PATH`, `env:BEAST_1_10_4_HOME`, `reference-install`, `packaged-fallback`).
* A `missing:` line and exit code 2 mean something required is absent — fix it before continuing to the [quickstart](quickstart.md).
* A missing BEAGLE prints `beagle <version>: NOT FOUND` and one `guidance:` line per version with the exact `BEASTX_BEAGLE_<tag>_LIB` variable to set. Missing BEAGLE does not make `check-environment` fail: `generate` and `check-data` work without it, while Gate 3 falls back to `java-core` and `run`/`compare` refuse to start.

Continue with the [quickstart](quickstart.md).
