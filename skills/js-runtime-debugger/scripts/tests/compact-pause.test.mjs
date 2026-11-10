/**
 * Pause snapshots stay small enough for an agent's context: a pause inside a webpack-style bundle
 * (bundler runtime closure, 60 TS import bindings, CommonJS wrapper, a library call chain from
 * node_modules, 100-element arrays) must still show every local while summarizing the noise.
 *
 * The fixture is generated into a temp dir (node_modules/ paths are not committed).
 */

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, test } from 'node:test';

import { collapseStack, framePackage, wrapperScopeKind } from '../lib/session.mjs';
import { alive, freePort, killProcess, runDbg } from './helpers.mjs';

const BUDGET_BYTES = 4096;

/** @param {string} dir */
function writeBundleFixture(dir) {
  const deps = Array.from({ length: 60 }, (_, i) => `dep_${i + 1}`);
  const modules = Array.from({ length: 100 }, (_, i) => `  ${i}: function (module) { module.exports = ${i}; },`);
  const lines = [
    '(() => {',
    '  var __webpack_modules__ = {',
    ...modules,
    '  };',
    '  var __webpack_module_cache__ = {};',
    '  function __webpack_require__(id) {',
    '    if (!(id in __webpack_module_cache__)) { const module = { exports: {} }; __webpack_modules__[id](module); __webpack_module_cache__[id] = module; }',
    '    return __webpack_module_cache__[id].exports;',
    '  }',
    '  for (let i = 0; i < 100; i++) __webpack_require__(i);',
    '  const router = require("fake-router");',
    '  (function main() {',
    ...deps.map((d, i) => `    var ${d} = { id: ${i} };`),
    `    const allDeps = () => [${deps.join(', ')}, __webpack_require__];`,
    '    function handle(order) {',
    '      const items = Array.from({ length: 100 }, (_, i) => i);',
    '      const handlers = Array.from({ length: 100 }, () => function noop() {});',
    '      const qty = order.qty;',
    '      const total = qty * items.length + allDeps().length + handlers.length;', // BREAK_LINE
    '      return total;',
    '    }',
    '    setInterval(() => router.dispatch(handle, { qty: 3, sku: "sku-1" }), 50);',
    '  })();',
    '})();',
  ];
  writeFileSync(path.join(dir, 'bundle.js'), `${lines.join('\n')}\n`);
  mkdirSync(path.join(dir, 'node_modules', 'fake-router'), { recursive: true });
  writeFileSync(
    path.join(dir, 'node_modules', 'fake-router', 'index.js'),
    [
      'function layer3(fn, x) { return fn(x); }',
      'function layer2(fn, x) { return layer3(fn, x); }',
      'function layer1(fn, x) { return layer2(fn, x); }',
      'exports.dispatch = (fn, x) => layer1(fn, x);',
      '',
    ].join('\n'),
  );
  return lines.findIndex((l) => l.includes('const total = qty')) + 1;
}

describe('scope and stack summarization (pure)', () => {
  test('wrapper/bundler/import-binding closures are recognized; ordinary closures are not', () => {
    assert.equal(wrapperScopeKind(['__webpack_require__', 'x']), 'bundler runtime');
    assert.equal(wrapperScopeKind(['exports', 'require', 'module', '__filename', '__dirname']), 'module wrapper');
    assert.equal(wrapperScopeKind(['express_1', 'common_1', 'config_1', 'app']), 'import bindings');
    assert.equal(wrapperScopeKind(['count', 'setCount', 'label_1']), null);
  });

  test('package names come from the last node_modules segment', () => {
    assert.equal(framePackage('/r/node_modules/express/lib/router/index.js'), 'express');
    assert.equal(framePackage('/r/node_modules/a/node_modules/@nestjs/core/x.js'), '@nestjs/core');
    assert.equal(framePackage('node:internal/timers'), 'node:internal');
    assert.equal(framePackage(''), 'internal');
  });

  test('library runs collapse; user frames beyond 10 become a "more" entry', () => {
    const user = (/** @type {number} */ i) => ({ index: i, function: `f${i}`, file: 'src/a.ts' });
    const lib = (/** @type {number} */ i, /** @type {string} */ file) => ({ index: i, function: 'x', file, tag: 'node_modules' });
    const frames = [
      lib(0, '/r/node_modules/pg/lib/client.js'), // frame 0 is always shown in full
      lib(1, '/r/node_modules/pg/lib/query.js'),
      lib(2, '/r/node_modules/express/lib/router.js'),
      user(3),
      ...Array.from({ length: 12 }, (_, k) => user(4 + k)),
    ];
    const out = collapseStack(frames, frames.length);
    assert.deepEqual(out[0], frames[0]);
    assert.deepEqual(out[1], { collapsed: 2, from: 'pg, express' });
    assert.equal(out.filter((f) => 'function' in f).length, 10);
    // frame 0 + 9 user frames fill the 10; frames 12..15 remain.
    assert.deepEqual(out.at(-1), { more: 4, hint: 'dbg stack --all' });
  });
});

describe('pause snapshot size budget in a webpack-style bundle', { timeout: 60_000 }, () => {
  /** @type {string} */ let dir;
  /** @type {string} */ let home;
  /** @type {number} */ let breakLine;
  /** @type {import('node:child_process').ChildProcess | null} */ let child = null;
  /** @type {number | null} */ let daemonPid = null;
  const run = (/** @type {string[]} */ ...args) => runDbg({ home, cwd: dir }, args);

  before(async () => {
    dir = mkdtempSync(path.join(os.tmpdir(), 'dbg-bundle-'));
    home = mkdtempSync(path.join(os.tmpdir(), 'dbg-home-'));
    breakLine = writeBundleFixture(dir);
    const port = await freePort();
    child = spawn(process.execPath, [`--inspect=127.0.0.1:${port}`, 'bundle.js'], { cwd: dir, stdio: ['ignore', 'ignore', 'pipe'] });
    await new Promise((resolve, reject) => {
      let stderr = '';
      child?.stderr?.on('data', (d) => {
        stderr += d;
        if (stderr.includes('Debugger listening')) resolve(undefined);
      });
      child?.once('exit', (code) => reject(new Error(`bundle fixture exited ${code}: ${stderr}`)));
    });
    const started = await run('start', '--port', String(port), '--idle-timeout', '5m');
    assert.equal(started.code, 0, JSON.stringify(started.json));
    daemonPid = started.json.pid;
  });

  after(async () => {
    await run('stop');
    if (daemonPid !== null && alive(daemonPid)) process.kill(daemonPid, 'SIGKILL');
    await killProcess(child);
    rmSync(dir, { recursive: true, force: true });
    rmSync(home, { recursive: true, force: true });
  });

  test('wait output fits the budget, shows all locals, and summarizes closures and library frames', async (t) => {
    const bp = await run('break', `bundle.js:${breakLine}`);
    assert.equal(bp.code, 0, JSON.stringify(bp.json));
    const hit = await run('wait', '--timeout', '10');
    assert.equal(hit.code, 0, JSON.stringify(hit.json));
    const size = Buffer.byteLength(hit.stdout);
    t.diagnostic(`pause stdout: ${size} bytes`);
    assert.ok(size < BUDGET_BYTES, `snapshot is ${size} bytes:\n${JSON.stringify(hit.json, null, 2)}`);

    const local = hit.json.scopes.find((/** @type {any} */ s) => s.type === 'local');
    assert.deepEqual(Object.keys(local.variables).sort(), ['handlers', 'items', 'order', 'qty', 'total']);
    assert.equal(local.variables.qty, 3);
    assert.equal(local.variables.items.length, 11, '10 entries plus the overflow marker');
    assert.equal(local.variables.items.at(-1), '…+90 more');

    const omitted = hit.json.scopes.filter((/** @type {any} */ s) => s.omitted !== undefined);
    assert.ok(omitted.some((/** @type {any} */ s) => s.why === 'import bindings' && s.omitted >= 60), JSON.stringify(hit.json.scopes));
    assert.ok(omitted.some((/** @type {any} */ s) => s.why === 'bundler runtime'), JSON.stringify(hit.json.scopes));
    assert.ok(omitted.every((/** @type {any} */ s) => s.hint === 'dbg scope --frame 0 --all'));

    assert.equal(hit.json.stack[0].function, 'handle');
    assert.deepEqual(hit.json.stack[1], { collapsed: 4, from: 'fake-router' });

    const all = await run('scope', '--all');
    const bundler = all.json.scopes.find((/** @type {any} */ s) => s.type === 'closure' && '__webpack_require__' in s.variables);
    assert.ok(bundler, 'scope --all shows the bundler closure');
    const raw = await run('stack', '--all');
    assert.ok(raw.json.stack.filter((/** @type {any} */ f) => f.tag === 'node_modules').length >= 4);

    const resumed = await run('clear');
    assert.equal(resumed.code, 0);
    await run('continue');
  });
});
