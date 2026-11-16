---
name: mitmproxy-api-testing
description: Test HTTP API failure handling with mitmdump and a small Python addon. Use for matching requests, injecting errors, delays or dropped flows, and inspecting captures, including HTTPS from an iOS simulator.
---

# API testing with mitmproxy

Use a test account and a narrow request match. Establish the expected UI, credential retention, retry behavior, and telemetry before injecting a fault. Setup commands below are for an authorized test run; a planning request does not authorize running them.

## Prepare the iOS simulator

Prerequisite: mitmproxy is installed; the scripts report missing binaries.

Set `MITM_SKILL_DIR` to this skill's absolute directory and run [scripts/prepare.sh](scripts/prepare.sh) with the installed app's bundle identifier (add `--udid SIMULATOR_UDID` before it to select a booted device):

```bash
bash "$MITM_SKILL_DIR/scripts/prepare.sh" --udid SIMULATOR_UDID APP_BUNDLE_ID
```

It stops at the first failure: checks mitmdump, reuses or creates the shared CA without a listener, checks the booted simulator, trusts the root, and resolves `CFBundleExecutable` through `xcrun simctl get_app_container`. Copy its printed assignments into the capture shell: `SIMULATOR_UDID` identifies the device, `SIM_APP_BUNDLE_ID` the app, `SIM_APP_NAME` the capture process, and `MITMPROXY_CONFDIR` the shared CA folder. If using a non-default confdir, export that same value for capture. The default `~/.mitmproxy` survives projects, worktrees and cleanup; keep it outside repositories. [setup-cert.sh](scripts/setup-cert.sh) remains usable alone for certificate/trust setup.

Launch the target app. Local capture selects its executable name across relaunches; keep other apps with that name stopped, including on other simulators. Start [scripts/start-capture.sh](scripts/start-capture.sh) with the printed name (`--ui` selects mitmproxy):

```bash
bash "$MITM_SKILL_DIR/scripts/start-capture.sh" "$SIM_APP_NAME"
```

The first [local capture](https://docs.mitmproxy.org/stable/concepts/modes/) needs one-time macOS approval of the signed **Mitmproxy Redirector** network extension; follow the system prompt and retry if requested. This is separate from simulator root trust. Keep Mac proxy settings unchanged. Before faults, prove target HTTPS succeeds, the simulator request appears, and an independent host request does not. Stop if helpers or duplicate names prevent isolation. Use a separate private `MITM_RUN` directory only for optional recordings.

## Start the addon and inject one case

The bundled [scripts/api_cases.py](scripts/api_cases.py) defaults to pass-through; an empty hostname disables matching. Configure exact host, method, and path (query excluded), then optional `X-Amz-Target` and a top-level JSON string field. It does not inspect or log credential values. All enabled match conditions must hold.

```bash
bash "$MITM_SKILL_DIR/scripts/start-capture.sh" "$SIM_APP_NAME" "$MITM_SKILL_DIR/scripts/api_cases.py" -- --set case=error --set case_host=api.example.test --set case_method=POST --set case_path=/session --set case_status=503
```

Replace the reserved example host with the test endpoint. `connection_strategy=lazy` permits a local fake response without first contacting upstream. The [options reference](https://docs.mitmproxy.org/stable/concepts/options/) describes this and the capture options.

Pass additional mitmproxy arguments after `--`; keep mode and confdir under the script's control. Restart with different `--set` arguments to switch cases. Restarts reset the injection counter; retain the shared CA across runs.

| Option | Behavior |
| --- | --- |
| `case=pass` | Observe matches, forward normally. |
| `case=error` | Return `case_status` and a JSON message before forwarding. `case_error` adds an AWS JSON `__type` and matching error header; `case_body` replaces the full JSON body for another API protocol. |
| `case=delay` | Hold a matched request asynchronously for `case_seconds`, then forward it. |
| `case=timeout` | Hold for `case_seconds`, then kill without an HTTP response. Set duration beyond the measured client timeout and confirm the client actually times out first. |
| `case=kill` | Kill before forwarding, without an HTTP error response. This tests a transport interruption, not a precise TCP reset or radio outage. |
| `case_limit=1` | Default: inject once, subsequent matches pass. Use `0` to fault every retry until the proxy is stopped/restarted. |
| `case_target`, `case_field`, `case_value` | Optional operation and JSON field match, e.g. distinguish an auth refresh from sign-in at the same URL. |

Use `http.Response.make`, an async `request` hook, and `flow.kill()` as in the [addon examples](https://docs.mitmproxy.org/stable/addons/examples/) and [Flow API](https://docs.mitmproxy.org/stable/api/mitmproxy/flow.html). A synthetic 504 is an HTTP response and does not test a client timeout. Keep faults active across SDK retries when testing persistent failure.

## Read evidence

The addon writes only flow IDs, scenario, timestamp, response status, and transport-error markers. Correlate them with the app's UI and telemetry. For raw flow recording, add `-w "$MITM_RUN/case.mitm"` to the capture command; recordings contain request bodies, credentials and tokens. Keep them private and out of source control. Do not paste raw flows into chat or reports.

Read a recording without opening a capture listener:

```bash
mitmdump -n -r "$MITM_RUN/case.mitm" --set confdir="${MITMPROXY_CONFDIR:-$HOME/.mitmproxy}" --set flow_detail=1
```

Use a narrow filter and `flow_detail=2` for headers or `4` for complete bodies only in a private terminal. Those levels can expose secrets. `-r` reads saved traffic; it does not replay it. See the [mitmdump guide](https://docs.mitmproxy.org/stable/).

## Cleanup checklist

- Stop mitmdump with Ctrl-C, then prove target HTTPS works directly and no test proxy/helper remains active.
- Retain the shared `~/.mitmproxy` folder (or override), its CA/private key, simulator root trust, and reusable mitmproxy installation. Do not remove or regenerate the CA between projects or worktrees. Avoid `simctl keychain reset` and simulator erasure.
- Keep the approved Mitmproxy Redirector extension available for future local capture. Record its state and any pending approval/restart requirement.
- Remove only optional run-specific recordings and scratch addons after collecting sanitized evidence; preserve other captures and tools.
- Confirm the Mac system proxy settings match the baseline. Report retained shared setup and any incomplete cleanup explicitly.
