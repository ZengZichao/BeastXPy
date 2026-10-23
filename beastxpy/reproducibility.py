"""Reproducibility artefacts for BeastXPy (core innovation).

Provides the three reproducibility products documented in
``docs/en/reproducibility.md``:

1. **Analysis fingerprint** -- ``BXP-{sha256(normalised scientific content)[:12]}-{__version__}``.
   Only fields that actually determine the generated XML enter the hash:
   alignments/partitions, priors, calibrations (provenance *notes* excluded),
   MCMC/logging cadence, the RNG seed (``control.seed``), the target BEAST
   version and whether the fingerprint comment is embedded.  Output *file
   names*, diagnostics settings and other non-science metadata are excluded,
   so renaming a log or changing a diagnostic threshold does not fork the
   analysis.  A version-independent :func:`content_fingerprint` is provided
   for resume checks (upgrading BeastXPy must not invalidate a resumable run).
2. **LaTeX methods paragraph generator** -- a filled-in template covering data,
   models, priors, the calibration table (including DOIs), MCMC length,
   operators, validation results and the fingerprint.  It only claims BEAST
   parser validation that actually ran.
3. **Pipeline generation** -- Snakemake ``Snakefile`` and Nextflow ``main.nf``
   implementing ``prepare -> beast(seed) -> trace -> annotate`` with a
   fingerprint-consistency check step.  ``control.seed`` is a single RNG seed,
   never a replicate count.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .models import BEASTConfig, NON_SCIENCE_METADATA
from .utils import canonical_json, sha256_hex
from .xml_writer import atomic_write_text

FINGERPRINT_PREFIX = "BXP-"

# Metadata keys that are not part of the scientific definition of the
# analysis.  ``tool_version`` is set by the config parser; including it would
# make every BeastXPy upgrade change the analysis fingerprint.
_EXTRA_NON_SCIENCE_METADATA = frozenset({"tool_version", "analysis_name"})


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


def _fingerprint_payload(config: BEASTConfig) -> Dict[str, Any]:
    """Return the XML-deterministic subset of the config used for hashing."""
    payload = config.to_dict()

    # Diagnostics settings influence Gate 2 only (nothing is written to XML).
    payload.pop("diagnostics", None)

    metadata = payload.get("metadata") or {}
    payload["metadata"] = {
        key: value
        for key, value in metadata.items()
        if key not in NON_SCIENCE_METADATA and key not in _EXTRA_NON_SCIENCE_METADATA
    }

    # Logger *file names* are execution artefacts, not model content; the
    # cadence (log_every) is scientific and stays.
    loggers = payload.get("loggers") or {}
    for spec in loggers.values():
        if isinstance(spec, dict):
            for key in list(spec):
                if key in ("file_name", "file", "filename", "path"):
                    spec.pop(key, None)

    # Provenance notes are prose, not part of the model definition.
    calibrations = payload.get("calibrations") or []
    for calibration in calibrations:
        provenance = calibration.get("provenance")
        if isinstance(provenance, dict):
            provenance.pop("notes", None)

    control = payload.setdefault("control", {})
    control["seed"] = config.control.seed

    # Whether the fingerprint comment is embedded changes the XML bytes.
    payload["output_rendering"] = {"embed_fingerprint": bool(config.output.embed_fingerprint)}
    return payload


def analysis_fingerprint(config: BEASTConfig) -> str:
    """Return the analysis fingerprint ``BXP-{hash12}-{tool version}``.

    The scientific content (see :func:`_fingerprint_payload`) is canonicalised
    (sorted JSON) together with the target BEAST version, so editing the
    description, adding an author or re-running the command produces the
    *same* fingerprint while changing the seed or a model parameter produces
    a different one.
    """
    payload = _fingerprint_payload(config)
    digest = sha256_hex(canonical_json(payload))[:12]
    return f"{FINGERPRINT_PREFIX}{digest}-{__version__}"


def content_fingerprint(config: BEASTConfig) -> str:
    """Version-independent fingerprint of the scientific content.

    Used by ``beastxpy run --resume``: upgrading BeastXPy must not make an
    in-progress run unresumable, while changing the seed, model or version
    still does.
    """
    digest = sha256_hex(canonical_json(_fingerprint_payload(config)))[:16]
    return f"{FINGERPRINT_PREFIX}{digest}"


def data_digest(config: BEASTConfig) -> str:
    """Return the alignment-content digest (``sha256[:16]``) of all alignments."""
    import hashlib

    hasher = hashlib.sha256()
    for alignment in sorted(config.all_alignments, key=lambda a: a.id):
        hasher.update(alignment.id.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(alignment.content_digest().encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()[:16]


def fingerprint_comment(config: BEASTConfig) -> str:
    """XML header comment lines embedding the fingerprint and data digest."""
    return (
        f"BXP-fingerprint: {analysis_fingerprint(config)}\n"
        f"BXP-content-fingerprint: {content_fingerprint(config)}\n"
        f"BXP-data-digest: {data_digest(config)}\n"
        f"BXP-beast-version: {config.control.beast_version}"
    )


def write_fingerprint_json(
    config: BEASTConfig,
    output_path: str,
    validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write ``.fingerprint.json`` (side-car with wall-clock timestamps).

    Parent directories are created; the file is written atomically as UTF-8.
    ``validation`` records the real Gate-3 outcome (status per version), never
    a fabricated success.
    """
    payload: Dict[str, Any] = {
        "fingerprint": analysis_fingerprint(config),
        "content_fingerprint": content_fingerprint(config),
        "data_digest": data_digest(config),
        "beast_version": config.control.beast_version,
        "beastxpy_version": __version__,
        "analysis_name": config.analysis_name,
        "control_seed": config.control.seed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if validation is not None:
        payload["validation"] = validation
    atomic_write_text(output_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


# ---------------------------------------------------------------------------
# LaTeX methods
# ---------------------------------------------------------------------------


def _latex_escape(text: Any) -> str:
    """Escape *plain* text for LaTeX; never use on pre-built math markup."""
    return (
        str(text)
        .replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("~", r"\textasciitilde{}")
        .replace("^", r"\textasciicircum{}")
    )


#: natbib citation key -> one-line reference.  Emitted as a LaTeX comment
#: block at the end of the generated methods paragraph so a manuscript can
#: turn every model mention into a real citation.
METHODS_CITATIONS: Dict[str, str] = {
    "drummond2007beast": (
        "Drummond AJ, Rambaut A (2007) BEAST: Bayesian evolutionary analysis "
        "by sampling trees. BMC Evolutionary Biology 7:214. "
        "doi:10.1186/1471-2148-7-214"
    ),
    "drummond2012beast": (
        "Drummond AJ, Suchard MA, Xie D, Rambaut A (2012) Bayesian "
        "phylogenetics with BEAUti and the BEAST 1.7. Molecular Biology and "
        "Evolution 29(8):1969-1973. doi:10.1093/molbev/mss075"
    ),
    "jukes1969": (
        "Jukes TH, Cantor CR (1969) Evolution of protein molecules. In: "
        "Munro HN (ed) Mammalian Protein Metabolism, vol 3. Academic Press, "
        "New York, pp 21-132."
    ),
    "hasegawa1985": (
        "Hasegawa M, Kishino H, Yano T (1985) Dating of the human-ape "
        "splitting by a molecular clock of mitochondrial DNA. Journal of "
        "Molecular Evolution 22(2):160-174. doi:10.1007/BF02101694"
    ),
    "tamura1993": (
        "Tamura K, Nei M (1993) Estimation of the number of nucleotide "
        "substitutions in the control region of mitochondrial DNA in humans "
        "and chimpanzees. Molecular Biology and Evolution 10(3):512-526. "
        "doi:10.1093/oxfordjournals.molbev.a040023"
    ),
    "tavare1986": (
        "Tavare S (1986) Some probabilistic and statistical problems in the "
        "analysis of DNA sequences. Lectures on Mathematics in the Life "
        "Sciences 17:57-86."
    ),
    "jones1992": (
        "Jones DT, Taylor WR, Thornton JM (1992) The rapid generation of "
        "mutation data matrices from protein sequences. Computer "
        "Applications in the Biosciences 8(3):275-282. "
        "doi:10.1093/bioinformatics/8.3.275"
    ),
    "dayhoff1978": (
        "Dayhoff MO, Schwartz RM, Orcutt BC (1978) A model of evolutionary "
        "change in proteins. In: Dayhoff MO (ed) Atlas of Protein Sequence "
        "and Structure, vol 5, suppl 3. National Biomedical Research "
        "Foundation, Washington DC, pp 345-352."
    ),
    "whelan2001": (
        "Whelan S, Goldman N (2001) A general empirical model of protein "
        "evolution derived from multiple protein families using a "
        "maximum-likelihood approach. Molecular Biology and Evolution "
        "18(5):691-693. doi:10.1093/oxfordjournals.molbev.a003851"
    ),
    "henikoff1992": (
        "Henikoff S, Henikoff JG (1992) Amino acid substitution matrices "
        "from protein blocks. Proceedings of the National Academy of "
        "Sciences USA 89(22):10915-10919. doi:10.1073/pnas.89.22.10915"
    ),
    "adachi1996": (
        "Adachi J, Hasegawa M (1996) Model of amino acid substitution in "
        "proteins encoded by mitochondrial DNA. Journal of Molecular "
        "Evolution 42(4):459-468. doi:10.1007/BF02338802"
    ),
    "adachi2000": (
        "Adachi J, Waddell PJ, Martin W, Hasegawa M (2000) Plastid genome "
        "phylogeny and a model of amino acid substitution for proteins "
        "encoded by chloroplast DNA. Journal of Molecular Evolution "
        "50(4):348-358. doi:10.1007/s002399910038"
    ),
    "yang1994": (
        "Yang Z (1994) Maximum likelihood phylogenetic estimation from DNA "
        "sequences with variable rates over sites: approximate methods. "
        "Journal of Molecular Evolution 39(3):306-314. "
        "doi:10.1007/BF00160154"
    ),
    "zuckerkandl1965": (
        "Zuckerkandl E, Pauling L (1965) Evolutionary divergence and "
        "convergence in proteins. In: Bryson V, Vogel HJ (eds) Evolving "
        "Genes and Proteins. Academic Press, New York, pp 97-166."
    ),
    "drummond2006relaxed": (
        "Drummond AJ, Ho SYW, Phillips MJ, Rambaut A (2006) Relaxed "
        "phylogenetics and dating with confidence. PLoS Biology 4(5):e88. "
        "doi:10.1371/journal.pbio.0040088"
    ),
    "drummond2010rlc": (
        "Drummond AJ, Suchard MA (2010) Bayesian random local clocks, or "
        "one rate to rule them all. BMC Biology 8:114. "
        "doi:10.1186/1741-7007-8-114"
    ),
    "yule1925": (
        "Yule GU (1925) A mathematical theory of evolution, based on the "
        "conclusions of Dr. J. C. Willis, F.R.S. Philosophical Transactions "
        "of the Royal Society B 213:21-87. doi:10.1098/rstb.1925.0002"
    ),
    "gernhard2008": (
        "Gernhard T (2008) The conditioned reconstructed process. Journal "
        "of Theoretical Biology 253(4):769-778. "
        "doi:10.1016/j.jtbi.2008.04.005"
    ),
    "kingman1982": (
        "Kingman JFC (1982) The coalescent. Stochastic Processes and their "
        "Applications 13(3):235-248. doi:10.1016/0304-4149(82)90011-4"
    ),
    "drummond2002": (
        "Drummond AJ, Nicholls GK, Rodrigo AG, Solomon W (2002) Estimating "
        "mutation parameters, population history and genealogy "
        "simultaneously from temporally spaced sequence data. Genetics "
        "161(3):1307-1320. doi:10.1093/genetics/161.3.1307"
    ),
    "drummond2005skyline": (
        "Drummond AJ, Rambaut A, Shapiro B, Pybus OG (2005) Bayesian "
        "coalescent inference of past population dynamics from molecular "
        "sequences. Molecular Biology and Evolution 22(5):1185-1192. "
        "doi:10.1093/molbev/msi103"
    ),
    "heled2008ebsp": (
        "Heled J, Drummond AJ (2008) Bayesian inference of population size "
        "history from multiple loci. BMC Evolutionary Biology 8:289. "
        "doi:10.1186/1471-2148-8-289"
    ),
    "heled2010starbeast": (
        "Heled J, Drummond AJ (2010) Bayesian inference of species trees "
        "from multilocus data. Molecular Biology and Evolution 27(3):570-580. "
        "doi:10.1093/molbev/msp274"
    ),
    "ho2009calibration": (
        "Ho SYW, Phillips MJ (2009) Accounting for calibration uncertainty "
        "in phylogenetic estimation of evolutionary divergence times. "
        "Systematic Biology 58(3):367-380. doi:10.1093/sysbio/syp035"
    ),
}

#: Substitution model type -> citation key (see registry names).
_SUBST_CITATION_KEYS: Dict[str, str] = {
    "jc69": "jukes1969",
    "hky": "hasegawa1985",
    "tn93": "tamura1993",
    "gtr": "tavare1986",
    "wag": "whelan2001",
    "jtt": "jones1992",
    "dayhoff": "dayhoff1978",
    "blosum62": "henikoff1992",
    "mtrev": "adachi1996",
    "cprev": "adachi2000",
}

#: Clock model type -> citation key.
_CLOCK_CITATION_KEYS: Dict[str, str] = {
    "strict": "zuckerkandl1965",
    "ucln": "drummond2006relaxed",
    "uce": "drummond2006relaxed",
    "rlc": "drummond2010rlc",
}

#: Tree prior type -> comma-joined citation keys.
_TREE_PRIOR_CITATION_KEYS: Dict[str, str] = {
    "yule": "yule1925",
    "birth_death": "gernhard2008",
    "coalescent_constant": "kingman1982",
    "coalescent_exponential": "kingman1982,drummond2002",
    "coalescent_logistic": "kingman1982,drummond2002",
    "coalescent_expansion": "kingman1982,drummond2002",
    "skyline": "drummond2005skyline",
    "ebsp": "heled2008ebsp",
}


def generate_methods_latex(
    config: BEASTConfig,
    validated: bool = False,
    validation_versions: Optional[List[str]] = None,
    validation_status: Optional[str] = None,
) -> str:
    """Render the methodology paragraph as LaTeX.

    ``validated`` must only be true when Gate 3 actually ran and passed for
    every requested version; ``validation_versions`` lists those versions.
    When ``validated`` is false the paragraph explicitly states that BEAST
    parser validation was *not* performed (or that it failed, when
    ``validation_status == "failed"``) so the methods section never overstates
    what was checked.
    """
    parts: List[str] = []
    parts.append("%% Generated by BeastXPy %s -- do not edit by hand" % __version__)
    parts.append(r"\subsection*{Phylogenetic analyses}")
    parts.append("")

    # Data description
    alignment_descs = []
    for p in config.partitions:
        alignment_descs.append(
            f"{p.alignment.n_taxa} taxa and {p.alignment.n_sites} aligned sites "
            f"({_latex_escape(p.alignment.data_type.value)}; partition "
            f"``{_latex_escape(p.id)}``)"
        )
    parts.append(
        "We analysed %s. " % (" and ".join(alignment_descs))
        if alignment_descs
        else "We analysed sequence data. "
    )

    # Models: escape the plain model name, then append LaTeX math markup and
    # a \citep for the primary description of every named model.
    model_descs: List[str] = []
    cited: set = set()
    for p in config.partitions:
        sm = p.site_model.substitution_model
        subst_type = sm.get("type", "hky")
        base = _latex_escape(subst_type)
        subst_key = _SUBST_CITATION_KEYS.get(subst_type)
        if subst_key:
            base += rf" \citep{{{subst_key}}}"
            cited.add(subst_key)
        maths = ""
        gamma = p.site_model.gamma_categories
        if gamma > 1:
            maths += rf"$+\Gamma_{{{gamma}}}$"
            cited.add("yang1994")
        if p.site_model.proportion_invariant is not None:
            maths += r"$+I$"
        clock = str(p.clock_model.type.value)
        clock_text = _latex_escape(clock)
        clock_key = _CLOCK_CITATION_KEYS.get(clock)
        if clock_key:
            clock_text += rf" \citep{{{clock_key}}}"
            cited.add(clock_key)
        model_descs.append(f"a {base}{maths} substitution model with a {clock_text} clock")
    parts.append(
        "Substitution and clock models were specified as %s. " % " and ".join(model_descs)
        if model_descs
        else ""
    )

    # Tree prior
    tp_names = {
        "yule": "Yule",
        "birth_death": "birth--death",
        "coalescent_constant": "constant-size coalescent",
        "coalescent_exponential": "exponential-growth coalescent",
        "coalescent_logistic": "logistic-growth coalescent",
        "coalescent_expansion": "expansive coalescent",
        "skyline": "Bayesian skyline",
        "ebsp": "extended Bayesian skyline plot",
    }
    tp_name = tp_names.get(config.tree_prior.type.value, config.tree_prior.type.value)
    prior_text = _latex_escape(tp_name)
    prior_keys = _TREE_PRIOR_CITATION_KEYS.get(config.tree_prior.type.value)
    if prior_keys:
        cited.update(prior_keys.split(","))
        prior_text += " \\citep{%s}" % prior_keys
    parts.append(f"A {prior_text} tree prior was assumed. ")

    # Calibration table
    if config.calibrations:
        cited.update({"drummond2006relaxed", "ho2009calibration"})
        parts.append(
            "Node calibrations were assigned as standard calibration "
            "densities \\citep{drummond2006relaxed, ho2009calibration}: "
        )
        parts.append(r"\begin{center}\small")
        parts.append(r"\begin{tabular}{llll}")
        parts.append(r"\toprule")
        parts.append("Calibration & Distribution & Parameters & Source \\\\")
        parts.append(r"\midrule")
        for cal in config.calibrations:
            dist = cal.distribution
            if dist is None:
                continue
            param_repr = ", ".join(
                f"{_latex_escape(k)}={_latex_escape(v)}" for k, v in sorted(dist.parameters.items())
            )
            prov = cal.provenance
            source = (
                _latex_escape(prov.reference)
                if prov and prov.reference
                else _latex_escape(prov.source if prov else "")
            )
            parts.append(
                f"{_latex_escape(cal.name)} & {_latex_escape(dist.type)} & "
                f"{param_repr} & {source} \\\\"
            )
        parts.append(r"\bottomrule")
        parts.append(r"\end{tabular}")
        parts.append(r"\end{center}")
        parts.append("")

    # MCMC description
    log_every = int(config.loggers.trace_log.get("log_every") or 1)
    cited.add("drummond2007beast")
    parts.append(
        "MCMC analyses were run for %d states, sampling every %d states "
        "(%d samples) with an automatically adapted operator scheme "
        "\\citep{drummond2007beast}. "
        % (
            config.mcmc.chain_length,
            log_every,
            max(1, config.mcmc.chain_length // log_every),
        )
    )

    # Tool and target-version statement
    cited.update({"drummond2007beast", "drummond2012beast"})
    beast_target = str(getattr(getattr(config, "control", None), "beast_version", "") or "1.10.4")
    if beast_target == "both":
        target_text = "BEAST v1.10.4 and BEAST X v10.5.0"
    else:
        target_text = "BEAST v%s" % beast_target
    parts.append(
        "Analyses were configured with BeastXPy v%s and targeted %s "
        "\\citep{drummond2007beast, drummond2012beast}. "
        % (__version__, _latex_escape(target_text))
    )

    # Validation -- only describe checks that really ran.
    versions = list(validation_versions or [])
    if validated and versions:
        version_text = " and ".join(versions)
        parts.append(
            "All XML input files passed the BeastXPy validation pipeline "
            "(configuration-, structure- and calibration-gates, plus BEAST "
            f"{_latex_escape(version_text)} parser instantiation). "
        )
    elif validated:
        parts.append(
            "All XML input files passed the BeastXPy validation pipeline "
            "(configuration-, structure- and calibration-gates, plus BEAST "
            "parser instantiation). "
        )
    elif validation_status == "failed":
        parts.append(
            "XML input files passed the BeastXPy configuration, structure and "
            "calibration gates; BEAST parser instantiation was attempted and "
            "failed, so no parser-level validation is claimed. "
        )
    else:
        parts.append(
            "XML input files passed the BeastXPy configuration, structure and "
            "calibration gates; BEAST parser validation was not performed. "
        )

    # Fingerprint
    parts.append(
        "The analysis fingerprint is \\texttt{%s} (data digest "
        "\\texttt{%s})." % (analysis_fingerprint(config), data_digest(config))
    )

    # Citations actually used, as a LaTeX comment the manuscript can copy.
    if cited:
        parts.append("")
        parts.append(
            "%% Citations used above (natbib keys; add matching entries to " "your bibliography):"
        )
        for key in sorted(cited):
            parts.append(f"%%   {key} -- {METHODS_CITATIONS.get(key, '')}")
    parts.append("")
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


def _seed_list(config: BEASTConfig) -> List[int]:
    """The single RNG seed used by the pipeline (never a replicate count)."""
    seed = config.control.seed
    return [int(seed)] if isinstance(seed, int) and not isinstance(seed, bool) else [1]


def _config_filename(config: BEASTConfig, config_filename: Optional[str]) -> str:
    if config_filename:
        return Path(config_filename).name
    return f"{config.analysis_name}.yaml"


def generate_snakemake(
    config: BEASTConfig,
    output_path: str,
    config_filename: Optional[str] = None,
) -> str:
    """Generate a Snakemake ``Snakefile`` implementing the E2E pipeline."""
    seeds = _seed_list(config)
    name = config.analysis_name
    fingerprint = analysis_fingerprint(config)
    config_name = _config_filename(config, config_filename)
    # These land in *Python* assignments, not shell text: shlex.quote leaves
    # simple names unquoted (CONFIG = config_basic.yaml -> NameError under
    # snakemake).  repr() produces a valid Python string literal.
    q_config = repr(config_name)
    q_outdir = repr("results")
    text = f"""# Snakefile generated by BeastXPy {__version__}
# Analysis fingerprint: {fingerprint}
# Steps: prepare -> beast -> trace -> annotate (+ fingerprint check)
# control.seed is a single RNG seed (not a replicate count); add replicates
# by editing SEEDS.

SEEDS = [{", ".join(str(s) for s in seeds)}]
CONFIG = {q_config}
OUTDIR = {q_outdir}
NAME = "{name}"

rule all:
    input:
        expand(OUTDIR + "/{{seed}}/seed_{{seed}}_tree.log", seed=SEEDS),
        expand(OUTDIR + "/{{seed}}/seed_{{seed}}_trace.log", seed=SEEDS),
        OUTDIR + "/mcc.tre",
        OUTDIR + "/trace_summary.json",
        OUTDIR + "/prepared/{name}.fingerprint.ok",

rule prepare:
    output: OUTDIR + "/prepared/{name}.xml"
    log: OUTDIR + "/logs/prepare.log"
    shell:
        "beastxpy generate --config {{CONFIG:q}} --output {{output:q}} --force "
        "> /dev/null 2> {{log:q}}"

rule fingerprint_check:
    input: OUTDIR + "/prepared/{name}.xml"
    output: OUTDIR + "/prepared/{name}.fingerprint.ok"
    run:
        from pathlib import Path
        text = Path(input[0]).read_text(encoding="utf-8")
        expected = "{fingerprint}"
        assert expected in text, f"fingerprint mismatch in {{input[0]}}: expected {{expected}}"
        Path(output[0]).write_text(expected + "\\n", encoding="utf-8")

rule beast:
    input: OUTDIR + "/prepared/{name}.xml"
    output:
        OUTDIR + "/{{seed}}/seed_{{seed}}_tree.log",
        OUTDIR + "/{{seed}}/seed_{{seed}}_trace.log"
    log: OUTDIR + "/logs/beast_{{seed}}.log"
    shell:
        "beastxpy run --config {{CONFIG:q}} --workdir {{OUTDIR:q}} "
        "--seeds {{wildcards.seed}} --force > /dev/null 2> {{log:q}}"

rule trace:
    input: OUTDIR + "/{{seed}}/seed_{{seed}}_trace.log"
    output: OUTDIR + "/{{seed}}/trace.json"
    log: OUTDIR + "/logs/trace_{{seed}}.log"
    shell:
        "beastxpy trace --log {{input:q}} --json > {{output:q}} 2> {{log:q}}"

rule summary:
    input: expand(OUTDIR + "/{{seed}}/trace.json", seed=SEEDS)
    output: OUTDIR + "/trace_summary.json"
    run:
        import json
        from pathlib import Path
        data = [json.loads(Path(f).read_text(encoding="utf-8")) for f in input]
        Path(output[0]).write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")

rule annotate:
    input: expand(OUTDIR + "/{{seed}}/seed_{{seed}}_tree.log", seed=SEEDS)
    output: OUTDIR + "/mcc.tre"
    params: combined=OUTDIR + "/combined.trees"
    log: OUTDIR + "/logs/annotate.log"
    shell:
        "beastxpy logcombine --logs {{input:q}} --output {{params.combined:q}} && "
        "beastxpy tree-annotate --trees {{params.combined:q}} --output {{output:q}} "
        "--heights mean 2> {{log:q}}"
"""
    atomic_write_text(output_path, text)
    return text


def generate_nextflow(
    config: BEASTConfig,
    output_path: str,
    config_filename: Optional[str] = None,
) -> str:
    """Generate a Nextflow ``main.nf`` implementing the E2E pipeline."""
    seeds = _seed_list(config)
    name = config.analysis_name
    fingerprint = analysis_fingerprint(config)
    config_name = _config_filename(config, config_filename)
    nf_config = config_name.replace("\\", "\\\\").replace('"', '\\"')
    seed_literal = "[{}]".format(", ".join(str(s) for s in seeds))
    text = f"""// main.nf generated by BeastXPy {__version__}
// Analysis fingerprint: {fingerprint}
// Steps: prepare -> beast (one task per seed) -> trace -> annotate
// control.seed is a single RNG seed; params.seeds lists the RNG seed(s).

nextflow.enable.dsl = 2

params.config = "{nf_config}"
params.seeds = {seed_literal}
params.outdir = "results"

process PREPARE {{
  publishDir params.outdir, mode: 'copy'
  output:
    path("prepared/{name}.xml"), emit: xml
  script:
    \"""
    beastxpy generate --config "${{params.config}}" --output prepared/{name}.xml --force > /dev/null
    \"""
}}

process BEAST {{
  publishDir params.outdir, mode: 'copy'
  input:
    path xml
    val seed
  output:
    path("${{seed}}/seed_${{seed}}_trace.log"), emit: log
    path("${{seed}}/seed_${{seed}}_tree.log"), emit: trees
  script:
    \"""
    beastxpy run --config "${{params.config}}" --workdir . --seeds ${{seed}} --force > /dev/null
    \"""
}}

process TRACE {{
  publishDir params.outdir, mode: 'copy'
  input:
    path trace_log
  output:
    path("trace.json"), emit: summary
  script:
    \"""
    beastxpy trace --log "${{trace_log}}" --json > trace.json
    \"""
}}

process ANNOTATE {{
  publishDir params.outdir, mode: 'copy'
  input:
    path trees, stageAs: 'trees/*'
  output:
    path("mcc.tre")
  script:
    \"""
    beastxpy logcombine --logs trees/*.trees --output combined.trees
    beastxpy tree-annotate --trees combined.trees --output mcc.tre --heights mean
    \"""
}}

workflow {{
  PREPARE()
  BEAST(PREPARE.out.xml.first(), Channel.fromList(params.seeds))
  TRACE(BEAST.out.log)
  ANNOTATE(BEAST.out.trees.collect())
}}
"""
    atomic_write_text(output_path, text)
    return text


def generate_pipeline(
    config: BEASTConfig,
    pipeline_type: str,
    output_path: str,
    config_filename: Optional[str] = None,
) -> str:
    """Generate a pipeline file (``snakemake`` or ``nextflow``)."""
    if pipeline_type == "snakemake":
        return generate_snakemake(config, output_path, config_filename)
    if pipeline_type == "nextflow":
        return generate_nextflow(config, output_path, config_filename)
    raise ValueError(f"Unknown pipeline type {pipeline_type!r} (valid: snakemake, nextflow)")


def validate_pipeline_text(pipeline_type: str, text: str) -> List[str]:
    """Static sanity checks for a generated pipeline (no external tools).

    Returns a list of problems (empty when the basic structural contract
    holds).  These checks do not replace ``snakemake -n`` / ``nextflow lint``;
    they catch the historical template bugs (Python ``input`` interpolation,
    treating ``control.seed`` as a replicate count, invalid Nextflow syntax).
    """
    problems: List[str] = []
    if "<built-in function input>" in text:
        problems.append("Python builtin 'input' was interpolated into the pipeline")
    if "range(1, " in text and "SEEDS" in text:
        problems.append("seed list is a range (control.seed treated as replicate count)")
    if pipeline_type == "snakemake":
        if "SEEDS = [" not in text:
            problems.append("Snakemake: SEEDS is not a literal list")
        if "rule beast" not in text:
            problems.append("Snakemake: missing 'rule beast'")
        if "seed_{seed}_trace.log" not in text:
            problems.append("Snakemake: outputs do not match the runner contract")
    elif pipeline_type == "nextflow":
        if "nextflow.enable.dsl = 2" not in text:
            problems.append("Nextflow: DSL2 not enabled")
        if "path(*" in text:
            problems.append("Nextflow: invalid path(*.ext .collect()) syntax")
        if "params.seeds = [" not in text:
            problems.append("Nextflow: params.seeds is not a list literal")
        if "PREPARE.out.xml.first()" not in text:
            problems.append("Nextflow: PREPARE output is not broadcast per seed")
        if text.count("{") != text.count("}"):
            problems.append("Nextflow: unbalanced braces")
    else:
        problems.append(f"unknown pipeline type {pipeline_type!r}")
    return problems


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def fingerprint_payload(config: BEASTConfig) -> Dict[str, Any]:
    """The machine-readable fingerprint payload (without wall-clock time)."""
    return {
        "fingerprint": analysis_fingerprint(config),
        "content_fingerprint": content_fingerprint(config),
        "data_digest": data_digest(config),
        "beast_version": config.control.beast_version,
        "beastxpy_version": __version__,
    }
