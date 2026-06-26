#!/usr/bin/env bash
# Print the next zero-padded 4-digit ADR number for docs/adr/.
# Deterministic helper so the next-number is not re-inferred by the model each call.
# Usage: .claude/skills/adr-create/next-number.sh [adr-dir]
#   adr-dir defaults to docs/adr (relative to repo root / CWD).
set -euo pipefail

dir="${1:-docs/adr}"

# Highest existing NNNN prefix among NNNN-*.md files; 0 when the dir is empty/missing.
last=$(ls "$dir"/[0-9][0-9][0-9][0-9]-*.md 2>/dev/null \
  | sed -E 's|.*/([0-9]{4})-.*|\1|' \
  | sort -n \
  | tail -1)

printf '%04d\n' $(( 10#${last:-0} + 1 ))
