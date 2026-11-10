---
name: subagent-feature-driver
description: Orchestration skill for autonomous, long-running feature development using lean context management and disciplined subagent delegation. The lead agent acts as planner and verification gatekeeper while delegating raw research, implementation, and hunk-level testing to at most two concurrent subagents. Use when implementing multi-step features, executing refactors, or running unattended workflows across Claude, Codex, and Antigravity (AGY).
---

# Subagent Feature Driver

Context-preserving orchestration protocol for autonomous feature delivery. Eliminates lead context pollution: the lead agent plans, decides, and gates verification, offloading code inspection, editing, and test runs to subagents.

**Composed skill: [feature-memory](../feature-memory/SKILL.md).** `.feature-state.md` (this
skill) and `dev-plans/<slug>/memory/MEMORY.md` (feature-memory) are different artifacts, not
duplicates: `.feature-state.md` is the milestone DAG and decision journal — write-heavy, session-
scoped. `MEMORY.md` is a small routing hub — quick-start commands, a concept→code map, a status
line — that outlives any one session and is what a fresh lead reads first to orient before it
even opens `.feature-state.md`. At the start of a feature, resolve it (`feature-memory`'s
`scripts/feature-slug.sh`) and read `memory/MEMORY.md` if present; do not re-derive its domain
map from scratch. Keep milestone/decision detail out of it — that stays here.

---

## 1. Core Operating Directives

1. **Zero Lead Context Pollution**: Lead never reads raw source files, verbose diffs, or debug traces. Lead ingests only structured subagent summaries (paths, contract changes, pass/fail verdicts).
2. **Strict Concurrency Ceiling**: Max 2 active subagents:
   - **Worker (`implementer`)**: Writes code, runs hunk-level tests.
   - **Sidecar (`investigator`/`flex`)**: Codebase research, log analysis, or parallel independent tasks.
3. **Autonomous Decisions**: Halt for human input **only** on irreversible forks (destructive migrations, external billing/auth, unprecedented architecture). Decide all tactical and precedented architectural choices immediately and log in the Decision Journal.
4. **Cross-Runtime Parity**: Functions identically across Antigravity (AGY), Claude Code, and Codex CLI agents.

---

## 2. Role Boundaries

| Role | Owns | Allowed Tools | Context Payload | Returns |
| :--- | :--- | :--- | :--- | :--- |
| **Lead (Judge)** | Milestone DAG, gating, invariants, decisions | Subagent dispatch, top-level verification scripts | Milestone goals, status, decision journal | Deliverable confirmation, acceptance proof |
| **Worker (Implementer)** | Vertical slice, edits, unit tests | File edits, terminal, local test runner | Scope brief, invariants, target paths, test cmd | Lean summary, touched files, Tier-1 verdict |
| **Sidecar (Investigator)** | Reconnaissance, contract discovery, spikes | Read, search, git log, docs | Focused questions, target symbols | Distilled paths, signatures, conventions |

---

## 3. Subagent Lifecycle & Token Management

- **Clustering vs. Isolation**:
  - *Cluster* related tasks in the same subagent session to reuse file context.
  - *Isolate* across architectural boundaries (e.g. core to UI) or after large error traces by spinning up a fresh session.
- **Dispatch Contract**:
  ```markdown
  TASK: [Milestone ID - Title]
  - Objective: [Target behavior]
  - Boundaries: [Allowed paths] | Forbidden: [Untouched core]
  - Invariants: [Domain rules to maintain]
  - Evidence: [Exact test command to verify]
  ```
- **Return Contract**:
  ```markdown
  STATUS: [SUCCESS | BLOCKED]
  - Touched: [Modified files]
  - Summary: [2-3 sentences on flow changes]
  - Tier-1 Test: [Exact command and pass result]
  - Decisions: [Tactical assumptions made]
  ```
- **Compaction**: Upon closing a milestone, discard subagent logs and record only status and journal entries in the lead state. If the milestone moved a concept to a new file or changed the feature's overall phase, also refresh those one-line entries in `memory/MEMORY.md` (feature-memory) — not the full decision, just the pointer.

---

## 4. Decision Framework & Journal

Stop for user input **only** at irreversible forks. For tactical choices (naming, schema primitives, existing utils), pick the simplest existing repo pattern and append to the state journal:

```markdown
- [Decision] <timestamp> (<Milestone ID>): <Title>
  - Choice: <Selected pattern> | Precedent: <Repo file/convention>
  - Flow Impact: <User/system flow effect>
```
*See [decision-framework.md](references/decision-framework.md) for taxonomy details.*

---

## 5. Tiered Verification

- **Tier 1 (Worker)**: Scoped unit specs and hunk-level assertions executed before returning.
- **Tier 2 (Lead Gate)**: Integration smoke suite, invariant compliance, and blast-radius verification run across milestone boundaries and at completion.

```bash
# Lead blast-radius and regression gate:
./skills/subagent-feature-driver/scripts/verify_blast_radius.sh <allowed_paths...>
pytest -q tests/integration/ # or repo equivalent
```

---

## 6. Runtime Dispatch Mapping

| Runtime | Research / Sidecar | Worker Dispatch | Execution Rules |
| :--- | :--- | :--- | :--- |
| **AGY** | `invoke_subagent` (`TypeName: "research"`) | `invoke_subagent` (`TypeName: "self"`) | Never poll; await reactive events. Use artifacts for large plans. |
| **Claude** | `Task` tool with read-only prompt | `Task` tool with edit/test boundaries | Pass explicit return contract; maintain `.feature-state.md`. |
| **Codex** | Subshell `codex exec` (bounded stdout) | Subshell `codex exec` (sandboxed) | Standard file-based IPC; gate on exit code and summary. |

*See [runtime-adapters.md](references/runtime-adapters.md) for full prompt templates.*
*See [FEATURE_STATE.md](templates/FEATURE_STATE.md) for the active session template.*
*See [feature-memory](../feature-memory/SKILL.md) for the per-feature quick start / domain map / status doc this composes with.*
