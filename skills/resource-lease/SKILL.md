---
name: resource-lease
description: Atomic lease, lock, and quota engine for coordinating parallel agents, scripts, and CI tasks. Manages file-locked namespaces with configurable capacities, TTL heartbeats, PID liveness crash detection, FIFO queueing, and orphan reaping. Use to avoid conflicting file edits, serialize Git operations, throttle API/DB/GPU concurrency, or coordinate multi-process workflows.
---

# Resource Lease

Coordinate concurrent processes using atomic, file-locked resource leases and quotas.
Locks are temporary leases with TTLs, PID liveness probing, and FIFO fair queueing.

```bash
# Set this to the directory containing this loaded SKILL.md. Do not guess a home-directory install path.
RESOURCE_LEASE_DIR=/absolute/path/from-the-skill-source-locator/resource-lease
S="$RESOURCE_LEASE_DIR/scripts"
test -f "$S/resource_pool.py" || exit 1
```

Every script resolves sibling tools and configuration from its own canonical directory.


State is stored in `~/.config/.agents/resource_pool/state.json` (override via
`AGENT_HERDER_POOL_STATE_FILE`) and guarded by exclusive POSIX `fcntl.flock` locks.

## Non-negotiable rules

1. Agree on canonical IDs: Use standard schemas (`file:<relpath>`, `dir:<relpath>`,
   `git-state:<abspath>`, `worktree:<abspath>`, `db:<name>`, `api:<name>`, `gpu:<id>`).
2. Namespace by group: Isolate project mutations using `repo:<canonical-path>`. Use shared
   groups (e.g. `global`) only when cross-project contention is intentional.
3. Capacity defines exclusivity: Set capacity 1 for exclusive mutation (files, git state,
   migrations); set capacity N for throttled concurrency (APIs, test DBs, GPU workers).
4. Leases expire unless renewed: Periodically heartbeat active leases. If renewal fails
   (`status: lost`), immediately abort mutation—ownership was revoked or reaped.
5. Accurate PID liveness: Pass `--pid <PID>` only for long-lived worker processes to allow
   instant same-host crash reclamation. Never pass short-lived wrapper PIDs.
6. Rollback partial acquisitions: When acquiring multiple resources, release all held leases
   if any intermediate acquisition times out or fails.
7. Clean up on exit: Always release held leases in `finally` / shell `trap` handlers, or
   call `release-agent` during abnormal worker termination.
8. Destructive operations require coarse locks: Never perform git checkout, switch, reset,
   rebase, clean, stash, or schema drops under a narrow file lease. Require exclusive
   `git-state:<repo>` or `db:<name>`.
9. Fail closed on corruption: Corrupt or unreadable state files fail closed with `StateError`
   and must never be silently overwritten or deleted.

## CLI Usage

```bash
# Define a resource with capacity and TTL
python3 "$S/resource_pool.py" define --group repo:/app --resource file:src/api.ts --capacity 1 --ttl 900

# Acquire lease (exit 0: acquired, exit 1: locked/timeout)
python3 "$S/resource_pool.py" acquire --group repo:/app --resource file:src/api.ts --agent worker-1 --timeout 10 --pid $$

# Extend lease (heartbeat)
python3 "$S/resource_pool.py" renew --group repo:/app --resource file:src/api.ts --agent worker-1

# Release single lease or drop all leases for an agent
python3 "$S/resource_pool.py" release --group repo:/app --resource file:src/api.ts --agent worker-1
python3 "$S/resource_pool.py" release-agent --group repo:/app --agent worker-1

# Inspect state and reap dead/expired leases
python3 "$S/resource_pool.py" status --group repo:/app
python3 "$S/resource_pool.py" reap --group repo:/app
```

## Python Usage

```python
import os
from pathlib import Path
from resource_pool import ResourcePoolManager

mgr = ResourcePoolManager()  # optional: ResourcePoolManager(Path("/custom/state.json"))

# Define and acquire
mgr.define(group="repo:/app", resource="file:src/api.ts", capacity=1, ttl=900.0)
res = mgr.acquire(
    group="repo:/app", resource="file:src/api.ts", agent="worker-1",
    timeout=10.0, pid=os.getpid(), fifo=True
)
if res["status"] != "acquired":
    raise RuntimeError(f"Could not acquire resource: {res.get('error')}")

try:
    # Heartbeat during long operations
    mgr.renew(group="repo:/app", resource="file:src/api.ts", agent="worker-1")
    # ... perform safe mutation ...
finally:
    mgr.release(group="repo:/app", resource="file:src/api.ts", agent="worker-1")
```

## Load detail only when needed

- **Resource schemas, capacities, TTLs, FIFO queues, PID liveness, orphan recovery:**
  [resource_leases.md](references/resource_leases.md)

Check `python3 "$S/resource_pool.py" <command> --help` for specific argument details.
