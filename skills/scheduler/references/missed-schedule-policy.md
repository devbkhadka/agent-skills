# Missed-schedule policy: `skip` vs `catchUpOnce`

Every schedule (`sched schedule <name> --cron "..." --missed-policy <skip|catchUpOnce>`) picks
exactly one of two mutually exclusive behaviors for what happens when the Mac was off, asleep, or
the worker was down at the moment a fire was due.

## `skip` — BullMQ's native behavior, nothing extra

BullMQ's job scheduler computes each schedule's *next* fire time relative to *now*. If a 9am fire
was due while the Mac was asleep and the Mac wakes at 11am, the scheduler simply targets the next
9am — the missed fire is gone, by design. No catch-up job is ever enqueued for a `skip`-policy
schedule, under any circumstance, including a reconciler run. This is the right choice for
schedules where a missed run is harmless to skip (e.g. a periodic "tidy up temp files" job) or
where running two fires close together would be wrong (e.g. anything non-idempotent that assumes
exactly one fire per calendar day).

## `catchUpOnce` — a separate, reconciler-only mechanism

BullMQ's scheduler has no concept of "run the fire I missed." mac-scheduler adds this as an
entirely separate, deliberately narrow mechanism:

- The **reconciler** (`dist/reconciler.js`) is the only thing that ever enqueues a catch-up job.
  It is a **one-shot script**, run once by `launchd` at boot/login (`RunAtLoad: true`, never
  `KeepAlive`) — **never a polling loop**, and **never built into the worker**. If you need to
  force a reconciliation pass outside of boot/login (e.g. for testing), run
  `node <install-dir>/dist/reconciler.js` directly; do not expect it to run on any other trigger.
- On each pass, for every `catchUpOnce` schedule, the reconciler computes the schedule's most
  recent cron-due timestamp strictly before "now" and compares it to the schedule's stored
  `lastRunAt`. If `lastRunAt` is absent or predates that due timestamp, exactly one immediate
  catch-up job is enqueued (`isCatchUp: true` in the job data).
- `lastRunAt` is written only on job **completion** (see architecture.md) — so a crash mid-run
  correctly leaves the schedule looking "still missed" for the next reconciler pass, rather than
  falsely marking it caught up.
- Catch-up jobs use a deterministic `jobId` (`catchup-<scheduleName>-<dueAt.getTime()>`). BullMQ
  treats adding a job with an existing id as a no-op returning the existing job, so **running the
  reconciler more than once for the same missed fire is safe** — it will never double-enqueue.

## What this means for you as an agent

- If a schedule needs "definitely run this at least once even if the Mac was off," use
  `--missed-policy catchUpOnce`. If a missed fire is fine to simply skip, use `--missed-policy
  skip` (the simpler, native behavior with zero extra moving parts).
- Do not build your own polling/backfill logic on top of `skip` schedules — that duplicates what
  `catchUpOnce` already does, correctly, in one place. Change the schedule's policy instead
  (`sched schedule <name> --cron "..." --missed-policy catchUpOnce ...` re-registers it).
- Do not expect a catch-up job to appear until the reconciler actually runs (next boot/login, or a
  manual invocation) — `catchUpOnce` is not real-time; it is checked once per boot/login cycle.
