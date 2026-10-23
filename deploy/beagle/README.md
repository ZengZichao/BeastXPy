# Offline BEAGLE source drop

`deploy/Dockerfile` and `deploy/apptainer.def` build native BEAGLE from source.

By default the definition downloads the official release archive. If this
directory contains a file named exactly `beagle-lib.tar.gz`, the build uses
that file instead and performs no download, which is what you want on an
air-gapped machine or on a network where GitHub is unreachable.

```
# optional: enable an offline build
cp /path/to/beagle-lib-4.0.1.tar.gz deploy/beagle/beagle-lib.tar.gz
docker build -f deploy/Dockerfile -t beastxpy:0.1.0 .
rm deploy/beagle/beagle-lib.tar.gz   # do not commit the tarball
```

The tarball must unpack to a single top-level directory (the upstream
`beagle-lib-<version>/` layout). It is never committed: `.gitignore` excludes
it, and shipping a third-party source archive inside this repository would
confuse the licensing position.

Version pairing matters: BEAST X 10.5.0 uses BEAGLE 4.0.x, and BEAST 1.10.4
uses the older 3.1.0 ABI. The default build produces 4.0.1, which is the one
`BEASTX_BEAGLE_10_5_0_LIB` points at.
