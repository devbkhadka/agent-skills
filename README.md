# Agent Skills & MCP Registry

A unified registry and installer toolchain for managing **AI agent skills**, **Model Context Protocol (MCP) servers**, and **system prompts** across modern CLI coding agents: **Gemini / AGY**, **Claude Code**, **Codex**, and **OpenCodeInterpreter (OMP)**.

---

## Quick Start

### Prerequisites
- **Node.js** (v20+ recommended)
- **[pnpm](https://pnpm.io/)** (`npm install -g pnpm`)
- **[just](https://just.systems/)** (`brew install just`)

```sh
# Clone & install dependencies
git clone git@dev-github:devbkhadka/agent-skills.git
cd agent-skills
pnpm install

# Interactive installers
just install         # Install agent skills
just mcp-install     # Install MCP servers
just prompt-install  # Install system prompts
```

---

## Repository Structure

```text
agent-skills/
├── skills/              # Reusable agent skills
│   ├── agent-desk/              # User communication & human-in-the-loop asks
│   ├── agent-herder/            # Unattended multi-agent terminal fleet orchestration
│   ├── architecture-rules/      # Pure-core boundaries emitted as repo fitness checks (VFD)
│   ├── beads-task-scheduler/    # DAG task scheduling via Beads
│   ├── browser-app-debugger/    # Chrome/Playwright + DAP live browser debugger
│   ├── change-lane/             # Triage a diff into TWEAK / EXTENSION / NEW_CAPABILITY (VFD)
│   ├── domain-baseline/         # Committed project domain baseline in docs/domain (VFD)
│   ├── feature-gaps/            # Find missing states, branches & terms in a feature (VFD)
│   ├── js-runtime-debugger/     # Runtime TS/JS breakpoint & logpoint debugger
│   ├── playwright-cli/          # Headless browser testing & visual review
│   ├── quick-prompt/            # Reusable prompt template runner
│   ├── resource-lease/          # Concurrency leases & lock coordination
│   ├── scheduler/               # mac-scheduler (BullMQ/Redis) wrapper
│   ├── skill-improver/          # Bug & friction suggestion collector
│   ├── smart-context-lite/      # REST-backed project concept context
│   ├── subagent-feature-driver/ # Autonomous multi-step feature implementation
│   ├── terminal-agent-runner/   # Herdr terminal runner for coding fleets
│   ├── timelog-generator/       # Deterministic Git & Jira monthly time logging
│   └── verified-feature-delivery# Contract-driven 6-stage pipeline; engine + router for the 4 VFD skills
├── mcp/                 # Model Context Protocol (MCP) server definitions
│   ├── context7/                # Context7 API server integration
│   └── fetch/                   # Web resource fetch server
├── system-prompts/      # Persona & instructions library
│   └── generic-system-prompt/   # Base personas with per-agent overrides
├── scripts/             # Automated installer, linker & status toolchain
├── justfile             # Command runner recipes
└── package.json         # Toolchain manifests and runner dependencies
```

---

## Agent Target Matrix

The toolchain automatically resolves target config locations and format differences across agents:

| Agent | Global Skill Path | MCP Configuration | Format |
| :--- | :--- | :--- | :--- |
| **Claude Code** | `~/.claude/skills` | `~/.claude.json` | JSON |
| **Gemini CLI** | `~/.gemini/skills` | `~/.gemini/settings.json` | JSON |
| **AGY** | `~/.gemini/skills` | `~/.gemini/config/mcp_config.json` | JSON |
| **Codex** | `~/.codex/skills` | `~/.codex/config.toml` | TOML |
| **OMP** | `~/.omp/agent/skills` | `~/.omp/agent/mcp.json` | JSON |

> **Project Scopes:** Skills and MCP servers can also be installed into individual project roots using `--scope project --project <path>`.

---

## Common Commands

All common tasks are accessible via [`just`](justfile) or `pnpm run`:

### 1. Skills
```sh
just install                                              # Interactive selector
just install --skill agent-herder --scope global --agents claude,agy
just install --skill playwright-cli --scope project --project ../my-app --agents codex
just status                                               # Audit installed vs repo versions
```

### 2. MCP Servers
```sh
just mcp-install                                          # Interactive selector
just mcp-install --server fetch --scope global --agents claude
just mcp-install --server context7 --scope global --agents agy --set CONTEXT7_API_KEY=your_key
just mcp-status                                           # Audit installed MCP servers
just mcp-uninstall --server fetch --scope global --agents claude
```

### 3. System Prompts
```sh
just prompt-install                                       # Interactive prompt setup
just prompt-install --prompt generic-system-prompt --scope global --agents claude,gemini
just prompt-status                                        # Audit prompt installations
```

### 4. Quality & Testing
```sh
just test                                                 # Run full unit & installation test suites
just suggestions                                          # View captured agent improvement suggestions
```

---

## Architecture & Safety Guarantees

1. **Read-Only Installation Protection (Copy-then-Link)**:
   Skills are copied to `~/.agents/skills/` and set to read-only (`r-xr-xr-x`) before symlinking into agent directories. If an autonomous agent attempts to "fix" a skill during execution, the OS write restriction blocks it from modifying the installed copy, redirecting developers back to this source repository.
2. **Credential Redaction in Backups**:
   Before modifying any agent configuration file (`.claude.json`, `config.toml`, etc.), the installer creates a timestamped safety backup in `~/.agents/mcp-servers/backups/`. All API tokens, bearer keys, and environment passwords are systematically redacted in the backup.
3. **Declarative MCP Environment Resolution**:
   MCP configurations strictly resolve environment parameters using:
   `CLI Flags (--set)` > `mcp/<server>/.env` > `process.env` > `.env.example` > `defaults`. Undeclared parameters or missing required keys fail fast before any configuration file is altered.

---

## License

This project is licensed under the [MIT License](LICENSE).
