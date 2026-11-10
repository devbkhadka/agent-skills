---
name: agent-herder
description: Run an unattended Beads-driven fleet by composing three skills — beads-task-scheduler for the dependency graph, resource-lease for exclusivity, terminal-agent-runner for Herdr panes. Owns only the wave loop that no single skill owns: claim a ready bead, lease its scope, spawn a worker, gate completion on evidence, close or roll back, dispatch the next wave. Use for unattended herding, task-graph-driven parallel coding, and multi-repo dispatch with br/bv/bvr.
---

# Agent Herder

Act as the lead herder. This skill is glue: it owns the wave loop and the metadata contract
between the graph and the panes. Everything else belongs to a composed skill, and every
command below comes from one of them.

| Composed skill | Owns | Load when |
|---|---|---|
| [beads-task-scheduler](../beads-task-scheduler/SKILL.md) | `.beads/` graph, ready-set ranking, claims, bead-level completion gate | decomposing work, computing a wave, closing a bead |
| [terminal-agent-runner](../terminal-agent-runner/SKILL.md) | Herdr panes, approval policy, monitoring, handoffs, pane-level completion gate | spawning, watching, approving, parking, resetting |
| [resource-lease](../resource-lease/SKILL.md) | lease/quota engine and capacities | defining capacities, inspecting or reaping leases |
| [feature-memory](../feature-memory/SKILL.md) | per-feature routing hub (`dev-plans/<slug>/memory/MEMORY.md`): quick start, concept→code map, status | bootstrap, before writing bead briefs, and once at report time |

```bash
# Set this to the directory containing this loaded SKILL.md. Do not guess a home-directory path.
AGENT_HERDER_DIR=/absolute/path/from-the-skill-source-locator/agent-herder
SKILLS=$(dirname "$AGENT_HERDER_DIR")
SCHED="$SKILLS/beads-task-scheduler/scripts/scheduler.py"
RUN="$SKILLS/terminal-agent-runner/scripts/fleet.py"
HERD="$SKILLS/terminal-agent-runner/scripts/herd.sh"
AUTO="$SKILLS/terminal-agent-runner/scripts/auto_herd.sh"
POOL="$SKILLS/resource-lease/scripts/resource_pool.py"
MEM_SLUG="$SKILLS/feature-memory/scripts/feature-slug.sh"
# SESSION must be a unique name for this fleet run. Never omit it.
# Omitting it targets the default Herdr session and makes every status/watch
# query invisible to wrong-session diagnosis.
SESSION="fleet-$(date +%s)"
test -f "$SCHED" -a -f "$HERD" -a -f "$AUTO" -a -f "$POOL" -a -f "$MEM_SLUG" || exit 1
```

### Pre-provisioned sessions

When the caller explicitly supplies a session that it has already initialized (for example,
the fleet simulation's detached `--emit-daemon-evidence init` session), treat it as
**pre-provisioned**. Verify it with `herd.sh --session "$SESSION" status`, then begin at
`ready` → `claim` → `spawn`; do not run `init`, `attach`, or `attach --recover-session`.
Those are lifecycle operations for a lead-created session, and rerunning them against a live
caller-owned session can contend on its bootstrap lock and manufacture a false recovery error.
The caller retains lifecycle ownership and performs the named-session cleanup after evidence
capture.

This skill ships no scripts of its own. If the paths above do not resolve, stop and
report the missing skill rather than falling back to a copy inside this directory.

## Non-negotiable rules

Each composed skill states its own rules; these are the ones that only exist at the seam.

1. One bead, one agent, one durable mapping. The default worker name is `bead-<id>`, but
   Herdr permits only 32 lowercase characters (`[a-z][a-z0-9_-]{0,31}`). The `bead-` prefix
   consumes 5, leaving **27** for an unmapped Beads ID. For any longer ID, set a unique legal
   complete worker name in durable Beads metadata: `agent_context.herder.pane_id`, for example
   `"pane_id":"bead-gsl4-a"`. Validate it before claim; never truncate an ID. Beads metadata
   is authoritative and the runner records the same full `bead_id` in session `tasks.json` only
   as a recovery cache. Lowercase is not cosmetic: `Bead-<id>` fails at `agent start`.
2. Claim before spawn, and roll the claim back if the spawn fails. A claimed bead with no live
   pane is the one state no later command can detect.
3. The runner's `complete` gates the pane; the scheduler's `close` gates the bead. Run both,
   in that order, and never close a bead whose `complete` was refused.
4. Leases are acquired by the runner's `spawn`, not by hand. Use `resource_pool.py` directly
   only to define capacities up front and to inspect or reap afterwards (note: `resource_pool.py status`
   prints JSON by default; it does not take a `--json` flag).
5. A blocked worker is parked, not left holding locks: `park --agent <name> --cancel` frees its leases, then
   record the blocker on the bead. Continue the independent part of the wave.
6. One wave at a time. `dispatch` is deliberately not a daemon; re-enter the loop after
   completions rather than topping up panes continuously.
7. Never authorize checkout/switch/reset/clean/stash, worktree mutation, or destructive
   shared-state actions while fleet work may coexist.
8. Never answer a dialog with `herdr agent send-keys`, `pane send-keys`, or any raw keystroke.
   Every authorization goes through `approve`, which is what applies the policy; a keystroke
   goes around it. This holds for trust dialogs, permission prompts and plan approvals alike,
   and it holds for a worker you believe is stuck. A refusal you disagree with is a finding to
   record on the bead, not an obstacle to route around — observed once, a lead answered a
   refused trust dialog itself and logged it as "not a failure".
9. Strict Anti-Over-Reading Invariant: Never read, grep, or explore underlying script implementation files (`fleet.py`, `auto_herd.sh`, `run_simulation.py`, `scheduler.py`, `resource_pool.py`, etc.) inline in the herder context. `fleet.py` is ~178KB; reading it consumes massive token budgets, triggers repetitive chunk-reading loops, and bloats thinking latency. The herder is strictly an orchestrator and arbitrator: execute the documented CLI commands without exploring their source code.
10. Fast 3-Step Wave Bootstrap: Start Wave 1 worker spawning within ~5 tool calls without exploratory pre-spawn loops:
    1. Read brief / verify session (`herd.sh --session "$SESSION" status` for pre-provisioned sessions, or `herd.sh --session "$SESSION" init --cwd /repo` if creating a new lead session)
    2. Discover & claim ready wave (`scheduler.py ready` -> `scheduler.py claim`)
    3. Spawn workers (`herd.sh spawn`) & launch background supervisor (`auto_herd.sh`) and yield.
11. Mandatory Diagnostic Delegation: IF error diagnosis requires >1 command or inspecting >30 lines of logs -> MUST delegate to `triage-sidecar` or an ephemeral subagent under the 2-sentence distillation contract (Root cause + Fix target). Strictly forbid running unbounded `cat`, `grep`, `diff`, or `git status` directly in the herder context.

## Deny list and dispatchable task briefs

`risk_policy.json` carries two independent rule lists, because pane text and task text differ
in both trust and shape.

**Pane subject — `deny` then `allow`.** When an agent asks for approval, `classify_text`
checks the scraped pane subject against `deny` (19 rules: `rm -rf`, `git reset --hard`,
`sudo`, compound shell, …) and returns `escalate` on the first hit. Only if no deny rule
matches does it consult `allow`. **Deny is checked first and wins** — an `allow` entry cannot
rescue a subject a deny rule already caught. `bash-syntax-check` and `repo-test-entrypoint`
work because no deny rule matches those commands, not because `allow` outranks `deny`. If a
routine command is being escalated, the fix is to narrow the deny pattern, not to add an
allow entry. Anything matching neither list escalates (default-deny).

**Task prose — `task_deny` only.** `check_task_text` checks the assigned task against
`task_deny` (16 rules), never against `deny` or `allow`. `spawn`, `prompt`, and `reset`
refuse outright on a hit; `classify_text(subject, policy, task=…)` uses the same check to
taint the agent, so every later prompt from it escalates even a harmless read. `task_deny`
patterns deliberately match *instruction shape* — an explicit control flag with its leading
dashes, or a full destructive invocation — rather than bare vocabulary, and skip the
wrap-interpretation readings the pane path needs. That is why prose merely *discussing* a
restricted topic stays dispatchable where a pane subject would not.

**Rule for writing a dispatchable brief.** A brief that quotes a destructive command in
order to *prohibit* it is still textually an instruction to run it, so `task_deny` refuses
it. Describe what the worker *must do*, and forbid by category rather than by command: write
"do not discard working-tree changes" instead of naming the git commands that do it. The
rules stay in `risk_policy.json` so they keep catching those commands when an agent actually
issues one; they must not appear verbatim in task prose.

Adding or narrowing a rule in either list is a `risk_policy.json` edit plus a
`tests/test_safety.py` fixture, not a SKILL.md edit.

> **Host permissions & sandboxing note:** Installed skill directories (e.g. `~/.claude/skills/...` or `~/.gemini/config/skills/...`) are installed read-only (`dr-xr-xr-x`) to prevent unversioned drift. Herders running on sandboxed hosts cannot modify `risk_policy.json` directly. If a rule requires widening, stage the policy edit in the working repo and run tests via `git-add-explicit` / tests, or hand off an install command to the operator. Common read-only commands such as `test -f <path>` and `git add <explicit-files>` are authorized under `allow` to prevent unnecessary escalations.

## The metadata contract

`agent_context.herder` is where the graph carries execution intent, and this skill is what
translates it into runner flags. The `herder` key is opaque to the scheduler and unknown to
the runner; nothing validates the mapping but this document.

```json
{"herder":{"cli":"codex","model":"gpt-5","approval_mode":"auto","read_only":false,"cwd":".",
 "resources":["file:src/api.ts"],"verification":["pytest -q tests/api"]}}
```

| Field | Becomes |
|---|---|
| `cli` | `spawn --cli` |
| `model` | `spawn --model` |
| `profile` | `spawn --profile` (account; omitted → the profiles file's `default`, else the plain CLI) |
| `approval_mode` | `spawn --approval-mode` |
| `read_only` | `spawn --read-only` (forces `plan`; refused for AGY/OMP) |
| `cwd` | `spawn --cwd`, resolved against the bead's repository root |
| `resources` | one `spawn --resource` each |
| `group` | `spawn --group`; defaults to `repo:<repo-root>` when absent |
| `verification` | **not** passed to `spawn`; enforced only at `scheduler.py close` |

**Omit `model` unless the operator named one.** Leave the field out and the worker spawns on
the runner's own default model. Setting it is an override that changes cost, quota
consumption and behaviour without the operator having asked for it, and a spent quota stalls
every later wave, not just the bead that spent it. Name a model only when the operator
specified one for that bead, or when a capability gap you can state makes the default
unusable — and say which it was in the dispatch note.

**Pass `profile` only when the operator named an account** for the run or the bead ("use my
personal codex"). When an operator names one for the whole run, add `--profile` to every spawn rather than editing
the profiles file; its `default` belongs to the operator. `reset` keeps a worker's profile, so
do not repeat it there. An unknown profile fails the spawn; report it rather than retrying without it.

Set `group` only for a resource shared beyond one repository — a staging endpoint, a test
database, an API quota, or a model provider quota — because that is the case the repository-scoped default gets wrong.
For example, when running multiple workers against the same provider, pass `--group model:<provider>`
with a configured capacity so quota is leased safely rather than discovering exhaustion on mid-turn failure.
A shared group also needs its capacity defined up front; everything else is capacity one.

That last row is the one asymmetry worth remembering. `spawn` has no `--verification` flag, so
the runner's `complete` can only check that evidence exists and every exit code is zero — it
cannot know which commands were required. The required-command check happens when the
scheduler closes the bead against its own `agent_context`. Run `complete` first anyway: it is
what releases the leases.

> **Resources list write scopes only:** `agent_context.herder.resources` (or `agent_context.resources`) must list write scopes only (e.g. `file:src/api.ts`). Never add read-only files to `resources`. Adding read-only paths serializes candidate waves that could otherwise execute safely in parallel without lock contention.

> **Scope verification commands strictly to changed surface (avoid repo-wide foot-guns):** Verification commands must be scoped strictly to the bead's changed surface (e.g. `npm test -- test/my-module.test.ts` or `pytest tests/test_feature.py`). Never configure repo-wide recipes (e.g. monorepo-wide `just lint-affected` or root `npm run lint`) in bead verification metadata. If a repository has pre-existing unrelated lint or test debt, repo-wide recipes will fail closed and prevent bead closure after the worker completes.

## The wave loop

> **Rule 1 (session targeting):** Always pass `--session "$SESSION"` to every `herd.sh`
> and `auto_herd.sh` call. Omitting it targets the default Herdr session; a wrong session returns
> an empty fleet that `auto_herd.sh` treats as an error on initial poll (exit 4). One session name,
> set once at the top of the script, used everywhere.

```bash
REPO=/repo
# For a fresh lead session, initialize it (for pre-provisioned sessions, verify with 'herd.sh --session "$SESSION" status' instead):
bash "$HERD" --session "$SESSION" init
# Optionally pre-warm a read-only triage sidecar for zero-turn error diagnosis:
bash "$HERD" --session "$SESSION" spawn --name triage-sidecar --cli codex --read-only --approval-mode plan --cwd "$REPO" \
  --task "Pre-warmed triage sidecar for offloaded error investigation, stack trace analysis, and root-cause distillation"

python3 "$SCHED" ready --project "$REPO" --limit 4 --json          # rank unblocked candidates
python3 "$SCHED" claim <bead-id> --actor <pane-id> --project "$REPO"
bash "$HERD" --session "$SESSION" spawn --name <pane-id> --bead-id <bead-id> --cli codex --cwd "$REPO" \
  --task "<bead title + acceptance criteria>" \
  --group "repo:$REPO" --resource file:src/api.ts --approval-mode auto
```

### Standing rules for worker tasks

Every dispatched task should include standing house rules to maintain branch and staging isolation:
- **Stay on branch:** Work strictly on the assigned Git branch; never switch branches.
- **Isolated staging:** Stage only your explicitly leased paths by name (`git add <file1> <file2>`), never repo-wide (`git add -A` or `git add .`), to avoid git staging conflicts with parallel workers. (Note: `risk_policy.json` explicitly authorizes `git-add-explicit` for named paths, while repo-wide staging is refused).
- **Bounded-step execution:** Dispense tasks with explicit bounded steps (e.g. "Do exactly these N steps and stop; explore nothing else"). Do not dispatch open-ended exploratory prose, which exhausts worker execution budgets.
- **Run verification:** Run the required verification command before emitting the handoff.
- **Structured evidence:** Emit the required `FLEET_HANDOFF: {"status":"completed",...}` JSON ending token.
- **Commit conventionally:** Use standard conventional commit format referencing the bead ID.

### Fleet supervision: background execution vs direct watch

Supervising the fleet does not require burning an agent turn per check. `fleet.py watch` (invoked via `herd.sh watch` or inside `auto_herd.sh`) blocks inside one process for the full wait duration (`--timeout`), so a single `watch` invocation costs the herding agent **zero turns** while it waits. The turn cost only arises when a herder repeatedly re-invokes `status` or `watch` as separate tool calls across turns in a polling loop.

For unattended waves, run the supervision loop as a background command and stop calling tools; your host will re-invoke you when it exits.

| Host | Background invocation mechanism | Status |
|---|---|---|
| Claude Code | Bash tool with `run_in_background: true` | Verified |
| Antigravity | `run_command` (async background task execution with Reactive Wakeup) | Unverified |
| Codex CLI | Host background process execution | Unverified |
| Other hosts / harnesses | Host-specific asynchronous shell tool with completion callback | Unverified |

```bash
# Launch unattended supervision in the background and yield (stop calling tools):
bash "$AUTO" --session "$SESSION"
```

#### What `auto_herd.sh` does and does not do

`auto_herd.sh` automates the in-process watch/approve/complete loop:
- **Auto-approves only policy-approved prompts:** Calls `herd.sh approve --agent <name>`, which checks prompt text against `risk_policy.json`. If a prompt escalates or is denied, it halts with exit code `1` for human attention rather than forcing an approval.
- **Completes only validated evidence:** Calls `herd.sh complete --agent <name>` when an agent becomes `idle` or `done`. If completion evidence is missing or verification failed, it marks the agent blocked and halts with exit code `1`.
- **Exits on empty fleet:** Exits with code `0` when all observed agents complete successfully.
- **Guards against empty/failed spawn:** An empty fleet on the **first poll** exits with code `4` (failure) instead of `0`, catching wrong session names or failed spawns immediately. Only an empty fleet observed after active agents have run is treated as completion.
- **Closes beads when given `--project`:** `bash "$AUTO" --session "$SESSION" --project <repo>` binds pane completion to bead closure — on a successful `complete` it resolves the pane through durable `herder.pane_id`, then runs `scheduler.py close` on that full Beads ID using the recorded handoff. It never closes a bead whose `complete` was refused. Without `--project` it only gates panes and releases leases, and `scheduler.py close` stays the herder's responsibility upon host wakeup.
- **Ignores supervisor pane:** The lead herder's own pane appears in the same session fleet listing as its workers (e.g. `sim-herder-XXXXX`, state `working`). `auto_herd.sh` automatically excludes it and monitors only worker panes, so the supervisor never appears blocked.

#### When to watch directly instead

Use direct synchronous watch (`bash "$HERD" --session "$SESSION" watch --until actionable --timeout 60`) instead of `auto_herd.sh` when:
- Tasks require human-in-the-loop judgment calls that risk policy cannot make.
- High-risk operations are expected to trigger policy escalation and need immediate interactive review.
- Active debugging or troubleshooting of a live worker pane is required.

Even when watching directly, a single blocking `watch` covers the wait—do not poll in a loop across turns.

### Closing completed beads and recovery

Once `auto_herd.sh` exits `0` (or after direct pane completion):

```bash
# complete records handoffs under handoffs.json; pass inner handoff to close:
python3 "$SCHED" close <bead-id> --actor bead-<bead-id> --project "$REPO" \
  --handoff @handoff.json                          # bead gate; checks required verification
```

`complete` prints `{"agent":…,"handoff":{…}}` and records the same payload under
`handoffs.json`. Pass the inner `handoff` object to `close` — as a bare JSON string, a full
`FLEET_HANDOFF: {json}` line, or `@file`.

Recovery, by failure mode:

| What happened | Do this |
|---|---|
| `auto_herd.sh` exited 4 | Check the session with `herd.sh --session "$SESSION" status`; if its server was lost, use guarded `herd.sh --session "$SESSION" attach --recover-session` only after reviewing the loss |
| `auto_herd.sh` exited 1 (blocked/escalated) | Inspect `herd.sh --session "$SESSION" status`, resolve prompt via `herd.sh --session "$SESSION" approve` or `herd.sh --session "$SESSION" park --agent bead-<bead-id> --cancel` |
| `spawn` failed after `claim` | `scheduler.py rollback <bead-id> --actor <pane-id> --project "$REPO"` |
| worker crashed or pane vanished | `herd.sh --session "$SESSION" reconcile --group <group>`, recover the full ID from session `tasks.json` (or inspect `known_dead_agents`), verify it with `scheduler.py resolve-pane --pane-id <pane-id> --project "$REPO"`, then `scheduler.py rollback <bead-id> --actor <pane-id> --project "$REPO"` |
| `complete` refused the evidence | leave the claim, re-prompt the worker; do not `close` |
| human decision needed | `herd.sh --session "$SESSION" park --agent bead-<bead-id> --cancel`, note the blocker on the bead; once resolved, revive via `herd.sh --session "$SESSION" reset --agent bead-<bead-id> --cli agy --task "<task>" --handoff "<handoff>" --group <group> --resource <resource> --approval-mode auto` |
| worker interrupted/cancelled | Inspect pane, formulate alternate path, and prompt (`herd.sh --session "$SESSION" prompt --agent <name> --text "<guidance>"`) or reject/halt (`herd.sh --session "$SESSION" reject --agent <name> --halt`) |
| provider quota exhausted | `herd.sh --session "$SESSION" park --agent <name> --cancel` for all active panes, `scheduler.py rollback` unclaimed beads, record reset time on the bead, and re-dispatch on alternate model/lease |
| wave finished | re-run `ready`; stop when it returns nothing |

> **Parked worker recovery note:** Parking an agent (`herd.sh --session "$SESSION" park --agent <name> --cancel`) releases its resource leases and records `lifecycle: "parked"`, preventing fleet deadlocks while awaiting human resolution. To resume a parked worker once the human decision or blocker is resolved, use `herd.sh --session "$SESSION" reset` with the same lease and task flags as spawn (`--group`, `--resource`, `--approval-mode`, `--task`, `--handoff`) to launch a fresh context with newly acquired leases. Direct `prompt` into a parked agent is refused because its prior leases were freed. Note: If calling `fleet.py` directly, `--session <name>` must precede the subcommand and requires `--allow-cross-session`.

> **Crash recovery note:** `herd.sh --session "$SESSION" reconcile --group <group>` reclaims resources held by dead workers. By default convention the group is `repo:$REPO`. If unknown or across multiple repos, obtain it by inspecting `tasks.json` in the session state directory (`~/.config/.agents/herder/<session>/tasks.json`) or listing active groups via `python3 "$POOL" list-groups` / `python3 "$POOL" status` (note: `resource_pool.py status` prints JSON by default and has no `--json` flag).

Multi-repo waves use `ready --workspace /work/.bv/workspace.yaml`, but `claim` and `close` are
per-repository: pass the `--project` that the candidate came from.

## Herder context protection & token efficiency

Orchestrating multi-wave agent fleets across long sessions requires active context protection. Prevent context bloat, token exhaustion, and premature compaction by applying five core disciplines:

1. **Strict Anti-Over-Reading & Direct Dispatch:**
   - **Never Read Script Implementation Source:** Do NOT open, view, or grep `fleet.py`, `auto_herd.sh`, `herd.sh`, `scheduler.py`, `resource_pool.py`, or `run_simulation.py` inline. Reading massive internal files (~178KB) causes severe context bloat and thinking token runaway.
   - **Fast 3-Step Bootstrap (Zero-Research Spawning):** Reach Wave 1 worker spawning in ~5 tool calls: (1) read brief / verify session (`herd.sh --session "$SESSION" status` for pre-provisioned sessions, or `herd.sh init`), (2) query & claim ready candidates (`scheduler.py ready`, `scheduler.py claim`), (3) spawn workers (`herd.sh spawn`) and run `auto_herd.sh`. Preflight and CLI flag validations are handled by `preflight.sh` or automated harness.

2. **Zero-turn background supervision (`auto_herd.sh` / `watch`):**
   - Polling workers across repeated conversational turns burns context rapidly. Never run a loop of discrete `status` or short `watch` tool calls.
   - Run `bash "$AUTO" --session "$SESSION"` (with `--project "$REPO"` for unattended bead closure) as an asynchronous background command and yield control (stop calling tools).
   - In single-step or interactive scenarios, a single blocking `watch` call (`herd.sh watch --timeout 60`) waits inside one helper process for the full duration, costing **zero LLM turns** until an actionable event occurs.

3. **Pre-Warmed Triage Sidecar & Mandatory IF-THEN Invariant:**
   - **Pre-Warmed Triage Sidecar Pattern:** Spawn `triage-sidecar` (read-only/plan mode) during `init` / bootstrap so triage capacity is immediately available without cold-start spawn delay.
   - **Mandatory IF-THEN Context Invariant:**
     * **IF** error diagnosis or anomaly investigation requires >1 command OR inspecting >30 lines of logs -> **MUST** delegate to `triage-sidecar` or an ephemeral subagent.
     * **2-Sentence Distillation Contract:** Enforce that sidecars / subagents return strictly:
       1. **Root cause:** Concise statement of failure mechanism.
       2. **Fix target:** Exact file, line, or configuration change needed.
     * **Strictly Forbidden Unbounded Commands:** Never run unbounded `cat`, `grep`, `diff`, or `git status` directly in the herder context.

4. **Standard Command Output Bounds:**
   When the herder must run diagnostic or verification commands directly, always bound the output:
   - `git status -s` (never bare `git status`)
   - `diff -u ... | head -n 30` or `diff -q` (never unbounded `diff`)
   - `grep -m 5` (never unbounded `grep`)
   - `pytest -q --tb=short --maxfail=1` (never unbounded test runner execution)
   - `head -n 20` / `tail -n 30` when inspecting logs or traces

5. **DAG-scoped task decomposition in `beads-task-scheduler`:**
   - Keep the dependency graph, full backlog, and planning state inside the Beads graph (`.beads/` database) rather than duplicating the global plan in prompt context.
   - Dispatched workers receive only their immediate bead's title, description/acceptance criteria, and scoped resource paths.
   - The herder maintains only the active wave's claims, leases, and handoff evidence—preventing global plan duplication across turns.

## Core loop

1. **Bootstrap:** Verify pre-provisioned session (`herd.sh --session "$SESSION" status`) or initialize new session (`herd.sh init`), and optionally spawn pre-warmed `triage-sidecar`. Also resolve feature-memory: `slug="$("$MEM_SLUG")" || slug=""`; if non-empty and `dev-plans/$slug/memory/MEMORY.md` exists, read it once — its domain map gives you real file pointers to cite in bead descriptions and `resources`, instead of the fleet rediscovering them per-worker. If it doesn't exist, don't block on scaffolding it here; that's feature-memory's own concern, not the herder's.
2. **Discover & Claim:** Rank unblocked candidates with `python3 "$SCHED" ready --project "$REPO" --limit 4 --json`, then claim each with `python3 "$SCHED" claim <id> --actor bead-<id> --project "$REPO"`.
3. **Spawn & Supervise:** Spawn workers with `bash "$HERD" --session "$SESSION" spawn ...`, then launch `bash "$AUTO" --session "$SESSION" --project "$REPO"` as a background command and yield.
4. **Advance Waves:** Once the wave completes, re-run `ready` for the next wave; repeat until `ready` returns no candidates.
5. **Recovery & Blockers:** If human attention is needed, approve eligible prompts or `park --cancel` blockers. If a spawn fails, `rollback`. Reconcile leases on crash (`herd.sh reconcile`).
6. **Report:** Derive final report from durable state (`br list --status closed --json`) and record all closed, parked, and error entries. If `dev-plans/$slug/memory/MEMORY.md` exists and the fleet's work changed its Status (phase, blockers) or moved a concept to a new file, update those lines — one edit, not a per-wave log. Beads stays the durable graph; MEMORY.md stays the human-readable orientation pointer to it.

## Load detail only when needed

- **Graph, metadata schema, claims, completion gates, multi-repo:**
  [beads-task-scheduler](../beads-task-scheduler/SKILL.md)
- **Panes, monitoring, approvals, handoffs, escalation, Herdr surface:**
  [terminal-agent-runner](../terminal-agent-runner/SKILL.md)
- **Resource naming, capacities, TTLs, FIFO queues, orphan recovery:**
  [resource-lease](../resource-lease/SKILL.md)
- **Per-feature quick start / domain map / status, folder resolution:**
  [feature-memory](../feature-memory/SKILL.md)
- **End-to-end examples and verification scenarios:** [examples.md](references/examples.md)
- **End-to-end test — real agents, real panes, real graph:**
  [resources/fleet-simulation/README.md](resources/fleet-simulation/README.md) — it drives the
  whole wave loop against a disposable Beads project in a Herdr workspace of its own. There is
  no offline mode; unit-level behaviour belongs to the three composed skills' own suites.

Prefer the relevant command's `--help` over loading a broad reference when the question is
about one flag. Never invent a CLI flag from memory.
