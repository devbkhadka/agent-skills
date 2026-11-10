---
name: beads-task-scheduler
description: Dependency-graph task management and wave planning with Beads (br 0.2.11, bv 0.16.4, bvr) — decomposition, dependency sequencing, ready-set ranking, atomic claims, completion gates, and multi-repo aggregation. Use for task graph planning, managing beads DAGs, computing ready waves, claiming beads, or validating completion evidence before close without requiring Herdr or terminal fleets.
---

# Beads Task Scheduler

Plan, sequence, claim, and close tasks in a Beads DAG. Use `br` as the durable task
database, `bv`/`bvr` for graph planning, and `scheduler.py` for wave execution helpers.

Verified core surfaces: `br` 0.2.11, `bv` 0.16.4, and `bvr`. Inspect local `--help`
before relying on version-sensitive advanced flags.

```bash
# Set this to the directory containing this loaded SKILL.md. Do not guess a home-directory path.
BEADS_SCHEDULER_DIR=/absolute/path/from-the-skill-source-locator/beads-task-scheduler
S="$BEADS_SCHEDULER_DIR/scripts"
test -f "$S/scheduler.py" || exit 1
```

Every script resolves sibling tools and configuration from its own canonical directory.

## Non-negotiable rules

1. Maintain one `.beads/` database per repository. Never merge databases or create external
   tracking files (`tasks.json`).
2. Aggregate multi-repo workspaces exclusively via explicit `.bv/workspace.yaml` `repos: - path:`
   entries. Never guess workspace layouts.
3. Configure unique `id.prefix` values in each repository's `.beads/config.yaml` to prevent
   cross-repository ID collisions.
4. Put execution metadata in `agent_context`. Top-level keys (`herder`, `runner`) are executor
   namespaces opaque to this scheduler.
5. Atomically claim candidate beads before execution. Roll back claims to `status: open` if
   worker dispatch or execution fails.
6. Enforce evidence-gated completion: never close a bead without validating structured
   `FLEET_HANDOFF` evidence and verifying zero exit codes on all required verification commands.
7. Never invent CLI flags or subcommands from memory. Always inspect the live tool's `--help`
   or machine documentation (`bv --robot-help`, `br robot-docs guide`).

## Task decomposition and dependency sequencing

Decompose work into granular beads and sequence blocker relationships:

```bash
# Create beads with acceptance criteria and execution context
br create --title "Implement API endpoint" --description "Add POST /v1/items"
br update <endpoint-id> --agent-context @context.json
br create --title "Add integration tests" --description "Verify POST /v1/items"

# Sequence dependency (tests depend on endpoint)
br dep add <test-id> <endpoint-id>
```

Execution metadata in `agent_context` (`herder` is the executor's namespace, opaque here):

```json
{
  "herder": {
    "cli": "codex",
    "approval_mode": "auto",
    "resources": ["file:src/api.ts"],
    "read_only": false,
    "cwd": ".",
    "verification": ["pytest -q tests/api"]
  }
}
```

## Wave planning and claim lifecycle

Preview and rank unblocked ready tasks for single-repo or multi-repo workspaces:

```bash
# Discover ranked ready candidates
python3 "$S/scheduler.py" ready --project /repo --limit 5
python3 "$S/scheduler.py" ready --workspace /work/.bv/workspace.yaml --limit 5

# Atomically claim candidate for worker
python3 "$S/scheduler.py" claim <bead-id> --actor worker-1 --project /repo

# On worker failure or crash: roll back claim to open status (optionally attach failure reason)
python3 "$S/scheduler.py" rollback <bead-id> --actor worker-1 --project /repo [--reason "failure diagnostics"]

# On worker completion: validate FLEET_HANDOFF evidence and close bead
python3 "$S/scheduler.py" close <bead-id> --actor worker-1 --project /repo \
  --handoff '{"status":"completed","decisions":["..."],"changed_files":["src/api.ts"],"verification":[{"command":"pytest -q tests/api","exit_code":0}],"blockers":[],"next_inputs":[]}'
```

## Multi-project workspace aggregation

Aggregate repositories in `.bv/workspace.yaml`:

```yaml
repos:
  - path: services/api
  - path: apps/web
  - path: libs/shared
```

Relative repository paths resolve against the workspace root (directory containing `.bv/`).

## Core loop

1. Decompose task into beads and sequence dependencies (`br create`, `br dep add`).
2. Attach execution metadata and verification requirements in `agent_context`.
3. Query ranked ready candidates (`scheduler.py ready`).
4. Atomically claim candidate (`scheduler.py claim`).
5. Hand claimed bead, repository root, and validated context to pluggable executor (e.g.
   `terminal-agent-runner`).
6. On success, validate `FLEET_HANDOFF` evidence and close bead (`scheduler.py close`). On
   failure/crash, roll back claim (`scheduler.py rollback`). On human blocker, park and record note.
7. Dispatch the next ready wave.

## Load detail only when needed

- **Beads dispatch, metadata schema, claims, completion gates, recovery:**
  [dispatch.md](references/dispatch.md)
- **Core CLI commands, flag discovery, robot docs:**
  [cli_tools.md](references/cli_tools.md)
