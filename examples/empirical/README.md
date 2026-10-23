# BeastXPy reference-data case study

This directory runs the complete BeastXPy workflow on the shipped
`examples/primates.fasta` alignment (12 taxa, 898 bp). Those sequences are a
short published/reference tutorial alignment already part of this repository;
no new empirical data were generated or downloaded.

**Intended use.** A workflow and reproducibility demonstration. It is not a
biological inference about primate divergence times, and it is not a
publication-grade analysis. The alignment is a short tutorial alignment and
the chain length is chosen for a reproducible demonstration; the recorded
`assess` verdict is `pass`, but that only means the configured diagnostic
thresholds were not crossed.

## Files

| File | Content |
|---|---|
| `config_primates_reference.yaml` | two partitions (HKY+G each), strict clock, Yule tree prior, two fossil calibrations |
| `results/config.yaml` | exact configuration used for the recorded run |
| `results/generated.xml` | BEAST XML produced by `generate --beast-validate` |
| `results/generated.fingerprint.json` | Gate-3 record for that document (`status=passed`, `mode=beagle`) |
| `results/generated_methods.tex` | LaTeX methods paragraph written with the validation result |
| `results/beastxpy.lock` | environment lock (jar, Java, BEAGLE, dependencies) |
| `results/assessment.json` | `assess` envelope (ESS, PSRF, ASDSF, verdict) |
| `results/report.html`, `results/report.json` | self-contained report and its machine-readable envelope |
| `results/case_study_summary.json` | commands, exit codes, timings and the assessment summary |

`results/` is produced by:

```
export JAVA_HOME=/path/to/jdk-17
export BEASTX_BEAGLE_1_10_4_LIB=/path/to/beagle-3.1.x/lib
python tools/run_reference_case.py \
    --workdir validation_runs/primates_reference \
    --results examples/empirical/results
```

The script refuses to invent diagnostics: if a statistic is not computable,
the assessment JSON records `not_computable` with a reason, and the summary
copies that value.

## Recorded result (2026-10-22)

BEAST X 10.5.0, BEAGLE 4.0.1, Temurin JDK 17.0.20.1, Linux x86_64; four seeds
in parallel, 2,000,000 steps, two partitions (HKY+G), strict clock, Yule prior
and two fossil calibrations.

| Metric | Result |
|---|---|
| `assess` verdict | `pass` (ESS 200, PSRF 1.01 thresholds) |
| minimum ESS per chain | 698.7 / 775.4 / 748.9 / 767.0 |
| max rank-normalized split R-hat | 1.00145 |
| ASDSF | 0.000115 |
| root height | median 49.83, 95% HPD [42.86, 60.19] |
| Catarrhini tmrca | median 26.10, 95% HPD [25.29, 27.85] |
| Homininae tmrca | median 7.56, 95% HPD [7.17, 8.07] |
| MCMC wall time / total | 192.8 s / 216.0 s |

This is a reference-data workflow demonstration, not a biological dating
result.

## Posterior comparison with BEAUti

An independent BEAUti posterior comparison requires running BEAUti, which is a
GUI application; the validation environment for this repository has no
display and no BEAUti automation. What is checked instead is structural
equivalence of the XML conventions in
`tests/golden/beauti_reference/` and `tests/test_beauti_reference.py`
(element and attribute level, with every deliberate difference documented).
The posterior-summary comparison remains open and is listed in
`docs/en/limitations.md`; it is not claimed here.
