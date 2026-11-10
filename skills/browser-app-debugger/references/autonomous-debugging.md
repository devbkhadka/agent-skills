# Autonomous Agent Debugging Workflow

Use this workflow when the agent is operating independently to diagnose, reproduce, and fix runtime bugs in a browser application without human intervention.

---

## 1. Autonomous Lifecycle

```
[1. Identify Target Component & Line]
                 │
[2. Inject Non-Blocking Logpoint / Probe via CDP/DAP]
                 │
[3. Automate Reproduction via Playwright]
                 │
[4. Capture Scope Variables & Call Stack]
                 │
[5. Formulate Fix & Apply Source Edits]
                 │
[6. Re-run Verification Loop]
```

---

## 2. Setting Non-Blocking Logpoints

To prevent freezing the browser thread (which disconnects WebSockets and cancels async queries), the agent sets conditional logpoints or CDP probes instead of hard-pausing breakpoints:

```javascript
// Dynamic logpoint via CDPSession
const cdp = await page.context().newCDPSession(page);
await cdp.send('Debugger.enable');

// Injects non-blocking probe at target line
await cdp.send('Debugger.setBreakpointByUrl', {
  urlRegex: '.*calculateDiscount\\.ts',
  lineNumber: 34,
  condition: 'console.log("AGENT_DEBUG_PROBE:", JSON.stringify({ cartItems, discount, total })), false'
});
```

---

## 3. Capturing Runtime Logs & Scope

```bash
# Capture the injected probe output from console stream
playwright-cli console
# Output: AGENT_DEBUG_PROBE: {"cartItems":[{"price":20,"qty":2}],"discount":null,"total":NaN}
```

---

## 4. Remediation and Re-Verification

1. **Pinpoint Error**: Identify that `discount` is `null` instead of `0`, resulting in `40 - null = NaN` or invalid type conversion.
2. **Apply Patch**: Update `src/features/checkout/calculateDiscount.ts` with defensive fallback handling.
3. **Re-trigger**: Re-run the Playwright reproduction journey to verify `AGENT_DEBUG_PROBE` now logs clean values and the UI shows the correct calculated total.
