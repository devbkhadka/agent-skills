/**
 * Target discovery for `dbg targets` and `dbg start --pid`: which debuggable targets exist right now.
 *
 *   - node: user-owned node processes -> their LISTEN ports -> ports answering `/json/list`
 *     (finds `--inspect=localhost:0` random ports used by Nx `@nx/js:node` and VS Code auto-attach).
 *   - chrome: `/json/list` pages on common remote-debugging ports.
 *   - hermes: Metro's `/json/list` (React Native pages).
 */

import { DbgError } from './ipc.mjs';
import { listeningAddresses, listUserProcesses, processCwds } from './processes.mjs';
import { isModernHermesPage, listTargets } from './targets.mjs';

const PROBE_TIMEOUT_MS = 600;
const COMMAND_MAX = 160;
export const DEFAULT_CHROME_PORTS = [9222, 9229];
export const DEFAULT_METRO_PORT = 8081;
export const NO_INSPECTOR_HINT = 'start it with --inspect or use the serve:debug config';

/**
 * @typedef {{ kind: 'node', pid: number, host: string, port: number, id: string, title: string, url: string,
 *   cwd: string | null, command: string }} NodeInspector
 */

/**
 * Whether a command line runs the node binary (any version-suffixed name), or asks for an inspector.
 * @param {string} command
 */
export function isNodeCommand(command) {
  const exe = command.trim().split(/\s+/)[0] ?? '';
  const base = exe.split('/').pop() ?? '';
  return /^node(js)?[\d.]*$/.test(base) || /\s--inspect(-brk|-wait)?(=|\s|$)/.test(command);
}

/** @param {string} text */
function truncateCommand(text) {
  // Keep both ends: the executable and the script being run are what identify a process.
  if (text.length <= COMMAND_MAX) return text;
  const half = Math.floor((COMMAND_MAX - 1) / 2);
  return `${text.slice(0, half)}…${text.slice(text.length - half)}`;
}

/**
 * `/json/list` on a port, or null when it is not a CDP endpoint.
 * @param {string} host
 * @param {number} port
 */
async function probe(host, port) {
  try {
    const list = await listTargets(host, port, { timeoutMs: PROBE_TIMEOUT_MS });
    return list.some((t) => typeof t.webSocketDebuggerUrl === 'string') ? list : null;
  } catch {
    return null;
  }
}

/**
 * Inspector endpoints of node processes owned by this user.
 * @param {{ pids?: number[] }} [options] restrict to these pids (default: every node process)
 * @returns {Promise<NodeInspector[]>}
 */
export async function findNodeInspectors(options = {}) {
  const all = await listUserProcesses();
  const wanted = options.pids ? new Set(options.pids) : null;
  const procs = all.filter((p) => p.pid !== process.pid && (wanted ? wanted.has(p.pid) : isNodeCommand(p.command)));
  if (procs.length === 0) return [];
  const listening = await listeningAddresses(procs.map((p) => p.pid));
  const candidates = procs.filter((p) => (listening.get(p.pid) ?? []).length > 0);
  /** @type {NodeInspector[]} */
  const found = [];
  await Promise.all(
    candidates.flatMap((proc) =>
      (listening.get(proc.pid) ?? []).map(async ({ host, port }) => {
        const list = await probe(host, port);
        for (const t of list ?? []) {
          // Metro is a node process whose /json/list proxies React Native pages: those are hermes targets.
          if (typeof t.webSocketDebuggerUrl !== 'string' || /** @type {any} */ (t).reactNative) continue;
          found.push({ kind: 'node', pid: proc.pid, host, port, id: t.id, title: t.title, url: t.url, cwd: null, command: proc.command });
        }
      }),
    ),
  );
  // `--inspect=localhost:0` binds ::1 and 127.0.0.1 on two different random ports for the same
  // target; keep one endpoint per (pid, target), preferring IPv4.
  /** @type {Map<string, NodeInspector>} */
  const unique = new Map();
  for (const f of found.sort((a, b) => Number(a.host.includes(':')) - Number(b.host.includes(':')) || a.port - b.port)) {
    const key = `${f.pid}\0${f.id}`;
    if (!unique.has(key)) unique.set(key, f);
  }
  const result = [...unique.values()];
  const cwds = await processCwds([...new Set(result.map((f) => f.pid))]).catch(() => new Map());
  for (const f of result) f.cwd = cwds.get(f.pid) ?? null;
  return result.sort((a, b) => a.pid - b.pid || a.port - b.port);
}

/**
 * Inspector endpoint of one process (`dbg start --pid`).
 * @param {number} pid
 * @returns {Promise<NodeInspector>}
 */
export async function inspectorForPid(pid) {
  const procs = await listUserProcesses();
  if (!procs.some((p) => p.pid === pid)) {
    throw new DbgError('NO_PROCESS', `no process ${pid} owned by the current user`);
  }
  const [found] = await findNodeInspectors({ pids: [pid] });
  if (!found) {
    throw new DbgError('NO_INSPECTOR', `process ${pid} is not listening on a CDP inspector port`, { hint: NO_INSPECTOR_HINT });
  }
  return found;
}

/**
 * Re-find a `--pid` target after it restarted (watch-mode re-fork picks a new random port): the
 * original pid while it still serves an inspector, else the newest node process with the same
 * command line and cwd.
 * @param {{ pid: number, command: string, cwd: string | null }} origin
 * @returns {Promise<NodeInspector | null>}
 */
export async function relocateInspector(origin) {
  const inspectors = await findNodeInspectors();
  const same = inspectors.find((i) => i.pid === origin.pid);
  if (same) return same;
  const twins = inspectors.filter((i) => i.command === origin.command && (origin.cwd === null || i.cwd === origin.cwd));
  return twins.sort((a, b) => b.pid - a.pid)[0] ?? null;
}

/**
 * @param {number[]} ports
 */
async function chromeTargets(ports) {
  const results = await Promise.all(
    [...new Set(ports)].map(async (port) => {
      const list = (await probe('127.0.0.1', port)) ?? [];
      return list
        .filter((t) => t.type === 'page' && typeof t.webSocketDebuggerUrl === 'string')
        .map((t) => ({ kind: 'chrome', host: '127.0.0.1', port, id: t.id, title: t.title, url: t.url }));
    }),
  );
  return results.flat();
}

/**
 * @param {number} port
 */
async function hermesTargets(port) {
  const list = (await probe('127.0.0.1', port)) ?? [];
  return list
    .filter((t) => typeof t.webSocketDebuggerUrl === 'string')
    .map((t) => ({
      kind: 'hermes',
      host: '127.0.0.1',
      port,
      id: t.id,
      title: t.title,
      description: t.description,
      deviceName: t.deviceName,
      appId: t.appId,
      modern: isModernHermesPage(t),
    }));
}

/**
 * Every debuggable target of the requested kind(s).
 * @param {{ kind?: string, port?: number }} options
 */
export async function discoverTargets(options) {
  const kinds = options.kind ? [options.kind] : ['node', 'chrome', 'hermes'];
  for (const kind of kinds) {
    if (!['node', 'chrome', 'hermes'].includes(kind)) {
      throw new DbgError('BAD_ARGS', `unsupported --kind "${kind}" (supported: node, chrome, hermes)`);
    }
  }
  /** @type {Record<string, unknown>[]} */
  const targets = [];
  /** @type {Record<string, string>} */
  const errors = {};
  await Promise.all(
    kinds.map(async (kind) => {
      try {
        if (kind === 'node') {
          const found = await findNodeInspectors();
          const filtered = options.port ? found.filter((f) => f.port === options.port) : found;
          targets.push(...filtered.map((f) => ({ ...f, command: truncateCommand(f.command) })));
        } else if (kind === 'chrome') {
          targets.push(...(await chromeTargets([...DEFAULT_CHROME_PORTS, ...(options.port ? [options.port] : [])])));
        } else {
          targets.push(...(await hermesTargets(options.port ?? DEFAULT_METRO_PORT)));
        }
      } catch (err) {
        // One kind failing (e.g. no lsof) must not hide the others; surface it only when asked for.
        errors[kind] = /** @type {Error} */ (err).message;
      }
    }),
  );
  const order = { node: 0, chrome: 1, hermes: 2 };
  targets.sort((a, b) => order[/** @type {'node'} */ (a.kind)] - order[/** @type {'node'} */ (b.kind)]);
  if (options.kind && errors[options.kind]) throw new DbgError('DISCOVERY', errors[options.kind]);
  return { targets, ...(Object.keys(errors).length ? { errors } : {}) };
}
