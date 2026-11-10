#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERD_SH="$SCRIPT_DIR/../scripts/herd.sh"
CLEANUP_SH="$SCRIPT_DIR/../scripts/cleanup_fleet.sh"

TEST_SESSION="test-fleet-$RANDOM"

echo "Running herd.sh with --session $TEST_SESSION (un-attached default behavior)"
bash "$HERD_SH" --session "$TEST_SESSION" init
bash "$HERD_SH" --session "$TEST_SESSION" spawn --name isolated_agent_99 --cli claude --task "echo test" --read-only

echo "Verifying server is running for un-attached session..."
if ! herdr --session "$TEST_SESSION" status server --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('status')=='running' else 1)" 2>/dev/null; then
  echo "FAIL: Un-attached server not running!"
  exit 1
fi

echo "Verifying it is not in default session..."
if herdr pane list 2>/dev/null | grep -q "isolated_agent_99"; then
  echo "FAIL: Found agent in default session! It should be un-attached."
  bash "$CLEANUP_SH" --session "$TEST_SESSION"
  exit 1
fi

echo "PASS: Fleet started in an isolated, un-attached session."

bash "$CLEANUP_SH" --session "$TEST_SESSION"
