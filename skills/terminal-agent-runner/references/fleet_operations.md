# Fleet operations

Read this file for permissions, monitoring, context lifecycle, classification, escalation, and
failure handling after tasks have been selected.

## Session and pane safety

- Inherit `HERDR_SOCKET_PATH`; reject accidental cross-session targeting.
- Address agents by unique name. Resolve a pane only immediately before key operations.
- Use one tab per TUI agent so approvals remain visible.
- Resolve the Herdr workspace from the herding pane and pass its ID to both tab creation and
  agent start; task `--cwd` changes the shell directory but does not select a workspace.
- Seed the first prompt through argv. Codex/Claude/OMP accept positional prompts; AGY uses `-i`.
- Use Codex `--no-alt-screen` so Herdr can read scrollback.

## Approval modes

| Mode | Codex | Claude | AGY | Purpose |
|---|---|---|---|---|
| `auto` | `--approve-for-me` | `--permission-mode auto` | `--mode accept-edits` | Scoped leased implementation |
| `edit` | workspace-write + on-request | `acceptEdits` | `--mode accept-edits` | Accept edits, retain more command prompts |
| `manual` | workspace-write + untrusted | `manual` | native | Sensitive work |
| `plan` | read-only + never prompt | `plan` | `--mode plan` | Analysis only |
| `default` | workspace-write + untrusted | `manual` | native | Safe wrapper default; native only for AGY/OMP |

AGY `accept-edits` auto-accepts file edits only — shell commands still prompt, and no
non-bypass AGY flag changes that, so `auto` and `edit` map to the same thing. An AGY pane
therefore needs wrapper `approve` (or a widened AGY allowlist) to run commands unattended.
OMP has no verified mapping and keeps its native defaults.

Never use bypass/yolo flags. Auto review reduces ceremony; it does not replace resource scoping,
the CLI sandbox, or verification.

`--cli` accepts only `codex`, `claude`, `agy`, or `omp`; it never accepts a command string or
flags. For `read_only`, the wrapper ignores broader modes and enforces Codex read-only/never,
Claude plan mode, or AGY plan mode (`--mode plan`). It refuses read-only OMP work until an enforced mapping is verified.

## Monitor and act

```bash
fleet.py status --json
fleet.py watch --until actionable --timeout 60
fleet.py classify --agent A
fleet.py approve --agent A
fleet.py park --agent A --cancel
fleet.py escalations
fleet.py reconcile --group G
```

`watch` polls inside one helper call, renews leases, and emits one compact snapshot on a change,
block, completion, health failure, lost lease, trust prompt, or timeout. A `done` pane with a
trust prompt is actionable and is never terminal success.

Classify pending prompts with `risk_policy.json`: deny wins, unmatched escalates, and assigned
task intent taints later prompts. The parser checks normalized and de-wrapped TUI text. Wrapper
`approve` has no force override and accepts only complete, simple read-only commands; tests,
builds, installs, compound shell, redirection, and unmatched prompts escalate.

AGY wraps its shell prompt in a `Command Requesting permission for:` preamble, which the allow
rules tolerate. Do not reach for an AGY `PreToolUse` hook instead: systematic 5-probe empirical testing
against AGY 1.1.14 (with logged executable script invocations isolating both headless print mode and
interactive Herdr pane sessions) confirms that a `PreToolUse` hook returning `{"decision": "allow"}`
(with or without `permissionOverrides`) does not bypass AGY's internal tool permission manager.
Un-allowlisted commands still trigger confirmation prompts in interactive mode (`--mode accept-edits`)
and auto-deny in headless print mode (`-p`). Wrapper `approve` and command-prefix entries in the AGY
`permissions.allow` settings array are the mechanisms that actually work. Rather than being configured
by hand, standing authorizations are declared under `agy_allow` in `risk_policy.json` and synchronized
to AGY's settings file with automated backup and rollback; see [agy_permissions.md](agy_permissions.md)
for the operating procedure and safety invariants.

## Context lifecycle

Every assigned task requests `FLEET_HANDOFF: {json}` with:

- `status`: `completed` or `blocked`
- arrays: `decisions`, `changed_files`, `blockers`, and `next_inputs`
- `verification`: array of `{command, exit_code}` objects, each optionally carrying
  `expected_exit` (default `0`) for a check whose passing case is a nonzero exit — a leak-check
  `grep` exits 1 when it finds nothing. The comparison stays an equality check, so a declared
  expectation is evidence to read, not a way to wave a failure through.

`complete` refuses missing/malformed handoffs, empty verification, exits that do not match
expectations, missing configured commands, and unhealthy/trust-prompt panes. It retains
assignment state and leases on refusal. Use `prompt --related` only for the same task domain.
Use `reset` with a minimal handoff for unrelated work.
The parser selects the last line-level handoff marker, allowing failed or old attempts earlier in
pane history. Text after that final JSON object no longer refuses completion — CLIs sign off and
redraw prompts constantly — but any of it that is not a recognised separator is echoed as a
`warning:` on stderr, because prose after the handoff can contradict its evidence. A newer
handoff marker still wins and remains a hard failure when it is malformed.

Keep only the task ownership/leases, decisions, handoffs, verification, and unresolved
escalations in the herder context.

Lifecycle mutations are ordered for rollback: `prompt` records a task only after verified submit;
`park` marks state under the session lock and restores it if lease release fails; `reset`
prevalidates and acquires the new scope before closing the old pane, retaining old assignment state
and leases if launch fails. A closed TUI context itself cannot be reconstructed, so reset failures
still require operator recovery even though ownership remains safe.

## Herder Context Hygiene & Subagent Delegation

### Design Rationale: Non-Restrictive Orchestrator Pattern

The herder functions strictly as a high-level orchestrator, supervisor, and arbitrator—never as a direct task worker, code editor, or deep investigator.

Attempting to enforce this separation via **hard tool interceptors** (such as blocking shell commands or file reads at the hook/pre-tool level) is harmful and restrictive:
- **False positives:** Interceptors inadvertently block safe, necessary diagnostic checks (e.g. checking process status, querying sockets, or inspecting short configuration files).
- **Broken situational awareness:** Rigid tool blocks prevent the herder from diagnosing fleet stalls, escalations, or abnormal environment failures.
- **Fragile error loops:** When a hard hook denies a command, agents frequently get trapped in repeated retry loops.

Instead, apply **behavioral orchestrator discipline**: the herder retains full tool capability but adheres to strict delegation patterns and output bounds.

### Subagent Delegation Architecture & Pre-Warmed Triage Sidecar

Offload all deep explorations and side-tracks away from the herder's context window:

- **Pre-Warmed Triage Sidecar Pattern:** Spawn `triage-sidecar` (read-only/plan mode) during `init` / bootstrap so triage capacity is immediately available without cold-start spawn delay when investigating failed verification, escalations, or unexpected errors.
- **Research & Investigation Subagents:** Delegate complex debugging, root-cause analysis of test failures, large codebase searches, and architecture reviews to ephemeral subagents (e.g. read-only research subagents).
- **Fleet Scratch Workers:** When reproduction or experimentation requires active shell execution or tool access in a live environment, spawn a temporary scratch worker in the fleet (`herd.sh spawn`) rather than executing experimental commands in the herder process.

### Mandatory IF-THEN Context Invariant

Strictly adhere to the context protection invariant:
- **IF** error diagnosis or anomaly investigation requires >1 command OR inspecting >30 lines of logs -> **MUST** delegate to `triage-sidecar` or an ephemeral subagent.
- **2-Sentence Distillation Contract (Root cause + Fix target):** Subagents and sidecars must return only:
  1. **Root cause:** Concise statement of what failed and why.
  2. **Fix target:** Exact files, lines, or configuration to update.
- **Zero Raw Dumps:** Explicitly forbid returning full file contents, raw stack traces, verbose compiler output, or unbounded AST dumps.
- **Strictly Forbidden Unbounded Commands:** Never run unbounded `cat`, `grep`, `diff`, or `git status` directly in the herder context.

### Standard Command Output Bounds

When the herder must run diagnostic or verification commands directly, always bound the output:
- `git status -s` instead of unbounded `git status`
- `diff -u ... | head -n 30` or `diff -q` instead of unbounded `diff`
- `grep -m 5 <pattern> <file>` to cap matching lines
- `pytest -q --tb=short --maxfail=1` instead of unbounded test runner execution
- `head -n 20` or `tail -n 30` when inspecting output logs
- Pass `--limit <N>` to list or search commands

### Multi-Wave Context Longevity

Combining these disciplines preserves herder token headroom across extended sessions:
- **Zero-turn supervision:** `auto_herd.sh` runs asynchronously in the background, yielding execution turns until an actionable event occurs.
- **DAG-scoped context:** Task details are maintained in the `beads-task-scheduler` DAG (`.beads/`), so the herder only holds the active wave's immediate task briefs and handoff evidence.
- **Distilled side-tracks:** Subagents resolve anomalies out-of-band and return minimal summaries.

## Failure semantics

- Quota/auth/context-full: mark the CLI unusable and reassign truthfully.
- Human decision: park, release leases, continue independent work, report decisions in one batch.
- Dead worker: release leases, reconcile ghosts, and spawn or reset as needed.
- Test unavailable or pre-existing failure: report it as unverified or baseline failure, never pass.

Use `fleet.py <command> --help` for one command's advanced flags. Read
[herdr_cheatsheet.md](herdr_cheatsheet.md) only for raw Herdr behavior or terminal-level debugging.
