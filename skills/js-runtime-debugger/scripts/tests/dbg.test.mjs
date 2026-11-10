/**
 * End-to-end behavior of the `dbg` CLI against a real Node process (no CDP mocking).
 *
 * Fixture: tests/fixtures/app — TypeScript compiled to CommonJS with external .map files
 * (regenerate with `tsc -p tests/fixtures/app`; tests never need typescript).
 */

import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, test } from 'node:test';

const SCRIPTS = path.join(import.meta.dirname, '..');
const DBG = path.join(SCRIPTS, 'dbg.mjs');
const APP_DIR = path.join(import.meta.dirname, 'fixtures', 'app');

/** @returns {Promise<number>} */
function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = /** @type {net.AddressInfo} */ (server.address());
      server.close(() => resolve(port));
    });
  });
}

/**
 * Start the fixture app under the inspector and wait until its HTTP server listens.
 * @param {number} inspectPort
 * @param {number} httpPort
 * @returns {Promise<import('node:child_process').ChildProcess>}
 */
async function startFixture(inspectPort, httpPort) {
  for (let attempt = 0; ; attempt++) {
    const child = spawn(process.execPath, [`--inspect=127.0.0.1:${inspectPort}`, 'dist/app.js'], {
      cwd: APP_DIR,
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
    // The previous process may still hold the port for a moment after SIGKILL.
    if (attempt >= 20) throw new Error(`fixture failed to start: ${stderr}`);
    await new Promise((r) => setTimeout(r, 100));
  }
}

/** @param {import('node:child_process').ChildProcess | null} child */
async function killProcess(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((r) => child.once('exit', r));
  child.kill('SIGKILL');
  await exited;
}

/** @param {number} pid */
function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * Run the CLI; returns exit code and parsed stdout JSON.
 * @param {string} home DBG_HOME
 * @param {string[]} args
 * @returns {Promise<{ code: number, json: any, stderr: string }>}
 */
function dbg(home, args) {
  return new Promise((resolve) => {
    execFile(
      process.execPath,
      [DBG, ...args],
      { cwd: APP_DIR, env: { ...process.env, DBG_HOME: home }, timeout: 60_000 },
      (err, stdout, stderr) => {
        const code = err ? (typeof err.code === 'number' ? err.code : 1) : 0;
        let json;
        try {
          json = JSON.parse(stdout);
        } catch {
          json = { unparsable: stdout };
        }
        resolve({ code, json, stderr });
      },
    );
  });
}

/**
 * @param {() => Promise<boolean>} check
 * @param {number} timeoutMs
 * @param {string} what
 */
async function eventually(check, timeoutMs, what) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`timed out waiting for ${what}`);
}

describe('dbg against a live Node target', { timeout: 60_000 }, () => {
  /** @type {string} */ let home;
  /** @type {number} */ let inspectPort;
  /** @type {number} */ let httpPort;
  /** @type {import('node:child_process').ChildProcess | null} */ let app = null;
  /** @type {number | null} */ let daemonPid = null;
  const run = (/** @type {string[]} */ ...args) => dbg(home, args);
  const get = (/** @type {string} */ p) => fetch(`http://127.0.0.1:${httpPort}${p}`).then((r) => r.json());

  before(async () => {
    home = mkdtempSync(path.join(os.tmpdir(), 'dbg-'));
    inspectPort = await freePort();
    httpPort = await freePort();
    app = await startFixture(inspectPort, httpPort);
  });

  after(async () => {
    await run('stop');
    if (daemonPid !== null && alive(daemonPid)) process.kill(daemonPid, 'SIGKILL');
    await killProcess(app);
    rmSync(home, { recursive: true, force: true });
  });

  test('start attaches and is idempotent', async () => {
    const first = await run('start', '--port', String(inspectPort), '--idle-timeout', '5m');
    assert.equal(first.code, 0, JSON.stringify(first.json));
    assert.equal(first.json.started, true);
    assert.equal(first.json.attached, true);
    assert.match(first.json.target.url, /dist\/app\.js$/);
    daemonPid = first.json.pid;

    const again = await run('start', '--port', String(inspectPort));
    assert.equal(again.code, 0);
    assert.equal(again.json.alreadyRunning, true);
    assert.equal(again.json.pid, daemonPid);
  });

  test('breakpoint on a TS line pauses with original location and locals; eval, step, continue', async () => {
    const bp = await run('break', 'src/pricing.ts:13');
    assert.equal(bp.code, 0, JSON.stringify(bp.json));
    assert.equal(bp.json.state, 'bound');
    assert.equal(bp.json.locations[0].file, path.join('src', 'pricing.ts'));
    assert.equal(bp.json.locations[0].line, 13);
    assert.equal(bp.json.locations[0].generated.line, 8);

    const response = get('/order?qty=3');
    const hit = await run('wait', '--timeout', '10');
    assert.equal(hit.code, 0, JSON.stringify(hit.json));
    assert.deepEqual(hit.json.hitBreakpoints, [bp.json.id]);
    assert.equal(hit.json.frame.function, 'priceOrder');
    assert.equal(hit.json.frame.file, path.join('src', 'pricing.ts'));
    assert.equal(hit.json.frame.line, 13);
    assert.ok(hit.json.frame.source.some((/** @type {string} */ l) => l.startsWith('> 13 |') && l.includes('const subtotal')));
    const locals = hit.json.scopes.find((/** @type {any} */ s) => s.type === 'local').variables;
    assert.equal(locals.sku, 'sku-1');
    assert.equal(locals.qty, 3);
    assert.equal(locals.unitPrice, 25);
    assert.equal(hit.json.stack[1].function, 'handle');
    assert.equal(hit.json.stack[1].file, path.join('src', 'app.ts'));
    // Runs of node internals collapse into one entry naming where they come from.
    assert.ok(hit.json.stack.some((/** @type {any} */ f) => f.collapsed >= 1 && /internal/.test(f.from)), JSON.stringify(hit.json.stack));
    const raw = await run('stack', '--all');
    assert.ok(raw.json.stack.some((/** @type {any} */ f) => f.tag === 'internal'));

    const evaluated = await run('eval', 'unitPrice * qty');
    assert.equal(evaluated.json.value, 75);
    const objectEval = await run('eval', '({ sku, nested: { qty } })');
    assert.deepEqual(objectEval.json.value, { sku: 'sku-1', nested: { qty: 3 } });

    const stepped = await run('step', 'over');
    assert.equal(stepped.code, 0, JSON.stringify(stepped.json));
    assert.equal(stepped.json.frame.line, 14);
    assert.equal(stepped.json.scopes.find((/** @type {any} */ s) => s.type === 'local').variables.subtotal, 75);

    const resumed = await run('continue');
    assert.deepEqual(resumed.json, { resumed: true });
    assert.equal((await response).total, 75);
  });

  test('logpoint records values without pausing', async () => {
    const cleared = await run('clear');
    assert.equal(cleared.code, 0);
    const lp = await run('logpoint', 'src/pricing.ts:15', '{ qty, subtotal }');
    assert.equal(lp.json.state, 'bound', JSON.stringify(lp.json));

    assert.equal((await get('/order?qty=2')).total, 50);
    assert.equal((await get('/order?qty=4')).total, 100);
    const status = await run('status');
    assert.equal(status.json.paused, false);

    const logs = await run('logs');
    assert.deepEqual(
      logs.json.hits.map((/** @type {any} */ h) => h.value),
      [{ qty: 2, subtotal: 50 }, { qty: 4, subtotal: 100 }],
    );
    assert.equal(logs.json.hits[0].location, `${path.join('src', 'pricing.ts')}:15`);
    const since = await run('logs', '--since', String(logs.json.hits[0].seq));
    assert.equal(since.json.hits.length, 1);
    // lastSeq is the newest seq seen; `--since` is exclusive, so passing it back yields only newer hits.
    assert.equal(logs.json.lastSeq, logs.json.hits[1].seq);
    assert.deepEqual((await run('logs', '--since', String(logs.json.lastSeq))).json.hits, []);
    assert.equal((await get('/order?qty=5')).total, 125);
    const newer = await run('logs', '--since', String(logs.json.lastSeq));
    assert.deepEqual(
      newer.json.hits.map((/** @type {any} */ h) => [h.seq, h.value]),
      [[logs.json.lastSeq + 1, { qty: 5, subtotal: 125 }]],
    );

    const throwing = await run('logpoint', 'src/pricing.ts:16', 'notDefined.prop');
    assert.equal((await get('/order?qty=1')).total, 25, 'a throwing logpoint expression must not break the program');
    const errorHit = (await run('logs')).json.hits.find((/** @type {any} */ h) => h.breakpoint === throwing.json.id);
    assert.match(errorHit.value.logpointError, /notDefined/);

    const removed = await run('remove', lp.json.id);
    assert.equal(removed.json.removed, lp.json.id);
    await run('clear');
    assert.deepEqual((await run('list')).json.breakpoints, []);
  });

  /** Clear breakpoints, then resume until the target runs, so a failed assertion cannot strand later tests. */
  async function releaseTarget() {
    await run('clear');
    for (let i = 0; i < 10 && (await run('status')).json.paused; i++) await run('continue');
  }

  test('continue followed immediately by wait returns the next pause, never the resumed one', async () => {
    /** @param {any} json */
    const qtyOf = (json) => json.scopes.find((/** @type {any} */ s) => s.type === 'local').variables.qty;
    /** @type {Promise<any>[]} */
    const responses = [];
    try {
      const bp = await run('break', 'src/pricing.ts:13');
      assert.equal(bp.json.state, 'bound', JSON.stringify(bp.json));
      responses.push(get('/order?qty=6'));
      const first = await run('wait', '--timeout', '10');
      assert.equal(first.code, 0, JSON.stringify(first.json));
      assert.equal(qtyOf(first.json), 6);
      // Queued while paused: it can only reach the breakpoint after `continue`.
      responses.push(get('/order?qty=7'));

      const resumed = await run('continue');
      assert.deepEqual(resumed.json, { resumed: true });
      const next = await run('wait', '--timeout', '10');
      assert.equal(next.code, 0, JSON.stringify(next.json));
      assert.deepEqual(next.json.hitBreakpoints, [bp.json.id]);
      assert.equal(qtyOf(next.json), 7, 'wait after continue must not return the resumed pause');

      assert.deepEqual((await run('continue')).json, { resumed: true });
      assert.deepEqual((await Promise.all(responses)).map((r) => r.total), [150, 175]);
    } finally {
      await releaseTarget();
      await Promise.allSettled(responses);
    }
  });

  test('logs pages oldest-first with a nextSeq cursor, hasMore, and an explicit --tail', async () => {
    try {
      const base = (await run('logs', '--tail', '--limit', '1')).json.latestSeq;
      assert.equal(typeof base, 'number');
      const lp = await run('logpoint', 'src/pricing.ts:15', 'qty');
      assert.equal(lp.json.state, 'bound', JSON.stringify(lp.json));
      for (let qty = 1; qty <= 45; qty++) assert.equal((await get(`/order?qty=${qty}`)).qty, qty);

      const oldest = await run('logs');
      assert.equal(oldest.code, 0, JSON.stringify(oldest.json));
      assert.equal(oldest.json.hits[0].seq, 1, 'default page starts at the oldest retained hit');
      assert.equal(oldest.json.hasMore, true);

      const page = await run('logs', '--since', String(base));
      assert.deepEqual(page.json.hits.map((/** @type {any} */ h) => h.value), Array.from({ length: 20 }, (_, i) => i + 1));
      assert.equal(page.json.nextSeq, base + 20);
      assert.equal(page.json.hasMore, true);
      assert.equal(page.json.latestSeq, base + 45);

      /** @type {number[]} */
      const values = [];
      let cursor = base;
      /** @type {any} */
      let current;
      do {
        current = (await run('logs', '--since', String(cursor), '--limit', '15')).json;
        assert.ok(current.hits.length <= 15, JSON.stringify(current));
        values.push(...current.hits.map((/** @type {any} */ h) => h.value));
        cursor = current.nextSeq;
      } while (current.hasMore);
      assert.deepEqual(values, Array.from({ length: 45 }, (_, i) => i + 1));
      assert.equal(cursor, base + 45);

      const drained = await run('logs', '--since', String(cursor));
      assert.deepEqual(drained.json.hits, []);
      assert.equal(drained.json.nextSeq, cursor);
      assert.equal(drained.json.hasMore, false);

      const tail = await run('logs', '--tail', '--limit', '3');
      assert.deepEqual(tail.json.hits.map((/** @type {any} */ h) => h.value), [43, 44, 45]);
      assert.equal(tail.json.hasMore, false);
    } finally {
      await releaseTarget();
    }
  });

  test('pending breakpoint in a lazily required module binds on load and hits', async () => {
    const bp = await run('break', 'lazy/report.ts:4');
    assert.equal(bp.json.state, 'pending', JSON.stringify(bp.json));

    const response = get('/report');
    const hit = await run('wait', '--timeout', '10');
    assert.equal(hit.code, 0, JSON.stringify(hit.json));
    assert.deepEqual(hit.json.hitBreakpoints, [bp.json.id]);
    assert.equal(hit.json.frame.file, path.join('src', 'lazy', 'report.ts'));
    assert.equal(hit.json.frame.line, 4);
    assert.equal(hit.json.scopes.find((/** @type {any} */ s) => s.type === 'local').variables.label, 'fixture-report');
    await run('continue');
    assert.match((await response).summary, /fixture-report after/);

    const listed = (await run('list')).json.breakpoints.find((/** @type {any} */ b) => b.id === bp.json.id);
    assert.equal(listed.state, 'bound');
    await run('clear');
  });

  test('pause on caught exceptions reports the exception; wait times out with exit 2', async () => {
    assert.equal((await run('exceptions', 'all')).json.exceptions, 'all');
    const response = get('/boom');
    const hit = await run('wait', '--timeout', '10');
    assert.equal(hit.json.reason, 'exception');
    assert.match(String(hit.json.exception), /boom: requested/);
    assert.equal(hit.json.frame.file, path.join('src', 'pricing.ts'));
    assert.equal(hit.json.frame.line, 20);
    await run('exceptions', 'none');
    await run('continue');
    assert.match((await response).caught, /boom/);

    const timedOut = await run('wait', '--timeout', '1');
    assert.equal(timedOut.code, 2);
    assert.equal(timedOut.json.error.code, 'WAIT_TIMEOUT');
  });

  test('target restart on the same port: daemon reattaches and re-applies breakpoints', async () => {
    const bp = await run('break', 'src/pricing.ts:16');
    assert.equal(bp.json.state, 'bound');
    const before = (await run('status')).json;

    await killProcess(app);
    app = await startFixture(inspectPort, httpPort);
    /** @type {any} */
    let status;
    await eventually(
      async () => {
        status = (await run('status')).json;
        return status.attached && status.target.id !== before.target.id && status.breakpoints.bound === 1;
      },
      10_000,
      'reattach',
    );
    assert.ok(status.reconnects >= 1);

    const response = get('/order?qty=10');
    const hit = await run('wait', '--timeout', '10');
    assert.deepEqual(hit.json.hitBreakpoints, [bp.json.id]);
    assert.equal(hit.json.frame.line, 16);
    const locals = hit.json.scopes.find((/** @type {any} */ s) => s.type === 'local').variables;
    assert.equal(locals.discount, 25);
    await run('continue');
    assert.equal((await response).total, 225);
  });

  test('stop removes breakpoints, resumes, exits the daemon and cleans state', async () => {
    const response = get('/order?qty=1');
    const hit = await run('wait', '--timeout', '10');
    assert.equal(hit.json.paused, true);

    const stopped = await run('stop');
    assert.equal(stopped.json.stopped, true);
    assert.equal((await response).total, 25, 'stop resumes a paused target');
    assert.equal(alive(/** @type {number} */ (daemonPid)), false);
    assert.equal(existsSync(path.join(home, 'default')), false);
    assert.equal((await get('/order?qty=1')).total, 25, 'no breakpoint left behind');

    const status = await run('status');
    assert.equal(status.code, 1);
    assert.equal(status.json.error.code, 'NOT_RUNNING');
  });
});

describe('dbg without a daemon', () => {
  test('stale state is cleaned and errors are JSON with exit 1', async () => {
    const home = mkdtempSync(path.join(os.tmpdir(), 'dbg-'));
    try {
      const dir = path.join(home, 'ghost');
      mkdirSync(dir, { recursive: true });
      writeFileSync(path.join(dir, 'daemon.pid'), '999999\n');
      const result = await dbg(home, ['list', '--session', 'ghost']);
      assert.equal(result.code, 1);
      assert.equal(result.json.error.code, 'NOT_RUNNING');
      assert.equal(existsSync(dir), false);

      const bad = await dbg(home, ['start']);
      assert.equal(bad.code, 1);
      assert.equal(bad.json.error.code, 'BAD_ARGS');

      const port = await freePort();
      const noTarget = await dbg(home, ['start', '--port', String(port), '--attach-timeout', '1']);
      assert.equal(noTarget.code, 1);
      assert.equal(noTarget.json.error.code, 'ATTACH_TIMEOUT');
      assert.match(noTarget.json.error.lastError, /not reachable/);
      assert.equal(existsSync(path.join(home, 'default')), false);
    } finally {
      rmSync(home, { recursive: true, force: true });
    }
  });
});
