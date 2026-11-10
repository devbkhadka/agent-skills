# Resource Leases

Read this reference when defining resource scopes, preventing concurrent conflicts, throttling shared capacity, managing lease lifecycles, or recovering abandoned locks.

## Overview

The resource pool manager provides an atomic, file-locked lease and quota engine for coordinating any set of concurrent processes—such as coding agents, shell scripts, CI jobs, or background tasks.

Locks are **leases**, not permanent claims. Every held lease records its holder identity, host, process ID (if provided), acquisition time, and expiration timestamp. If a holding process crashes or abandons a lease, the pool reaps the lock automatically via process liveness probing or TTL expiry, preventing deadlocks.

## Resource Model

Use canonical, intention-revealing resource IDs:

```text
file:src/config.ts              # Fine-grained file mutation
dir:tests/api                   # Directory-level operations
git-state:/absolute/repo        # Exclusive Git operations (commit, rebase, checkout)
worktree:/absolute/repo         # Worktree creation, deletion, or switching
db:test-postgres                # Database instance or schema migration
api:staging-rate-limit          # Throttled external API endpoint
gpu:0                           # Hardware device or worker slot
```

### Resource Identifiers
- Resource and group identifiers must be non-empty strings up to 512 characters, contain no control characters, and must not start with a hyphen (`-`).
- Agent/process identifiers must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`.

### Group Semantics (Namespaces)
- Groups isolate unrelated projects and execution contexts.
- Default to `repo:<canonical-path>` for repository-specific mutations.
- Use a shared group name (e.g., `global` or `shared:<service>`) for system-wide shared resources to make cross-project contention explicit.

### Capacity and Quotas
- **Capacity 1 (Exclusive Lock):** Used for single files, Git state, worktrees, schema migrations, and exclusive mutation.
- **Capacity N (Shared Pool / Semaphore):** Used to throttle access to APIs, test environments, GPU devices, or bounded worker pools.

## CLI Commands

```bash
# Define or update resource capacity and optional default TTL
python3 scripts/resource_pool.py define --group repo:/app --resource file:config.ts --capacity 1 --ttl 900

# Acquire a lease (non-blocking: --timeout 0; blocking: --timeout <sec>)
python3 scripts/resource_pool.py acquire --group repo:/app --resource file:config.ts --agent worker-1 --timeout 30 --pid 12345

# Renew an existing lease (heartbeat)
python3 scripts/resource_pool.py renew --group repo:/app --resource file:config.ts --agent worker-1

# Release a held lease or cancel a queued waiter
python3 scripts/resource_pool.py release --group repo:/app --resource file:config.ts --agent worker-1

# Release all leases and queue entries held by an agent in a group
python3 scripts/resource_pool.py release-agent --group repo:/app --agent worker-1

# Check status of resources in a group (or all groups)
python3 scripts/resource_pool.py status --group repo:/app

# Reclaim expired leases and dead-process claims
python3 scripts/resource_pool.py reap --group repo:/app

# List active resource groups
python3 scripts/resource_pool.py list-groups

# Clear an entire resource group
python3 scripts/resource_pool.py clear-group --group repo:/app
```

### Exit Codes
- `0`: Operation succeeded (`acquired`, `renewed`, `released`, `ok`, `cleared`).
- `1`: Resource locked or lease lost (cannot acquire within timeout, or lease expired/not held).
- `2`: Syntax, argument, validation, or state error (details on stderr).

## Leases, TTLs, and Heartbeat Renewal

1. **TTL Expiry:** Every lease has a time-to-live (`DEFAULT_TTL = 900.0` seconds / 15 minutes unless overridden). A lease expires automatically when `expires_at <= now`.
2. **Heartbeat Renewal:** Long-running tasks must periodically invoke `renew` before the TTL expires.
3. **Loss of Ownership:** If `renew` returns `{"status": "lost"}`, the lease was reaped or expired. The process **must** treat this as complete loss of ownership, immediately cease mutating the shared resource, and handle the failure.
4. **PID Liveness:** When acquiring a lease with `--pid <PID>`, the pool records the process ID. On the recording host, pool operations inspect process liveness (`os.kill(pid, 0)`). If the process is dead, the lease is reaped immediately without waiting for the TTL. Only pass the PID of the long-lived worker process; never pass the PID of a short-lived wrapper script.

## FIFO Queueing and Starvation Prevention

- When acquiring a resource that is at capacity with `--timeout > 0`, the caller is placed in a FIFO waiting queue.
- Waiting entries track arrival time (`since`) and active polling (`seen_at`).
- Queue slots are granted strictly in arrival order. A new caller cannot barge ahead of older waiters unless fair queueing is explicitly disabled via `--no-fifo`.
- Stale waiters that abandon polling are reaped after `WAITER_TTL = 30.0` seconds.

## Multi-Resource Acquisition & Rollback

When a task requires multiple resources (for example, two files or a file plus an API quota):
1. Acquire resources sequentially.
2. If any acquisition fails or times out, immediately release all already-acquired resources in reverse order before failing.
3. Never hold a partial set of required resources while waiting indefinitely for another, as this can introduce circular deadlocks between concurrent processes.

## Orphan Reaping and Clean-up

- **Explicit Reaping:** Run `reap [--group G] [--dry-run]` to inspect or purge expired leases and dead process claims.
- **Automatic Reaping:** Pool operations (`acquire`, `renew`, `status`) automatically reap expired holders and dead local processes before evaluating requests.
- **Task Teardown:** When a worker completes, fails, or is cancelled, always invoke `release` for specific resources or `release-agent` to drop all held leases and queue slots in the group.

## State Management and Safety

- **State File:** Default location is `~/.config/.agents/resource_pool/state.json`. Can be overridden with the `AGENT_HERDER_POOL_STATE_FILE` environment variable.
- **File Locking:** All reads and writes are protected by an exclusive file lock (`fcntl.flock` on `state.json.lock`).
- **Atomic Writes:** State updates are written to a PID-tagged temporary file, flushed, fsynced, and atomically renamed (`os.replace`) with parent directory fsync.
- **Fail-Closed Behavior:** If the state file is unreadable, empty, malformed, or corrupt, operations fail closed by raising `StateError`. The corrupt file is never silently deleted or overwritten.

## Destructive Shared-State Rule

Never execute destructive shared-state operations (`git checkout`, `git switch`, `git reset`, `git clean`, `git stash`, `git rebase`, worktree removal, database schema reset) merely because a process holds a narrow file lease. Always require the coarse/global resource (`git-state:<repo>`, `worktree:<repo>`, or `db:<name>`) to be acquired exclusively.
