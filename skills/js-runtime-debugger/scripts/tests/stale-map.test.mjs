/**
 * A build that rewrites dist/ while the process keeps running leaves on-disk source maps that no
 * longer describe the running code. Breakpoints must report `stale-map` instead of binding to the
 * wrong statement, and bind normally once the process runs the new build.
 *
 * Works on a temp copy of tests/fixtures/app so the committed build output is never touched.
 */

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { chmodSync, cpSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, test } from 'node:test';

import { alive, eventually, freePort, killProcess, runDbg } from './helpers.mjs';

/** @param {string} dir */
function makeWritable(dir) {
  chmodSync(dir, 0o755);
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) makeWritable(p);
    else chmodSync(p, 0o644);
  }
}

const FIXTURE_APP = path.join(import.meta.dirname, 'fixtures', 'app');

/**
 * Start the app under the inspector and wait until its HTTP server listens.
 * @param {string} cwd
 * @param {number} inspectPort
 * @param {number} httpPort
 * @returns {Promise<import('node:child_process').ChildProcess>}
 */
async function startApp(cwd, inspectPort, httpPort) {
  for (let attempt = 0; ; attempt++) {
    const child = spawn(process.execPath, [`--inspect=127.0.0.1:${inspectPort}`, 'dist/app.js'], {
      cwd,
      env: { ...process.env, PORT: String(httpPort) },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    child.stderr.on('data', (d) => (stderr += d));
    const ok = await new Promise((resolve) => {
      child.stdout.on('data', (d) => {
        if (String(d).includes('listening')) resolve(true);
      });
      child.once('exit', () => resolve(false));
    });
    if (ok) return child;
    if (attempt >= 20) throw new Error(`app failed to start: ${stderr}`);
    await new Promise((r) => setTimeout(r, 100));
  }
}

/**
 * Simulate a rebuild that shifts every generated line: prepend lines to the .js and the same number
 * of empty generated lines to the map (`mapOnly`: leave the .js untouched, e.g. a half-written build).
 * @param {string} distDir
 * @param {string} name module path under dist/ without extension
 * @param {number} lines
 * @param {{ mapOnly?: boolean }} [options]
 */
function rebuildShifted(distDir, name, lines, { mapOnly = false } = {}) {
  const js = path.join(distDir, `${name}.js`);
  const mapPath = path.join(distDir, `${name}.js.map`);
  if (!mapOnly) writeFileSync(js, `${'// rebuilt\n'.repeat(lines)}${readFileSync(js, 'utf8')}`);
  const map = JSON.parse(readFileSync(mapPath, 'utf8'));
  map.mappings = `${';'.repeat(lines)}${map.mappings}`;
  writeFileSync(mapPath, JSON.stringify(map));
}

describe('dbg with a source map rewritten on disk after the process loaded it', { timeout: 30_000 }, () => {
  /** @type {string} */ let home;
  /** @type {string} */ let appDir;
  /** @type {number} */ let inspectPort;
  /** @type {number} */ let httpPort;
  /** @type {import('node:child_process').ChildProcess | null} */ let app = null;
  /** @type {number | null} */ let daemonPid = null;
  const run = (/** @type {string[]} */ ...args) => runDbg({ home, cwd: appDir }, args);
  const get = (/** @type {string} */ p) => fetch(`http://127.0.0.1:${httpPort}${p}`).then((r) => r.json());

  before(async () => {
    home = mkdtempSync(path.join(os.tmpdir(), 'dbg-'));
    appDir = path.join(realpathSync(mkdtempSync(path.join(os.tmpdir(), 'dbg-stale-'))), 'app');
    // Installed skill copies are read-only and cpSync keeps modes; the test rewrites this copy.
    cpSync(FIXTURE_APP, appDir, { recursive: true });
    makeWritable(appDir);
    inspectPort = await freePort();
    httpPort = await freePort();
    app = await startApp(appDir, inspectPort, httpPort);
    const started = await run('start', '--port', String(inspectPort));
    assert.equal(started.code, 0, JSON.stringify(started.json));
    daemonPid = started.json.pid;
  });

  after(async () => {
    await run('stop');
    if (daemonPid !== null && alive(daemonPid)) process.kill(daemonPid, 'SIGKILL');
    await killProcess(app);
    rmSync(home, { recursive: true, force: true });
    rmSync(path.dirname(appDir), { recursive: true, force: true });
  });

  test('break reports stale-map instead of binding; after restart on the new build it binds and hits', async () => {
    rebuildShifted(path.join(appDir, 'dist'), 'pricing', 3);

    const stale = await run('break', 'src/pricing.ts:13');
    assert.equal(stale.code, 0, JSON.stringify(stale.json));
    assert.equal(stale.json.state, 'stale-map', JSON.stringify(stale.json));
    assert.deepEqual(stale.json.locations, []);
    assert.equal(stale.json.error.code, 'STALE_SOURCE_MAP');
    assert.match(stale.json.error.message, /^STALE_SOURCE_MAP: running dist\/pricing\.js differs from the build output on disk; restart/);

    const status = (await run('status')).json;
    assert.equal(status.breakpoints.staleMap, 1);
    assert.equal(status.breakpoints.bound, 0);
    assert.equal(status.staleSourceMaps[0].breakpoint, stale.json.id);
    assert.equal((await get('/order?qty=3')).total, 75, 'nothing bound, so the running code never pauses');

    const before = status.target.id;
    await killProcess(app);
    app = await startApp(appDir, inspectPort, httpPort);
    await eventually(
      async () => {
        const s = (await run('status')).json;
        return s.attached && s.target.id !== before && s.breakpoints.bound === 1;
      },
      10_000,
      'rebind on the restarted process',
    );
    const listed = (await run('list')).json.breakpoints[0];
    assert.equal(listed.state, 'bound');
    assert.equal(listed.error, undefined);
    assert.equal(listed.locations[0].line, 13);
    assert.equal(listed.locations[0].generated.line, 11, 'generated line shifted by the 3 prepended lines');

    const response = get('/order?qty=3');
    const hit = await run('wait', '--timeout', '10');
    assert.deepEqual(hit.json.hitBreakpoints, [stale.json.id]);
    assert.equal(hit.json.frame.file, path.join('src', 'pricing.ts'));
    assert.equal(hit.json.frame.line, 13);
    await run('continue');
    assert.equal((await response).total, 75);
    await run('clear');
  });

  test('a map describing more lines than the running script is stale even when the .js on disk matches', async () => {
    rebuildShifted(path.join(appDir, 'dist'), 'lazy/report', 40, { mapOnly: true });
    const bp = await run('break', 'lazy/report.ts:4');
    assert.equal(bp.json.state, 'pending', JSON.stringify(bp.json));

    assert.match((await get('/report')).summary, /fixture-report/, 'the lazily loaded module runs without pausing');
    const listed = (await run('list')).json.breakpoints[0];
    assert.equal(listed.state, 'stale-map', JSON.stringify(listed));
    assert.match(listed.error.message, /^STALE_SOURCE_MAP: source map for dist\/lazy\/report\.js maps generated line \d+ but the running script has \d+ lines/);
    assert.equal((await run('status')).json.paused, false);
  });
});
