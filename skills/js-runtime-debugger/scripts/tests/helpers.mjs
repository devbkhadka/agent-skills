/**
 * Shared helpers for the end-to-end suites (not a test file itself).
 */

import { execFile } from 'node:child_process';
import net from 'node:net';
import path from 'node:path';

export const DBG = path.join(import.meta.dirname, '..', 'dbg.mjs');

/** @returns {Promise<number>} */
export function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = /** @type {net.AddressInfo} */ (server.address());
      server.close(() => resolve(port));
    });
  });
}

/** @param {number} pid */
export function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * Run the CLI; returns exit code and parsed stdout JSON.
 * @param {{ home: string, cwd: string }} env DBG_HOME and working directory
 * @param {string[]} args
 * @returns {Promise<{ code: number, json: any, stderr: string, stdout: string }>}
 */
export function runDbg(env, args) {
  return new Promise((resolve) => {
    execFile(
      process.execPath,
      [DBG, ...args],
      { cwd: env.cwd, env: { ...process.env, DBG_HOME: env.home }, timeout: 30_000 },
      (err, stdout, stderr) => {
        const code = err ? (typeof err.code === 'number' ? err.code : 1) : 0;
        let json;
        try {
          json = JSON.parse(stdout);
        } catch {
          json = { unparsable: stdout };
        }
        resolve({ code, json, stderr, stdout });
      },
    );
  });
}

/**
 * @param {() => Promise<boolean>} check
 * @param {number} timeoutMs
 * @param {string} what
 */
export async function eventually(check, timeoutMs, what) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await check().catch(() => false)) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** @param {import('node:child_process').ChildProcess | null} child */
export async function killProcess(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((r) => child.once('exit', r));
  child.kill('SIGKILL');
  await exited;
}
