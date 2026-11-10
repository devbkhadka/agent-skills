#!/usr/bin/env bash
# Contract test: the installed `sched` CLI still exposes the subcommand
# surface this skill documents. Run this after any mac-scheduler
# reinstall/upgrade to catch a drifted CLI before an agent trusts stale
# SKILL.md guidance.
#
# Exits non-zero (with the specific missing subcommand named) on any
# mismatch, per this skill's tests contract.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
S="$SKILL_DIR/scripts"

SCHED="$("$S/sched-path.sh")"
status=$?
if [ $status -ne 0 ] || [ -z "$SCHED" ]; then
  echo "FAIL: could not resolve the installed 'sched' CLI via $S/sched-path.sh" >&2
  exit 1
fi
echo "Resolved sched CLI: $SCHED"

HELP_OUTPUT="$("$SCHED" --help 2>&1)"
help_status=$?
if [ $help_status -ne 0 ]; then
  echo "FAIL: '$SCHED --help' exited with status $help_status" >&2
  echo "$HELP_OUTPUT" >&2
  exit 1
fi

# The finalized `sched` subcommand surface (M2, mac-scheduler/PLAN.md).
SUBCOMMANDS=(add list status retry remove schedule)

missing=()
for cmd in "${SUBCOMMANDS[@]}"; do
  if ! grep -qE "^\s*${cmd}([[:space:]]|\$)" <<<"$HELP_OUTPUT"; then
    missing+=("$cmd")
  fi
done

if [ "${#missing[@]}" -gt 0 ]; then
  echo "FAIL: 'sched --help' output is missing documented subcommand(s): ${missing[*]}" >&2
  echo "--- sched --help output ---" >&2
  echo "$HELP_OUTPUT" >&2
  exit 1
fi

echo "PASS: all documented subcommands (${SUBCOMMANDS[*]}) present in 'sched --help'."
exit 0
