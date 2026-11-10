---
name: js-runtime-debugger
description: Debug JavaScript and TypeScript at runtime through Node, Chrome, or React Native Hermes. Use for breakpoints, logpoints, stepping, scope inspection, and tracing live values over CDP.
---

# JS Runtime Debugger

A background daemon holds one CDP connection per target. The CLI returns one JSON object per
command, compact by default. Exit codes are `0` success, `1` error, and `2` pause timeout. Requires
Node **22 or newer**, with no package installation. Native Swift/Kotlin debugging is outside this skill.

Resolve the skill directory from its supplied source location; avoid guessing an installation path:

```bash
JS_DEBUGGER_SKILL_DIR=/absolute/path/to/the/loaded/js-runtime-debugger
node "$JS_DEBUGGER_SKILL_DIR/scripts/dbg.mjs" --help
```

Below, `dbg` means `node "$JS_DEBUGGER_SKILL_DIR/scripts/dbg.mjs"`. Spell out the executable path
on each tool call if shell variables do not persist. Read only the relevant runtime reference:

| Target | Attach example | Details |
| :--- | :--- | :--- |
| Node inspector | `dbg start --port 9229 --cwd /repo` | [Node](references/node.md) |
| Chrome page | `dbg start --kind chrome --port 9222 --cwd /repo --web-root apps/web` | [Chrome](references/web.md) |
| Hermes through Metro | `dbg start --kind hermes --cwd /repo --metro-root /repo` | [React Native](references/rn.md) |

## Start with a small hypothesis

Read the route, handler, and relevant service before attaching. Select two to five candidate lines
that distinguish possible causes. Discover unknown targets with `dbg targets --kind node|chrome|hermes`
(the alternatives mean choose one). Confirm `attached: true` after `start`. Set probes using
repo-relative source paths and one-based lines, such as `src/orders.ts:42`.

Use one session per target. When inspecting multiple targets, supply `--session api` or
`--session web` on **every** call. Reusing a name with different target settings returns
`SESSION_CONFLICT`; use a different session or stop the old one first.

## Logpoint loop

Prefer logpoints on UI threads and when values alone answer the question:

```bash
dbg logpoint src/orders.ts:42 '{ id: order.id, total }'
# Run the request, navigate the route, or reproduce the action.
dbg logs --since 0 --limit 20
```

Read the oldest unread page, then pass its **`nextSeq`** as the next `--since`. Continue while
`hasMore` is true. `latestSeq` and legacy `lastSeq` are the highest observed sequence, not page
cursors. `dropped` reports overwritten ring-buffer hits; `omitted` reports remaining hits outside
this response. Use `--tail` explicitly when only the newest samples matter. Logpoints never
complete `wait`, so this loop needs no background wait command.

## Breakpoint loop

When stepping or paused-frame evaluation is necessary:

```bash
dbg break src/orders.ts:42 --if 'total < 0'
dbg wait --timeout 300
```

Run `wait` in the tool's background mode **before** triggering the request or UI action. A pause
returns its source excerpt, compact stack, and scopes. Inspect selected values with
`dbg scope --scope local --vars total,order`; evaluate a small synchronous expression, or
`dbg step over|into|out`. Resume promptly with `dbg continue`. A backend pause stalls every request;
keep the trigger running in the background with sufficient request timeout. A browser/mobile
pause freezes its JS thread; explain the expected freeze before triggering it. Avoid async app
calls and browser automation while paused. Continue before editing target code.

## Recover by state

Check each probe response and use `dbg list` or `dbg status` after an unexpected timeout.

| State | Next action |
| :--- | :--- |
| `bound` | Trigger the relevant code and inspect. |
| `pending`, module unloaded | Load the lazy module or route, then recheck. |
| `pending`, module loaded | Check path, runtime root, and build membership. |
| `stale-map` | Restart/rebuild the target before trusting the probe. |
| `DETACHED` | Target may be restarting; inspect status and retry after reattach. |
| `displaced` / `DISPLACED` | Coordinate with the other debugger; explicitly restart only with consent. |
| `WAIT_TIMEOUT` | Check binding and trigger execution before repeating the wait. |
| `AMBIGUOUS` | Use a longer source path from `error.candidates`. |

Do not extend timeouts repeatedly to conceal an unbound probe. Never attach to shared or remote
environments without explicit authorization.

## Expand only what answers the question

Snapshots collapse library frame runs and summarize wrapper/large closure scopes. Select a
scope by type or name with `--scope`, and variables with `--vars a,b`. Selected variables bypass
closure summaries. `scope --all` includes summarized scopes but retains caps: 200 variables per
scope, 10 properties per value, depth at most 5, and truncated strings.

`stack --all` returns up to 60 uncollapsed frames. Paginate with `--offset N --limit 20`, using
`nextOffset`; frame indices always refer to the real stack. Output defaults to a 16,384-byte budget.
`outputTruncated` explicitly reports trimming; narrow the request or increase
`--max-output-bytes N` when evidence is missing. `--pretty` adds indentation within the same budget.
See [command details and limits](references/commands.md) only when needed.

Remove probes that ruled out a cause with `remove` or `clear`. Fix the code, rerun the same trigger,
and compare values. **Always `dbg stop`**, including error and abandoned sessions: it removes
probes, resumes, and detaches. It never kills the target application. Stop only application
processes you started; idle timeout is not a cleanup plan.
