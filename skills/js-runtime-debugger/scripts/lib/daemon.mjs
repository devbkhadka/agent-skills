/**
 * Detached daemon entry point: one per session. Holds the CDP connection so breakpoints persist
 * between CLI calls, serves commands on `<state dir>/daemon.sock`, exits on `stop`, idle timeout,
 * SIGTERM/SIGINT/SIGHUP — always removing its breakpoints, resuming the target and cleaning the
 * state dir.
 *
 * Spawned by `dbg start` as: node daemon.mjs --config '<json>'
 */

import { unlinkSync, writeFileSync } from 'node:fs';

import { relocateInspector } from './discovery.mjs';
import { DbgError, ensureStateDir, removeState, serveCommands, statePaths } from './ipc.mjs';
import { DebugSession } from './session.mjs';
import { getProfile } from './targets.mjs';

const SHUTDOWN_CAP_MS = 6000;

/**
 * @typedef {{ session: string, host: string, port: number, kind: string, cwd: string, target?: string,
 *   webRoot?: string, metroRoot?: string, watchFolders?: string[], idleTimeoutMs: number,
 *   origin?: { pid: number, command: string, cwd: string | null } }} DaemonConfig
 *
 * `origin` (from `dbg start --pid`): the process the user picked; when it re-forks onto a new
 * random inspector port the daemon follows its twin (same command line and cwd).
 */

/** @param {string} msg */
function log(msg) {
  process.stderr.write(`${new Date().toISOString()} ${msg}\n`);
}

/** @returns {DaemonConfig} */
function readConfig() {
  const i = process.argv.indexOf('--config');
  if (i === -1 || !process.argv[i + 1]) throw new Error('daemon: missing --config <json>');
  return JSON.parse(process.argv[i + 1]);
}

async function main() {
  const config = readConfig();
  const paths = statePaths(config.session);
  ensureStateDir(paths);
  try {
    unlinkSync(paths.socket);
  } catch {
    // no stale socket
  }

  const origin = config.origin;
  let targetPid = origin?.pid;
  const session = new DebugSession({
    profile: getProfile(config.kind, { webRoot: config.webRoot, metroRoot: config.metroRoot, watchFolders: config.watchFolders }),
    ...(origin
      ? {
          resolveEndpoint: async () => {
            const found = await relocateInspector(origin);
            if (found) targetPid = found.pid;
            return found;
          },
        }
      : {}),
    host: config.host,
    port: config.port,
    target: config.target,
    cwd: config.cwd,
    log,
  });

  let lastActivity = Date.now();
  let inflight = 0;
  let shuttingDown = false;
  /** @type {import('node:http').Server | null} */
  let server = null;

  /** @param {string} reason */
  const shutdown = async (reason) => {
    if (shuttingDown) return;
    shuttingDown = true;
    log(`shutting down: ${reason}`);
    const hardStop = setTimeout(() => {
      log('shutdown cap reached; forcing exit');
      removeState(paths);
      process.exit(0);
    }, SHUTDOWN_CAP_MS);
    try {
      await session.shutdown();
    } catch (err) {
      log(`session shutdown error: ${/** @type {Error} */ (err).message}`);
    }
    server?.closeAllConnections?.();
    server?.close();
    removeState(paths);
    clearTimeout(hardStop);
    process.exit(0);
  };

  /**
   * @param {string} cmd
   * @param {Record<string, any>} args
   */
  const handle = async (cmd, args) => {
    if (shuttingDown && cmd !== 'status') throw new DbgError('STOPPING', 'daemon is stopping');
    switch (cmd) {
      case 'status':
        return {
          session: config.session,
          pid: process.pid,
          log: paths.log,
          config,
          ...(targetPid !== undefined ? { targetPid } : {}),
          ...session.status(),
        };
      case 'reattach':
        return session.reattach();
      case 'reload':
        return session.reload();
      case 'stop':
        setImmediate(() => void shutdown('stop command'));
        return { stopped: true };
      case 'break':
        return session.addBreakpoint({ location: args.location, condition: args.condition });
      case 'logpoint':
        return session.addLogpoint({ location: args.location, expr: args.expr });
      case 'list':
        return session.list();
      case 'remove':
        return session.remove({ id: args.id });
      case 'clear':
        return session.clear();
      case 'wait':
        return session.wait({ timeout: args.timeout });
      case 'stack':
        return session.stack({ all: args.all === true, offset: args.offset, limit: args.limit });
      case 'scope':
        return session.scope({ frame: args.frame, depth: args.depth, all: args.all === true, vars: args.vars, scope: args.scope });
      case 'eval':
        return session.evaluate({ expr: args.expr, frame: args.frame, depth: args.depth });
      case 'step':
        return session.step({ kind: args.kind, timeout: args.timeout });
      case 'continue':
        return session.resume();
      case 'pause':
        return session.pause();
      case 'exceptions':
        return session.setExceptions({ state: args.state });
      case 'logs':
        return session.logs({ since: args.since, limit: args.limit, tail: args.tail === true });
      default:
        throw new DbgError('BAD_ARGS', `unknown command "${cmd}"`);
    }
  };

  server = await serveCommands(paths.socket, handle, {
    onRequestStart: () => {
      inflight++;
      lastActivity = Date.now();
    },
    onRequestEnd: () => {
      inflight--;
      lastActivity = Date.now();
    },
  });
  writeFileSync(paths.pid, `${process.pid}\n`);
  log(`daemon ${process.pid} serving ${paths.socket}; target ${config.kind} ${config.host}:${config.port}`);

  const idleCheck = setInterval(
    () => {
      if (inflight === 0 && Date.now() - lastActivity > config.idleTimeoutMs) void shutdown('idle timeout');
    },
    Math.min(5000, Math.max(200, Math.floor(config.idleTimeoutMs / 4))),
  );
  idleCheck.unref();

  for (const signal of /** @type {NodeJS.Signals[]} */ (['SIGTERM', 'SIGINT', 'SIGHUP'])) {
    process.on(signal, () => void shutdown(signal));
  }
  process.on('uncaughtException', (err) => {
    log(`uncaught exception: ${err.stack ?? err.message}`);
    void shutdown('uncaught exception');
  });
  process.on('unhandledRejection', (err) => {
    log(`unhandled rejection: ${/** @type {Error} */ (err)?.stack ?? String(err)}`);
  });

  session.start();
}

main().catch((err) => {
  log(`daemon failed to start: ${err.stack ?? err.message}`);
  process.exit(1);
});
