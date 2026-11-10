# CLI Completion and Lifecycle Hooks Reference

## Overview and Problem Statement

When the terminal agent runner supervises autonomous CLI agents in Herdr terminal panes, it needs to know reliably when an agent has finished its work or terminated abnormally. Relying solely on PTY screen-scraping or polling is fragile and prone to stalls. This document establishes, based strictly on the tools and documentation installed locally on this machine, which agent CLIs support native lifecycle hooks to execute shell notifications upon completion or failure.

> [!IMPORTANT]
> **Distinction Between Permission Bypasses and Completion Notifications:**
> This repository previously established a decisive negative result regarding AGY tool permissions: an AGY `PreToolUse` hook returning `{"decision": "allow"}` does **not** suppress interactive permission prompts or bypass headless auto-denial for un-allowlisted commands (documented in [agy_permissions.md](file:///Users/devkhadka/projects/agent-skills/skills/terminal-agent-runner/references/agy_permissions.md)).
> That negative result relates strictly to tool authorization safety gates. It does **not** preclude lifecycle hooks (such as AGY's `Stop` hook) from executing shell commands to fire push notifications when an execution loop terminates.

---

## Summary Matrix

| CLI | Installed Version | Lifecycle Hook Name | Configuration Path | Fires on Abnormal Exit | Status |
|---|---|---|---|---|---|
| **`agy`** | `1.1.14` | `Stop` | `.agents/hooks.json` (`<hook_name>.Stop`); global `~/.gemini/config/hooks.json` | **Yes** (`terminationReason`: `"error"`, `"max_steps_exceeded"`) | **Verified** (trust-exemption of the global path: Inferred) |
| **`claude`** | `2.1.226 (Claude Code)` | `StopAgent` / `SessionEnd` *(scout claim)* | `.claude/settings.json` *(scout claim)* | Unverified locally | **Unverified** |
| **`codex`** | `codex-cli 0.147.0` | `notify` (turn/session emitter) / `SessionEnd` / `Stop` *(scout claim)* | `~/.codex/config.toml` (`notify` array) or `.codex/hooks.json` *(scout claim)* | Unverified locally | **Unverified** *(Hook system unverified; `notify` array observed in live config)* |
| **`omp`** | `omp/17.3.0` | `agent_end` / `turn_end` *(scout claim)* | `.omp/` hooks *(scout claim)* | Unverified locally | **Unverified** |

---

## Findings by CLI

### 1. AGY (`agy`)

- **Installed Version:** `1.1.14` (verified via `agy --version`).
- **Hook Name:** `Stop` (flat array under top-level named hook block).
- **Configuration Path & Key:**
  - Workspace-scoped: `<workspace>/.agents/hooks.json` -> `<hook_name>.Stop`
  - Global: `~/.gemini/config/hooks.json` -> `<hook_name>.Stop`
- **Documentation Citation:** Locally installed reference at `~/.gemini/antigravity-cli/builtin/skills/agy-customizations/docs/hooks.md` (lines 78–101, 118–130, 290–320); verified changelog entries in `agy changelog` (1.0.8, 1.1.1, 1.1.11).

#### Payload and Execution Capabilities (Verified)
The `Stop` hook executes a configured shell command via `sh -c` (Unix) or `cmd /c` (Windows) with a default 30-second timeout.

**Context received on `stdin` (JSON):**
```json
{
  "executionNum": 1,
  "terminationReason": "model_stop",
  "error": "",
  "fullyIdle": true,
  "conversationId": "ec33ebf9-0cba-4100-8142-c61503f6c587",
  "workspacePaths": ["/path/to/workspace"],
  "transcriptPath": "/path/to/workspace/.gemini/antigravity-cli/transcript.jsonl",
  "artifactDirectoryPath": "/path/to/workspace/.gemini/antigravity-cli/artifacts",
  "modelName": "auto"
}
```

**Output contract (`stdout`):**
- Returning `{"decision": "continue", "reason": "..."}` blocks termination and re-enters the agent loop.
- Returning an empty JSON object `{}` or any other decision allows the agent to terminate normally.

#### Abnormal Termination Support (Verified)
The hook passes `terminationReason`, which is documented to include:
- `"model_stop"`: Normal agent completion.
- `"max_steps_exceeded"`: Step budget exhaustion.
- `"error"`: Runtime error during execution (with the `error` string populated).

Because `Stop` fires across both success and abnormal failure modes, it satisfies the requirements for a reliable supervisor notification mechanism.

#### Execution Gates and Trust
- **Workspace-Local Hooks (`.agents/hooks.json`):** Gated on workspace folder trust (`trustedWorkspaces` in `~/.gemini/antigravity-cli/settings.json`, per AGY 1.1.1 changelog). Untrusted directories will not load workspace-local hooks.
- **Global Hooks (`~/.gemini/config/hooks.json`):** This path exists and is the shared hooks config the TUI and backend both read (`agy changelog`, entry: "Fixed a bug where the `/hooks` command wrote configurations to `~/.gemini/antigravity-cli/hooks.json` instead of the shared `~/.gemini/config/hooks.json`"). Whether a global hook loads in an **untrusted** workspace is **not stated** by any locally installed document; the trust-gating changelog entry names only the workspace-local path. Treat "global hooks bypass trust" as inferred, not established — see Inferred below.

---

### 2. Claude Code (`claude`)

- **Installed Version:** `2.1.226 (Claude Code)` (verified via `claude --version`).
- **Hook Name:** Unverified locally (`StopAgent` / `SessionEnd` claimed in scout bead).
- **Configuration Path:** Unverified locally (`.claude/settings.json` claimed in scout bead).
- **Documentation Sources Checked:**
  - `claude --help`: Documents `--safe-mode` ("Start with all customizations (CLAUDE.md, skills, plugins, hooks, MCP servers...) disabled"), verifying the existence of a customization category for hooks.
  - `claude agents --help`: Documents background agent management flags, but contains no hook configuration specifications.
  - Local installation at `/opt/homebrew/Caskroom/claude-code/2.1.226/claude`: Standalone binary shipping no human-readable markdown documentation or configuration schemas.
  - User configuration directory `~/.claude/`: Contains `settings.json` and `CLAUDE.md`, but no installed schema or documentation defining hook event triggers.

#### Evaluation
While `claude --help` confirms that Claude Code includes a "hooks" concept disabled under `--safe-mode`, the exact hook names, configuration keys, stdin/stdout contracts, and abnormal exit handling cannot be verified from documentation installed on this machine.

---

### 3. Codex CLI (`codex`)

- **Installed Version:** `codex-cli 0.147.0` (verified via `codex --version`).
- **Hook Name / Mechanism:**
  - Native `notify` configuration: `notify` key in `~/.codex/config.toml` (verified present in live user config).
  - Lifecycle hooks: Unverified locally (`SessionEnd` / `Stop` claimed in scout bead).
- **Configuration Path:** `~/.codex/config.toml` (for `notify`) / `.codex/hooks.json` (scout claim).
- **Documentation Sources Checked:**
  - `codex --help`: Documents `--dangerously-bypass-hook-trust` ("Run enabled hooks without requiring persisted hook trust for this invocation"), confirming that Codex contains a hook execution subsystem with trust gating.
  - Live configuration `~/.codex/config.toml`: Contains active entry `notify = ["/Users/devkhadka/.../SkyComputerUseClient", "turn-ended"]`.
  - Local installation at `/opt/homebrew/Caskroom/codex/0.147.0/`: Contains binaries and shell completions, but no installed documentation or hook reference manual.

#### Evaluation
Codex provides a verified `notify` configuration key in `config.toml` that executes a command array upon turn completion. However, the generic lifecycle hook event names (`SessionEnd`, `Stop`), event schemas, and abnormal termination behavior claimed in the scout pass cannot be verified from installed documentation. Hook execution requires workspace trust or the `--dangerously-bypass-hook-trust` CLI flag.

---

### 4. Oh My Pi (`omp`)

- **Installed Version:** `omp/17.3.0` (verified via `omp --version`).
- **Hook Name:** Unverified locally (`agent_end` / `turn_end` claimed in scout bead).
- **Configuration Path:** Unverified locally (`.omp/` claimed in scout bead).
- **Documentation Sources Checked:**
  - `omp --help`: Documents CLI flags, profile support, subagent commands (`omp agents unpack`), and environment variables, but lists no lifecycle hook flags or settings.
  - `omp config --help`: Documents setting actions (`list`, `get`, `set`, `reset`, `path`), but provides no built-in schema for lifecycle hooks.
  - Installation directory `/opt/homebrew/Cellar/omp/17.3.0/`: Contains binary and shell completions, with no installed reference documentation.
  - User directory `~/.omp/`: Contains runtime databases and logs, but no hook schema files.

#### Evaluation
The scout pass claims regarding TypeScript/YAML lifecycle hooks (`agent_end`, `turn_end`) in OMP cannot be established from the locally installed CLI help or documentation.

---

## Verified vs. Unverified

### Verified (From installed docs, live config, or CLI help output)
- `agy --version` output is `1.1.14`.
- `claude --version` output is `2.1.226 (Claude Code)`.
- `codex --version` output is `codex-cli 0.147.0`.
- `omp --version` output is `omp/17.3.0`.
- **AGY `Stop` hook** is fully specified in `~/.gemini/antigravity-cli/builtin/skills/agy-customizations/docs/hooks.md`:
  - Defined under `<hook_name>.Stop` in `.agents/hooks.json` or `~/.gemini/config/hooks.json`.
  - Executes shell commands via `sh -c`.
  - Passes JSON context on `stdin` containing `terminationReason` (`"model_stop"`, `"max_steps_exceeded"`, `"error"`) and `error` details.
  - Fires on abnormal exit as well as normal completion.
- **Claude Code** acknowledges a `hooks` customization category in `claude --help` (`--safe-mode`).
- **Codex CLI** includes a `notify` command array in `~/.codex/config.toml` and a `--dangerously-bypass-hook-trust` flag in `codex --help`.

### Inferred (consistent with local evidence, not stated by it)
- **Global `~/.gemini/config/hooks.json` bypasses workspace folder trust gating.** The changelog documents that *workspace-local* hooks failed to load until a folder was trusted, and separately that the global path is the shared config. Neither states that the global path is exempt from the trust gate. Confirming this needs an experiment in an untrusted directory, which this task was scoped not to run.

### Unverified (Scout pass claims absent from local documentation)
- **Claude Code:** Exact hook event names (`StopAgent`, `SessionEnd`), payload JSON schemas, shell execution contracts, and abnormal exit handling in `.claude/settings.json`.
- **Codex CLI:** Lifecycle hook event names (`SessionEnd`, `Stop`), payload schemas, and abnormal exit behavior in `.codex/hooks.json` or via `features.codex_hooks`.
- **OMP:** Lifecycle event names (`agent_end`, `turn_end`) and hook configuration schemas.

---

## Push Notification Feasibility and Weakest Link

### Feasibility Today

1. **Supported Today with Verified Native Lifecycle Hooks:**
   - **`agy` (Antigravity CLI):** Fully supported. Placing a `Stop` hook in global `~/.gemini/config/hooks.json` runs an arbitrary shell notifier across normal completions (`model_stop`), step limit exhaustion (`max_steps_exceeded`), and runtime crashes (`error`). One caveat: whether that global hook loads in an **untrusted** workspace is inferred, not established (see Inferred above), so an unattended fleet should either trust its workspaces or verify this first.

2. **Partially Supported via Native Notification Dispatch:**
   - **`codex`:** Supports external notification dispatch via the `notify` array in `~/.codex/config.toml`. Full lifecycle hook contracts remain unverified.

3. **Cannot Determine / Unverified from Local Documentation:**
   - **`claude` (Claude Code):** Lacks installed hook documentation. Cannot configure push notification hooks unattended without empirical trial or external documentation.
   - **`omp` (Oh My Pi):** Lacks installed hook documentation or CLI help references for completion hooks.

### The Weakest Link

The weakest link in relying on CLI-level completion hooks for fleet supervision is **cross-engine heterogeneity and failure mode coverage**:
- While AGY explicitly documents abnormal termination events (`terminationReason: "error"`) within its `Stop` hook, client-side lifecycle hooks do not fire if the CLI process itself crashes catastrophically (e.g. `SIGKILL`, OS OOM killer, or unhandled PTY termination).
- Non-AGY CLIs (Claude, Codex, OMP) lack uniform, locally documented lifecycle hook schemas.
- Consequently, an external supervisor process (such as a Herdr runner monitor or process exit watcher) remains essential as the primary or fallback supervision mechanism for worker termination across the heterogeneous fleet.
