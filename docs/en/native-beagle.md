# Native BEAGLE

[English](native-beagle.md) | [中文](../zh/native-beagle.md)

A real MCMC likelihood needs the native hmsbeagle JNI library. Without it BEAST falls back to a pure-Java implementation that never implemented the site log-likelihood entry point, so a run stops on its first evaluation rather than producing a chain that only looks real.

## Step 1 — Get the right build

| BEAST | Native BEAGLE |
|---|---|
| 1.10.4 | 3.1.x |
| 10.5.0 | 4.0.x |

(BEAGLE itself: Ayres et al. 2012 - see [references](references.md).)

The ABI is not compatible across these; a 3.1.x library does not serve BEAST X 10.5.0 and vice versa. Build or install the version your BEAST needs, then locate the directory that contains the library **itself**: `hmsbeagle64.dll` (Windows) or `libhmsbeagle-jni.so` / `libhmsbeagle.so` (Linux). BEAST's own `lib/beagle` folder only contains a Java wrapper and never counts.

## Step 2 — Point BeastXPy at it

```
export BEASTX_BEAGLE_1_10_4_LIB="/opt/beagle/3.1.0"
export BEASTX_BEAGLE_10_5_0_LIB="/opt/beagle/4.0.1"
```

Both variables are optional and independent: set only the version you use. The directories are discovered through, in order: an explicit `--beagle-dir` (`compare`) / `--beagle-lib` (`lock`, `reproduce`) argument, then `BEASTX_BEAGLE_<tag>_LIB`, `BEASTX_BEAGLE_LIB`, `BEAGLE_HOME`, `BEAGLE_LIB` (each also tried with `/lib` and `/bin` appended), then a `beagle` directory next to the selected jar.

## Step 3 — Verify it is discoverable

```
beastxpy check-environment
```

`check-environment` reports the jars and Java. To confirm BEAST itself can use the library, run a Gate-3-validated generation — with the variable set, Gate 3 runs in `beagle` mode (the strongest validation):

```
beastxpy generate --config examples/config_subst_jc69.yaml \
    --output out.xml --beast-validate
```

The per-version core selection is automatic: `beagle` when a native library is discoverable, `java-core` otherwise (with a note). A library that exists but fails to load is reported and the weaker core is used — an unusable BEAGLE is an environment fault, not a defect in the XML.

## How `run` uses it

`beastxpy run` exposes the discovered directory to the BEAST process the same way Gate 3 does: `-Djava.library.path=<dir>` **plus** the loader path (`LD_LIBRARY_PATH` on Linux, `DYLD_LIBRARY_PATH` on macOS, `PATH` on Windows). The second part matters: BEAGLE loads its resource plugins (`libhmsbeagle-cpu.so` and friends) by bare library name through `dlopen`/`LoadLibrary`, which consults the loader path, not `java.library.path`. Setting only `-Djava.library.path` leads to

```
Unable to load CPU plugin!
...
BEAGLE function, create, returned error code -6 (No resource matches requirements)
```

BeastXPy handles both for you; if you run BEAST by hand, export `LD_LIBRARY_PATH` yourself.

## The bitness trap

**The JVM and the library must have the same bitness.** On Windows it is easy to end up with a 32-bit JDK (`os.arch=x86`) and 64-bit `hmsbeagle64.dll`. The symptom is:

```
native hmsbeagle library not found in java.library.path
```

even though the file is right there. Check before blaming the path:

```
java -XshowSettings:properties -version 2>&1 | grep 'os.arch'
```

`os.arch = amd64` is 64-bit; `x86` is 32-bit.

## Threads on Windows

Pass `--beagle-threads 1` on Windows. Multi-threaded BEAGLE has been unreliable there, and a single thread is fast enough for validation and short chains.

## Which commands need it

| Command | Needs native BEAGLE |
|---|---|
| `validate --beast` | no (falls back to the java-core harness) |
| `generate --beast-validate` | no (same) |
| `run` | yes for every BeastXPy document (all contain `<treeLikelihood>`) |
| `compare` | yes, and fails closed without it |
| `assess`, `report`, `lock`, `check-data` | no |
