> Optional historical project example, recorded 2026-10-03. Commands, environment assumptions and abbreviated outputs are project-specific. Use the current [command contract](../references/commands.md); paginate logs with `nextSeq`, not historical `lastSeq` examples.

# Web targets (Chrome page via CDP)

`--kind chrome` attaches to one browser **page** on a Chrome remote-debugging port and maps the
dev server's script URLs back to repo files. Verified against Vite 7 (Chrome for Testing 153);
webpack dev servers with `file`-style sources work through the same source-map path.

Worked example: incyt **dashboard** — Vite, port `4200`, Vite `root` = `apps/dashboard`; workspace
libs outside the root are served as `/@fs/<abs path>`.

`dbg` below means `node "$JS_DEBUGGER_SKILL_DIR/scripts/dbg.mjs"` (see ../SKILL.md).

## 1. Dev server + debuggable browser

```bash
# monorepo root — dashboard dev server against local services (justfile: dashboard-local)
npx nx run dashboard:serve:local          # http://localhost:4200
```

The user's everyday browser is not debuggable. Pick one:

- **Agent-only, headless** (no human clicking): launch Chrome for Testing with its own profile and
  run it in the background; kill it after `dbg stop`.

  ```bash
  "<Chrome for Testing binary>" --headless=new --remote-debugging-port=9334 \
    --user-data-dir="$(mktemp -d)" about:blank
  ```

- **Headed window the human uses** — the **browser-app-debugger** skill's launcher on `:9222`
  (dependency — resolve its directory the same way as this skill's; do not copy the script):

  ```bash
  node "$DEBUGGER_SKILL_DIR/scripts/launch-and-hold.mjs" http://localhost:4200/<route> \
    --auth=.playwright-cli/auth.json        # optional saved storageState; --port=<n> to change 9222
  ```

  Run it in the background; it holds the browser open until SIGINT. The human clicks through the
  app in **that** window. The agent may drive it with playwright-cli
  (`playwright-cli attach --cdp=http://127.0.0.1:9222`) — unverified live as a second CDP client;
  fall back to asking the human.

`dbg targets --kind chrome` lists pages on 9222/9229 (add `--port 9334` for another port).

## 2. Attach

```bash
dbg start --kind chrome --port 9334 --cwd /abs/path/to/incyt-monorepo --web-root apps/dashboard \
  [--target 'localhost:4200']
```

- `--web-root` = the dev server's root (Vite `root`), relative to `--cwd`. `/src/x.ts` resolves under
  it; `/@fs/<abs>` (libs) is already absolute. A wrong web root shows up as probes stuck `pending`.
- `--target` regex picks the page by URL/title (DevTools and service-worker targets are skipped).
  Without it the first ordinary page wins — always pass it when several tabs are open.
- **Navigate from `dbg`:** `dbg eval 'location.href = "http://localhost:4200/"'` loads the app in
  the attached page (works from `about:blank`); `dbg eval 'location.reload()'` re-runs it.
  `dbg reload` does the same over CDP.

## 3. Breakpoints and logpoints

Use repo paths, exactly as for Node:

```bash
dbg logpoint apps/dashboard/src/pages/orders/OrderTable.tsx:57 '{ rows: rows.length, filter }'
dbg break libs/ui/src/lib/date-range.ts:31 --if 'end < start'
```

- **Lazy routes / dynamic `import()`** bind before the module's first line runs, so a probe set
  before navigating to a lazy page still catches its top-level code. Until the module loads the
  probe is `pending` — that is expected; navigate there, then `dbg list` shows `bound`.
- **Reload and HMR survive.** Probes are re-bound after a full reload and after HMR replaces a
  module (URLs gain `?t=<ms>`); no need to re-set them after saving a file. Re-check line numbers
  after editing the probed file itself.
- **Logpoints are the default here.** A hard pause freezes rendering, timers and websockets; the
  page looks hung to the human. Tell them before you set a breakpoint, and `continue` quickly.
- **Other CDP clients see our pauses.** Playwright or an open DevTools window on the same page
  observe the pause (reason `other`) and can resume it; a playwright-cli action issued while
  paused will hang until `continue`. Do not drive the page while it is paused.
- Evaluating while paused is synchronous only: `eval` of an expression that awaits a timer or
  network promise will block. Read state, do not call async app code.

## 4. Worked session (real run, 2026-10-03: dashboard on :4200, headless Chrome for Testing on :9334)

Goal: see `nrAppId` before `main.tsx` decides whether to start New Relic. Probes set while the page is
still `about:blank`, so they start `pending` and bind before the module's top level runs.

```bash
dbg start --kind chrome --port 9334 --cwd ~/projects/incyt/incyt-monorepo --web-root apps/dashboard
# {"started":true,"attached":true,"kind":"chrome","inspector":"127.0.0.1:9334","target":{…"url":"about:blank"},…}

dbg break apps/dashboard/src/main.tsx:29
# {"id":"1","kind":"breakpoint","location":"apps/dashboard/src/main.tsx:29","state":"pending","locations":[]}
dbg logpoint apps/dashboard/src/main.tsx:20 '{ production: environment.production, nrAppId }'
# {"id":"2","kind":"logpoint",…,"state":"pending",…}

dbg eval 'location.href = "http://localhost:4200/"'     # navigate the attached page
dbg wait --timeout 120
# {"paused":true,"reason":"other","hitBreakpoints":["1"],
#  "frame":{"index":0,"function":"(anonymous)","file":"apps/dashboard/src/main.tsx","line":29,…,
#           "generated":"http://localhost:4200/src/main.tsx:24:1",
#           "source":[…,"> 29 | if (nrAppId !== ''){",…]},…}
#  → paused before the module's top level ran past line 29

dbg eval nrAppId
# {"context":"frame 0","type":"string","value":""}
dbg logs --since 0
# {"hits":[{"seq":1,…,"breakpoint":"2","location":"apps/dashboard/src/main.tsx:20",
#           "value":{"production":false,"nrAppId":""}}],"lastSeq":1}

dbg continue
dbg eval 'location.reload()'
dbg wait --timeout 120          # re-hits main.tsx:29 — no need to re-issue `break` after a reload
dbg continue
dbg stop                        # then kill the headless Chrome (or SIGINT the launcher)
```

Human-driven variant: same probes against the launcher's `:9222` window; start `dbg wait` in the
background and tell the human exactly what to click, e.g. "In the debug Chromium window open
Orders, set the filter to 'Pending', and tell me when the table renders." Prefer logpoints there —
a pause freezes the page the human is looking at.
