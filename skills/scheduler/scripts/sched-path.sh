#!/usr/bin/env bash
# Resolves the already-installed mac-scheduler `sched` CLI and prints its
# single absolute, directly-executable path to stdout — never a hardcoded
# path (invariant 4, mac-scheduler/PLAN.md: this skill directory stays
# stateless and holds no copy of the CLI or its state; it only locates and
# thinly wraps the real install).
#
# Output contract: exactly one line on stdout — one absolute path, always
# directly executable (mac-scheduler's install.sh `chmod +x`'s dist/cli.js,
# which carries a `#!/usr/bin/env node` shebang). Callers quote it and never
# word-split, so the contract is identical under bash, zsh, or sh:
#   SCHED="$("$S"/sched-path.sh)" || exit 1
#   "$SCHED" list
#   "$SCHED" schedule foo --cron "0 9 * * *" --missed-policy catchUpOnce
#
# Gotcha worth knowing (see references/install-and-ops.md): in an
# *interactive* zsh shell (the default on this Mac), the bare word `sched`
# resolves to zsh's own builtin of the same name, NOT this CLI — that
# builtin schedules a command for later execution in that shell, and has
# nothing to do with mac-scheduler. This script runs under bash (see the
# shebang) and is unaffected (bash has no such builtin), but any caller that
# shells out to a literal `sched ...` string from zsh must use this script's
# resolved path (or `command sched ...`) instead of the bare word.
set -euo pipefail

# 1. Explicit override escape hatch.
if [ -n "${SCHED_CLI_PATH:-}" ]; then
  if [ -x "$SCHED_CLI_PATH" ]; then
    echo "$SCHED_CLI_PATH"
    exit 0
  fi
  echo "sched-path.sh: SCHED_CLI_PATH is set to '$SCHED_CLI_PATH' but it is not executable" >&2
  exit 1
fi

# 2. Globally linked/installed `sched` on PATH (mac-scheduler's install.sh
# runs `npm link`, which is the normal path here). Bash has no `sched`
# builtin, so this resolves the real CLI even though zsh's builtin of the
# same name would otherwise shadow it interactively.
if resolved="$(command -v sched 2>/dev/null)" && [ -x "$resolved" ]; then
  echo "$resolved"
  exit 0
fi

# 3. Known default checkout location for this CLI on this class of machine.
default_checkout="$HOME/projects/mac-scheduler"
if [ -x "$default_checkout/dist/cli.js" ]; then
  echo "$default_checkout/dist/cli.js"
  exit 0
fi

# 4. Derive the install directory from the rendered worker LaunchAgent plist
# — the source of truth for "where mac-scheduler was actually installed",
# written by mac-scheduler/install.sh. Robust even if the CLI was installed
# somewhere other than the default checkout above.
worker_plist="$HOME/Library/LaunchAgents/com.devkhadka.scheduler.worker.plist"
if [ -f "$worker_plist" ]; then
  # ProgramArguments' second <string> is "<INSTALL_DIR>/dist/worker.js".
  install_dir="$(/usr/libexec/PlistBuddy -c "Print :ProgramArguments:1" "$worker_plist" 2>/dev/null | sed -E 's#/dist/worker\.js$##')"
  if [ -n "$install_dir" ] && [ -x "$install_dir/dist/cli.js" ]; then
    echo "$install_dir/dist/cli.js"
    exit 0
  fi
fi

echo "sched-path.sh: could not resolve the mac-scheduler 'sched' CLI." >&2
echo "  Tried: \$SCHED_CLI_PATH, 'command -v sched', $default_checkout/dist/cli.js," >&2
echo "  and the installed worker LaunchAgent plist ($worker_plist)." >&2
echo "  Run mac-scheduler/install.sh first (it chmod +x's dist/cli.js and npm" >&2
echo "  links it), or set SCHED_CLI_PATH explicitly." >&2
exit 1
