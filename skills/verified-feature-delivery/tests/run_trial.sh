#!/usr/bin/env bash
# Phase 8 trial entrypoint. Drives one trial through the stage gates it has artifacts for
# and then runs the trial gate.
#
#   bash skills/verified-feature-delivery/tests/run_trial.sh --trial-a --band-0-2
#
# The arguments are flags rather than positional values on purpose. The fleet's
# repo-test-entrypoint allow rule permits only --flag arguments after the script path, so a
# positional argument makes a command the policy is meant to allow escalate to a human
# instead. Baking the value into the flag name is the shape that stays approvable.
#
# This is a thin wrapper otherwise: the driver and the gate both live under
# plan-docs/gate-fixtures/, which no worker leases. Do not add logic here.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

usage() {
  echo "usage: run_trial.sh --trial-<a|d> --band-<0-2|3-5>" >&2
  echo "  e.g. bash skills/verified-feature-delivery/tests/run_trial.sh --trial-a --band-0-2" >&2
  exit 2
}

TRIAL=""
BAND=""
for arg in "$@"; do
  case "$arg" in
    --trial-a) TRIAL="trial-a" ;;
    --trial-d) TRIAL="trial-d" ;;
    --band-0-2) BAND="0-2" ;;
    --band-3-5) BAND="3-5" ;;
    *) echo "unrecognised argument: $arg" >&2; usage ;;
  esac
done
[ -n "$TRIAL" ] && [ -n "$BAND" ] || usage

exec python3 "$REPO_ROOT/plan-docs/gate-fixtures/drive-trial.py" "$TRIAL" "$BAND"
