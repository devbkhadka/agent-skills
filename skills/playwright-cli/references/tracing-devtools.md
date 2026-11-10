# Tracing, Video Recording, and DevTools Diagnostics

Use this workflow to capture end-to-end execution traces, record video demonstrations with visual callout annotations, and inspect runtime console diagnostics with `playwright-cli`.

---

## 1. Tracing Execution (`tracing-start` & `tracing-stop`)

Traces record full DOM snapshots before/after each action, network request waterfalls, console logs, and visual frames for post-mortem debugging.

### Recording a Trace
```bash
# Start trace recording
playwright-cli tracing-start

# Execute exploratory or test actions
playwright-cli open https://app.example.com
playwright-cli click e2
playwright-cli fill e5 "Search term"

# Stop recording and save trace archive (.playwright-cli/traces/)
playwright-cli tracing-stop
```

---

## 2. Video Recording & Visual Callouts

Capture smooth WebM video walkthroughs with automatic or custom action callouts and chapter cards.

### Basic Recording with Chapters
```bash
# Open browser and start recording
playwright-cli open https://example.com
playwright-cli video-start recordings/feature-demo.webm

# Add chapter transitions
playwright-cli video-chapter "Overview" --description="Exploring dashboard features" --duration=2000

# Enable automated action callouts (highlights target elements as you click/type)
playwright-cli video-show-actions --duration=800 --position=top-right

# Perform actions...
playwright-cli click e4
playwright-cli fill e7 "Test data"

# Stop recording
playwright-cli video-hide-actions
playwright-cli video-stop
```

### Custom Screencast Overlays via `run-code`
```javascript
// Run via: playwright-cli run-code --filename=demo.js
async page => {
  await page.screencast.start({ path: 'recordings/custom-tour.webm', size: { width: 1280, height: 720 } });
  await page.goto('https://app.example.com');

  // Display chapter card
  await page.screencast.showChapter('User Profile Setup', { duration: 2000 });

  // Add sticky custom HTML overlay banner (pointer-events: none)
  const banner = await page.screencast.showOverlay(`
    <div style="position: absolute; top: 12px; right: 12px; padding: 8px 16px;
                background: rgba(30, 41, 59, 0.9); color: white; border-radius: 8px; font-size: 14px;">
      ✓ Automated Test Execution
    </div>
  `);

  await page.getByRole('button', { name: 'Edit Profile' }).click();
  await banner.dispose();

  await page.screencast.stop();
}
```

---

## 3. Console Logs & Element Highlighting

```bash
# Inspect console messages emitted by the web page
playwright-cli console
playwright-cli console warning
playwright-cli console error

# Add live visual highlight to an element on screen
playwright-cli highlight e5
playwright-cli highlight e5 --style="outline: 3px dashed #ef4444"

# Remove element highlights
playwright-cli highlight e5 --hide
playwright-cli highlight --hide
```
