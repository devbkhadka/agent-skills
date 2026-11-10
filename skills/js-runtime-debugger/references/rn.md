# React Native Hermes through Metro

`dbg` means the executable in [SKILL.md](../SKILL.md). Requires Metro running, usually on 8081,
and a debug app build on a simulator, emulator, or device. Release builds are not debuggable.
A physical Android device may need `adb reverse tcp:8081 tcp:8081` to reach Metro.

```bash
dbg targets --kind hermes
dbg start --kind hermes --port 8081 --cwd /absolute/app --metro-root /absolute/app \
  --target 'Device Name'
```

Metro can list legacy and modern pages for one device. The CLI prefers the modern Fusebox page;
`--target` matches title, description, device name or app id. `--metro-root` corresponds to Metro
`projectRoot`; `--watch-folders a,b` lists configured watch folders in their actual order. These
resolve `/[metro-project]/...` and `/[metro-watchFolders]/N/...` sources. Maps without
`sourcesContent` need disk excerpts, so the checkout must match the served bundle. Large maps
can take a few seconds to load.

Hermes permits one debugger per device. Attaching can displace React Native DevTools; coordinate
first if another debugger is open. When displaced, `status.state` is `displaced` and commands return
`DISPLACED`. Automatic reconnect is disabled to avoid fighting for the device. Re-run the same
`start` only after agreement to take it back.

Prefer logpoints; pausing freezes the JS thread, gestures, timers and network callbacks. Trigger
through a project-supported automation flow or a clearly described tap sequence. Only breakpoint
workflows need background `wait`. For startup probes, `dbg reload` works if the device advertises
native reload support; otherwise follow `RELOAD_UNSUPPORTED` instructions, usually Metro's `r`
or the dev menu. Probes rebind when the new bundle is parsed.

Conditions and logpoints use URL bindings because Hermes ignores conditions on script-id
breakpoints. Generated `.js` URL-mode probes are unsupported because Hermes lacks regex URL
breakpoints; use repo source paths. Hermes bundle `endLine` is unreliable, so stale-map line-count
checks are disabled; maintain checkout/build consistency.

Calling probed app code from `eval` can pause inside the evaluation. Schedule the call after it
returns, such as `dbg eval 'setTimeout(() => { /* synchronous app call */ }, 0)'`, then inspect
logs or await the breakpoint. Do not invoke async work while paused.

`dbg stop` releases the device for other debuggers. Optional historical session:
[Incyt mobile](../examples/incyt-rn.md).
