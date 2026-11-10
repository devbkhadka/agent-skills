#!/usr/bin/env bash
# Terminal Agent Runner preflight: prove the environment works BEFORE spawning a fleet.
#
# Exists because the expensive failures are the ones you discover late: a CLI
# whose quota is exhausted is free to detect here and costs a full spawn +
# prompt + read cycle per agent to discover after the fact.
#
# Usage: preflight.sh [--session NAME] [--cli claude,agy,omp,codex] [--cwd PATH] [--no-quota] [--allow-cross-session] [--json]
# Exit:  0 all good | 1 hard blocker | 2 a CLI is unusable (others may be fine)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SESSION=""; CLIS="claude,agy,omp,codex"; CWD="$PWD"; QUOTA=1; ALLOW_CROSS=0; JSON_MODE=0
need_value() { [ $# -ge 2 ] && [ -n "$2" ] || { echo "missing value for $1" >&2; exit 1; }; }
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      echo "Usage: preflight.sh [--session NAME] [--cli LIST] [--cwd PATH] [--no-quota] [--allow-cross-session] [--json]"
      echo "Defaults: --cli claude,agy,omp,codex --cwd current-directory"
      exit 0 ;;
    --session) need_value "$@"; SESSION="$2"; shift 2 ;;
    --cli)     need_value "$@"; CLIS="$2";    shift 2 ;;
    --cwd)     need_value "$@"; CWD="$2";     shift 2 ;;
    --no-quota) QUOTA=0;     shift ;;
    --allow-cross-session) ALLOW_CROSS=1; shift ;;
    --json)    JSON_MODE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

RC=0
log()  { [ "$JSON_MODE" -eq 1 ] || echo "$@"; }
ok()   { [ "$JSON_MODE" -eq 1 ] || printf '  \033[32mOK\033[0m    %s\n' "$1"; }
warn() { [ "$JSON_MODE" -eq 1 ] || printf '  \033[33mWARN\033[0m  %s\n' "$1"; }
bad()  { [ "$JSON_MODE" -eq 1 ] || printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }

PREFLIGHT_MODE="unattached"
if [ "${HERDR_ENV:-}" = "1" ]; then
  PREFLIGHT_MODE="attached"
fi

if [ -n "$SESSION" ]; then
  if [ "${#SESSION}" -gt 64 ]; then
    echo "invalid session identifier: longer than 64 characters" >&2; exit 1
  fi
  case "$SESSION" in
    [A-Za-z0-9]* ) ;;
    *) echo "invalid session identifier: $SESSION" >&2; exit 1 ;;
  esac
  case "$SESSION" in
    *[!A-Za-z0-9_.-]*) echo "invalid session identifier: $SESSION" >&2; exit 1 ;;
  esac
fi
if [ ! -d "$CWD" ]; then
  echo "working directory does not exist: $CWD" >&2
  exit 1
fi
CWD="$(cd "$CWD" 2>/dev/null && pwd -P)" || exit 1

log "== environment =="
if [ "$PREFLIGHT_MODE" = "attached" ]; then
  ok "HERDR_ENV=1 -- attached mode (running inside a Herdr-managed pane)"
else
  ok "HERDR_ENV unset -- un-attached/headless mode (default workflow, outside a Herdr pane)"
  log "        Default:  bash herd.sh --session <name> init  starts a headless server."
  log "        Attach:   bash herd.sh --session <name> attach  (guarded against unsafe restart)."
fi

CWD_WRITABLE=false
[ -r "$CWD" ] || { bad "working directory is not readable: $CWD"; exit 1; }
if [ -w "$CWD" ]; then
  CWD_WRITABLE=true
  ok "working directory readable/writable: $CWD"
else
  warn "working directory is read-only: $CWD (mutating assignments will fail)"
fi

HERDR_PRESENT=false
HERDR_VERSION=""
if command -v herdr >/dev/null 2>&1; then
  HERDR_PRESENT=true
  HV="$(herdr --version 2>&1 | head -1)"
  HERDR_VERSION="$HV"
  ok "herdr present: $HV"
  case "$HV" in
    *0.6.*|*0.7.*|*0.8.*) : ;;
    *) warn "skill verified against herdr 0.6-0.8.x; CLI surface may differ on $HV" ;;
  esac
else
  bad "herdr not on PATH"
  exit 1
fi

command -v python3 >/dev/null 2>&1 || { bad "python3 not on PATH"; exit 1; }

log
log "== session targeting =="
SESSION_TARGET=""
SESSION_SOCKET=""
AGENTS_RUNNING=0

if [ -n "$SESSION" ] && [ "$SESSION" != "default" ]; then
  SESSION_TARGET="$SESSION"
  SOCK="$HOME/.config/herdr/sessions/$SESSION/herdr.sock"
  if [ -S "$SOCK" ]; then
    if [ -n "${HERDR_SOCKET_PATH:-}" ] && [ "$HERDR_SOCKET_PATH" != "$SOCK" ] && [ "$ALLOW_CROSS" -ne 1 ]; then
      bad "herder is attached to $HERDR_SOCKET_PATH but --session targets $SOCK"
      log "        Omit --session to use this workspace, or pass --allow-cross-session intentionally."
      exit 1
    fi
    export HERDR_SOCKET_PATH="$SOCK"
    SESSION_SOCKET="$SOCK"
    ok "session '$SESSION' -> $SOCK"
  else
    bad "session '$SESSION' has no socket at $SOCK -- the herdr server for this session is not running."
    if [ "$PREFLIGHT_MODE" = "unattached" ]; then
      log "        Fix (un-attached):  bash herd.sh --session $SESSION init"
      log "          Normal fleet commands never restart a stopped session."
    else
      log "        Fix (attached):     bash herd.sh --session $SESSION attach"
    fi
    [ "$JSON_MODE" -eq 1 ] || herdr session list 2>&1 | sed 's/^/        /'
    exit 1
  fi
elif [ "$SESSION" = "default" ]; then
  SESSION_TARGET="default"
  SOCK="$HOME/.config/herdr/herdr.sock"
  if [ -n "${HERDR_SOCKET_PATH:-}" ] && [ "$HERDR_SOCKET_PATH" != "$SOCK" ] && [ "$ALLOW_CROSS" -ne 1 ]; then
    bad "herder is attached to $HERDR_SOCKET_PATH but --session default targets $SOCK"
    exit 1
  fi
  export HERDR_SOCKET_PATH="$SOCK"
  SESSION_SOCKET="$SOCK"
  ok "session 'default' -> $SOCK"
else
  SESSION_TARGET=""
  SESSION_SOCKET="${HERDR_SOCKET_PATH:-}"
  ok "using herder's current workspace (HERDR_SOCKET_PATH=${HERDR_SOCKET_PATH:-<unset>})"
fi

AGENT_JSON="$(herdr agent list 2>&1 || true)"
N="$(printf '%s' "$AGENT_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); a=d['result']['agents']; assert isinstance(a,list); print(len(a))" 2>/dev/null || true)"
if [ -n "$N" ]; then
  AGENTS_RUNNING="$N"
  ok "socket reachable; $N agent(s) already running"
else
  bad "cannot query agents over the socket"; exit 1
fi

log
log "== pane geometry =="
if [ "$PREFLIGHT_MODE" = "unattached" ]; then
  ok "pane geometry check skipped -- headless un-attached mode has no herding pane"
else
  GEO="$(herdr pane layout 2>/dev/null | python3 -c "
import sys,json
try:
    a=json.load(sys.stdin)['result']['layout']['area']; print(a['height'],a['width'])
except Exception: print('0 0')
" 2>/dev/null)"
  H=${GEO% *}; W=${GEO#* }
  if [ "${H:-0}" -ge 20 ] && [ "${W:-0}" -ge 80 ]; then
    ok "workspace ${H}x${W} -- comfortable"
  elif [ "${H:-0}" -ge 12 ]; then
    warn "workspace ${H}x${W} is narrow; agent output will hard-wrap (classifier compensates, but keep ONE TAB PER AGENT)"
  else
    warn "workspace ${H}x${W} is very small; TUI prompts may not render"
  fi
fi

log
log "== agent CLIs =="
IFS=',' read -ra LIST <<< "$CLIS"
SEEN=","
for cli in "${LIST[@]}"; do
  [ -z "$cli" ] && continue
  case "$cli" in
    *[[:space:]]*) bad "invalid CLI identifier '$cli' (whitespace/flags are forbidden)"; exit 1 ;;
  esac
  case "$cli" in
    codex|claude|agy|omp) ;;
    *) bad "invalid CLI identifier '$cli' (flags and paths are forbidden)"; exit 1 ;;
  esac
  case "$SEEN" in *",$cli,"*) bad "duplicate CLI identifier '$cli'"; exit 1 ;; esac
  SEEN="$SEEN$cli,"
done

CLI_PROBE_JSON="$(python3 - "$CLIS" "$CWD" "$QUOTA" <<'PY'
import json, os, re, shutil, subprocess, sys

clis_arg = sys.argv[1]
cwd = sys.argv[2]
quota_flag = int(sys.argv[3])
clis_list = [c for c in clis_arg.split(',') if c]

def parse_quota_duration(text):
    if not text:
        return None, None
    m = re.search(r'(?:resets?|retry)(?:\s+(?:in|after))?\s+([0-9]+[dhms](?:[0-9\s]+[dhms])*)', text, re.IGNORECASE)
    if m:
        raw_dur = m.group(1).strip()
        total_seconds = 0
        comp_matches = re.findall(r'(\d+)\s*([dhms])', raw_dur, re.IGNORECASE)
        if comp_matches:
            multipliers = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}
            for val, unit in comp_matches:
                total_seconds += int(val) * multipliers[unit.lower()]
            return raw_dur, total_seconds

    m_words = re.search(r'(?:resets?|retry)(?:\s+(?:in|after))?\s+((?:\d+\s*(?:days?|hours?|hrs?|minutes?|mins?|seconds?|secs?)\s*(?:and|,)?\s*)+)', text, re.IGNORECASE)
    if m_words:
        raw_dur = m_words.group(1).strip().rstrip(',.')
        total_seconds = 0
        comp_matches = re.findall(r'(\d+)\s*(days?|hours?|hrs?|minutes?|mins?|seconds?|secs?)', raw_dur, re.IGNORECASE)
        if comp_matches:
            for val, unit in comp_matches:
                u = unit.lower()
                if u.startswith('d'): mult = 86400
                elif u.startswith('h'): mult = 3600
                elif u.startswith('m'): mult = 60
                elif u.startswith('s'): mult = 1
                else: mult = 0
                total_seconds += int(val) * mult
            return raw_dur, total_seconds

    return None, None

cli_results = {}
cli_rc = 0

for cli in clis_list:
    cli_path = shutil.which(cli)
    if not cli_path:
        cli_results[cli] = {
            "present": False,
            "path": None,
            "usable": False,
            "probe_status": "missing",
            "reason": f"{cli} not on PATH",
            "retry_after": None,
            "retry_after_seconds": None,
            "flag_ok": True,
            "probe_out": ""
        }
        if cli_rc == 0:
            cli_rc = 2
        continue

    flag_ok = True
    flag_reason = None
    try:
        help_out = subprocess.run([cli, "--help"], capture_output=True, text=True, timeout=10).stdout or ""
    except Exception:
        help_out = ""

    if cli == "codex":
        c1 = "--sandbox" in help_out and "--ask-for-approval" in help_out
        c2 = subprocess.run([cli, "--approve-for-me", "--help"], capture_output=True, timeout=10).returncode == 0
        c3 = subprocess.run([cli, "--sandbox", "read-only", "--ask-for-approval", "never", "--help"], capture_output=True, timeout=10).returncode == 0
        if not (c1 and c2 and c3):
            flag_ok = False
            flag_reason = "codex lacks required --sandbox/--ask-for-approval controls"
    elif cli == "claude":
        c1 = "--permission-mode" in help_out
        c2 = subprocess.run([cli, "--permission-mode", "auto", "--help"], capture_output=True, timeout=10).returncode == 0
        c3 = subprocess.run([cli, "--permission-mode", "plan", "--help"], capture_output=True, timeout=10).returncode == 0
        if not (c1 and c2 and c3):
            flag_ok = False
            flag_reason = "claude lacks required --permission-mode control"

    if not flag_ok:
        cli_results[cli] = {
            "present": True,
            "path": cli_path,
            "usable": False,
            "probe_status": "inconclusive",
            "reason": flag_reason,
            "retry_after": None,
            "retry_after_seconds": None,
            "flag_ok": False,
            "probe_out": ""
        }
        if cli_rc == 0:
            cli_rc = 2
        continue

    if quota_flag == 0:
        cli_results[cli] = {
            "present": True,
            "path": cli_path,
            "usable": True,
            "probe_status": "unverified",
            "reason": None,
            "retry_after": None,
            "retry_after_seconds": None,
            "flag_ok": True,
            "probe_out": ""
        }
        continue

    # Run probe
    if cli == "codex":
        argv = [cli, "exec", "--ephemeral", "-C", cwd, "reply with the single word: ready"]
    else:
        argv = [cli, "-p", "reply with the single word: ready"]

    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=90, cwd=cwd)
        out = (((r.stdout or "") + "\n" + (r.stderr or "")).strip())
    except FileNotFoundError:
        out = "__PROBE_MISSING__"
    except subprocess.TimeoutExpired:
        out = "__PROBE_TIMEOUT__"
    except Exception as e:
        out = f"__PROBE_ERROR__ {e}"

    if out == "__PROBE_TIMEOUT__":
        cli_results[cli] = {
            "present": True,
            "path": cli_path,
            "usable": True,
            "probe_status": "timeout",
            "reason": "probe timed out",
            "retry_after": None,
            "retry_after_seconds": None,
            "flag_ok": True,
            "probe_out": out
        }
    elif out == "__PROBE_MISSING__":
        cli_results[cli] = {
            "present": False,
            "path": cli_path,
            "usable": False,
            "probe_status": "missing",
            "reason": "command not found during probe",
            "retry_after": None,
            "retry_after_seconds": None,
            "flag_ok": True,
            "probe_out": out
        }
        if cli_rc == 0:
            cli_rc = 2
    elif out.startswith("__PROBE_ERROR__"):
        cli_results[cli] = {
            "present": True,
            "path": cli_path,
            "usable": True,
            "probe_status": "inconclusive",
            "reason": out,
            "retry_after": None,
            "retry_after_seconds": None,
            "flag_ok": True,
            "probe_out": out
        }
    elif re.search(r"quota reached|rate limit|usage limit|upgrade your subscription", out, re.IGNORECASE):
        dur_str, dur_secs = parse_quota_duration(out)
        matching_lines = [line.strip() for line in out.splitlines() if re.search(r"quota|limit|resets|upgrade", line, re.IGNORECASE)]
        reason = matching_lines[0] if matching_lines else "quota exhausted"
        cli_results[cli] = {
            "present": True,
            "path": cli_path,
            "usable": False,
            "probe_status": "quota_exhausted",
            "reason": reason,
            "retry_after": dur_str,
            "retry_after_seconds": dur_secs,
            "flag_ok": True,
            "probe_out": out
        }
        if cli_rc == 0:
            cli_rc = 2
    elif re.search(r"not (logged in|authenticated)|please (log ?in|sign ?in)|invalid api key|unauthorized", out, re.IGNORECASE):
        matching_lines = [line.strip() for line in out.splitlines() if re.search(r"not (logged in|authenticated)|please (log ?in|sign ?in)|invalid api key|unauthorized", line, re.IGNORECASE)]
        reason = matching_lines[0] if matching_lines else "not authenticated"
        cli_results[cli] = {
            "present": True,
            "path": cli_path,
            "usable": False,
            "probe_status": "unauthenticated",
            "reason": reason,
            "retry_after": None,
            "retry_after_seconds": None,
            "flag_ok": True,
            "probe_out": out
        }
        if cli_rc == 0:
            cli_rc = 2
    elif not out:
        cli_results[cli] = {
            "present": True,
            "path": cli_path,
            "usable": True,
            "probe_status": "unverified",
            "reason": None,
            "retry_after": None,
            "retry_after_seconds": None,
            "flag_ok": True,
            "probe_out": out
        }
    else:
        cli_results[cli] = {
            "present": True,
            "path": cli_path,
            "usable": True,
            "probe_status": "ready",
            "reason": None,
            "retry_after": None,
            "retry_after_seconds": None,
            "flag_ok": True,
            "probe_out": out
        }

print(json.dumps({"results": cli_results, "cli_rc": cli_rc}))
PY
)"

CLI_RC="$(printf '%s' "$CLI_PROBE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['cli_rc'])")"
if [ "$CLI_RC" -ne 0 ] && [ "$RC" -eq 0 ]; then
  RC="$CLI_RC"
fi

if [ "$JSON_MODE" -ne 1 ]; then
  for cli in "${LIST[@]}"; do
    [ -z "$cli" ] && continue
    CLI_DATA="$(printf '%s' "$CLI_PROBE_JSON" | python3 -c "
import sys,json
d=json.load(sys.stdin)['results']['$cli']
print(json.dumps([d['present'], d['usable'], d['path'] or '', d['probe_status'], d['reason'] or '', d['flag_ok'], d['probe_out']]))
")"
    CLI_PRESENT="$(printf '%s' "$CLI_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)[0])")"
    CLI_USABLE="$(printf '%s' "$CLI_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)[1])")"
    CLI_PATH_VAL="$(printf '%s' "$CLI_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)[2])")"
    CLI_PSTATUS="$(printf '%s' "$CLI_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)[3])")"
    CLI_REASON="$(printf '%s' "$CLI_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)[4])")"
    CLI_FLAG_OK="$(printf '%s' "$CLI_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)[5])")"
    CLI_POUT="$(printf '%s' "$CLI_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)[6])")"

    if [ "$CLI_USABLE" = "True" ]; then
      ok "$cli present: $CLI_PATH_VAL"
      if [ "$cli" = "codex" ]; then
        ok "codex accepts automatic and enforced read-only mappings"
      elif [ "$cli" = "claude" ]; then
        ok "claude accepts automatic and enforced plan mappings"
      fi
      if [ "$CLI_PSTATUS" = "ready" ]; then
        SNIPPET="$(echo "$CLI_POUT" | tr -d '\n' | cut -c1-50)"
        ok "$cli auth OK (print mode): $SNIPPET"
        warn "$cli interactive tier unverified -- run 'fleet.py status' after spawn"
      elif [ "$CLI_PSTATUS" = "timeout" ] || [ "$CLI_PSTATUS" = "inconclusive" ]; then
        warn "$cli probe inconclusive ($CLI_POUT) -- verify after spawn with 'fleet.py status'"
      elif [ "$CLI_PSTATUS" = "unverified" ]; then
        if [ "$QUOTA" -eq 1 ]; then
          warn "$cli probe returned nothing -- treat as unverified"
        fi
      fi
    else
      # Usability failed - do NOT print OK present line
      if [ "$CLI_PSTATUS" = "missing" ]; then
        bad "$cli not on PATH"
      elif [ "$CLI_FLAG_OK" = "False" ]; then
        bad "$CLI_REASON"
      elif [ "$CLI_PSTATUS" = "quota_exhausted" ]; then
        bad "$cli QUOTA EXHAUSTED -- do not spawn it:"
        echo "$CLI_POUT" | grep -iE "quota|limit|resets|upgrade" | head -3 | sed 's/^/        /'
      elif [ "$CLI_PSTATUS" = "unauthenticated" ]; then
        bad "$cli NOT AUTHENTICATED"
      else
        bad "$cli failed check: $CLI_REASON"
      fi
    fi
  done
fi

log
log "== resource pool =="
POOL="$SCRIPT_DIR/resource_pool.py"
FLEET="$SCRIPT_DIR/fleet.py"
POLICY="$SCRIPT_DIR/risk_policy.json"
POOL_VALID=false
STALE_LEASES=0

if [ -f "$POOL" ] && [ -f "$FLEET" ] && [ -f "$POLICY" ]; then
  POOL_SYNTAX_OK=true
  python3 -c 'import pathlib,sys; [compile(pathlib.Path(p).read_text(), p, "exec") for p in sys.argv[1:]]' "$POOL" "$FLEET" >/dev/null 2>&1 \
    || { bad "fleet/resource scripts fail Python syntax validation"; RC=1; POOL_SYNTAX_OK=false; }
  python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); assert isinstance(p.get("deny"),list) and isinstance(p.get("allow"),list)' "$POLICY" >/dev/null 2>&1 \
    || { bad "risk_policy.json is invalid"; RC=1; POOL_SYNTAX_OK=false; }
  
  POOL_STATE_OK=true
  if python3 "$POOL" status --no-reap >/dev/null 2>&1; then
    ok "resource_pool state is readable and schema-valid"
  else
    bad "resource_pool state is corrupt or inaccessible (refusing fail-open recovery)"
    RC=1
    POOL_STATE_OK=false
  fi

  FLEET_STATE_OK=true
  FLEET_ARGS=()
  [ -n "$SESSION" ] && FLEET_ARGS+=(--session "$SESSION")
  [ "$ALLOW_CROSS" -eq 1 ] && FLEET_ARGS+=(--allow-cross-session)
  FLEET_ARGS+=(status --json)
  if python3 "$FLEET" "${FLEET_ARGS[@]}" >/dev/null 2>&1; then
    ok "fleet assignment state and agent buffers are readable"
  else
    bad "fleet assignment state or agent buffers are unavailable/corrupt"
    RC=1
    FLEET_STATE_OK=false
  fi

  if [ "$POOL_SYNTAX_OK" = "true" ] && [ "$POOL_STATE_OK" = "true" ] && [ "$FLEET_STATE_OK" = "true" ]; then
    POOL_VALID=true
  fi

  STALE="$(python3 "$POOL" reap --dry-run 2>/dev/null | python3 -c "
import sys,json
try: print(json.load(sys.stdin)['reaped_count'])
except Exception: print(0)" 2>/dev/null || echo 0)"
  STALE_LEASES="${STALE:-0}"
  [ "${STALE:-0}" -gt 0 ] && warn "$STALE stale lease(s) from a previous run -- 'resource_pool.py reap' will reclaim them"
else
  bad "fleet.py, resource_pool.py, or risk_policy.json missing next to preflight.sh"; RC=1
fi

if [ "$JSON_MODE" -eq 1 ]; then
  python3 - "$RC" "$PREFLIGHT_MODE" "$CWD" "$CWD_WRITABLE" "$HERDR_PRESENT" "$HERDR_VERSION" "$SESSION_TARGET" "$SESSION_SOCKET" "$AGENTS_RUNNING" "$POOL_VALID" "$STALE_LEASES" "$CLI_PROBE_JSON" <<'PY'
import json, sys

rc = int(sys.argv[1])
preflight_mode = sys.argv[2]
cwd = sys.argv[3]
cwd_writable = (sys.argv[4].lower() == "true")
herdr_present = (sys.argv[5].lower() == "true")
herdr_version = sys.argv[6] if sys.argv[6] else None
session_target = sys.argv[7] if sys.argv[7] else None
session_socket = sys.argv[8] if sys.argv[8] else None
agents_running = int(sys.argv[9]) if sys.argv[9].isdigit() else 0
pool_valid = (sys.argv[10].lower() == "true")
stale_leases = int(sys.argv[11]) if sys.argv[11].isdigit() else 0
cli_probe_json = json.loads(sys.argv[12])

status = "passed" if rc == 0 else ("partial" if rc == 2 else "failed")

clis_payload = {}
for cli, d in cli_probe_json["results"].items():
    clis_payload[cli] = {
        "present": d["present"],
        "path": d["path"],
        "usable": d["usable"],
        "probe_status": d["probe_status"],
        "reason": d["reason"],
        "retry_after": d["retry_after"],
        "retry_after_seconds": d["retry_after_seconds"]
    }

payload = {
    "status": status,
    "exit_code": rc,
    "clis": clis_payload,
    "environment": {
        "mode": preflight_mode,
        "cwd": cwd,
        "cwd_writable": cwd_writable,
        "herdr_present": herdr_present,
        "herdr_version": herdr_version
    },
    "session": {
        "target": session_target,
        "socket": session_socket,
        "agents_running": agents_running
    },
    "resource_pool": {
        "valid": pool_valid,
        "stale_leases": stale_leases
    }
}
print(json.dumps(payload, indent=2))
PY
  exit $RC
fi

log
case $RC in
  0) echo "PREFLIGHT PASSED -- safe to spawn." ;;
  2) echo "PREFLIGHT PARTIAL -- at least one CLI is unusable. Plan around it or reassign its tasks." ;;
  *) echo "PREFLIGHT FAILED." ;;
esac
exit $RC
