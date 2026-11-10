# Beads-backed task scheduling and dispatch

Read this reference when configuring, planning, claiming, or closing tasks in a Beads DAG.

## Contents

1. Ownership and storage
2. Task metadata and `agent_context`
3. Scheduling and dispatch algorithm
4. Multi-project workspace scope
5. Completion gates and worker recovery
6. Constraints and safety rules

## 1. Ownership and storage

Use each tool for one source of truth:

| Concern | Owner |
|---|---|
| Task definitions, dependencies, priority, claims, comments, acceptance criteria | `br` |
| Graph ranking, critical-path analysis, parallel track planning | `bv` or `bvr` |
| Ready-candidate selection, metadata validation, claim lifecycle, completion gates | `scheduler.py` |
| Worker execution, terminal panes, interactive permissions | Pluggable executor (e.g. `terminal-agent-runner`) |
| Concurrency locks, file leases, API quotas | Resource manager (e.g. `resource-lease`) |

Keep durable task data inside each repository:

```text
repo/.beads/beads.db       local primary SQLite database
repo/.beads/issues.jsonl   Git-friendly export
repo/.beads/config.yaml    workflow and prefix configuration
repo/.beads/routes.jsonl   optional cross-project ID routing
```

Do not create parallel `tasks.json` tracking files. Durable project task state lives solely
in Beads. Any execution or worker process tracking is transient state owned by the executor.

## 2. Task metadata and `agent_context`

Create dependencies and execution context with `br`:

```bash
br dep add child-id blocker-id
br update task-id --agent-context @task-context.json
br show task-id --json
```

Use this canonical `agent_context` structure:

```json
{
  "herder": {
    "cli": "codex",
    "approval_mode": "auto",
    "resources": ["file:src/api.ts", "api:test-db"],
    "read_only": false,
    "cwd": ".",
    "verification": ["pytest -q tests/api"]
  }
}
```

### Metadata schema rules

- Top-level keys (such as `herder` or `runner`) are executor-specific namespaces. The scheduler
  validates core scheduling properties and passes the namespace through to the chosen executor.
- `resources`: Required list of string resource descriptors for mutating tasks (e.g.
  `"file:src/api.ts"`, `"api:test-db"`). Set `read_only: true` when no mutation occurs.
- `read_only`: Boolean flag indicating whether the task is strictly non-mutating.
- `cwd`: Directory relative to the bead's repository root. Absolute paths or paths that escape
  the repository boundary are rejected. Defaults to `.`.
- `verification`: List or mapping of command strings required to prove task completion.
  Accepts a list of command strings (e.g. `["pytest -q"]`), a list of spec objects with
  per-command expectations (e.g. `[{"command": "npm run test:scripts", "expected_exit": 1}]`),
  or a dictionary mapping commands to expected exit codes or spec objects. When a top-level or
  executor-level `expected_exit` or `expected_failures` is set, it applies to all verification
  commands without explicit overrides, allowing known-red test baselines to validate cleanly.
  Command matching normalizes whitespace, expands `~` and environment variables, and resolves
  token paths for robust verification comparison.

Use `br update --help` for additional fields, inheritance rules, and workflow gates.

## 3. Scheduling and dispatch algorithm

Run a deterministic scheduling wave:

1. **Candidate Discovery**:
   - In a single project, query `br scheduler --json` for ranked ready work.
   - In a multi-repo workspace, query `bvr` or `bv --robot-plan` for parallel tracks, falling
     back to each repository's `br scheduler` recommendations if the viewer is unavailable.
2. **Namespace Check**:
   - Reject duplicate IDs across repositories; ensure unique `id.prefix` values in each `.beads/config.yaml`.
3. **Metadata Validation**:
   - Fetch each candidate bead with `br show ID --json`.
   - Validate its `agent_context` against the schema (valid JSON, valid `cwd` within repo,
     proper resource declarations or `read_only` flag, non-empty `verification` list).
4. **Atomic Claim**:
   - Claim the candidate bead atomically: `br update ID --claim --actor ACTOR`.
   - If the claim fails (e.g., claimed by another worker), skip to the next candidate.
5. **Handoff to Executor**:
   - Pass the claimed bead ID, repository root, validated context, and task text to an executor
     (for example, `terminal-agent-runner` or a custom subagent runner).
   - If executor initialization or resource acquisition fails, immediately roll back the claim:
     `br update ID --status open --assignee "" --actor ACTOR`.

Always preview the wave before mutating claims:

```bash
python3 "$S/scheduler.py" ready --project /repo --limit 5
python3 "$S/scheduler.py" ready --workspace /work/.bv/workspace.yaml --limit 5
```

## 4. Multi-project workspace scope

Maintain one independent `.beads/` database per repository. Aggregate repositories using an
explicit `.bv/workspace.yaml` manifest:

```yaml
# /work/.bv/workspace.yaml
repos:
  - path: services/api
  - path: apps/web
  - path: libs/shared
```

```bash
python3 "$S/scheduler.py" ready --workspace /work/.bv/workspace.yaml
python3 "$S/scheduler.py" ready --workspace /work/.bv/workspace.yaml --repo api
```

Relative repository paths in `workspace.yaml` resolve against the workspace root (the directory
containing `.bv/`).

Configure unique `id.prefix` values in each repository's `.beads/config.yaml`. Add `.beads/routes.jsonl`
only when commands in one repository must mutate an issue owned by another. Workspace aggregation
does not alter or merge underlying task data.

## 5. Completion gates and worker recovery

### Evidence-gated completion

When a worker finishes, validate evidence before closing:

```bash
python3 "$S/scheduler.py" close --id Bead-api-123 --actor worker-1 --project /repo \
  --handoff '{"status":"completed","decisions":["..."],"changed_files":["src/api.ts"],"verification":[{"command":"pytest -q tests/api","exit_code":0}],"blockers":[],"next_inputs":[]}'
```

The completion sequence:
1. Validates that the handoff conforms to the `FLEET_HANDOFF` schema.
2. Checks that `status == "completed"` and `blockers` is empty.
3. Checks that all configured `verification` commands from `agent_context` are present, and that
   each entry's `exit_code` equals its `expected_exit` (default `0`).
4. Appends the full handoff payload as an audit comment: `br comments add ID --message ...`.
5. Closes the bead via `br close ID --reason "Completed by ACTOR" --actor ACTOR --json`.
6. Signals the executor to release runtime locks and leases.

If validation fails, the bead remains in its claimed state and executor leases are retained.

An entry sets `expected_exit` when the passing case is a nonzero exit — a leak-check `grep` exits
1 when it finds nothing. The comparison is still equality, so declaring an expectation records
what the worker believed rather than excusing a failure. Text the worker emits after the handoff
JSON does not refuse the close, but anything that is not recognised terminal chrome is echoed as
a `warning:` on stderr: prose after the evidence can contradict it.

### Worker crash recovery and requeue

If a worker crashes, times out, or fails to complete its objective:

```bash
python3 "$S/scheduler.py" rollback --id Bead-api-123 --actor worker-1 --project /repo
```

This returns the bead to `status: open`, clears its `assignee`, and allows subsequent scheduling
waves to re-dispatch the work.

### Human escalation (park)

When a worker encounters an ambiguous requirement, missing credential, or human blocker:
1. Append an escalation note to the bead comments: `br comments add ID --message "Parked: reason"`.
2. Keep the bead in its claimed/in-progress state so other automated waves do not duplicate
   unresolved work.
3. Instruct the executor to release runtime concurrency leases.

## 6. Constraints and safety rules

- **Wave Planner, Not a Daemon**: The scheduler evaluates static waves on demand. It does not run
  as a background daemon.
- **SQLite Concurrency**: `br` SQLite locks coordinate task database operations. They do not lock
  filesystem files or code resources; concurrency leases are the executor's responsibility.
- **Independent Transactions**: Workspace scheduling queries separate databases; there is no
  distributed multi-repo transaction across SQLite databases.
- **Coordination Check**: Check `br coordination status --json` before reclaiming stale claims to
  avoid conflicting with active workers.
- **Never Guess CLI Flags**: Consult `br --help`, `br robot-docs guide`, and `bv --robot-help` for
  version-sensitive features.
