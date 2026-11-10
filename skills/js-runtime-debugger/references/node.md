# Node inspector

`dbg` means the executable defined in [SKILL.md](../SKILL.md). Launch a local development process
with `node --inspect=127.0.0.1:9229 dist/main.js`, or use its existing debug task. Wait for its
listening message; applications compiled from TypeScript need matching source maps.

```bash
dbg targets --kind node
dbg start --port 9229 --cwd /absolute/repo
# For an inspector on a random port, choose its pid from targets:
dbg start --pid 12345 --cwd /absolute/repo
```

Process discovery uses `ps` and `lsof` on macOS/Linux. `MISSING_TOOL` or `UNSUPPORTED_PLATFORM`
can be avoided by supplying `--port`. `NO_PROCESS` means the pid disappeared or is not yours;
`NO_INSPECTOR` means the process has no inspector. With several similar processes, match command,
cwd, and application listening port; do not choose by the first result alone.

`--pid` follows watch re-forks using process command and cwd. Normal disconnects reattach and
reapply probes; `status.reconnects` increments. Calls during the restart gap can return `DETACHED`.
Check probe line numbers again after editing the source.

Use the checkout that produced the running bundle: absolute map paths can point at another
worktree. Disk maps paired with a different running build cause `stale-map`; restart the process
or let its watcher rebuild and re-fork. Pseudo-sources such as webpack externals are not real files.
Use a longer path from `error.candidates` for ambiguous basenames.

Trigger a local endpoint with its actual route and authentication requirements. For breakpoints,
run both `wait` and the request in the background, for example
`curl --max-time 300 http://localhost:3000/health`. A pause blocks all requests until `continue`.
Keep credentials in environment variables; never log or commit them.

`dbg stop` detaches and resumes without killing the server. When stopping a server you started,
watch wrappers may leave child processes running; identify the owner of its listening port and
its parent before terminating them. Preserve user-owned processes.

Optional project setup and historical worked session: [Incyt Node](../examples/incyt-node.md).
