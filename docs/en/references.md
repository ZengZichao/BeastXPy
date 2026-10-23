# References

[English](references.md) | [中文](../zh/references.md)

Primary literature for the statistics, thresholds and models BeastXPy uses. The `beastxpy methods` command embeds the matching `\\citep` keys for your analysis into its LaTeX paragraph. DOIs were transcribed carefully but should be verified against the publisher before camera-ready submission.

## Software

* **BEAST 1** - Drummond AJ, Rambaut A (2007) BEAST: Bayesian evolutionary analysis by sampling trees. *BMC Evolutionary Biology* 7:214. doi:[10.1186/1471-2148-7-214](https://doi.org/10.1186/1471-2148-7-214)
* **BEAST 1.7+** - Drummond AJ, Suchard MA, Xie D, Rambaut A (2012) Bayesian phylogenetics with BEAUti and the BEAST 1.7. *Molecular Biology and Evolution* 29(8):1969-1973. doi:[10.1093/molbev/mss075](https://doi.org/10.1093/molbev/mss075)
* **BEAST X** - continued BEAST 1 lineage; [github.com/beast-dev/beast-mcmc](https://github.com/beast-dev/beast-mcmc)
* **BEAGLE** - Ayres DL, Darling A, Zwickl DJ, Beerli P, Holder MT, Lewis PO, Huelsenbeck JP, Ronquist F, Swofford DL, Cummings MP, Rambaut A, Suchard MA (2012) BEAGLE: an application programming interface and high-performance library for maximum likelihood phylogenetic inference. *Systematic Biology* 61(1):170-173. doi:[10.1093/sysbio/syr100](https://doi.org/10.1093/sysbio/syr100)
* **Tracer** - Rambaut A, Drummond AJ, Xie D, Baele G, Suchard MA (2018) Posterior summarization in Bayesian phylogenetics using Tracer 1.7. *Systematic Biology* 67(5):901-904. doi:[10.1093/sysbio/syy032](https://doi.org/10.1093/sysbio/syy032)
* **babette** - Bilderbeek RJC, Etienne RS (2018) babette: BEAUti 2, BEAST 2 and Tracer for R. *Methods in Ecology and Evolution* 9(9):2034-2040. doi:[10.1111/2041-210X.13032](https://doi.org/10.1111/2041-210X.13032)
* **RevBayes** - Höhna S, Landis MJ, Heath TA, Boussau B, Lartillot N, Moore BR, Huelsenbeck JP, Ronquist F (2016) RevBayes: Bayesian phylogenetic inference using graphical models and an interactive model-specification language. *Systematic Biology* 65(4):726-736. doi:[10.1093/sysbio/syw021](https://doi.org/10.1093/sysbio/syw021)
* **BEASTifier** - Joseph WB, BEASTifier: generate BEAST XML files for simulation experiments; [github.com/josephwb/BEASTifier](https://github.com/josephwb/BEASTifier)
* **BEAST book** - Drummond AJ, Bouckaert RR (2015) *Bayesian Evolutionary Analysis with BEAST*. Cambridge University Press. doi:[10.1017/CBO9781139095112](https://doi.org/10.1017/CBO9781139095112)

## MCMC diagnostics

* **ESS / ACT** - Geyer CJ (1992) Practical Markov chain Monte Carlo. *Statistical Science* 7(4):473-483. doi:[10.1214/ss/1177011137](https://doi.org/10.1214/ss/1177011137) (initial positive sequence and initial monotone sequence estimators of the integrated autocorrelation time, as implemented in `beastxpy trace`).
* **Burn-in** - Geweke J (1992) Evaluating the accuracy of sampling-based approaches to the calculation of posterior moments. In: Bernardo JM, Berger JO, Dawid AP, Smith AFM (eds) *Bayesian Statistics 4*. Oxford University Press, pp 169-193. (basis of the automatic burn-in probe).
* **PSRF / R-hat** - Gelman A, Rubin DB (1992) Inference from iterative simulation using multiple sequences. *Statistical Science* 7(4):457-472. doi:[10.1214/ss/1177011136](https://doi.org/10.1214/ss/1177011136) (split-chain variant: Gelman A et al. (2013) *Bayesian Data Analysis*, 3rd ed., CRC Press).
* **Rank-normalized R-hat** - Vehtari A, Gelman A, Simpson D, Carpenter B, Bürkner P-C (2021) Rank-normalization, folding, and localization: an improved R-hat for assessing convergence of MCMC. *Bayesian Analysis* 16(2):667-718. doi:[10.1214/20-BA1221](https://doi.org/10.1214/20-BA1221) (`assess` reports rank-normalized split R-hat plus bulk and tail ESS; the classic split-chain R-hat remains available for comparison).
* **ASDSF** - the average standard deviation of split frequencies popularized by MrBayes: Ronquist F, Huelsenbeck JP (2003) MrBayes 3: Bayesian phylogenetic inference under mixed models. *Bioinformatics* 19(12):1572-1574. doi:[10.1093/bioinformatics/btg180](https://doi.org/10.1093/bioinformatics/btg180)
* **Threshold conventions** - ESS > 200 and PSRF < 1.01 follow the guidance in the BEAST book (Drummond & Bouckaert 2015); the ASDSF warn threshold 0.05 follows MrBayes practice (Ronquist & Huelsenbeck 2003). They are defaults, not laws - every one is adjustable on the `assess` command line.

## Model comparison

* **Bayes factors** - Kass RE, Raftery AE (1995) Bayes factors. *Journal of the American Statistical Association* 90(430):773-795. doi:[10.1080/01621459.1995.10476572](https://doi.org/10.1080/01621459.1995.10476572)
* **Stepping stone** - Xie W, Lewis PO, Fan Y, Kuo L, Chen M-H (2011) Improving marginal likelihood estimation for Bayesian phylogenetic model selection. *Systematic Biology* 60(2):150-160. doi:[10.1093/sysbio/syq085](https://doi.org/10.1093/sysbio/syq085)
* **Path sampling / stepping stone in BEAST** - Baele G, Lemey P, Bedford T, Rambaut A, Suchard MA, Alekseyenko AV (2013) Improving the accuracy of demographic and molecular clock model comparison while accommodating model uncertainty. *Molecular Biology and Evolution* 30(2):239-243. doi:[10.1093/molbev/mss243](https://doi.org/10.1093/molbev/mss243)

## Substitution models

* **JC69** - Jukes TH, Cantor CR (1969) Evolution of protein molecules. In: Munro HN (ed) *Mammalian Protein Metabolism*, vol 3. Academic Press, New York, pp 21-132.
* **HKY** - Hasegawa M, Kishino H, Yano T (1985) Dating of the human-ape splitting by a molecular clock of mitochondrial DNA. *Journal of Molecular Evolution* 22(2):160-174. doi:[10.1007/BF02101694](https://doi.org/10.1007/BF02101694)
* **TN93** - Tamura K, Nei M (1993) Estimation of the number of nucleotide substitutions in the control region of mitochondrial DNA in humans and chimpanzees. *Molecular Biology and Evolution* 10(3):512-526. doi:[10.1093/oxfordjournals.molbev.a040023](https://doi.org/10.1093/oxfordjournals.molbev.a040023)
* **GTR** - Tavaré S (1986) Some probabilistic and statistical problems in the analysis of DNA sequences. *Lectures on Mathematics in the Life Sciences* 17:57-86.
* **Gamma rate heterogeneity** - Yang Z (1994) Maximum likelihood phylogenetic estimation from DNA sequences with variable rates over sites: approximate methods. *Journal of Molecular Evolution* 39(3):306-314. doi:[10.1007/BF00160154](https://doi.org/10.1007/BF00160154)
* **JTT** - Jones DT, Taylor WR, Thornton JM (1992) The rapid generation of mutation data matrices from protein sequences. *Computer Applications in the Biosciences* 8(3):275-282. doi:[10.1093/bioinformatics/8.3.275](https://doi.org/10.1093/bioinformatics/8.3.275)
* **WAG** - Whelan S, Goldman N (2001) A general empirical model of protein evolution derived from multiple protein families using a maximum-likelihood approach. *Molecular Biology and Evolution* 18(5):691-693. doi:[10.1093/oxfordjournals.molbev.a003851](https://doi.org/10.1093/oxfordjournals.molbev.a003851)
* **BLOSUM62** - Henikoff S, Henikoff JG (1992) Amino acid substitution matrices from protein blocks. *PNAS* 89(22):10915-10919. doi:[10.1073/pnas.89.22.10915](https://doi.org/10.1073/pnas.89.22.10915)
* **Dayhoff** - Dayhoff MO, Schwartz RM, Orcutt BC (1978) A model of evolutionary change in proteins. In: *Atlas of Protein Sequence and Structure*, vol 5, suppl 3. National Biomedical Research Foundation, Washington DC, pp 345-352.
* **MTREV** - Adachi J, Hasegawa M (1996) Model of amino acid substitution in proteins encoded by mitochondrial DNA. *Journal of Molecular Evolution* 42(4):459-468. doi:[10.1007/BF02338802](https://doi.org/10.1007/BF02338802)
* **CPREV** - Adachi J, Waddell PJ, Martin W, Hasegawa M (2000) Plastid genome phylogeny and a model of amino acid substitution for proteins encoded by chloroplast DNA. *Journal of Molecular Evolution* 50(4):348-358. doi:[10.1007/s002399910038](https://doi.org/10.1007/s002399910038)
* **SRD06** - Shapiro B, Rambaut A, Drummond AJ (2006) Choosing appropriate substitution models for the phylogenetic analysis of protein-coding sequences. *Molecular Biology and Evolution* 23(1):7-9. doi:[10.1093/molbev/msj021](https://doi.org/10.1093/molbev/msj021)

## Clocks

* **Molecular clock** - Zuckerkandl E, Pauling L (1965) Evolutionary divergence and convergence in proteins. In: Bryson V, Vogel HJ (eds) *Evolving Genes and Proteins*. Academic Press, New York, pp 97-166.
* **Relaxed clocks (ucln, uce)** - Drummond AJ, Ho SYW, Phillips MJ, Rambaut A (2006) Relaxed phylogenetics and dating with confidence. *PLoS Biology* 4(5):e88. doi:[10.1371/journal.pbio.0040088](https://doi.org/10.1371/journal.pbio.0040088)
* **Random local clock (rlc)** - Drummond AJ, Suchard MA (2010) Bayesian random local clocks, or one rate to rule them all. *BMC Biology* 8:114. doi:[10.1186/1741-7007-8-114](https://doi.org/10.1186/1741-7007-8-114)

## Tree priors and calibrations

* **Yule** - Yule GU (1925) A mathematical theory of evolution, based on the conclusions of Dr. J. C. Willis, F.R.S. *Philosophical Transactions of the Royal Society B* 213:21-87. doi:[10.1098/rstb.1925.0002](https://doi.org/10.1098/rstb.1925.0002)
* **Birth-death** - Gernhard T (2008) The conditioned reconstructed process. *Journal of Theoretical Biology* 253(4):769-778. doi:[10.1016/j.jtbi.2008.04.005](https://doi.org/10.1016/j.jtbi.2008.04.005)
* **Coalescent** - Kingman JFC (1982) The coalescent. *Stochastic Processes and their Applications* 13(3):235-248. doi:[10.1016/0304-4149(82)90011-4](https://doi.org/10.1016/0304-4149(82)90011-4)
* **Serially sampled / exponential coalescent** - Drummond AJ, Nicholls GK, Rodrigo AG, Solomon W (2002) Estimating mutation parameters, population history and genealogy simultaneously from temporally spaced sequence data. *Genetics* 161(3):1307-1320. doi:[10.1093/genetics/161.3.1307](https://doi.org/10.1093/genetics/161.3.1307)
* **Bayesian skyline** - Drummond AJ, Rambaut A, Shapiro B, Pybus OG (2005) Bayesian coalescent inference of past population dynamics from molecular sequences. *Molecular Biology and Evolution* 22(5):1185-1192. doi:[10.1093/molbev/msi103](https://doi.org/10.1093/molbev/msi103)
* **EBSP** - Heled J, Drummond AJ (2008) Bayesian inference of population size history from multiple loci. *BMC Evolutionary Biology* 8:289. doi:[10.1186/1471-2148-8-289](https://doi.org/10.1186/1471-2148-8-289)
* **Bayesian skyride** - Minin VN, Bloomquist EW, Suchard MA (2008) Smooth skyride through a rough skyline: Bayesian coalescent-based inference of population dynamics. *Molecular Biology and Evolution* 25(7):1459-1471. doi:[10.1093/molbev/msn090](https://doi.org/10.1093/molbev/msn090)
* **BDSS / sampled-ancestor trees** - Stadler T (2010) Sampling-through-time in birth-death trees. *Journal of Theoretical Biology* 267(3):396-404. doi:[10.1016/j.jtbi.2010.09.010](https://doi.org/10.1016/j.jtbi.2010.09.010); Gavryushkina A, Welch D, Stadler T, Drummond AJ (2014) Bayesian inference of sampled ancestor trees for epidemiology and fossil calibration. *PLoS Computational Biology* 10(12):e1003919. doi:[10.1371/journal.pcbi.1003919](https://doi.org/10.1371/journal.pcbi.1003919)
* **Species trees (*BEAST / starBEAST)** - Heled J, Drummond AJ (2010) Bayesian inference of species trees from multilocus data. *Molecular Biology and Evolution* 27(3):570-580. doi:[10.1093/molbev/msp274](https://doi.org/10.1093/molbev/msp274)
* **Calibration uncertainty (soft vs hard bounds)** - Ho SYW, Phillips MJ (2009) Accounting for calibration uncertainty in phylogenetic estimation of evolutionary divergence times. *Systematic Biology* 58(3):367-380. doi:[10.1093/sysbio/syp035](https://doi.org/10.1093/sysbio/syp035)
* **Common-ancestor summary tree (`--heights ca`)** - Heled J, Bouckaert RR (2013) Looking for trees in the forest: summary tree from posterior samples. *BMC Evolutionary Biology* 13:221. doi:[10.1186/1471-2148-13-221](https://doi.org/10.1186/1471-2148-13-221)

## Trait and phylogeographic models

* **Discrete phylogeography** - Lemey P, Rambaut A, Drummond AJ, Suchard MA (2009) Bayesian phylogeography finds its roots. *PLoS Computational Biology* 5(9):e1000520. doi:[10.1371/journal.pcbi.1000520](https://doi.org/10.1371/journal.pcbi.1000520)
* **Continuous traits / relaxed random walk** - Lemey P, Rambaut A, Welch JJ, Suchard MA (2010) Phylogeography takes a relaxed random walk in continuous space and time. *Molecular Biology and Evolution* 27(8):1877-1885. doi:[10.1093/molbev/msq067](https://doi.org/10.1093/molbev/msq067)
* **Dollo / mutation-death model** - Ronquist F (2004) Bayesian inference of character evolution. *Trends in Ecology & Evolution* 19(9):475-481. doi:[10.1016/j.tree.2004.07.002](https://doi.org/10.1016/j.tree.2004.07.002)
