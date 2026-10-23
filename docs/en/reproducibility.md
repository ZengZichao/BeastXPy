# Reproducibility

[English](reproducibility.md) | [中文](../zh/reproducibility.md)

Two artefacts make a run checkable later: fingerprints, which are embedded in the XML, and a lock file, which records the environment.

## Fingerprints

```
beastxpy fingerprint --config demo.yaml
```

prints JSON with exactly three keys: `fingerprint` (`BXP-<hash12>-<tool version>`), `data_digest` and `beastxpy_version`. The same fingerprints are carried as comments inside every generated XML, so a document can be traced back to the configuration that produced it. Sidecar files (`demo.fingerprint.json`) are written next to each `generate` output.

The payload deliberately excludes non-science metadata, logger file names and provenance notes; it includes `control.seed` and the fingerprint embedding switch, because those change the analysis.

`methods` writes the LaTeX paragraph cited above and embeds a `\citep` command for every model the analysis uses (substitution model, clock, tree prior, calibration densities, MCMC scheme), plus a trailing LaTeX comment listing the full reference for each key - see [references](references.md).

## Lock

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

The lock records:

* BeastXPy, Python and platform
* Java version and architecture
* the BEAST jar by **SHA-256**, and which source it came from
* native BEAGLE version and **SHA-256**
* versions of the key dependencies
* the configuration's **content hash**
* the seeds

It records basenames and digests, never absolute paths, and it is scanned for credential-shaped values before it is written. The document is sealed with per-field digests, so an edit is detectable.

## Verify (`--check`)

```
beastxpy reproduce --lock beastxpy.lock --check --config demo.yaml
```

```
reproduce: mode=check verdict=match
  25 field(s) match
```

`--check` compares the machine field by field and exits non-zero on any mismatch (2 = a field differs, 3 = the lock itself is corrupt or was edited). If the environment really changed, regenerate the lock; do not edit it.

Without `--config` the configuration-content fields cannot be checked, so the verdict is `match_partial` (exit 0) and the reasons name every unchecked field. A `match` verdict is only printed when every recorded field was compared; do not cite `match_partial` as "the environment is identical".

## Replay (`--run`)

```
beastxpy reproduce --lock beastxpy.lock --run --config demo.yaml --workdir runs/replay
```

`--run` re-executes through the normal runner, so a replay uses the same code path as an ordinary run. It requires `--config` and `--workdir`.

## Limits

A lock pins versions and file identities. It cannot pin the operating system's maths libraries, the CPU's floating-point behaviour or BEAST's own RNG implementation. Bit-identical results across machines are not promised; reproducible *inputs* are.

Two known boundaries are recorded explicitly:

* **Java** is identified by the launcher executable's SHA-256 plus the reported version, vendor and architecture. The rest of the JRE (modules, shared libraries, the bundled trust store) is not hashed; a repackaged JRE that keeps the launcher and version string would not be detected.
* **BEAGLE** records both the expected (configuration) and observed (runtime) version, plus the JNI library SHA-256. `reproduce --check` compares the observed version as well, so an unannounced BEAGLE upgrade is reported as a field mismatch even when the library digest is unchanged.
