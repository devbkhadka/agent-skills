#!/bin/bash
# tests/test_auto_herd_budget.sh
#
# auto_herd.sh working-state budget and blocked handoffs, against a stub herd.sh:
#   1. A working agent whose pane never changes is declared stalled once
#      --max-idle-seconds of inactivity elapse (exit 1, budget named in output).
#   2. A working agent whose pane keeps changing is never stalled, however many
#      polls run; the supervisor keeps going until the fleet drains (exit 0).
#   3. A working agent whose pane is static but shows foreground execution
#      (status row "foreground": true) is never stalled.
#   4. A worker in state handoff-blocked is set aside with its blocker (exit 1),
#      never re-prompted or completed.
#   5. A non-numeric budget is a usage error (exit 2).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_HERD="$SCRIPT_DIR/../scripts/auto_herd.sh"

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/test_auto_herd_budget.XXXXXX")"
export AGENT_HERDER_STATE_DIR="$SCRATCH/state"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

cat > "$SCRATCH/herd.sh" << 'STUB'
#!/bin/bash
# Stub herd.sh. MODE (file) selects the scenario; status polls are counted so a
# scenario can drain the fleet after enough polls to prove it was not stalled.
SCRATCH="$(dirname "$0")"
MODE="$(cat "$SCRATCH/mode")"
SUBCMD=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --session|--agent) shift 2 ;;
    watch|status|approve|complete|read|prompt) SUBCMD="$1"; shift ;;
    *) shift ;;
  esac
done
case "$SUBCMD" in
  watch) sleep 0.2; exit 0 ;;
  read)
    if [[ "$MODE" == "changing" ]]; then
      echo "step $(date +%s%N) $RANDOM"
    else
      echo "Reading files..."
    fi
    exit 0 ;;
  prompt|approve|complete)
    echo "$SUBCMD" >> "$SCRATCH/mutations.txt"
    exit 0 ;;
  status)
    n=$(( $(cat "$SCRATCH/polls" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$SCRATCH/polls"
    if [[ "$MODE" != "static" && "$MODE" != "blocked" && "$n" -gt 12 ]]; then
      echo '{"session":"s","agents":[]}'
      exit 0
    fi
    state="working"; fg=false; pending=null
    [[ "$MODE" == "foreground" ]] && fg=true
    if [[ "$MODE" == "blocked" ]]; then
      state="handoff-blocked"; pending='"lease too narrow for spec files"'
    fi
    cat << JSON
{"session":"s","agents":[{"agent":"worker-1","cli":"agy","state":"$state","health":"ok",
 "action":null,"rule":null,"pending":$pending,"parked":false,"has_handoff":false,
 "foreground":$fg}]}
JSON
    exit 0 ;;
esac
exit 0
STUB
chmod +x "$SCRATCH/herd.sh"
export HERD_BIN="$SCRATCH/herd.sh"

FAILED=0
run_case() {
  local mode="$1"; shift
  echo "$mode" > "$SCRATCH/mode"
  rm -f "$SCRATCH/polls" "$SCRATCH/mutations.txt"
  local rc=0
  OUT=$(bash "$AUTO_HERD" --session "budget-test-$mode" "$@" 2>&1) || rc=$?
  RC=$rc
}

expect() {
  local name="$1" cond="$2"
  if eval "$cond"; then
    echo "PASS: $name"
  else
    echo "FAIL: $name (rc=$RC)"; echo "$OUT" | sed 's/^/    /'
    FAILED=1
  fi
}

run_case static --max-idle-seconds 2
expect "static pane stalls after the idle budget" \
  '[[ $RC -eq 1 ]] && grep -q "execution budget exceeded" <<< "$OUT" && grep -q "max-idle-seconds" <<< "$OUT"'

run_case changing --budget 1
expect "changing pane is never stalled" '[[ $RC -eq 0 ]] && [[ $(cat "$SCRATCH/polls") -gt 12 ]]'

AGENT_HERDER_MAX_IDLE_SECONDS=1 run_case foreground
expect "foreground pane is never stalled" '[[ $RC -eq 0 ]] && ! grep -q "stalled" <<< "$OUT"'

run_case blocked
expect "handoff-blocked worker is set aside with its blocker" \
  '[[ $RC -eq 1 ]] && grep -q "blocked handoff" <<< "$OUT" && grep -q "lease too narrow" <<< "$OUT" && [[ ! -s "$SCRATCH/mutations.txt" ]]'

run_case static --max-idle-seconds soon
expect "non-numeric budget is a usage error" '[[ $RC -eq 2 ]]'

if [[ "$FAILED" -ne 0 ]]; then
  echo "test_auto_herd_budget.sh: FAILED"
  exit 1
fi
echo "test_auto_herd_budget.sh: all cases passed"
