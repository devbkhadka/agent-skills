#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Checking Stage 0 touched fixture ==="
python3 "$SKILL_DIR/bin/validate-lexicon" "$SKILL_DIR/tests/cases/stage-0/missing-fact-precedence.lexicon.json" || true

echo "=== Checking Stage 2 touched fixtures ==="
fixtures=(
  "empty-flow-group"
  "s2-flow-steps-discontinuous-real-transitions-wrong-order"
  "state-not-covered-by-flow"
  "s2-flow-outcome-evidence-key-declared-on-another-state"
  "s2-precondition-duplicated-from-group-verbatim"
  "s2-flows-cover-every-state-accepted"
)

for f in "${fixtures[@]}"; do
  echo "--- $f ---"
  python3 "$SKILL_DIR/bin/validate-scenario-matrix" "$SKILL_DIR/tests/cases/stage-2/$f/scenario-matrix.json" || true
done
