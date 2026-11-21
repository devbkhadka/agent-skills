#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: bash prepare.sh [--udid SIMULATOR_UDID] APP_BUNDLE_ID"
  echo "Prepare the shared CA and simulator trust, then resolve the installed app executable."
  echo 'MITMPROXY_CONFDIR overrides the shared folder: ${HOME}/.mitmproxy'
}

simulator=booted
if [[ "${1:-}" == --udid ]]; then
  if (( $# < 3 )) || [[ -z "$2" ]]; then
    usage >&2
    exit 1
  fi
  simulator=$2
  shift 2
fi
if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
  usage
  exit 0
fi
if (( $# != 1 )) || [[ -z "$1" || "$1" == -* ]]; then
  usage >&2
  exit 1
fi
bundle_id=$1
step="locating setup-cert.sh"
trap 'echo "Error: preparation failed while $step. Capture is not ready." >&2' ERR
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
step="preparing the shared certificate and trusting it in simulator '$simulator'"
bash "$script_dir/setup-cert.sh" --udid "$simulator"
step="resolving the booted simulator (use --udid when multiple simulators are booted)"
simulator_udid=$(xcrun simctl getenv "$simulator" SIMULATOR_UDID)
step="locating installed app '$bundle_id' in simulator '$simulator_udid'"
app_bundle=$(xcrun simctl get_app_container "$simulator_udid" "$bundle_id" app)
step="reading CFBundleExecutable from '$app_bundle/Info.plist'"
app_process=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$app_bundle/Info.plist")
case "$app_process" in
  ''|*','*|*'!'*|*':'*|*'@'*|*'/'*|*$'\n'*)
    echo "Error: CFBundleExecutable is not a single supported local-capture process name." >&2
    exit 1
    ;;
esac
if [[ ! -x "$app_bundle/$app_process" ]]; then
  echo "Error: app executable is missing or not executable: $app_bundle/$app_process" >&2
  exit 1
fi

echo "Ready for capture; copy these assignments into the capture shell:"
printf 'SIMULATOR_UDID=%q\n' "$simulator_udid"
printf 'SIM_APP_BUNDLE_ID=%q\n' "$bundle_id"
printf 'SIM_APP_NAME=%q\n' "$app_process"
printf 'MITMPROXY_CONFDIR=%q\n' "${MITMPROXY_CONFDIR:-$HOME/.mitmproxy}"
echo "Launch the target app before capture; keep other apps with this executable name stopped."
echo "Local capture may still need one-time macOS Mitmproxy Redirector approval."
