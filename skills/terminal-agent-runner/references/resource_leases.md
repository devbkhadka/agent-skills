# Resource leases

Read this file when defining scopes, preventing conflicts, throttling shared capacity, or
recovering abandoned locks.

## Resource model

Use canonical, intention-revealing IDs:

```text
file:src/config.ts
dir:tests/api
git-state:/absolute/repo
worktree:/absolute/repo
db:test-postgres
api:staging-rate-limit
gpu:0
```

Groups isolate unrelated projects. Default to `repo:<canonical-path>`. Put a genuinely shared
resource in one shared group so cross-project contention is visible.

Use capacity 1 for files, Git state, worktrees, schema migration, and other exclusive mutation.
Use capacity N to throttle APIs, devices, GPUs, test environments, or bounded writers.

## Commands

```bash
python3 "$S/resource_pool.py" define  --group G --resource file:config.ts --capacity 1 --ttl 900
python3 "$S/resource_pool.py" acquire --group G --resource file:config.ts --agent A --timeout 30
python3 "$S/resource_pool.py" renew   --group G --resource file:config.ts --agent A
python3 "$S/resource_pool.py" release --group G --resource file:config.ts --agent A
python3 "$S/resource_pool.py" release-agent --group G --agent A
python3 "$S/resource_pool.py" status  --group G
python3 "$S/resource_pool.py" reap    --group G
```

Leases expire unless renewed. `fleet.py watch` heartbeats recorded assignment leases. Treat a
failed renewal as loss of ownership: stop mutation and reassess. Pass `--pid` only for a real,
long-lived agent process; never pass the short-lived wrapper PID.

Pool state is schema-validated under an exclusive file lock and replaced atomically. Missing state
creates an empty pool; unreadable, empty, malformed, or unsupported state fails closed and must be
repaired explicitly. Never delete or overwrite corrupt state as an automatic recovery step.

FIFO waiters prevent starvation. `fleet.py spawn` acquires all assignment resources and rolls
back a partial acquisition. `park` and `complete` release the assignment's leases.

## Destructive shared-state rule

Do not approve checkout, switch, reset, clean, stash, rebase, worktree changes, schema resets,
or other destructive shared-state operations merely because an agent holds one file lease.
Require the relevant global resource to be drained and escalate the decision.

Use `resource_pool.py <command> --help` for TTL, FIFO, PID liveness, state-file, and recovery
options instead of loading unrelated fleet documentation.
