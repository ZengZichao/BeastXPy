"""``beastxpy container`` - print the bundled container definitions.

Prints ``deploy/Dockerfile`` or ``deploy/apptainer.def`` verbatim so the file
you read in the repository is exactly the file you pipe into a builder.  This
command never builds anything and never contacts a registry.

The definitions live at the top level of the source tree rather than inside the
package: they are packaging inputs, not runtime data.  An installed wheel
therefore has no definitions to print, and the command says so.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

from . import _contracts as C

KINDS: Dict[str, str] = {
    "docker": "Dockerfile",
    "apptainer": "apptainer.def",
}


def register(sub: Any) -> None:
    """Attach the ``container`` command. Called automatically by main.py."""
    p = sub.add_parser(
        "container",
        help="print the bundled Docker/Apptainer definition",
    )
    p.description = (
        "Print deploy/Dockerfile or deploy/apptainer.def to stdout, byte for "
        "byte as it appears in the repository. The image contains Python, a "
        "JDK 17, both BEAST jars and native BEAGLE, runs as a non-root user and "
        "uses `beastxpy` as its entrypoint. This command does not build."
    )
    p.epilog = (
        "Exit codes: 0 = definition printed, 1 = missing --print, "
        "2 = the definition file is not available."
    )
    p.add_argument(
        "--print",
        dest="kind",
        choices=sorted(KINDS),
        required=True,
        metavar="{docker,apptainer}",
        help="which definition to print",
    )
    p.set_defaults(handler=cmd_container)


def definition_path(kind: str) -> Path:
    """Return the bundled definition path, or raise with the next step."""
    name = KINDS.get(kind)
    if name is None:
        raise C.ContractError(
            f"unknown container kind {kind!r}",
            fix="Use --print docker or --print apptainer.",
        )
    root = Path(__file__).resolve().parent.parent
    candidate = root / "deploy" / name
    if candidate.is_file():
        return candidate
    raise C.ContractError(
        f"container definition not found: {C.display_path(candidate)}",
        fix=(
            "Run this from a source checkout (the deploy/ directory sits next to "
            "beastxpy/); an installed wheel does not ship the container "
            "definitions."
        ),
    )


def cmd_container(args: argparse.Namespace) -> int:
    """Write the requested definition to stdout; never build."""
    path = definition_path(args.kind)
    payload = path.read_bytes()
    # Emit the file's exact bytes. Text-mode stdout would translate "\n" to
    # "\r\n" on Windows, so the output would no longer be the file you build.
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(payload)
        buffer.flush()
    else:  # pragma: no cover - only for a replaced, text-only stdout
        sys.stdout.write(payload.decode("utf-8"))
        sys.stdout.flush()
    return C.EXIT_OK


__all__ = ["KINDS", "cmd_container", "definition_path", "register"]
