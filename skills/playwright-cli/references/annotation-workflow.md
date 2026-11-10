# Playwright Visual Annotation & Component-to-Code Mapping

Use this workflow when collaborating interactively with the user on UI/UX reviews, visual defect debugging, design feedback, or rapid feature tweaks via `playwright-cli`.

---

## 1. End-to-End Workflow Lifecycle

```
[1. Open Headed Browser] 
       │
[2. Launch Annotation Dashboard] ──> User draws boxes & notes ──> User clicks Submit
       │
[3. Read Annotation Output] ──> Extract (x, y, w, h) bounding boxes (Tier 1: ~150 tokens)
       │
[4. React Fiber / DOM Probe] ──> Identify Component & Source File (Tier 2: ~80 tokens)
       │
[5. Targeted Code Modification] ──> Edit source component & props wiring
       │
[6. Visual Verification] ──> Targeted screenshot diff & cleanup
```

---

## 2. Launching the Interactive Session

### Step 1: Open Headed Browser
Always open with `--headed` so the user has full visual visibility and control over mouse/keyboard interaction:

```bash
# Launch headed browser
playwright-cli open <url> --headed --browser=chrome
```

> [!TIP]
> If testing a mobile viewport or responsive layout, pass `--mobile` or `--device="iPhone 15"`:
> ```bash
> playwright-cli open <url> --headed --mobile
> ```

### Step 2: Launch Annotation Dashboard
Run the annotation dashboard:

```bash
playwright-cli show --annotate
```

> [!IMPORTANT]
> The annotation dashboard is a blocking interactive tool. It waits until the user draws one or more bounding boxes, writes review notes, and clicks **Submit** (or closes the window).
> When running inside agent fleets or asynchronous runners, listen for completion notifications rather than polling in a tight loop.

---

## 3. Token Optimization Strategy

Indiscriminately loading full DOM dumps or large high-resolution screenshots into LLM context will quickly consume 20,000–60,000 tokens per interaction. Use this strictly tiered approach:

| Tier | Artifact | Token Cost | When to Use | Action |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1 (Default)** | CLI Output Summary | **~100–200 tokens** | Immediate after submit | Extract `{ x, y, width, height }` coordinates, note text, and active URL from the CLI stdout. |
| **Tier 2 (Targeted)** | React Fiber / DOM Probe | **~50–100 tokens** | Locating component code | Evaluate the element at the center of the bounding box to extract component names, source files, and test IDs. |
| **Tier 3 (Visual)** | Cropped / Annotated PNG | **~1,000–1,500 tokens** | Complex visual layout/iconography | Use `view_file` on `annotations-*.png` only if visual context cannot be resolved via component hierarchy. |
| **Tier 4 (Forbidden)** | Full `.yaml` DOM Snapshot | **~15,000–50,000+ tokens** | **Avoid** | **Do not read raw YAML snapshots into context.** They dump whole DOM trees without adding value. |

---

## 4. React Fiber & Component-to-Code Mapping

Modern React development servers (Vite, Webpack, Next.js, CRA) attach internal Fiber nodes to DOM elements. Probing the DOM element at the annotated coordinates reveals the exact component name, parent container, and source file location (`_debugSource` / `_debugOwner`).

### Option A: Using the Fiber Probe Script (Recommended)

Run the bundled probe helper located in `scripts/probe-fiber.js`:

```bash
# Evaluate at annotation coordinates (e.g. x: 340, y: 220, width: 120, height: 40)
playwright-cli eval "($(< scripts/probe-fiber.js))({ x: 340, y: 220, width: 120, height: 40 })"
```

### Option B: Inline CLI Evaluation

If scripts are not in your working directory, execute this compact snippet:

```bash
playwright-cli eval '
(() => {
  const x = <TARGET_X> + (<TARGET_WIDTH> || 0) / 2;
  const y = <TARGET_Y> + (<TARGET_HEIGHT> || 0) / 2;
  const el = document.elementFromPoint(x, y);
  if (!el) return { error: "No element found at point" };

  const fiberKey = Object.keys(el).find(k => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$"));
  if (!fiberKey) return { tag: el.tagName, testId: el.getAttribute("data-testid"), id: el.id, className: el.className };

  let fiber = el[fiberKey];
  const hierarchy = [];
  while (fiber && hierarchy.length < 8) {
    const name = fiber.type?.displayName || fiber.type?.name || (typeof fiber.type === "string" ? fiber.type : null);
    const source = fiber._debugSource ? `${fiber._debugSource.fileName}:${fiber._debugSource.lineNumber}` : null;
    const owner = fiber._debugOwner?.type?.displayName || fiber._debugOwner?.type?.name || null;
    if (name && !["div", "span", "p", "button", "section", "main"].includes(name.toLowerCase())) {
      hierarchy.push({ name, source, owner });
    }
    fiber = fiber.return;
  }
  return {
    element: el.tagName.toLowerCase(),
    testId: el.getAttribute("data-testid") || el.getAttribute("data-cy"),
    hierarchy
  };
})()
'
```

### Probe Output Example

```json
{
  "point": { "x": 340, "y": 220 },
  "element": {
    "tagName": "button",
    "testId": "submit-checkout-btn",
    "selector": "[data-testid=\"submit-checkout-btn\"]"
  },
  "framework": "react",
  "reactHierarchy": [
    {
      "name": "CheckoutSubmitButton",
      "source": "apps/web/src/features/checkout/components/CheckoutSubmitButton.tsx:28"
    },
    {
      "name": "CheckoutForm",
      "source": "apps/web/src/features/checkout/CheckoutForm.tsx:142",
      "owner": "CheckoutPage"
    },
    {
      "name": "CheckoutPage",
      "source": "apps/web/src/pages/checkout.tsx:45"
    }
  ]
}
```

---

## 5. Navigating and Modifying Code

With the component hierarchy and source files in hand:

1. **Direct File Navigation**: Jump straight to the identified file and line (e.g. `apps/web/src/features/checkout/components/CheckoutSubmitButton.tsx:28`).
2. **Symbol Search (if source path is obfuscated/relative)**:
   Use `grep_search` with exact component export signatures:
   ```json
   { "Query": "export (const|function) CheckoutSubmitButton", "IsRegex": true }
   ```
3. **Trace Upstream Props**:
   Inspect the parent component (`CheckoutForm.tsx`) to check how state, handlers, and design props are wired down.
4. **Targeted Edits**: Modify only the identified component or its immediate parent container. Avoid sweeping codebase refactors.

---

## 6. Verification and Visual Diffing

After modifying the code:

1. **Targeted Screenshot Capture**:
   ```bash
   playwright-cli screenshot --filename=.playwright-cli/verify-fix.png
   ```
2. **Inspect Screenshot**:
   Use `view_file` on `.playwright-cli/verify-fix.png` to confirm that the reported visual defect or layout misalignment has been fixed.
3. **Cleanup**:
   ```bash
   # Close the browser session when done
   playwright-cli close
   ```
4. **Git Hygiene**:
   Ensure `.playwright-cli/` is listed in `.gitignore` so temporary review screenshots and snapshots are never committed to git history.
