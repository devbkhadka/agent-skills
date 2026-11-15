---
name: verified-feature-delivery
description: Phased, contract-driven planning and verification pipeline resolving the five fundamental failure modes in agentic development across six verifiable stages.
---

# Verified Feature Delivery

A stateful, contract-driven, six-stage pipeline that eliminates agentic software engineering failure modes: incomplete requirements, mock-to-reality verification gaps, conversational abstraction mismatch, horizontal layer lock-in, and regressive loops.

Before writing or amending any gate in this pipeline, read [`SURROGATE-VERIFICATION.md`](SURROGATE-VERIFICATION.md) — the three ways a check passes broken work, written from the eleven real defects found while building this skill's own gates.

## Environment prerequisite: the browser that observes a `rendered_ui` slice

A `rendered_ui` capture is not accepted on the agent's word that it was observed. Stage 5
drives the declared `observation_target` in headless Chromium itself and compares what the
browser rendered against the claim, so **Node.js and Playwright must be resolvable from the
repository under verification** before a slice may declare that medium. Resolution order is
`PLAYWRIGHT_PATH` (the playwright module directory), then `NODE_PATH` (a `node_modules`
directory containing playwright), then `node_modules/playwright` walking up from the repository.

Without it, `bin/verify-evidence` refuses the delivery with `OBSERVATION_BROWSER_UNAVAILABLE`
rather than passing it — a delivery nobody could observe is not verified. Decide this at
Stage 3, when the slice picks its verification medium, not at Stage 5 when the gate refuses.

## State Dispatcher & Progressive Loading Protocol

To keep cognitive token context minimal, agents must **progressively load** instructions. Do not load downstream stage guides or schema catalogs into context ahead of time. Executables under `bin/` (validators, renderers, state manager) are available to the active stage as referenced in that stage's phase guide.

1. **Query Current State**:
   Check the active stage in `.verified-feature-delivery/<feature-slug>/state.json` or run:
   ```bash
   python3 bin/planner-state show --feature <feature-slug>
   ```

2. **Load the Active Stage Guide**:
   Read **only** the guide file corresponding to the active state:

| `current_state` | Phase Directory & Entrypoint Guide | Primary Stage Objective |
| :--- | :--- | :--- |
| `UNINITIALIZED` | Run `init` then load `phases/0-domain-ingestion/guide.md` | Initialize feature state tracking. |
| `STAGE_0_DOMAIN_INGESTION` | `phases/0-domain-ingestion/guide.md` | Ingest domain taxonomy, lock precisions, register actors/observers, declare aliases & projection bindings (`domain-lexicon.json`). |
| `STAGE_1_CONTRACT_PROBING` | `phases/1-contract-probing/guide.md` | Probe live external boundaries, capture failure envelope & variance censuses (`contract-fixtures/`). |
| `STAGE_2_SCENARIO_SIMULATION` | `phases/2-scenario-simulation/guide.md` | Replay system states, cover branch groups A & B, declare observable projection claims (`flow-simulation-matrix.json`). |
| `STAGE_3_VERTICAL_SLICING` | `phases/3-vertical-slicing/guide.md` | Lock the 6 architectural dimensions, generate tracer-bullet vertical slice DAG (`execution-dag.json`). |
| `STAGE_4_MILESTONE_EXECUTION` | `phases/4-milestone-execution/guide.md` | Execute vertical slices, refute/confirm projection fingerprints, capture evidence, handle regression rollbacks. |
| `STAGE_5_DELIVERY` | `phases/5-process-delivery/guide.md` | Emit intent-structured communications, verify evidence-to-medium/claim axes, generate delivery report & changelog. |
| `COMPLETED` | Feature delivery and verification complete. | No further planning actions required. |

## Lifecycle CLI Commands

All state mutations and evaluations must be performed using `bin/planner-state`:

```bash
# Initialize state for a new feature
python3 bin/planner-state init --feature <slug>

# Show current lifecycle state and registered artifacts
python3 bin/planner-state show --feature <slug>

# Deterministic status query (returns stage, gate readiness, approvals, milestone progress)
python3 bin/planner-state status --feature <slug> [--json]

# Deterministic next action query (returns exact next action, command, and guide)
python3 bin/planner-state next-action --feature <slug> [--json]

# Generate slice-scoped sidecar context for workers (with embedded 3-tier verification plan)
python3 bin/planner-state sidecar --feature <slug> --slice <slice_id> [--diff <files>] [--out <path>]

# Generate or inspect deterministic risk- and impact-based verification plan
python3 bin/planner-state verification-plan --feature <slug> [--slice <slice_id>] [--diff <files>] [--stage-5-exit] [--json]

# Evaluate worker handoff capsule against gate criteria
python3 bin/planner-state evaluate-capsule --feature <slug> --capsule <path/to/capsule.json>

# Retrieve file-backed raw diagnostics on demand
python3 bin/planner-state diagnostics --feature <slug> --ref <diagnostics_ref>

# Advance to next stage (requires gate exit artifact on disk)
python3 bin/planner-state advance --feature <slug> --to <NEXT_STATE>

# Roll back on regression (Stage 4 only; requires valid regression cause)
python3 bin/planner-state rollback --feature <slug> --to <STATE> --cause <CAUSE>
```

## Structured Worker Capsule & Sidecar Protocol

To preserve cognitive context and avoid token exhaustion across long-running feature execution:

1. **Slice Sidecars & Verification Plans**: Workers do not load the full domain lexicon, scenario matrix, or execution DAG. The driver generates a slice sidecar (`bin/planner-state sidecar --feature <slug> --slice <slice_id>`), containing the target slice definition, relevant invariants, domain entity subsets, contract fixture references, and an embedded 3-tier `verification_plan` (`schemas/verification-plan.schema.json`) so workers execute only required tests without monolithic suite overhead. See [`phases/4-milestone-execution/verification-policy.md`](phases/4-milestone-execution/verification-policy.md).
2. **Compact Handoff Capsules**: Workers return structured capsules (`schemas/worker-capsule.schema.json`) limited to:
   - `status`: `completed | blocked | failed`
   - `outcome`: concise summary of work accomplished
   - `changed_artifacts`: relative paths of modified files
   - `verification`: array of `{command, exit_code, output, expected_exit, expected_empty}`
   - `evidence_paths`: relative paths of captured evidence files
   - `diagnostics_ref`: relative path to file-backed raw logs
   - `blockers`: array of blockers or regression cause tokens
   - `next_action`: recommended next action
3. **File-Backed Raw Diagnostics**: Raw execution stdout/stderr and verbose logs are written to `.verified-feature-delivery/<slug>/diagnostics/<task_id>.log`. The capsule references this path without embedding bulky traces, keeping the capsule payload strictly under 4000 characters.
4. **Capsule-Driven Progression**: The driver evaluates the capsule via `bin/planner-state evaluate-capsule` and directly advances stage or executes rollbacks from capsule evidence without parsing raw logs.

## BDD Scenarios & Mock-Boundary Contract

Executable BDD scenarios bridge Stage 2 flow simulation and Stage 4 milestone execution (`schemas/bdd-scenario.schema.json`):
1. **Strict Mock-Boundary Isolation**: Real production code executes for domain entities, validation logic, and state machines. ONLY external boundaries declared in `boundary-manifest.json` may be mocked.
2. **Contract Fixture Replay**: External mocks must replay verified recordings from `contract-fixtures/`. Ad-hoc mocks are prohibited (`UNVERIFIED_FIXTURE_REPLAY`).
3. **Interface Discovery & Rollback**: Static validators reject undeclared tokens (`UNDEFINED_STATE_TOKEN`, `UNBOUND_PROJECTION_QUANTITY`), while runtime discovery failures trigger deterministic rollbacks via `lib/rollback.py` with content-addressed cache invalidation. See [`phases/4-milestone-execution/bdd-scenario-contract.md`](phases/4-milestone-execution/bdd-scenario-contract.md).

## Non-Negotiable Driver Role & Context Hygiene

The pipeline is explicitly long-running (spanning six stages and many milestone waves). The driving agent is the singular context that must survive the entire delivery as an independent, neutral judge:

1. **Driver Produces No Evidence & Reads No Source**: The driver never runs test suites, compilers, repository diffs, raw file reads, migrations, or log inspection directly. **Exception — deterministic checks:** the driver may run a check whose verdict is its exit code and whose output is a reason code (`bin/planner-state`, the `bin/validate-*` gates, `bin/triage-change`, `bin/find-gaps`, an emitted `check_architecture.py`). Their verdict is not the driver's judgement, so it is not tainted.
2. **Structured Worker Dispatch**: Every exploratory or evidence-generating command is dispatched to a worker subagent or Herdr pane and returns as a structured claim object. The driver's tool use is strictly restricted to dispatching tasks, evaluating structured handoffs, and recording state decisions via `bin/planner-state`.
3. **Independent Cross-Verification**: When the driver questions or rejects an evidence claim, it must spawn an independent cross-verification agent. Re-running the command directly converts the judge into a producer and destroys the independence of the verdict.
4. **Self-Verification Penalty**: Any *judged* claim the driving agent produces itself — a reading of a log, a diff, a rendering or prose — is tainted as `SELF_VERIFIED` and cannot satisfy independent gate criteria. Exit-code verdicts of deterministic checks (rule 1) are not judged claims.



