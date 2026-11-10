# Custom Playwright Script Execution & Page Automation

Use this workflow to execute custom JavaScript/TypeScript in the browser context via `playwright-cli run-code` or `eval` for advanced scenarios not covered by single CLI commands.

---

## 1. Syntax & Execution Model

### Inline Script Execution
```bash
playwright-cli run-code "async page => {
  // Access full Playwright Page and BrowserContext APIs
  const title = await page.title();
  return { title, url: page.url() };
}"
```

### Executing from File
```bash
playwright-cli run-code --filename=./scripts/my-automation.js
```

> [!NOTE]
> Code passed to `run-code` must be a single async function expression `async page => { ... }`. External ES module imports (`import ...`) are not supported directly inside inline snippets.

---

## 2. Browser Permissions & Geolocation

```bash
# Grant multiple permissions (clipboard, geolocation, notifications)
playwright-cli run-code "async page => {
  await page.context().grantPermissions(['geolocation', 'clipboard-read', 'notifications']);
  await page.context().setGeolocation({ latitude: 37.7749, longitude: -122.4194 }); // San Francisco
}"

# Clear permissions
playwright-cli run-code "async page => {
  await page.context().clearPermissions();
}"
```

---

## 3. Media & Color Scheme Emulation

```bash
# Dark mode emulation
playwright-cli run-code "async page => {
  await page.emulateMedia({ colorScheme: 'dark' });
}"

# Light mode & reduced motion
playwright-cli run-code "async page => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
}"

# Print media styles
playwright-cli run-code "async page => {
  await page.emulateMedia({ media: 'print' });
}"
```

---

## 4. Iframes, Downloads & Dialogs

### Working with Iframes
```bash
playwright-cli run-code "async page => {
  const frame = page.frameLocator('iframe#checkout-frame');
  await frame.getByRole('button', { name: 'Confirm' }).click();
}"
```

### File Downloads
```bash
playwright-cli run-code "async page => {
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  const download = await downloadPromise;
  await download.saveAs('./downloads/report.csv');
  return download.suggestedFilename();
}"
```

### Handling Alert / Confirm Dialogs
```bash
# Via CLI:
playwright-cli dialog-accept
playwright-cli dialog-accept "User prompt response"
playwright-cli dialog-dismiss

# Via run-code:
playwright-cli run-code "async page => {
  page.once('dialog', async dialog => {
    console.log('Dialog opened:', dialog.message());
    await dialog.accept();
  });
}"
```

---

## 5. Precise Wait Strategies

```bash
# Wait for network idle
playwright-cli run-code "async page => {
  await page.waitForLoadState('networkidle');
}"

# Wait for dynamic DOM element or state
playwright-cli run-code "async page => {
  await page.locator('.loading-spinner').waitFor({ state: 'hidden', timeout: 10000 });
}"

# Wait for client-side window condition
playwright-cli run-code "async page => {
  await page.waitForFunction(() => window.__APP_INITIALIZED__ === true);
}"
```
