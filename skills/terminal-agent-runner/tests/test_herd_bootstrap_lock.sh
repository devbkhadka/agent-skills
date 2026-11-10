#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERD_SH="$SCRIPT_DIR/../scripts/herd.sh"
TEST_ROOT="$(mktemp -d)"
MOCK_BIN="$TEST_ROOT/bin"
STATE_DIR="$TEST_ROOT/state"
CONFIG_DIR="$TEST_ROOT/config"
SESSION="lock-test-$$"

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

mkdir -p "$MOCK_BIN" "$STATE_DIR" "$CONFIG_DIR/sessions/$SESSION/.bootstrap.lock"
printf '{"pid":999999,"started_at":1}\n' > "$CONFIG_DIR/sessions/$SESSION/.bootstrap.lock/owner.json"

cat > "$MOCK_BIN/herdr" <<'EOF'
#!/bin/bash
if [[ "$3" == "status" && "$4" == "server" ]]; then
  if [[ -f "$MOCK_HERDR_STATE/running" ]]; then
    printf '{"status":"running","socket":"/tmp/mock.sock"}\n'
  else
    printf '{"status":"stopped"}\n'
  fi
  exit 0
fi
if [[ "$3" == "server" ]]; then
  touch "$MOCK_HERDR_STATE/running"
  exit 0
fi
if [[ "$3" == "workspace" && "$4" == "list" ]]; then
  printf '{"result":{"workspaces":[{"workspace_id":"w1"}]}}\n'
  exit 0
fi
printf '{"result":{}}\n'
EOF
chmod +x "$MOCK_BIN/herdr"

PATH="$MOCK_BIN:$PATH" MOCK_HERDR_STATE="$STATE_DIR" HERDR_CONFIG_DIR="$CONFIG_DIR" \
  AGENT_HERDER_STATE_DIR="$STATE_DIR/fleet" bash "$HERD_SH" --session "$SESSION" init --cwd "$TEST_ROOT"

if [[ -d "$CONFIG_DIR/sessions/$SESSION/.bootstrap.lock" ]]; then
  echo "FAIL: stale bootstrap lock survived recovery" >&2
  exit 1
fi

echo "PASS: stale bootstrap lock reaped safely"
