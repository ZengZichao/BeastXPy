"""``beastxpy capabilities`` - the feature x BEAST-version support matrix.

Renders ``beastxpy/data/capabilities.yaml``.  That file is the single source of
truth: this module only loads and formats it, so the CLI, the documentation and
the tests cannot disagree about what BeastXPy supports.

The matrix describes XML generation and official-parser acceptance.  A
``supported`` cell is not a claim that a model suits a particular dataset, and
nothing here is a convergence or model-selection statement.
"""

from __future__ import annotations

import argparse
from typing import Any, List

from . import _contracts as C
from .capabilities import (
    CapabilitiesError,
    beast_versions,
    format_table,
    load_capabilities,
    status_counts,
    to_envelope,
)

VERSION_CHOICES = ("1.10.4", "10.5.0", "both")


def register(sub: Any) -> None:
    """Attach the ``capabilities`` command. Called automatically by main.py."""
    p = sub.add_parser(
        "capabilities",
        help="show which features each BEAST version supports",
    )
    p.description = (
        "Print the feature x BEAST-version support matrix read from "
        "beastxpy/data/capabilities.yaml: supported / partial / blocked, whether "
        "native BEAGLE is needed to execute the analysis, the reason for each "
        "cell, and a command that verifies it. The manifest is the single source "
        "of truth used by the CLI, the documentation and the tests."
    )
    p.epilog = "Exit codes: 0 = ok, 2 = the capability manifest is missing or unparsable."
    p.add_argument(
        "--beast-version",
        choices=VERSION_CHOICES,
        default="both",
        help="restrict the table to one BEAST version (default: both)",
    )
    C.add_json_argument(p)
    C.add_quiet_argument(p)
    p.set_defaults(handler=cmd_capabilities)


def _summary(version: str, manifest: dict) -> List[str]:
    lines: List[str] = []
    if version == "both":
        for item in beast_versions(manifest):
            counts = status_counts(item, manifest)
            lines.append(
                f"BEAST {item}: {counts['supported']} supported, "
                f"{counts['partial']} partial, {counts['blocked']} blocked"
            )
    else:
        counts = status_counts(version, manifest)
        lines.append(
            f"BEAST {version}: {counts['supported']} supported, "
            f"{counts['partial']} partial, {counts['blocked']} blocked"
        )
    lines.append(
        "A 'supported' cell means the XML is generated and accepted by the "
        "official BEAST parsers in java-core mode (construct-only; project "
        "TreeLikelihood shim, no likelihood evaluation). Cite --mode beagle "
        "(native BEAGLE) for a full-parser, real-likelihood check. This is not "
        "a convergence or model-selection claim."
    )
    return lines


def cmd_capabilities(args: argparse.Namespace) -> int:
    """Print the matrix; exit 0, or 2 when the manifest cannot be read."""
    manifest = load_capabilities()
    envelope = to_envelope(args.beast_version, manifest)

    if args.json_path:
        C.emit_json(envelope, args.json_path, to_stdout=not args.quiet)
    elif not args.quiet:
        for line in format_table(args.beast_version, manifest):
            print(line)
        print()
        for line in _summary(args.beast_version, manifest):
            print(line)
    return C.EXIT_OK


__all__ = ["VERSION_CHOICES", "cmd_capabilities", "register", "CapabilitiesError"]
