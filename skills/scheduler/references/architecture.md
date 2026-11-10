# Architecture

mac-scheduler is a single BullMQ queue (`mac-scheduler`, overridable via `SCHED_QUEUE_NAME`)
backed by a Redis instance (`REDIS_URL`, default `redis://127.0.0.1:6379`), plus four Node
processes that all talk to that one queue:

- **`sched` CLI** (`dist/cli.js`) — the only interface this skill uses. Enqueues ad-hoc jobs,
  registers/updates cron schedules, inspects job/schedule state, retries failed jobs, removes
  jobs/schedules.
- **worker** (`dist/worker.js`) — a `launchd`-supervised, long-running process that pulls jobs
  off the queue and executes `job.data.command` as a real shell command via `child_process.spawn`,
  streaming stdout/stderr line-by-line into the job's log.
- **dashboard** (`dist/dashboard.js`) — a Bull Board Express app (`SCHED_DASHBOARD_PORT`, default
  `4000`) for human-readable visibility into queue/job state. Read-only aside from Bull Board's
  own native retry/promote/remove UI actions.
- **reconciler** (`dist/reconciler.js`) — a one-shot script, run once at boot/login by `launchd`
  (`RunAtLoad`, never `KeepAlive`), that checks every `catchUpOnce` schedule for a missed fire and
  enqueues at most one catch-up job per missed fire. See
  [missed-schedule-policy.md](missed-schedule-policy.md) for the full semantics.

## Job lifecycle and the retry/dead-letter model

Every job the worker's processor throws on (non-zero exit code, spawn failure, signal
termination) lands in BullMQ's native `failed` state — **regardless of `attempts`**. This is
intentional: retry count and dead-letter visibility are orthogonal concerns. `--attempts 1` still
dead-letters on the first failure; it does not mean "don't bother recording the failure." Never
expect a low `--attempts` value to suppress the `failed` state, and never treat `sched retry
<jobId>` as required for a job to be visible — it's purely how you ask BullMQ to re-run a job
that's already sitting in `failed`.

A job only ever reaches one of: `waiting` → `active` → `completed`, or `waiting` → `active` →
`failed` (looping back through `waiting`/`delayed` between attempts if `--attempts > 1`).
`sched status <name>` and the dashboard both surface the current state plus `attemptsMade` and
(on failure) `failedReason`.

## Schedule metadata

`sched schedule <name> --cron "..." --missed-policy <skip|catchUpOnce> [--command "..."]` does two
things atomically from the caller's point of view:

1. Writes a `schedule-meta:<name>` Redis hash (`cron`, `missedPolicy`, `command`, `createdAt`, and
   `lastRunAt` once a run completes) — this is what `sched status`/`sched list` read.
2. Registers a BullMQ job scheduler (`queue.upsertJobScheduler`) so the queue itself fires a job on
   that cron pattern going forward.

`lastRunAt` is written **only when a job for that schedule reaches `completed`** — never at
enqueue time. This is deliberate: if the Mac crashes mid-run, `lastRunAt` correctly still reflects
the last *successful* completion, so a `catchUpOnce` schedule's next reconciler pass sees the
in-flight (now lost) run as still missed, rather than silently marking it done.

## Queue name and Redis target

All four processes agree on the queue name (`SCHED_QUEUE_NAME`, default `mac-scheduler`) and Redis
target (`REDIS_URL`) via environment variables set identically in each rendered launchd plist (see
[install-and-ops.md](install-and-ops.md)). If you ever run `sched` from a shell with a different
`REDIS_URL`/`SCHED_QUEUE_NAME` than the installed services use, you will be looking at (or writing
to) a completely different, empty queue — always leave these unset unless you deliberately mean to
target a different instance.
