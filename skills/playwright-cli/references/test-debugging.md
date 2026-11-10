# Running and Debugging Playwright Tests

Use this workflow to run test suites, debug failing tests interactively without launching bloated GUIs, and inspect live browser state during test execution.

---

## 1. Running Playwright Tests

Always disable the automatic opening of interactive HTML reports in agent environments using the `PLAYWRIGHT_HTML_OPEN=never` environment variable:

```bash
# Run all tests headlessly
PLAYWRIGHT_HTML_OPEN=never npx playwright test

# Run a specific test file
PLAYWRIGHT_HTML_OPEN=never npx playwright test tests/auth.spec.ts

# Run tests matching a specific title grep pattern
PLAYWRIGHT_HTML_OPEN=never npx playwright test -g "successful login"

# Run tests in headed mode for visual observation
PLAYWRIGHT_HTML_OPEN=never npx playwright test --headed
```

---

## 2. Interactive Test Debugging via `--debug=cli`

When a test fails, do not guess selector fixes. Use Playwright's built-in `--debug=cli` runner to pause test execution at the start and attach an interactive `playwright-cli` session.

### Step 1: Start Test in Debug Mode
Launch the test command in the background:

```bash
PLAYWRIGHT_HTML_OPEN=never npx playwright test tests/checkout.spec.ts --debug=cli
```

### Step 2: Read Debugging Session Name
The test process pauses at the start and prints debugging instructions to stdout:
```
Debugging Instructions:
Run 'playwright-cli attach tw-8f3a1b' to interact with this test.
```

### Step 3: Attach via Playwright CLI
Attach to the paused test session:

```bash
playwright-cli attach tw-8f3a1b
```

---

## 3. Test Navigation & Stepping Commands

While attached to a paused test, step through test steps or run directly to specific breakpoints:

| Command | Action |
| :--- | :--- |
| `playwright-cli step-over` | Executes the next line/action in the test script. |
| `playwright-cli pause-at <location>` | Runs until the specified file and line number (e.g. `tests/checkout.spec.ts:45`). |
| `playwright-cli resume` | Continues normal test execution until completion or the next breakpoint. |
| `playwright-cli snapshot` | Captures current accessibility tree and element references (`e1`, `e2`, ...). |
| `playwright-cli detach` | Detaches the CLI from the test session without terminating the browser. |

---

## 4. Inspecting Elements & Testing Locators

When the test pauses at a failure point:

1. **Capture Current Snapshot**:
   ```bash
   playwright-cli snapshot
   ```
2. **Search for Elements**:
   ```bash
   playwright-cli find "Submit Order"
   ```
3. **Generate Resilient Locators**:
   Use `generate-locator` to produce clean Playwright TypeScript locator code:
   ```bash
   playwright-cli generate-locator e14 --raw
   # Output: page.getByRole('button', { name: 'Submit Order' })
   ```
4. **Highlight Element Live**:
   Verify the element on screen:
   ```bash
   playwright-cli highlight e14
   playwright-cli highlight --hide
   ```

---

## 5. Completing the Debug Cycle

1. Update the locator or assertion in the test file (e.g. `tests/checkout.spec.ts`).
2. Terminate or let the background test process exit.
3. Re-run the test to ensure it passes cleanly:
   ```bash
   PLAYWRIGHT_HTML_OPEN=never npx playwright test tests/checkout.spec.ts
   ```
