#!/usr/bin/env bash
# test_preflight_unattached.sh
#
# Regression test: preflight.sh must succeed (exit 0) for an un-attached/headless
# session with HERDR_ENV unset.  This is Rule 1's DEFAULT workflow -- the herding
# agent runs outside any Herdr pane and drives a headless herdr server via herd.sh.
#
# Defect guarded: preflight.sh formerly hard-exited on "HERDR_ENV != 1" with a
# message telling operators to "attach Herdr first", which contradicts Rule 1.
#
# Usage:  bash tests/test_preflight_unattached.sh
# Exit:   0 PASS | 1 FAIL.  Cleans up any herdr session it creates.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PREFLIGHT="$SCRIPT_DIR/../scripts/preflight.sh"

# Use PID for uniqueness; keep name alphanumeric-leading for session validation.
TEST_SESSION="pftest$$"
HERDR_PID=""

cleanup() {
  local exit_code=$?
  echo
  echo "-- cleanup --"
  if [ -n "$HERDR_PID" ] && kill -0 "$HERDR_PID" 2>/dev/null; then
    kill "$HERDR_PID" 2>/dev/null || true
    wait "$HERDR_PID" 2>/dev/null || true
    echo "Stopped herdr server (PID=$HERDR_PID)"
  fi
  # Belt-and-suspenders: ask herdr to stop the named session if the process
  # is already gone (e.g. the server exited on its own).
  herdr --session "$TEST_SESSION" server stop 2>/dev/null || true
  if [ "$exit_code" -eq 0 ]; then
    echo "PASS: test_preflight_unattached"
  else
    echo "FAIL: test_preflight_unattached (exit $exit_code)"
  fi
  exit "$exit_code"
}
trap cleanup EXIT

echo "=== test_preflight_unattached ==="
echo "Session:   $TEST_SESSION"
echo "HERDR_ENV: (unset -- simulating un-attached default workflow)"

# -- 1. Ensure HERDR_ENV is NOT inherited from any outer environment -----------
unset HERDR_ENV

# -- 2. Start a headless herdr server for the test session ---------------------
# herd.sh does exactly this on first use; we replicate it here to keep the test
# self-contained and independent of herd.sh.
nohup herdr --session "$TEST_SESSION" server > /dev/null 2>&1 &
HERDR_PID=$!
echo "Started herdr server (PID=$HERDR_PID)"

# -- 3. Wait up to 5 s for the named-session socket to appear -----------------
SOCK="$HOME/.config/herdr/sessions/$TEST_SESSION/herdr.sock"
waited=0
until [ -S "$SOCK" ]; do
  sleep 0.2
  waited=$((waited + 1))
  if [ "$waited" -ge 25 ]; then
    echo "FAIL: herdr socket did not appear at $SOCK within 5 s"
    exit 1
  fi
done
echo "Socket ready: $SOCK"

# -- 4. Run preflight WITHOUT HERDR_ENV set ------------------------------------
# --no-quota skips the expensive interactive CLI probes (auth/quota check).
# Choose an available CLI to verify the unattached preflight pass path.
#
# Key assertion: must exit 0 without the old "HERDR_ENV != 1" hard failure.
CHOSEN_CLI=""
for c in claude agy omp codex; do
  if command -v "$c" >/dev/null 2>&1; then
    CHOSEN_CLI="$c"
    break
  fi
done
[ -n "$CHOSEN_CLI" ] || { echo "FAIL: no supported agent CLI found on PATH"; exit 1; }

echo
echo "Running: bash preflight.sh --session $TEST_SESSION --no-quota --cli $CHOSEN_CLI --allow-cross-session"
echo

bash "$PREFLIGHT" --session "$TEST_SESSION" --no-quota --cli "$CHOSEN_CLI" --allow-cross-session

echo
echo "Running: bash preflight.sh --session $TEST_SESSION --no-quota --cli $CHOSEN_CLI --allow-cross-session --json"
echo

JSON_OUT="$(bash "$PREFLIGHT" --session "$TEST_SESSION" --no-quota --cli "$CHOSEN_CLI" --allow-cross-session --json)"
STATUS="$(printf '%s' "$JSON_OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status'] == 'passed' and d['exit_code'] == 0 and d['environment']['mode'] == 'unattached'; print(d['status'])")"
[ "$STATUS" = "passed" ] || { echo "FAIL: --json output validation failed"; exit 1; }

