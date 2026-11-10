# Hybrid Debugging Workflow (Human + Agent)

Use this workflow when you want to visually set breakpoints in your Antigravity IDE editor, let the agent automate the tedious login and setup navigation via Playwright, and collaborate with the agent to diagnose runtime bugs.

---

## 1. The 4-Step Collaborative Loop

```
[1. Developer sets Breakpoints in IDE] ──> Red gutter breakpoints in .tsx/.ts files
                 │
[2. Developer instructs Agent]         ──> "Log in and open checkout step 2"
                 │
[3. Agent pilots Playwright]           ──> Fast navigation with port 9222 active
                 │
[4. Developer attaches IDE Debugger]   ──> Press F5 ("Attach to Browser App")
                 │
[5. Trigger & Collaborate]             ──> Pauses at breakpoint; Agent analyzes scope
```

---

## 2. Step-by-Step Instructions

### Step 1: Set Visual Breakpoints in Antigravity IDE
Open your TypeScript / React source files (e.g. `src/features/cart/applyCoupon.ts:28`) and click the gutter to place a red breakpoint.

### Step 2: Ask the Agent to Prepare the Session
Prompt the agent:
> *"Launch the app and navigate to `http://localhost:3000/cart` using the debug launcher so I can attach the debugger."*

The agent executes the launcher:
```bash
node scripts/launch-and-hold.mjs http://localhost:3000/cart --auth=.playwright-cli/auth.json
```

### Step 3: Attach the Debugger
1. In Antigravity IDE / VS Code, switch to the **Run & Debug** tab (<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>D</kbd> / <kbd>⌘</kbd>+<kbd>Shift</kbd>+<kbd>D</kbd>).
2. Select **"Attach to Browser App"** and press <kbd>F5</kbd>.
3. Your breakpoints will bind (solid red dots).

### Step 4: Trigger and Inspect
1. Click the UI element in the open browser (or ask the agent to trigger the action via Playwright).
2. The browser immediately pauses execution, focusing the exact line in Antigravity IDE.
3. Ask the agent in the sidebar chat:
   > *"I'm paused at line 28 in `applyCoupon.ts`. Look at the `couponResponse` and `discountRate` in local scope and tell me why the calculation overflows."*
4. The agent inspects the code, explains the failure, and offers a verified fix.
