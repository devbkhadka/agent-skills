---
name: terminal-agent-runner
description: Spawn, monitor, approve, hand off, reset, and park lock-safe CLI coding agents (Codex, Claude, AGY, OMP) running in Herdr terminal panes without a task database. Use for interactive multi-agent coding fleets, parallel terminal worker orchestration, safe TUI permission approvals, and resource lease isolation across concurrent tasks.
---

# Terminal Agent Runner

Act as the lead runner. Use `fleet.py` for Herdr actions and `resource_pool.py` for runtime leases.

Verified core surfaces: Herdr 0.8.0, Codex 0.147.0, and Claude 2.1.220. Inspect local `--help`
before relying on version-sensitive advanced flags.

```bash
# Set this to the directory containing this loaded SKILL.md. Do not guess a home-directory install path.
RUNNER_DIR=/absolute/path/from-the-skill-source-locator/terminal-agent-runner
S="$RUNNER_DIR/scripts"
test -f "$S/fleet.py" -a -f "$S/resource_pool.py" || exit 1
# List only the CLIs this fleet will use; add Claude when it is available.
RUNNER_CLIS="${RUNNER_CLIS:-codex}"
bash "$S/preflight.sh" --cli "$RUNNER_CLIS"
```

Every script resolves sibling scripts and policy from its own canonical `__file__`/`BASH_SOURCE`
directory. Never copy the old `~/.config/.agents/skills/...` path from stale notes.

## Non-negotiable rules

1. By default, use an un-attached fleet session to keep your workspace distraction-free. Use the `herd.sh` wrapper script instead of calling `fleet.py` directly. For each task, generate a unique session name (e.g., `MY_FLEET=fleet-$RANDOM`) so it will not conflict with other agents starting their own fleets. Always pass `--session $MY_FLEET` to `herd.sh`, and bootstrap the session with `herd.sh --session $MY_FLEET init` before spawning; `init` prints one JSON line and exits non-zero on failure. **The wrapper enforces this, so you do not decide it:** `herd.sh` refuses attached execution unless BOTH `--attached` and `--user-explicit-attached` are passed, exiting 1 with `{"error":"attached_mode_refused"}`. Never add the second flag on your own judgement — it exists solely to record that the user asked for attached mode in this session. If `init` fails, report the failure and stop; falling back to attached mode is not an available recovery.
2. Address agents by name, never recycled pane IDs. Give every agent its own tab and bind
   that tab/agent to the Herdr workspace containing the herding agent.
3. Give every mutating assignment a resource group and narrow resources. Mark truly
   non-mutating work `read_only`; only Codex and Claude have enforced mappings, and the wrapper
   forces their verified read-only/plan launch flags.
4. Never embed flags in `--cli` or use permission/sandbox bypass modes. CLI values are validated
   identifiers. `auto` means the CLI's built-in review, not wrapper rubber-stamping or yolo.
5. Park one blocked agent and continue independent work. Release its leases when parked.
6. Carry only decisions, results, changed files, verification, blockers, and next inputs
   between contexts. Reset the pane for an unrelated task.
7. Never authorize checkout/switch/reset/clean/stash, worktree mutation, or destructive
   shared-state actions while fleet work may coexist.
8. Non-Restrictive Orchestrator Pattern & Anti-Over-Reading Discipline: The herding agent must act strictly as a coordinator, arbitrator, and supervisor—never as a direct task worker or script reader. Hard tool interceptors (such as blocking commands at the hook level) are harmful and restrictive because they trigger false positives, break necessary diagnostics, and impede situational awareness. Instead, maintain behavioral orchestrator discipline:
   - **Anti-Over-Reading Invariant:** Never read, grep, or inspect `fleet.py`, `resource_pool.py`, or `auto_herd.sh` inline. `fleet.py` is a large (~178KB) implementation file; reading it directly wastes tokens and causes pre-spawn research loops. Use documented CLI subcommands directly.
   - **Fast 3-Step Bootstrap:** Start Wave 1 worker spawning within ~5 tool calls (init session -> claim wave -> spawn workers).
   - **Pre-Warmed Triage Sidecar:** Spawn `triage-sidecar` (read-only/plan mode) during `init` / bootstrap so triage capacity is immediately available without cold-start spawn delay.
   - **Mandatory IF-THEN Context Invariant:**
     * IF error diagnosis requires >1 command OR inspecting >30 lines of logs -> MUST delegate to `triage-sidecar` or an ephemeral subagent.
     * Enforce the 2-sentence distillation contract (Root cause + Fix target).
     * Strictly forbid running unbounded `cat`, `grep`, `diff`, or `git status` directly in the herder context.
   - **Standard Command Output Bounds:** When running direct diagnostics, always enforce bounds: `git status -s` (never bare `git status`), `diff -u ... | head -n 30` or `diff -q` (never unbounded `diff`), `grep -m 5` (never unbounded `grep`), `pytest -q --tb=short --maxfail=1` (never unbounded test suites), `head -n 20`, `tail -n 30`.
   - If the user has given a limit on the number of fleet workers or subagents, ask for permission before spawning additional ones. This keeps the herding agent free to resolve questions and permission prompts, allowing it to run for long periods without polluting its context.

## Core workflow

First, establish your unique session name and bootstrap it. `init` starts the headless daemon,
provisions a workspace, and prints one JSON line; a non-zero exit means the un-attached path is
unavailable, which you report rather than work around:

```bash
MY_FLEET="fleet-$RANDOM"
bash "$S/herd.sh" --session "$MY_FLEET" init
# {"session":"fleet-1234","status":"ready","workspace_id":"w1","socket_path":"..."}

# Pre-warm triage sidecar for offloaded error diagnosis:
bash "$S/herd.sh" --session "$MY_FLEET" spawn --name triage-sidecar --cli codex \
  --read-only --approval-mode plan --cwd /repo \
  --task "Pre-warmed triage sidecar for offloaded error investigation, stack trace analysis, and root-cause distillation"
```

Never attach to a named fleet session with a raw `herdr` client command. Use
`herd.sh --session "$MY_FLEET" attach` instead: it refuses to restart a stopped session that
still records managed workers. Use `--recover-session` only after reviewing the loss; it starts
the server and reconciles missing workers before attaching. Session names are unconstrained in
case, length, and charset.

Use `spawn` to launch agents with scoped resource leases:

```bash
bash "$S/herd.sh" --session "$MY_FLEET" spawn --name c1 --cli codex --task "Implement feature X" \
  --group repo:/repo --resource file:src/x.ts --approval-mode auto --cwd /repo
bash "$S/herd.sh" --session "$MY_FLEET" spawn --name c2 --cli codex --task "Inspect architecture" \
  --read-only --approval-mode plan --cwd /repo
```

Approval modes are `auto` (default for mutating Codex/Claude tasks), `edit`, `manual`, `plan`,
and `default`. AGY maps `auto`/`edit` to `--mode accept-edits` (file edits only; shell commands
still prompt) and `plan` to `--mode plan`; OMP keeps its native defaults. Read-only tasks force
`plan` (enforced via Codex read-only sandbox, Claude plan mode, and AGY `--mode plan`), while OMP read-only
dispatch is refused because no enforced mapping is verified.
Change a running agent's mode only by `reset`.

The optional `--model` option specifies the model for the worker CLI session, passed through
to the launched CLI's own model option. Omitting `--model` changes nothing, launching with no
model option at all. Only AGY validates the model value against its `agy models` enumeration
(accepting both IDs like `gemini-3.7-flash-high` and display names like `Gemini 3.7 Flash (High)`);
for Codex, Claude, and OMP, the value passes through directly for the CLI itself to reject.

The optional `--profile <name>` option (on `spawn` and `reset`) launches the worker under one
account, such as `work` or `personal`, by setting that account's environment on the worker's tab. This is
the same isolation a shell alias like `codex-work` gives, but aliases never reach Herdr panes.
Profiles live in one file, `~/.config/.agents/profiles.json` (override with
`AGENT_HERDER_PROFILES_FILE`). Any number of profiles may be defined, and `default` is the single place to
pick the account used when `--profile` is omitted:

```json
{
  "default": "work",
  "profiles": {
    "work":     {"codex":  {"CODEX_HOME": "~/.codex-work"},
                 "agy":    {"AGY_ACCOUNT_HOME": "~/.agy-accounts/work"},
                 "claude": {"CLAUDE_CONFIG_DIR": "~/.claude-work"}},
    "personal": {"codex":  {"CODEX_HOME": "~/.codex-personal"}}
  }
}
```

Resolution order is `--profile`, then `default`, then the plain CLI. A default that does not
define the CLI being launched also falls back to the plain CLI, and `--profile none` forces it.
A missing file means no profiles. An unknown profile, or an explicit profile that does not define the CLI, is
refused before any tab or lease is created; it never silently falls back to another account.
Values expand `~` and `$VAR`; `HERDR_*` names are refused. The resolved profile is reported in the
spawn result and recorded with the assignment, and `reset` keeps it unless `--profile` is
given.

AGY has no account switch of its own: it keeps one login in the macOS Keychain and ignores
`ANTIGRAVITY_APP_DATA_DIR`. An AGY profile therefore sets `AGY_ACCOUNT_HOME`, a folder that holds
that account's `.gemini/antigravity-cli/antigravity-oauth-token`. The worker's tab routes `agy`
through `scripts/agy_profile/bin/agy`, which gives only agy that folder as `HOME`. Unable to reach
the Keychain, agy signs in from the folder's token file. Every shell agy starts reads
`agy_profile/zdotdir/.zshenv` (zsh) or `agy_profile/bash_env` (bash), which restore the real `HOME`,
so the agent's commands see the real `~/.ssh`, `~/.gitconfig` and `PATH`. The wrapper links
`config/` (MCP servers, projects), `skills/`, `GEMINI.md` and `antigravity-cli/mcp/` from the real
`~/.gemini` into an account when the account has none of its own. They are linked as folders
because agy saves by renaming over a file, which would replace a file link with a local copy.
`antigravity-cli/settings.json` stays per account and receives the runner's AGY allowlist. A
profile whose account has no token file is refused before any tab is created. Log in once by
running the wrapper directly: `AGY_ACCOUNT_HOME=~/.agy-accounts/<name> scripts/agy_profile/bin/agy`.

Drive the fleet deterministically using the automated lifecycle manager:

```bash
# Watches, auto-approves, and completes agents. Keeps driving rather than exiting on the first
# ambiguous state. Pass --project to close the matching bead the moment `complete` succeeds.
bash "$S/auto_herd.sh" --session "$MY_FLEET" --project /repo
```

Exit codes: `0` fleet empty and all work completed, `1` every remaining agent needs human
review, `2` usage or operational error, `4` empty fleet on the first poll (failed spawn or wrong
session). A `1` is where you read the reported blockers and act; it is not a crash.

A `working` agent is declared stalled only after its pane has not changed for
`--max-idle-seconds` (alias `--budget`, env `AGENT_HERDER_MAX_IDLE_SECONDS`, default 600) — time
since the last pane change, not a poll count. A pane showing a running foreground command
(spinner, AGY `○ Running (...)`/`N task(s)`, Claude `✶ Verb… (Ns`) is never stalled; raise the
budget for work that is legitimately quiet. `AGENT_HERDER_MAX_WORKING_POLLS` is no longer used.

`status` will not report an agent `done` or `idle` from a single sample — a state must hold
across `--settle-samples` consecutive polls (default 2), and empty or partially-rendered pane
frames never count as settled. Do not treat a single status row as a completion signal.
A pane showing foreground execution is `working` even when Herdr reports done/idle or an
earlier `FLEET_HANDOFF` is still on screen; a handoff the pane already showed when `prompt`/`reject`
sent the current task is from the previous turn and never counts as completion (a handoff file
written for this turn still does). A worker whose handoff says `status` other than `completed`,
or lists `blockers`, shows as `handoff-blocked` with its first blocker under PENDING — read it and
decide; `auto_herd.sh` sets it aside the same way.

Completion (`complete`) ends the agent: it requires structured evidence, records the handoff, releases all resource leases, and closes the agent's pane. The assignment is kept with `lifecycle: "completed"` (task, cwd and `bead_id` intact); `completed` and `dead` are terminal, so `prompt` and `park` refuse them and `status` reports a pane that outlived `complete` as `completed`. Follow-up work needs a new `spawn` rather than re-prompting the completed worker. Re-prompting a still-live agent remains supported for related follow-up work; a failed `prompt` leaves the recorded task and the worker's handoff files untouched.

Closing a pane (`complete`, `reset`) hangs up its terminal and kills every process the worker
started from it, including background servers. Both therefore refuse (exit 2, listing pid and
command, locks and state retained) while the pane's shell still has live processes outside the
agent CLI's own process group; stop them, or pass `--allow-kill-children` to close anyway. When
Herdr gives no shell pid the check is skipped with a stderr note. Long-lived services a later gate
needs (fixtures, dev servers) must be started detached (`nohup setsid <cmd> &`, or `nohup <cmd> &
disown` where `setsid` is missing) or by the lead — never left owned by a worker pane.

`spawn` fails, and closes the new pane, only when the CLI never becomes ready or opens a dialog
policy will not clear. Before closing, it prints the visible pane to stderr and writes a
`spawn_failed` event (with `pane_text`); the error names the readiness predicate that failed. When
the initial task was submitted but its delivery could not be confirmed from the pane (a first
command that blocks quietly shows no marker), spawn succeeds with `"task_confirmed": false` and a
stderr warning, keeping the pane and leases: check `read`/`status` before re-prompting with
`prompt --related`.

The handoff must be valid JSON within `--max-chars` (default 16000) or `complete` is refused and
the agent keeps its leases; the refusal names what was measured (handoff file, pane, or override).
`--lines` does not shrink it — the cap applies to the agent's own
emitted handoff block, not the pane scrape. A lead-supplied `--handoff <json|@file>` skips the pane
entirely and only the override is measured. Tell workers the character budget in their task text;
in practice a worker reporting real verification output will exceed 4000 unless told not to. When
a re-prompt fails to shrink it, raising `--max-chars` preserves the evidence, whereas
`park --cancel` frees the leases but discards it.

If an agent escalates a prompt requiring human review, inspect its pending prompt and digest with `classify` or `status`, and authorize the prompt via the reviewed-approval path:

```bash
# Authorize an eligible escalation after human review (bound to the exact prompt digest):
bash "$S/herd.sh" --session "$MY_FLEET" approve --agent c1 --reviewed-by dev --reviewed-subject <digest>
```

A reviewed approval is one-shot and strictly bound to the exact prompt that was reviewed (matched by subject text or digest). It is never a durable exemption and never widens policy for future commands. Ineligible categories (all content deny rules: deletion, privilege, git-destructive operations, remote execution, bypass flags, secrets, etc.) cannot be reviewed at all; attempting to waive an ineligible rule is refused. Only positional syntax rules (`shell-composition`, `write-output-option`) and `default-deny` are eligible for human review. Answering a dialog with a raw keystroke (e.g. sending raw un-reviewed keys to bypass classification) is still never acceptable. Reviewed approvals are persisted in fleet state and surfaced distinctly in status and handoffs.

Because the grant is one-shot and digest-bound, unprompted replaying of an already consumed grant is prevented. If a worker legitimately repeats a command later in its task (such as re-running a verification command or test suite after an edit), pass `--regrant` to explicitly mint a fresh reviewed grant for that prompt subject:

```bash
# Re-authorize repeated execution of an identical prompt subject after prior grant consumption:
bash "$S/herd.sh" --session "$MY_FLEET" approve --agent c1 --reviewed-by dev --reviewed-subject <digest> --regrant
```

If a pending operation or permission should be denied, use `reject` to dismiss the dialog with `Esc` and either instruct the worker on what alternative path to take, or halt/park it. `reject` and `cancel` send `Esc` only when the pane shows a pending dialog: on a working pane they refuse (exit 2), because `Esc` would abort the turn — redirect a working agent with `prompt --related` instead, or pass `--interrupt` to abort it deliberately. `reject` exits 1 when its continuation prompt cannot be verified; `reject --prompt` records the prompt as a related follow-up on the task, like `prompt`.

```bash
# Reject permission and instruct the worker on how to continue without the action:
bash "$S/herd.sh" --session "$MY_FLEET" reject --agent c1 --prompt "Do not delete files directly; use git rm or stage changes instead."

# Reject permission and park the agent immediately (releasing its resource leases):
bash "$S/herd.sh" --session "$MY_FLEET" reject --agent c1 --halt --reason "Disallowed deletion command"
```

If an agent becomes hopelessly blocked, you can escalate and free its locks manually. `park`
refuses (exit 2, `nothing to park`) an agent with no assignment or a `completed`/`dead` one.
`park --cancel` sends `Esc` only to a pending dialog, never when the worker has written a handoff
file or printed `FLEET_HANDOFF`; on a working pane without a dialog it parks without `Esc` unless
`--interrupt` is also given (the output reports `esc_sent`/`esc_skipped`). Parking with no dialog
and no `--reason` records `parked by lead (no pending dialog)`. A parked pane may show
`Interrupted`: that is the CLI's rendering of the `Esc` sent to dismiss a dialog (or of
`--interrupt`), not a sign the worker was killed:

```bash
bash "$S/herd.sh" --session "$MY_FLEET" park --agent c1 --cancel             # escalate blocked worker & free locks
bash "$S/herd.sh" --session "$MY_FLEET" reset --agent c1 --cli codex \
  --task "Next task" --handoff "Facts from prior task" \
  --group repo:/repo --resource file:src/y.ts              # fresh context with structured handoff
```

When a worker is parked, `plan` says what is still runnable so the rest of the fleet keeps
moving. It reads a JSON task list — id, deps, resources, read_only — and needs no task
database; use `beads-task-scheduler` instead once the work lives in a Beads graph.

```bash
python3 "$S/fleet.py" plan --file tasks.json --done c1-task --blocked c2-task
```

## Teardown & Cleanup

To check resource lease status or reclaim locks from crashed agents:

```bash
# Check resource pool status (note: resource_pool.py status prints JSON by default; it has no --json flag):
python3 "$S/resource_pool.py" status --group repo:/repo

# Reclaim leases from crashed or vanished agents across all active resource groups (or pass --group for a specific group):
bash "$S/herd.sh" --session "$MY_FLEET" reconcile
```

When the fleet work is completely finished, safely destroy the detached session and any lingering resource locks:

```bash
bash "$S/cleanup_fleet.sh" --session "$MY_FLEET"
```

## Load detail only when needed

- **Resource naming, capacities, leases, throttling, reconciliation:**
  [resource_leases.md](references/resource_leases.md)
- **Monitoring, context lifecycle, permissions, classification, escalation:**
  [fleet_operations.md](references/fleet_operations.md)
- **Which CLI commands are core and how to discover advanced flags:**
  [cli_tools.md](references/cli_tools.md)
- **Raw Herdr 0.6.10 command surface and terminal gotchas:**
  [herdr_cheatsheet.md](references/herdr_cheatsheet.md)

Prefer the relevant command's `--help` or machine docs over loading a broad reference when
the question is only about one advanced option. Never invent a CLI flag from memory.

## Avoid Continuous Permission Prompts

To prevent the herding agent from continuously asking for permission when running safe fleet commands, register a `PreToolUse` hook in your workspace to auto-approve safe scripts (like `fleet.py`) while blocking risky commands (like `npx`).

1. **Create `.agents/scripts/auto_approve.sh` (and run `chmod +x`):**
   ```bash
   #!/bin/bash
   payload=$(cat)
   command_line=$(echo "$payload" | jq -r '.toolCall.args.CommandLine // ""')
   if echo "$command_line" | grep -q '\bnpx\b'; then
     echo '{"decision": "ask", "reason": "npx command detected, manual review required."}'
     exit 0
   fi
   if echo "$command_line" | grep -q 'fleet\.py'; then
     echo '{"decision": "allow", "reason": "Auto-approved fleet.py script."}'
     exit 0
   fi
   echo '{"decision": "ask", "reason": "Requires manual review."}'
   ```

2. **Register the hook in `.agents/hooks.json`:**
   ```json
   {
     "auto-approve-safe-scripts": {
       "PreToolUse": [
         {
           "matcher": "run_command",
           "hooks": [
             {
               "type": "command",
               "command": "./.agents/scripts/auto_approve.sh"
             }
           ]
         }
       ]
     }
   }
   ```
