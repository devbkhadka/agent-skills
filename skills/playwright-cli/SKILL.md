---
name: playwright-cli
description: Browser automation, interactive visual UI reviews, test debugging, network mocking, and auth state management via playwright-cli. Use when automating web tasks, debugging Playwright tests, interacting with web pages, running visual annotation workflows, or inspecting React Fiber component hierarchies with minimal token overhead.
---

# Playwright CLI

Token-efficient browser automation daemon and testing toolkit designed specifically for AI coding agents. Uses ref-based accessibility snapshots and progressive task disclosure to interact with pages without bloating context windows.

```bash
# Set this to the directory containing this loaded SKILL.md. Do not guess a home-directory path.
PLAYWRIGHT_SKILL_DIR=/absolute/path/from-the-skill-source-locator/playwright-cli
S="$PLAYWRIGHT_SKILL_DIR/scripts"
test -f "$S/probe-fiber.js" || exit 1
```

---

## Non-negotiable rules

1. **Strict Token Conservation**: Never load raw full-page DOM/YAML snapshot files into agent context. Interact via element refs (`e1`, `e2`), targeted search (`find`), or coordinate probing (`probe-fiber.js`).
2. **Interactive vs Headless Targeting**: Always pass `--headed` when opening a browser for user-facing visual review or annotation workflows (`playwright-cli open <url> --headed`). Use headless mode for background tasks and automated tests.
3. **Session Hygiene**: Always close or detach browser sessions upon workflow completion (`playwright-cli close`, `playwright-cli -s=<name> close`, or `playwright-cli close-all`).
4. **Ref & Semantic Locators**: Target elements using snapshot refs (`e1`, `e5`) or role-based locators (`getByRole('button', { name: 'Save' })`). Never generate or rely on brittle, long CSS selectors.
5. **Progressive Disclosure**: Load only the task-specific reference file needed for your immediate objective. Do not dump the entire reference library into context.

---

## Core Quick Start

```bash
# 1. Open browser & navigate
playwright-cli open https://example.com/login

# 2. Get accessibility snapshot with element refs (e1, e2, ...)
playwright-cli snapshot

# 3. Perform actions using snapshot refs
playwright-cli fill e1 "user@example.com"
playwright-cli fill e2 "password123" --submit
playwright-cli click e3

# 4. Take targeted screenshot or search
playwright-cli screenshot --filename=.playwright-cli/state.png
playwright-cli find "Dashboard"

# 5. Clean up
playwright-cli close
```

---

## Task Dispatcher (Branch into Sub-Skills)

When your goal matches a specific workflow, load its dedicated reference guide:

| Task / Goal | Key Capabilities | Load Reference |
| :--- | :--- | :--- |
| **Visual Annotation & React Mapping** | Interactive user UI review (`show --annotate`), token-efficient bounding boxes, React Fiber `_debugSource` probing to React component files. | [annotation-workflow.md](references/annotation-workflow.md) |
| **Test Debugging & Execution** | Running test suites (`PLAYWRIGHT_HTML_OPEN=never`), pause-and-attach with `--debug=cli`, step-over, live locator repair. | [test-debugging.md](references/test-debugging.md) |
| **Test Generation & Healing** | Turning exploratory CLI actions into `@playwright/test` TypeScript files, semantic locators, aria snapshot assertions, test healing. | [test-generation.md](references/test-generation.md) |
| **Network Mocking & Interception** | Intercepting API routes (`route`, `unroute`), mocking status codes and JSON payloads, inspecting live request/response bodies. | [network-mocking.md](references/network-mocking.md) |
| **Session & Auth State Storage** | Saving/loading auth state (`state-save`/`state-load`), cookie management, localStorage/sessionStorage, multi-session isolation (`-s=<name>`). | [session-storage.md](references/session-storage.md) |
| **Custom Code & DevTools** | Running arbitrary JavaScript expressions (`run-code`/`eval`), permissions, geolocation, iframe handling, downloads, dialogs. | [custom-code.md](references/custom-code.md) |
| **Tracing & Video Diagnostics** | Action tracing (`tracing-start`/`tracing-stop`), video recording with chapter markers and custom overlay callouts, console logs. | [tracing-devtools.md](references/tracing-devtools.md) |
| **Application Debugging (Playwright + DAP)** | Launching with `--remote-debugging-port=9222`, attaching Antigravity IDE / VS Code for live React/TypeScript breakpoints. | [`../browser-app-debugger/SKILL.md`](../browser-app-debugger/SKILL.md) |

---

## Essential CLI Cheatsheet

### Core Interaction
```bash
playwright-cli open <url> [--headed] [--mobile] [--browser=chrome|firefox|webkit]
playwright-cli snapshot [--boxes] [--depth=<n>]   # capture snapshot refs
playwright-cli find "<text>" [--regex "<pattern>"] # search snapshot with context
playwright-cli click <ref|selector>
playwright-cli fill <ref|selector> "<text>" [--submit]
playwright-cli type "<text>"
playwright-cli press <key>                         # e.g. Enter, Escape, ArrowDown
playwright-cli select <ref> "<value>"
playwright-cli upload <file-path>
playwright-cli resize <width> <height>
```

### Element Inspection & Fiber Probing
```bash
playwright-cli eval "el => el.getAttribute('data-testid')" <ref>
playwright-cli eval "($(< scripts/probe-fiber.js))({ x: <X>, y: <Y>, width: <W>, height: <H> })"
playwright-cli generate-locator <ref> --raw
playwright-cli highlight <ref> [--style="..."] [--hide]
```

### Session & Tab Management
```bash
playwright-cli -s=<name> open <url>                # isolated session
playwright-cli tab-list
playwright-cli tab-new <url>
playwright-cli tab-select <index>
playwright-cli close-all                           # terminate all active browsers
playwright-cli kill-all                            # force kill zombie processes
```

---

## Load detail only when needed

- **Visual Annotation & React Component Mapping:** [annotation-workflow.md](references/annotation-workflow.md)
- **Running and Debugging Playwright Tests:** [test-debugging.md](references/test-debugging.md)
- **Test Generation & Healing:** [test-generation.md](references/test-generation.md)
- **Network Mocking & Traffic Inspection:** [network-mocking.md](references/network-mocking.md)
- **Browser Session & Storage Management:** [session-storage.md](references/session-storage.md)
- **Custom Playwright Script Execution:** [custom-code.md](references/custom-code.md)
- **Tracing & Video Diagnostics:** [tracing-devtools.md](references/tracing-devtools.md)
- **Live Application Debugging (Playwright + DAP):** [`../browser-app-debugger/SKILL.md`](../browser-app-debugger/SKILL.md)
