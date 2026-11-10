/**
 * Session state directory + CLI <-> daemon transport.
 *
 * Each session owns `${DBG_HOME:-$TMPDIR/js-runtime-debugger}/<session>/` holding:
 *   daemon.sock  HTTP/JSON endpoint (node:http over a unix socket; `/tmp/dbg-<hash>.sock` when the
 *                path would exceed the OS socket-path limit)
 *   daemon.pid   daemon process id
 *   daemon.log   daemon stdout/stderr
 *   .dbg-session ownership marker written at creation; only marked dirs are deleted recursively
 *
 * Wire format: `POST /cmd` with `{ cmd, args }`; the reply is always HTTP 200 with either
 * `{ ok: true, result }` or `{ ok: false, error: { code, message, ... } }`.
 */

import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, rmdirSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

/** Unix socket paths are capped at 104 bytes on macOS (108 on Linux). */
const MAX_SOCKET_PATH = 103;

/** Error carrying a stable machine-readable code; serialized as `{ error: { code, message } }`. */
export class DbgError extends Error {
  /**
   * @param {string} code
   * @param {string} message
   * @param {Record<string, unknown>} [details] extra fields merged into the error JSON
   */
  constructor(code, message, details) {
    super(message);
    this.name = 'DbgError';
    this.code = code;
    this.details = details;
  }
}

/**
 * @param {unknown} err
 * @returns {{ code: string, message: string } & Record<string, unknown>}
 */
export function toErrorJSON(err) {
  if (err instanceof DbgError) return { code: err.code, message: err.message, ...(err.details ?? {}) };
  const e = /** @type {{ code?: unknown, message?: unknown }} */ (err ?? {});
  return {
    code: typeof e.code === 'string' ? e.code : 'INTERNAL',
    message: typeof e.message === 'string' ? e.message : String(err),
  };
}

/** Ownership marker: a session dir is deleted recursively only when it carries this file. */
export const SESSION_MARKER = '.dbg-session';

/** @param {string} session */
export function assertSessionName(session) {
  if (typeof session !== 'string' || !/^[A-Za-z0-9._-]{1,64}$/.test(session) || session.startsWith('.')) {
    throw new DbgError('BAD_ARGS', `invalid session name "${session}" (use [A-Za-z0-9._-], max 64 chars, must not start with ".")`);
  }
}

/**
 * @typedef {{ dir: string, socket: string, pid: string, log: string, marker: string }} StatePaths
 */

/**
 * @param {string} session
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {StatePaths}
 */
export function statePaths(session, env = process.env) {
  assertSessionName(session);
  const home = env.DBG_HOME && env.DBG_HOME.trim() !== '' ? env.DBG_HOME : path.join(os.tmpdir(), 'js-runtime-debugger');
  const root = path.resolve(home);
  const dir = path.resolve(root, session);
  if (dir === root || path.dirname(dir) !== root) {
    throw new DbgError('BAD_ARGS', `session "${session}" resolves outside the state dir ${root}`);
  }
  let socket = path.join(dir, 'daemon.sock');
  if (Buffer.byteLength(socket) > MAX_SOCKET_PATH) {
    // Deep state dirs overflow the unix socket path limit: fall back to a short, dir-derived name.
    const hash = createHash('sha1').update(dir).digest('hex').slice(0, 16);
    socket = path.join(process.platform === 'win32' ? os.tmpdir() : '/tmp', `dbg-${hash}.sock`);
  }
  return {
    dir,
    socket,
    pid: path.join(dir, 'daemon.pid'),
    log: path.join(dir, 'daemon.log'),
    marker: path.join(dir, SESSION_MARKER),
  };
}

/**
 * Create the session dir; a dir this call creates gets the ownership marker. A pre-existing
 * marker-less dir stays unmarked so cleanup never recursively deletes content it did not create.
 * @param {StatePaths} paths
 */
export function ensureStateDir(paths) {
  const created = mkdirSync(paths.dir, { recursive: true });
  if (created !== undefined) writeFileSync(paths.marker, `${process.pid}\n`, { flag: 'w' });
}

/** @param {number} pid */
export function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return /** @type {NodeJS.ErrnoException} */ (err).code === 'EPERM';
  }
}

/**
 * @param {StatePaths} paths
 * @returns {number | null}
 */
export function readPid(paths) {
  try {
    const pid = Number.parseInt(readFileSync(paths.pid, 'utf8').trim(), 10);
    return Number.isInteger(pid) ? pid : null;
  } catch {
    return null;
  }
}

/**
 * Remove the state dir when its daemon is gone. Returns true when a live daemon owns it.
 * @param {StatePaths} paths
 */
export function cleanIfStale(paths) {
  const pid = readPid(paths);
  if (pid !== null && isProcessAlive(pid)) return true;
  removeState(paths);
  return false;
}

/**
 * Delete the state dir and the socket (which may live outside it). Only a dir carrying the
 * ownership marker is deleted recursively; otherwise just the known daemon files are unlinked
 * and the dir is removed only when that leaves it empty.
 * @param {StatePaths} paths
 */
export function removeState(paths) {
  rmSync(paths.socket, { force: true });
  if (existsSync(paths.marker)) {
    rmSync(paths.dir, { recursive: true, force: true });
  } else {
    for (const file of [paths.pid, paths.log]) rmSync(file, { force: true });
    try {
      rmdirSync(paths.dir);
    } catch (err) {
      const code = /** @type {NodeJS.ErrnoException} */ (err).code;
      if (code !== 'ENOENT' && code !== 'ENOTEMPTY' && code !== 'EEXIST' && code !== 'ENOTDIR') throw err;
    }
  }
}

/**
 * Last `maxLines` lines of a log file ('' when missing).
 * @param {string} file
 * @param {number} [maxLines]
 */
export function tailFile(file, maxLines = 30) {
  try {
    const lines = readFileSync(file, 'utf8').split('\n');
    return lines.slice(-maxLines - 1).join('\n').trim();
  } catch {
    return '';
  }
}

/**
 * Send one command to the daemon.
 * @param {string} socketPath
 * @param {string} cmd
 * @param {Record<string, unknown>} args
 * @param {{ timeoutMs?: number }} [options]
 * @returns {Promise<unknown>} the `result` field; rejects with DbgError on `{ ok: false }`
 */
export function sendCommand(socketPath, cmd, args, options = {}) {
  const timeoutMs = options.timeoutMs ?? 30_000;
  const body = JSON.stringify({ cmd, args });
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        socketPath,
        path: '/cmd',
        method: 'POST',
        headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) },
      },
      (res) => {
        /** @type {Buffer[]} */
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          let reply;
          try {
            reply = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          } catch (err) {
            reject(new DbgError('IPC', `daemon sent invalid JSON: ${/** @type {Error} */ (err).message}`));
            return;
          }
          if (reply && reply.ok) resolve(reply.result);
          else {
            const { code = 'INTERNAL', message = 'unknown daemon error', ...details } = reply?.error ?? {};
            reject(new DbgError(code, message, details));
          }
        });
        res.on('error', reject);
      },
    );
    req.setTimeout(timeoutMs, () => {
      req.destroy(new DbgError('IPC_TIMEOUT', `daemon did not answer "${cmd}" within ${timeoutMs} ms`));
    });
    req.on('error', (err) => {
      const code = /** @type {NodeJS.ErrnoException} */ (err).code;
      if (code === 'ENOENT' || code === 'ECONNREFUSED') {
        reject(new DbgError('NOT_RUNNING', 'no daemon for this session (run `dbg start` first)'));
      } else reject(err);
    });
    req.end(body);
  });
}

/**
 * Serve commands on a unix socket.
 * @param {string} socketPath
 * @param {(cmd: string, args: Record<string, unknown>) => Promise<unknown>} handler
 * @param {{ onRequestStart?: () => void, onRequestEnd?: () => void }} [hooks]
 * @returns {Promise<http.Server>}
 */
export function serveCommands(socketPath, handler, hooks = {}) {
  const server = http.createServer((req, res) => {
    /** @param {unknown} payload */
    const reply = (payload) => {
      const text = JSON.stringify(payload);
      res.writeHead(200, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(text) });
      res.end(text);
    };
    if (req.method !== 'POST' || req.url !== '/cmd') {
      reply({ ok: false, error: { code: 'BAD_REQUEST', message: 'POST /cmd only' } });
      return;
    }
    hooks.onRequestStart?.();
    /** @type {Buffer[]} */
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', async () => {
      try {
        const { cmd, args } = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if (typeof cmd !== 'string') throw new DbgError('BAD_REQUEST', 'missing "cmd"');
        const result = await handler(cmd, args && typeof args === 'object' ? args : {});
        reply({ ok: true, result });
      } catch (err) {
        reply({ ok: false, error: toErrorJSON(err) });
      } finally {
        hooks.onRequestEnd?.();
      }
    });
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(socketPath, () => {
      server.off('error', reject);
      resolve(server);
    });
  });
}
