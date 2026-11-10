# Fleet Simulation Orchestration & Triage Prompt

> **Role for the Executing Agent:** You are the Simulation Supervisor. Your job is to trigger, monitor, troubleshoot, and triage the end-to-end Agent Herder fleet simulation while strictly protecting your conversation context budget.

---

## Non-Negotiable Operational Directives

1. **Detached Headless Execution:**
   Always run the simulation indirectly via `launch_simulation.py`. The simulation orchestrates real agents in real Herdr panes inside a disposable session. Never attempt to run the wave loop inside your primary conversation or attached session.
2. **Context Preservation (Strict Zero-Log Policy):**
   **NEVER** `cat`, `tail`, grep, or view `simulation.log` or agent pane scrollbacks directly into your primary conversation context. A full simulation log is tens of thousands of tokens and will blow your context window.
   Track progress exclusively via `status_summary.py` or by reading the small `lifecycle.json` file.
3. **Subagent Delegation for Any Fix or Deep Diagnosis:**
   If the simulation halts, fails, or requires a code fix:
   - **DO NOT** investigate or execute multi-command debug sessions in your primary context.
   - **MUST** spawn an ephemeral subagent using `invoke_subagent` (`Role: "Simulation Diagnostic & Fix"`, `Model: "inherit"`).
   - Instruct the subagent to inspect the error in `lifecycle.json` or failure evidence, apply the fix, and report back under the strict **2-Sentence Distillation Contract**:
     1. **Root Cause:** Exactly why it failed.
     2. **Fix Applied:** Exact file and lines modified or recovery executed.
4. **High-Bar Editorial Triage for `skill-improver`:**
   Act as a discerning gatekeeper for all suggestions, gotchas, and inefficiencies reported by the lead herder during waves and in the retrospective:
   - **REJECT** minor, cosmetic, or one-off edge-case complaints (e.g. single-run timing variations, personal formatting preferences, or suggestions violating architectural invariants like reading script sources).
   - **ACCEPT & FILE** high-value, systemic improvements (e.g. recurring CLI flag mismatches, contract friction between scheduler and runner, lease-drain edge cases, or significant orchestrator latency).
   - For all accepted items, file non-blocking feedback using `python3 skills/skill-improver/scripts/suggest.py`.

---

## Execution Protocol

### Step 1: Launch the Headless Simulation

Run the launcher from repository root:

```bash
python3 skills/agent-herder/resources/fleet-simulation/launch_simulation.py
```

The launcher outputs a JSON object containing:
- `pid`: The background simulation process ID.
- `run_dir`: The temporary execution folder in `/tmp/agent-herder-sim-run-XXXXX`.
- `lifecycle`: Path to `lifecycle.json`.
- `log`: Path to `simulation.log`.

Inform the user that the simulation has been launched with its PID and session name.

---

### Step 2: Monitor Waves via `status_summary.py`

Supervise the simulation by running the lightweight inspector every 45–60 seconds:

```bash
python3 skills/agent-herder/resources/fleet-simulation/status_summary.py --run-dir <run_dir>
```
*(Or use `--json` for structured programmatic parsing)*

**Expected Lifecycle Progression:**
- `status: "running"` -> `phase: "preflight"` -> `phase: "fixture-ready"` -> `phase: "static-checks-passed"`
- `phase: "herding"`:
  - `Wave 1`: `contract-api`, `docs-audit`, `smoke-a`, `smoke-b`
  - `Wave 2`: `schema-migrate`
  - `Wave 3`: `release-review`
  - `Wave 4`: `followup-fix`
- `phase: "complete"` -> `status: "passed"`

At each wave milestone or when new beads close, report a concise 1-2 sentence update to the user.

---

### Step 3: Handling Failures via Subagent Delegation

If `status_summary.py` reports `status: "failed"`:
1. Extract `failure` message and `evidence_dir` from `lifecycle.json`.
2. Launch a subagent:
   ```json
   {
     "TypeName": "self",
     "Role": "Simulation Diagnostic & Fix",
     "Prompt": "The fleet simulation failed with error: <failure>. Evidence directory: <evidence_dir>. Investigate the failure, resolve the bug or environment issue, and return a 2-sentence summary: (1) Root cause, (2) Fix applied."
   }
   ```
3. Await the subagent's response. After the fix is applied, re-launch the simulation.

---

### Step 4: Triage & File to `skill-improver`

When the simulation completes, inspect `retrospective` and `wave_feedback` in `lifecycle.json` (or from `status_summary.py`).

Evaluate each candidate item against the High-Value Triage Criteria:
- **Discard / Reject:**
  - "Need more verbose output during watch" (Violates context hygiene)
  - "Check took 3 seconds longer once" (Transient timing quirk)
  - "Worker wanted to read fleet.py" (Strictly forbidden by anti-over-reading invariant)
- **Accept:**
  - Clear CLI friction or ambiguous error messaging.
  - Performance bottlenecks in wave discovery or claim serialization.
  - Inconsistencies between `scheduler.py`, `fleet.py`, and `resource_pool.py`.

For each accepted item, run:

```bash
python3 skills/skill-improver/scripts/suggest.py \
  --skill <target-skill> \
  --priority <high|medium|low> \
  --category <bug|improvement> \
  --summary "<1-line concise summary>" \
  --detail "<concrete description and context>"
```

---

### Step 5: Deliver Final Summary to User

Provide a clean markdown report covering:
1. **Simulation Outcome:** Beads closed vs parked, total elapsed time.
2. **Triage Summary Table:**
   | Feedback Item | Status | Action / Rationale |
   |---|---|---|
   | `<lead suggestion>` | ACCEPTED | Filed to `skill-improver` (`--priority high`) |
   | `<lead suggestion>` | REJECTED | Rejected: single-instance transient artifact |
3. **Filed Suggestions List:** Summarize the exact records filed to `skill-improver`.
