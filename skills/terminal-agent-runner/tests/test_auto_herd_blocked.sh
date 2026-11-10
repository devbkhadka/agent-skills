#!/bin/bash
# tests/test_auto_herd_blocked.sh
#
# Regression test for blocked worker survival:
#   auto_herd.sh must NOT abort the whole supervision loop when a single agent
#   escalates/blocks. It must set the blocked agent aside, continue servicing
#   other working/completable agents to completion, and return exit code 1
#   only when all remaining agents are blocked.
#
# Test cases:
#   1. Mixed fleet (1 blocked agent that escalates, 1 working agent that becomes completable):
#      - auto_herd.sh must not exit on the first escalation.
#      - the completable agent must be successfully completed via `herd.sh complete`.
#      - `herd.sh approve` must not be called repeatedly on the blocked agent.
#      - final exit code must be 1 (stopped, human needed).
#      - output must name the blocked agent and why it is blocked.
#   2. Empty first poll regression:
#      - auto_herd.sh must exit 4 on an empty fleet from the start.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_HERD="$SCRIPT_DIR/../scripts/auto_herd.sh"

# ---- scratch space ----------------------------------------------------------
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/test_auto_herd_blocked.XXXXXX")"
export AGENT_HERDER_STATE_DIR="$SCRATCH/state"

cleanup() {
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

# ---- write stateful stub herd.sh via python3 --------------------------------
python3 - "$SCRATCH/herd.sh" "$SCRATCH" << 'PYEOF'
import sys, os, stat
path = sys.argv[1]
scratch = sys.argv[2]

content = r"""#!/bin/bash
# Stub herd.sh for test_auto_herd_blocked.sh
SCRATCH="@SCRATCH@"

SESSION_NAME=""
SUBCMD=""
AGENT_ARG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)
      SESSION_NAME="$2"
      shift 2
      ;;
    --agent)
      AGENT_ARG="$2"
      shift 2
      ;;
    watch|status|approve|complete|read|prompt)
      SUBCMD="$1"
      shift
      ;;
    *)
      shift
      ;;
  esac
done

if [[ "$SUBCMD" == "watch" ]]; then
  exit 0
fi

if [[ "$SUBCMD" == "read" ]]; then
  if [[ -f "$SCRATCH/interrupted_mode" && "$AGENT_ARG" == "agent-interrupted" ]]; then
    echo "Tool call was cancelled by user"
    exit 0
  fi
  if [[ -f "$SCRATCH/blocked_handoff_mode" && "$AGENT_ARG" == "agent-blocked-handoff" ]]; then
    echo 'FLEET_HANDOFF: {"status": "blocked", "decisions": [], "changed_files": [], "verification": [], "blockers": ["lease too narrow for spec files"], "next_inputs": []}'
    exit 0
  fi
  if [[ "$AGENT_ARG" == "agent-completable" || "$AGENT_ARG" == "agent-transient" || "$AGENT_ARG" == "agent-repo" || "$AGENT_ARG" == "bead-short-map" ]]; then
    echo 'FLEET_HANDOFF: {"status": "completed", "decisions": ["done"], "changed_files": [], "verification": [{"command": "test", "exit_code": 0, "output": "ok"}], "blockers": [], "next_inputs": []}'
  fi
  exit 0
fi

if [[ "$SUBCMD" == "prompt" ]]; then
  echo "$AGENT_ARG" >> "$SCRATCH/prompt_calls.txt"
  exit 0
fi

if [[ "$SUBCMD" == "approve" ]]; then
  echo "$AGENT_ARG" >> "$SCRATCH/approve_calls.txt"
  if [[ "$AGENT_ARG" == "agent-blocked" ]]; then
    # Policy refuses approval (escalation)
    exit 2
  else
    exit 0
  fi
fi

if [[ "$SUBCMD" == "complete" ]]; then
  echo "$AGENT_ARG" >> "$SCRATCH/complete_calls.txt"
  if [[ "$AGENT_ARG" == "agent-completable" ]]; then
    touch "$SCRATCH/agent-completable.completed"
    exit 0
  elif [[ "$AGENT_ARG" == "agent-transient" ]]; then
    touch "$SCRATCH/agent-transient.completed"
    exit 0
  elif [[ "$AGENT_ARG" == "agent-repo" ]]; then
    touch "$SCRATCH/agent-repo.completed"
    exit 0
  elif [[ "$AGENT_ARG" == "bead-short-map" ]]; then
    touch "$SCRATCH/bead-short-map.completed"
    echo '{"agent":"bead-short-map","handoff":{"status":"completed","decisions":["done"],"changed_files":[],"verification":[{"command":"test","exit_code":0,"output":"ok"}],"blockers":[],"next_inputs":[]}}'
    exit 0
  else
    exit 1
  fi
fi

if [[ "$SUBCMD" == "status" ]]; then
  if [[ -f "$SCRATCH/interrupted_mode" ]]; then
    cat << JSONEOF
{
  "session": "$SESSION_NAME",
  "agents": [
    {
      "agent": "agent-interrupted",
      "cli": "agy",
      "state": "idle",
      "health": "ok",
      "action": null,
      "rule": null,
      "pending": null
    }
  ]
}
JSONEOF
    exit 0
  fi

  if [[ -f "$SCRATCH/blocked_handoff_mode" ]]; then
    cat << JSONEOF
{
  "session": "$SESSION_NAME",
  "agents": [
    {
      "agent": "agent-blocked-handoff",
      "cli": "codex",
      "state": "idle",
      "health": "ok",
      "action": null,
      "rule": null,
      "pending": null
    }
  ]
}
JSONEOF
    exit 0
  fi

  if [[ -f "$SCRATCH/empty_mode" ]]; then
    printf '{"session":"%s","agents":[]}\n' "$SESSION_NAME"
    exit 0
  fi

  if [[ -f "$SCRATCH/repo_handoff_mode" ]]; then
    if [[ -f "$SCRATCH/agent-repo.completed" ]]; then
      printf '{"session":"%s","agents":[]}\n' "$SESSION_NAME"
    else
      cat << JSONEOF
{
  "session": "$SESSION_NAME",
  "agents": [
    {
      "agent": "agent-repo",
      "cli": "agy",
      "state": "working",
      "health": "ok",
      "action": null,
      "rule": null,
      "pending": null,
      "has_handoff": true
    }
  ]
}
JSONEOF
    fi
    exit 0
  fi

  if [[ -f "$SCRATCH/mapped_pane_mode" ]]; then
    if [[ -f "$SCRATCH/bead-short-map.completed" ]]; then
      printf '{"session":"%s","agents":[]}\n' "$SESSION_NAME"
    else
      printf '{"session":"%s","agents":[{"agent":"bead-short-map","cli":"agy","state":"idle","health":"ok","action":null,"rule":null,"pending":null}]}\n' "$SESSION_NAME"
    fi
    exit 0
  fi

  if [[ -f "$SCRATCH/operational_error_mode" ]]; then
    echo "herdr: error: session '$SESSION_NAME' does not exist" >&2
    exit 2
  fi

  if [[ -f "$SCRATCH/permanent_syntax_mode" ]]; then
    echo "  File \"/path/to/fleet.py\", line 100" >&2
    echo "    SyntaxError: unexpected EOF while parsing" >&2
    exit 1
  fi

  if [[ -f "$SCRATCH/transient_syntax_mode" ]]; then
    TRANSIENT_POLL_FILE="$SCRATCH/transient_poll_count.txt"
    T_POLL=1
    if [[ -f "$TRANSIENT_POLL_FILE" ]]; then
      T_POLL=$(cat "$TRANSIENT_POLL_FILE")
      T_POLL=$((T_POLL + 1))
    fi
    echo "$T_POLL" > "$TRANSIENT_POLL_FILE"

    if [[ "$T_POLL" -eq 1 ]]; then
      # Poll 1: simulate mid-write transient syntax error
      echo "  File \".../skills/terminal-agent-runner/scripts/fleet.py\", line 2138" >&2
      echo "    raise RuntimeError(f\"agent {a.name} did not register\")" >&2
      echo "                                  ^" >&2
      echo "  SyntaxError: '(' was never closed" >&2
      exit 1
    elif [[ -f "$SCRATCH/agent-transient.completed" ]]; then
      # Completed
      printf '{"session":"%s","agents":[]}\n' "$SESSION_NAME"
      exit 0
    else
      # Poll 2+: recovered, agent is completable
      cat << JSONEOF
{
  "session": "$SESSION_NAME",
  "agents": [
    {
      "agent": "agent-transient",
      "cli": "agy",
      "state": "idle",
      "health": "ok",
      "action": null,
      "rule": null,
      "pending": null
    }
  ]
}
JSONEOF
      exit 0
    fi
  fi

  # Check poll count
  POLL_FILE="$SCRATCH/poll_count.txt"
  POLL=1
  if [[ -f "$POLL_FILE" ]]; then
    POLL=$(cat "$POLL_FILE")
    POLL=$((POLL + 1))
  fi
  echo "$POLL" > "$POLL_FILE"

  if [[ -f "$SCRATCH/agent-completable.completed" ]]; then
    # After completable agent is completed and closed, only blocked agent remains
    cat << JSONEOF
{
  "session": "$SESSION_NAME",
  "agents": [
    {
      "agent": "agent-blocked",
      "cli": "agy",
      "state": "blocked",
      "health": "ok",
      "action": "escalate",
      "rule": "no-rm",
      "pending": "Bash command: rm -rf /tmp/important"
    }
  ]
}
JSONEOF
    exit 0
  fi

  if [[ "$POLL" -eq 1 ]]; then
    # Poll 1: agent-blocked is blocked/escalate, agent-completable is working
    cat << JSONEOF
{
  "session": "$SESSION_NAME",
  "agents": [
    {
      "agent": "agent-blocked",
      "cli": "agy",
      "state": "blocked",
      "health": "ok",
      "action": "escalate",
      "rule": "no-rm",
      "pending": "Bash command: rm -rf /tmp/important"
    },
    {
      "agent": "agent-completable",
      "cli": "agy",
      "state": "working",
      "health": "ok",
      "action": null,
      "rule": null,
      "pending": null
    }
  ]
}
JSONEOF
    exit 0
  else
    # Poll 2+: agent-blocked is blocked/escalate, agent-completable finished and is idle
    cat << JSONEOF
{
  "session": "$SESSION_NAME",
  "agents": [
    {
      "agent": "agent-blocked",
      "cli": "agy",
      "state": "blocked",
      "health": "ok",
      "action": "escalate",
      "rule": "no-rm",
      "pending": "Bash command: rm -rf /tmp/important"
    },
    {
      "agent": "agent-completable",
      "cli": "agy",
      "state": "idle",
      "health": "ok",
      "action": null,
      "rule": null,
      "pending": null
    }
  ]
}
JSONEOF
    exit 0
  fi
fi

exit 0
"""

content = content.replace("@SCRATCH@", scratch)
with open(path, "w") as f:
    f.write(content)
os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
PYEOF

# Scheduler stub proves auto_herd resolves the full durable Beads ID before close.
python3 - "$SCRATCH/scheduler.py" "$SCRATCH" << 'PYEOF'
import sys
from pathlib import Path
path, scratch = map(Path, sys.argv[1:])
path.write_text('''import json, sys
from pathlib import Path
scratch = Path({scratch!r})
if "resolve-pane" in sys.argv:
    print(json.dumps({{"pane_id": "bead-short-map", "bead_id": "agent-skills-a-long-durable-bead-id"}}))
elif "close" in sys.argv:
    (scratch / "close_args.json").write_text(json.dumps(sys.argv[1:]))
    print(json.dumps({{"status": "closed"}}))
else:
    raise SystemExit(2)
'''.format(scratch=str(scratch)))
PYEOF

# ---- test 1: mixed fleet with one blocked agent and one completable agent ---
FAILED=0
EXIT_CODE=0
OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" bash "$AUTO_HERD" --session "test-mixed-session" 2>&1)" || EXIT_CODE=$?

echo "--- Run output (Test 1: Mixed Fleet) ---"
echo "$OUTPUT"
echo "----------------------------------------"

# Assertion 1: Final exit code must be 1 (stopped, human needed)
if [[ "$EXIT_CODE" -eq 1 ]]; then
  echo "PASS: auto_herd.sh exited 1 (stopped, human needed)"
else
  echo "FAIL: auto_herd.sh exited $EXIT_CODE (expected 1)"
  FAILED=1
fi

# Assertion 2: The completable agent WAS completed
if [[ -f "$SCRATCH/agent-completable.completed" ]]; then
  echo "PASS: agent-completable was completed"
else
  echo "FAIL: agent-completable was never completed"
  FAILED=1
fi

if echo "$OUTPUT" | grep -q "Agent agent-completable successfully completed."; then
  echo "PASS: output confirms agent-completable completed"
else
  echo "FAIL: output missing completion message for agent-completable"
  FAILED=1
fi

# Assertion 3: Loop did not exit at first escalation
LOG_MIXED="$SCRATCH/state/test-mixed-session/auto_herd.log"
if [[ -f "$LOG_MIXED" ]] && grep -q "Agent agent-blocked requires manual intervention" "$LOG_MIXED" && echo "$OUTPUT" | grep -q "Agent agent-completable successfully completed."; then
  echo "PASS: loop continued past the first escalation and completed the healthy worker"
else
  echo "FAIL: loop did not survive escalation to complete healthy worker"
  FAILED=1
fi

# Assertion 4: Output names the blocked agent and details
if echo "$OUTPUT" | grep -q "agent-blocked"; then
  echo "PASS: output names blocked agent 'agent-blocked'"
else
  echo "FAIL: output does not name 'agent-blocked'"
  FAILED=1
fi

if echo "$OUTPUT" | grep -q "Bash command: rm -rf /tmp/important"; then
  echo "PASS: output describes what agent-blocked is blocked on"
else
  echo "FAIL: output does not describe blocked prompt"
  FAILED=1
fi

if echo "$OUTPUT" | grep -q "auto_herd.log"; then
  echo "PASS: output points to auto_herd.log"
else
  echo "FAIL: output does not point to auto_herd.log"
  FAILED=1
fi

# Assertion 5: Blocked agent is not re-offered to approve in a tight spin
APPROVE_COUNT=0
if [[ -f "$SCRATCH/approve_calls.txt" ]]; then
  APPROVE_COUNT=$(grep -c "^agent-blocked$" "$SCRATCH/approve_calls.txt" || true)
fi
if [[ "$APPROVE_COUNT" -eq 1 ]]; then
  echo "PASS: approve was called exactly once for agent-blocked (no tight spin)"
else
  echo "FAIL: approve was called $APPROVE_COUNT times for agent-blocked (expected 1)"
  FAILED=1
fi

# ---- test 2: empty fleet on initial poll regression test --------------------
touch "$SCRATCH/empty_mode"
EMPTY_EXIT=0
EMPTY_OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" bash "$AUTO_HERD" --session "test-empty-session" 2>&1)" || EMPTY_EXIT=$?

if [[ "$EMPTY_EXIT" -eq 4 ]]; then
  echo "PASS: empty initial fleet exits 4 (regression preserved)"
else
  echo "FAIL: empty initial fleet exited $EMPTY_EXIT (expected 4)"
  FAILED=1
fi
rm -f "$SCRATCH/empty_mode"

# ---- test 3: transient unparseable runner source / worker mid-write --------
touch "$SCRATCH/transient_syntax_mode"
TRANSIENT_EXIT=0
TRANSIENT_OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" bash "$AUTO_HERD" --session "test-transient-session" 2>&1)" || TRANSIENT_EXIT=$?

echo "--- Run output (Test 3: Transient Unparseable Runner) ---"
echo "$TRANSIENT_OUTPUT"
echo "--------------------------------------------------------"

if [[ "$TRANSIENT_EXIT" -eq 0 ]]; then
  echo "PASS: auto_herd.sh survived transient unparseable runner and exited 0 on completion"
else
  echo "FAIL: auto_herd.sh exited $TRANSIENT_EXIT (expected 0)"
  FAILED=1
fi

LOG_TRANSIENT="$SCRATCH/state/test-transient-session/auto_herd.log"
if [[ -f "$LOG_TRANSIENT" ]] && grep -q "Runner source is transiently unparseable or mid-write" "$LOG_TRANSIENT"; then
  echo "PASS: output diagnosed transient unparseable runner state and retried"
else
  echo "FAIL: output did not diagnose transient unparseable runner state in log"
  FAILED=1
fi

if [[ -f "$SCRATCH/agent-transient.completed" ]]; then
  echo "PASS: agent-transient was completed after runner recovery"
else
  echo "FAIL: agent-transient was not completed after runner recovery"
  FAILED=1
fi
rm -f "$SCRATCH/transient_syntax_mode"

# ---- test 4: genuine operational error exits 2 ------------------------------
touch "$SCRATCH/operational_error_mode"
OP_EXIT=0
OP_OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" bash "$AUTO_HERD" --session "test-op-error-session" 2>&1)" || OP_EXIT=$?

echo "--- Run output (Test 4: Genuine Operational Error) ---"
echo "$OP_OUTPUT"
echo "------------------------------------------------------"

if [[ "$OP_EXIT" -eq 2 ]]; then
  echo "PASS: genuine operational error exited 2"
else
  echo "FAIL: genuine operational error exited $OP_EXIT (expected 2)"
  FAILED=1
fi

if echo "$OP_OUTPUT" | grep -q "ERROR: fleet status --json failed"; then
  echo "PASS: output reports operational error"
else
  echo "FAIL: output missing operational error report"
  FAILED=1
fi
rm -f "$SCRATCH/operational_error_mode"

# ---- test 5: permanent unparseable runner error exits 2 ---------------------
touch "$SCRATCH/permanent_syntax_mode"
PERM_EXIT=0
PERM_OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" bash "$AUTO_HERD" --session "test-perm-error-session" 2>&1)" || PERM_EXIT=$?

echo "--- Run output (Test 5: Permanent Syntax Error) ---"
echo "$PERM_OUTPUT"
echo "---------------------------------------------------"

if [[ "$PERM_EXIT" -eq 2 ]]; then
  echo "PASS: permanent runner syntax error exited 2 after retry exhaustion"
else
  echo "FAIL: permanent runner syntax error exited $PERM_EXIT (expected 2)"
  FAILED=1
fi

if echo "$PERM_OUTPUT" | grep -q "remained unparseable after"; then
  echo "PASS: output reports runner source remained unparseable"
else
  echo "FAIL: output missing retry exhaustion report"
  FAILED=1
fi
rm -f "$SCRATCH/permanent_syntax_mode"

# ---- test 6: repo handoff with has_handoff: true completes cleanly --------
touch "$SCRATCH/repo_handoff_mode"
REPO_EXIT=0
REPO_OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" bash "$AUTO_HERD" --session "test-repo-session" 2>&1)" || REPO_EXIT=$?

echo "--- Run output (Test 6: Repo Handoff Mode) ---"
echo "$REPO_OUTPUT"
echo "----------------------------------------------"

if [[ "$REPO_EXIT" -eq 0 ]]; then
  echo "PASS: auto_herd.sh completed repo handoff worker and exited 0"
else
  echo "FAIL: auto_herd.sh exited $REPO_EXIT on repo handoff worker (expected 0)"
  FAILED=1
fi

if [[ -f "$SCRATCH/agent-repo.completed" ]]; then
  echo "PASS: agent-repo was completed"
else
  echo "FAIL: agent-repo was never completed"
  FAILED=1
fi
rm -f "$SCRATCH/repo_handoff_mode"

# ---- test 7: a short pane closes its mapped long Beads ID ------------------
touch "$SCRATCH/mapped_pane_mode"
MAP_EXIT=0
MAP_OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" SCHED_BIN="$SCRATCH/scheduler.py" bash "$AUTO_HERD" --session "test-mapped-pane-session" --project "$SCRATCH" 2>&1)" || MAP_EXIT=$?

if [[ "$MAP_EXIT" -eq 0 ]] && [[ -f "$SCRATCH/close_args.json" ]] && grep -q 'agent-skills-a-long-durable-bead-id' "$SCRATCH/close_args.json"; then
  echo "PASS: short pane resolved and closed its full durable Beads ID"
else
  echo "FAIL: short pane did not close its mapped full Beads ID (exit $MAP_EXIT): $MAP_OUTPUT"
  FAILED=1
fi
rm -f "$SCRATCH/mapped_pane_mode"

# ---- test 8: an interrupted worker halts with exit 1 and notifies herding agent -
touch "$SCRATCH/interrupted_mode"
INT_EXIT=0
INT_OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" bash "$AUTO_HERD" --session "test-int-session" 2>&1)" || INT_EXIT=$?

if [[ "$INT_EXIT" -eq 1 ]] && echo "$INT_OUTPUT" | grep -q "operation was interrupted or cancelled (requires herding agent guidance)"; then
  echo "PASS: interrupted agent halts auto_herd with exit 1 and notifies herding agent"
else
  echo "FAIL: interrupted agent did not halt with exit 1 or missing notification: $INT_OUTPUT"
  FAILED=1
fi
rm -f "$SCRATCH/interrupted_mode"

# ---- test 9: a worker that hands off as blocked is escalated, not re-prompted ---
# Regression: re-prompting a blocked worker with a sample completed handoff let the
# sample be scraped back from the pane and completed as success.
touch "$SCRATCH/blocked_handoff_mode"
rm -f "$SCRATCH/prompt_calls.txt" "$SCRATCH/complete_calls.txt"
BH_EXIT=0
BH_OUTPUT="$(HERD_BIN="$SCRATCH/herd.sh" bash "$AUTO_HERD" --session "test-blocked-handoff-session" 2>&1)" || BH_EXIT=$?

if [[ "$BH_EXIT" -eq 1 ]] \
  && echo "$BH_OUTPUT" | grep -q "agent-blocked-handoff: worker reported blocked handoff (requires herding agent guidance)" \
  && ! grep -qs "agent-blocked-handoff" "$SCRATCH/prompt_calls.txt" \
  && ! grep -qs "agent-blocked-handoff" "$SCRATCH/complete_calls.txt"; then
  echo "PASS: blocked handoff halts auto_herd with exit 1 without re-prompting or completing"
else
  echo "FAIL: blocked handoff was not escalated cleanly (exit $BH_EXIT): $BH_OUTPUT"
  FAILED=1
fi
rm -f "$SCRATCH/blocked_handoff_mode"

if [[ "$FAILED" -eq 0 ]]; then
  echo "ALL PASS: auto_herd.sh survives transient runner half-reads, catches genuine operational errors, and satisfies all criteria"
  exit 0
else
  exit 1
fi
