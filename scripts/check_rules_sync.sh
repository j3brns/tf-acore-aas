#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"

fail=0

check_required() {
  local path="$1"
  if [[ ! -f "$ROOT/$path" ]]; then
    echo "ERROR: missing required rules file: $path"
    fail=1
  fi
}

check_mirror_set() {
  local canonical="$1"
  shift
  local mirrors=("$@")

  if [[ ! -f "$canonical" ]]; then
    echo "ERROR: canonical rules file missing: ${canonical#$ROOT/}"
    fail=1
    return
  fi

  for mirror in "${mirrors[@]}"; do
    if [[ ! -f "$mirror" ]]; then
      echo "ERROR: mirror rules file missing: ${mirror#$ROOT/}"
      fail=1
      continue
    fi

    if ! cmp -s "$canonical" "$mirror"; then
      echo "ERROR: rules files differ:"
      echo "  canonical: ${canonical#$ROOT/}"
      echo "  mirror:    ${mirror#$ROOT/}"
      sha256sum "$canonical" "$mirror" | sed 's#'"$ROOT/"'##'
      fail=1
    else
      echo "OK: ${canonical#$ROOT/} == ${mirror#$ROOT/} (byte-identical)"
    fi
  done
}

# Required rules files for this repository.
check_required "AGENTS.md"
check_required "ai-context/AGENTS.md"
check_required "CLAUDE.md"
check_required "ai-context/CLAUDE.md"
check_required "GEMINI.md"
check_required "ai-context/GEMINI.md"

# Enforced mirror sets (byte-identical).
check_mirror_set "$ROOT/AGENTS.md" "$ROOT/ai-context/AGENTS.md"
check_mirror_set "$ROOT/CLAUDE.md" "$ROOT/ai-context/CLAUDE.md"
check_mirror_set "$ROOT/GEMINI.md" "$ROOT/ai-context/GEMINI.md"

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "Rules sync audit: PASS"
