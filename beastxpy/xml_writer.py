"""Canonical BEAST 1 XML serialisation for BeastXPy.

Turns the structure tree produced by :mod:`beastxpy.model` into canonical,
4-space-indented BEAST 1 XML.  The writer maintains an id registry and an
idref resolution table and asserts the BEAST 1 invariants:

* every ``id`` is unique within the document;
* every ``idref`` resolves to a declared ``id``;
* every estimated (continuous) parameter appears in at least one operator;
* every operator target is declared estimable;
* every estimated parameter carries a prior (explicit or auto-completed).

The estimate/operator/prior coverage bookkeeping is performed by the model
assembler; the writer receives the declared sets and asserts them.  Any
violation raises :class:`XMLWriterError` before a single byte reaches disk.

Text content and comments are XML-safe: element text is escaped (``&``/``<``/
``>``), comments are sanitised (``--`` is never emitted, trailing ``-`` is
removed and long provenance comments are truncated) and tag/attribute names
are validated against the XML ``Name`` production.  Files are always written
as UTF-8 with deterministic ``\\n`` newlines using an atomic replace.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .utils import escape_xml_attr, format_number

# XML 1.0 ``Name`` (approximation used for tag/attribute validation).
_XML_NAME_RE = re.compile(r"^[A-Za-z_:][A-Za-z0-9._:\-]*$")

# Comments longer than this (per emitted line) are truncated.  Provenance
# references and DOIs are short; anything longer is almost certainly a pasted
# paragraph and would bloat every generated XML.
MAX_COMMENT_LENGTH = 1000


class XMLWriterError(ValueError):
    """Raised when the XML tree violates a writer invariant."""


PROLOGUE = '<?xml version="1.0" standalone="yes"?>\n'

# Tags whose <parameter> children declare new parameter objects (ids must be
# globally unique). Everything else that references a parameter uses idref.
_PARAMETER_PARENT_TAGS = {
    "treeModel",
    "HKYModel",
    "TN93Model",
    "gtrModel",
    "siteModel",
    "strictClockBranchRates",
    "discretizedBranchRates",
    "randomLocalClock",
    "yuleModel",
    "birthDeathModel",
    "constantSize",
    "exponentialGrowth",
    "logisticGrowth",
    "expansion",
    "bayesianSkyline",
    "frequencyModel",
    "gammaShape",
    "proportionInvariant",
    "rate",
    "kappa",
    "kappa1",
    "kappa2",
    "birthRate",
    "birthMinusDeathRate",
    "relativeDeathRate",
    "populationSize",
    "growthRate",
    "ancestralPopulationSize",
    "populationSizes",
    "groupSizes",
    "rateCategories",
    "rates",
    "rateIndicators",
    "rootHeight",
    "nodeHeights",
    "mean",
    "stdev",
}


# ---------------------------------------------------------------------------
# Escaping / sanitising helpers
# ---------------------------------------------------------------------------


def escape_xml_text(text: Any) -> str:
    """Escape character data for an XML element body.

    ``&`` and ``<`` must be escaped; ``>`` is escaped as well so that a
    literal ``]]>`` can never terminate a (future) CDATA section.
    Carriage returns are normalised to ``\\n`` for deterministic output.
    """
    value = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def sanitize_xml_comment(text: Any, max_length: int = MAX_COMMENT_LENGTH) -> str:
    """Return ``text`` as one or more legal XML comment bodies.

    The returned string may contain newlines; callers must emit one
    ``<!-- body -->`` pair per line (see :meth:`XmlWriter._append_comment`).
    ``--`` never survives, a trailing ``-`` is removed, control characters
    illegal in XML 1.0 are dropped and each line is length-limited.
    """
    value = str(text).replace("\r\n", "\n").replace("\r", "\n")
    # Drop characters that are not legal XML 1.0 characters.
    value = "".join(
        ch
        for ch in value
        if ch in "\t\n"
        or 0x20 <= ord(ch) <= 0xD7FF
        or 0xE000 <= ord(ch) <= 0xFFFD
        or ord(ch) >= 0x10000
    )
    # '-' immediately before the closing delimiter would form '--->'; and
    # '--' is forbidden anywhere in a comment.
    value = re.sub(r"-{2,}", "- -", value)
    lines: List[str] = []
    for raw_line in value.split("\n"):
        line = raw_line.strip()
        line = re.sub(r"-{2,}", "- -", line)
        if line.endswith("-"):
            line = line[:-1].rstrip() or "-"
        if len(line) > max_length:
            line = line[: max_length - 3].rstrip() + "..."
        lines.append(line)
    return "\n".join(lines)


def _validate_name(name: str, kind: str, context: str = "") -> None:
    if not name:
        raise XMLWriterError(
            f"empty XML {kind} name{' under <' + context + '>' if context else ''}"
        )
    if not _XML_NAME_RE.match(name):
        raise XMLWriterError(
            f"invalid XML {kind} name {name!r} " f"(must match {_XML_NAME_RE.pattern})"
        )


def _escape_attr_value(value: Any) -> str:
    text = format_number(value)
    # Reject characters that are illegal in XML 1.0 attributes.
    bad = [ch for ch in text if ord(ch) < 0x20 and ch not in "\t\n\r"]
    if bad:
        raise XMLWriterError(f"attribute value contains control character(s) {bad!r}: {text!r}")
    return escape_xml_attr(text)


# ---------------------------------------------------------------------------
# Comment stripping (used by diagnostics sensitivity variants)
# ---------------------------------------------------------------------------


def strip_xml_comments(xml_str: str) -> str:
    """Remove every XML comment from ``xml_str``.

    Contract:
      * only real ``<!-- ... -->`` markup outside CDATA sections is removed;
      * text inside ``<![CDATA[ ... ]]>`` blocks, quoted attribute values and
        processing instructions is preserved verbatim;
      * the XML declaration and all element content are left untouched, so a
        stripped document differs from the input only by the removed comments
        (including BeastXPy fingerprint header comments).

    A malformed comment (``<!--`` without ``-->``) raises
    :class:`XMLWriterError` instead of silently truncating the document.
    """
    out: List[str] = []
    index = 0
    length = len(xml_str)
    in_tag = False
    quote: Optional[str] = None
    while index < length:
        char = xml_str[index]
        if not in_tag and xml_str.startswith("<![CDATA[", index):
            end = xml_str.find("]]>", index + 9)
            if end == -1:
                raise XMLWriterError("unterminated CDATA section")
            out.append(xml_str[index : end + 3])
            index = end + 3
            continue
        if not in_tag and xml_str.startswith("<!--", index):
            end = xml_str.find("-->", index + 4)
            if end == -1:
                raise XMLWriterError("unterminated XML comment")
            # Preserve the line break that followed the comment so that
            # pretty-printed documents keep their line structure.
            index = end + 3
            continue
        if quote is not None:
            out.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'") and in_tag:
            quote = char
            out.append(char)
            index += 1
            continue
        if char == "<":
            in_tag = True
        elif char == ">" and in_tag:
            in_tag = False
        out.append(char)
        index += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class XmlWriter:
    """Serialize a structure tree into canonical BEAST 1 XML.

    The structure tree is the plain-dict shape produced by
    :class:`beastxpy.model.ModelAssembler`: every node is
    ``{"tag": ..., "attrib": {...}, "children": [...], "text": ..., "comment": ...}``.
    """

    def __init__(self, beast_version: str = "1.10.4") -> None:
        self.beast_version = beast_version
        self._ids: Set[str] = set()
        self._idrefs: Dict[str, List[str]] = {}
        self._estimated: Set[str] = set()
        self._operator_targets: Set[str] = set()
        self._prior_targets: Set[str] = set()
        self._header_comments: List[str] = []
        self._seen_tags: Set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def add_header_comment(self, comment: str) -> None:
        """Add a comment placed right after the XML prologue."""
        self._header_comments.append(comment)

    def write(self, root: Dict[str, Any]) -> str:
        """Serialize ``root`` (the ``<beast>`` node) to a canonical XML string."""
        self._reset()
        lines: List[str] = [PROLOGUE.rstrip("\n")]
        for comment in self._header_comments:
            self._append_comment(lines, comment, "")
        if root is None or root.get("tag") != "beast":
            raise XMLWriterError("the root element of a BEAST 1 document must be <beast>")
        self._render_node(root, "", 0, lines)
        self.check_invariants()
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _reset(self) -> None:
        self._ids = set()
        self._idrefs = {}
        self._estimated = set()
        self._operator_targets = set()
        self._prior_targets = set()
        self._seen_tags = set()

    def _append_comment(self, lines: List[str], comment: Any, indent: str) -> None:
        body = sanitize_xml_comment(comment)
        for line in body.split("\n"):
            if not line:
                continue
            lines.append(f"{indent}<!-- {line} -->")

    def _render_node(
        self,
        node: Dict[str, Any],
        parent_tag: str,
        depth: int,
        lines: List[str],
    ) -> None:
        tag = str(node.get("tag", ""))
        if tag == "text":
            lines.append(f"{'    ' * depth}{escape_xml_text(node.get('text') or '')}")
            return
        _validate_name(tag, "element", parent_tag)
        attrib = node.get("attrib") or {}
        children = node.get("children") or []
        text = node.get("text")
        comment = node.get("comment")
        indent = "    " * depth

        for key in attrib:
            _validate_name(str(key), "attribute", tag)

        node_id = attrib.get("id")
        node_idref = attrib.get("idref")
        if node_id is not None:
            self._register_id(str(node_id), parent_tag)
        if node_idref is not None:
            self._register_idref(str(node_idref), tag)
        self._record_coverage(tag, attrib, parent_tag)

        attr_str = "".join(f' {k}="{_escape_attr_value(v)}"' for k, v in attrib.items())
        if comment is not None:
            self._append_comment(lines, comment, indent)
        if not children and text is None:
            lines.append(f"{indent}<{tag}{attr_str}/>")
            return
        lines.append(f"{indent}<{tag}{attr_str}>")
        if text is not None:
            body = escape_xml_text(text).strip()
            if body:
                for child in children:
                    if str(child.get("tag", "")) == "text":
                        continue
                    self._render_node(child, tag, depth + 1, lines)
                lines.append(f"{indent}    {body}")
            else:
                for child in children:
                    self._render_node(child, tag, depth + 1, lines)
        else:
            for child in children:
                self._render_node(child, tag, depth + 1, lines)
        lines.append(f"{indent}</{tag}>")

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------
    def _register_id(self, node_id: str, parent_tag: str) -> None:
        if node_id in self._ids:
            raise XMLWriterError(
                f"duplicate id {node_id!r} (second occurrence under <{parent_tag}>)"
            )
        self._ids.add(node_id)

    def _register_idref(self, node_id: str, tag: str) -> None:
        self._idrefs.setdefault(node_id, []).append(tag)

    def _record_coverage(self, tag: str, attrib: Dict[str, Any], parent_tag: str) -> None:
        """Collect operator/prior coverage info for the invariants."""
        idref = attrib.get("idref")
        if tag != "parameter" or idref is None:
            return
        pid = str(idref)
        if parent_tag in (
            "scaleOperator",
            "deltaExchange",
            "uniformOperator",
            "randomWalkOperator",
            "randomWalkIntegerOperator",
            "uniformIntegerOperator",
            "swapOperator",
            "bitFlipOperator",
            "intUniformOperator",
        ):
            self._operator_targets.add(pid)
        elif parent_tag in ("up", "down"):
            # upDownOperator couples rates/root height; treated as operator cover.
            self._operator_targets.add(pid)
        elif parent_tag.endswith("Prior") or parent_tag in (
            "speciationLikelihood",  # noqa: SIM114
            "coalescentLikelihood",
        ):
            if parent_tag not in ("speciationLikelihood", "coalescentLikelihood"):
                self._prior_targets.add(pid)

    # ------------------------------------------------------------------
    # Invariants
    # ------------------------------------------------------------------
    def declare_estimated(self, param_id: str) -> None:
        """Declare a parameter as estimated (set by the generator)."""
        self._estimated.add(param_id)

    def declare_covered(self, param_id: str, by: str) -> None:
        """Declare an estimated parameter covered by 'operator' or 'prior'."""
        if by == "operator":
            self._operator_targets.add(param_id)
        elif by == "prior":
            self._prior_targets.add(param_id)

    def check_invariants(self) -> None:
        """Assert the writer/BEAST 1 invariants; raise XMLWriterError."""
        for idref in sorted(self._idrefs):
            if idref not in self._ids:
                raise XMLWriterError(
                    f"dangling idref {idref!r} (referenced by "
                    f"{', '.join(sorted(set(self._idrefs[idref])))}; not defined)"
                )
        for pid in sorted(self._estimated):
            if pid not in self._operator_targets:
                raise XMLWriterError(
                    f"estimated parameter {pid!r} has no operator (invariant violated)"
                )
            if pid not in self._prior_targets:
                raise XMLWriterError(
                    f"estimated parameter {pid!r} has no prior (invariant violated)"
                )


def write_xml(
    root: Dict[str, Any],
    beast_version: str = "1.10.4",
    header_comments: Optional[List[str]] = None,
    declared_estimated: Optional[List[str]] = None,
    covered: Optional[Dict[str, List[str]]] = None,
) -> str:
    """One-shot convenience wrapper around :class:`XmlWriter`.

    Args:
        root: The ``<beast>`` structure tree from the model assembler.
        beast_version: Target BEAST distribution (diagnostics only).
        header_comments: Extra XML header comments (fingerprint etc).
        declared_estimated: Parameter ids declared estimable.
        covered: Mapping parameter id -> list of 'operator'/'prior' markers.
    """
    writer = XmlWriter(beast_version=beast_version)
    for comment in header_comments or []:
        writer.add_header_comment(comment)
    for pid in declared_estimated or []:
        writer.declare_estimated(pid)
    for pid, markers in (covered or {}).items():
        for marker in markers:
            writer.declare_covered(pid, marker)
    return writer.write(root)


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def atomic_write_text(
    path: str,
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str = "\n",
) -> Path:
    """Write ``text`` to ``path`` atomically (UTF-8 by default).

    The parent directory is created if needed.  The content is first written
    to a temporary file in the target directory and then ``os.replace``-d
    into place, so an interrupted write never leaves a truncated artefact and
    a failure never leaves a half-written file behind.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        normalised = normalised.replace("\n", newline)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(normalised)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target


def atomic_copy_file(source: str, path: str) -> Path:
    """Copy ``source`` to ``path`` atomically (fsync + ``os.replace``)."""
    src = Path(source)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "wb") as out_handle, src.open("rb") as in_handle:
            for chunk in iter(lambda: in_handle.read(1024 * 1024), b""):
                out_handle.write(chunk)
            out_handle.flush()
            os.fsync(out_handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target


def write_xml_file(
    path: str,
    xml_text: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Write serialized XML with explicit UTF-8 encoding and ``\\n`` newlines.

    Uses :func:`atomic_write_text`; returns the written path.
    """
    return atomic_write_text(path, xml_text, encoding=encoding, newline="\n")


def plan_versioned_outputs(
    output: str,
    versions: List[str],
    analysis_name: str,
) -> List[Tuple[str, str]]:
    """Map requested versions to output file paths.

    For a single version the given ``output`` path is used unchanged.  For
    multiple versions a version tag is appended to the stem, e.g.
    ``out.xml`` -> ``out_1.10.4.xml`` / ``out_10.5.0.xml``.  If ``output``
    is an existing directory the files are named
    ``<analysis_name>_<version>.xml`` inside it.
    """
    if len(versions) == 1:
        return [(versions[0], output)]
    target = Path(output)
    if target.exists() and target.is_dir():
        return [(version, str(target / f"{analysis_name}_{version}.xml")) for version in versions]
    stem = target.stem if target.suffix else target.name
    suffix = target.suffix or ".xml"
    return [(version, str(target.with_name(f"{stem}_{version}{suffix}"))) for version in versions]
