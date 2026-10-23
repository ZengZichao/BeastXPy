# FAQ

[English](faq.md) | [中文](../zh/faq.md)

**Do I need to install BEAST?** No. Both jars ship in `beastxpy/tools/jars/`. You need a JDK for Gate 3 and for running BEAST, and native BEAGLE for a real likelihood.

**Do I need native BEAGLE?** For `run` and `compare`, yes. For `generate`, `validate`, `check-data`, `assess`, `report` and `lock`, no.

**Why does `run --java-only` refuse to start?** BEAST's bundled pure-Java BEAGLE never implemented `getSiteLogLikelihoods`, so `-java` aborts on any document containing `<treeLikelihood>` - which is every document BeastXPy generates. Install native BEAGLE instead. See [native-beagle](native-beagle.md).

**Why does `assess` say `not_computable`?** PSRF and ASDSF need at least two chains. Run more seeds. A missing statistic means the column is absent, not that the value is zero.

**Why did `assess` fail on my short test chain?** Because the chain is short. That is the correct report; a short chain has little ESS and should not pass.

**Is a `pass` verdict a convergence statement?** No. See [limitations](limitations.md).

**Can I use my own XML?** Yes - that is what BEAST is for. `custom_fragments` only supports a small allow-list of inert elements and always forces Gate 3.

**Why is `codon` rejected?** The official BEAST parsers cannot apply `<convert dataType="codon">` on either version. Use `srd06`, or a BEAST build whose parser accepts it.

**Does `compare` work without BEAGLE?** No. It exits 2 and reports no numbers.

**How do I reproduce a run on another machine?** `beastxpy lock` there and `beastxpy reproduce --check` here.

**Which Python versions?** 3.10 and 3.11.

**Can I run it in a container?** Yes: `docker build -f deploy/Dockerfile .`. See [container](container.md).
