> Optional historical project example, recorded 2026-10-03. Commands, environment assumptions and abbreviated outputs are project-specific. Use the current [command contract](../references/commands.md); paginate logs with `nextSeq`, not historical `lastSeq` examples.

# React Native targets (Hermes via Metro)

`--kind hermes` attaches to one React Native **device page** through Metro's inspector proxy and
maps the Metro bundle back to repo files. Verified live (2026-10-03) on LX_MobileApp — RN 0.83.1,
Hermes, Metro 0.83 — on an iOS simulator (iPhone 17 Pro Max). JS only: native Swift/Kotlin code
(lldb, Xcode, Android Studio) is out of scope.

`dbg` below means `node "$JS_DEBUGGER_SKILL_DIR/scripts/dbg.mjs"` (see ../SKILL.md).

## 1. Prerequisites

- Metro running in the app repo (`yarn start` in LX_MobileApp, `:8081`) and a **debug** build of the
  app open on a simulator/emulator/device. Release builds are not debuggable.
- Physical Android device: `adb reverse tcp:8081 tcp:8081` so the app reaches Metro.

## 2. Discover and attach

```bash
dbg targets --kind hermes
# {"targets":[{"kind":"hermes","host":"127.0.0.1","port":8081,"id":"…","title":"…",
#              "description":"…","deviceName":"iPhone 17 Pro Max","appId":"…","modern":true}, …]}

dbg start --kind hermes --metro-root /abs/path/to/LX_MobileApp --cwd /abs/path/to/LX_MobileApp \
  [--target 'iPhone 17']                  # regex on title/description/deviceName/appId
```

- Metro's `/json/list` has **two pages per device**. The debuggable one is
  "React Native Bridgeless [C++ connection]" (`reactNative.capabilities.prefersFuseboxFrontend`,
  shown as `"modern": true`); the CLI picks it automatically. Use `--target` only to choose a device.
- `--metro-root` = Metro `projectRoot`; `--watch-folders a,b` = Metro `watchFolders` (in config
  order) when the app pulls code from outside its root. Map sources look like `/[metro-project]/…`
  and `/[metro-watchFolders]/<N>/…`; these flags turn them into repo paths. Metro maps carry no
  `sourcesContent`, so excerpts come from disk — the checkout must match what Metro is serving.
- First map load of the index bundle (≈11.9k sources) takes 2-3 s; lazily loaded bundles have their
  own maps and load when they are parsed.

## 3. One debugger per device — displacement

Hermes allows **one** debugger connection per device. Attaching `dbg` kicks an open React Native
DevTools window (Metro `j`) or any other debugger, and opening one of those kicks `dbg`.

- Tell the user before attaching if they have DevTools open.
- When kicked, the session goes `"state": "displaced"` (`dbg status`) and calls fail with
  `DISPLACED`. The daemon never reconnects on its own — do not fight the other debugger. Ask the
  user; if they agree, re-run the same `dbg start` (`"reattached": true`) to take the device back.

## 4. Probing

- **Logpoints strongly preferred.** A pause freezes the JS thread: UI, gestures, timers and network
  callbacks stop until `continue`; the human sees a hung app.
- **Startup code** (module top level, app init): set probes, then `dbg reload` (CDP `Page.reload`;
  works when the app reports `nativePageReloads`). On `RELOAD_UNSUPPORTED`, press `r` in the Metro
  terminal (or reload from the dev menu). Probes re-bind when the new bundle is parsed.
- Hermes quirks, handled internally — nothing to do, but explains behaviour:
  - conditions (`--if`, logpoints) are bound with `setBreakpointByUrl` because Hermes ignores
    conditions on scriptId breakpoints;
  - no regex URL breakpoints, so the `.js` generated-URL mode (probing the bundle by its own path)
    is unavailable — always use repo source paths;
  - Hermes reports bundle `endLine` 0, so the cheap stale-map line check is off; keep the checkout
    in sync with Metro.
- **Calling app code from `eval`.** Metro modules are reachable through `__r(<moduleId>)`. If the
  called code runs through a probed line, it would pause inside your own eval; wrap it so probes fire
  after the eval returns: `dbg eval 'setTimeout(() => { /* call app code */ }, 0)'`, then read
  `dbg logs` / `dbg wait`.

## 5. Triggering

- Agent-driven: Maestro flows in LX_MobileApp (`yarn e2e`, flows under `e2e-test/flows`).
- Human-driven: start `dbg wait --timeout 300` in the background (or just set logpoints) and tell
  the human exactly what to tap in the simulator.

## 6. Worked session (real run, 2026-10-03, iOS simulator)

```bash
dbg start --kind hermes --metro-root ~/projects/incyt/LX_MobileApp --cwd ~/projects/incyt/LX_MobileApp
# {"started":true,"attached":true,"kind":"hermes","inspector":"127.0.0.1:8081",…}

dbg logpoint App/common/colorUtils.ts:17 '{hex, cleanHex}'
# {"id":"1","kind":"logpoint","state":"bound",…}
# human navigates the app (or a Maestro flow runs)
dbg logs --since 0
# {"hits":[{"seq":1,…,"location":"App/common/colorUtils.ts:17","value":{"hex":"…","cleanHex":"…"}}, …],"lastSeq":…}

dbg break App/common/colorUtils.ts:30          # only when locals beyond the logpoint are needed
dbg wait --timeout 300                          # background; trigger the screen again
# {"paused":true,…,"frame":{"file":"App/common/colorUtils.ts","line":30,…},"scopes":[{"type":"local",…}]}
dbg continue                                    # promptly — the app is frozen while paused
dbg stop
```

`dbg stop` releases the device; React Native DevTools can connect again afterwards.
