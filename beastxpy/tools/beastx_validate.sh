#!/usr/bin/env bash
# beastx_validate.sh - POSIX Gate-3 validation wrapper for BeastXPy.
#
# Windows users should use beastx_validate.cmd (cmd.exe, no Git Bash needed)
# or one of the cross-platform Python entry points:
#
#   python -m beastxpy.main validate --xml out.xml --beast
#   python tools/official_validate.py out.xml --mode java-core
#
# This script is a thin convenience wrapper around the compiled
# BeastXValidator class:
#
#   beastx_validate.sh XML_PATH [--beast-version 1.10.4|10.5.0] [BeastXValidator args...]
#
# Environment overrides:
#   BEASTX_JAVA_CANDIDATES    space-separated list of java binaries to try first
#   BEASTX_JAVA               explicit java binary
#   BEASTX_JAR                external beast.jar override (regression testing)
#   BEASTX_SKIP_JDK_PROBES    non-empty -> do not probe, use first available java
#   BEASTX_VALIDATE_TRACE     non-empty -> trace-level diagnostics to stderr
#   BEASTX_VALIDATE_JAR_DIR   directory holding beast-<ver>.jar
#   BEASTX_VALIDATE_CLASSES   validator class directory override
#
# Exit code: 0 = valid, 1 = invalid, 2 = environment missing.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XML_PATH="${1:-}"
if [[ -z "$XML_PATH" ]]; then
  echo "usage: beastx_validate.sh XML_PATH [--beast-version 1.10.4|10.5.0] [args...]" >&2
  exit 2
fi
shift

BEAST_VERSION="1.10.4"
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --beast-version)
      BEAST_VERSION="$2"; shift 2;;
    *)
      EXTRA_ARGS+=("$1"); shift;;
  esac
done

if [[ "$BEAST_VERSION" != "1.10.4" && "$BEAST_VERSION" != "10.5.0" ]]; then
  echo "beastx_validate: invalid --beast-version '$BEAST_VERSION' (1.10.4 or 10.5.0)" >&2
  exit 2
fi

# ---- resolve the beast jar --------------------------------------------------
if [[ -n "${BEASTX_JAR:-}" && -f "$BEASTX_JAR" ]]; then
  JAR="$BEASTX_JAR"
else
  JAR_DIR="${BEASTX_VALIDATE_JAR_DIR:-$SCRIPT_DIR/jars}"
  JAR="$JAR_DIR/beast-$BEAST_VERSION.jar"
fi
if [[ ! -f "$JAR" ]]; then
  echo "beastx_validate: beast jar not found: $JAR" >&2
  echo "  (set BEASTX_JAR or BEASTX_VALIDATE_JAR_DIR, or run build_jars.py)" >&2
  exit 2
fi

# ---- resolve java -----------------------------------------------------------
if [[ -n "${BEASTX_SKIP_JDK_PROBES:-}" ]]; then
  JAVA_BIN="${BEASTX_JAVA:-java}"
else
  JAVA_BIN=""
  for candidate in ${BEASTX_JAVA_CANDIDATES:-}; do
    if command -v "$candidate" >/dev/null 2>&1; then JAVA_BIN="$candidate"; break; fi
  done
  if [[ -z "$JAVA_BIN" ]]; then
    if [[ -n "${BEASTX_JAVA:-}" ]]; then
      JAVA_BIN="$BEASTX_JAVA"
    elif [[ -n "${JAVA_HOME:-}" && -x "$JAVA_HOME/bin/java" ]]; then
      JAVA_BIN="$JAVA_HOME/bin/java"
    elif command -v /usr/libexec/java_home >/dev/null 2>&1; then
      JH="$(/usr/libexec/java_home 2>/dev/null)"
      if [[ -n "$JH" && -x "$JH/bin/java" ]]; then JAVA_BIN="$JH/bin/java"; fi
    fi
  fi
  if [[ -z "$JAVA_BIN" ]]; then
    if command -v java >/dev/null 2>&1; then JAVA_BIN="java"; fi
  fi
fi
if ! command -v "$JAVA_BIN" >/dev/null 2>&1 && [[ ! -x "$JAVA_BIN" ]]; then
  echo "beastx_validate: no usable Java runtime found (set JAVA_HOME or BEASTX_JAVA)" >&2
  exit 2
fi

# Prefer the validator classes compiled against this exact BEAST version.
CLASSPATH="$JAR"
if [[ -n "${BEASTX_VALIDATE_CLASSES:-}" && -d "$BEASTX_VALIDATE_CLASSES" ]]; then
  CLASSPATH="$CLASSPATH:$BEASTX_VALIDATE_CLASSES"
elif [[ -d "$SCRIPT_DIR/classes/$BEAST_VERSION" ]]; then
  CLASSPATH="$CLASSPATH:$SCRIPT_DIR/classes/$BEAST_VERSION"
elif [[ -d "$SCRIPT_DIR/classes" ]]; then
  CLASSPATH="$CLASSPATH:$SCRIPT_DIR/classes"
fi
if [[ -d "$SCRIPT_DIR/parsers" ]]; then
  CLASSPATH="$CLASSPATH:$SCRIPT_DIR/parsers"
fi

if [[ -n "${BEASTX_VALIDATE_TRACE:-}" ]]; then
  echo "beastx_validate: java=$JAVA_BIN jar=$JAR classpath=$CLASSPATH" >&2
fi

exec "$JAVA_BIN" -Dfile.encoding=UTF-8 -cp "$CLASSPATH" BeastXValidator "$XML_PATH" "${EXTRA_ARGS[@]}"
