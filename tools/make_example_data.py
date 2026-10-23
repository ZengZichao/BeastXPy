#!/usr/bin/env python
"""Generate the example data files for BeastXPy.

Produces ``examples/primates.fasta`` (12 taxa x 898 bp, converted from the
reference BEAST 1.10.4 Primates NEXUS) and ``examples/aminoacid.fasta``
(10 taxa x 234 aa synthetic mitochondrial protein data).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

REF_NEX = (
    ROOT.parent
    / "BeastXPy-参考软件"
    / "beast-mcmc-1.10.4"
    / "examples"
    / "Data"
    / "Primates_two_partition.nex"
)


def make_primates() -> None:
    from Bio import SeqIO

    if not REF_NEX.exists():
        raise FileNotFoundError(f"Reference NEXUS not found: {REF_NEX}")
    recs = list(SeqIO.parse(str(REF_NEX), "nexus"))
    fasta = "".join(f">{rec.id}\n{str(rec.seq).upper()}\n" for rec in recs)
    (EXAMPLES / "primates.fasta").write_text(fasta, encoding="utf-8")
    print(f"primates.fasta: {len(recs)} taxa, {len(recs[0])} sites")


def make_aminoacid() -> None:
    # A synthetic mitochondrial protein alignment (10 taxa x 234 aa).
    # A conserved consensus built deterministically; every taxon differs by a
    # few scattered substitutions so the alignment is informative.
    import random

    rng = random.Random(42)
    aa = "ACDEFGHIKLMNPQRSTVWY"
    consensus = "".join(rng.choice(aa) for _ in range(234))
    seqs = {}
    for i, name in enumerate(
        [
            "Pan_troglodytes",
            "Gorilla_gorilla",
            "Pongo_pygmaeus",
            "Hylobates_lar",
            "Macaca_mulatta",
            "Papio_hamadryas",
            "Saimiri_sciureus",
            "Tarsius_syrichta",
            "Lemur_catta",
            "Mus_musculus",
        ]
    ):
        chars = list(consensus)
        # Introduce ~8 random point substitutions (unique per taxon).
        rng2 = random.Random(1000 + i)
        for _ in range(8):
            pos = rng2.randrange(234)
            chars[pos] = rng2.choice([c for c in aa if c != chars[pos]])
        seqs[name] = "".join(chars)
    for name, seq in seqs.items():
        if len(seq) != 234:
            raise ValueError(f"{name}: length {len(seq)} != 234")
    (EXAMPLES / "aminoacid.fasta").write_text(
        "".join(f">{name}\n{seq}\n" for name, seq in seqs.items()), encoding="utf-8"
    )
    print(f"aminoacid.fasta: {len(seqs)} taxa, 234 sites")


def make_calibrations() -> None:
    cal = """# Shared calibration set for 'quick' generation.
calibrations:
  - name: "Hominoidea"
    taxa: [Pan_troglodytes, Gorilla_gorilla, Pongo_pygmaeus, Hylobates_lar]
    monophyletic: true
    distribution:
      type: lognormal
      parameters: {mean: 1.0, stdev: 0.5}
      offset: 15.0
    provenance:
      source: fossil
      reference: "doi:10.1038/nature10286"
      calibration_type: soft
      original_age_min: 15.0
      original_age_max: 20.0
  - name: "Catarrhini"
    taxa: [Pan_troglodytes, Gorilla_gorilla, Pongo_pygmaeus, Hylobates_lar, Macaca_mulatta, Papio_hamadryas]
    monophyletic: true
    distribution:
      type: lognormal
      parameters: {mean: 1.0, stdev: 0.5}
      offset: 25.0
    provenance:
      source: fossil
      calibration_type: soft
      original_age_min: 25.0
      original_age_max: 30.0
"""
    (EXAMPLES / "calibrations.yaml").write_text(cal, encoding="utf-8")
    print("calibrations.yaml written")


if __name__ == "__main__":
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    make_primates()
    make_aminoacid()
    make_calibrations()
