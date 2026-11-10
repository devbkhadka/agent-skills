#!/bin/bash
# scripts/herd.sh
# Wrapper around fleet.py to automatically manage headless Herdr sessions.

for _extra_path in /opt/homebrew/bin /opt/homebrew/sbin /usr/local/bin "$HOME/.local/bin"; do
  if [[ -d "$_extra_path" && ":$PATH:" != *":$_extra_path:"* ]]; then
    PATH="$_extra_path:$PATH"
  fi
done
export PATH

SESSION_NAME=""
ATTACHED=0
USER_EXPLICIT_ATTACHED=0
SHOW_HELP=0
RECOVER_SESSION=0
EMIT_DAEMON_EVIDENCE=0
ARGS=()

# Parse wrapper arguments
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
    --attached)
      ATTACHED=1
      shift
      ;;
    --user-explicit-attached)
      USER_EXPLICIT_ATTACHED=1
      shift
      ;;
    --recover-session)
      RECOVER_SESSION=1
      shift
      ;;
    --emit-daemon-evidence)
      EMIT_DAEMON_EVIDENCE=1
      shift
      ;;
    -h|--help)
      SHOW_HELP=1
      shift
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ $SHOW_HELP -eq 1 || ("${#ARGS[@]}" -gt 0 && "${ARGS[0]}" == "help") ]]; then
  SUBCMD=""
  if [[ "${#ARGS[@]}" -gt 0 && "${ARGS[0]}" == "help" ]]; then
    SUBCMD="${ARGS[1]:-}"
  elif [[ $SHOW_HELP -eq 1 && "${#ARGS[@]}" -gt 0 ]]; then
    SUBCMD="${ARGS[0]}"
  fi

  if [[ -n "$SUBCMD" ]]; then
    if [[ "$SUBCMD" == "init" ]]; then
      echo "Usage: herd.sh [--session <name>] init [--cwd <path>] [--emit-daemon-evidence]"
      echo "Bootstrap an un-attached headless session and provision workspace."
      exit 0
    elif [[ "$SUBCMD" == "attach" ]]; then
      echo "Usage: herd.sh --session <name> attach [--recover-session]"
      echo "Attach to a named session after guarding against unsafe recovery."
      exit 0
    fi
    FLEET_PY="$(cd "$(dirname "$0")" && pwd)/fleet.py"
    exec python3 "$FLEET_PY" "$SUBCMD" --help
  fi

  echo "Usage: herd.sh --session <name> <subcommand> [args...]"
  echo "       herd.sh init --session <name> [--cwd <path>]"
  echo "       herd.sh --session <name> init [--cwd <path>]"
  echo "       herd.sh --attached --user-explicit-attached <subcommand> [args...]"
  echo ""
  echo "Commands:"
  echo "  init      Bootstrap an un-attached headless session and provision workspace"
  echo "  attach    Attach to a named session after guarding against unsafe recovery"
  echo "  spawn     Provision pane, acquire leases, and start worker agent"
  echo "  status    Show status of workers in the fleet session"
  echo "  watch     Wait for fleet events or actionable state"
  echo "  notify    Emit an atomic outbox event"
  echo "  approve   Submit approved response to agent prompt"
  echo "  reject    Reject permission with Esc and instruct worker on continuation (or --halt)"
  echo "  complete  Validate structured handoff, release leases, and close pane"
  echo "  park      Mark worker blocked and release resource leases"
  echo "  reset     Terminate stalled pane and restart with prior handoff"
  echo "  classify  Evaluate pane prompt against risk policy"
  echo ""
  echo "Options:"
  echo "  --session <name>          Named un-attached session (required for un-attached fleets)"
  echo "  --attached                Run in current attached session (requires --user-explicit-attached)"
  echo "  --user-explicit-attached  Explicitly allow running in user interactive workspace"
  echo "  --recover-session         Explicitly restart a stopped named session and reconcile workers"
  echo "  --emit-daemon-evidence    Keep the bootstrap daemon log and return its PID (init only)"
  echo "  -h, --help                Show this help message"
  exit 0
fi

if [[ $ATTACHED -eq 1 ]]; then
  if [[ $USER_EXPLICIT_ATTACHED -ne 1 ]]; then
    echo '{"error":"attached_mode_refused","message":"Attached fleet execution is disabled by default. Pass --session <unique_name> for an un-attached session, or include --user-explicit-attached if explicitly requested by the user."}'
    exit 1
  fi
  FLEET_PY="$(cd "$(dirname "$0")" && pwd)/fleet.py"
  exec python3 "$FLEET_PY" "${ARGS[@]}"
fi

if [[ -z "$SESSION_NAME" ]]; then
  echo '{"error":"missing_session","message":"Missing required --session <unique_name> argument."}' >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FLEET_PY="$SCRIPT_DIR/fleet.py"
BOOTSTRAP_LOCK_DIR=""

cleanup_bootstrap_lock() {
  [[ -n "$BOOTSTRAP_LOCK_DIR" ]] || return 0
  rm -f "$BOOTSTRAP_LOCK_DIR/owner.json" 2>/dev/null || true
  rmdir "$BOOTSTRAP_LOCK_DIR" 2>/dev/null || true
  BOOTSTRAP_LOCK_DIR=""
}

# A bootstrap may be interrupted after acquiring the directory lock. The owner record
# makes cleanup safe: only a dead PID or an expired short bootstrap lease is reaped.
trap cleanup_bootstrap_lock EXIT
trap 'cleanup_bootstrap_lock; exit 130' INT TERM HUP

reap_stale_bootstrap_lock() {
  local lock_dir="$1"
  local ttl="${HERD_BOOTSTRAP_LOCK_TTL:-30}"
  local verdict
  verdict="$(python3 - "$lock_dir" "$ttl" <<'PY'
import json, os, sys, time
lock_dir, ttl = sys.argv[1], float(sys.argv[2])
owner = os.path.join(lock_dir, "owner.json")
try:
    has_owner = os.path.exists(owner)
    started = os.stat(owner if has_owner else lock_dir).st_mtime
    data = json.load(open(owner, encoding="utf-8")) if has_owner else {}
    started = float(data.get("started_at", started))
    pid = int(data.get("pid", 0))
except Exception:
    has_owner, started, pid = False, 0.0, 0
alive = False
if pid > 0:
    try:
        os.kill(pid, 0)
        alive = True
    except OSError:
        pass
age_expired = time.time() - started > ttl
stale = age_expired or (has_owner and pid > 0 and not alive)
print("stale" if stale else "active")
PY
)"
  if [[ "$verdict" == "stale" ]]; then
    rm -f "$lock_dir/owner.json" 2>/dev/null || true
    rmdir "$lock_dir" 2>/dev/null || true
  fi
}

is_server_running() {
  local out rc
  out="$(herdr --session "$SESSION_NAME" status server --json 2>&1)"
  rc=$?
  printf "[%s] is_server_running session=%s rc=%d out=%s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$SESSION_NAME" "$rc" "$out" >> "${TMPDIR:-/tmp}/herd_debug.log"
  echo "$out" | python3 -c "import sys,json
try:
    d = json.load(sys.stdin)
    sys.exit(0 if (d.get('status') == 'running' or d.get('running') is True) else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

ensure_server_running() {
  if ! is_server_running; then
    local session_root lock_dir lock_acquired=0
    session_root="${HERDR_CONFIG_DIR:-$HOME/.config/herdr}/sessions/$SESSION_NAME"
    lock_dir="$session_root/.bootstrap.lock"
    mkdir -p "$session_root"
    for _ in {1..50}; do
      reap_stale_bootstrap_lock "$lock_dir"
      if mkdir "$lock_dir" 2>/dev/null; then
        lock_acquired=1
        BOOTSTRAP_LOCK_DIR="$lock_dir"
        python3 - "$lock_dir/owner.json" "$$" <<'PY'
import json, os, sys, tempfile, time
target, pid = sys.argv[1:]
fd, temp = tempfile.mkstemp(prefix=".owner.", dir=os.path.dirname(target))
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump({"pid": int(pid), "started_at": time.time()}, fh)
os.replace(temp, target)
PY
        break
      fi
      sleep 0.1
    done
    if [[ $lock_acquired -ne 1 ]]; then
      python3 -c '
import json, sys
print(json.dumps({"status":"error","error":"session_bootstrap_locked","session":sys.argv[1],"reason":"another bootstrap or recovery is in progress"}))
' "$SESSION_NAME"
      exit 1
    fi
    # A concurrent bootstrap may have finished while we waited for the lock.
    if is_server_running; then
      cleanup_bootstrap_lock
      return 0
    fi
    local server_log server_pid
    server_log="$(mktemp "${TMPDIR:-/tmp}/herdr-server-${SESSION_NAME}.XXXXXX.log" 2>/dev/null || echo "/tmp/herdr-server-${SESSION_NAME}.log")"
    server_pid="$(python3 - "$SESSION_NAME" "$server_log" <<'PY'
import subprocess, sys

session, log_path = sys.argv[1:3]
try:
    with open(log_path, "ab") as log_fh:
        proc = subprocess.Popen(
            ["herdr", "--session", session, "server"],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(proc.pid)
except Exception as exc:
    with open(log_path, "a", encoding="utf-8") as log_fh:
        log_fh.write(f"\nfailed to spawn herdr server: {exc}\n")
    sys.exit(1)
PY
)" || server_pid=""
    local server_ok=0
    for i in {1..10}; do
      sleep 0.2
      if is_server_running; then
        server_ok=1
        break
      fi
    done
    if [[ $server_ok -ne 1 ]]; then
      local err_detail=""
      if [[ -f "$server_log" ]]; then
        err_detail="$(tr '\n' ' ' < "$server_log" | sed 's/[[:space:]]\+/ /g' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        rm -f "$server_log"
      fi
      cleanup_bootstrap_lock
      local reason_msg="server daemon failed to report running status after 2000ms"
      if [[ -n "$err_detail" ]]; then
        reason_msg="$reason_msg: $err_detail"
      fi
      python3 -c '
import sys, json
session = sys.argv[1]
reason = sys.argv[2]
if "PermissionDenied" in reason or "Operation not permitted" in reason:
    reason = f"{reason} (named-session socket creation requires unsandboxed execution)"
print(json.dumps({
    "status": "error",
    "error": "session_init_failed",
    "session": session,
    "reason": reason
}))
' "$SESSION_NAME" "$reason_msg"
      exit 1
    fi
    local bootstrap_record
    bootstrap_record="$session_root/bootstrap.json"
    python3 - "$bootstrap_record" "$server_pid" "$SESSION_NAME" "$server_log" \
      "$EMIT_DAEMON_EVIDENCE" <<'PY'
import json, os, sys, tempfile, time
target, pid, session, log_path, preserve_log = sys.argv[1:]
record = {"session": session, "pid": int(pid) if pid.isdigit() else None, "started_at": time.time()}
if preserve_log == "1":
    record["log_path"] = log_path
fd, temp = tempfile.mkstemp(prefix=".bootstrap.", dir=os.path.dirname(target))
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(record, fh)
os.replace(temp, target)
PY
    if [[ $EMIT_DAEMON_EVIDENCE -ne 1 ]]; then
      rm -f "$server_log"
    fi
    cleanup_bootstrap_lock
  fi
}

has_managed_activity() {
  local state_dir task_file
  state_dir="${AGENT_HERDER_STATE_DIR:-$HOME/.config/.agents/herder}"
  task_file="$state_dir/$SESSION_NAME/tasks.json"
  [[ -s "$task_file" ]] || return 1
  python3 - "$task_file" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
if isinstance(data, dict):
    data = data.get("agents", data)
active = (data.values() if isinstance(data, dict) else data)
sys.exit(0 if any(isinstance(item, dict) and item.get("lifecycle", "active") == "active"
                  for item in active) else 1)
PY
}

refuse_unsafe_restart() {
  python3 -c '
import json, sys
print(json.dumps({
  "status":"error", "error":"session_recovery_required", "session":sys.argv[1],
  "reason":"the named server is stopped while managed-agent state exists; refusing to restore bare panes",
  "hint":"rerun with --recover-session to restart and reconcile missing agents"
}))
' "$SESSION_NAME"
  exit 1
}

recover_session_if_requested() {
  if [[ $RECOVER_SESSION -eq 1 ]]; then
    python3 "$FLEET_PY" --session "$SESSION_NAME" --allow-cross-session reconcile || exit $?
  fi
}

get_or_create_workspace() {
  local target_cwd="$1"
  local wid
  wid=$(herdr --session "$SESSION_NAME" workspace list 2>/dev/null | jq -r '.result.workspaces[0].workspace_id // empty' 2>/dev/null)
  if [[ -z "$wid" ]]; then
    local create_out
    create_out=$(herdr --session "$SESSION_NAME" workspace create --cwd "$target_cwd" 2>/dev/null)
    wid=$(echo "$create_out" | jq -r '.result.workspace.workspace_id // empty' 2>/dev/null)
  fi
  if [[ -z "$wid" ]]; then
    python3 -c '
import sys, json
print(json.dumps({
    "status": "error",
    "error": "session_init_failed",
    "session": sys.argv[1],
    "reason": "failed to provision workspace"
}))
' "$SESSION_NAME"
    exit 1
  fi
  echo "$wid"
}

# Handle init subcommand
if [[ "${#ARGS[@]}" -gt 0 && "${ARGS[0]}" == "init" ]]; then
  INIT_CWD="$(pwd)"
  INIT_REST=("${ARGS[@]:1}")
  idx=0
  while [[ $idx -lt ${#INIT_REST[@]} ]]; do
    case "${INIT_REST[$idx]}" in
      --cwd)
        idx=$((idx + 1))
        if [[ $idx -lt ${#INIT_REST[@]} ]]; then
          INIT_CWD="${INIT_REST[$idx]}"
        fi
        ;;
      --cwd=*)
        INIT_CWD="${INIT_REST[$idx]#*=}"
        ;;
      *)
        ;;
    esac
    idx=$((idx + 1))
  done

  if [[ ! -d "$INIT_CWD" ]]; then
    python3 -c '
import sys, json
print(json.dumps({
    "status": "error",
    "error": "session_init_failed",
    "session": sys.argv[1],
    "reason": f"working directory does not exist: {sys.argv[2]}"
}))
' "$SESSION_NAME" "$INIT_CWD"
    exit 1
  fi

  if ! is_server_running && has_managed_activity && [[ $RECOVER_SESSION -ne 1 ]]; then
    refuse_unsafe_restart
  fi
  ensure_server_running
  recover_session_if_requested
  WID=$(get_or_create_workspace "$INIT_CWD")

  STATE_DIR="${AGENT_HERDER_STATE_DIR:-$HOME/.config/.agents/herder}"
  EVENTS_DIR="$STATE_DIR/$SESSION_NAME/events"
  mkdir -p "$EVENTS_DIR/tmp"
  rm -f "$EVENTS_DIR"/*.json "$EVENTS_DIR"/tmp/*.json 2>/dev/null || true

  STATUS_JSON=$(herdr --session "$SESSION_NAME" status server --json 2>/dev/null)
  SOCK_PATH=$(echo "$STATUS_JSON" | jq -r '.socket // empty' 2>/dev/null)
  if [[ -z "$SOCK_PATH" || "$SOCK_PATH" == "null" ]]; then
    SOCK_PATH="${HERDR_CONFIG_DIR:-$HOME/.config/herdr}/sessions/$SESSION_NAME/herdr.sock"
  fi

  if [[ $EMIT_DAEMON_EVIDENCE -eq 1 ]]; then
    BOOTSTRAP_RECORD="${HERDR_CONFIG_DIR:-$HOME/.config/herdr}/sessions/$SESSION_NAME/bootstrap.json"
    python3 - "$SESSION_NAME" "$WID" "$SOCK_PATH" "$BOOTSTRAP_RECORD" <<'PY'
import json, sys
session, workspace, socket_path, record_path = sys.argv[1:]
try:
    record = json.load(open(record_path, encoding="utf-8"))
except Exception:
    record = {}
print(json.dumps({
    "session": session,
    "status": "ready",
    "workspace_id": workspace,
    "socket_path": socket_path,
    "daemon_pid": record.get("pid"),
    "daemon_log_path": record.get("log_path"),
}))
PY
  else
    python3 -c '
import sys, json
print(json.dumps({
    "session": sys.argv[1],
    "status": "ready",
    "workspace_id": sys.argv[2],
    "socket_path": sys.argv[3]
}))
' "$SESSION_NAME" "$WID" "$SOCK_PATH"
  fi
  exit 0
fi

# Guarded attach never silently restores a stopped session that had managed workers.
if [[ "${#ARGS[@]}" -gt 0 && "${ARGS[0]}" == "attach" ]]; then
  if ! is_server_running; then
    if has_managed_activity && [[ $RECOVER_SESSION -ne 1 ]]; then
      refuse_unsafe_restart
    fi
    ensure_server_running
    recover_session_if_requested
  fi
  exec herdr session attach "$SESSION_NAME"
fi

# Normal fleet operations never start or restore a server. A lost server must be surfaced to
# the supervisor instead of creating a second daemon that restores only shell panes.
if ! is_server_running; then
  if [[ $RECOVER_SESSION -eq 1 ]]; then
    ensure_server_running
    recover_session_if_requested
  else
    if has_managed_activity; then
      HINT="run herd.sh --session <name> attach --recover-session after reviewing the loss"
    else
      HINT="run herd.sh --session <name> init before using fleet commands"
    fi
    python3 -c '
import json, sys
print(json.dumps({"status":"error","error":"session_server_not_running","session":sys.argv[1],"hint":sys.argv[2]}))
' "$SESSION_NAME" "$HINT"
    exit 1
  fi
fi

WID=""
if [[ "${#ARGS[@]}" -gt 0 && ("${ARGS[0]}" == "spawn" || "${ARGS[0]}" == "reset") ]]; then
  WID=$(herdr --session "$SESSION_NAME" workspace list 2>/dev/null | jq -r '.result.workspaces[0].workspace_id // empty' 2>/dev/null)
  if [[ -z "$WID" ]]; then
    python3 -c '
import json, sys
print(json.dumps({"status":"error","error":"session_workspace_missing","session":sys.argv[1],"hint":"run herd.sh --session <name> init before spawning workers"}))
' "$SESSION_NAME"
    exit 1
  fi
fi

# Inject --workspace-id if spawning or resetting
if [[ "${#ARGS[@]}" -gt 0 && ("${ARGS[0]}" == "spawn" || "${ARGS[0]}" == "reset") ]]; then
  has_wid=0
  for arg in "${ARGS[@]}"; do
    if [[ "$arg" == "--workspace-id" || "$arg" == --workspace-id=* ]]; then
      has_wid=1
      break
    fi
  done
  if [[ $has_wid -eq 0 && -n "$WID" ]]; then
    ARGS+=("--workspace-id" "$WID")
  fi
fi

# Run the fleet command against the headless session, allowing cross-session
exec python3 "$FLEET_PY" --session "$SESSION_NAME" --allow-cross-session "${ARGS[@]}"
