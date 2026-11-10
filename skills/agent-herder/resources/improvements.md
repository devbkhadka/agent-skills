# Agent-Herder Improvements & Issue Consolidation

Empirical findings recorded during live fleet runs:
- **Run 1 (2026-08-23):** Single AGY worker on `verified-feature-delivery` pipeline / greenfield project (`fleet-vfd-timelog` / worker `vfd1`). Findings #1–#13.
- **Run 2 (2026-08-24):** Two AGY workers over a 7-bead graph in an existing TypeScript repository (`e15-fleet`, beads `lxe15-e15-t*`). Findings #14–#25.

---

## Priority & Consolidation Matrix

| Priority | Consolidated Area | Core Failure Mode | Original Findings | Primary Target Components |
|---|---|---|---|---|
| **P0 - Critical** | **1. Worker State Detection & False "Done" / "Idle" Hazards** | Active foreground commands and unclassified dialogs detected as `done`/`idle`, causing premature worker resets, stalled execution, and lost work in flight. | #7, #10, #15 | `fleet.py`, `herd.sh`, `auto_herd.sh`, classifier |
| **P0 - Critical** | **2. Handoff Contract Gating, Clock Skew & Evidence Loss** | Rigid completion gates reject valid work due to sub-second clock jitter, char limits, omitted brief contract instructions, or minor formatting slips without override paths. | #9, #12, #14, #24, #25 | `fleet.py` (`complete`, `parse_handoff_file`), brief generation |
| **P1 - High** | **3. Permission Escalations, Allowlist Matching & Auto-Herd Stalls** | Static token matching, one-shot reviewed approvals, and default-deny escalations stall autonomous runs, deadlock worker scratch scripts, and break `auto_herd.sh`. | #1, #2, #3, #4, #11, #22 | `agy_permissions.py`, `risk_policy.json`, `herd.sh approve` |
| **P1 - High** | **4. Task Initialization, Naming & Scheduler Invariants** | Silent acceptance of empty tasks, late crashes from 27-char bead ID limits, unvalidated agent names, and unhandled context resolution errors in candidate lists. | #16, #17, #18, #19 | `scheduler.py`, `fleet.py spawn`, Herdr agent naming |
| **P2 - Medium** | **5. Test Baseline Awareness & Known-Red Suite Support** | Redundant worker test runs wasting turns, completion gates failing on pre-existing test failures, and environment-dependent verification claims. | #13, #21, #23 | `agent_context`, verification gates, herder briefing |
| **P2 - Medium** | **6. Herder Ergonomics, Steering & Communication Channels** | High token consumption from polling loops, worker guessing due to lack of question channels, `--related` continuation friction, and CLI argument ordering traps. | #5, #6, #8, #20 | `herd.sh watch`, `fleet.py`, protocol documentation |

---

## P0 (Critical) — Execution Integrity & Work Loss

### 1. Worker State Detection & False "Done" / "Idle" Hazards
*(Consolidates Findings #7, #10, #15)*

#### Problem Description
The fleet supervisor (`status`, `classify`, and `auto_herd.sh`) misclassifies active or blocked worker states as `done` or `idle`. When a worker is actively executing a long-running foreground command (e.g. running a 3-minute test suite) or waiting on an unclassified non-command interactive prompt (e.g. AGY file access confirmation or CLI satisfaction surveys), the detection engine fails to recognize the live state:
- **Foreground test commands misclassified as `done` (#15):** While `npm run test:scripts` was actively running with a live spinner in the pane, `status` reported the worker as `done`. `auto_herd.sh` repeatedly re-prompted the worker, declared it "idle but lacks completion evidence", and exited with code 1, destroying valid verification work in progress.
- **Unclassified interactive dialogs misclassified as `done` (#7, #10):** When AGY presented an interactive file-access confirmation or an inline product survey (`"How's the CLI experience so far? [1] Good [2] Fine [3] Bad [0] Skip"`), `status` reported `done` or `working` while `classify` reported `{"pending": false}`. A supervisor relying on these signals either prematurely accepted unverified beads or allowed workers to hang indefinitely without notification.

#### Technical Improvements
1. **Detect Running Foreground Processes in Pane:** Treat any pane displaying active process execution or progress indicators (spinners, running command markers) as `working`, not `done`.
2. **Delta-Based Idle Detection in `auto_herd.sh`:** Before declaring a worker idle or sending automated re-prompts, verify that pane content and cursor positions have remained unchanged across multiple polling cycles.
3. **Modal & Numbered Choice Classification:** Expand classifier heuristics to detect any interactive dialog (numbered selection menus, `[Y/n]` confirmations, trust dialogs) as `pending: true` with a dedicated dialog category (e.g. `dialog_type: "survey" | "file_access"`), enabling non-command prompts to be surfaced or dismissed without requiring standard security escalation review.

---

### 2. Handoff Contract Gating, Clock Skew & Evidence Loss
*(Consolidates Findings #9, #12, #14, #24, #25)*

#### Problem Description
Completion validation is brittle and all-or-nothing. Valid work verified across multiple test cycles is discarded due to contract omission in task briefs, minor JSON formatting errors, clock jitter, or fixed character thresholds, with no recovery path other than costly agent restarts:
- **Omitted brief contract instructions (#14):** `complete` requires a `FLEET_HANDOFF: {json}` marker, but briefs generated from task trackers do not automatically include the requirement. Workers execute tasks cleanly, output standard summaries, and fail gates, causing discards and restarts.
- **Microsecond clock skew rejections (#24):** `parse_handoff_file` rejected handoff files written **53 ms** before the task timestamp (`stat.st_mtime < task_time - 0.05`) due to inter-process scheduling jitter, misdiagnosing sub-second clock differences as stale files.
- **Loss of raw verification evidence on schema slip (#9):** Formatting slips (e.g., `handoff.verification must be an array`) caused `complete` to refuse the handoff immediately, losing multi-page pane verification logs unless manually recovered.
- **Default character cap truncation (#12):** `complete` defaulted to a 4,000-character cap, rejecting legitimate multi-round verification matrices and forcing operators to supply `--max-chars 8000`.
- **Lack of lead-supplied handoff override (#25):** When a handoff failed purely on formatting, the only recovery was re-prompting the worker, incurring extra round trips, restarting task clocks, and triggering permission prompts on already finished workers.

#### Technical Improvements
1. **Automated Handoff Contract Injection:** Update `spawn` to automatically append the mandatory `FLEET_HANDOFF: {json}` contract schema and instructions to `--task` briefs if not already present.
2. **Tolerant Timestamp Window:** Widen the handoff file staleness threshold in `parse_handoff_file` (e.g., from 50 ms to 2.0 seconds) or compare against monotonic task turn markers to eliminate false clock-skew rejections.
3. **Evidence Persistence on Gate Failure:** When `complete` fails due to schema validation, automatically persist raw pane scrollback and extracted text blocks to the leased project directory before returning error status.
4. **Adaptive / Increased Character Limit:** Increase default handoff character limit from 4,000 to 16,000 characters to accommodate multi-round test logs and verification matrices.
5. **Lead Override Flag:** Support `fleet.py complete --agent <name> --handoff @<path-to-file>` allowing the lead herder to supply corrected structured handoffs, recording the override explicitly in the audit trail.

---

## P1 (High) — Autonomous Flow & Task Invariants

### 3. Permission Escalations, Allowlist Matching & Auto-Herd Stalls
*(Consolidates Findings #1, #2, #3, #4, #11, #22)*

#### Problem Description
Permission allowlists and approval workflows fail under real-world multi-agent execution, creating deadlocks and breaking autonomous supervisors:
- **Token boundary matching vs. directory prefixes (#1):** `agy_permissions.py sync --prefix 'python3 /abs/path/to/bin/'` failed to match `python3 /abs/path/to/bin/planner-state` because token matching did not expand trailing slashes or globs.
- **Static permissions in running sessions (#2):** In-flight AGY sessions do not pick up dynamic allowlist updates without a full session `reset` and handoff.
- **One-shot approval deadlocks on worker scratch scripts (#3):** Reviewed approvals are valid once; when a worker re-ran its own generated test script (`scripts/probe_jira.py`), the identical digest was blocked on re-execution.
- **`auto_herd.sh` default-deny stall (#4):** `auto_herd.sh` called bare `herd.sh approve` without reviewed metadata, exiting immediately on the first default-deny escalation on greenfield repos.
- **Denial submission failure on ineligible rules (#11):** When a worker requested an ineligible command (`rm -rf ...`), `approve --choice 4` was rejected by policy checks, preventing the herder from sending a rejection through the approval channel and forcing raw prompt workarounds.
- **Hardcoded repo entrypoint assumptions (#22):** Security allowlists assumed fixed entrypoint patterns (`npm run test:scripts`, `bash .../tests/*.sh`), requiring repositories with alternative test harnesses to add compatibility aliases.

#### Technical Improvements
1. **Allowlist Prefix Expansion & Globbing:** Update `agy_permissions.py sync` to expand directory prefixes and glob patterns into discrete executable token entries at sync time.
2. **Pre-Spawn Permission Compilation:** Compile and sync all expected skill and wave commands prior to worker `spawn`.
3. **Workspace Scratch-Script Allowlisting:** Add standard workspace scratch directories (e.g. `python3 <leased_dir>/scripts/*.py`) to spawn-time allowlists so worker-generated scripts do not deadlock on second runs.
4. **Pre-Reviewed Wave Command Families:** Allow herders to declare explicit, wave-scoped command families in fleet state, enabling `auto_herd.sh` to auto-approve matching reviewed commands.
5. **Policy Denial Routing:** Allow `approve --choice <N>` to execute rejection choices even on ineligible rules, ensuring clean policy denials are submitted without raw prompt injection.
6. **Explicit Test Entrypoint Declarations:** Document required allowlist aliases or allow project metadata to specify custom test entrypoints up front.

---

### 4. Task Initialization, Naming & Scheduler Invariants
*(Consolidates Findings #16, #17, #18, #19)*

#### Problem Description
Invariants between task decomposition, scheduling, and execution are not validated up front, causing late failures and orphaned state:
- **Silent spawn of empty tasks (#16):** `spawn` accepted `--task ""` without error, returning a valid pane and holding leases while the worker sat idle with no instructions.
- **Unvalidated bead-to-agent naming in project mode (#17):** `spawn --name bead-e15-t1` for bead `lxe15-e15-t1-vo2` succeeded without verifying existence against the graph, silently breaking subsequent automated bead closures.
- **32-character agent name constraint (#18):** Herdr's 32-character limit on agent names imposes an unvalidated 27-character cap on bead IDs (`bead-<id>`). Long descriptive slugs failed late at `agent start` after graph creation.
- **Inline context errors returned as ready candidates (#19):** When a bead had context errors (e.g. `cwd escapes repository root`), `scheduler.py ready` returned the bead as a normal candidate with inline error JSON, resulting in missing leases and un-executable tasks.

#### Technical Improvements
1. **Strict Brief Validation:** Reject empty or whitespace-only `--task` strings at `spawn` time with immediate nonzero exit.
2. **Bead Name Verification:** When `--project` is passed, validate that `--name` corresponds directly to an existing bead in the graph before acquiring leases or spawning panes.
3. **Decomposition Name Budget Enforcement:** Enforce the 27-character bead ID budget in `beads-task-scheduler` at `create` / decomposition time.
4. **Separate Error Filtering in Scheduler:** Update `scheduler.py ready` to exclude beads with context resolution errors from dispatchable candidate lists, surfacing them in a dedicated blocked/error output.

---

## P2 (Medium) — Test Integrity & Herder Ergonomics

### 5. Test Baseline Awareness & Known-Red Suite Support
*(Consolidates Findings #13, #21, #23)*

#### Problem Description
Lack of baseline propagation causes redundant execution, false regression diagnoses, and broken completion gates:
- **Redundant baseline runs & false timeouts (#21):** Workers in existing repos spent their first 3 minutes executing full test suites to measure baselines, consuming turns and triggering false idle timeouts in supervisors.
- **Completion gate failure on known-red suites (#23):** Verification gates enforced `exit_code == 0` by default. In repos with known pre-existing test failures, every bead was refused unless workers discovered `expected_exit`.
- **Environment-dependent verification claims (#13):** Workers validated tests within specific local environments that masked regressions present in standard clean environments.

#### Technical Improvements
1. **Dispatch-Time Baseline Injection:** Support injecting known test baselines (pass/fail counts, pre-existing failures) into the task brief so workers do not re-run full suites prior to starting work.
2. **Project-Level `expected_exit` Configuration:** Allow `agent_context.herder.verification` to define expected exit codes and expected failure counts at the project/bead level.
3. **Herder-Side Verification Reproduction:** Require the lead herder / wave loop to independently re-run verification commands across defined target environments before bead closure.

---

### 6. Herder Ergonomics, Steering & Communication Channels
*(Consolidates Findings #5, #6, #8, #20)*

#### Problem Description
Herder interactions suffer from token-expensive polling patterns, missing worker-to-herder communication channels, and CLI flag strictness:
- **Context consumption from status poll loops (#5):** Guidance emphasized `status` polling loops (`until status ... sleep`), consuming excessive agent context turns instead of using event-driven `herd.sh watch`.
- **Lack of structured question channel (#6):** Workers instructed to "ask, do not guess" had no structured mechanism to surface questions, leading to silent assumptions.
- **Refusal of same-domain steering prompts (#8):** `prompt` refused follow-up steering instructions ("do not read .env, use CLI") without explicit `--related` flags.
- **CLI global argument ordering traps (#20):** `fleet.py park --agent X --session Y` failed because `--session` must precede subcommands, producing confusing errors during cross-session orchestration.

#### Technical Improvements
1. **Document `watch` as Default Wait Primitive:** Update documentation and wave loop scripts to use `herd.sh watch` for event-driven wakeups, reserving `status` for static inspection.
2. **Structured Question Protocol (`QUESTIONS.md`):** Establish a convention-based question file under the leased scope (`QUESTIONS.md`) checked by the wave loop when workers settle.
3. **Clarify Steering Flags:** Document `--related` as the standard path for in-flight task steering, reserving bare `prompt` for new dispatches.
4. **Flexible CLI Argument Parsing:** Update `fleet.py` argument parsing to accept global flags (`--session`, `--allow-cross-session`) both before and after subcommands.

