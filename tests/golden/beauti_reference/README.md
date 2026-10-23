# BEAUti-convention reference fixtures

These three XML subsets are the external reference for
`tests/test_beauti_reference.py`. They are **hand-transcribed from BEAUti
1.10.4 source defaults**, not produced by a live BEAUti run. A headless
driver was also tried against the BEAUti generator API in the 1.10.4 and
10.5.0 builds; its default output is rejected by BEAST itself
(`coalescentTree` references an object emitted as `coalescentSimulator`), so
the references below are transcribed from source defaults instead. Their
values come from:

- `src/dr/app/beauti/options/PartitionTreePrior.java` (Yule birth rate,
  birth-death parameters);
- `src/dr/app/beauti/options/PartitionSubstitutionModel.java` (kappa, gamma
  shape, GTR rates, base frequencies);
- `src/dr/app/beauti/options/PartitionClockModel.java` (clock rate, UCLN
  mean and stdev);
- `src/dr/app/beauti/generator/ParameterPriorGenerator.java` (XML shape of
  each prior);
- `examples/Benchmarks/benchmark1.xml`, a BEAUti v1.6.0pre output shipped in
  the BEAST 1.10.4 source tree, used to confirm the XML shape and the
  kappa/stdev conventions.

Each file contains a comment listing the documented differences from this
generator. The test asserts each difference explicitly, so a future change
that accidentally "fixes" or worsens a convention fails loudly. The reference
files are not runnable BEAST documents (they omit taxa and some ids); only the
model, prior and likelihood fragments are compared.

## Compared conventions

| Convention | BEAUti 1.10.4 | BeastXPy | Status |
|---|---|---|---|
| `yule.birthRate` prior | lognormal(log mean 2, log sd 1.5) | same | match |
| `kappa` prior | lognormal(log mean 1, log sd 1.25) | same | match |
| `ucld.stdev` prior | exponential(mean 1/3) | same | match |
| `relativeDeathRate` prior | uniform(0, 1) | same | match |
| clock-rate prior | CTMC rate reference | `oneOnXPrior` | documented deviation |
| gamma-shape prior | exponential(mean 0.5) | lognormal(log mean 1, log sd 1.25) | documented deviation |
| GTR rate priors | gamma(shape 0.05, scale 20) | uniform(0, 100) | documented deviation |
| base frequencies | Dirichlet(1) or fixed | uniform over the simplex | documented deviation |
| calibration `meanInRealSpace` | `true` by default | whatever the YAML says (omitted means `false`) | documented, Gate 2 warns |
| calibration XML | `distributionLikelihood` over calibration data | direct prior on a `tmrcaStatistic` | documented, both accepted by BEAST |

The deviations are deliberate BeastXPy choices, not hidden BEAUti defaults;
each is named in the generated XML comment, in Gate 2, and in
`docs/en/configuration.md`.
