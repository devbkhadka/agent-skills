#!/bin/bash
# tests/test_auto_herd_telemetry.sh
#
# Tests:
# 1. auto_herd.sh writes structured wave telemetry to wave_completions.jsonl on successful wave completion.
# 2. auto_herd.sh --json outputs structured JSON on wave completion.
# 3. auto_herd.sh rejects empty fleet when closure errors were recorded.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_HERD="$SCRIPT_DIR/../scripts/auto_herd.sh"

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/test_auto_herd_telemetry.XXXXXX")"
export AGENT_HERDER_STATE_DIR="$SCRATCH/state"
mkdir -p "$AGENT_HERDER_STATE_DIR"
export HERD_BOOTSTRAP_LOCK_TTL=5

cleanup() {
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

export AUTO_HERD_STEP1="$SCRATCH/auto_herd_test_step.txt"
rm -f "$AUTO_HERD_STEP1"
cat << 'EOF_HERD' > "$SCRATCH/herd.sh"
#!/bin/bash
STEP_FILE="${AUTO_HERD_STEP1:-/tmp/auto_herd_test_step.txt}"
STEP=0
if [[ -f "$STEP_FILE" ]]; then
  STEP=$(cat "$STEP_FILE")
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) shift 2 ;;
    watch) exit 0 ;;
    status)
      if [[ "$STEP" -eq 0 ]]; then
        echo 1 > "$STEP_FILE"
        printf '{"session":"test-tel","agents":[{"agent":"bead-1","state":"working","health":"ok"}]}\n'
      elif [[ "$STEP" -eq 1 ]]; then
        echo 2 > "$STEP_FILE"
        printf '{"session":"test-tel","agents":[{"agent":"bead-1","state":"idle","health":"ok","has_handoff":true}]}\n'
      else
        printf '{"session":"test-tel","agents":[]}\n'
      fi
      exit 0
      ;;
    read)
      printf 'Done\nFLEET_HANDOFF: {"status":"completed","decisions":["ok"],"changed_files":[],"verification":[{"command":"pytest","exit_code":0,"output":"1 passed"}],"blockers":[],"next_inputs":[]}\n'
      exit 0
      ;;
    complete)
      printf '{"status":"ok","agent":"bead-1","handoff":{"status":"completed","decisions":["ok"],"changed_files":[],"verification":[{"command":"pytest","exit_code":0,"output":"1 passed"}],"blockers":[],"next_inputs":[]}}\n'
      exit 0
      ;;
    *) shift ;;
  esac
done
exit 0
EOF_HERD
chmod +x "$SCRATCH/herd.sh"

# Run auto_herd.sh with --json
OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" bash "$AUTO_HERD" --session "test-tel" --json 2>&1)"

# Verify JSON output from stdout
python3 -c '
import json, sys
data = json.loads(sys.argv[1])
assert data["session"] == "test-tel", f"Wrong session: {data}"
assert data["status"] == "completed", f"Wrong status: {data}"
assert data["completed_agents"] == 1, f"Wrong count: {data}"
assert "duration_seconds" in data, f"Missing duration: {data}"
assert "timestamp" in data, f"Missing timestamp: {data}"
print("PASS: --json emitted valid wave telemetry")
' "$OUTPUT"

# Verify log file
LOG_FILE="$SCRATCH/state/test-tel/wave_completions.jsonl"
if [[ -f "$LOG_FILE" ]]; then
  echo "PASS: wave_completions.jsonl exists"
  python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    records = [json.loads(line) for line in f if line.strip()]
assert len(records) == 1, f"Expected 1 record, got {len(records)}"
assert records[0]["session"] == "test-tel"
print("PASS: wave_completions.jsonl record verified")
' "$LOG_FILE"
else
  echo "FAIL: wave_completions.jsonl not found at $LOG_FILE"
  exit 1
fi

# 2. Test closure failure propagation:
# When --project is passed and closing the bead fails, auto_herd.sh must exit 1.
FAKE_REPO="$SCRATCH/fake_repo"
mkdir -p "$FAKE_REPO/.beads"
cat << 'EOF_SCHED' > "$SCRATCH/scheduler.py"
#!/usr/bin/env python3
import sys, json
if len(sys.argv) > 1 and sys.argv[1] == "resolve-pane":
    print(json.dumps({"bead_id": "task-99"}))
    sys.exit(0)
if len(sys.argv) > 1 and sys.argv[1] == "close":
    sys.stderr.write("Simulated scheduler failure: Bead verification checksum mismatch\n")
    sys.exit(1)
sys.exit(0)
EOF_SCHED
chmod +x "$SCRATCH/scheduler.py"

export AUTO_HERD_STEP_FILE="$SCRATCH/auto_herd_test_step2.txt"
rm -f "$AUTO_HERD_STEP_FILE"
cat << 'EOF_HERD2' > "$SCRATCH/herd2.sh"
#!/bin/bash
STEP_FILE="${AUTO_HERD_STEP_FILE:-/tmp/auto_herd_test_step2.txt}"
STEP=0
if [[ -f "$STEP_FILE" ]]; then
  STEP=$(cat "$STEP_FILE")
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) shift 2 ;;
    watch) exit 0 ;;
    status)
      if [[ "$STEP" -eq 0 ]]; then
        echo 1 > "$STEP_FILE"
        printf '{"session":"test-fail","agents":[{"agent":"bead-task-99","state":"working","health":"ok"}]}\n'
      elif [[ "$STEP" -eq 1 ]]; then
        echo 2 > "$STEP_FILE"
        printf '{"session":"test-fail","agents":[{"agent":"bead-task-99","state":"idle","health":"ok","has_handoff":true}]}\n'
      else
        printf '{"session":"test-fail","agents":[]}\n'
      fi
      exit 0
      ;;
    read)
      printf 'Done\nFLEET_HANDOFF: {"status":"completed","decisions":["ok"],"changed_files":[],"verification":[{"command":"pytest","exit_code":0,"output":"1 passed"}],"blockers":[],"next_inputs":[]}\n'
      exit 0
      ;;
    complete)
      printf '{"status":"ok","agent":"bead-task-99","handoff":{"status":"completed","decisions":["ok"],"changed_files":[],"verification":[{"command":"pytest","exit_code":0,"output":"1 passed"}],"blockers":[],"next_inputs":[]}}\n'
      exit 0
      ;;
    *) shift ;;
  esac
done
exit 0
EOF_HERD2
chmod +x "$SCRATCH/herd2.sh"

FAIL_EXIT=0
FAIL_OUTPUT="$(HERD_BIN="$SCRATCH/herd2.sh" SCHED_BIN="$SCRATCH/scheduler.py" bash "$AUTO_HERD" --session "test-fail" --project "$FAKE_REPO" 2>&1)" || FAIL_EXIT=$?

if [[ "$FAIL_EXIT" -ne 0 ]]; then
  echo "PASS: auto_herd.sh exited non-zero ($FAIL_EXIT) when bead closure failed"
else
  echo "FAIL: auto_herd.sh exited 0 despite bead closure failure! Output: $FAIL_OUTPUT"
  exit 1
fi

if echo "$FAIL_OUTPUT" | grep -qi "closure failed"; then
  echo "PASS: failure output includes closure failed diagnostic"
else
  echo "FAIL: failure output missing closure failure info: $FAIL_OUTPUT"
  exit 1
fi

echo "ALL PASS: test_auto_herd_telemetry passed"
exit 0
