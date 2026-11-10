# Agent Herder examples

Use these examples after reading the main skill. Every command belongs to one of the composed
skills; `$SCHED`, `$RUN`, and `$POOL` are the paths resolved in SKILL.md. Inspect command help
before adapting advanced flags to a different installed version.

## Beads project: configure and run one wave

Create task metadata with `br update --agent-context` (inline JSON shown for brevity):

```bash
br update api-123 --agent-context \
  '{"herder":{"cli":"codex","approval_mode":"auto","resources":["file:src/api.ts"],"verification":["pytest -q tests/api"]}}'

br update docs-42 --agent-context \
  '{"herder":{"cli":"claude","approval_mode":"edit","resources":["file:README.md"],"verification":["markdownlint README.md"]}}'
```

`verification` is required: the scheduler refuses metadata without at least one command.

Rank the wave, then claim and spawn each candidate:

```bash
python3 "$SCHED" ready --project /work/api --limit 3 --json
python3 "$SCHED" claim api-123 --actor bead-api-123 --project /work/api
python3 "$RUN" spawn --name bead-api-123 --cli codex --cwd /work/api \
  --task "Complete Beads task api-123: <title + acceptance criteria>" \
  --group repo:/work/api --resource file:src/api.ts --approval-mode auto
```

Monitor and advance:

```bash
python3 "$RUN" watch --until actionable --timeout 60
python3 "$RUN" complete --agent bead-api-123
python3 "$SCHED" close api-123 --actor bead-api-123 --project /work/api --handoff @handoff.json
python3 "$SCHED" ready --project /work/api --limit 3 --json    # next wave
```

## Multi-project workspace

```yaml
# /work/.bv/workspace.yaml
repos:
  - path: services/api
  - path: apps/web
  - path: libs/shared
```

```bash
python3 "$SCHED" ready --workspace /work/.bv/workspace.yaml --limit 4 --json
python3 "$SCHED" ready --workspace /work/.bv/workspace.yaml --repo api --limit 2 --json
```

`ready` aggregates, but `claim` and `close` are per-repository: pass the `--project` the
candidate came from. Give every repository a unique Beads prefix so IDs cannot collide.

## Shared capacity across tasks

Predefine a capacity when the default exclusive lease is not correct:

```bash
python3 "$POOL" define \
  --group shared:staging --resource api:staging --capacity 2 --ttl 900
```

Beads that need the shared resource name the group in their own metadata, because the
`repo:<repo-root>` default would scope it to one repository:

```json
{"herder":{"cli":"codex","group":"shared:staging","resources":["api:staging"],
 "verification":["pytest -q tests/smoke"]}}
```

Spawn each relevant worker against that group and resource:

```bash
python3 "$RUN" spawn --name bead-smoke-1 --cli codex --cwd /work/api \
  --task "Run the staging smoke suite" \
  --group shared:staging --resource api:staging --approval-mode auto
```

Only two such tasks can hold the resource concurrently, even across repositories. The third
`spawn` waits for `--lock-timeout` and then refuses rather than double-booking staging.

## Human decision without fleet stall

```bash
python3 "$RUN" park --agent bead-api-123 --cancel
python3 "$RUN" escalations
python3 "$SCHED" ready --project /work/api --limit 3 --json
```

The bead stays claimed, its leases are released, and independent ready beads may run. Record
the human resolution on the bead (`br comments add`) before resuming or resetting its agent.

## Worker crash

```bash
python3 "$RUN" reconcile --group repo:/work/services/api
python3 "$SCHED" rollback api-123 --actor bead-api-123 --project /work/services/api
python3 "$SCHED" ready --project /work/services/api --limit 3 --json
```

`reconcile` reclaims locks from the dead pane; `rollback` returns the bead to `open` so a later
wave can pick it up. Do not reclaim a stale claim until `br coordination status --json` shows it
is fleet-owned rather than human-owned.

## Manual ad hoc work

Work with no bead skips the scheduler entirely — this is plain terminal-agent-runner:

```bash
python3 "$RUN" spawn --name Audit-1 --cli codex --cwd /work/api \
  --task "Inspect authentication flow; do not modify files" --read-only --approval-mode plan

python3 "$RUN" spawn --name Fix-1 --cli claude --cwd /work/api \
  --task "Fix the timeout handling" --group repo:/work/api \
  --resource file:src/auth.ts --approval-mode auto
```

Use `complete` to release a manual assignment. There is no `close` step, because there is no bead.

## Verification expectations

- Treat a test named in `verification` as a requirement, not evidence it ran.
- Require `FLEET_HANDOFF: {"status":"completed",...,"verification":[{"command":"...","exit_code":0}]}`.
  A check whose passing case is a nonzero exit declares `expected_exit`.
- Read the `warning:` lines from `complete` and `close`: text a worker adds after its handoff is
  surfaced there and is not covered by the evidence.
- `complete` checks that evidence exists and matches expectations; only `close` checks the bead's
  required command list. Run both.
- Report quota/auth failures, rollbacks, and parked beads explicitly.
