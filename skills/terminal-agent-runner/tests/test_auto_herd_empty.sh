#!/bin/bash
# tests/test_auto_herd_empty.sh
#
# Regression test for Defect 1:
#   auto_herd.sh must exit NON-ZERO when the fleet has never contained any
#   agent (wrong session name, failed spawn, etc.).
#
# Before the fix, auto_herd.sh printed "Fleet is empty. All tasks completed."
# and exited 0, silently declaring success even though nothing ran.
#
# Strategy: point HERD_BIN at a Python-generated stub herd.sh that:
#   - For "status --json" returns {"session":"test-empty","agents":[]}
#   - For "watch ..." exits 1  (absorbed by "|| true" in auto_herd.sh)
# auto_herd.sh must exit 4 (the new "never-had-agents" code).
#
# No real herdr session is created; this test is fully offline.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_HERD="$SCRIPT_DIR/../scripts/auto_herd.sh"

# ---- scratch space ----------------------------------------------------------
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/test_auto_herd_empty.XXXXXX")"
export AGENT_HERDER_STATE_DIR="$SCRATCH/state"

cleanup() {
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

# ---- write stub herd.sh via python3 (no heredoc needed) --------------------
python3 - "$SCRATCH/herd.sh" << 'PYEOF'
import sys, os, stat
path = sys.argv[1]
content = r"""#!/bin/bash
# Stub herd.sh for test_auto_herd_empty.sh
# Absorbs --session NAME, dispatches on the subcommand.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) shift 2 ;;
    watch) exit 1 ;;
    status)
      printf '{"session":"test-empty","agents":[]}\n'
      exit 0
      ;;
    *) shift ;;
  esac
done
exit 0
"""
with open(path, "w") as f:
    f.write(content)
os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
PYEOF

# ---- run auto_herd.sh with the stub via HERD_BIN ---------------------------
EXIT_CODE=0
OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" bash "$AUTO_HERD" --session "test-empty-session" 2>&1)" || EXIT_CODE=$?

# ---- assertions ------------------------------------------------------------
FAILED=0

if [[ "$EXIT_CODE" -eq 0 ]]; then
  echo "FAIL: auto_herd.sh exited 0 for a session that never had any agent (expected non-zero)"
  FAILED=1
else
  echo "PASS: auto_herd.sh exited $EXIT_CODE (non-zero) for empty session"
fi

# The output must name the session so the operator knows which one to check.
SESSION_LABEL="test-empty-session"
if echo "$OUTPUT" | grep -q "$SESSION_LABEL"; then
  echo "PASS: output names the session '$SESSION_LABEL'"
else
  echo "FAIL: output does not name the session '$SESSION_LABEL'"
  echo "      actual output: $OUTPUT"
  FAILED=1
fi

# The output must indicate that no agent was ever observed (not just "Fleet is empty").
if echo "$OUTPUT" | grep -qi "never\|no agent\|not.*observed\|wrong.*session\|session name"; then
  echo "PASS: output diagnoses the empty-session condition"
else
  echo "FAIL: output does not explain why the fleet is empty"
  echo "      actual output: $OUTPUT"
  FAILED=1
fi

if echo "$OUTPUT" | grep -q "auto_herd.log"; then
  echo "PASS: output points to auto_herd.log"
else
  echo "FAIL: output does not point to auto_herd.log"
  FAILED=1
fi

if [[ "$FAILED" -eq 0 ]]; then
  echo "ALL PASS: auto_herd.sh correctly rejects an empty-from-the-start fleet"
  exit 0
else
  exit 1
fi
