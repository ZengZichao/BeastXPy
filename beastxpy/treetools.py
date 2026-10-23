"""Tree post-processing tools for BeastXPy (tree-annotate / logcombine).

Two execution paths:

* **Java authoritative path** (default): calls the TreeAnnotator and
  LogCombiner main classes shipped inside ``beast-<ver>.jar`` headlessly.
  BEAST X 10.5.0 moved both into subpackages
  (``dr.app.tools.treeannotator.TreeAnnotator`` /
  ``dr.app.tools.logcombiner.LogCombiner``); 1.10.4 uses
  ``dr.app.tools.TreeAnnotator`` / ``dr.app.tools.LogCombiner``.
  (LogCombiner takes input files as positional arguments; the *last* one is
  the output file -- no ``-o`` option; verified against 1.10.4 sources.)

  ``-burninTrees`` is used deliberately: it counts **trees**, which is the
  same unit as the pure-Python path's ``burnin`` argument.  TreeAnnotator's
  ``-burnin`` counts MCMC *states* and would silently discard a different
  number of trees.

* **Pure-Python fallback** (``--python-only``, or when no Java/jar is
  available): parses NEXUS tree logs (translation table + Newick trees) or a
  plain Newick file, computes clade posterior support and node-age
  mean / median / shortest 95% HPD, and builds a maximum-clade-credibility
  (MCC) summary tree annotated with support, heights and branch lengths.

  The fallback is only used when Java is genuinely unavailable (or
  ``python_only=True``); Java failures are propagated with their stderr.  The
  fallback does not replace the authoritative TreeAnnotator; reports the
  algorithm used.

Burn-in units by command (also documented in ``--help``):

* ``tree-annotate``: **trees** (``-burninTrees`` on the Java path).
* ``logcombine``: the Python fallback treats ``burnin`` as **rows** of the
  log; the Java path passes it to LogCombiner's ``-burnin``, which counts
  **states**.  Use ``python_only=True`` for row-based burn-in.
"""

from __future__ import annotations

import math
import re
import subprocess
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import numpy as np

from .utils import hpd_interval
from .validator import find_java, lookup_jar


class TreeToolsError(ValueError):
    """Raised when a tree/log processing step fails."""


# ---------------------------------------------------------------------------
# Shared NEXUS tree parsing
# ---------------------------------------------------------------------------


@dataclass
class NexusTree:
    """One tree (Newick string) with its translation map and metadata."""

    newick: str
    translation: Dict[str, str]
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def labels(self) -> Dict[str, str]:
        """Map clade sets to 'name' for annotation output."""
        return self.translation


def _strip_wrapping_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


def _parse_translate_block(lines: List[str], start: int) -> Tuple[Dict[str, str], int]:
    """Parse a ``Translate`` block starting at ``start`` (the ``Translate`` line)."""
    translation: Dict[str, str] = {}
    index = start + 1
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue
        if line.startswith(";"):
            break
        ends_block = line.rstrip().endswith(";")
        entry = line.rstrip(",;").strip()
        match = re.match(r"^(\d+)\s+(.+)$", entry)
        if match:
            translation[match.group(1)] = _strip_wrapping_quotes(match.group(2))
        if ends_block:
            break
    return translation, index


def parse_nexus_trees(path: str, max_trees: Optional[int] = None) -> List[NexusTree]:
    """Parse a NEXUS tree log, or a plain Newick file, into :class:`NexusTree` items.

    Handles ``Translate`` blocks (each tree snapshots the map active at its
    position), ``tree STATE_N = [&R] (...);`` lines with or without the final
    semicolon, and bare Newick input (``.nwk``/``.tre``).  Malformed Newick
    raises :class:`TreeToolsError` instead of being silently truncated.

    Args:
        path: NEXUS ``.trees`` file or plain Newick file.
        max_trees: Optional cap on the number of trees parsed (for tests).

    Returns:
        List of :class:`NexusTree`.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Tree file not found: {path}")
    text = file_path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()

    trees: List[NexusTree] = []
    translation: Dict[str, str] = {}
    tree_pattern = re.compile(
        r"^\s*tree\s+(?:STATE_)?([A-Za-z0-9_.]+)\s*(\[[^\]]*\])?\s*=\s*(.*?)\s*$",
        re.IGNORECASE,
    )
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if re.match(r"^translate\b", stripped, re.IGNORECASE):
            translation, index = _parse_translate_block(lines, index)
            continue
        match = tree_pattern.match(line)
        if not match:
            index += 1
            continue
        state = match.group(1)
        inline_metadata = (match.group(2) or "").strip()
        body = match.group(3).strip()
        if body.endswith(";"):
            body = body[:-1].rstrip()
        metadata: Dict[str, str] = {"state": state}

        def absorb_metadata(bracket_text: str) -> None:
            bracket = bracket_text.strip()
            if bracket.startswith("[") and bracket.endswith("]"):
                bracket = bracket[1:-1].strip()
            if re.match(r"^&?R\b", bracket, re.IGNORECASE):
                metadata["rooted"] = "true"
            elif re.match(r"^&?U\b", bracket, re.IGNORECASE):
                metadata["rooted"] = "false"
            for key, value in re.findall(r"(\w+)=([^,\]]+)", bracket):
                metadata[key] = value.strip()

        if inline_metadata:
            absorb_metadata(inline_metadata)
        bracket_match = re.match(r"^\[(.*?)\]\s*(.*)$", body, re.DOTALL)
        if bracket_match:
            metadata.setdefault("rooted", "unknown")
            absorb_metadata(bracket_match.group(1))
            body = bracket_match.group(2).strip()
        else:
            metadata.setdefault("rooted", "unknown")
        # Validate eagerly so malformed trees are reported with their index.
        try:
            _parse_newick(body, translation)
        except TreeToolsError as exc:
            raise TreeToolsError(f"{path}: tree {state}: {exc}") from None
        trees.append(NexusTree(newick=body, translation=dict(translation), metadata=metadata))
        index += 1
        if max_trees is not None and len(trees) >= max_trees:
            break

    if trees:
        return trees

    # Fall back to a plain Newick file (single tree).
    newick = text.strip()
    if newick:
        candidate = newick[:-1].rstrip() if newick.endswith(";") else newick
        try:
            _parse_newick(candidate, {})
        except TreeToolsError as exc:
            raise TreeToolsError(
                f"{path}: no 'tree STATE_N = ...;' lines found and the file is "
                f"not valid Newick ({exc})"
            ) from None
        return [NexusTree(newick=candidate, translation={}, metadata={"rooted": "unknown"})]
    raise TreeToolsError(
        f"{path}: no trees found (expected NEXUS 'tree STATE_N = ...;' lines "
        f"or a plain Newick string)"
    )


# ---------------------------------------------------------------------------
# Newick parsing (iterative; supports quoted labels and comments)
# ---------------------------------------------------------------------------


def _tokenize_newick(text: str) -> List[str]:
    """Tokenise Newick text: structural tokens, labels, and skipped comments."""
    tokens: List[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "[":
            depth = 1
            index += 1
            while index < length and depth:
                if text[index] == "[":
                    depth += 1
                elif text[index] == "]":
                    depth -= 1
                index += 1
            if depth:
                raise TreeToolsError("unterminated '[...]' comment")
            continue
        if char in "(),;:":
            tokens.append(char)
            index += 1
            continue
        if char == "'":
            index += 1
            buffer: List[str] = []
            while True:
                if index >= length:
                    raise TreeToolsError("unterminated quoted label")
                current = text[index]
                if current == "'":
                    if index + 1 < length and text[index + 1] == "'":
                        buffer.append("'")
                        index += 2
                        continue
                    index += 1
                    break
                buffer.append(current)
                index += 1
            tokens.append("".join(buffer))
            continue
        start = index
        while index < length and not text[index].isspace() and text[index] not in "(),;:['":
            index += 1
        atom = text[start:index]
        if not atom:
            raise TreeToolsError(f"unexpected character {text[index]!r}")
        tokens.append(atom)
    return tokens


def _read_length(tokens: List[str], index: int) -> Tuple[float, int]:
    if index >= len(tokens):
        raise TreeToolsError("missing branch length after ':'")
    raw = tokens[index]
    try:
        value = float(raw)
    except ValueError:
        raise TreeToolsError(f"invalid branch length {raw!r}") from None
    if not math.isfinite(value):
        raise TreeToolsError(f"non-finite branch length {raw!r}")
    return value, index + 1


def _parse_newick(newick: str, translation: Dict[str, str]) -> Tuple[Dict[str, Any], List[str]]:
    """Parse a Newick string into a node dict; returns ``(root, tip_labels)``.

    Iterative (explicit stack), so 1000+ tip caterpillars do not hit Python's
    recursion limit.  Consumes every token; unbalanced parentheses, missing
    separators and trailing garbage raise :class:`TreeToolsError`.
    """
    tokens = _tokenize_newick(newick)
    if not tokens:
        raise TreeToolsError("empty Newick string")

    pos = 0
    stack: List[List[Dict[str, Any]]] = []
    root: Optional[Dict[str, Any]] = None
    need_separator = False
    while pos < len(tokens):
        token = tokens[pos]
        if need_separator:
            if token == ",":
                pos += 1
                need_separator = False
                continue
            if token == ";":
                pos += 1
                break
            if token != ")":
                raise TreeToolsError(f"expected ',' or ')' before {token!r}")
        if token == "(":
            stack.append([])
            pos += 1
            need_separator = False
            continue
        if token == ")":
            if not stack:
                raise TreeToolsError("unbalanced ')'")
            children = stack.pop()
            if len(children) < 2:
                raise TreeToolsError(
                    f"internal node has {len(children)} child(ren); at least 2 required"
                )
            pos += 1
            label: Optional[str] = None
            if pos < len(tokens) and tokens[pos] not in ("(", ")", ",", ";", ":"):
                label = tokens[pos]
                pos += 1
            branch: Optional[float] = None
            if pos < len(tokens) and tokens[pos] == ":":
                branch, pos = _read_length(tokens, pos + 1)
            node = {"label": label, "length": branch, "children": children}
            if stack:
                stack[-1].append(node)
            else:
                if root is not None:
                    raise TreeToolsError("multiple top-level subtrees")
                root = node
            need_separator = True
            continue
        if token in (",", ";"):
            raise TreeToolsError(f"unexpected token {token!r}")
        if token == ":":
            raise TreeToolsError("unexpected ':' (no label before branch length)")
        # Leaf.
        label = token
        pos += 1
        branch = None
        if pos < len(tokens) and tokens[pos] == ":":
            branch, pos = _read_length(tokens, pos + 1)
        node = {
            "label": translation.get(label, label),
            "length": branch,
            "children": [],
        }
        if stack:
            stack[-1].append(node)
        else:
            if root is not None:
                raise TreeToolsError("multiple top-level subtrees")
            root = node
        need_separator = True

    if stack:
        raise TreeToolsError("unbalanced '(' (missing closing parenthesis)")
    if root is None:
        raise TreeToolsError("no tree structure found")
    if pos < len(tokens):
        raise TreeToolsError(f"unexpected trailing token {tokens[pos]!r}")

    tips: List[str] = []
    seen: Set[str] = set()
    for node in _iter_postorder(root):
        if node["children"]:
            continue
        label = node["label"] if isinstance(node["label"], str) else None
        if label is None or label == "":
            raise TreeToolsError("tip without a label")
        if label in seen:
            raise TreeToolsError(
                f"duplicate tip label {label!r} in one tree (taxon names must be unique)"
            )
        seen.add(label)
        tips.append(label)
    return root, tips


def _iter_postorder(root: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Yield nodes of a tree in post-order without recursion."""
    stack: List[Tuple[Dict[str, Any], bool]] = [(root, False)]
    while stack:
        node, visited = stack.pop()
        if visited:
            yield node
            continue
        stack.append((node, True))
        # Push right-to-left so the leftmost child is processed first.
        for child in reversed(node["children"]):
            stack.append((child, False))


def _clade_and_heights(root: Dict[str, Any]) -> Tuple[Dict[int, frozenset], Dict[int, float]]:
    """Return ``{id(node): clade}`` and ``{id(node): height}`` in one pass."""
    clades: Dict[int, frozenset] = {}
    heights: Dict[int, float] = {}
    for node in _iter_postorder(root):
        if not node["children"]:
            clades[id(node)] = frozenset({node["label"]}) if node["label"] else frozenset()
            heights[id(node)] = 0.0
            continue
        tip_set: Set[str] = set()
        height = 0.0
        for child in node["children"]:
            tip_set.update(clades[id(child)])
            child_height = heights[id(child)] + (child["length"] or 0.0)
            height = max(height, child_height)
        clades[id(node)] = frozenset(tip_set)
        heights[id(node)] = height
    return clades, heights


def _clade_of_node(node: Dict[str, Any]) -> frozenset:
    """Clade of ``node`` (kept for backwards compatibility; O(n) per call)."""
    clade: frozenset = frozenset()
    for current in _iter_postorder(node):
        if not current["children"]:
            if current["label"]:
                clade = clade | {current["label"]}
    return clade


def _node_height(node: Dict[str, Any], tip_height: float = 0.0) -> float:
    """Height (root-to-tip distance) of ``node``; iterative, no recursion."""
    heights: Dict[int, float] = {}
    for current in _iter_postorder(node):
        if not current["children"]:
            heights[id(current)] = tip_height
            continue
        heights[id(current)] = max(
            heights[id(child)] + (child["length"] or 0.0) for child in current["children"]
        )
    return float(heights[id(node)])


def clade_statistics(trees: List[NexusTree], burnin: int = 0) -> Dict[str, Any]:
    """Compute clade posterior support and node-age statistics (pure Python).

    The root clade is included with its observed support (normally 1.0) so the
    MCC builder can keep the root, and its age is reported.
    Intervals are computed both as a shortest 95% HPD (:func:`utils.hpd_interval`)
    and as equal-tailed 2.5/97.5% quantiles (explicitly named).

    Args:
        trees: Parsed tree log.
        burnin: Number of *trees* discarded from the start.

    Returns:
        Dict with ``clades``, ``root``, ``tips``, ``n_trees`` and ``n_tips``.

    Raises:
        TreeToolsError: If burn-in leaves no trees or the tip sets are empty.
    """
    burnin = int(burnin)
    if burnin < 0:
        raise TreeToolsError(f"burn-in must be >= 0, got {burnin}")
    sampled = trees[burnin:]
    if not sampled:
        raise TreeToolsError(f"burn-in {burnin} leaves no trees (input has {len(trees)})")

    clade_map: Dict[frozenset, Dict[str, Any]] = {}
    all_tips: Set[str] = set()
    n = len(sampled)
    for tree in sampled:
        root, tips = _parse_newick(tree.newick, tree.translation)
        all_tips.update(tips)
        clades, heights = _clade_and_heights(root)
        seen: Set[frozenset] = set()
        for node in _iter_postorder(root):
            if not node["children"]:
                continue
            clade = clades[id(node)]
            if clade in seen:
                continue
            seen.add(clade)
            entry = clade_map.setdefault(clade, {"support_count": 0, "heights": []})
            entry["support_count"] += 1
            entry["heights"].append(float(heights[id(node)]))

    if not all_tips:
        raise TreeToolsError("no tip labels found in the sampled trees")

    clades_out: List[Dict[str, Any]] = []
    for clade, entry in clade_map.items():
        sorted_heights = sorted(entry["heights"])
        if not sorted_heights:
            continue
        support = entry["support_count"] / n
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            hpd_lo, hpd_hi = hpd_interval(np.asarray(sorted_heights), alpha=0.05)
        clades_out.append(
            {
                "tips": sorted(clade),
                "support": support,
                "n": len(sorted_heights),
                "mean_height": float(np.mean(sorted_heights)),
                "median_height": float(np.median(sorted_heights)),
                "hpd_lower": float(hpd_lo),
                "hpd_upper": float(hpd_hi),
                "equal_tailed_lower": float(np.quantile(sorted_heights, 0.025)),
                "equal_tailed_upper": float(np.quantile(sorted_heights, 0.975)),
            }
        )
    clades_out.sort(key=lambda c: (-c["support"], -len(c["tips"]), c["tips"]))

    root_entry = next(
        (c for c in clades_out if frozenset(c["tips"]) == frozenset(all_tips)),
        max(clades_out, key=lambda c: len(c["tips"])) if clades_out else None,
    )
    return {
        "clades": clades_out,
        "root": root_entry,
        "tips": sorted(all_tips),
        "n_tips": len(all_tips),
        "n_trees": n,
        "burnin": burnin,
    }


# ---------------------------------------------------------------------------
# MCC summary tree
# ---------------------------------------------------------------------------


def _format_support(value: float) -> str:
    return f"{value:.4g}"


def _quote_label(label: str) -> str:
    if label == "":
        return label
    if re.search(r"[\s,:;()\[\]']", label):
        return "'" + label.replace("'", "''") + "'"
    return label


def _summary_record_map(stats: Dict[str, Any]) -> Dict[frozenset, Dict[str, Any]]:
    mapping: Dict[frozenset, Dict[str, Any]] = {
        frozenset(entry["tips"]): entry for entry in stats["clades"]
    }
    root = stats.get("root")
    if root:
        mapping[frozenset(root["tips"])] = root
    return mapping


def build_mcc_tree(
    trees: List[NexusTree],
    burnin: int = 0,
    support_threshold: float = 0.0,
    heights: str = "mean",
) -> str:
    """Build an annotated maximum-clade-credibility summary tree (Newick).

    The scaffold is the sampled tree with the highest sum of log clade
    supports.  Its topology is preserved completely: ``support_threshold``
    only suppresses support/height *annotations* on low-support nodes, it never
    removes taxa or subtrees (TreeAnnotator's ``-limit`` semantics).

    Output nodes carry the support as a node label, ``[&height=...,posterior=...]``
    annotations, and branch lengths derived from the chosen height summary
    (``mean`` or ``median``).  The root has no branch length; its age is the
    statistic reported by :func:`clade_statistics` under ``root``.

    Raises:
        TreeToolsError: If ``stats`` lacks the required ``tips`` key.
    """
    if heights not in ("mean", "median", "keep"):
        raise TreeToolsError(f"heights must be one of 'mean', 'median', 'keep', got {heights!r}")
    height_key = "mean_height" if heights in ("mean", "keep") else "median_height"
    sampled = trees[burnin:]
    if not sampled:
        raise TreeToolsError(f"burn-in {burnin} leaves no trees (input has {len(trees)})")
    stats = clade_statistics(trees, burnin)
    tips = stats.get("tips")
    if not tips:
        raise TreeToolsError(
            "clade statistics are missing the 'tips' key; cannot build an MCC tree"
        )
    support_map: Dict[frozenset, float] = {
        frozenset(entry["tips"]): float(entry["support"]) for entry in stats["clades"]
    }
    support_map[frozenset(tips)] = 1.0

    best: Optional[Tuple[float, int, NexusTree]] = None
    for tree in sampled:
        root, _ = _parse_newick(tree.newick, tree.translation)
        clades, _ = _clade_and_heights(root)
        log_score = 0.0
        internal = 0
        for node in _iter_postorder(root):
            if not node["children"]:
                continue
            support = support_map.get(clades[id(node)], 0.5)
            log_score += math.log(max(support, 1e-12))
            internal += 1
        if best is None or (log_score, internal) > (best[0], best[1]):
            best = (log_score, internal, tree)
    if best is None:
        raise TreeToolsError("could not score any tree")

    scaffold_tree = best[2]
    root, _ = _parse_newick(scaffold_tree.newick, scaffold_tree.translation)
    clades, own_heights = _clade_and_heights(root)
    records = _summary_record_map(stats)

    def node_height(node: Dict[str, Any]) -> float:
        if heights == "keep":
            # "keep" = keep the scaffold (target) tree's own heights.
            return float(own_heights[id(node)])
        record = records.get(clades[id(node)])
        if record is not None:
            value = record.get(height_key)
            if value is not None and math.isfinite(value):
                return float(value)
        return float(own_heights[id(node)])

    parent_of: Dict[int, Dict[str, Any]] = {}
    for node in _iter_postorder(root):
        for child in node["children"]:
            parent_of[id(child)] = node

    # Single post-order pass: children render before parents, so branch
    # lengths and annotations are emitted exactly once.
    with_lengths: Dict[int, str] = {}
    for node in _iter_postorder(root):
        parent = parent_of.get(id(node))
        parent_height = node_height(parent) if parent is not None else None
        if not node["children"]:
            text = _quote_label(node["label"] or "")
        else:
            body = "(" + ",".join(with_lengths[id(child)] for child in node["children"]) + ")"
            record = records.get(clades[id(node)])
            support = float(record["support"]) if record is not None else 1.0
            if support >= support_threshold and support > 0.0:
                body += _format_support(support)
                body += f"[&height={node_height(node):.4g},posterior={support:.4g}]"
            text = body
        if parent_height is not None:
            text += f":{max(0.0, parent_height - node_height(node)):.6g}"
        with_lengths[id(node)] = text

    return with_lengths[id(root)] + ";"


# ---------------------------------------------------------------------------
# Java wrappers
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    ok: bool
    command: List[str]
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None


def _java_run(args: List[str], timeout: int = 600) -> CommandResult:
    java = find_java()
    if java is None:
        return CommandResult(ok=False, command=args, stderr="no usable Java runtime found")
    try:
        proc = subprocess.run(
            [java] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(ok=False, command=[java] + args, stderr=str(exc))
    return CommandResult(
        ok=proc.returncode == 0,
        command=[java] + args,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
    )


def _java_unavailable(result: CommandResult) -> bool:
    """True when the failure is "no Java/jar", not a TreeAnnotator error."""
    stderr = (result.stderr or "").lower()
    return (
        not result.command
        or "no usable java" in stderr
        or "no beast-" in stderr
        and "jar found" in stderr
    )


def _tree_annotator_class(beast_version: str) -> str:
    """BEAST X 10.5.0 moved TreeAnnotator into a subpackage."""
    if beast_version == "10.5.0":
        return "dr.app.tools.treeannotator.TreeAnnotator"
    return "dr.app.tools.TreeAnnotator"


def _log_combiner_class(beast_version: str) -> str:
    """BEAST X 10.5.0 moved LogCombiner into a subpackage."""
    if beast_version == "10.5.0":
        return "dr.app.tools.logcombiner.LogCombiner"
    return "dr.app.tools.LogCombiner"


def run_tree_annotator(
    trees_path: str,
    output_path: str,
    heights: str = "mean",
    burnin: int = 0,
    limit: float = 0.0,
    beast_version: str = "1.10.4",
) -> CommandResult:
    """Run TreeAnnotator on a tree log (Java authoritative path).

    ``burnin`` is a **tree** count and is passed as ``-burninTrees``.
    """
    jar = lookup_jar(beast_version)
    if jar is None:
        return CommandResult(ok=False, command=[], stderr=f"no beast-{beast_version}.jar found")
    args = [
        "-cp",
        str(jar),
        _tree_annotator_class(beast_version),
        "-heights",
        heights,
        "-burninTrees",
        str(int(burnin)),
        "-limit",
        str(limit),
        trees_path,
        output_path,
    ]
    return _java_run(args)


def run_log_combiner(
    inputs: List[str],
    output_path: str,
    burnin: int = 0,
    resample: Optional[int] = None,
    strip: bool = False,
    beast_version: str = "1.10.4",
) -> CommandResult:
    """Run LogCombiner on log/tree files (Java authoritative path).

    LogCombiner takes inputs as positional arguments and writes to the *last*
    positional argument (no ``-o`` option -- verified against 1.10.4).
    """
    jar = lookup_jar(beast_version)
    if jar is None:
        return CommandResult(ok=False, command=[], stderr=f"no beast-{beast_version}.jar found")
    args = ["-cp", str(jar), _log_combiner_class(beast_version), "-burnin", str(int(burnin))]
    if resample is not None:
        args += ["-resample", str(int(resample))]
    if strip:
        args += ["-strip"]
    args += list(inputs) + [output_path]
    return _java_run(args)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def annotate_tree_command(
    trees_path: str,
    output_path: str,
    heights: str = "mean",
    burnin: int = 0,
    limit: float = 0.0,
    python_only: bool = False,
    beast_version: str = "1.10.4",
) -> Dict[str, Any]:
    """Run tree annotation, returning a machine-readable summary.

    ``burnin`` counts **trees** in both execution paths.

    Raises:
        TreeToolsError: If Java runs but fails (stderr propagated), or if the
            pure-Python path cannot parse/annotate the input.
    """
    if heights not in ("mean", "median", "keep", "ca"):
        raise TreeToolsError(
            f"heights must be one of 'mean', 'median', 'keep', 'ca', got {heights!r}"
        )
    if python_only and heights == "ca":
        raise TreeToolsError(
            "common-ancestor (ca) heights need the Java TreeAnnotator; "
            "drop --python-only or use 'mean'/'median'"
        )
    jar_error: Optional[str] = None
    if not python_only:
        result = run_tree_annotator(
            trees_path,
            output_path,
            heights=heights,
            burnin=burnin,
            limit=limit,
            beast_version=beast_version,
        )
        if result.ok and Path(output_path).exists():
            return {
                "ok": True,
                "method": "TreeAnnotator",
                "beast_version": beast_version,
                "output": output_path,
                "heights": heights,
                "burnin": burnin,
                "limit": limit,
            }
        if not _java_unavailable(result):
            raise TreeToolsError(
                f"TreeAnnotator failed (exit {result.exit_code}): "
                f"{(result.stderr or result.stdout or '').strip()[:2000]}"
            )
        jar_error = result.stderr
        if result.ok:
            raise TreeToolsError(f"TreeAnnotator reported success but did not create {output_path}")
        if heights == "ca":
            raise TreeToolsError(
                "TreeAnnotator is unavailable, and the pure-Python fallback "
                "cannot compute common-ancestor (ca) heights; install a "
                "usable JDK and jar, or use 'mean'/'median'"
            )

    trees = parse_nexus_trees(trees_path)
    burnin = int(burnin)
    if burnin < 0:
        raise TreeToolsError(f"burn-in must be >= 0, got {burnin}")
    if burnin >= len(trees):
        raise TreeToolsError(f"burn-in {burnin} trees leaves no trees (input has {len(trees)})")
    stats = clade_statistics(trees, burnin)
    mcc = build_mcc_tree(trees, burnin, support_threshold=limit, heights=heights)
    Path(output_path).write_text(mcc + "\n", encoding="utf-8")
    root = stats.get("root") or {}
    return {
        "ok": True,
        "method": "python-fallback",
        "beast_version": None,
        "output": output_path,
        "heights": heights,
        "burnin": burnin,
        "limit": limit,
        "n_trees": stats["n_trees"],
        "n_tips": stats["n_tips"],
        "n_clades": len(stats["clades"]),
        "root_age": root.get("mean_height" if heights in ("mean", "keep") else "median_height"),
        "fallback_reason": jar_error,
    }


# -- pure-Python log combining ----------------------------------------------


def _read_log_rows(path: str) -> Tuple[List[str], str, List[str]]:
    """Return ``(comment_lines, header, data_rows)`` of a tab-separated log."""
    comments: List[str] = []
    header: Optional[str] = None
    rows: List[str] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\n\r")
            if not line.strip():
                continue
            if line.startswith("#"):
                comments.append(line)
                continue
            if header is None:
                header = line
                continue
            rows.append(line)
    if header is None:
        raise TreeToolsError(f"{path}: no column header found")
    return comments, header, rows


def _combine_logs_python(
    inputs: List[str],
    output_path: str,
    burnin: int = 0,
    resample: Optional[int] = None,
    strip: bool = False,
) -> Dict[str, Any]:
    """Pure-Python single/multi log combiner honouring burn-in/resample/strip.

    ``burnin`` is a count of **rows** (samples) per input; ``resample`` keeps
    every ``resample``-th remaining row.  ``strip`` (annotations in tree files)
    is not supported for numeric logs and raises a clear error.
    """
    if strip and not all(str(p).lower().endswith(".trees") for p in inputs):
        raise TreeToolsError(
            "the Python log-combining fallback does not support -strip for "
            "numeric .log files (LogCombiner -strip only strips tree "
            "annotations); use the Java path or drop -strip"
        )
    if strip:
        raise TreeToolsError(
            "the Python log-combining fallback does not support -strip "
            "(tree annotations); use the Java path"
        )
    if burnin < 0:
        raise TreeToolsError(f"burn-in must be >= 0, got {burnin}")
    if resample is not None and resample < 1:
        raise TreeToolsError(f"resample must be >= 1, got {resample}")

    headers: List[str] = []
    comments: List[str] = []
    all_rows: List[str] = []
    for index, path in enumerate(inputs):
        file_comments, header, rows = _read_log_rows(path)
        headers.append(header)
        if index == 0:
            comments = file_comments
        if len(rows) <= burnin:
            raise TreeToolsError(
                f"{path}: burn-in {burnin} rows leaves no data (input has {len(rows)})"
            )
        kept = rows[burnin:]
        if resample is not None:
            kept = kept[:: int(resample)]
        all_rows.extend(kept)

    if len(set(headers)) != 1:
        raise TreeToolsError(
            "input logs have different column headers; the Python fallback "
            "cannot combine them (use LogCombiner)"
        )
    columns = headers[0].split("\t")
    kept_columns = list(range(len(columns)))
    if strip:
        values = [row.split("\t") for row in all_rows]
        kept_columns = [
            i
            for i in range(len(columns))
            if len({row[i] for row in values if len(row) == len(columns)}) > 1
        ]
    lines = list(comments)
    lines.append("\t".join(columns[i] for i in kept_columns))
    for row in all_rows:
        parts = row.split("\t")
        lines.append("\t".join(parts[i] for i in kept_columns))
    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "method": "python-fallback",
        "rows": len(all_rows),
        "columns": len(kept_columns),
        "burnin": burnin,
        "resample": resample,
    }


def combine_logs_command(
    inputs: List[str],
    output_path: str,
    burnin: int = 0,
    resample: Optional[int] = None,
    strip: bool = False,
    python_only: bool = False,
    beast_version: str = "1.10.4",
) -> Dict[str, Any]:
    """Run log combining, returning a machine-readable summary.

    Java failures are propagated with their stderr; the pure-Python fallback
    is used only when no Java/jar is available (or ``python_only=True``) and it
    honours ``burnin``/``resample`` (``strip`` is rejected with a clear error).
    """
    if not inputs:
        raise TreeToolsError("no input logs given")
    jar_error: Optional[str] = None
    if not python_only:
        result = run_log_combiner(
            inputs,
            output_path,
            burnin=burnin,
            resample=resample,
            strip=strip,
            beast_version=beast_version,
        )
        if result.ok and Path(output_path).exists():
            return {
                "ok": True,
                "method": "LogCombiner",
                "beast_version": beast_version,
                "output": output_path,
                "burnin": burnin,
                "resample": resample,
                "strip": strip,
            }
        if not _java_unavailable(result):
            raise TreeToolsError(
                f"LogCombiner failed (exit {result.exit_code}): "
                f"{(result.stderr or result.stdout or '').strip()[:2000]}"
            )
        jar_error = result.stderr
        if result.ok:
            raise TreeToolsError(f"LogCombiner reported success but did not create {output_path}")

    summary = _combine_logs_python(
        inputs, output_path, burnin=burnin, resample=resample, strip=strip
    )
    summary.update(
        {
            "ok": True,
            "beast_version": None,
            "output": output_path,
            "fallback_reason": jar_error,
        }
    )
    return summary
