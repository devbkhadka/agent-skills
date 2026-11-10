#!/usr/bin/env node
/**
 * dbg — agent-facing CLI for debugging a running JS process over CDP.
 *
 * stdout: exactly one JSON object per call. stderr: diagnostics.
 * Exit codes: 0 ok, 1 error ({ error: { code, message } }), 2 wait/step timeout.
 * Every command accepts `--session <name>` (default "default"). Lines/columns are 1-based.
 */

import { spawn } from 'node:child_process';
import { closeSync, mkdirSync, openSync, readFileSync, unlinkSync, writeFileSync, readdirSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import os from 'node:os';
import { parseArgs, parseDuration, numberFlag as intFlag, usage } from './lib/commands.mjs';
import { serializeOutput } from './lib/output.mjs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  DbgError,
  cleanIfStale,
  ensureStateDir,
  isProcessAlive,
  readPid,
  removeState,
  sendCommand,
  statePaths,
  tailFile,
  toErrorJSON,
} from './lib/ipc.mjs';
import { DEFAULT_METRO_PORT, discoverTargets, inspectorForPid } from './lib/discovery.mjs';
import { getProfile } from './lib/targets.mjs';

const DAEMON_PATH = path.join(import.meta.dirname ?? path.dirname(fileURLToPath(import.meta.url)), 'lib', 'daemon.mjs');

/** @param {number} ms */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** Start-lock waiters allow the holder its attach window plus status/stop overhead. */
const START_LOCK_MIN_WAIT_MS = 120_000;
const START_LOCK_MARGIN_MS = 30_000;

/** Serialize startup with per-process tickets outside the removable session directory.
 * Tickets are published after exclusive creation; unpublished live tickets block admission.
 * Dead owners are ignored and their files removed; no lock directory is ever deleted.
 * A ticket records `<rank> <attachTimeoutMs>`; waiters give the current head (lowest rank)
 * its whole attach window plus margin, restarting the clock whenever the head changes.
 */
async function start(session, flags) {
  const paths = statePaths(session);
  const hash = createHash('sha256').update(paths.dir).digest('hex').slice(0, 24);
  const lockDir = path.join(os.tmpdir(), 'js-runtime-debugger-start-locks', hash);
  mkdirSync(lockDir, { recursive: true });
  const ticketPath = path.join(lockDir, `${process.pid}-${randomUUID()}`);
  const fd = openSync(ticketPath, 'wx', 0o600);
  const rank = process.hrtime.bigint();
  const attachTimeoutMs = (intFlag(flags['attach-timeout'], 'attach-timeout') ?? 15) * 1000;
  try {
    writeFileSync(fd, `${rank} ${attachTimeoutMs}`);
  } finally { closeSync(fd); }
  /** @type {{ path: string, rank: bigint } | undefined} */
  let head;
  let deadline = 0;
  try {
    for (;;) {
      let blocked = false;
      /** @type {{ path: string, rank: bigint, windowMs: number } | undefined} */
      let currentHead;
      for (const name of readdirSync(lockDir)) {
        const peerPath = path.join(lockDir, name);
        if (peerPath === ticketPath) continue;
        const peerPid = Number(name.split('-')[0]);
        if (!Number.isInteger(peerPid) || peerPid < 1) continue;
        if (!isProcessAlive(peerPid)) {
          try { unlinkSync(peerPath); } catch { /* another waiter reaped it */ }
          continue;
        }
        let ticket;
        try { ticket = readFileSync(peerPath, 'utf8').trim(); } catch { continue; }
        const match = /^(\d+)(?: (\d+))?$/.exec(ticket);
        // Unpublished tickets rank first: their owner may already be admitted.
        const peerRank = match ? BigInt(match[1]) : -1n;
        if (peerRank < rank || (peerRank === rank && peerPath < ticketPath)) {
          blocked = true;
          const windowMs = Math.max(START_LOCK_MIN_WAIT_MS, Number(match?.[2] ?? 0) + START_LOCK_MARGIN_MS);
          if (!currentHead || peerRank < currentHead.rank || (peerRank === currentHead.rank && peerPath < currentHead.path)) currentHead = { path: peerPath, rank: peerRank, windowMs };
        }
      }
      if (!blocked) return await startLocked(session, flags);
      if (currentHead && (currentHead.path !== head?.path || currentHead.rank !== head?.rank)) {
        head = currentHead;
        deadline = Date.now() + currentHead.windowMs;
      }
      if (Date.now() >= deadline) throw new DbgError('START_BUSY', 'another start is still running past its attach window; retry after it completes');
      await sleep(50);
    }
  } finally { try { unlinkSync(ticketPath); } catch { /* already gone */ } }
}

/** Explicit flags must agree with the original attachment before reuse or reattachment. */
function assertCompatibleSession(flags, status) {
  const config = status.config;
  const keys = ['kind', 'host', 'port', 'pid', 'cwd', 'target', 'web-root', 'metro-root', 'watch-folders', 'idle-timeout'];
  const provided = keys.filter((key) => flags[key] !== undefined);
  if (!config) {
    if (provided.length) throw new DbgError('SESSION_CONFLICT', 'existing daemon cannot verify attachment settings; stop it and start again');
    return;
  }
  const cwd = path.resolve(flags.cwd ?? config.cwd);
  const requested = {
    kind: flags.kind, host: flags.host, port: flags.port === undefined ? undefined : Number(flags.port),
    pid: flags.pid === undefined ? undefined : Number(flags.pid), cwd: flags.cwd === undefined ? undefined : cwd,
    target: flags.target, webRoot: flags['web-root'] === undefined ? undefined : path.resolve(cwd, flags['web-root']),
    metroRoot: flags['metro-root'] === undefined ? undefined : path.resolve(cwd, flags['metro-root']),
    watchFolders: flags['watch-folders'] === undefined ? undefined : flags['watch-folders'].split(',').map((f) => f.trim()).filter(Boolean).map((f) => path.resolve(cwd, f)),
    idleTimeoutMs: flags['idle-timeout'] === undefined ? undefined : parseDuration(flags['idle-timeout']),
  };
  const current = { ...config, pid: config.origin?.pid };
  const conflicts = Object.keys(requested).filter((key) => requested[key] !== undefined && JSON.stringify(requested[key]) !== JSON.stringify(current[key]));
  if (conflicts.length) throw new DbgError('SESSION_CONFLICT', `session already uses different ${conflicts.join(', ')}; choose another --session or stop it first`, { conflicts });
}

/**
 * @param {string} session
 * @param {Record<string, string>} flags
 */
async function startLocked(session, flags) {
  const paths = statePaths(session);
  const attachTimeoutMs = (intFlag(flags['attach-timeout'], 'attach-timeout') ?? 15) * 1000;
  if (cleanIfStale(paths)) {
    /** @type {any} */
    let status;
    try {
      status = await sendCommand(paths.socket, 'status', {}, { timeoutMs: 5000 });
    } catch (err) {
      throw new DbgError('DAEMON_UNRESPONSIVE', `daemon pid ${readPid(paths)} is alive but not answering (${/** @type {Error} */ (err).message}); run \`dbg stop\``);
    }
    assertCompatibleSession(flags, status);
    if (status.state === 'displaced') return reattach(paths, attachTimeoutMs);
    return { alreadyRunning: true, ...status };
  }
  const kind = flags.kind ?? 'node';
  getProfile(kind);
  const cwd = path.resolve(flags.cwd ?? process.cwd());
  let host = flags.host ?? '127.0.0.1';
  let port = intFlag(flags.port, 'port');
  /** @type {{ pid: number, command: string, cwd: string | null } | undefined} */
  let origin;
  const pid = intFlag(flags.pid, 'pid');
  if (pid !== undefined) {
    if (kind !== 'node') throw new DbgError('BAD_ARGS', '--pid is for node processes (use --port for chrome/hermes)');
    const found = await inspectorForPid(pid);
    ({ host, port } = found);
    origin = { pid: found.pid, command: found.command, cwd: found.cwd };
  }
  if (!port && kind === 'hermes') port = DEFAULT_METRO_PORT;
  if (!port) throw new DbgError('BAD_ARGS', 'start needs --port <inspector port> or --pid <pid> (see `dbg targets`)');
  const config = {
    session,
    host,
    port,
    kind,
    cwd,
    ...(flags.target ? { target: flags.target } : {}),
    ...(origin ? { origin } : {}),
    ...(kind === 'chrome' ? { webRoot: path.resolve(cwd, flags['web-root'] ?? '.') } : {}),
    ...(kind === 'hermes'
      ? {
          metroRoot: path.resolve(cwd, flags['metro-root'] ?? '.'),
          watchFolders: (flags['watch-folders'] ?? '')
            .split(',')
            .map((f) => f.trim())
            .filter(Boolean)
            .map((f) => path.resolve(cwd, f)),
        }
      : {}),
    idleTimeoutMs: parseDuration(flags['idle-timeout'] ?? '2h'),
  };
  if (flags.target) new RegExp(flags.target);

  ensureStateDir(paths);
  const logFd = openSync(paths.log, 'a');
  const child = spawn(process.execPath, [DAEMON_PATH, '--config', JSON.stringify(config)], {
    detached: true,
    stdio: ['ignore', logFd, logFd],
    env: process.env,
  });
  child.unref();
  closeSync(logFd);
  const daemonPid = /** @type {number} */ (child.pid);

  const deadline = Date.now() + attachTimeoutMs;
  /** @type {any} */
  let status = null;
  while (Date.now() < deadline) {
    if (!isProcessAlive(daemonPid)) {
      const tail = tailFile(paths.log);
      removeState(paths);
      throw new DbgError('DAEMON_EXITED', 'daemon exited during startup', { log: tail });
    }
    try {
      status = await sendCommand(paths.socket, 'status', {}, { timeoutMs: 2000 });
      if (status.attached) return { started: true, ...status };
    } catch {
      // socket not up yet
    }
    await sleep(100);
  }
  const tail = tailFile(paths.log);
  await stopDaemon(paths);
  throw new DbgError('ATTACH_TIMEOUT', `could not attach to ${config.host}:${port} within ${attachTimeoutMs / 1000} s`, {
    ...(status?.lastError ? { lastError: status.lastError } : {}),
    log: tail,
  });
}

/**
 * Reattach a session that another debugger displaced (single-client targets such as Hermes).
 * @param {import('./lib/ipc.mjs').StatePaths} paths
 * @param {number} attachTimeoutMs
 */
async function reattach(paths, attachTimeoutMs) {
  await sendCommand(paths.socket, 'reattach', {}, { timeoutMs: 5000 });
  const deadline = Date.now() + attachTimeoutMs;
  /** @type {any} */
  let status = null;
  while (Date.now() < deadline) {
    status = await sendCommand(paths.socket, 'status', {}, { timeoutMs: 2000 });
    if (status.attached) return { reattached: true, ...status };
    await sleep(100);
  }
  throw new DbgError('ATTACH_TIMEOUT', `could not reattach within ${attachTimeoutMs / 1000} s`, {
    ...(status?.lastError ? { lastError: status.lastError } : {}),
  });
}

/**
 * Ask the daemon to stop, escalate to signals, and remove the state dir.
 * @param {import('./lib/ipc.mjs').StatePaths} paths
 */
async function stopDaemon(paths) {
  const pid = readPid(paths);
  if (pid === null || !isProcessAlive(pid)) {
    removeState(paths);
    return { stopped: false, reason: 'not running' };
  }
  try {
    await sendCommand(paths.socket, 'stop', {}, { timeoutMs: 5000 });
  } catch {
    process.kill(pid, 'SIGTERM');
  }
  for (let i = 0; i < 80 && isProcessAlive(pid); i++) await sleep(100);
  if (isProcessAlive(pid)) {
    process.stderr.write(`dbg: daemon ${pid} did not exit; sending SIGKILL\n`);
    process.kill(pid, 'SIGKILL');
  }
  removeState(paths);
  return { stopped: true, pid };
}

/**
 * Map a CLI command to a daemon command + args + IPC timeout.
 * @param {string} command
 * @param {string[]} rest
 * @param {Record<string, string>} flags
 * @returns {{ cmd: string, args: Record<string, unknown>, timeoutMs?: number }}
 */
function toRequest(command, rest, flags) {
  /** @param {number} min */
  const need = (min) => {
    if (rest.length < min) throw new DbgError('BAD_ARGS', `${command}: missing argument`);
  };
  switch (command) {
    case 'status':
    case 'list':
    case 'clear':
    case 'continue':
    case 'pause':
      return { cmd: command, args: {} };
    case 'reload':
      return { cmd: 'reload', args: {} };
    case 'stack':
      return { cmd: 'stack', args: { all: flags.all === 'true', offset: intFlag(flags.offset, 'offset'), limit: intFlag(flags.limit, 'limit') }, timeoutMs: 60_000 };
    case 'break':
      need(1);
      return { cmd: 'break', args: { location: rest[0], condition: flags.if }, timeoutMs: 120_000 };
    case 'logpoint':
      need(2);
      return { cmd: 'logpoint', args: { location: rest[0], expr: rest.slice(1).join(' ') }, timeoutMs: 120_000 };
    case 'remove':
      need(1);
      return { cmd: 'remove', args: { id: rest[0] } };
    case 'wait': {
      const timeout = intFlag(flags.timeout, 'timeout') ?? 300;
      return { cmd: 'wait', args: { timeout }, timeoutMs: (timeout + 30) * 1000 };
    }
    case 'scope':
      return {
        cmd: 'scope',
        args: { frame: intFlag(flags.frame, 'frame'), depth: intFlag(flags.depth, 'depth'), all: flags.all === 'true', vars: flags.vars, scope: flags.scope },
        timeoutMs: 60_000,
      };
    case 'eval':
      need(1);
      return {
        cmd: 'eval',
        args: { expr: rest.join(' '), frame: intFlag(flags.frame, 'frame'), depth: intFlag(flags.depth, 'depth') },
        timeoutMs: 60_000,
      };
    case 'step': {
      need(1);
      const timeout = intFlag(flags.timeout, 'timeout') ?? 30;
      return { cmd: 'step', args: { kind: rest[0], timeout }, timeoutMs: (timeout * 10 + 30) * 1000 };
    }
    case 'exceptions':
      need(1);
      return { cmd: 'exceptions', args: { state: rest[0] } };
    case 'logs':
      return { cmd: 'logs', args: { since: intFlag(flags.since, 'since'), limit: intFlag(flags.limit, 'limit'), tail: flags.tail === 'true' } };
    default:
      throw new DbgError('BAD_ARGS', `unknown command "${command}"; see dbg --help`);
  }
}

/** @param {string[]} argv */
async function run(argv) {
  if (Number(process.versions.node.split('.')[0]) < 22) throw new DbgError('NODE_VERSION', 'dbg requires Node >=22');
  const { positional, flags } = parseArgs(argv);
  const [command, ...rest] = positional;
  if (!command || flags.help) return { usage: usage(command) };
  const session = flags.session ?? 'default';
  if (command === 'start') return start(session, flags);
  if (command === 'targets') return discoverTargets({ kind: flags.kind, port: intFlag(flags.port, 'port') });
  const paths = statePaths(session);
  if (command === 'stop') return stopDaemon(paths);
  if (!cleanIfStale(paths)) throw new DbgError('NOT_RUNNING', `no daemon for session "${session}" (run \`dbg start --port <n>\`)`);
  const { cmd, args, timeoutMs } = toRequest(command, rest, flags);
  return sendCommand(paths.socket, cmd, args, { timeoutMs: timeoutMs ?? 30_000 });
}

const argv = process.argv.slice(2);
let outputOptions = {};
try {
  const { flags } = parseArgs(argv);
  outputOptions = { pretty: flags.pretty === 'true', maxBytes: intFlag(flags['max-output-bytes'], 'max-output-bytes') ?? 16384, since: intFlag(flags.since, 'since') ?? 0 };
} catch { /* run reports validation errors */ }
run(argv).then(
  (result) => process.stdout.write(serializeOutput(result, outputOptions)),
  (err) => {
    const error = toErrorJSON(err);
    if (!(err instanceof DbgError)) process.stderr.write(`${err?.stack ?? err}\n`);
    process.stdout.write(serializeOutput({ error }, outputOptions));
    process.exitCode = error.code === 'WAIT_TIMEOUT' ? 2 : 1;
  },
);
