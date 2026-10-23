#!/usr/bin/env bash
# Convenience entry point for extract_xml_spec.py.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/extract_xml_spec.py" "$@"