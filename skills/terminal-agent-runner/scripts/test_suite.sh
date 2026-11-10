#!/usr/bin/env bash
# Terminal Agent Runner Test Suite
# Tests isolated safety regressions plus local resource-pool operations.
# It never spawns an agent or touches the live fleet/session.
#
# Usage:
#   bash test_suite.sh [--cli CLI1,CLI2,...] [--session NAME] [--unit-only]
# Examples:
#   bash test_suite.sh --cli codex,agy,omp
#   bash test_suite.sh --cli codex,agy,omp,claude --session test-session

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/terminal-agent-runner-test.XXXXXX")"
TEST_TMP="$(cd "$TEST_TMP" && pwd -P)"
export AGENT_HERDER_POOL_STATE_FILE="$TEST_TMP/pool.json"
export AGENT_HERDER_STATE_DIR="$TEST_TMP/fleet-state"
export PYTHONDONTWRITEBYTECODE=1
FLEET=(python3 "$SCRIPT_DIR/fleet.py")
POOL=(python3 "$SCRIPT_DIR/resource_pool.py")

CLIS="codex,agy,omp"
SESSION="test-session"
UNIT_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --cli)       CLIS="$2"; shift 2 ;;
    --session)   SESSION="$2"; shift 2 ;;
    --unit-only) UNIT_ONLY=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

IFS=',' read -ra CLI_ARRAY <<< "$CLIS"
NUM_AGENTS=${#CLI_ARRAY[@]}

if [ "$NUM_AGENTS" -gt 4 ] || [ "$NUM_AGENTS" -eq 0 ]; then
  echo "Error: Must specify between 1 and 4 agents via --cli (got $NUM_AGENTS)" >&2
  exit 1
fi

echo "=================================================="
echo " Terminal Agent Runner Test Suite"
echo " Configuration: $NUM_AGENTS agent(s) [${CLIS}]"
echo " Session:       $SESSION"
echo " Mode:          $( [ "$UNIT_ONLY" -eq 1 ] && echo "Unit/Mock Test Mode" || echo "Local Integration Mode (no agent spawn)" )"
echo "=================================================="
echo

PASSED=0
FAILED=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  [PASS] $label"
    PASSED=$((PASSED + 1))
  else
    echo "  [FAIL] $label (expected '$expected', got '$actual')"
    FAILED=$((FAILED + 1))
  fi
}

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -q -- "$needle"; then
    echo "  [PASS] $label"
    PASSED=$((PASSED + 1))
  else
    echo "  [FAIL] $label ('$needle' not found in output)"
    FAILED=$((FAILED + 1))
  fi
}

TEST_GROUP="test_suite_group_$$"

# Clean up any leftover test state on exit
cleanup() {
  echo
  echo "== Teardown =="
  "${POOL[@]}" clear-group --group "$TEST_GROUP" >/dev/null 2>&1 || true
  rm -rf "$TEST_TMP" >/dev/null 2>&1 || true
  echo "Test group '$TEST_GROUP' and temporary directories cleaned."
}
trap cleanup EXIT

echo "== 0. Running Isolated Python Regression Tests =="
if python3 -m unittest discover -s "$SKILL_DIR/tests" -p 'test_*.py' -v; then
  echo "  [PASS] isolated safety regressions"
  PASSED=$((PASSED + 1))
else
  echo "  [FAIL] isolated safety regressions"
  FAILED=$((FAILED + 1))
fi

echo

echo "== 1. Testing Resource Lease Operations =="
# Define capacity 1 resource
"${POOL[@]}" define --group "$TEST_GROUP" --resource "file:config.ts" --capacity 1 >/dev/null

# Agent-1 acquires
ACQ1="$("${POOL[@]}" acquire --group "$TEST_GROUP" --resource "file:config.ts" --agent "Agent-1")"
STATUS1="$(echo "$ACQ1" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status'))")"
assert_eq "Agent-1 acquires file:config.ts" "acquired" "$STATUS1"

# Agent-2 attempts acquire (should be locked/queued)
ACQ2="$("${POOL[@]}" acquire --group "$TEST_GROUP" --resource "file:config.ts" --agent "Agent-2" --timeout 0 || true)"
STATUS2="$(echo "$ACQ2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status'))")"
assert_eq "Agent-2 blocked on busy file:config.ts" "locked" "$STATUS2"

# Agent-1 releases
REL1="$("${POOL[@]}" release --group "$TEST_GROUP" --resource "file:config.ts" --agent "Agent-1")"
REL_STATUS1="$(echo "$REL1" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status'))")"
assert_eq "Agent-1 releases file:config.ts" "released" "$REL_STATUS1"

# Agent-2 acquires now
ACQ2_RETRY="$("${POOL[@]}" acquire --group "$TEST_GROUP" --resource "file:config.ts" --agent "Agent-2")"
STATUS2_RETRY="$(echo "$ACQ2_RETRY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status'))")"
assert_eq "Agent-2 acquires file:config.ts after release" "acquired" "$STATUS2_RETRY"

"${POOL[@]}" release --group "$TEST_GROUP" --resource "file:config.ts" --agent "Agent-2" >/dev/null

echo
echo "== 2. Testing Permission Request (Auto-Approve Case) =="
LOW_RISK_CMD="git status"
CLS_LOW="$(python3 -c "
import json
from scripts.fleet import classify_text, load_policy
pol = load_policy()
res = classify_text('$LOW_RISK_CMD', pol)
print(json.dumps(res))
" 2>/dev/null || python3 -c "
import sys, json, pathlib
sys.path.insert(0, '$SCRIPT_DIR')
from fleet import classify_text, load_policy
print(json.dumps(classify_text('$LOW_RISK_CMD', load_policy())))
")"

ACTION_LOW="$(echo "$CLS_LOW" | python3 -c "import sys,json; print(json.load(sys.stdin).get('action'))")"
RULE_LOW="$(echo "$CLS_LOW" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rule'))")"
assert_eq "Low-risk command '$LOW_RISK_CMD' action is approve" "approve" "$ACTION_LOW"
assert_eq "Low-risk command matched git-read rule" "git-read" "$RULE_LOW"

echo
echo "== 3. Testing Permission Deny & Escalation (Delete Folder Case) =="
TEST_DIR="$TEST_TMP/deny-target"
mkdir -p "$TEST_DIR"
DENY_CMD="rm -rf $TEST_DIR"

CLS_HIGH="$(python3 -c "
import sys, json
sys.path.insert(0, '$SCRIPT_DIR')
from fleet import classify_text, load_policy
print(json.dumps(classify_text('$DENY_CMD', load_policy())))
")"

ACTION_HIGH="$(echo "$CLS_HIGH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('action'))")"
RULE_HIGH="$(echo "$CLS_HIGH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rule'))")"
assert_eq "Destructive command '$DENY_CMD' action is escalate" "escalate" "$ACTION_HIGH"
assert_eq "Destructive command matched recursive-delete rule" "recursive-delete" "$RULE_HIGH"

CLS_CHECKOUT="$(python3 -c "
import sys, json
sys.path.insert(0, '$SCRIPT_DIR')
from fleet import classify_text, load_policy
print(json.dumps(classify_text('git checkout -- src/config.ts', load_policy())))
")"
RULE_CHECKOUT="$(echo "$CLS_CHECKOUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rule'))")"
assert_eq "Git checkout is blocked while fleet work may coexist" "git-destructive" "$RULE_CHECKOUT"

# Park Agent holding a lock and verify lock release
"${POOL[@]}" acquire --group "$TEST_GROUP" --resource "file:config.ts" --agent "agent-denytest" >/dev/null
python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from fleet import set_task
set_task('agent-denytest', 'inspect denied command', '$SESSION', '$TEST_GROUP', ['file:config.ts'], False, 'manual')
"
PARK_RES="$(python3 "$SCRIPT_DIR/fleet.py" --session "$SESSION" --allow-cross-session park --agent "agent-denytest" --group "$TEST_GROUP" --reason "Attempted $DENY_CMD" 2>&1 || true)"
assert_contains "Park command released locks" "file:config.ts" "$PARK_RES"

# Task plan verification
TASK_FILE="$TEST_TMP/tasks.json"
cat > "$TASK_FILE" <<JSON
[
  {"id":"safe-task", "agent":"Agent-1", "deps":[], "resources":[], "read_only":true},
  {"id":"blocked-task", "agent":"agent-denytest", "deps":["safe-task"], "resources":["file:config.ts"]}
]
JSON

PLAN_RES="$("${FLEET[@]}" plan --file "$TASK_FILE" --blocked "blocked-task")"
VERDICT="$(echo "$PLAN_RES" | python3 -c "import sys,json; print(json.load(sys.stdin).get('verdict'))")"
assert_eq "Plan verdict keeps safe tasks moving" "work remains" "$VERDICT"
rm -f "$TASK_FILE"

echo
echo "== 4. Testing Force Exit & Ghost Lock Reclaim =="
# Simulate a dead PID holding a lease on a resource
FAKE_PID=999999
"${POOL[@]}" acquire --group "$TEST_GROUP" --resource "shared-db" --agent "Ghost-Agent" --pid "$FAKE_PID" >/dev/null

STATUS_BEFORE="$("${POOL[@]}" status --group "$TEST_GROUP" --no-reap)"
HELD_BEFORE="$(echo "$STATUS_BEFORE" | python3 -c "import sys,json; print(json.load(sys.stdin)['$TEST_GROUP']['resources']['shared-db']['held'])")"
assert_eq "Ghost agent holds resource prior to reap" "1" "$HELD_BEFORE"

REAP_RES="$("${POOL[@]}" reap --group "$TEST_GROUP")"
REAPED_COUNT="$(echo "$REAP_RES" | python3 -c "import sys,json; print(json.load(sys.stdin).get('reaped_count'))")"
assert_eq "Reap reclaims dead process lease immediately" "1" "$REAPED_COUNT"

STATUS_AFTER="$("${POOL[@]}" status --group "$TEST_GROUP")"
HELD_AFTER="$(echo "$STATUS_AFTER" | python3 -c "import sys,json; print(json.load(sys.stdin)['$TEST_GROUP']['resources']['shared-db']['held'])")"
assert_eq "Resource is free after reap" "0" "$HELD_AFTER"

echo
echo "== 5. Testing Codex Adapter, Context Contract & Session Guard =="
CODEX_ARGV="$(python3 -c "
import json, sys
sys.path.insert(0, '$SCRIPT_DIR')
from fleet import cli_argv
print(json.dumps(cli_argv('codex', 'inspect the repo')))
")"
assert_contains "Codex runs inline for readable Herdr output" "--no-alt-screen" "$CODEX_ARGV"
assert_contains "Codex receives its initial task positionally" "inspect the repo" "$CODEX_ARGV"
if [[ "$(uname -s)" == "Darwin" ]]; then
  assert_contains "Codex defaults to automatic approval review" "--ask-for-approval" "$CODEX_ARGV"
else
  assert_contains "Codex defaults to automatic approval review" "--approve-for-me" "$CODEX_ARGV"
fi

CLAUDE_AUTO="$(python3 -c "
import json, sys
sys.path.insert(0, '$SCRIPT_DIR')
from fleet import cli_argv
print(json.dumps(cli_argv('claude', 'edit the file')))
")"
assert_contains "Claude defaults to auto permission mode" "auto" "$CLAUDE_AUTO"

EDIT_MODES="$(python3 -c "
import json, sys
sys.path.insert(0, '$SCRIPT_DIR')
from fleet import cli_argv
print(json.dumps({'codex': cli_argv('codex', 'x', 'edit'), 'claude': cli_argv('claude', 'x', 'edit')}))
")"
assert_contains "Edit mode uses Codex workspace-write sandbox" "workspace-write" "$EDIT_MODES"
assert_contains "Edit mode uses Claude acceptEdits" "acceptEdits" "$EDIT_MODES"

CONTRACT="$(python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from fleet import task_contract
print(task_contract('edit config', ['file:config.ts'], False))
")"
assert_contains "Task contract carries the leased scope" "file:config.ts" "$CONTRACT"
assert_contains "Task contract requires a compact handoff" "FLEET_HANDOFF" "$CONTRACT"

SESSION_GUARD="$(HERDR_SOCKET_PATH=/tmp/current.sock python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from fleet import validate_session_affinity
try:
    validate_session_affinity('other')
except SystemExit as e:
    print(e)
" 2>&1)"
assert_contains "Cross-session fleet targeting is refused" "refusing cross-session" "$SESSION_GUARD"

echo
echo "== 6. Testing Atomic Outbox Event Spool & Notify CLI =="
NOTIFY_RES="$("${FLEET[@]}" --session "$SESSION" --allow-cross-session notify --agent "agent-outbox-test" --kind "task_ready" --payload '{"status":"ready"}' 2>&1)"
assert_contains "Notify CLI confirms event emitted" "notified" "$NOTIFY_RES"
assert_contains "Notify CLI reports agent name" "agent-outbox-test" "$NOTIFY_RES"

OUTBOX_DRAIN="$(python3 -c "
import sys, json
sys.path.insert(0, '$SCRIPT_DIR')
import fleet
fleet.STATE_DIR = fleet.Path('$AGENT_HERDER_STATE_DIR')
events = fleet.drain_events('$SESSION')
print(json.dumps({'count': len(events), 'first_kind': events[0]['kind'] if events else None, 'last_kind': events[-1]['kind'] if events else None}))
")"
assert_eq "Outbox drain count is 2 (parked + notify)" "2" "$(echo "$OUTBOX_DRAIN" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count'))")"
assert_eq "Outbox carries parked event from section 3" "parked" "$(echo "$OUTBOX_DRAIN" | python3 -c "import sys,json; print(json.load(sys.stdin).get('first_kind'))")"
assert_eq "Outbox carries task_ready event from notify" "task_ready" "$(echo "$OUTBOX_DRAIN" | python3 -c "import sys,json; print(json.load(sys.stdin).get('last_kind'))")"

echo
echo "=================================================="
echo " Test Suite Summary: $PASSED Passed, $FAILED Failed"
echo "=================================================="

if [ "$FAILED" -eq 0 ]; then
  exit 0
else
  exit 1
fi
