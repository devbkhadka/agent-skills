import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, realpathSync, rmSync, mkdirSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { parseArgs, usage } from '../lib/commands.mjs';
import { serializeOutput } from '../lib/output.mjs';
import { statePaths, serveCommands } from '../lib/ipc.mjs';
import { DBG, freePort, runDbg, killProcess } from './helpers.mjs';

test('CLI validates per-command flags, arity, numeric bounds and regex before IPC', () => {
  for (const args of [ ['status', '--port', '9229'], ['stop', 'extra'], ['break'], ['stack', '--limit', '61'], ['logs', '--limit', '501'], ['scope', '--frame', '0.5'], ['eval', 'x', '--depth', '6'], ['start', '--port', '65536'], ['start', '--port', '0'], ['wait', '--timeout', 'Infinity'], ['start', '--target', '['], ['start', '--idle-timeout', '999h'], ['scope', '--depth', '--all'], ['scope', '--all=false'] ]) {
    assert.throws(() => parseArgs(args), (e) => e.code === 'BAD_ARGS', args.join(' '));
  }
  assert.equal(parseArgs(['scope', '--vars', 'a,b', '--scope', 'closure']).flags.vars, 'a,b');
  assert.match(usage(), /Node >=22/);
  assert.throws(() => execFileSync(process.execPath, [DBG, 'scope', '--port', '1'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }), (error) => JSON.parse(error.stdout).error.code === 'BAD_ARGS');
  for (const args of [['status', '--bogus'], ['--bogus'], ['eval', '--bogus']]) {
    assert.throws(() => execFileSync(process.execPath, [DBG, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }), (error) => error.status === 1 && JSON.parse(error.stdout).error.code === 'BAD_ARGS', args.join(' '));
  }
  // An eval expression that starts with -- is positional after the separator.
  assert.deepEqual(parseArgs(['eval', '--', '--x']).positional, ['eval', '--x']);
});

test('budget-trimmed log pages keep omitted, nextSeq and hasMore consistent with returned hits', () => {
  const since = 10, matching = 150;
  const sample = { hits: Array.from({ length: 100 }, (_, i) => ({ seq: since + 1 + i, value: 'x'.repeat(400) })), nextSeq: 110, hasMore: true, latestSeq: 160, lastSeq: 160, omitted: matching - 100 };
  const untrimmed = { hits: Array.from({ length: 4 }, (_, i) => ({ seq: since + 1 + i, value: 'x'.repeat(6000) })), nextSeq: 14, hasMore: false, latestSeq: 14, lastSeq: 14 };
  for (const pretty of [false, true]) {
    for (const maxBytes of [1024, 4096, 16384]) {
      for (const [input, total] of [[sample, matching], [untrimmed, 4]]) {
        const json = JSON.parse(serializeOutput(input, { pretty, maxBytes, since }));
        assert.ok(json.outputTruncated, `${pretty}/${maxBytes}`);
        // Returned hits are contiguous from the cursor, and nothing is lost when resuming at nextSeq.
        json.hits.forEach((hit, i) => assert.equal(hit.seq, since + 1 + i));
        assert.equal(json.nextSeq, json.hits.at(-1)?.seq ?? since);
        assert.equal(json.hits.length + (json.omitted ?? 0), total, `${pretty}/${maxBytes}`);
        assert.equal(json.hasMore, json.hits.length < total);
      }
    }
  }
});

test('actual serialized stdout stays bounded for logs, many scopes and deep values', () => {
  for (const pretty of [false, true]) {
    for (const maxBytes of [1024, 4096, 16384]) {
      const samples = [
        { attached: true, scopes: Array.from({ length: 40 }, (_, index) => ({ type: 'local', index, variables: Array.from({ length: 50 }, (_, i) => ({ name: `v${i}`, value: { nested: { data: 'ø'.repeat(500) } } })) })) },
        { hits: Array.from({ length: 100 }, (_, i) => ({ seq: i + 11, value: 'x'.repeat(1000) })), nextSeq: 110, latestSeq: 120, lastSeq: 120, hasMore: true, dropped: 5 },
        { frame: { index: 0 }, scopes: [{ type: 'local', variables: [{ name: 'chosen', value: 'x'.repeat(20000) }] }] },
        { error: { code: 'TARGET_ERROR', message: 'm'.repeat(40000), detail: 'x'.repeat(40000) } },
      ];
      for (const sample of samples) {
        const stdout = serializeOutput(sample, { pretty, maxBytes, since: 10 });
        assert.ok(Buffer.byteLength(stdout) <= maxBytes, `${pretty}/${maxBytes}: ${Buffer.byteLength(stdout)}`);
        const json = JSON.parse(stdout);
        assert.ok(json.outputTruncated);
        if (sample.hits) {
          assert.equal(json.nextSeq, json.hits.at(-1)?.seq ?? 10);
          assert.equal(json.latestSeq, 120);
          assert.equal(json.dropped, 5);
          assert.equal(json.hasMore, true);
        }
        if (sample.error) assert.equal(json.error.code, 'TARGET_ERROR');
        if (sample.attached) assert.equal(json.attached, true);
      }
    }
  }
});

test('budget trimming frame pages preserves the cursor', () => {
  const sample = { stack: Array.from({ length: 60 }, (_, i) => ({ index: i + 60, source: 'x'.repeat(300) })), offset: 60, nextOffset: 120, total: 130, hasMore: true };
  const result = JSON.parse(serializeOutput(sample, { maxBytes: 1024 }));
  assert.equal(result.nextOffset, result.stack.at(-1)?.index + 1);
  assert.equal(result.total, 130);
});

test('start is serialized, idempotent, rejects conflicting attachment and reaps dead lock tickets', async () => {
  const home = mkdtempSync(path.join(os.tmpdir(), 'dbg-cli-contract-'));
  const env = { home, cwd: home };
  const port = await freePort();
  const target = spawn(process.execPath, [`--inspect=127.0.0.1:${port}`, '-e', 'setInterval(() => {}, 1000)'], { stdio: ['ignore', 'ignore', 'pipe'] });
  try {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('inspector did not start')), 5000);
      target.stderr.on('data', (data) => { if (String(data).includes('Debugger listening')) { clearTimeout(timeout); resolve(); } });
      target.once('exit', () => { clearTimeout(timeout); reject(new Error('target exited')); });
    });
    const hash = createHash('sha256').update(statePaths('default', { DBG_HOME: home }).dir).digest('hex').slice(0, 24);
    const lockDir = path.join(os.tmpdir(), 'js-runtime-debugger-start-locks', hash);
    mkdirSync(lockDir, { recursive: true });
    writeFileSync(path.join(lockDir, '2147483647-dead'), '1');
    const starts = await Promise.all([runDbg(env, ['start', '--port', String(port)]), runDbg(env, ['start', '--port', String(port)])]);
    for (const r of starts) assert.equal(r.code, 0, JSON.stringify(r));
    assert.equal(starts[0].json.pid, starts[1].json.pid);
    assert.equal(starts.filter((r) => r.json.started).length, 1);
    assert.equal(starts.filter((r) => r.json.alreadyRunning).length, 1);
    const conflict = await runDbg(env, ['start', '--port', String(port + (port < 65535 ? 1 : -1))]);
    assert.equal(conflict.json.error.code, 'SESSION_CONFLICT');
    const kindConflict = await runDbg(env, ['start', '--kind', 'chrome']);
    assert.equal(kindConflict.json.error.code, 'SESSION_CONFLICT');
    const other = mkdtempSync(path.join(os.tmpdir(), 'dbg-cli-contract-other-'));
    try {
      for (const args of [['--cwd', other], ['--target', 'nothing-like-this'], ['--web-root', 'public']]) {
        const r = await runDbg(env, ['start', ...args]);
        assert.equal(r.code, 1, args.join(' '));
        assert.equal(r.json.error.code, 'SESSION_CONFLICT', args.join(' '));
      }
      // An implicit cwd change (agent cd'd elsewhere, no --cwd) reuses the session and reports its config.
      const moved = await runDbg({ home, cwd: other }, ['start']);
      assert.equal(moved.code, 0, JSON.stringify(moved));
      assert.equal(moved.json.alreadyRunning, true);
      assert.equal(moved.json.config.cwd, realpathSync(home));
    } finally { rmSync(other, { recursive: true, force: true }); }
    const reuse = await runDbg(env, ['start']);
    assert.equal(reuse.json.alreadyRunning, true);
    const pretty = await runDbg(env, ['status', '--pretty', '--max-output-bytes', '1024']);
    assert.equal(pretty.code, 0);
  } finally {
    await runDbg(env, ['stop']);
    await killProcess(target);
    rmSync(home, { recursive: true, force: true });
  }
});


test('actual CLI stdout budget applies in compact and pretty modes', async () => {
  const home = mkdtempSync(path.join(os.tmpdir(), 'dbg-output-contract-'));
  const env = { home, cwd: home };
  const paths = statePaths('default', { DBG_HOME: home });
  mkdirSync(paths.dir, { recursive: true });
  writeFileSync(paths.pid, `${process.pid}\n`);
  const server = await serveCommands(paths.socket, async () => ({ attached: true, scopes: [{ type: 'local', variables: Object.fromEntries(Array.from({ length: 200 }, (_, i) => [`selected${i}`, { deep: { value: 'ø'.repeat(200) } }])) }] }));
  try {
    for (const pretty of [false, true]) {
      const r = await runDbg(env, ['status', '--max-output-bytes', '1024', ...(pretty ? ['--pretty'] : [])]);
      assert.equal(r.code, 0, JSON.stringify(r));
      assert.ok(Buffer.byteLength(r.stdout) <= 1024);
      assert.equal(r.json.attached, true);
      assert.ok(r.json.outputTruncated);
      assert.ok(r.json.scopes?.[0]?.variables.selected0 || r.json.outputTruncated.omittedItems);
      if (pretty) assert.match(r.stdout, /\n  /);
      else assert.equal(r.stdout.trim().split('\n').length, 1);
    }
  } finally {
    await new Promise((resolve) => server.close(resolve));
    rmSync(home, { recursive: true, force: true });
  }
});


test('application properties named hits and stack do not change top-level cursors', () => {
  const appValue = { hits: Array.from({ length: 10 }, () => ({ seq: 999, data: 'x'.repeat(200) })), stack: Array.from({ length: 10 }, () => ({ index: 999, data: 'x'.repeat(200) })) };
  const result = JSON.parse(serializeOutput({ hits: [{ seq: 11, value: appValue }], nextSeq: 11, latestSeq: 11, lastSeq: 11, hasMore: false }, { maxBytes: 1024, since: 10 }));
  assert.equal(result.nextSeq, 11);
  assert.equal(result.hasMore, false);
  const frames = JSON.parse(serializeOutput({ stack: [{ index: 4, value: appValue }], offset: 4, nextOffset: 5, total: 5, hasMore: false }, { maxBytes: 1024 }));
  assert.equal(frames.nextOffset, 5);
  assert.equal(frames.hasMore, false);
});

test('fallback bounds huge protected objects and Unicode field names', () => {
  const sample = { attached: true, frame: { index: 0, ['ø'.repeat(10000)]: 'v' }, error: { code: 'TARGET_ERROR', message: 'ø'.repeat(20000) } };
  for (const pretty of [false, true]) {
    const output = serializeOutput(sample, { pretty, maxBytes: 1024 });
    assert.ok(Buffer.byteLength(output) <= 1024);
    const result = JSON.parse(output);
    assert.equal(result.attached, true);
    assert.equal(result.error.code, 'TARGET_ERROR');
  }
});
