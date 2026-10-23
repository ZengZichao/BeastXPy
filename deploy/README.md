# BeastXPy containers

Two definitions, one image:

| File | Builder |
|---|---|
| `Dockerfile` | Docker / Podman |
| `apptainer.def` | Apptainer / Singularity |

`beastxpy container --print docker` and `--print apptainer` print these files
verbatim, so what you read here is exactly what you build.

## What is in the image

- Python 3.10 (Ubuntu 22.04 from the Temurin base)
- JDK 17 (`eclipse-temurin:17-jdk-jammy`)
- BEAST 1.10.4 and BEAST X 10.5.0 — the jars committed under
  `beastxpy/tools/jars/` are installed as package data, so Gate 3 works with no
  network access at run time
- Native BEAGLE 4.0.1, compiled from source during the build, installed to
  `/usr/local/lib` and exported as `BEASTX_BEAGLE_10_5_0_LIB`
- BeastXPy 0.1.0, entrypoint `beastxpy`
- An unprivileged user, `beastxpy` (uid/gid 10001); `/work` is its writable
  working directory

## Build

From the repository root (the build context must contain `beastxpy/`):

```
docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .
```

The build compiles BEAGLE, so it needs a C/C++ toolchain; the image installs one
in an early layer. The base image is pinned by digest
(`eclipse-temurin:17-jdk-jammy@sha256:73ed4422...`) and the BEAGLE source
archive is verified against its SHA-256; an empty or wrong digest aborts the
build instead of warning.

### Building without network access

The default build downloads the BEAGLE source archive. To avoid that entirely,
put the archive in `deploy/beagle/` first:

```
cp /path/to/beagle-lib-4.0.1.tar.gz deploy/beagle/beagle-lib.tar.gz
docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .
rm deploy/beagle/beagle-lib.tar.gz    # never commit the tarball
```

The tarball must be the official v4.0.1 source archive and must match the
pinned SHA-256 `9d258cd9bedd86d7c28b91587acd1132f4e01d4f095c657ad4dc93bd83d4f120`;
the Dockerfile and `apptainer.def` verify it for both downloaded and local
archives. It must unpack to a single top-level directory, which is the
upstream layout.

### Mirrors

If `registry-1.docker.io` is unreachable, pull the base images from a mirror and
retag them so the definition still works unchanged:

```
docker pull docker.m.daocloud.io/eclipse-temurin:17-jdk-jammy
docker tag  docker.m.daocloud.io/eclipse-temurin:17-jdk-jammy eclipse-temurin:17-jdk-jammy
```

Then run the normal `docker build` command. The definition keeps the canonical
image name; the mirror is a property of the machine, not of the deliverable.

## Run

```
docker run --rm beastxpy:0.1.0 --help
docker run --rm beastxpy:0.1.0 --version

# work on files in the current directory
docker run --rm -v "$PWD:/work" beastxpy:0.1.0 \
    generate --config /work/config.yaml --output /work/out.xml --beast-validate

docker run --rm -v "$PWD:/work" beastxpy:0.1.0 \
    check-data --config /work/config.yaml
```

Apptainer:

```
apptainer build beastxpy_0.1.0.sif deploy/apptainer.def
apptainer run beastxpy_0.1.0.sif --help
apptainer exec --bind "$PWD:/work" beastxpy_0.1.0.sif \
    beastxpy generate --config /work/config.yaml --help
```

## BEAGLE version pairing

BEAST's JNI ABI is version-specific:

| BEAST | BEAGLE | Bundled here |
|---|---|---|
| BEAST X 10.5.0 | 4.0.x | yes, 4.0.1 |
| BEAST 1.10.4 | 3.1.0 | **no** — supply it yourself if you need native likelihoods on 1.10.4 |

The image always sets `BEASTX_BEAGLE_10_5_0_LIB=/usr/local/lib`. For native
1.10.4 runs, bind-mount a directory containing the 3.1.0 `libhmsbeagle*.so` and
set `BEASTX_BEAGLE_1_10_4_LIB` to it. Gate 3 validation does not need BEAGLE at
all; only a real MCMC likelihood does.

`compare` requires native BEAGLE and fails closed (exit 2, no numbers) without
it. That is deliberate.

## Licensing

BeastXPy is MIT AND LGPL-2.1-only. BEAST is LGPL-2.1-only; the jars carry their
own `META-INF/LICENSE.txt` and `META-INF/NOTICE.txt`, which are preserved inside
the packaged jar. BEAGLE is LGPL-3.0. This repository's `LICENSE` and `NOTICE`
apply to BeastXPy's own code. If you redistribute the image, keep these notices
and make the corresponding source available as those licences require.

## Verification status

**Built and run on 2026-10-21**, on Windows with Docker Desktop 29.8.0
(linux/amd64), using a local BEAGLE 4.0.1 source archive so the build performed
no download.

```
docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .        -> exit 0
docker run --rm beastxpy:0.1.0 --version                     -> beastxpy 0.1.0
docker run --rm --entrypoint id beastxpy:0.1.0               -> uid=10001(beastxpy)
docker run --rm beastxpy:0.1.0 check-environment             -> JDK 17.0.20.1,
                                                                both jars resolved
docker run --rm beastxpy:0.1.0 capabilities                  -> 35 rows, exit 0
docker run --rm -v ...:/data:ro beastxpy:0.1.0 generate \
    --config /data/config_subst_jc69.yaml --beast-validate    -> Gate 3 OK, exit 0
```

Native BEAGLE was confirmed inside the image:

```
/usr/local/lib/libhmsbeagle-cpu.so.40.0.0
lookup_beagle_details("10.5.0") -> {'path': '/usr/local/lib',
                                    'source': 'env:BEASTX_BEAGLE_10_5_0_LIB'}
```

**Not verified:** an Apptainer build (Apptainer is not installed on the build
machine), and a native 1.10.4 MCMC inside the image (the 3.1.0 library is not
bundled). Neither is claimed to work.
