---
name: scheduler
description: Thin wrapper for the installed mac-scheduler `sched` CLI — a BullMQ/Redis-backed job scheduler for arbitrary shell-command jobs, with orthogonal retry/dead-lettering, persistence across reboot, and a per-schedule missed-run policy (skip vs catchUpOnce). Use to enqueue one-off shell-command jobs, inspect job/schedule status, retry dead-lettered jobs, or register/remove cron-style schedules on this Mac.
---

# Scheduler

Enqueue, inspect, and schedule arbitrary shell-command jobs through the already-installed
`sched` CLI (mac-scheduler). This skill documents and thinly wraps that CLI — it never
reimplements queueing, retry, or scheduling logic itself.

```bash
# Set this to the directory containing this loaded SKILL.md. Do not guess a home-directory path.
SCHEDULER_SKILL_DIR=/absolute/path/from-the-skill-source-locator/scheduler
S="$SCHEDULER_SKILL_DIR/scripts"
SCHED="$("$S/sched-path.sh")" || exit 1
```

Every script resolves sibling tools and the installed CLI from its own canonical directory —
never a hardcoded path. All mutable state (jobs, schedules, `lastRunAt`) lives in Redis on this
machine, not in this skill directory.

## Non-negotiable rules

These restate mac-scheduler's core invariants (`mac-scheduler/PLAN.md`) as rules an agent must
follow when using `sched` — do not re-derive or work around them:

1. **Retry and dead-lettering are orthogonal.** A job whose command exits non-zero always lands
   in the `failed` state, regardless of `--attempts` (even `--attempts 1` still dead-letters on
   failure — that is the intended crash-visibility behavior, not a bug to route around).
2. **Redis persistence uses AOF**, so a job/schedule survives an abrupt shutdown or reboot. Never
   run mac-scheduler against a Redis instance you know is RDB-only for anything you care about
   surviving a crash.
3. **Missed-schedule handling is per-schedule, explicit, and reconciler-only.** `--missed-policy
   skip` means a fire missed while the Mac was off/asleep is never retroactively run — this is
   the native cron-scheduler behavior. `--missed-policy catchUpOnce` means a `RunAtLoad`
   reconciler (never a polling loop) checks once at boot/login and enqueues exactly one catch-up
   job if a fire was missed. Do not expect catch-up behavior from a `skip`-policy schedule, and do
   not invoke the reconciler manually as a substitute for fixing a schedule's policy.
4. **This skill directory stays stateless.** No `node_modules`, no runtime logs, no PID/state
   files, no build step, no dependencies of its own — only docs and the thin `scripts/` wrapper
   below. Never write job/schedule state here; it belongs in Redis via the CLI.
5. **The CLI is the only interface used.** Do not read/write Redis keys (`schedule-meta:*`, queue
   keys) directly from an agent session — always go through `sched`, so invariants 1-4 stay
   enforced in one place (the runtime project), not reimplemented per-caller.

## CLI usage

Resolve the CLI once per session (see the snippet above), then invoke it directly — never as the
bare word `sched`. zsh (the default interactive shell on this Mac) has its own builtin named
`sched` that silently shadows the CLI and produces a confusing `bad time specifier` error. Always
use the resolved `"$SCHED"` path (below), or — if invoking `sched` directly from an interactive
zsh prompt outside this skill — `command sched ...`, which bypasses the builtin. See the
zsh-builtin gotcha in `references/install-and-ops.md` for the full explanation:

```bash
# Enqueue an immediate one-off shell-command job
"$SCHED" add <name> --command "<shell command>" [--attempts <n>]

# List job counts, recent jobs, and registered schedules
"$SCHED" list

# Show schedule metadata and/or the most recent job for <name>
"$SCHED" status <name>

# Retry a job that is in the failed (dead-lettered) state, by job id
"$SCHED" retry <jobId>

# Remove a schedule (by schedule name) or a job (by job id/name)
"$SCHED" remove <name>

# Create/update a cron schedule with an explicit missed-run policy
"$SCHED" schedule <name> --cron "<cron expression>" \
  --missed-policy <skip|catchUpOnce> [--command "<shell command>"]
```

`sched --help` and `sched <command> --help` are authoritative for exact flags — this skill
documents the stable surface, not every flag nuance.

## Load detail only when needed

- **Queue/worker/dashboard model, how jobs move through states:**
  [references/architecture.md](references/architecture.md)
- **`skip` vs `catchUpOnce` semantics, and why catch-up is reconciler-only:**
  [references/missed-schedule-policy.md](references/missed-schedule-policy.md)
- **Install/uninstall, launchd services, Redis AOF config, operational gotchas:**
  [references/install-and-ops.md](references/install-and-ops.md)

Run `tests/test_cli_contract.sh` after a mac-scheduler reinstall/upgrade to confirm the CLI
surface this skill documents hasn't drifted.
