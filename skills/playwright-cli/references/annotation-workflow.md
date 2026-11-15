# Live Element Annotation & Component-to-Code Mapping

Use this workflow when the user wants to point at parts of a running page and leave feedback: UI/UX reviews, visual defects, design tweaks, "change this one". The user selects real DOM elements in a headed browser, comments on each, and presses **Submit**. You receive the comments as structured JSON on stdout — element selector, Playwright locator, box, and the React component/source when the dev build exposes it. No images are produced.

---

## 1. Lifecycle

```
[1. Open headed browser]
       │
[2. Run element-annotator.js] ── blocks ──> user toggles Select, clicks elements, writes comments, presses Submit
       │
[3. Read JSON from stdout] ──> one entry per comment: note + selector + locator + box + component/source
       │
[4. Edit code] ──> jump to `source`, or grep `component`
       │
[5. Verify] ──> re-check the element by `locator`, then close the session
```

---

## 2. Start a Review

```bash
playwright-cli open <url> --headed            # add --mobile or --device="iPhone 15" for responsive reviews
playwright-cli --raw run-code --filename="$S/element-annotator.js"
```

The `run-code` call **blocks** until the user presses **Submit** or closes the page, then prints JSON and exits 0. Other `playwright-cli` commands keep working against the same session while it blocks. In agent fleets, run it as a background job and wait for its exit instead of polling.

Tell the user how to drive it:

| Control | Effect |
| :--- | :--- |
| **Select** switch (bottom-right) or **Alt+S** | Toggle selection mode. Starts **on**. |
| Hover / click (selection on) | Hovered element is outlined; click opens a comment box anchored to it. The page does not receive the click. |
| **Cmd/Ctrl+Enter** / **Esc** | Save / cancel the comment box. |
| Numbered pin | Edit or delete that comment. Pins stay visible in both modes. |
| **Submit** | Finish and hand all comments back. |

With selection **off** the page behaves exactly as normal (links navigate, buttons fire, inputs accept text), so the user can reach deeper views, then switch selection back on. Toggle state survives SPA route changes, reloads, and full navigations; the overlay re-injects itself on every new document. Pins show only on the page where their comment was made.

---

## 3. Output

```json
{
  "status": "submitted",
  "count": 2,
  "annotations": [
    {
      "id": 1,
      "note": "Rename to Save",
      "selector": "[data-testid=\"save-btn\"]",
      "locator": "getByTestId('save-btn')",
      "testId": "save-btn",
      "tag": "button",
      "text": "Save changes",
      "box": { "x": 161, "y": 128, "w": 100, "h": 21 },
      "url": "http://localhost:5173/settings",
      "component": "SaveButton",
      "source": "src/features/settings/SaveButton.tsx:12",
      "componentStack": ["SaveButton", "SettingsForm", "SettingsPage"]
    }
  ]
}
```

| Field | Meaning |
| :--- | :--- |
| `status` | `submitted` (Submit pressed) or `closed` (page/window closed; comments made so far are still returned). |
| `selector` | Unique CSS path, anchored on the nearest unique `id` or test id. Usable directly as a `playwright-cli` target. |
| `locator` | Playwright's own best-practice locator (`locator.normalize()`): role, test id, label, or text. Prefer it in tests. |
| `box` | Viewport-relative `getBoundingClientRect()` at selection time, CSS px. |
| `component` / `componentStack` | Nearest React components (innermost first). Present only on React dev builds. |
| `source` | JSX location of the element. React ≤18: `_debugSource` file:line. React 19: first app frame of `_debugStack` (served module URL:line:col). Bundler transforms can shift the line, so treat `component` as the reliable key. |

With no comments the command prints `{"status":"closed"|"submitted","message":"No annotations","annotations":[]}` and still exits 0. Read the stdout JSON only; never load page snapshots to "see" what the user meant.

---

## 4. Map Comments to Code

1. **`source` present**: open that file at that line. For a React 19 URL such as `http://localhost:5173/src/Card.tsx:44:21`, the path after the origin is the repo-relative file.
2. **Only `component`**: search for its definition, e.g. regex `export (default )?(const|function) SaveButton\b`.
3. **No React info**: search the codebase for `testId`, then for distinctive `text`.
4. Check how the parent (next entry in `componentStack`) wires props and handlers before editing.
5. Edit only the identified component or its direct parent.

---

## 5. Verify and Clean Up

```bash
playwright-cli eval "el => getComputedStyle(el).backgroundColor" "<selector>"   # assert the changed property
playwright-cli close
```

For a follow-up round, run the annotator again; each run starts with an empty comment list.

---

## 6. Limits

- **Iframes**: only the top-level document is annotatable; clicks inside an iframe are not captured.
- **Shadow DOM targets**: selection stops at the shadow host (events are retargeted), so a comment on a web component's internals is recorded against its host element.
- **Cross-origin navigation**: comments persist (they live in the CLI process), but toggle state is per-origin `sessionStorage` and resets to on after crossing origins.
- **Single tab**: only the page that was current when the command started is annotated; popups and new tabs are not.
