/**
 * `dbg targets` / `dbg start --pid` against real node processes started with a random inspector
 * port (`--inspect=127.0.0.1:0`, the way Nx `@nx/js:node` and VS Code auto-attach start them).
 */

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, test } from 'node:test';

import { isNodeCommand } from '../lib/discovery.mjs';
import { parseListenName } from '../lib/processes.mjs';
import { alive, freePort, killProcess, runDbg } from './helpers.mjs';

const APP_DIR = path.join(import.meta.dirname, 'fixtures', 'app');
const SKIP = ['darwin', 'linux'].includes(process.platform) ? false : `process discovery is macOS/Linux only (${process.platform})`;

/**
 * Start the fixture app on a random inspector port; resolves with the port node printed.
 * @param {number} httpPort
 * @returns {Promise<{ child: import('node:child_process').ChildProcess, inspectPort: number }>}
 */
function startRandomPortFixture(httpPort) {
  const child = spawn(process.execPath, ['--inspect=127.0.0.1:0', 'dist/app.js'], {
    cwd: APP_DIR,
    env: { ...process.env, PORT: String(httpPort) },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  return new Promise((resolve, reject) => {
    let stderr = '';
    let inspectPort = 0;
    let listening = false;
    const done = () => {
      if (inspectPort && listening) resolve({ child, inspectPort });
    };
    child.stderr.on('data', (d) => {
      stderr += d;
      const match = /Debugger listening on ws:\/\/127\.0\.0\.1:(\d+)\//.exec(stderr);
      if (match) inspectPort = Number(match[1]);
      done();
    });
    child.stdout.on('data', (d) => {
      if (String(d).includes('listening')) listening = true;
      done();
    });
    child.once('exit', (code) => reject(new Error(`fixture exited ${code}: ${stderr}`)));
  });
}

describe('listen-address and command parsing', () => {
  test('lsof names become connectable hosts', () => {
    assert.deepEqual(parseListenName('127.0.0.1:9229'), { host: '127.0.0.1', port: 9229 });
    assert.deepEqual(parseListenName('*:8081'), { host: '127.0.0.1', port: 8081 });
    assert.deepEqual(parseListenName('[::1]:53818'), { host: '::1', port: 53818 });
    assert.deepEqual(parseListenName('[::]:9229'), { host: '::1', port: 9229 });
    assert.equal(parseListenName('garbage'), null);
  });

  test('node command lines are recognized, other runtimes are not', () => {
    assert.ok(isNodeCommand('/Users/x/.nvm/versions/node/v22.17.0/bin/node -r register.js main.js'));
    assert.ok(isNodeCommand('node --inspect=localhost:0 dist/main.js'));
    assert.ok(isNodeCommand('/usr/bin/nodejs server.js'));
    assert.ok(isNodeCommand('/opt/electron-helper --inspect=9230 app'));
    assert.ok(!isNodeCommand('/usr/bin/python3 -m http.server'));
    assert.ok(!isNodeCommand('/usr/local/bin/nodemon-like-thing app.js'));
  });
});

describe('node discovery by process', { skip: SKIP, timeout: 60_000 }, () => {
  /** @type {string} */ let home;
  /** @type {import('node:child_process').ChildProcess | null} */ let app = null;
  /** @type {import('node:child_process').ChildProcess | null} */ let plain = null;
  /** @type {number} */ let inspectPort;
  /** @type {number} */ let httpPort;
  /** @type {number | null} */ let daemonPid = null;
  const run = (/** @type {string[]} */ ...args) => runDbg({ home, cwd: APP_DIR }, args);

  before(async () => {
    home = mkdtempSync(path.join(os.tmpdir(), 'dbg-disc-'));
    httpPort = await freePort();
    ({ child: app, inspectPort } = await startRandomPortFixture(httpPort));
    plain = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
  });

  after(async () => {
    await run('stop');
    if (daemonPid !== null && alive(daemonPid)) process.kill(daemonPid, 'SIGKILL');
    await killProcess(app);
    await killProcess(plain);
    rmSync(home, { recursive: true, force: true });
  });

  test('targets lists the process with its random inspector port, cwd and command', async () => {
    const res = await run('targets', '--kind', 'node');
    assert.equal(res.code, 0, JSON.stringify(res.json));
    const mine = res.json.targets.filter((/** @type {any} */ t) => t.pid === app?.pid);
    assert.equal(mine.length, 1, `exactly one endpoint for the fixture: ${JSON.stringify(res.json.targets)}`);
    assert.equal(mine[0].kind, 'node');
    assert.equal(mine[0].port, inspectPort, 'the inspector port, not the fixture HTTP port');
    assert.equal(mine[0].host, '127.0.0.1');
    assert.match(mine[0].url, /dist\/app\.js$/);
    assert.equal(mine[0].cwd, APP_DIR);
    assert.match(mine[0].command, /--inspect=127\.0\.0\.1:0 dist\/app\.js$/);
    assert.ok(!res.json.targets.some((/** @type {any} */ t) => t.pid === plain?.pid), 'no inspector, not listed');
  });

  test('start --pid resolves the port and attaches; breakpoints work', async () => {
    const started = await run('start', '--pid', String(app?.pid), '--idle-timeout', '5m');
    assert.equal(started.code, 0, JSON.stringify(started.json));
    daemonPid = started.json.pid;
    assert.equal(started.json.attached, true);
    assert.equal(started.json.inspector, `127.0.0.1:${inspectPort}`);
    assert.equal(started.json.targetPid, app?.pid);

    const bp = await run('break', 'src/pricing.ts:13');
    assert.equal(bp.json.state, 'bound', JSON.stringify(bp.json));
    const response = fetch(`http://127.0.0.1:${httpPort}/order?qty=2`).then((r) => r.json());
    const hit = await run('wait', '--timeout', '10');
    assert.equal(hit.json.frame.function, 'priceOrder');
    await run('continue');
    assert.equal((await response).total, 50);
  });

  test('reload is refused for node with a hint', async () => {
    const res = await run('reload');
    assert.equal(res.code, 1);
    assert.equal(res.json.error.code, 'RELOAD_UNSUPPORTED');
    assert.ok(res.json.error.hint, JSON.stringify(res.json));
    await run('stop');
  });

  test('start --pid on a process without an inspector fails with NO_INSPECTOR', async () => {
    const res = await run('start', '--session', 'plain', '--pid', String(plain?.pid));
    assert.equal(res.code, 1);
    assert.equal(res.json.error.code, 'NO_INSPECTOR');
    assert.match(res.json.error.hint, /--inspect/);
  });
});
