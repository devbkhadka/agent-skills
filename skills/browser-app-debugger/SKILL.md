---
name: browser-app-debugger
description: Live browser application debugging with Playwright and Debug Adapter Protocol (DAP / Antigravity IDE / VS Code). Automates login and deep view navigation via Playwright with --remote-debugging-port=9222, then attaches IDE debuggers for TypeScript/React breakpoint hit handling, sourcemap resolution, call stack inspection, and runtime variable diagnosis in hybrid or autonomous modes.
---

# Browser App Debugger (Playwright + DAP)

Automate tedious setup navigation, form fills, and authentication with Playwright, while attaching Antigravity IDE / VS Code debuggers (`js-debug` / DAP) to set breakpoints directly in your TypeScript/React application source code.

```bash
# Set this to the directory containing this loaded SKILL.md. Do not guess a home-directory path.
DEBUGGER_SKILL_DIR=/absolute/path/from-the-skill-source-locator/browser-app-debugger
S="$DEBUGGER_SKILL_DIR/scripts"
test -f "$S/launch-and-hold.mjs" || exit 1
```

---

## Non-negotiable rules

1. **Port Isolation**: Always launch Chrome with `--remote-debugging-port=9222` (or a dedicated custom port) so the IDE debugger can attach seamlessly.
2. **Auth State First**: Use saved auth states (`storageState`) whenever possible to avoid executing manual login flows on every debug iteration.
3. **Non-Blocking in Autonomous Mode**: In unattended agent runs, prefer non-blocking **Logpoints** (`Debugger.setBreakpoint` with `condition: "console.log(...), false"`) over hard breakpoints to avoid freezing browser event loops.
4. **Sourcemap Accuracy**: Verify that `.vscode/launch.json` points `webRoot` to the workspace directory containing the frontend source files so breakpoints bind to solid red dots.
5. **Clean Session Exit**: Always terminate the background launcher process (<kbd>Ctrl</kbd>+<kbd>C</kbd> or `SIGINT`) when debugging is completed.

---

## Quick Start: Launch & Attach

### 1. Launch Browser to Target Page
```bash
# Launch headed browser to target URL with port 9222 active
node "$S/launch-and-hold.mjs" http://localhost:3000/checkout --auth=.playwright-cli/auth.json
```

### 2. Attach Debugger in Antigravity IDE
In Antigravity IDE, press <kbd>F5</kbd> (**"Attach to Browser App"**). Breakpoints in your `.tsx` / `.ts` files will immediately bind!

---

## Task Matrix & Mode Dispatcher

| Mode / Workflow | Primary Use Case | Reference Guide |
| :--- | :--- | :--- |
| **Launch & Attach Configurations** | Setting up `.vscode/launch.json` for Vite, Next.js, Webpack, and monorepos. | [launch-configs.md](references/launch-configs.md) |
| **Hybrid Debugging (Human + Agent)** | Developer sets visual breakpoints in Antigravity IDE; Agent drives browser navigation and assists on paused scopes. | [hybrid-debugging.md](references/hybrid-debugging.md) |
| **Autonomous Debugging (Agent Solo)** | Agent sets non-blocking logpoints, triggers Playwright journeys, captures variable states, and fixes bugs autonomously. | [autonomous-debugging.md](references/autonomous-debugging.md) |

---

## Load detail only when needed

- **`.vscode/launch.json` Setup & Bundler Overrides:** [launch-configs.md](references/launch-configs.md)
- **Human-in-the-Loop IDE Breakpoint Workflow:** [hybrid-debugging.md](references/hybrid-debugging.md)
- **Autonomous Logpoint & Probe Remediation:** [autonomous-debugging.md](references/autonomous-debugging.md)
- **General Browser Automation Commands:** [`../playwright-cli/SKILL.md`](../playwright-cli/SKILL.md)
