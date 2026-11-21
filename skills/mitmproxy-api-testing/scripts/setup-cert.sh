#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: bash setup-cert.sh [--udid SIMULATOR_UDID]"
  echo "Trust the shared CA in a booted iOS simulator (default: booted)."
  echo "MITMPROXY_CONFDIR overrides the shared folder: \${HOME}/.mitmproxy"
}

simulator=booted
while (( $# )); do
  case "$1" in
    --udid)
      if (( $# < 2 )) || [[ -z "$2" ]]; then
        echo "Error: --udid requires a simulator UDID." >&2
        exit 1
      fi
      simulator=$2
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 1 ;;
  esac
done

if command -v mitmdump >/dev/null 2>&1; then
  mitmdump_bin=mitmdump
elif [[ -x /opt/homebrew/bin/mitmdump ]]; then
  mitmdump_bin=/opt/homebrew/bin/mitmdump
else
  echo "Error: mitmdump is missing. Install mitmproxy and put mitmdump on PATH." >&2
  exit 1
fi

if ! command -v xcrun >/dev/null 2>&1; then
  echo "Error: xcrun is missing. Select an Xcode installation with iOS simulator support." >&2
  exit 1
fi
confdir=${MITMPROXY_CONFDIR:-"$HOME/.mitmproxy"}
certificate="$confdir/mitmproxy-ca-cert.pem"
generator_pid=
stop_generator() {
  if [[ -n "$generator_pid" ]]; then
    kill "$generator_pid" 2>/dev/null || true
    wait "$generator_pid" 2>/dev/null || true
    generator_pid=
  fi
}
trap stop_generator EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -f "$certificate" ]]; then
  echo "Reusing shared certificate: $certificate"
else
  umask 077
  mkdir -p "$confdir"
  echo "Generating shared certificate in: $confdir"
  # Initialize the CA without opening a listener or starting local capture.
  "$mitmdump_bin" --set "confdir=$confdir" --no-server --set flow_detail=0 &
  generator_pid=$!
  for ((attempt = 0; attempt < 300; attempt++)); do
    if [[ -f "$certificate" ]]; then
      break
    fi
    if ! kill -0 "$generator_pid" 2>/dev/null; then
      echo "Error: mitmdump exited before creating $certificate." >&2
      exit 1
    fi
    sleep 0.1
  done
  if [[ ! -f "$certificate" ]]; then
    echo "Error: timed out waiting for mitmdump to create $certificate." >&2
    exit 1
  fi
  stop_generator
  echo "Created shared certificate: $certificate"
fi

if ! simulator=$(xcrun simctl getenv "$simulator" SIMULATOR_UDID); then
  echo "Error: no unambiguous booted iOS simulator is available. Boot the intended simulator, then retry with --udid UDID." >&2
  exit 1
fi
if ! xcrun simctl keychain "$simulator" add-root-cert "$certificate"; then
  echo "Error: could not trust $certificate in simulator $simulator. Check simulator/Xcode availability and retry." >&2
  exit 1
fi
echo "Trusted shared certificate in simulator: $simulator"
echo "Keep $confdir for future runs across projects and worktrees."
