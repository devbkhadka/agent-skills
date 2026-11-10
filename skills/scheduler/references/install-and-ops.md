# Install and ops

This skill never installs, builds, or manages mac-scheduler itself — that all happens in the
runtime project (`mac-scheduler/`). This reference is background for diagnosing "why isn't `sched`
working" without guessing.

## What `mac-scheduler/install.sh` does (idempotent — safe to re-run)

1. `npm ci` + `npm run build` in the mac-scheduler checkout, producing `dist/{cli,worker,dashboard,
   reconciler}.js`.
2. `chmod +x dist/cli.js` and `npm link`, so the CLI is invokable as a plain `sched` on `PATH` (see
   the zsh gotcha below for why this skill never relies on the bare word `sched` internally).
3. Renders three launchd plist templates (`launchd/*.plist.template`, with `{{INSTALL_DIR}}` and
   `{{NODE_BIN}}` substituted for this machine) into `~/Library/LaunchAgents/`:
   - `com.devkhadka.scheduler.worker` — `KeepAlive.SuccessfulExit: false` + `ThrottleInterval`, so
     launchd restarts it on a crash but not on a deliberate clean stop (e.g. `uninstall.sh`).
   - `com.devkhadka.scheduler.dashboard` — same `KeepAlive`/`ThrottleInterval` shape.
   - `com.devkhadka.scheduler.reconciler` — `RunAtLoad: true`, **not** `KeepAlive` (one-shot; see
     [missed-schedule-policy.md](missed-schedule-policy.md)).
4. Appends an `include <mac-scheduler>/redis/redis.local.conf` line to the Homebrew redis config
   (`$(brew --prefix)/etc/redis.conf`) if not already present, then `brew services restart redis`.
   `redis/redis.local.conf` sets `appendonly yes` — AOF persistence, so queued/scheduled state
   survives a reboot or power loss (never RDB-only).
5. `launchctl bootout`s then `bootstrap`s each of the three services under `gui/$(id -u)`, so a
   second `install.sh` run cleanly replaces an already-loaded service rather than erroring.

`mac-scheduler/uninstall.sh` reverses step 5 only — `launchctl bootout` for all three labels, then
removes their rendered plists from `~/Library/LaunchAgents/`. It deliberately leaves Redis data,
the Homebrew redis service, and the AOF config untouched.

## Diagnosing "sched isn't doing anything"

- `launchctl print gui/$(id -u)/com.devkhadka.scheduler.worker` — confirm `state = running` and
  check `last exit code` if it's not.
- `redis-cli ping` — confirm Redis is actually up (`brew services list | grep redis`).
- `"$SCHED" list` — confirm you're pointed at the same `REDIS_URL`/`SCHED_QUEUE_NAME` the installed
  services use (both default; only relevant if you've exported either in your shell).
- Worker/dashboard logs: `<install-dir>/logs/{worker,dashboard,reconciler}{,.error}.log`.

## The zsh `sched` builtin collision

On this Mac (default shell zsh), the bare word `sched` in an *interactive* shell resolves to
**zsh's own builtin** of the same name (`sched` — schedules a command for later execution within
that zsh session), not the mac-scheduler CLI, even after `npm link`. This is exactly why
`scripts/sched-path.sh` always resolves and returns an absolute path rather than assuming the bare
word `sched` reaches the right binary, and why this skill's usage examples always invoke `"$SCHED"`
(the resolved path) rather than the literal word `sched`. If you ever need to run the real CLI from
an interactive zsh prompt directly, use `command sched ...` (bypasses the builtin) or the resolved
absolute path — never the bare word.

## `sched-path.sh` resolution order

1. `$SCHED_CLI_PATH` env var, if set and executable (explicit override).
2. `command -v sched` (works because `sched-path.sh` runs under bash, which has no `sched`
   builtin) — the normal path after `install.sh`'s `npm link`.
3. The default checkout, `~/projects/mac-scheduler/dist/cli.js`, if executable.
4. The `ProgramArguments` of the installed worker LaunchAgent plist
   (`~/Library/LaunchAgents/com.devkhadka.scheduler.worker.plist`) — the source of truth for
   "where was this actually installed," in case it's somewhere other than the default checkout.

If all four fail, `sched-path.sh` exits non-zero with a message naming what it tried — treat that
as "mac-scheduler isn't installed on this machine yet; run its `install.sh`," not as a skill bug.
