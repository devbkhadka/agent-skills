#!/bin/bash
# scripts/cleanup_fleet.sh
# Safely tears down a headless herdr session and reconciles locks.

for _extra_path in /opt/homebrew/bin /opt/homebrew/sbin /usr/local/bin "$HOME/.local/bin"; do
  if [[ -d "$_extra_path" && ":$PATH:" != *":$_extra_path:"* ]]; then
    PATH="$_extra_path:$PATH"
  fi
done
export PATH

SESSION_NAME=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION_NAME="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ -z "$SESSION_NAME" ]]; then
  echo "Error: --session <unique_name> is required."
  exit 1
fi

echo "Cleaning up headless session: $SESSION_NAME"
S="$(dirname "$0")"

# Attempt to reconcile any leftover locks/assignments
python3 "$S/fleet.py" --session "$SESSION_NAME" --allow-cross-session reconcile >/dev/null 2>&1 || true

# Stop and delete the herdr session
herdr session stop "$SESSION_NAME" >/dev/null 2>&1 || true
herdr session delete "$SESSION_NAME" >/dev/null 2>&1 || true

echo "Cleanup complete."
