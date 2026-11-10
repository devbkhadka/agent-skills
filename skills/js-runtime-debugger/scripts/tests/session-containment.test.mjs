/**
 * Session state containment: session names cannot escape the state root, and cleanup only
 * deletes recursively what dbg itself created (marked with `.dbg-session`).
 */

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { removeState, statePaths } from '../lib/ipc.mjs';
import { freePort, killProcess, runDbg } from './helpers.mjs';

/** Throwaway sandbox: `<tmp>/dbg-containment-XXXX/home` is DBG_HOME, its parent holds a sentinel too. */
function sandbox() {
  const parent = mkdtempSync(path.join(os.tmpdir(), 'dbg-containment-'));
  const home = path.join(parent, 'home');
  mkdirSync(home);
  const parentSentinel = path.join(parent, 'sentinel.txt');
  const homeSentinel = path.join(home, 'sentinel.txt');
  writeFileSync(parentSentinel, 'keep');
  writeFileSync(homeSentinel, 'keep');
  return { parent, home, parentSentinel, homeSentinel, cleanup: () => rmSync(parent, { recursive: true, force: true }) };
}

test('dot session names are rejected by the CLI and nothing outside the session dir is touched', async () => {
  const box = sandbox();
  try {
    for (const session of ['.', '..', '.hidden']) {
      for (const args of [['status'], ['stop'], ['start', '--port', '9229']]) {
        const r = await runDbg({ home: box.home, cwd: box.parent }, [...args, '--session', session]);
        assert.equal(r.code, 1, `${session} ${args[0]}: ${r.stdout}`);
        assert.equal(r.json.error.code, 'BAD_ARGS', `${session} ${args[0]}`);
        assert.equal(typeof r.json.error.message, 'string');
      }
    }
    assert.equal(readFileSync(box.parentSentinel, 'utf8'), 'keep');
    assert.equal(readFileSync(box.homeSentinel, 'utf8'), 'keep');
  } finally {
    box.cleanup();
  }
});

test('statePaths keeps every session dir a direct child of the state root', () => {
  const box = sandbox();
  try {
    for (const session of ['.', '..', '.hidden', '../x', 'a/b', '']) {
      assert.throws(() => statePaths(session, { DBG_HOME: box.home }), (e) => e.code === 'BAD_ARGS', JSON.stringify(session));
    }
    const paths = statePaths('ok-name_1.2', { DBG_HOME: box.home });
    assert.equal(path.dirname(paths.dir), path.resolve(box.home));
    assert.equal(path.dirname(paths.marker), paths.dir);
  } finally {
    box.cleanup();
  }
});

test('removeState without the ownership marker only removes daemon files and keeps foreign content', () => {
  const box = sandbox();
  try {
    const paths = statePaths('legacy', { DBG_HOME: box.home });
    mkdirSync(paths.dir);
    writeFileSync(paths.pid, '999999\n');
    writeFileSync(paths.log, 'log');
    const foreign = path.join(paths.dir, 'notes.txt');
    writeFileSync(foreign, 'user data');
    removeState(paths);
    assert.equal(existsSync(paths.pid), false);
    assert.equal(existsSync(paths.log), false);
    assert.equal(readFileSync(foreign, 'utf8'), 'user data');
    assert.ok(existsSync(paths.dir));

    // An unmarked dir that holds only daemon files is removed entirely.
    const empty = statePaths('legacy-empty', { DBG_HOME: box.home });
    mkdirSync(empty.dir);
    writeFileSync(empty.pid, '999999\n');
    removeState(empty);
    assert.equal(existsSync(empty.dir), false);
    assert.equal(readFileSync(box.homeSentinel, 'utf8'), 'keep');
  } finally {
    box.cleanup();
  }
});

test('removeState with the ownership marker removes the whole session dir', () => {
  const box = sandbox();
  try {
    const paths = statePaths('owned', { DBG_HOME: box.home });
    mkdirSync(paths.dir);
    writeFileSync(paths.marker, '1\n');
    writeFileSync(paths.pid, '999999\n');
    writeFileSync(path.join(paths.dir, 'extra.txt'), 'x');
    removeState(paths);
    assert.equal(existsSync(paths.dir), false);
    assert.equal(readFileSync(box.homeSentinel, 'utf8'), 'keep');
    assert.equal(readFileSync(box.parentSentinel, 'utf8'), 'keep');
  } finally {
    box.cleanup();
  }
});

test('a normal start then stop leaves no session dir behind', async () => {
  const box = sandbox();
  const env = { home: box.home, cwd: box.parent };
  const port = await freePort();
  const target = spawn(process.execPath, [`--inspect=127.0.0.1:${port}`, '-e', 'setInterval(() => {}, 1000)'], { stdio: ['ignore', 'ignore', 'pipe'] });
  try {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('inspector did not start')), 5000);
      target.stderr.on('data', (data) => { if (String(data).includes('Debugger listening')) { clearTimeout(timeout); resolve(undefined); } });
      target.once('exit', () => { clearTimeout(timeout); reject(new Error('target exited')); });
    });
    const paths = statePaths('contained', { DBG_HOME: box.home });
    const started = await runDbg(env, ['start', '--port', String(port), '--session', 'contained']);
    assert.equal(started.code, 0, JSON.stringify(started));
    assert.ok(existsSync(paths.marker), 'session dir carries the ownership marker');
    const stopped = await runDbg(env, ['stop', '--session', 'contained']);
    assert.equal(stopped.code, 0, JSON.stringify(stopped));
    assert.equal(stopped.json.stopped, true);
    assert.equal(existsSync(paths.dir), false);
    assert.equal(readFileSync(box.homeSentinel, 'utf8'), 'keep');
    assert.equal(readFileSync(box.parentSentinel, 'utf8'), 'keep');
  } finally {
    await runDbg(env, ['stop', '--session', 'contained']);
    await killProcess(target);
    box.cleanup();
  }
});
