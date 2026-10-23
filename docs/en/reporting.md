# Reporting

[English](reporting.md) | [中文](../zh/reporting.md)

`beastxpy report` turns a run directory into one HTML file.

## Step 1 — Finish (or skip) the assessment

The report includes the assessment if it can find one. Normal order:

```
beastxpy assess runs/demo --json runs/demo/assessment.json
```

If you call `report` without an assessment, the report says **not assessed / 未评估** and shows no diagnostic numbers - it never estimates or back-fills them.

## Step 2 — Render

```
beastxpy report runs/demo --output runs/demo/report.html --lang en --include-config
```

Expected output:

```
report: runs/demo/report.html
conclusion: completed
chains: 2
assessment: present (verdict fail)
```

(The full output also echoes `language: en`; with `--lang zh` it echoes `language: zh`.)

The file has no external CSS, JavaScript, fonts or images: it opens from `file://` on a machine with no network. Figures are inlined as `data:` URIs. The tree log itself stays a standard BEAST NEXUS file - for interactive exploration (rotations, dating, colouring) open it in FigTree or iTOL; the report is the static, shareable summary, not a tree viewer.

## Sections

Conclusion, provenance and fingerprints, Gate 0-3 status, automatic priors (when the generated XML contains `BXP auto prior` annotations), diagnostics, run metadata, trace and assessment summary, a hashed file manifest, and the limitations.

The automatic-prior section lists the parameter, the emitted distribution and the XML source. It is extracted from the generated document, not inferred from the YAML; a workdir without a generated XML says so.

## Honesty rules

* If `assess` was not run, the report says **not assessed / 未评估** and shows no diagnostic numbers. It never estimates or back-fills them.
* A Gate that did not run is shown as `not-run` with its reason, never as a tick.
* Every user-supplied string is HTML-escaped before it is written.

## Languages

`--lang en` and `--lang zh` produce complete reports; there is no mixed-language output and no English fallback inside a Chinese report.

## Machine-readable output

```
beastxpy report runs/demo --output report.html --json report.json
```

The JSON carries the same values the HTML renders, under `beastxpy.report/v1`.

## Options

`--include-config` embeds the configuration found in the run directory. `--assessment PATH` points at an assessment file outside the default lookup. `--no-plot` skips the trace figure for a smaller file. `--ess-threshold N` changes the ESS line the report draws; it does not re-run the assessment.
