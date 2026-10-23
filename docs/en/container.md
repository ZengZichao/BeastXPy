# Containers

[English](container.md) | [中文](../zh/container.md)

Two definitions ship with the source: `deploy/Dockerfile` and `deploy/apptainer.def`. Print them without opening the repository:

```
beastxpy container --print docker
beastxpy container --print apptainer
```

The command prints the file verbatim and builds nothing. The definitions live next to the `beastxpy` package in a **source checkout**; a plain wheel install carries no `deploy/` directory, so there `container --print` exits 2 and points you at the source tree (or copy the two files from it).

## What the image contains

Python 3.10, JDK 17, both BEAST jars from `beastxpy/tools/jars/`, native BEAGLE 4.0.1 compiled from source, and BeastXPy 0.1.0. It runs as the unprivileged user `beastxpy` (uid 10001) and its entrypoint is `beastxpy`. The base image is pinned by digest, and both the downloaded and the offline BEAGLE archive must match a pinned SHA-256; the build fails on an empty or wrong digest.

## Build and run

```
docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .
docker run --rm beastxpy:0.1.0 --version
docker run --rm -v "$PWD:/work" beastxpy:0.1.0     generate --config /work/config.yaml --beast-validate
```

For an offline build, put `beagle-lib.tar.gz` in `deploy/beagle/` first. See `deploy/README.md` for mirrors, the BEAGLE version pairing and the licensing position.

## What is verified

The Docker image was built and run: `beastxpy --version`, a non-root `uid=10001`, `check-environment` resolving JDK 17 and both jars, `capabilities`, and a `generate --beast-validate` whose Gate 3 passed. Native BEAGLE was confirmed present at `/usr/local/lib`.

**Not verified:** an Apptainer build (Apptainer is not installed on the build machine), and a native BEAST 1.10.4 MCMC in the image (BEAGLE 3.1.0 is not bundled; only 4.0.1 for BEAST X 10.5.0).
