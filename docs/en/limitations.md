# Limitations

[English](limitations.md) | [中文](../zh/limitations.md)

This page states what BeastXPy does not do. It is the most important page here.

## Inference

* **No convergence claim.** `assess` reports diagnostics. A `pass` verdict means no configured threshold was crossed, nothing more.
* **No model selection.** `compare` computes marginal likelihoods; interpreting them is yours.
* **No model criticism.** BeastXPy does not test whether your substitution model, clock or prior is appropriate for your data.
* **No posterior predictive checks.**

## Validation

* Gate 3 proves that the official parser accepts a document and can build the model graph. It does not prove the analysis is scientifically sound.
* The `parse` and `java-core` modes use a BeastXPy-supplied likelihood parser (see [validation](validation.md)). Only `beagle` mode uses the stock parser set.
* Validation covers the two BEAST versions in the manifest, on the platforms in the CI matrix.

## Blocked analyses

`codon` (GY94 via `<convert dataType="codon">`) and `recombination` (ARG) are rejected at Gate 0. Both failures are in the official BEAST parsers, on both versions. BeastXPy does not ship a compatibility shim for either and does not describe one as official support. `srd06` is the supported codon path.

## Data and scale

* BeastXPy assembles XML; it does not implement likelihoods.
* Very large alignments are limited by BEAST and BEAGLE, not by BeastXPy.
* `compare` needs native BEAGLE and fails closed without it.

## Reproducibility

A lock pins versions and file digests. It does not pin OS maths libraries or CPU floating-point behaviour, so bit-identical results across machines are not promised.

## Citation metadata

`CITATION.cff` names the author (ZengZichao, 曾子超) with an ORCID iD (0000-0001-6553-970X). A DOI will be added to the record once the release has been archived.

## Platforms

Windows, Linux and macOS are supported. Nextflow pipelines are not natively supported on Windows.

## Scope and roadmap

* **Reference-data demonstration, not a new empirical study.** The end-to-end case study in `examples/empirical/` uses the shipped 898 bp tutorial alignment and is a workflow/reproducibility demonstration. It must not be cited as a biological result, and the chain length is chosen for a reproducible demonstration rather than for publication-grade inference.
* **BEAUti comparison is structural.** The repository compares generated XML with hand-transcribed BEAUti 1.10.4 conventions at element/attribute level and lists every deliberate difference. A posterior-summary comparison against a live BEAUti run is not performed: BEAUti has no supported headless export, and driving its generator API directly (1.10.4 and 10.5.0 builds) produced XML that BEAST itself rejected (`coalescentTree` referenced an object emitted as `coalescentSimulator`). A live comparison therefore needs the GUI and remains open.
* **BEAST 1 / BEAST X only.** BeastXPy targets the BEAST 1 lineage (1.10.4) and its continuation (BEAST X 10.5.0). Support for BEAST 2 - a different XML schema, parser set and package ecosystem - is a possible future direction and is deliberately not promised.
* **`assess` accepts bare logs.** `--trace` (repeatable) and the matching `--trees` assess runs produced outside BeastXPy without copying them into a `<workdir>/<seed>/` layout. The operator table is unavailable in that mode (there is no `run.log`), and chain labels are the input order (1..N).
* **Naming.** "BeastXPy" means "Python workflow layer for BEAST 1 / BEAST X". It is an independent project and implies no endorsement by, or affiliation with, the BEAST or BEAGLE developer communities.
