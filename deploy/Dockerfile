# BeastXPy 0.1.0 - reproducible BEAST 1 runtime image.
#
# Build from the repository root so the committed BEAST jars and the BeastXPy
# source are both in the build context:
#
#   docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .
#
# The image contains Python, a JDK 17, the two official BEAST jars that ship
# with this repository, and a native BEAGLE library built from source.  It runs
# as an unprivileged user and its entrypoint is `beastxpy`.
#
# Native BEAGLE is built from an official release tarball.  To build without
# network access, drop `beagle-lib.tar.gz` into `deploy/beagle/` first; the
# build then uses that file and verifies nothing is downloaded.

# syntax=docker/dockerfile:1

FROM eclipse-temurin:17-jdk-jammy@sha256:73ed442231ec66cfe48cae0b263ce619a75ab96d162ab325f38fc0a269ee3aab

ARG DEBIAN_FRONTEND=noninteractive
ARG BEAGLE_VERSION=4.0.1
ARG BEAGLE_URL=https://codeload.github.com/beagle-dev/beagle-lib/tar.gz/refs/tags/v4.0.1
# SHA-256 of the official v4.0.1 source archive.  Both the downloaded archive
# and a tarball supplied in deploy/beagle/ must match this digest; a missing or
# wrong digest aborts the build.
ARG BEAGLE_SHA256=9d258cd9bedd86d7c28b91587acd1132f4e01d4f095c657ad4dc93bd83d4f120
ARG BEASTXPY_UID=10001
ARG BEASTXPY_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    JAVA_HOME=/opt/java/openjdk

# System packages: Python (for BeastXPy), a C/C++ toolchain and CMake (to build
# BEAGLE), and curl/ca-certificates for the optional source download.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-dev \
        build-essential \
        cmake \
        curl \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Native BEAGLE (JNI library used by BEAST for likelihood evaluation)
# ---------------------------------------------------------------------------
# `deploy/beagle/` is always present (it carries a README), so this COPY is
# valid whether or not a tarball was supplied.
COPY deploy/beagle/ /tmp/beagle-src/

RUN set -eux; \
    mkdir -p /tmp/beagle; \
    if [ -f /tmp/beagle-src/beagle-lib.tar.gz ]; then \
        echo "building BEAGLE from the tarball in deploy/beagle/"; \
        cp /tmp/beagle-src/beagle-lib.tar.gz /tmp/beagle.tar.gz; \
    else \
        echo "downloading BEAGLE ${BEAGLE_VERSION}"; \
        curl -fsSL "${BEAGLE_URL}" -o /tmp/beagle.tar.gz; \
    fi; \
    echo "${BEAGLE_SHA256}  /tmp/beagle.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/beagle.tar.gz -C /tmp/beagle --strip-components=1; \
    cd /tmp/beagle; \
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_CUDA=OFF; \
    cmake --build build --parallel "$(nproc)"; \
    cmake --install build; \
    ldconfig; \
    rm -rf /tmp/beagle /tmp/beagle-src /tmp/beagle.tar.gz; \
    test -e /usr/local/lib/libhmsbeagle.so

# ---------------------------------------------------------------------------
# BeastXPy
# ---------------------------------------------------------------------------
WORKDIR /opt/beastxpy
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY beastxpy ./beastxpy
COPY examples ./examples
COPY tools ./tools

RUN python3 -m pip install --upgrade pip \
    && python3 -m pip install . \
    && python3 -c "import beastxpy; print('beastxpy', beastxpy.__version__)"

# The BEAST jars are committed in beastxpy/tools/jars/, so they arrive with the
# package data and Gate 3 works offline.  Point BeastXPy at the native BEAGLE
# library built above for BEAST X 10.5.0 (BEAGLE 4.0.1).  BEAST 1.10.4 needs
# BEAGLE 3.1.0, which is a different ABI; supply it via a mount if you need it.
ENV BEASTX_BEAGLE_10_5_0_LIB=/usr/local/lib

# ---------------------------------------------------------------------------
# Unprivileged runtime
# ---------------------------------------------------------------------------
RUN groupadd --gid "${BEASTXPY_GID}" beastxpy \
    && useradd --uid "${BEASTXPY_UID}" --gid beastxpy --create-home --shell /usr/sbin/nologin beastxpy \
    && mkdir -p /work \
    && chown -R beastxpy:beastxpy /work /opt/beastxpy

USER beastxpy
WORKDIR /work

ENTRYPOINT ["beastxpy"]
CMD ["--help"]

LABEL org.opencontainers.image.title="BeastXPy" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.description="Reproducible BEAST 1 divergence-time estimation" \
      org.opencontainers.image.licenses="MIT AND LGPL-2.1-only"
