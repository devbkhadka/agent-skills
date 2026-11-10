/**
 * OS process introspection (macOS + Linux only): user processes, their LISTEN TCP ports and cwd.
 *
 * Everything platform-specific (`ps`, `lsof`) lives here so discovery logic stays portable.
 */

import { execFile } from 'node:child_process';

import { DbgError } from './ipc.mjs';

const SUPPORTED = new Set(['darwin', 'linux']);
const EXEC_TIMEOUT_MS = 5000;

/**
 * @typedef {{ pid: number, command: string }} ProcessInfo
 * @typedef {{ host: string, port: number }} ListenAddress
 */

function assertSupported() {
  if (!SUPPORTED.has(process.platform)) {
    throw new DbgError('UNSUPPORTED_PLATFORM', `process discovery needs ps/lsof (macOS or Linux); this is ${process.platform}`);
  }
}

/**
 * Run a command; resolves stdout. `okCodes` are exit codes that still count as success
 * (lsof exits 1 when nothing matched).
 * @param {string} file
 * @param {string[]} args
 * @param {number[]} [okCodes]
 * @returns {Promise<string>}
 */
function run(file, args, okCodes = [0]) {
  return new Promise((resolve, reject) => {
    execFile(file, args, { timeout: EXEC_TIMEOUT_MS, maxBuffer: 16 * 1024 * 1024 }, (err, stdout) => {
      if (!err) return resolve(stdout);
      const code = /** @type {any} */ (err).code;
      if (code === 'ENOENT') return reject(new DbgError('MISSING_TOOL', `\`${file}\` not found on PATH (needed for process discovery)`));
      if (typeof code === 'number' && okCodes.includes(code)) return resolve(stdout);
      reject(new DbgError('PROCESS_QUERY', `${file} ${args.join(' ')} failed: ${err.message}`));
    });
  });
}

/**
 * Processes owned by the current user (pid + full command line).
 * @returns {Promise<ProcessInfo[]>}
 */
export async function listUserProcesses() {
  assertSupported();
  const uid = String(process.getuid?.() ?? '');
  const out = await run('ps', ['-U', uid, '-o', 'pid=,args=']);
  /** @type {ProcessInfo[]} */
  const list = [];
  for (const line of out.split('\n')) {
    const match = /^\s*(\d+)\s+(.*)$/.exec(line);
    if (match) list.push({ pid: Number(match[1]), command: match[2] });
  }
  return list;
}

/**
 * Parse `lsof -F pn` output into pid -> names.
 * @param {string} out
 * @returns {Map<number, string[]>}
 */
function parseLsofFields(out) {
  /** @type {Map<number, string[]>} */
  const byPid = new Map();
  let pid = -1;
  for (const line of out.split('\n')) {
    if (line.startsWith('p')) {
      pid = Number(line.slice(1));
      if (!byPid.has(pid)) byPid.set(pid, []);
    } else if (line.startsWith('n') && pid !== -1) {
      byPid.get(pid)?.push(line.slice(1));
    }
  }
  return byPid;
}

/**
 * `127.0.0.1:9229`, `*:9229`, `[::1]:9229` -> connectable host + port.
 * @param {string} name
 * @returns {ListenAddress | null}
 */
export function parseListenName(name) {
  const match = /^(.*):(\d+)$/.exec(name.trim());
  if (!match) return null;
  let host = match[1].replace(/^\[(.*)\]$/, '$1');
  if (host === '*' || host === '0.0.0.0' || host === '') host = '127.0.0.1';
  else if (host === '::') host = '::1';
  return { host, port: Number(match[2]) };
}

/**
 * LISTEN TCP sockets of the given pids.
 * @param {number[]} pids
 * @returns {Promise<Map<number, ListenAddress[]>>}
 */
export async function listeningAddresses(pids) {
  assertSupported();
  /** @type {Map<number, ListenAddress[]>} */
  const result = new Map();
  if (pids.length === 0) return result;
  const out = await run('lsof', ['-nP', '-a', '-p', pids.join(','), '-iTCP', '-sTCP:LISTEN', '-F', 'pn'], [1]);
  for (const [pid, names] of parseLsofFields(out)) {
    /** @type {ListenAddress[]} */
    const addrs = [];
    for (const name of names) {
      const addr = parseListenName(name);
      if (addr && !addrs.some((a) => a.host === addr.host && a.port === addr.port)) addrs.push(addr);
    }
    result.set(pid, addrs);
  }
  return result;
}

/**
 * Working directories of the given pids (missing when not readable).
 * @param {number[]} pids
 * @returns {Promise<Map<number, string>>}
 */
export async function processCwds(pids) {
  assertSupported();
  /** @type {Map<number, string>} */
  const result = new Map();
  if (pids.length === 0) return result;
  const out = await run('lsof', ['-a', '-p', pids.join(','), '-d', 'cwd', '-F', 'pn'], [1]);
  for (const [pid, names] of parseLsofFields(out)) if (names[0]) result.set(pid, names[0]);
  return result;
}
