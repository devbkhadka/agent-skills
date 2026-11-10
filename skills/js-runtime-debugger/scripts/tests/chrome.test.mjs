/**
 * End-to-end behavior of `dbg --kind chrome` against a real headless Chromium page served by a real
 * Vite dev server (no CDP mocking).
 *
 * Fixture: tests/fixtures/web — `root/` is the Vite root (TS modules served as /src/...),
 * `shared/` lives outside it (served as /@fs/<abs>), `root/src/lazy/` is dynamically imported.
 * Breakpoints are given as repo paths relative to fixtures/web (`root/src/app/counter.ts:3`), the
 * way a monorepo user would write `apps/dashboard/src/...`.
 *
 * Skips (never fails) when Vite or a Playwright Chromium build is not installed locally.
 */

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readdirSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, test } from 'node:test';

import { alive, eventually, freePort, killProcess, runDbg } from './helpers.mjs';

const WEB_DIR = path.join(import.meta.dirname, 'fixtures', 'web');
const VITE_ROOT = path.join(WEB_DIR, 'root');
const VITE_ENTRY = process.env.VITE_ENTRY || (existsSync('node_modules/vite/dist/node/index.js') ? 'node_modules/vite/dist/node/index.js' : '');
const PLAYWRIGHT_CACHE = path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright');

/** Newest Playwright "Chrome for Testing" binary, or null. */
function findChromium() {
  if (!existsSync(PLAYWRIGHT_CACHE)) return null;
  const builds = readdirSync(PLAYWRIGHT_CACHE)
    .map((name) => /^chromium-(\d+)$/.exec(name))
    .filter((m) => m !== null)
    .sort((a, b) => Number(b[1]) - Number(a[1]));
  for (const build of builds) {
    const dir = path.join(PLAYWRIGHT_CACHE, build[0]);
    for (const platform of readdirSync(dir).filter((n) => n.startsWith('chrome-mac'))) {
      const bin = path.join(dir, platform, 'Google Chrome for Testing.app', 'Contents', 'MacOS', 'Google Chrome for Testing');
      if (existsSync(bin)) return bin;
    }
  }
  return null;
}

const CHROME = findChromium();
const SKIP = !existsSync(VITE_ENTRY)
  ? `vite not found at ${VITE_ENTRY}`
  : !CHROME
    ? `no Playwright Chromium under ${PLAYWRIGHT_CACHE}/chromium-*/chrome-mac*`
    : false;

/** @param {any} snapshot */
const localsOf = (snapshot) => snapshot.scopes.find((/** @type {any} */ s) => s.type === 'local').variables;

describe('dbg against a Chrome page served by Vite', { skip: SKIP, timeout: 30_000 }, () => {
  /** @type {string} */ let home;
  /** @type {string} */ let profileDir;
  /** @type {number} */ let cdpPort;
  /** @type {any} */ let vite = null;
  /** @type {import('node:child_process').ChildProcess | null} */ let chrome = null;
  /** @type {number | null} */ let daemonPid = null;
  const run = (/** @type {string[]} */ ...args) => runDbg({ home, cwd: WEB_DIR }, args);
  /** Page titles from the browser's target list (no debugger involved). */
  const pageTitles = async () => {
    const list = await fetch(`http://127.0.0.1:${cdpPort}/json/list`).then((r) => r.json());
    return list.filter((/** @type {any} */ t) => t.type === 'page').map((/** @type {any} */ t) => t.title);
  };

  before(async () => {
    home = mkdtempSync(path.join(os.tmpdir(), 'dbg-'));
    profileDir = mkdtempSync(path.join(os.tmpdir(), 'dbg-chrome-'));
    const { createServer } = await import(VITE_ENTRY);
    vite = await createServer({
      root: VITE_ROOT,
      configFile: false,
      logLevel: 'silent',
      cacheDir: path.join(profileDir, 'vite-cache'),
      optimizeDeps: { noDiscovery: true, include: [] },
      server: { host: '127.0.0.1', port: await freePort(), strictPort: true, hmr: false, fs: { allow: [WEB_DIR] } },
    });
    await vite.listen();
    const appUrl = `http://127.0.0.1:${vite.config.server.port}/`;

    cdpPort = await freePort();
    chrome = spawn(
      /** @type {string} */ (CHROME),
      [
        `--remote-debugging-port=${cdpPort}`,
        `--user-data-dir=${profileDir}`,
        '--headless=new',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-extensions',
        appUrl,
      ],
      { stdio: 'ignore' },
    );
    await eventually(async () => (await pageTitles()).includes('fixture 12'), 15_000, 'fixture page to boot');
  });

  after(async () => {
    await run('stop');
    if (daemonPid !== null && alive(daemonPid)) process.kill(daemonPid, 'SIGKILL');
    await killProcess(chrome);
    await vite?.close();
    rmSync(home, { recursive: true, force: true });
    rmSync(profileDir, { recursive: true, force: true });
  });

  test('start attaches to the page selected by --target', async () => {
    const started = await run('start', '--kind', 'chrome', '--port', String(cdpPort), '--target', '^fixture', '--web-root', 'root');
    assert.equal(started.code, 0, JSON.stringify(started.json));
    assert.equal(started.json.attached, true);
    assert.equal(started.json.kind, 'chrome');
    assert.equal(started.json.target.title, 'fixture 12');
    assert.equal(started.json.instrumentation, true);
    assert.equal(started.json.loadTrap, false);
    daemonPid = started.json.pid;
  });

  test('breakpoint on a TS line in a root module hits with original file:line, locals and frame eval', async () => {
    const bp = await run('break', 'root/src/app/counter.ts:3');
    assert.equal(bp.code, 0, JSON.stringify(bp.json));
    assert.equal(bp.json.state, 'bound');
    assert.equal(bp.json.locations[0].file, path.join('root', 'src', 'app', 'counter.ts'));
    assert.equal(bp.json.locations[0].line, 3);
    assert.match(bp.json.locations[0].generated.url, /\/src\/app\/counter\.ts$/);

    await run('eval', 'setTimeout(() => window.fixture.total(5), 0)');
    const hit = await run('wait', '--timeout', '10');
    assert.equal(hit.code, 0, JSON.stringify(hit.json));
    assert.deepEqual(hit.json.hitBreakpoints, [bp.json.id]);
    assert.equal(hit.json.frame.function, 'computeTotal');
    assert.equal(hit.json.frame.file, path.join('root', 'src', 'app', 'counter.ts'));
    assert.equal(hit.json.frame.line, 3);
    assert.ok(hit.json.frame.source.some((/** @type {string} */ l) => l.startsWith('> 3 |') && l.includes('subtotal * factor')));
    const locals = localsOf(hit.json);
    assert.deepEqual(locals.items, [5, 5]);
    assert.equal(locals.factor, 3);
    assert.equal(locals.subtotal, 10);
    assert.equal(hit.json.stack[1].file, path.join('root', 'src', 'main.ts'));

    const evaluated = await run('eval', 'subtotal * factor + 1');
    assert.equal(evaluated.json.context, 'frame 0');
    assert.equal(evaluated.json.value, 31);

    const stepped = await run('step', 'over');
    assert.equal(stepped.code, 0, JSON.stringify(stepped.json));
    assert.equal(stepped.json.frame.line, 4);
    assert.equal(localsOf(stepped.json).total, 30);
    assert.deepEqual((await run('continue')).json, { resumed: true });
  });

  test('page reload: the breakpoint hits again during boot without being re-issued', async () => {
    const before = (await run('list')).json.breakpoints;
    assert.equal(before.length, 1);
    await run('eval', 'setTimeout(() => location.reload(), 0)');
    const hit = await run('wait', '--timeout', '10');
    assert.equal(hit.code, 0, JSON.stringify(hit.json));
    assert.deepEqual(hit.json.hitBreakpoints, [before[0].id]);
    assert.equal(hit.json.frame.file, path.join('root', 'src', 'app', 'counter.ts'));
    assert.deepEqual(localsOf(hit.json).items, [1, 2, 3], 'the boot-time call, i.e. the reloaded module');
    await run('continue');
    await eventually(async () => (await pageTitles()).includes('fixture 12'), 10_000, 'reloaded page to boot');
    await run('clear');
  });

  test('module outside the Vite root (/@fs) binds by repo path and hits', async () => {
    const bp = await run('break', 'shared/greet.ts:3');
    assert.equal(bp.json.state, 'bound', JSON.stringify(bp.json));
    assert.equal(bp.json.locations[0].file, path.join('shared', 'greet.ts'));
    assert.match(bp.json.locations[0].generated.url, /\/@fs\/.*\/shared\/greet\.ts/);

    await run('eval', 'setTimeout(() => window.fixture.greet("bob"), 0)');
    const hit = await run('wait', '--timeout', '10');
    assert.deepEqual(hit.json.hitBreakpoints, [bp.json.id]);
    assert.equal(hit.json.frame.file, path.join('shared', 'greet.ts'));
    assert.equal(hit.json.frame.line, 3);
    assert.equal(localsOf(hit.json).message, 'hello bob');
    await run('continue');
    await run('clear');
  });

  test('pending breakpoint in a dynamically imported module binds via instrumentation and hits on first call', async () => {
    const bp = await run('break', 'root/src/lazy/report.ts:3');
    assert.equal(bp.json.state, 'pending', JSON.stringify(bp.json));

    await run('eval', 'setTimeout(() => window.fixture.lazy("q3"), 0)');
    const hit = await run('wait', '--timeout', '10');
    assert.equal(hit.code, 0, JSON.stringify(hit.json));
    assert.deepEqual(hit.json.hitBreakpoints, [bp.json.id]);
    assert.equal(hit.json.frame.function, 'buildReport');
    assert.equal(hit.json.frame.file, path.join('root', 'src', 'lazy', 'report.ts'));
    assert.equal(hit.json.frame.line, 3);
    assert.equal(localsOf(hit.json).stamp, 'report:q3');
    await run('continue');
    assert.equal((await run('list')).json.breakpoints[0].state, 'bound');
    await run('clear');
  });

  test('logpoint collects values without pausing the page', async () => {
    const lp = await run('logpoint', 'root/src/app/counter.ts:4', '{ subtotal, total }');
    assert.equal(lp.json.state, 'bound', JSON.stringify(lp.json));
    assert.equal((await run('eval', 'window.fixture.total(2)')).json.value, 12);
    assert.equal((await run('eval', 'window.fixture.total(4)')).json.value, 24);
    assert.equal((await run('status')).json.paused, false);

    const logs = await run('logs');
    assert.deepEqual(
      logs.json.hits.map((/** @type {any} */ h) => h.value),
      [{ subtotal: 4, total: 12 }, { subtotal: 8, total: 24 }],
    );
    assert.equal(logs.json.hits[0].location, `${path.join('root', 'src', 'app', 'counter.ts')}:4`);
    await run('clear');
  });

  test('stop resumes the page and leaves no daemon or state behind', async () => {
    await run('break', 'root/src/app/counter.ts:3');
    await run('eval', 'setTimeout(() => window.fixture.total(1), 0)');
    assert.equal((await run('wait', '--timeout', '10')).json.paused, true);

    const stopped = await run('stop');
    assert.equal(stopped.json.stopped, true);
    assert.equal(alive(/** @type {number} */ (daemonPid)), false);
    assert.equal(existsSync(path.join(home, 'default')), false);
    assert.equal((await run('status')).json.error.code, 'NOT_RUNNING');
  });
});
