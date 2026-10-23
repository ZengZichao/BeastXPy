# Custom XML fragments

[English](extensions.md) | [中文](../zh/extensions.md)

`custom_fragments` splices hand-written XML into a generated document. It is **off by default** and is the most dangerous feature here, so it is fenced tightly.

## Step 1 — Write the fragment block

Add this to your config:

```yaml
custom_fragments:
  enabled: true
  fragments:
    - name: root-height-stat
      xml: '<treeHeightStatistic id="rootHeight"> <treeModel idref="treeModel"/> </treeHeightStatistic>'
      anchor_id: speciation
      position: before
```

One fragment is one entry. `xml` takes an inline string; `file` takes a path to a fragment file (use one or the other). The target anchor is addressed by `anchor_id` (an existing element id, e.g. `speciation`) or `anchor_tag` (an element name), and `position` is `before`, `after` or `inside` (`inside` is only legal against `<beast>`).

Either set `enabled: true` in the config or keep it false and pass `--allow-custom-fragments` on the command line. With neither, the block is ignored.

## Step 2 — Generate with fragments allowed

```
beastxpy generate --config demo.yaml --output demo.xml --allow-custom-fragments
```

The spliced document passes through the same gates as any other. Gate 0 rejects a bad fragment with named reasons and a `next step:` line:

```
error: refusing to generate: fragment 'root-height-stat': anchor ambiguous -- 3 elements match tag=<speciationLikelihood>
  next step: An anchor must match exactly one element so the splice is deterministic. Use anchor_id instead of anchor_tag, or pick a unique tag from: 'HKYModel', 'alignment', 'beast', ...
```

Tags such as `speciationLikelihood` also match `<speciationLikelihood idref=...>` references, so they are usually ambiguous - prefer `anchor_id`.

## What the allow-list permits

Fragments may declare taxa and taxon sets, numeric `parameter` elements, read-only statistics (`tmrcaStatistic`, `treeHeightStatistic`, `monophylyStatistic`, `branchLengthStatistic`, `statistic` with `idref` only), views of the existing tree (`treeModel` with `idref` only), and standalone distribution priors (`logNormalPrior`, `normalPrior`, `uniformPrior`, ...). `mrca` and `taxon` build clades; nothing here can replace BeastXPy's own machinery.

## What is refused

* XML that is not well formed
* elements or attributes outside the allow-list
* duplicate ids, or a fragment redefining an existing id
* unresolvable `idref`s
* `<!DOCTYPE>` and entity declarations (no external entities are ever expanded)
* `file:` paths that escape the config directory, and absolute paths
* anything that would reach `mcmc`, the loggers, the operators or another core block

## Gate 3 is mandatory

A spliced document is marked `custom` in the provenance record and **must** pass Gate 3 against the official BEAST jars. If validation fails, generation is refused - and `--allow-unvalidated` does not override this.

## A warning

The allow-list keeps fragments inert: they may declare taxa, parameters, statistics and standalone distributions, but nothing that silently replaces BeastXPy's own machinery. If what you need is not on the list, the honest answer is a BEAST XML of your own, not a fragment.
