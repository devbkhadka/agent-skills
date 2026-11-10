#!/bin/bash
# scripts/auto_herd.sh
# Automates the fleet lifecycle loop: watches, auto-approves, and completes agents.
#
# Exit codes:
#   0 - All agents completed successfully (fleet is empty after work).
#   1 - Stopped: human attention needed (all remaining agents are blocked/escalated).
#   2 - Operational or usage error (bad arguments, herdr/fleet.py failure, invalid JSON).
#   4 - Empty fleet on initial poll (wrong session name or failed spawn).

set -euo pipefail

for _extra_path in /opt/homebrew/bin /opt/homebrew/sbin /usr/local/bin "$HOME/.local/bin"; do
  if [[ -d "$_extra_path" && ":$PATH:" != *":$_extra_path:"* ]]; then
    PATH="$_extra_path:$PATH"
  fi
done
export PATH

SESSION_NAME=""
PROJECT_PATH=""
LEAD_AGENT="${HERDER_AGENT_NAME:-${AGENT_NAME:-}}"
JSON_OUTPUT=0
IGNORED_AGENTS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)
      SESSION_NAME="$2"
      shift 2
      ;;
    --session=*)
      SESSION_NAME="${1#*=}"
      shift
      ;;
    --project|-p)
      PROJECT_PATH="$2"
      shift 2
      ;;
    --project=*)
      PROJECT_PATH="${1#*=}"
      shift
      ;;
    --lead|--lead-agent|--exclude)
      LEAD_AGENT="$2"
      shift 2
      ;;
    --lead=*|--lead-agent=*|--exclude=*)
      LEAD_AGENT="${1#*=}"
      shift
      ;;
    --ignore-agent)
      IGNORED_AGENTS+=("$2")
      shift 2
      ;;
    --ignore-agent=*)
      IGNORED_AGENTS+=("${1#*=}")
      shift
      ;;
    --json)
      JSON_OUTPUT=1
      shift
      ;;
    -h|--help)
      echo "Usage: auto_herd.sh --session <name> [--project <repo-path>] [--lead <agent-name>] [--ignore-agent <agent>] [--json]"
      echo ""
      echo "Automates the fleet lifecycle loop: watches, auto-approves, and completes agents."
      echo ""
      echo "Options:"
      echo "  --session <name>       Session identifier (required)"
      echo "  --project, -p <path>   Repository root containing .beads/ for closing beads"
      echo "  --lead, --exclude <ag> Name of lead orchestrator agent to exclude from worker loop"
      echo "  --ignore-agent <ag>    Name of worker/sidecar agent to ignore in completion sweep"
      echo "  --json                 Emit structured JSON telemetry record upon wave completion"
      echo "  -h, --help             Show this help message"
      exit 0
      ;;
    *)
      echo "Error: unrecognized argument '$1'" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$SESSION_NAME" ]]; then
  echo "Error: --session <unique_name> is required." >&2
  exit 2
fi

if [[ -n "$PROJECT_PATH" && ! -d "$PROJECT_PATH" ]]; then
  echo "Error: --project directory '$PROJECT_PATH' does not exist." >&2
  exit 2
fi

S="$(dirname "$0")"
SKILLS_DIR="$(dirname "$(dirname "$S")")"

# The herd.sh wrapper always comes from this script's own directory. HERD_BIN
# exists so the shell tests can substitute a stub; it is deliberately NOT a
# PATH lookup, because a stray herd.sh anywhere on PATH would then silently
# take over fleet supervision.
HERD="${HERD_BIN:-$S/herd.sh}"
SCHED="${SCHED_BIN:-$SKILLS_DIR/beads-task-scheduler/scripts/scheduler.py}"
STATE_DIR="${AGENT_HERDER_STATE_DIR:-$HOME/.config/.agents/herder}"
LOG_DIR="$STATE_DIR/$SESSION_NAME"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/auto_herd.log"

log_diag() {
  local msg="$*"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $msg" >> "$LOG_FILE" 2>/dev/null || true
}

if [[ -n "$PROJECT_PATH" && ! -f "$SCHED" ]]; then
  echo "Error: beads scheduler not found at '$SCHED'." >&2
  exit 2
fi

# Maximum attempts to re-prompt an idle agent for valid/compact completion evidence
MAX_REPROMPTS=3
# Maximum polls an agent may stay in 'settling' state before being declared stalled
MAX_SETTLE_POLLS=5
# Maximum polls an agent may stay continuously in 'working' state before being declared stalled (default 150 polls = 300s)
MAX_WORKING_POLLS="${AGENT_HERDER_MAX_WORKING_POLLS:-150}"
# Maximum retries for transient unparseable runner states (e.g. mid-write on fleet.py)
MAX_RUNNER_RETRIES=10
RUNNER_RETRY_DELAY=0.5

# Temporary scratch directory to track state across iterations
STATE_SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/auto_herd.XXXXXX")"
cleanup() {
  rm -rf "$STATE_SCRATCH"
}
trap cleanup EXIT INT TERM

# Runner snapshotting:
# When HERD_BIN is not set, snapshot the runner scripts from $S into $STATE_SCRATCH/runner.
# This ensures that when a worker is legitimately mid-write on $S/fleet.py,
# the supervision loop running from the snapshot remains unaffected.
# When $S/fleet.py is parseable and valid, the snapshot is kept in sync.
RUNNER_DIR="$S"
if [[ -z "${HERD_BIN:-}" ]]; then
  RUNNER_DIR="$STATE_SCRATCH/runner"
  mkdir -p "$RUNNER_DIR"
  cp "$S"/*.py "$S"/*.sh "$S"/*.json "$RUNNER_DIR/" 2>/dev/null || true
  chmod +x "$RUNNER_DIR"/*.sh 2>/dev/null || true
  HERD="$RUNNER_DIR/herd.sh"
else
  HERD="$HERD_BIN"
fi

sync_runner_snapshot() {
  if [[ "$RUNNER_DIR" != "$S" && -f "$S/fleet.py" ]]; then
    if python3 -c "
import sys
p = sys.argv[1]
try:
    with open(p, 'rb') as f:
        src = f.read()
    if len(src.strip()) == 0:
        sys.exit(1)
    compile(src, p, 'exec')
except Exception:
    sys.exit(1)
sys.exit(0)
" "$S/fleet.py" >/dev/null 2>&1; then
      cp "$S"/*.py "$S"/*.sh "$S"/*.json "$RUNNER_DIR/" 2>/dev/null || true
      chmod +x "$RUNNER_DIR"/*.sh 2>/dev/null || true
    fi
  fi
}

is_transient_runner_error() {
  local output="${1:-}"
  # Check if output contains Python syntax / compile / tokenizer errors
  if echo "$output" | grep -qE "SyntaxError:|IndentationError:|TabError:|TokenError:|unexpected EOF|unclosed \(|invalid syntax"; then
    return 0
  fi
  # Check if live fleet.py is currently failing compilation (mid-write)
  if [[ -f "$S/fleet.py" ]]; then
    if ! python3 -c "
import sys
p = sys.argv[1]
try:
    with open(p, 'rb') as f:
        src = f.read()
    if len(src.strip()) == 0:
        sys.exit(1)
    compile(src, p, 'exec')
except Exception:
    sys.exit(1)
sys.exit(0)
" "$S/fleet.py" >/dev/null 2>&1; then
      return 0
    fi
  fi
  if [[ "$RUNNER_DIR" != "$S" && -f "$RUNNER_DIR/fleet.py" ]]; then
    if ! python3 -m py_compile "$RUNNER_DIR/fleet.py" >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}

if [[ "$JSON_OUTPUT" -ne 1 ]]; then
  echo "Starting automated fleet lifecycle for session: $SESSION_NAME"
fi
log_diag "Starting automated fleet lifecycle for session: $SESSION_NAME"
WAVE_START_TS=$(date +%s)

emit_wave_completion() {
  local duration=$(( $(date +%s) - WAVE_START_TS ))
  local iso_ts
  iso_ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local count=0
  if [[ -f "$STATE_SCRATCH/completed_agents.txt" ]]; then
    count=$(grep -c . "$STATE_SCRATCH/completed_agents.txt" 2>/dev/null || echo 0)
  fi
  local telemetry_record
  telemetry_record=$(python3 -c '
import sys, json
session, count, dur, ts = sys.argv[1:5]
print(json.dumps({
    "session": session,
    "status": "completed",
    "completed_agents": int(count),
    "duration_seconds": int(dur),
    "timestamp": ts,
}))
' "$SESSION_NAME" "$count" "$duration" "$iso_ts")
  echo "$telemetry_record" >> "$LOG_DIR/wave_completions.jsonl" 2>/dev/null || true
  log_diag "Wave telemetry: $telemetry_record"
  if [[ "$JSON_OUTPUT" -eq 1 ]]; then
    echo "$telemetry_record"
  else
    echo "All agents completed successfully."
  fi
}

# Track whether at least one agent has ever been observed. An empty fleet
# on the FIRST poll means nothing was spawned (wrong session name or failed
# spawn), which is a failure -- not a completion. Only an empty fleet seen
# AFTER at least one agent has been observed is a real completion.
EVER_HAD_AGENTS=0
EMPTY_POLL_COUNT=0

while true; do
  # Wait briefly for something actionable to happen
  bash "$HERD" --session "$SESSION_NAME" watch --until actionable --timeout 10 >/dev/null 2>&1 || true
  
  # Snapshot the fleet state using `status --json` (instant, no --timeout argument).
  # If fleet.py is in a transient unparseable / mid-write state, retry with backoff.
  SNAPSHOT=""
  STATUS_OK=0
  for ((runner_try=1; runner_try<=MAX_RUNNER_RETRIES; runner_try++)); do
    sync_runner_snapshot
    SNAPSHOT_STATUS=0
    SNAPSHOT_ERR=$(mktemp)
    SNAPSHOT_OUT=$(bash "$HERD" --session "$SESSION_NAME" status --json 2>"$SNAPSHOT_ERR") || SNAPSHOT_STATUS=$?

    if [[ "$SNAPSHOT_STATUS" -eq 0 && -n "$SNAPSHOT_OUT" ]]; then
      # Strip any stray warning lines that might precede the JSON object
      SNAPSHOT=$(python3 -c "
import sys
raw = sys.stdin.read()
start = min([i for i in (raw.find('{'), raw.find('[')) if i >= 0] or [-1])
if start >= 0:
    sys.stdout.write(raw[start:])
else:
    sys.stdout.write(raw)
" <<< "$SNAPSHOT_OUT")
      if echo "$SNAPSHOT" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        STATUS_OK=1
        rm -f "$SNAPSHOT_ERR"
        break
      fi
    fi

    SNAPSHOT="$(cat "$SNAPSHOT_ERR")"
    if [[ -z "$SNAPSHOT" ]]; then
      SNAPSHOT="$SNAPSHOT_OUT"
    fi
    rm -f "$SNAPSHOT_ERR"

    # Check if this failure is due to a transient unparseable runner state / mid-write
    if is_transient_runner_error "$SNAPSHOT"; then
      log_diag "Runner source is transiently unparseable or mid-write (attempt $runner_try/$MAX_RUNNER_RETRIES); waiting for write to settle..."
      sleep "$RUNNER_RETRY_DELAY"
      continue
    fi

    # Genuine operational error: runner source is valid, but status failed or returned invalid output
    log_diag "ERROR: fleet status --json failed. Output: $SNAPSHOT"
    echo "ERROR: fleet status --json failed. Output:" >&2
    echo "$SNAPSHOT" >&2
    exit 2
  done

  if [[ "$STATUS_OK" -ne 1 ]]; then
    log_diag "ERROR: fleet status --json failed: runner source remained unparseable after $MAX_RUNNER_RETRIES retries. Output: $SNAPSHOT"
    echo "ERROR: fleet status --json failed: runner source remained unparseable after $MAX_RUNNER_RETRIES retries. Output:" >&2
    echo "$SNAPSHOT" >&2
    exit 2
  fi

  # `status --json` returns {"session":..., "agents":[...]} or array; extract worker count (excluding lead herder and parked agents).
  FLEET_COUNT=$(echo "$SNAPSHOT" | jq -e --arg lead "$LEAD_AGENT" '
    if type == "object" then
      if .agents and (.agents | type == "array") then
        [.agents[] | select((type == "object") and (.parked != true) and ($lead == "" or .agent != $lead) and (((.group // "") | test("^lead:") | not)) and (((.resources // []) | all(tostring | test("^role:(herder|lead):") | not))) and (((.agent // "") | tostring | test("^sim-herder-") | not)))]
      elif .fleet and (.fleet | type == "array") then
        [.fleet[] | select((type == "object") and (.parked != true) and ($lead == "" or .agent != $lead) and (((.group // "") | test("^lead:") | not)) and (((.resources // []) | all(tostring | test("^role:(herder|lead):") | not))) and (((.agent // "") | tostring | test("^sim-herder-") | not)))]
      else [] end
    elif type == "array" then
      [.[] | select((type == "object") and (.parked != true) and ($lead == "" or .agent != $lead) and (((.group // "") | test("^lead:") | not)) and (((.resources // []) | all(tostring | test("^role:(herder|lead):") | not))) and (((.agent // "") | tostring | test("^sim-herder-") | not)))]
    else [] end | length') || {
    log_diag "ERROR: jq failed to compute fleet length. Snapshot: $SNAPSHOT"
    echo "ERROR: jq failed to compute fleet length. Snapshot:" >&2
    echo "$SNAPSHOT" >&2
    exit 2
  }

  TOTAL_AGENTS=$(echo "$SNAPSHOT" | jq -e --arg lead "$LEAD_AGENT" '
    if type == "object" then
      if .agents and (.agents | type == "array") then
        [.agents[] | select((type == "object") and ($lead == "" or .agent != $lead) and (((.group // "") | test("^lead:") | not)) and (((.resources // []) | all(tostring | test("^role:(herder|lead):") | not))) and (((.agent // "") | tostring | test("^sim-herder-") | not)))]
      elif .fleet and (.fleet | type == "array") then
        [.fleet[] | select((type == "object") and ($lead == "" or .agent != $lead) and (((.group // "") | test("^lead:") | not)) and (((.resources // []) | all(tostring | test("^role:(herder|lead):") | not))) and (((.agent // "") | tostring | test("^sim-herder-") | not)))]
      else [] end
    elif type == "array" then
      [.[] | select((type == "object") and ($lead == "" or .agent != $lead) and (((.group // "") | test("^lead:") | not)) and (((.resources // []) | all(tostring | test("^role:(herder|lead):") | not))) and (((.agent // "") | tostring | test("^sim-herder-") | not)))]
    else [] end | length' 2>/dev/null || echo "0")

  if [[ -z "$FLEET_COUNT" ]]; then
    log_diag "ERROR: jq returned empty fleet count (should not happen after -e). Snapshot: $SNAPSHOT"
    echo "ERROR: jq returned empty fleet count (should not happen after -e). Snapshot:" >&2
    echo "$SNAPSHOT" >&2
    exit 2
  fi

  if [[ "$FLEET_COUNT" -eq 0 ]]; then
    if [[ "$TOTAL_AGENTS" -gt 0 ]]; then
      log_diag "All remaining agents in session '$SESSION_NAME' are parked for human attention."
      echo "All remaining agents in session '$SESSION_NAME' are parked for human attention." >&2
      exit 1
    fi
    if [[ "$EVER_HAD_AGENTS" -eq 0 ]]; then
      log_diag "ERROR: Fleet has no workers and no worker was ever observed in session '$SESSION_NAME'."
      echo "ERROR: Fleet has no workers and no worker was ever observed in session '$SESSION_NAME'." >&2
      echo "       This usually means the session name is wrong or the spawn failed." >&2
      echo "       See $LOG_FILE for details." >&2
      exit 4
    fi
    if [[ -s "$STATE_SCRATCH/closure_failed_beads.txt" ]]; then
      log_diag "ERROR: Fleet is empty but one or more beads failed closure in scheduler:"
      while IFS= read -r err_line; do
        log_diag "  - $err_line"
      done < "$STATE_SCRATCH/closure_failed_beads.txt"
      echo "ERROR: Fleet drained but one or more beads failed closure in scheduler:" >&2
      cat "$STATE_SCRATCH/closure_failed_beads.txt" >&2
      exit 1
    fi
    EMPTY_POLL_COUNT=$((EMPTY_POLL_COUNT + 1))
    if [[ "$EMPTY_POLL_COUNT" -lt 2 ]]; then
      log_diag "Fleet observed empty (sample $EMPTY_POLL_COUNT/2); settling before completing wave..."
      sleep 1
      continue
    fi
    emit_wave_completion
    exit 0
  fi
  
  EMPTY_POLL_COUNT=0
  
  # We have observed at least one agent; subsequent empty fleet = true completion.
  EVER_HAD_AGENTS=1
  
  # Track whether any agent made progress during this poll:
  # - actively working or settling within bounds
  # - successfully auto-approved
  # - re-prompted for completion evidence
  # - successfully completed (and bead closed if --project)
  CAN_PROGRESS=0
  
  # Clear and populate blocked reasons for this snapshot
  rm -f "$STATE_SCRATCH/current_blocked_reasons.txt"
  touch "$STATE_SCRATCH/current_blocked_reasons.txt"
  
  while IFS= read -r agent_json; do
    if [[ -z "$agent_json" ]]; then continue; fi
    AGENT=$(echo "$agent_json" | jq -r '.agent')
    IS_HERDER=$(echo "$agent_json" | jq -r --arg lead "$LEAD_AGENT" '
      if ($lead != "" and .agent == $lead) or
         ((.group // "") | test("^lead:")) or
         ((.resources // []) | any(test("^role:(herder|lead):"))) or
         (.agent | test("^sim-herder-")) then "true" else "false" end')
    IS_PARKED=$(echo "$agent_json" | jq -r '.parked // false')
    if [[ "$IS_HERDER" == "true" || "$IS_PARKED" == "true" ]]; then
      continue
    fi
    IS_READ_ONLY=$(echo "$agent_json" | jq -r '.read_only // false')
    IS_IGNORED=0
    for ign in "${IGNORED_AGENTS[@]}"; do
      if [[ "$AGENT" == "$ign" ]]; then
        IS_IGNORED=1
        break
      fi
    done
    STATE=$(echo "$agent_json" | jq -r '.state')
    if [[ "$IS_READ_ONLY" == "true" || "$IS_IGNORED" -eq 1 ]]; then
      if [[ "$STATE" == "idle" || "$STATE" == "done" ]]; then
        log_diag "Agent $AGENT is read-only/ignored in state '$STATE'; skipping completion sweep."
        CAN_PROGRESS=1
        continue
      fi
    fi
    ACTION=$(echo "$agent_json" | jq -r '.action // ""')
    HEALTH=$(echo "$agent_json" | jq -r '.health // "ok"')
    PENDING=$(echo "$agent_json" | jq -r '.pending // ""')
    RULE=$(echo "$agent_json" | jq -r '.rule // ""')
    HAS_HANDOFF=$(echo "$agent_json" | jq -r '.has_handoff // false')
    
    DECLINED_FILE="$STATE_SCRATCH/declined_${AGENT}"
    REPROMPT_FILE="$STATE_SCRATCH/reprompt_${AGENT}"
    SETTLE_FILE="$STATE_SCRATCH/settle_${AGENT}"
    PANE_FILE="$STATE_SCRATCH/pane_${AGENT}"
    WORKING_FILE="$STATE_SCRATCH/working_${AGENT}"
    
    if [[ "$ACTION" == "approve" || "$ACTION" == "escalate" ]]; then
      # Agent is waiting on a prompt that needs classification/approval.
      rm -f "$SETTLE_FILE"
      rm -f "$PANE_FILE"
      rm -f "$WORKING_FILE"
      LAST_DECLINED=""
      if [[ -f "$DECLINED_FILE" ]]; then
        LAST_DECLINED="$(cat "$DECLINED_FILE")"
      fi
      
      # If this exact prompt was already declined by policy, do not re-offer in a tight spin.
      PROMPT_KEY="${PENDING:-__no_prompt__}"
      if [[ -f "$DECLINED_FILE" && "$PROMPT_KEY" == "$LAST_DECLINED" ]]; then
        # Already set aside; record blocked reason without re-running approve.
        if [[ -n "$PENDING" ]]; then
          echo "Agent $AGENT: blocked on prompt '$PENDING' (rule: ${RULE:-none}, action: $ACTION)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
        else
          echo "Agent $AGENT: blocked on prompt (rule: ${RULE:-none}, action: $ACTION)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
        fi
      else
        log_diag "Agent $AGENT needs approval/review..."
        APPROVE_OUTPUT=""
        if APPROVE_OUTPUT=$(bash "$HERD" --session "$SESSION_NAME" approve --agent "$AGENT" 2>&1); then
          log_diag "Auto-approved $AGENT."
          rm -f "$DECLINED_FILE"
          CAN_PROGRESS=1
        else
          if is_transient_runner_error "$APPROVE_OUTPUT"; then
            log_diag "Runner transiently unparseable during approve; will retry next poll..."
            CAN_PROGRESS=1
          else
            log_diag "Agent $AGENT requires manual intervention (escalated or failed auto-approve)."
            printf '%s' "$PROMPT_KEY" > "$DECLINED_FILE"
            if [[ -n "$PENDING" ]]; then
              echo "Agent $AGENT: blocked on prompt '$PENDING' (rule: ${RULE:-none}, action: $ACTION)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
            else
              echo "Agent $AGENT: blocked on manual intervention (rule: ${RULE:-none}, action: $ACTION)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
            fi
          fi
        fi
      fi
    elif [[ "$STATE" == "blocked" || "$HEALTH" != "ok" ]]; then
      rm -f "$SETTLE_FILE"
      rm -f "$PANE_FILE"
      rm -f "$WORKING_FILE"
      log_diag "Agent $AGENT is blocked or unhealthy. Action: $ACTION, Health: $HEALTH, State: $STATE"
      echo "Agent $AGENT: blocked/unhealthy (health: $HEALTH, state: $STATE, action: $ACTION)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
    elif [[ "$HAS_HANDOFF" == "true" || "$STATE" == "idle" || "$STATE" == "done" ]]; then
      rm -f "$SETTLE_FILE"
      rm -f "$WORKING_FILE"
      PANE_TEXT=$(bash "$HERD" --session "$SESSION_NAME" read --agent "$AGENT" 2>/dev/null || true)
      # Verify Evidence Precondition before attempting complete
      CHECK_JSON=$(python3 -c '
import sys, json, hashlib, re
from pathlib import Path
sys.path.insert(0, sys.argv[1])
try:
    import fleet
    session = sys.argv[2]
    agent = sys.argv[3]
    pane_arg = sys.argv[4] if len(sys.argv) > 4 else ""
    max_chars = getattr(fleet, "DEFAULT_MAX_HANDOFF_CHARS", 16000)
    assignment = fleet.get_assignment(agent, session)
    hf = fleet.handoff_path(agent, session)
    repo_hf = fleet.repo_handoff_path(agent, (assignment or {}).get("cwd"))
    handoff_str = None
    handoff_data = None
    parse_err = None
    txt = ""
    try:
        txt = fleet.read_text(agent, session, lines=100, source="recent-unwrapped")
    except (SystemExit, Exception):
        try:
            txt = fleet.read_text(agent, session, lines=100, source="visible")
        except (SystemExit, Exception):
            txt = ""
    if not txt and pane_arg:
        txt = pane_arg
    pane_hash = hashlib.sha256(txt.encode("utf-8")).hexdigest() if txt else None
    for candidate_hf in (hf, repo_hf):
        if candidate_hf and candidate_hf.is_file():
            try:
                handoff_data, handoff_str = fleet.parse_handoff_file(candidate_hf, assignment, expected_agent=agent)
                if handoff_str:
                    break
            except (SystemExit, Exception) as e:
                parse_err = str(e)
    if not handoff_str and txt and fleet.HANDOFF_MARKER in txt:
        try:
            handoff_data, handoff_str = fleet.parse_handoff(txt, assignment)
        except (SystemExit, Exception) as e:
            parse_err = str(e)
    is_interrupted = any(
        re.search(pat, txt, re.IGNORECASE) for pat in (
            r"\b(?:Operation|Command|Permission|Tool call) (?:was )?cancelled\b",
            r"\b(?:Operation|Command|Permission|Tool call) (?:was )?rejected\b",
            r"Cancelled by user",
            r"\bInterrupted\b",
        )
    )
    # A worker that reports status blocked (or lists blockers) has finished its turn
    # on purpose. Re-prompting it for a completed handoff invites a fabricated one.
    worker_blocked = bool(parse_err) and (
        "handoff status is" in parse_err or "still lists blockers" in parse_err
    )
    if handoff_str:
        if len(handoff_str) > max_chars:
            print(json.dumps({"status": "oversize", "length": len(handoff_str), "max": max_chars, "pane_hash": pane_hash}))
        else:
            print(json.dumps({"status": "valid", "length": len(handoff_str), "pane_hash": pane_hash}))
    elif worker_blocked:
        print(json.dumps({"status": "worker_blocked", "error": parse_err, "pane_hash": pane_hash}))
    elif is_interrupted:
        print(json.dumps({"status": "interrupted", "pane_hash": pane_hash}))
    else:
        print(json.dumps({"status": "missing", "error": parse_err or "no handoff found", "pane_hash": pane_hash}))
except (SystemExit, Exception) as err:
    print(json.dumps({"status": "error", "error": str(err)}))
' "$RUNNER_DIR" "$SESSION_NAME" "$AGENT" "$PANE_TEXT" 2>/dev/null || echo '{"status":"error","error":"runner_execution_failed"}')

      HANDOFF_STATUS=$(echo "$CHECK_JSON" | jq -r '.status // "missing"')
      HANDOFF_ERR=$(echo "$CHECK_JSON" | jq -r '.error // ""')

      if [[ "$HANDOFF_STATUS" == "error" ]] && is_transient_runner_error "$HANDOFF_ERR"; then
        log_diag "Runner is transiently unparseable during handoff validation; waiting..."
        CAN_PROGRESS=1
        continue
      fi

      PANE_HASH=$(echo "$CHECK_JSON" | jq -r '.pane_hash // empty' 2>/dev/null || true)
      if [[ -z "$PANE_HASH" || "$PANE_HASH" == "null" ]]; then
        PANE_HASH=$(python3 -c "import hashlib, sys; print(hashlib.sha256(sys.stdin.read().encode('utf-8')).hexdigest())" <<< "$PANE_TEXT" 2>/dev/null || echo "")
      fi

      LAST_PANE_HASH=""
      if [[ -f "$PANE_FILE" ]]; then
        LAST_PANE_HASH="$(cat "$PANE_FILE")"
      fi

      if [[ "$HANDOFF_STATUS" == "valid" ]]; then
        rm -f "$PANE_FILE"
        log_diag "Agent $AGENT is $STATE with valid handoff. Attempting to complete..."
        COMPLETE_OUTPUT=""
        if COMPLETE_OUTPUT=$(bash "$HERD" --session "$SESSION_NAME" complete --agent "$AGENT" 2>&1); then
          log_diag "Agent $AGENT successfully completed."
          if [[ "$JSON_OUTPUT" -ne 1 ]]; then
            echo "Agent $AGENT successfully completed."
          fi
          
          # If --project is set and agent matches bead-<id>, close the bead in scheduler
          if [[ -n "$PROJECT_PATH" ]]; then
            if [[ "$AGENT" =~ ^bead-(.+)$ ]]; then
              # Resolve through durable Beads metadata.  A short pane name is not a
              # truncated ID: herder.pane_id is the authoritative lossless mapping.
              BEAD_ID=""
              PANE_MAPPING_OUTPUT=""
              if PANE_MAPPING_OUTPUT=$(python3 "$SCHED" resolve-pane --pane-id "$AGENT" --project "$PROJECT_PATH" --json 2>&1); then
                BEAD_ID=$(echo "$PANE_MAPPING_OUTPUT" | jq -r '.bead_id // empty' 2>/dev/null || true)
              fi
              if [[ -z "$BEAD_ID" ]]; then
                log_diag "ERROR: Could not resolve durable Beads ID for pane $AGENT: $PANE_MAPPING_OUTPUT"
                echo "Agent $AGENT: pane mapping failed ($PANE_MAPPING_OUTPUT)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
                echo "Agent $AGENT: pane mapping failed ($PANE_MAPPING_OUTPUT)" >> "$STATE_SCRATCH/closure_failed_beads.txt"
                continue
              fi
              HANDOFF_PAYLOAD=$(echo "$COMPLETE_OUTPUT" | jq -c '.handoff // empty' 2>/dev/null || true)
              if [[ -z "$HANDOFF_PAYLOAD" || "$HANDOFF_PAYLOAD" == "null" ]]; then
                HANDOFF_PATH=$(echo "$COMPLETE_OUTPUT" | jq -r '.handoff_path // empty' 2>/dev/null || true)
                if [[ -n "$HANDOFF_PATH" && -f "$HANDOFF_PATH" ]]; then
                  HANDOFF_PAYLOAD=$(jq -c --arg ag "$AGENT" '.[$ag].handoff // empty' "$HANDOFF_PATH" 2>/dev/null || true)
                elif [[ -f "$STATE_DIR/$SESSION_NAME/handoffs.json" ]]; then
                  HANDOFF_PAYLOAD=$(jq -c --arg ag "$AGENT" '.[$ag].handoff // empty' "$STATE_DIR/$SESSION_NAME/handoffs.json" 2>/dev/null || true)
                fi
              fi

              if [[ -n "$HANDOFF_PAYLOAD" && "$HANDOFF_PAYLOAD" != "null" ]]; then
                log_diag "Closing bead $BEAD_ID for agent $AGENT in scheduler..."
                CLOSE_OUTPUT=""
                if CLOSE_OUTPUT=$(python3 "$SCHED" close "$BEAD_ID" --actor "$AGENT" --project "$PROJECT_PATH" --session "$SESSION_NAME" --handoff "$HANDOFF_PAYLOAD" 2>&1); then
                  log_diag "Successfully validated evidence and closed bead $BEAD_ID."
                  if [[ "$JSON_OUTPUT" -ne 1 ]]; then
                    echo "Successfully validated evidence and closed bead $BEAD_ID."
                  fi
                else
                  log_diag "ERROR: Failed to close bead $BEAD_ID: $CLOSE_OUTPUT"
                  echo "ERROR: Failed to close bead $BEAD_ID: $CLOSE_OUTPUT" >&2
                  echo "Agent $AGENT: bead closure failed for $BEAD_ID ($CLOSE_OUTPUT)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
                  echo "Agent $AGENT: bead closure failed for $BEAD_ID ($CLOSE_OUTPUT)" >> "$STATE_SCRATCH/closure_failed_beads.txt"
                fi
              else
                log_diag "ERROR: Could not extract handoff payload for bead $BEAD_ID"
                echo "ERROR: Could not extract handoff payload for bead $BEAD_ID" >&2
                echo "Agent $AGENT: missing handoff record for bead $BEAD_ID" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
                echo "Agent $AGENT: missing handoff record for bead $BEAD_ID" >> "$STATE_SCRATCH/closure_failed_beads.txt"
              fi
            fi
          fi
          
          rm -f "$DECLINED_FILE"
          rm -f "$REPROMPT_FILE"
          echo "$AGENT" >> "$STATE_SCRATCH/completed_agents.txt"
          CAN_PROGRESS=1
        else
          if is_transient_runner_error "$COMPLETE_OUTPUT"; then
            log_diag "Runner transiently unparseable during complete; will retry next poll..."
            CAN_PROGRESS=1
          else
            log_diag "Agent $AGENT complete refused. Output: $COMPLETE_OUTPUT"
            echo "Agent $AGENT: complete refused ($COMPLETE_OUTPUT)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
          fi
        fi

      elif [[ -n "$LAST_PANE_HASH" && -n "$PANE_HASH" && "$PANE_HASH" != "$LAST_PANE_HASH" ]]; then
        # Pane changed since last poll: worker is still actively generating output
        printf '%s' "$PANE_HASH" > "$PANE_FILE"
        log_diag "Agent $AGENT pane is active/changing (delta detected); waiting..."
        CAN_PROGRESS=1

      elif [[ -z "$LAST_PANE_HASH" && -n "$PANE_HASH" ]]; then
        # First candidate idle observation: record hash and sample across delta window before declaring idle
        printf '%s' "$PANE_HASH" > "$PANE_FILE"
        log_diag "Agent $AGENT candidate $STATE; observing pane delta before declaring idle..."
        CAN_PROGRESS=1

      elif [[ "$HANDOFF_STATUS" == "oversize" ]]; then
        REPROMPT_COUNT=0
        if [[ -f "$REPROMPT_FILE" ]]; then
          REPROMPT_COUNT=$(cat "$REPROMPT_FILE")
        fi
        if [[ "$REPROMPT_COUNT" -lt "$MAX_REPROMPTS" ]]; then
          REPROMPT_COUNT=$((REPROMPT_COUNT + 1))
          printf '%s' "$REPROMPT_COUNT" > "$REPROMPT_FILE"
          HO_LEN=$(echo "$CHECK_JSON" | jq -r '.length // "oversize"')
          HO_MAX=$(echo "$CHECK_JSON" | jq -r '.max // "16000"')
          log_diag "Agent $AGENT handoff exceeds character limit ($HO_LEN chars > $HO_MAX chars). Re-prompting (attempt $REPROMPT_COUNT/$MAX_REPROMPTS)..."
          PROMPT_OUT=""
          if ! PROMPT_OUT=$(bash "$HERD" --session "$SESSION_NAME" prompt --agent "$AGENT" --related --text "Your completion handoff exceeds the character limit ($HO_LEN chars > $HO_MAX chars). Please compress free-prose fields (decisions, blockers) while keeping status, changed_files, and verification intact. Re-emit your final FLEET_HANDOFF line as compact JSON under 1200 characters with the same keys (status, decisions, changed_files, verification entries of command, exit_code and output, blockers, next_inputs)." 2>&1); then
            log_diag "Warning: failed to send oversize handoff re-prompt to agent $AGENT: $PROMPT_OUT"
          fi
          CAN_PROGRESS=1
        else
          log_diag "Agent $AGENT: handoff exceeds character limit after $MAX_REPROMPTS re-prompts."
          echo "Agent $AGENT: handoff exceeds character limit after $MAX_REPROMPTS re-prompts" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
        fi

      elif [[ "$HANDOFF_STATUS" == "worker_blocked" ]]; then
        # The worker declared itself blocked. Keep its locks and pane, and hand the
        # decision to the herding agent instead of re-prompting for completion.
        log_diag "Agent $AGENT handed off as blocked: $HANDOFF_ERR"
        echo "Agent $AGENT: worker reported blocked handoff (requires herding agent guidance)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"

      elif [[ "$HANDOFF_STATUS" == "interrupted" ]]; then
        # Agent dropped into interrupted/cancelled state after rejected permission or aborted action.
        # Decisions on alternative paths or halting must come from the herding agent.
        log_diag "Agent $AGENT: operation was interrupted or cancelled; requires herding agent guidance."
        echo "Agent $AGENT: operation was interrupted or cancelled (requires herding agent guidance)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"

      else
        REPROMPT_COUNT=0
        if [[ -f "$REPROMPT_FILE" ]]; then
          REPROMPT_COUNT=$(cat "$REPROMPT_FILE")
        fi
        if [[ "$REPROMPT_COUNT" -lt "$MAX_REPROMPTS" ]]; then
          REPROMPT_COUNT=$((REPROMPT_COUNT + 1))
          printf '%s' "$REPROMPT_COUNT" > "$REPROMPT_FILE"
          log_diag "Agent $AGENT is $STATE but lacks structured handoff evidence. Re-prompting (attempt $REPROMPT_COUNT/$MAX_REPROMPTS)..."
          PROMPT_OUT=""
          if ! PROMPT_OUT=$(bash "$HERD" --session "$SESSION_NAME" prompt --agent "$AGENT" --related --text "You have completed your assigned work but have not emitted the required structured completion evidence. Run each verification command listed in the bead acceptance criteria verbatim (a different command is refused at close) and end with your FLEET_HANDOFF line as JSON with keys status, decisions, changed_files, verification (each entry: the command you ran, its exit_code and its real output), blockers and next_inputs. If you could not finish, set status to blocked and list the blockers instead." 2>&1); then
            log_diag "Warning: failed to send missing handoff re-prompt to agent $AGENT: $PROMPT_OUT"
          fi
          CAN_PROGRESS=1
        else
          log_diag "Agent $AGENT is idle but lacks completion evidence."
          echo "Agent $AGENT: idle but lacks completion evidence" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
        fi
      fi
    elif [[ "$STATE" == "working" ]]; then
      # Agent is actively running a task.
      rm -f "$DECLINED_FILE"
      rm -f "$SETTLE_FILE"
      rm -f "$PANE_FILE"
      WORKING_COUNT=0
      if [[ -f "$WORKING_FILE" ]]; then
        WORKING_COUNT=$(cat "$WORKING_FILE")
      fi
      WORKING_COUNT=$((WORKING_COUNT + 1))
      printf '%s' "$WORKING_COUNT" > "$WORKING_FILE"
      if [[ "$WORKING_COUNT" -le "$MAX_WORKING_POLLS" ]]; then
        CAN_PROGRESS=1
      else
        log_diag "Agent $AGENT: execution budget exceeded ($WORKING_COUNT polls); marking stalled."
        echo "Agent $AGENT: execution budget exceeded (stalled in working state)" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
      fi
    elif [[ "$STATE" == "settling" ]]; then
      # Agent state is transient / settling across samples
      rm -f "$PANE_FILE"
      rm -f "$WORKING_FILE"
      SETTLE_COUNT=0
      if [[ -f "$SETTLE_FILE" ]]; then
        SETTLE_COUNT=$(cat "$SETTLE_FILE")
      fi
      SETTLE_COUNT=$((SETTLE_COUNT + 1))
      printf '%s' "$SETTLE_COUNT" > "$SETTLE_FILE"
      if [[ "$SETTLE_COUNT" -le "$MAX_SETTLE_POLLS" ]]; then
        log_diag "Agent $AGENT is settling (sample $SETTLE_COUNT/$MAX_SETTLE_POLLS)..."
        CAN_PROGRESS=1
      else
        log_diag "Agent $AGENT: stalled in settling state after $MAX_SETTLE_POLLS polls"
        echo "Agent $AGENT: stalled in settling state" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
      fi
    else
      # Any other state (e.g. unassigned)
      rm -f "$WORKING_FILE"
      log_diag "Agent $AGENT is in state '$STATE'."
      echo "Agent $AGENT: state is '$STATE'" >> "$STATE_SCRATCH/current_blocked_reasons.txt"
    fi
  done < <(echo "$SNAPSHOT" | jq -c 'if type == "array" then .[] elif .agents then .agents[] elif .fleet then .fleet[] else empty end')
  
  # If any agent is working, settling, re-prompting, or made progress (approved / completed), continue supervising
  if [[ "$CAN_PROGRESS" -eq 1 ]]; then
    continue
  fi
  
  # If no agent can make progress, all remaining agents are blocked on human intervention
  if [[ -s "$STATE_SCRATCH/current_blocked_reasons.txt" ]]; then
    log_diag "One or more agents are blocked requiring human attention:"
    while IFS= read -r reason; do
      log_diag "  - $reason"
    done < "$STATE_SCRATCH/current_blocked_reasons.txt"

    echo "One or more agents are blocked requiring human attention:"
    head -n 3 "$STATE_SCRATCH/current_blocked_reasons.txt" | while IFS= read -r reason; do
      echo "  - $reason"
    done
    echo "See $LOG_FILE for details."
    exit 1
  fi
  
  # If fleet is empty, all agents completed
  if [[ -s "$STATE_SCRATCH/closure_failed_beads.txt" ]]; then
    echo "ERROR: Fleet drained but one or more beads failed closure in scheduler:" >&2
    cat "$STATE_SCRATCH/closure_failed_beads.txt" >&2
    exit 1
  fi
  emit_wave_completion
  exit 0
done
