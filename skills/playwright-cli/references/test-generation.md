# Test Generation and Self-Healing (Plan → Generate → Heal)

Use this workflow to plan test scenarios, generate robust Playwright TypeScript tests from interactive exploratory sessions, and heal broken test suites.

---

## 1. How Test Generation Works

Every action performed in `playwright-cli` automatically emits the equivalent Playwright TypeScript code to stdout. This generated code forms the direct building blocks for test files.

### Interactive Exploration Example

```bash
# Open the target page
playwright-cli open https://example.com/checkout

# Capture the snapshot to retrieve element refs
playwright-cli snapshot
# Output includes: e1 [textbox "Email"], e2 [textbox "Address"], e3 [button "Place Order"]

# Fill inputs - CLI outputs corresponding TypeScript
playwright-cli fill e1 "customer@example.com"
# Generated: await page.getByRole('textbox', { name: 'Email' }).fill('customer@example.com');

playwright-cli fill e2 "123 Market Street"
# Generated: await page.getByRole('textbox', { name: 'Address' }).fill('123 Market Street');

playwright-cli click e3
# Generated: await page.getByRole('button', { name: 'Place Order' }).click();
```

---

## 2. Structuring Test Files

Assemble the generated actions into a structured test file using `@playwright/test`:

```typescript
import { test, expect } from '@playwright/test';

test.describe('Checkout Flow', () => {
  test('successfully places an order', async ({ page }) => {
    // 1. Navigation
    await page.goto('https://example.com/checkout');

    // 2. Actions (collected from CLI session)
    await page.getByRole('textbox', { name: 'Email' }).fill('customer@example.com');
    await page.getByRole('textbox', { name: 'Address' }).fill('123 Market Street');
    await page.getByRole('button', { name: 'Place Order' }).click();

    // 3. Assertions
    await expect(page).toHaveURL(/.*order-confirmation/);
    await expect(page.getByRole('heading', { name: 'Thank you for your order' })).toBeVisible();
  });
});
```

---

## 3. Best Practices for Resilient Locators

| Priority | Locator Type | Example | When to Use |
| :--- | :--- | :--- | :--- |
| **1 (Best)** | Role locator | `page.getByRole('button', { name: 'Save' })` | Standard UI components (buttons, links, headings, checkboxes) |
| **2** | Label locator | `page.getByLabel('Password')` | Form inputs with explicit `<label>` or `aria-label` |
| **3** | Test ID | `page.getByTestId('cart-summary')` | Complex custom components where semantic roles are unavailable |
| **4** | Text locator | `page.getByText('Order #12345')` | Static informational text (avoid for buttons/inputs) |
| **Avoid** | Brittle CSS/XPath | `page.locator('div > div.col-md-4 > button:nth-child(2)')` | **Never use in generated tests.** Breaks on minor layout changes |

---

## 4. Crafting Assertions

Actions are recorded automatically, but assertions must be added explicitly:

```typescript
// Visibility
await expect(page.getByRole('alert')).toBeVisible();

// Text Content
await expect(page.getByTestId('order-status')).toHaveText('Shipped');

// Input Value
await expect(page.getByLabel('Quantity')).toHaveValue('2');

// Checkbox / Radio State
await expect(page.getByRole('checkbox', { name: 'Accept Terms' })).toBeChecked();

// Partial Accessibility Snapshot Match
await expect(page.getByTestId('shopping-cart')).toMatchAriaSnapshot(`
  - list:
    - listitem: 1x Wireless Mouse
    - listitem: 2x USB-C Cables
  - text: Total: $45.00
`);
```

---

## 5. Test Healing Workflow

When an existing test fails due to UI updates:

1. **Reproduce with Debug Mode**:
   ```bash
   PLAYWRIGHT_HTML_OPEN=never npx playwright test <test-file> --debug=cli
   ```
2. **Attach to Paused Session**:
   ```bash
   playwright-cli attach <session-id>
   ```
3. **Inspect the New DOM / State**:
   ```bash
   playwright-cli snapshot
   ```
4. **Generate New Locator**:
   ```bash
   playwright-cli generate-locator <ref> --raw
   ```
5. **Update Test Code & Verify**:
   Replace the obsolete locator in the test and re-run.
