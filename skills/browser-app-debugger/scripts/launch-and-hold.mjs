#!/usr/bin/env node

/**
 * Launch & Hold Helper for Playwright + DAP Debugging
 * 
 * Launches Chromium in headed mode with --remote-debugging-port=9222,
 * applies saved authentication state if provided, navigates to the target URL,
 * and holds the browser session open so Antigravity IDE or VS Code can attach
 * via DAP / js-debug.
 *
 * Usage:
 *   node launch-and-hold.mjs <url> [--auth=<auth-state.json>] [--port=9222]
 *
 * Example:
 *   node launch-and-hold.mjs http://localhost:3000/dashboard/orders/42 --auth=.playwright-cli/auth.json
 */

import { chromium } from 'playwright';

function parseArgs(args) {
  const options = { port: 9222, url: 'http://localhost:3000' };
  for (const arg of args) {
    if (arg.startsWith('--auth=')) {
      options.auth = arg.slice(7);
    } else if (arg.startsWith('--port=')) {
      options.port = parseInt(arg.slice(7), 10);
    } else if (!arg.startsWith('--')) {
      options.url = arg;
    }
  }
  return options;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));

  console.log(`[Browser App Debugger] Starting Chromium on port ${options.port}...`);

  const browser = await chromium.launch({
    headless: false,
    args: [`--remote-debugging-port=${options.port}`]
  });

  const contextOptions = {};
  if (options.auth) {
    console.log(`[Browser App Debugger] Injecting auth state from ${options.auth}...`);
    contextOptions.storageState = options.auth;
  }

  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();

  console.log(`[Browser App Debugger] Navigating to ${options.url}...`);
  await page.goto(options.url);

  console.log('\n===============================================================');
  console.log(`✅ Ready! Browser listening on remote debugging port ${options.port}`);
  console.log(`📍 Current URL: ${page.url()}`);
  console.log('👉 In Antigravity IDE / VS Code: Attach debugger (F5)');
  console.log('🛑 Press Ctrl+C in this terminal to close the browser when done.');
  console.log('===============================================================\n');

  // Keep alive until process termination
  await new Promise((resolve) => {
    process.on('SIGINT', async () => {
      console.log('\n[Browser App Debugger] Closing browser session...');
      await browser.close();
      resolve();
    });
    process.on('SIGTERM', async () => {
      await browser.close();
      resolve();
    });
  });
}

main().catch((err) => {
  console.error('[Browser App Debugger] Error:', err.message);
  process.exit(1);
});
