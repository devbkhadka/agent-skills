/** Real Node/CDP regressions for HMR excerpts, lossless log paging and selective inspection. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, test } from 'node:test';
import { DebugSession } from '../lib/session.mjs';
import { getProfile } from '../lib/targets.mjs';
import { eventually, freePort, killProcess } from './helpers.mjs';

describe('runtime regression contracts', { timeout: 30_000 }, () => {
  let dir, child, session;
  before(async () => {
    dir = mkdtempSync(path.join(os.tmpdir(), 'dbg-regressions-'));
    writeFileSync(path.join(dir, 'fixture.cjs'), [
      "const vm = require('node:vm');",
      'function emit(count) {',
      '  for (let i = 0; i < count; i++) {',
      '    const value = i;',
      '    globalThis.last = value;',
      '  }',
      '}',
      "process.on('message', (m) => {",
      "  if (m.code) vm.runInThisContext(m.code);",
      '  else emit(m.count);',
      "  process.send('done');",
      '});',
    ].join('\n'));
    const port = await freePort();
    child = spawn(process.execPath, [`--inspect=127.0.0.1:${port}`, 'fixture.cjs'], { cwd: dir, stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
    await new Promise((resolve, reject) => {
      child.stderr.on('data', (d) => { if (String(d).includes('Debugger listening')) resolve(); });
      child.once('exit', (code) => reject(new Error(`fixture exited ${code}`)));
    });
    session = new DebugSession({ profile: getProfile('node'), host: '127.0.0.1', port, cwd: dir });
    session.start();
    await eventually(async () => session.status().attached, 5000, 'attach');
  });
  after(async () => {
    await session?.shutdown();
    await killProcess(child);
    if (dir) rmSync(dir, { recursive: true, force: true });
  });
  function trigger(message) {
    const done = new Promise((resolve) => child.once('message', resolve));
    child.send(message);
    return done;
  }
  /** Resume if still paused, then let the child finish, so one failed assertion cannot strand the target. */
  async function release(done) {
    let finished = false;
    void done.then(() => (finished = true));
    // Poll: a pause can still land after a failed/timed-out wait, and the child only replies once it runs on.
    while (!finished) {
      if (session.status().paused) await session.resume().catch(() => {});
      await Promise.race([done, new Promise((r) => setTimeout(r, 50))]);
    }
  }
  test('resume retires the pause before returning: an immediate wait cannot observe it', async () => {
    const done = trigger({ code: `debugger;\n//# sourceURL=${path.join(dir, 'resume.js')}` });
    try {
      const pause = await session.wait({ timeout: 5 });
      assert.match(pause.frame.file, /resume\.js$/);
      assert.deepEqual(await session.resume(), { resumed: true });
      // No new pause is coming, so the only thing an immediate wait could return is the stale one.
      assert.equal(session.status().paused, false);
      await assert.rejects(session.wait({ timeout: 0 }), { code: 'WAIT_TIMEOUT' });
    } finally {
      await release(done);
    }
  });
  test('HMR generations sharing an original path show their own source content', async () => {
    for (const version of ['OLD_SOURCE', 'NEW_SOURCE']) {
      const map = { version: 3, sources: ['src/shared.ts'], sourcesContent: [`const value = '${version}';`], names: [], mappings: 'AAAA' };
      const code = `debugger;\n//# sourceURL=${path.join(dir, 'hot.js')}?t=${version}\n//# sourceMappingURL=data:application/json;base64,${Buffer.from(JSON.stringify(map)).toString('base64')}`;
      const done = trigger({ code });
      try {
        const pause = await session.wait({ timeout: 5 });
        assert.match(pause.frame.file, /shared\.ts$/);
        assert.ok(pause.frame.source.some((line) => line.includes(version)), JSON.stringify(pause.frame));
        assert.deepEqual(await session.resume(), { resumed: true });
      } finally {
        await release(done);
      }
    }
  });
  test('oldest unread pages retain every hit, tail is explicit and ring loss is reported', async () => {
    await session.addLogpoint({ location: 'fixture.cjs:5', expr: 'value' });
    try {
      await trigger({ count: 150 });
      await eventually(async () => session.logs({}).latestSeq === 150, 5000, '150 log hits');
      const first = session.logs({});
      assert.equal(first.hits.length, 20);
      assert.equal(first.nextSeq, 20);
      assert.equal(first.hasMore, true);
      assert.equal(first.omitted, 130);
      assert.equal(first.lastSeq, 150);
      const seqs = [];
      let cursor = 0, page;
      do {
        page = session.logs({ since: cursor, limit: 37 });
        seqs.push(...page.hits.map((h) => h.seq));
        cursor = page.nextSeq;
      } while (page.hasMore);
      assert.deepEqual(seqs, Array.from({ length: 150 }, (_, i) => i + 1));
      const empty = session.logs({ since: cursor });
      assert.equal(empty.nextSeq, cursor);
      assert.equal(empty.hasMore, false);
      assert.deepEqual(session.logs({ tail: true, limit: 3 }).hits.map((h) => h.seq), [148, 149, 150]);
      await trigger({ count: 400 });
      await eventually(async () => session.logs({}).latestSeq === 550, 5000, '550 log hits');
      const dropped = session.logs({});
      assert.equal(dropped.dropped, 50);
      assert.equal(dropped.hits[0].seq, 51);
      assert.equal(dropped.nextSeq, 70);
    } finally {
      await session.clear({});
    }
  });
  test('scope filters expand selected closures, stack pages preserve frame indices', async () => {
    const code = `(function outer() { const kept = 42; const hidden = 99; const __webpack_require__ = () => 0; function inner() { const local = 3; debugger; return kept + hidden + local + __webpack_require__(); } inner(); })();\n//# sourceURL=${path.join(dir, 'scope.js')}`;
    const done = trigger({ code });
    try {
      await session.wait({ timeout: 5 });
      const defaults = await session.scope({});
      assert.ok(defaults.scopes.some((s) => s.why === 'bundler runtime' && s.omitted > 0));
      const scope = await session.scope({ vars: 'kept', scope: 'outer' });
      assert.ok(scope.scopes.some((s) => s.variables.kept === 42), JSON.stringify(scope));
      assert.ok(scope.scopes.every((s) => Object.keys(s.variables).every((k) => k === 'kept')));
      const local = await session.scope({ scope: 'local', vars: 'local' });
      assert.deepEqual(local.scopes.map((s) => s.variables), [{ local: 3 }]);
      const stack = await session.stack({ offset: 1, limit: 2 });
      assert.deepEqual(stack.stack.map((f) => f.index), [1, 2]);
      assert.equal(stack.nextOffset, 3);
      assert.equal(stack.hasMore, stack.total > 3);
      assert.equal((await session.stack({ offset: stack.total, limit: 2 })).hasMore, false);
    } finally {
      await release(done);
    }
  });
});
