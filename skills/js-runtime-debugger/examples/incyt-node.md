> Optional historical project example, recorded 2026-10-03. Commands, environment assumptions and abbreviated outputs are project-specific. Use the current [command contract](../references/commands.md); paginate logs with `nextSeq`, not historical `lastSeq` examples.

# Node targets (V8 inspector)

Any Node process with an inspector (`--inspect[=host:port]`) is a target. The worked example is the
incyt `client-api` (Nx `@nx/js:node` executor, webpack bundle with an external source map), which
is the general pattern for Nx Node apps: build to `dist/apps/<app>/main.js`, run it under a watcher,
expose the inspector on a fixed or random port.

`dbg` below means `node "$JS_DEBUGGER_SKILL_DIR/scripts/dbg.mjs"` (see ../SKILL.md).

## 1. Start the server in debug mode (incyt client-api)

Run from the monorepo root. Pick the mode before starting; the default is local data.

| Mode | Command | Data |
| :--- | :--- | :--- |
| **Default — local DB** | `NEW_RELIC_ENABLED=false OUTBOX_DISABLE_DISPATCH=1 npx nx run client-api:serve --inspect=inspect --port=9229` | Docker postgres `incyt-local-postgres` on `:5432` (default `environment.ts`, DB `incyt`; `DB_HOST`/`DB_PORT` override) |
| **Opt-in — dev/staging data** | `AWS_PROFILE=incyt-aws NEW_RELIC_ENABLED=false OUTBOX_DISABLE_DISPATCH=1 npx nx run client-api:serve:debug` | Remote dev RDS via Secrets Manager; inspector on `localhost:9229` |

- Use `serve:debug` **only when the user asks** to reproduce against dev/staging data. It is a shared
  environment: on boot it **writes** application-registry sync rows to that DB and, on dev, starts
  the Storage Service SQS queue processor (it consumes real queue messages). Say so before starting it.
- Always set `OUTBOX_DISABLE_DISPATCH=1` (no outbox dispatch loop, no outbound side effects) and
  `NEW_RELIC_ENABLED=false` (no APM noise).
- **Second instance alongside an existing one** (the user's own server already holds `:3333`): add
  env `port=3334` — lowercase, the app reads `process.env.port || 3333`. The inspector port must be
  free too (`--port=9230` etc.).
- Wait for the "listening" log before attaching; the first webpack build of client-api is slow.

Other Node apps: anything with `--inspect` works — `node --inspect dist/main.js`,
`tsx --inspect src/main.ts`, `nest start --debug`, etc. Ask before adding `--inspect` to a process
the user did not start for debugging.

## 2. Attach

Known fixed port:

```bash
dbg start --port 9229 --cwd /abs/path/to/incyt-monorepo
```

Already-running server on a **random** inspector port (`--inspect=localhost:0`: Nx default `serve`,
VS Code auto-attach, `NODE_OPTIONS=--inspect` children):

```bash
dbg targets --kind node                     # real output, a running `nx run client-api:serve`
# {"targets":[{"kind":"node","pid":75149,"host":"127.0.0.1","port":53819,"id":"59713b39-…",
#   "title":"/…/incyt-monorepo/node_modules/@nx/js/src/executors/node/node-with-require-overrides",
#   "url":"file://","cwd":"/…/incyt-monorepo","command":"…/node -r …/node-with-require-overrides"}]}
# Several client-api instances look identical except pid/port: pick by pid (`lsof -nP -iTCP:3333 -sTCP:LISTEN`
# names the one serving HTTP on 3333) or ask the user which one.
dbg start --pid 75149 --cwd /abs/path/to/incyt-monorepo
```

- `--pid` looks up the process's inspector port; when the watcher re-forks onto a new random port,
  the daemon finds the new child (same command line and cwd) and reattaches.
- `NO_PROCESS` → pid is gone or not yours; `NO_INSPECTOR` → it runs without `--inspect` (restart it
  with `--inspect` or use `serve:debug`). Never attach to a process the user did not point you at.
- `--cwd` must be the repo root the server was **built from**; breakpoint paths are matched and
  displayed relative to it.
- `NO_TARGET` → nothing on that port; `error.targets` lists what is. If several node targets share
  a port, pick one with `--target <regex>` (matched against the target url/title).

## 3. Behaviours to know

- **Watch re-fork.** `serve` rebuilds on save, SIGTERMs the child and re-forks ~1 s later with a new
  target id. The daemon reattaches automatically and re-applies every probe (`status.reconnects`
  increments; calls during the gap fail with `DETACHED` — retry). Line numbers you set refer to the
  source as it is now; after editing a file, re-check probe lines.
- **`pending`.** The source is not in any loaded script yet (the bundle has not loaded, path typo,
  or the file is not part of this app's bundle). For modules loaded later the daemon arms a
  load-time trap and binds before their first line runs.
- **`stale-map`** (`error.code: STALE_SOURCE_MAP`, also in `status.staleSourceMaps`). The running
  `main.js` differs from `dist/.../main.js(.map)` on disk, so breakpoints would land on the wrong
  lines and are refused. Real case: the user's server was built at 12:56; another build rewrote
  `dist` at 13:02; every probe came back `stale-map`. Fix: restart the server (or let the watcher
  re-fork), then `dbg list` shows `bound` — the daemon re-binds on re-parse by itself.
- **One worktree.** client-api maps hold **absolute** paths of the worktree that produced the bundle
  (2,500+ sources). If you are editing a different worktree, its line numbers may not match — run
  the server from the worktree you edit.
- **Pseudo-sources.** Maps also contain `external commonjs "x"` / `webpack/...` entries; they are not
  files and cannot take breakpoints. `AMBIGUOUS` on a short name (`index.ts`, `router.ts`) → pass a
  longer path from `error.candidates`.
- **A pause stops the whole server.** Every other request (health checks, other devs' calls against
  a shared server) blocks until you `continue`.

## 4. Trigger with curl

Base paths: REST `/v2/client-api/*`, tRPC `/api/trpc` and `/api/trpc/v11`, port `3333` (or `$port`).

- Give curl a `--max-time` that outlives your inspection (120-600) and run it in the background so
  you can keep issuing `dbg` calls while the server is paused.
- Authenticated endpoints: ask the user for a token (or the exact request copied from the browser's
  network tab) and pass it as `-H "Authorization: Bearer $TOKEN"` from an env var. Never hardcode,
  log or commit tokens; never mint one against a shared Cognito pool on your own.
- tRPC queries are `GET /api/trpc/<router.proc>?input=<url-encoded JSON>`; mutations are `POST`
  with a JSON body. Read the router to get the exact procedure name and input shape.

## 5. Worked session (real run, 2026-10-03: `serve:debug` with `port=3334`, inspector 9229)

Breakpoint in `getHeapStats()` (`apps/client-api/src/main.ts:102`, local `stats` already assigned),
reached by `GET /v2/client-api/healthcheck` (no DB query, no auth). JSON abbreviated (`…`).

```bash
dbg start --port 9229 --cwd ~/projects/incyt/incyt-monorepo
# {"started":true,"attached":true,"kind":"node","inspector":"127.0.0.1:9229","paused":false,
#  "breakpoints":{"total":0,…},"scripts":{"parsed":6716,"withSourceMap":1402,…},…}

dbg break apps/client-api/src/main.ts:102
# {"id":"1","kind":"breakpoint","location":"apps/client-api/src/main.ts:102","state":"bound",
#  "locations":[{"file":"apps/client-api/src/main.ts","line":102,"column":3,
#                "generated":{"url":"file:///…/dist/apps/client-api/main.js","line":315142,…}}]}

dbg wait --timeout 300                                                   # background
curl -s -m 120 http://localhost:3334/v2/client-api/healthcheck          # background trigger

# wait returns (compact snapshot):
# {"paused":true,"reason":"other","hitBreakpoints":["1"],
#  "frame":{"index":0,"function":"getHeapStats","file":"apps/client-api/src/main.ts","line":102,"column":3,
#           "generated":"dist/apps/client-api/main.js:315142:…",
#           "source":["  100 | function getHeapStats() {",
#                     "  101 |   const stats =  v8.getHeapStatistics();",
#                     "> 102 |   return {",
#                     "  103 |     max: stats.heap_size_limit,",
#                     "  104 |     usage: stats.used_heap_size,"]},
#  "stack":[{"index":0,"function":"getHeapStats","file":"apps/client-api/src/main.ts","line":102,…},
#           {"index":1,"function":"(anonymous)","file":"apps/client-api/src/main.ts","line":919,…},
#           {"collapsed":<N>,"from":"express"}, …],
#  "scopes":[{"type":"local","name":"getHeapStats","variables":{"stats":{"total_heap_size":…,"used_heap_size":…,…}}},
#            {"type":"closure","omitted":<N>,"why":"…","hint":"dbg scope --frame 0 --all"}, …]}

dbg eval 'stats.used_heap_size / stats.heap_size_limit'
# {"context":"frame 0","type":"number","value":0.0181…}

dbg step over                     # → snapshot at apps/client-api/src/main.ts:106
dbg continue                      # {"resumed":true}; curl completes
dbg stop                          # {"stopped":true,"pid":…}
```

`{"collapsed":N,"from":"express"}` hides express router frames; `{"omitted":N,…}` hides a closure
(module/bundler/large) scope. Use `dbg stack --all` / `dbg scope --frame 0 --all` when you need them.

Logpoint variant (no pause; preferred when other people use the server):

```bash
dbg logpoint apps/client-api/src/main.ts:102 '{ used: stats.used_heap_size, limit: stats.heap_size_limit }'
curl -s -m 60 http://localhost:3334/v2/client-api/healthcheck
dbg logs --since 0
# {"hits":[{"seq":1,"ts":"…","breakpoint":"1","location":"apps/client-api/src/main.ts:102","value":{"used":…,"limit":…}}],"lastSeq":1}
```

## 6. Cleanup of a server the agent started

`npx nx run …` reparents the Nx run-executor to pid 1, so killing the `npx` pid leaves the server
running (and holding its ports). Stop an agent-started server by the pid that owns its port and that
pid's run-executor parent:

```bash
lsof -nP -iTCP:3334 -sTCP:LISTEN          # server pid
ps -o ppid= -p <server pid>               # its run-executor parent
kill <server pid> <run-executor pid>
```

Never kill processes the agent did not start — the user's own `:3333` server, their VS Code
debug session, or a teammate's process. `dbg stop` only detaches; it never kills the target.
