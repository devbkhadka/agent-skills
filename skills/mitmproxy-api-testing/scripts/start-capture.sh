#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: bash start-capture.sh [--ui] APP_PROCESS_NAME [ADDON_SCRIPT] [-- MITMPROXY_ARGS...]"
  echo "Uses mitmdump by default; --ui selects mitmproxy."
  echo "MITMPROXY_CONFDIR overrides the shared folder: \${HOME}/.mitmproxy"
  echo "Use SIM_APP_NAME printed by prepare.sh for the intended simulator and app bundle ID."
  echo "First local capture needs one-time macOS approval of the Mitmproxy Redirector network extension."
  echo "Follow the macOS prompt in System Settings. The Mac system proxy stays unchanged."
}

capture_bin=mitmdump
if [[ "${1:-}" == --ui ]]; then
  capture_bin=mitmproxy
  shift
fi
if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
  usage
  exit 0
fi
if (( $# == 0 )) || [[ -z "$1" || "$1" == -* ]]; then
  usage >&2
  exit 1
fi
app_process=$1
shift
case "$app_process" in
  *','*|*'!'*|*':'*|*'@'*|*$'\n'*)
    echo "Error: provide one app process name, not a local capture selector list." >&2
    exit 1
    ;;
esac

addon_script=
if (( $# )) && [[ "$1" != -- ]]; then
  if [[ ! -f "$1" ]]; then
    echo "Error: addon script not found: $1" >&2
    exit 1
  fi
  addon_script=$1
  shift
fi
if (( $# )); then
  if [[ "$1" != -- ]]; then
    usage >&2
    exit 1
  fi
  shift
fi

if ! command -v "$capture_bin" >/dev/null 2>&1; then
  if [[ -x "/opt/homebrew/bin/$capture_bin" ]]; then
    capture_bin="/opt/homebrew/bin/$capture_bin"
  else
    echo "Error: $capture_bin is missing. Install mitmproxy and put its binaries on PATH." >&2
    exit 1
  fi
fi
confdir=${MITMPROXY_CONFDIR:-"$HOME/.mitmproxy"}
if [[ ! -f "$confdir/mitmproxy-ca-cert.pem" ]]; then
  echo "Error: shared certificate missing in $confdir. Run prepare.sh first." >&2
  exit 1
fi

echo "Capturing local process: $app_process; shared confdir: $confdir"
echo "First use: approve the Mitmproxy Redirector network extension when macOS prompts."
echo "Mac system proxy settings are unchanged. Stop capture with Ctrl-C."
if [[ -n "$addon_script" ]]; then
  set -- "$@" -s "$addon_script"
fi
exec "$capture_bin" "$@" --mode "local:$app_process" --set "confdir=$confdir" --set connection_strategy=lazy --set flow_detail=0
