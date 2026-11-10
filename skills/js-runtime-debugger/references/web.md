# Chrome page

`dbg` means the executable in [SKILL.md](../SKILL.md). Start the project's dev server with source
maps and open a dedicated browser with remote debugging enabled. An ordinary existing browser
window usually has no debugging port.

```bash
"/path/to/chrome" --headless=new --remote-debugging-port=9334 \
  --user-data-dir=/absolute/temporary/debug-profile about:blank
dbg targets --kind chrome --port 9334
dbg start --kind chrome --port 9334 --cwd /absolute/repo \
  --web-root apps/web --target 'localhost:4200|about:blank'
```

Run Chrome in the background; use a fresh profile directory. For a human-operated debug window,
the optional [browser-app-debugger](../../browser-app-debugger/SKILL.md) launcher can hold it open.
Resolve that skill's location before using its scripts. Optional
[playwright-cli](../../playwright-cli/SKILL.md) can drive a running page. Read those skills only if
needed; simultaneous CDP-client behavior depends on the client.

`--target` matches page URL/title; DevTools and service workers are skipped. Specify it when
multiple pages exist. `--web-root` is the dev server's root, relative to `--cwd`: `/src/a.ts` maps
under that root; Vite `/@fs/<absolute-path>` sources already identify a disk file. Incorrect roots
commonly leave probes pending. Navigate with `dbg eval 'location.href = "http://localhost:4200/"'`
or reload with `dbg reload`.

Lazy routes and dynamic imports can initially leave probes `pending`. Trigger module loading,
then recheck `list`; the daemon binds before module execution using its load trap. Reload and
HMR rebind registered probes; source excerpts follow the new map generation. Check source line
numbers after editing the probed file.

Prefer logpoints. Breakpoints freeze rendering, timers and sockets. Other clients observe pauses
and can resume them; browser automation while paused may hang. Inspect synchronous state, then
`continue`; avoid evaluating promises that need timers or networking to finish.

After `dbg stop`, shut down only the dedicated browser/launcher you started and clean its temporary
profile. Optional historical session: [Incyt dashboard](../examples/incyt-web.md).
