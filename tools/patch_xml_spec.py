#!/usr/bin/env python3
"""Rebuild ``beastxpy/data/xml_spec.json`` from verifiable BEAST evidence.

The old ``tools/patch_xml_spec.py`` merged a hand-written ``EXTRA`` dictionary
into the spec.  That dictionary contained element names that are not BEAST
parser names (``generalizedSkyline``, ``bayesianSkyline``, ``randomLocalClock``)
and pure object ids (``yule``, ``birthDeath``, ``expGrowth``, ``skyline``).
Gate 1 therefore accepted documents that BEAST rejects and rejected real
BEAST elements that the source extractor had missed.

This generator only accepts an element name when there is real evidence for
it in at least one BEAST version:

1. the parser store of the official BEAST jar (``BxpParserNames`` probe),
   or, when no jar is available, the parser classes registered in the
   ``release_parsers.properties`` / ``development_parsers.properties`` files
   of the reference source tree;
2. element names accepted by an ``ElementRule`` of a registered parser
   (structural children such as ``rateCT`` or ``groupSizes``);
3. every element name that occurs in the official example XML corpus of the
   corresponding distribution;
4. element names emitted by the project itself are recorded as evidence, but
   they are only added to the whitelist when (1)-(3) corroborate them.  Names
   that exist only in the project (for example ``generalizedSkyline``) are
   reported under ``provenance.project_only`` and are *not* whitelisted, so
   Gate 1 keeps flagging them.

The generated file records provenance (jar SHA-256, parser counts, corpus
counts, digests of the observed element sets) and a version reverse table
(``removed_in_10_5_0`` / ``renames``).  ``--check`` verifies that the
committed file is byte-identical to a fresh build.

Usage::

    python tools/patch_xml_spec.py                 # rebuild in place
    python tools/patch_xml_spec.py --check         # CI drift guard
    python tools/patch_xml_spec.py --no-jar        # source-tree fallback
    BEAST_1_10_4_HOME=/path/to/beast \\
    BEAST_10_5_0_HOME=/path/to/beast-x \\
        python tools/patch_xml_spec.py

The command never invents element names and never merges a static list.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from defusedxml import ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REF_DIR = PROJECT_ROOT.parent / "BeastXPy-参考软件"
SPEC_PATH = PROJECT_ROOT / "beastxpy" / "data" / "xml_spec.json"
PROBE_SOURCE = PROJECT_ROOT / "tools" / "java" / "BxpParserNames.java"
SCHEMA = "beastxpy-xml-spec"
SCHEMA_VERSION = 2

OFFICIAL_INSTALL_DIRS = {
    "1.10.4": (
        ("BEASTv1.10.4", ""),
        ("BEAST.v1.10.4", "BEAST v1.10.4"),
        ("BEAST.v1.10.4", ""),
    ),
    "10.5.0": (
        ("BEASTv10.5.0", ""),
        ("BEAST.X.v10.5.0", "BEAST X v10.5.0"),
        ("BEAST.X.v10.5.0", ""),
    ),
}


def _official_install_candidates(version: str, ref_dir: Path) -> List[Path]:
    """Candidate official distribution roots in ``ref_dir``.

    The reference bundles are unpacked with different directory spellings
    (``BEASTv1.10.4``, ``BEAST.v1.10.4/BEAST v1.10.4``, ...), so both the
    canonical and the historical layouts are probed.
    """
    directories: List[Path] = []
    env_home = os.environ.get(f"BEAST_{version.replace('.', '_')}_HOME")
    if env_home:
        directories.append(Path(env_home))
    for outer, inner in OFFICIAL_INSTALL_DIRS[version]:
        directories.append(ref_dir / outer / inner if inner else ref_dir / outer)
    # Tolerate an extra wrapper such as ``*/BEAST v10.5.0``.
    for pattern in ("*BEAST*v*", "*BEAST*X*v*"):
        for root in sorted(ref_dir.glob(pattern)):
            if root.is_dir():
                directories.append(root)
                for child in sorted(root.glob("*BEAST*")):
                    if child.is_dir():
                        directories.append(child)
    seen: Set[Path] = set()
    unique: List[Path] = []
    for directory in directories:
        resolved = directory.resolve() if directory.exists() else directory
        if resolved not in seen:
            seen.add(resolved)
            unique.append(directory)
    return [directory for directory in unique if version in str(directory)]


# Required attributes that are enforced by the corresponding BEAST parser
# (an AttributeRule constructed without the ``optional`` flag).  The list is
# deliberately small and was verified against both official example corpora.
# tests/test_spec_contract.py pins this table to the committed xml_spec.json so
# the two cannot drift.  It is data, not a whitelist: every element named here
# must still be proven by the evidence above.
REQUIRED_ATTRIBUTES = {
    "mcmc": ["chainLength"],
    "scaleOperator": ["scaleFactor", "weight"],
    "randomWalkOperator": ["windowSize", "weight"],
    "randomWalkIntegerOperator": ["windowSize", "weight"],
    "uniformOperator": ["weight"],
    "uniformIntegerOperator": ["weight"],
    "deltaExchange": ["delta", "weight"],
    "bitFlipOperator": ["weight"],
    "swapOperator": ["size", "weight"],
    "upDownOperator": ["scaleFactor", "weight"],
    "subtreeSlide": ["gaussian", "weight"],
    "narrowExchange": ["weight"],
    "wideExchange": ["weight"],
    "wilsonBalding": ["weight"],
}

ATTRIBUTE_ENUMS = {
    "alignment.dataType": [
        "amino acid",
        "binary",
        "codon",
        "continuous",
        "hiddenAminoAcid",
        "hiddenCodon",
        "hiddenNucleotide",
        "microsatellite",
        "nucleotide",
        "P2P",
        "pairedDataType",
        "simpleCodon",
        "twoStateCovarion",
    ],
    "date.direction": ["forwards", "backwards"],
    "date.units": ["years", "months", "days"],
}

# Parents whose direct-child vocabulary is checked structurally by the Python
# validator.  The generated file only records observed evidence counts, which
# documents where the checks come from without pretending to be a full schema.
STRUCTURAL_PARENTS = (
    "patterns",
    "gtrModel",
    "randomLocalClockModel",
    "generalizedSkyLineLikelihood",
    "variableDemographic",
    "date",
    "ptree",
)


# ---------------------------------------------------------------------------
# small process helpers
# ---------------------------------------------------------------------------
def _run(
    cmd: Sequence[str], cwd: Optional[Path] = None, timeout: int = 120
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _find_executable(name: str, extra: Iterable[Optional[str]]) -> Optional[str]:
    for candidate in extra:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return str(path)
        resolved = shutil.which(str(candidate))
        if resolved:
            return resolved
    return shutil.which(name)


def find_java() -> Optional[str]:
    java_home = os.environ.get("JAVA_HOME")
    candidates = []
    if os.environ.get("BEASTX_JAVA"):
        candidates.append(os.environ["BEASTX_JAVA"])
    if java_home:
        candidates.extend(
            [str(Path(java_home) / "bin" / "java"), str(Path(java_home) / "bin" / "java.exe")]
        )
    candidates.append("java")
    return _find_executable("java", candidates)


def find_javac() -> Optional[str]:
    java = find_java()
    if java:
        path = Path(java)
        candidate = path.with_name("javac.exe" if path.suffix.lower() == ".exe" else "javac")
        if candidate.is_file():
            return str(candidate)
    java_home = os.environ.get("JAVA_HOME")
    candidates = []
    if java_home:
        candidates.extend(
            [str(Path(java_home) / "bin" / "javac"), str(Path(java_home) / "bin" / "javac.exe")]
        )
    candidates.append("javac")
    return _find_executable("javac", candidates)


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def sha256_lines(lines: Iterable[str]) -> str:
    hasher = hashlib.sha256()
    for line in sorted(lines):
        hasher.update(line.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


#: Official release artifacts and their upstream URLs (same hashes as
#: beastxpy/tools/build_jars.py).  ``sha256`` is the distribution-archive
#: digest, ``jar_sha256`` the digest of ``lib/beast.jar`` inside it; a jar
#: matching ``jar_sha256`` is labelled official-release.
OFFICIAL_RELEASES = {
    "1.10.4": {
        "url": "https://github.com/beast-dev/beast-mcmc/releases/download/v1.10.4/BEAST.v1.10.4.zip",
        "sha256": "05bb929433ad157ed22258908c08c0c04af7a84e1fee0820e03f9f0819a76bfa",
        "jar_sha256": "0f6eb4fdf7832c511aa8a1a99c233b88bec844698ae9322dabf8e74b156ae3b0",
    },
    "10.5.0": {
        "url": "https://github.com/beast-dev/beast-mcmc/releases/download/v10.5.0/BEAST.X.v10.5.0.zip",
        "sha256": "d46c99e84d915d736520b2fe6bc43f01f3043f75d1ce7e4b6bcf9a1d6b68564a",
        "jar_sha256": "df11dea1d7118ff1570ad687621912b776fb683c5023333f3dbf2bc762ee136d",
    },
}


# ---------------------------------------------------------------------------
# BEAST installation / source discovery
# ---------------------------------------------------------------------------
def official_jar(version: str, ref_dir: Optional[Path]) -> Optional[Tuple[Path, str]]:
    tag = version.replace(".", "_")
    candidates: List[Tuple[Path, str]] = []
    for env_key in (f"BEAST_{tag}_HOME", f"BEASTX_{tag}_HOME"):
        value = os.environ.get(env_key)
        if value:
            base = Path(value)
            if base.is_file():
                candidates.append((base, f"env:{env_key}"))
            else:
                candidates.extend(
                    [
                        (base / "lib" / "beast.jar", f"env:{env_key}"),
                        (base / "beast.jar", f"env:{env_key}"),
                    ]
                )
    for env_key in (f"BEASTX_JAR_{tag}", f"BEAST_{tag}_JAR"):
        value = os.environ.get(env_key)
        if value:
            candidates.append((Path(value), f"env:{env_key}"))
    if ref_dir is not None:
        for directory in _official_install_candidates(version, ref_dir):
            candidates.append((directory / "lib" / "beast.jar", "reference-install"))
            candidates.append((directory / "beast.jar", "reference-install"))
    for path, source in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path, source
    return None


def _registered_parser_classes(src_root: Path) -> List[str]:
    """Parser classes loaded by default (release + development stores)."""
    classes: List[str] = []
    for name in ("release_parsers.properties", "development_parsers.properties"):
        path = src_root / "dr" / "app" / "beast" / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                classes.append(line)
    return classes


_BODY_RE = re.compile(r"getParserNames?\s*\([^)]*\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}")
_CONST_RE = re.compile(
    r"\b(?:(?:public|protected|private|static|final)\s+)+String\s+(\w+)\s*=\s*\"([^\"]+)\""
)
_ELEMENT_RULE_RE = re.compile(r"new\s+ElementRule\s*\(\s*([A-Za-z_][\w.]*)\s*,")


def _resolve_reference(
    ref: str,
    owner_constants: Dict[str, str],
    class_files: Dict[str, Path],
    src_root: Path,
) -> Optional[str]:
    if "." in ref:
        parts = ref.split(".")
        if parts[-1] == "" and len(parts) >= 2:
            parts = parts[:-1]
        if len(parts) >= 2:
            class_name, constant = parts[-2], parts[-1]
            source = class_files.get(class_name) or class_files.get(Path(class_name).name)
            if source is not None:
                text = source.read_text(encoding="utf-8", errors="replace")
                for name, value in _CONST_RE.findall(text):
                    if name == constant:
                        return value
            if ref.endswith(".getXMLName()") and len(parts) >= 3:
                enum_name, member = parts[-3], parts[-2]
                enum_file = class_files.get(enum_name)
                if enum_file is not None:
                    enum_text = enum_file.read_text(encoding="utf-8", errors="replace")
                    if re.search(r"return\s+name\(\)\s*\+\s*\"Model\"", enum_text):
                        return member + "Model"
        return None
    return owner_constants.get(ref)


def source_vocabulary(src_root: Path, version: str) -> Tuple[Set[str], Set[str]]:
    """Fallback parser/child vocabulary extracted from the Java sources.

    This is only used when no official jar is available.  It follows the same
    registration list as ``BeastParser`` (release + development) and resolves
    constants in the declaring class, other classes and the ``NucModelType``
    style ``getXMLName()`` enums.
    """
    if not src_root.exists():
        return set(), set()
    classes = _registered_parser_classes(src_root)
    class_files = {path.stem: path for path in src_root.rglob("*.java")}
    per_class: Dict[str, Dict[str, str]] = {}
    bodies: List[Tuple[str, str]] = []
    for class_name in classes:
        path = src_root / (class_name.replace(".", "/") + ".java")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        per_class[class_name] = dict(_CONST_RE.findall(text))
        for match in _BODY_RE.finditer(text):
            bodies.append((class_name, match.group(1)))

    elements: Set[str] = set()
    for class_name, body in bodies:
        literal = re.search(r'return\s*"([^"]+)"\s*;', body)
        if literal:
            elements.add(literal.group(1))
            continue
        ref = re.search(r"return\s+([A-Za-z_][\w.]*)\s*;", body)
        if ref:
            value = _resolve_reference(
                ref.group(1), per_class.get(class_name, {}), class_files, src_root
            )
            if value:
                elements.add(value)

    children: Set[str] = set()
    for class_name in classes:
        path = src_root / (class_name.replace(".", "/") + ".java")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        constants = per_class.get(class_name, {})
        for ref in _ELEMENT_RULE_RE.findall(text):
            if ref.startswith('"') or ref[0].isdigit():
                continue
            value = _resolve_reference(ref, constants, class_files, src_root)
            if value:
                children.add(value)
    return elements, children


def jar_vocabulary(jar: Path, javac: str, java: str, workdir: Path) -> Optional[Dict[str, object]]:
    """Compile and run BxpParserNames against one official jar."""
    classes_dir = workdir / "classes"
    classes_dir.mkdir(parents=True, exist_ok=True)
    compile_proc = _run(
        [
            javac,
            "-encoding",
            "UTF-8",
            "-source",
            "8",
            "-target",
            "8",
            "-cp",
            str(jar),
            "-d",
            str(classes_dir),
            str(PROBE_SOURCE),
        ],
        cwd=workdir,
    )
    if compile_proc.returncode != 0:
        print(
            f"warning: could not compile {PROBE_SOURCE.name}: "
            f"{compile_proc.stderr.strip()[:400]}",
            file=sys.stderr,
        )
        return None
    classpath = os.pathsep.join([str(jar), str(classes_dir)])
    run_proc = _run(
        [java, "-Dfile.encoding=UTF-8", "-cp", classpath, "BxpParserNames"],
        cwd=workdir,
        timeout=180,
    )
    if run_proc.returncode != 0:
        print(
            f"warning: BxpParserNames failed for {jar}: " f"{run_proc.stderr.strip()[:400]}",
            file=sys.stderr,
        )
        return None
    elements: Set[str] = set()
    children: Set[str] = set()
    class_names: Dict[str, Set[str]] = {}
    beast_version = ""
    for line in run_proc.stdout.splitlines():
        fields = line.split("\t")
        if fields[0] == "BEAST_VERSION" and len(fields) >= 2:
            beast_version = fields[1].strip()
        elif fields[0] == "PARSER" and len(fields) >= 3:
            class_name, element = fields[1], fields[2]
            elements.add(element)
            class_names.setdefault(class_name, set()).add(element)
        elif fields[0] == "CHILD" and len(fields) >= 2:
            children.add(fields[1])
    return {
        "elements": elements,
        "children": children,
        "class_names": class_names,
        "beast_version": beast_version,
        "stderr": run_proc.stderr,
    }


# ---------------------------------------------------------------------------
# corpus / project evidence
# ---------------------------------------------------------------------------
def scan_examples(examples_dir: Path) -> Dict[str, object]:
    tags: Set[str] = set()
    child_counts: Dict[str, Dict[str, int]] = {}
    tag_count = 0
    files = 0
    failures = 0
    digest_parts: List[str] = []
    if not examples_dir.exists():
        return {
            "files": 0,
            "parse_failures": 0,
            "tags": tags,
            "child_counts": child_counts,
            "digest": sha256_lines([]),
        }
    for xml_path in sorted(examples_dir.rglob("*.xml")):
        files += 1
        try:
            root = ET.parse(xml_path).getroot()
        except Exception:
            failures += 1
            continue
        if root.tag != "beast":
            failures += 1
            continue
        relative = xml_path.relative_to(examples_dir).as_posix()
        for element in root.iter():
            tags.add(element.tag)
            tag_count += 1
            digest_parts.append(f"{relative}\t{element.tag}")
            if element.tag in STRUCTURAL_PARENTS:
                bucket = child_counts.setdefault(element.tag, {})
                for child in list(element):
                    bucket[child.tag] = bucket.get(child.tag, 0) + 1
    return {
        "files": files,
        "parse_failures": failures,
        "tags": tags,
        "child_counts": child_counts,
        "digest": sha256_lines(digest_parts),
        "tag_occurrences": tag_count,
    }


def scan_project_tags(project_root: Path) -> Set[str]:
    """Element names emitted by ``el("...")`` or ``{"tag": "..."}`` nodes."""
    tags: Set[str] = set()
    for path in sorted((project_root / "beastxpy").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute) else ""
                )
                if name in ("el", "Element") and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        tags.add(first.value)
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "tag"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        tags.add(value.value)
    return tags


# ---------------------------------------------------------------------------
# spec assembly
# ---------------------------------------------------------------------------
def _versions_with(name: str, evidence: Dict[str, Set[str]]) -> List[str]:
    return [
        version
        for version in ("1.10.4", "10.5.0")
        if name in evidence[version] or name in evidence[f"examples:{version}"]
    ]


def _evidence_labels(name: str, evidence: Dict[str, Set[str]], project: Set[str]) -> List[str]:
    labels: List[str] = []
    for version in ("1.10.4", "10.5.0"):
        if name in evidence[version]:
            labels.append(f"parser-or-child:{version}")
    for version in ("1.10.4", "10.5.0"):
        if name in evidence[f"examples:{version}"]:
            labels.append(f"examples:{version}")
    if name in project:
        labels.append("project")
    return labels


def build_spec(
    ref_dir: Path,
    use_jar: bool = True,
    project_root: Path = PROJECT_ROOT,
    javac: Optional[str] = None,
    java: Optional[str] = None,
) -> Dict[str, object]:
    inputs: Dict[str, Dict[str, object]] = {}
    vocabulary: Dict[str, Set[str]] = {"1.10.4": set(), "10.5.0": set()}
    class_names: Dict[str, Dict[str, Set[str]]] = {"1.10.4": {}, "10.5.0": {}}
    temp_root = Path(tempfile.mkdtemp(prefix="bxp-spec-"))
    try:
        for version in ("1.10.4", "10.5.0"):
            source_root = ref_dir / f"beast-mcmc-{version}" / "src"
            jar_info = official_jar(version, ref_dir) if use_jar else None
            parser_source = "source-tree"
            jar_path: Optional[Path] = None
            jar_digest = ""
            parser_count = 0
            child_count = 0
            beast_version = version
            if jar_info is not None and javac and java:
                jar_path, jar_source = jar_info
                result = jar_vocabulary(
                    jar_path, javac, java, temp_root / version.replace(".", "_")
                )
                if result is not None:
                    vocabulary[version] = set(result["elements"]) | set(result["children"])  # type: ignore[arg-type]
                    class_names[version] = result["class_names"]  # type: ignore[assignment]
                    parser_count = len(set(result["elements"]))  # type: ignore[arg-type]
                    child_count = len(set(result["children"]))  # type: ignore[arg-type]
                    beast_version = str(result["beast_version"] or version)
                    parser_source = "official-jar"
                    jar_digest = sha256_file(jar_path)
                    official = OFFICIAL_RELEASES.get(version, {})
                    if official.get("jar_sha256") == jar_digest:
                        jar_source = "official-release"
                        jar_url = official.get("url")
                    else:
                        jar_url = None
                    inputs[version] = {
                        "parser_source": parser_source,
                        "beast_version": beast_version,
                        "jar": jar_source,
                        "jar_source": "official-release" if jar_url else "local-install",
                        "jar_url": jar_url,
                        "jar_sha256": jar_digest,
                        "parser_names": parser_count,
                        "syntax_child_names": child_count,
                    }
            if parser_source == "source-tree":
                elements, children = source_vocabulary(source_root, version)
                vocabulary[version] = elements | children
                parser_count = len(elements)
                child_count = len(children)
                inputs[version] = {
                    "parser_source": parser_source,
                    "beast_version": version,
                    "jar": None,
                    "jar_sha256": "",
                    "parser_names": parser_count,
                    "syntax_child_names": child_count,
                    "source_root": f"beast-mcmc-{version}/src",
                }

            examples_dirs = [
                directory / "examples"
                for directory in _official_install_candidates(version, ref_dir)
            ]
            examples_dir = next((d for d in examples_dirs if d.exists()), examples_dirs[0])
            corpus = scan_examples(examples_dir)
            inputs[version].update(
                {
                    "examples_dir": (
                        examples_dir.name if examples_dir.exists() else str(examples_dir)
                    ),
                    "example_files": corpus["files"],
                    "example_parse_failures": corpus["parse_failures"],
                    "example_tags": len(corpus["tags"]),  # type: ignore[arg-type]
                    "element_set_sha256": sha256_lines(
                        set(corpus["tags"]) | set(vocabulary[version])  # type: ignore[arg-type]
                    ),
                    "example_tag_sha256": corpus["digest"],
                }
            )
            vocabulary[f"examples:{version}"] = set(corpus["tags"])  # type: ignore[arg-type]
            inputs[version]["child_evidence"] = corpus["child_counts"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    project_tags = scan_project_tags(project_root)
    evidence: Dict[str, Set[str]] = {
        "1.10.4": vocabulary["1.10.4"],
        "10.5.0": vocabulary["10.5.0"],
        "examples:1.10.4": vocabulary["examples:1.10.4"],
        "examples:10.5.0": vocabulary["examples:10.5.0"],
    }
    all_evidenced = (
        set(evidence["1.10.4"])
        | set(evidence["10.5.0"])
        | set(evidence["examples:1.10.4"])
        | set(evidence["examples:10.5.0"])
    )
    project_only = sorted(project_tags - all_evidenced)

    elements: Dict[str, Dict[str, object]] = {}
    for name in sorted(all_evidenced):
        versions = _versions_with(name, evidence)
        if not versions:
            continue
        elements[name] = {
            "versions": versions,
            "since": versions[0],
            "evidence": _evidence_labels(name, evidence, project_tags),
        }

    set_110 = set(evidence["1.10.4"]) | set(evidence["examples:1.10.4"])
    set_105 = set(evidence["10.5.0"]) | set(evidence["examples:10.5.0"])
    removed_in_10_5_0 = sorted(set_110 - set_105)
    added_in_10_5_0 = sorted(set_105 - set_110)

    renames: Dict[str, str] = {}
    for class_name, names_110 in class_names["1.10.4"].items():
        names_105 = class_names["10.5.0"].get(class_name)
        if not names_105 or "$" in class_name:
            continue
        if len(names_110) == 1 and len(names_105) == 1:
            old = next(iter(names_110))
            new = next(iter(names_105))
            if old != new:
                renames[old] = new

    child_evidence = {}
    for parent in STRUCTURAL_PARENTS:
        per_version = {}
        for version in ("1.10.4", "10.5.0"):
            counts = inputs[version].get("child_evidence", {})  # type: ignore[union-attr]
            if isinstance(counts, dict) and parent in counts:
                per_version[version] = dict(sorted(counts[parent].items()))
        if per_version:
            child_evidence[parent] = per_version

    return {
        "schema": SCHEMA,
        "version": SCHEMA_VERSION,
        "provenance": {
            "generator": "tools/patch_xml_spec.py",
            "policy": (
                "elements require official parser-store, parser syntax-rule or "
                "official-example evidence; project-only names are never whitelisted"
            ),
            "inputs": inputs,
            "project": {
                "project_emitted_tags": len(project_tags),
                "project_only_tags": project_only,
                "note": (
                    "project_only tags have no BEAST evidence in either "
                    "distribution and are intentionally rejected by Gate 1"
                ),
            },
            "counts": {
                "elements": len(elements),
                "1.10.4": len(set_110),
                "10.5.0": len(set_105),
                "intersection": len(set_110 & set_105),
            },
            "removed_in_10_5_0": removed_in_10_5_0,
            "added_in_10_5_0": added_in_10_5_0,
            "renames": dict(sorted(renames.items())),
        },
        "structure": {
            "required_attributes": REQUIRED_ATTRIBUTES,
            "attribute_enums": ATTRIBUTE_ENUMS,
            "child_evidence": child_evidence,
        },
        "elements": elements,
    }


def render(spec: Dict[str, object]) -> str:
    return json.dumps(spec, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ref-dir", default=None, help="BeastXPy-参考软件 directory")
    parser.add_argument("--out", default=None, help="output xml_spec.json")
    parser.add_argument(
        "--no-jar",
        action="store_true",
        help="use the source-tree parser fallback even when official jars exist",
    )
    parser.add_argument("--check", action="store_true", help="verify committed spec is current")
    args = parser.parse_args(argv)

    ref_dir = Path(args.ref_dir) if args.ref_dir else DEFAULT_REF_DIR
    env_homes_ready = all(
        (
            os.environ.get(f"BEAST_{version.replace('.', '_')}_HOME")
            and Path(os.environ[f"BEAST_{version.replace('.', '_')}_HOME"]).is_dir()
        )
        for version in ("1.10.4", "10.5.0")
    )
    if not ref_dir.exists() and not env_homes_ready:
        print(
            f"reference directory not found: {ref_dir} (and both "
            "BEAST_1_10_4_HOME / BEAST_10_5_0_HOME are not set)",
            file=sys.stderr,
        )
        return 2
    out = Path(args.out) if args.out else SPEC_PATH

    javac = find_javac()
    java = find_java()
    if not args.no_jar and (not javac or not java):
        print(
            "warning: java/javac not found; falling back to source-tree parser "
            "extraction (provenance will say parser_source=source-tree)",
            file=sys.stderr,
        )

    spec = build_spec(
        ref_dir,
        use_jar=not args.no_jar,
        project_root=PROJECT_ROOT,
        javac=None if args.no_jar else javac,
        java=None if args.no_jar else java,
    )
    rendered = render(spec)

    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current == rendered:
            print(
                f"xml_spec.json is in sync ({len(spec['elements'])} elements, "
                f"{spec['provenance']['counts']['intersection']} shared)"
            )
            return 0
        print(
            "xml_spec.json is OUT OF SYNC; regenerate with " "`python tools/patch_xml_spec.py`",
            file=sys.stderr,
        )
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    counts = spec["provenance"]["counts"]
    print(
        f"wrote {out} ({counts['elements']} elements; "
        f"1.10.4={counts['1.10.4']} 10.5.0={counts['10.5.0']} "
        f"shared={counts['intersection']})"
    )
    project_only = spec["provenance"]["project"]["project_only_tags"]
    if project_only:
        print("project-only tags (rejected by Gate 1): " + ", ".join(project_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
