"""Coverage extension plugins for BEAST analysis families.

The core :class:`beastxpy.model.ModelAssembler` emits a standard
divergence-time analysis.  A plugin in this package extends that document in
place when the YAML selects a non-standard analysis::

    analysis:
      type: discrete_trait
      ...

Every plugin module exposes:

``ANALYSIS_TYPES``
    Tuple of type names it handles.
``validate(raw, errors)``
    Gate-0 validation; append human-readable messages to ``errors``.
``apply(root, analysis, config, assembler)``
    Mutate the assembled ``<beast>`` structure tree (plain dicts) in place
    and return it.  ``assembler`` exposes the parameter registry and the
    standard builders so plugins can add operators, priors and loggers.

Plugins are discovered automatically; adding a module is enough (no central
registry edit).
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Dict, List, Optional, Tuple

StrTree = Dict[str, Any]

_PLUGIN_CACHE: Optional[Dict[str, Any]] = None


def _plugins() -> Dict[str, Any]:
    global _PLUGIN_CACHE
    if _PLUGIN_CACHE is not None:
        return _PLUGIN_CACHE
    found: Dict[str, Any] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        for analysis_type in getattr(module, "ANALYSIS_TYPES", ()):
            found[analysis_type] = module
    _PLUGIN_CACHE = found
    return found


def supported_analysis_types() -> Tuple[str, ...]:
    return tuple(sorted(_plugins()))


def validate_analysis(analysis_type: str, raw: Dict[str, Any], errors: List[str]) -> None:
    plugin = _plugins().get(analysis_type)
    if plugin is None:
        errors.append(
            f"analysis.type: unknown {analysis_type!r}; supported: "
            + ", ".join(supported_analysis_types())
        )
        return
    plugin.validate(raw, errors)


def apply_coverage(root: StrTree, config: Any, assembler: Any) -> StrTree:
    analysis = getattr(config, "analysis", None) or {}
    analysis_type = analysis.get("type")
    if not analysis_type or analysis_type == "standard":
        return root
    plugin = _plugins().get(str(analysis_type))
    if plugin is None:
        raise ValueError(
            f"unknown analysis type {analysis_type!r}; supported: "
            + ", ".join(supported_analysis_types())
        )
    result = plugin.apply(root, analysis, config, assembler)
    return result if result is not None else root


# ---- generic structure-tree helpers shared by plugins ---------------------


def children(root: StrTree) -> List[StrTree]:
    return root.setdefault("children", [])


def find_all(root: StrTree, tag: str) -> List[StrTree]:
    found: List[StrTree] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("tag") == tag:
            found.append(node)
        stack.extend(node.get("children") or [])
    return found


def find_one(root: StrTree, tag: str) -> Optional[StrTree]:
    matches = find_all(root, tag)
    return matches[0] if matches else None


def remove_all(root: StrTree, tag: str) -> None:
    def prune(node: StrTree) -> None:
        if node.get("children"):
            node["children"] = [child for child in node["children"] if child.get("tag") != tag]
            for child in node["children"]:
                prune(child)

    prune(root)


def append_child(root: StrTree, tag: str, node: StrTree) -> StrTree:
    parent = find_one(root, tag)
    if parent is None:
        raise ValueError(f"cannot append to missing <{tag}>")
    parent.setdefault("children", []).append(node)
    return parent
