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

# Required rules files for this repository.
check_required "AGENTS.md"
check_required "CLAUDE.md"
check_required "GEMINI.md"

if [[ -d "$ROOT/ai-context" ]]; then
  echo "ERROR: retired rules directory still exists: ai-context"
  fail=1
fi

if ! grep -q "Read \[CLAUDE.md\](CLAUDE.md)" "$ROOT/AGENTS.md"; then
  echo "ERROR: AGENTS.md must point to CLAUDE.md"
  fail=1
fi

if ! grep -q "Read \[CLAUDE.md\](CLAUDE.md)" "$ROOT/GEMINI.md"; then
  echo "ERROR: GEMINI.md must point to CLAUDE.md"
  fail=1
fi

if cmp -s "$ROOT/AGENTS.md" "$ROOT/CLAUDE.md"; then
  echo "ERROR: AGENTS.md must not duplicate CLAUDE.md"
  fail=1
fi

if cmp -s "$ROOT/GEMINI.md" "$ROOT/CLAUDE.md"; then
  echo "ERROR: GEMINI.md must not duplicate CLAUDE.md"
  fail=1
fi

if ! cmp -s "$ROOT/AGENTS.md" "$ROOT/GEMINI.md"; then
  echo "ERROR: AGENTS.md and GEMINI.md should stay aligned as pointer stubs"
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "Rules sync audit: PASS"
