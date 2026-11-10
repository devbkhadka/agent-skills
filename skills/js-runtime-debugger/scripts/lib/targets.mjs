/**
 * Runtime profiles: how to discover a debuggable target and what the runtime supports.
 *
 * The session core only talks to a `TargetProfile`; adding a runtime means adding a profile here.
 *
 * Profiles:
 *   - node: `--inspect` targets; CommonJS/ESM bodies need a line-1 load trap for pending breakpoints.
 *   - chrome: a browser PAGE (`/json/list` `type === 'page'`). Scripts arrive over http(s) (Vite:
 *     `/src/...` under the web root, `/@fs/<abs>` outside it, `?t=` after HMR) with inline data: maps;
 *     `beforeScriptWithSourceMapExecution` fires for every module incl. dynamic import(), so no load trap.
 *
 *   - hermes: a React Native page behind Metro's inspector proxy (`/json/list` on :8081). ONE debugger
 *     per device (a new one kicks the old with `[NEW_DEBUGGER_OPENED]`); `FuseboxClient.setClientMetadata`
 *     before enabling; no instrumentation breakpoints and no load trap (single bundle, bind on
 *     scriptParsed); the http bundle map's `/[metro-project]/` and `/[metro-watchFolders]/N/` sources
 *     resolve against `--metro-root` / `--watch-folders`.
 */

import path from 'node:path';

import { DbgError } from './ipc.mjs';

/**
 * @typedef {{ id: string, title: string, url: string, type: string, webSocketDebuggerUrl: string }} TargetInfo
 * @typedef {{ host: string, port: number, target?: string }} DiscoverOptions
 * @typedef {'user' | 'node_modules' | 'internal'} UrlClass
 * @typedef {{ webRoot?: string, metroRoot?: string, watchFolders?: string[] }} ProfileOptions
 * @typedef {{
 *   supportsInstrumentation: boolean,
 *   multiClient: boolean,
 *   detachOnDefaultContextDestroyed: boolean,
 *   reportsEndLine: boolean,
 *   urlRegexBreakpoints: boolean,
 *   bindByExactUrl: boolean,
 * }} TargetCapabilities
 * @typedef {{
 *   kind: string,
 *   capabilities: TargetCapabilities,
 *   discover: (options: DiscoverOptions) => Promise<TargetInfo>,
 *   beforeEnable?: (send: (method: string, params?: Record<string, unknown>) => Promise<any>) => Promise<void>,
 *   classifyUrl: (url: string) => UrlClass,
 *   sourceMapOptions?: () => Record<string, unknown>,
 *   scriptPath?: (url: string) => string | null,
 *   loadTrapUrlRegex?: string,
 *   supportsReload?: (target: TargetInfo) => boolean,
 *   reloadHint?: string,
 * }} TargetProfile
 *
 * `detachOnDefaultContextDestroyed`: the default execution context going away means the process is
 * done (node); pages destroy it on every reload and must keep the connection.
 * `reportsEndLine`: `Debugger.scriptParsed.endLine` is the script's real last line (Hermes reports 0
 * for a multi-megabyte bundle, so the cheap map-vs-script line check cannot run there).
 * `urlRegexBreakpoints`: `Debugger.setBreakpointByUrl` accepts `urlRegex` (Hermes: "regex unsupported").
 * `bindByExactUrl`: bind mapped breakpoints with `setBreakpointByUrl({ url })` instead of
 * `setBreakpoint({ scriptId })` (Hermes ignores `condition` on the latter; verified live, RN 0.83).
 * `scriptPath`: filesystem path a served script URL corresponds to; source-map sources are resolved
 * against it so breakpoints match repo paths and frames show files (omit when URLs are already paths).
 * `loadTrapUrlRegex`: user-script URLs on which the session may set a line-1 "module-load trap"
 * breakpoint while mapped breakpoints are pending (omit to bind on scriptParsed only).
 * `multiClient: false`: the target accepts one debugger; being displaced by another is not a
 * disconnect to recover from automatically.
 * `supportsReload`: whether `Page.reload` reloads this target (`dbg reload`); `reloadHint` otherwise.
 */

/**
 * GET `/json/list` on an inspector port.
 * @param {string} host
 * @param {number} port
 * @param {{ timeoutMs?: number }} [options]
 * @returns {Promise<TargetInfo[]>}
 */
export async function listTargets(host, port, options = {}) {
  const hostPart = host.includes(':') ? `[${host}]` : host;
  let res;
  try {
    res = await fetch(`http://${hostPart}:${port}/json/list`, { signal: AbortSignal.timeout(options.timeoutMs ?? 3000) });
  } catch (err) {
    throw new DbgError('NO_TARGET', `inspector not reachable at ${host}:${port} (${/** @type {Error} */ (err).message})`);
  }
  if (!res.ok) throw new DbgError('NO_TARGET', `/json/list on ${host}:${port} returned HTTP ${res.status}`);
  const list = await res.json();
  return Array.isArray(list) ? list : [];
}

/**
 * Pick the first target whose url or title matches `pattern` (all targets when absent).
 * @param {TargetInfo[]} targets
 * @param {string | undefined} pattern
 * @param {(t: TargetInfo) => boolean} accept profile-specific filter
 */
function pickTarget(targets, pattern, accept) {
  const regex = pattern ? new RegExp(pattern) : null;
  return targets.find(
    (t) => typeof t.webSocketDebuggerUrl === 'string' && accept(t) && (!regex || regex.test(t.url ?? '') || regex.test(t.title ?? '')),
  );
}

/** @type {TargetProfile} */
const nodeProfile = {
  kind: 'node',
  capabilities: {
    supportsInstrumentation: true,
    multiClient: true,
    detachOnDefaultContextDestroyed: true,
    reportsEndLine: true,
    urlRegexBreakpoints: true,
    bindByExactUrl: false,
  },
  loadTrapUrlRegex: '^file://(?!.*/node_modules/)',
  async discover({ host, port, target }) {
    const targets = await listTargets(host, port);
    const found = pickTarget(targets, target, (t) => !t.type || t.type === 'node');
    if (!found) {
      throw new DbgError('NO_TARGET', `no node target on ${host}:${port}${target ? ` matching /${target}/` : ''}`, {
        targets: targets.map((t) => ({ id: t.id, type: t.type, title: t.title, url: t.url })),
      });
    }
    return found;
  },
  classifyUrl(url) {
    if (!url || url.startsWith('node:') || !/^(file:|\/|[A-Za-z]:[\\/])/.test(url)) return 'internal';
    if (/[\\/]node_modules[\\/]/.test(url)) return 'node_modules';
    return 'user';
  },
};

/** Vite-internal module URLs (client, env, HMR runtime) that never correspond to repo files. */
const VITE_INTERNAL = /^\/(?:@vite\/|@id\/__x00__|@react-refresh\b|__vite)/;

/**
 * Filesystem path for a dev-server script URL: Vite `/@fs/<abs>` is already absolute; any other
 * http(s) pathname is served from the web root. Query (`?t=` HMR stamps) and hash are dropped.
 * @param {string} url
 * @param {string} webRoot absolute directory the dev server serves `/` from
 * @returns {string | null}
 */
export function webScriptPath(url, webRoot) {
  if (!/^https?:\/\//i.test(url)) return null;
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(url).pathname);
  } catch {
    return null;
  }
  if (pathname.startsWith('/@fs/')) return path.normalize(pathname.slice(4));
  if (pathname === '/' || VITE_INTERNAL.test(pathname)) return null;
  return path.join(webRoot, pathname);
}

/**
 * @param {ProfileOptions} options
 * @returns {TargetProfile}
 */
function chromeProfile(options) {
  const webRoot = path.resolve(options.webRoot ?? process.cwd());
  return {
    kind: 'chrome',
    capabilities: {
      supportsInstrumentation: true,
      multiClient: true,
      detachOnDefaultContextDestroyed: false,
      reportsEndLine: true,
      urlRegexBreakpoints: true,
      bindByExactUrl: false,
    },
    async discover({ host, port, target }) {
      const targets = await listTargets(host, port);
      const pages = targets.filter((t) => t.type === 'page');
      // DevTools front-ends are pages too; only fall back to them when nothing else matches.
      const found =
        pickTarget(pages, target, (t) => !/^devtools:/.test(t.url ?? '')) ?? pickTarget(pages, target, () => true);
      if (!found) {
        throw new DbgError('NO_TARGET', `no page on ${host}:${port}${target ? ` matching /${target}/` : ''}`, {
          targets: targets.map((t) => ({ id: t.id, type: t.type, title: t.title, url: t.url })),
        });
      }
      return found;
    },
    classifyUrl(url) {
      if (!/^https?:\/\//i.test(url ?? '')) return 'internal';
      let pathname;
      try {
        pathname = new URL(url).pathname;
      } catch {
        return 'internal';
      }
      if (/\/node_modules\//.test(pathname)) return 'node_modules';
      if (VITE_INTERNAL.test(pathname)) return 'internal';
      return 'user';
    },
    scriptPath: (url) => webScriptPath(url, webRoot),
    supportsReload: () => true,
  };
}

/**
 * Metro page served by the modern (Fusebox) React Native inspector target, as opposed to legacy
 * or auxiliary pages (e.g. the "UI" thread page).
 * @param {any} t `/json/list` entry
 */
export function isModernHermesPage(t) {
  return t?.reactNative?.capabilities?.prefersFuseboxFrontend === true;
}

/**
 * Choose a Metro page: `pattern` matches title/description/deviceName/appId; modern pages win;
 * the previously attached device (`preferDevice`) wins over other devices so a reattach never
 * silently jumps to another simulator.
 * @param {any[]} targets
 * @param {string | undefined} pattern
 * @param {string | null} [preferDevice] `reactNative.logicalDeviceId` of the last attached page
 * @returns {any | undefined}
 */
export function pickHermesTarget(targets, pattern, preferDevice = null) {
  const regex = pattern ? new RegExp(pattern) : null;
  const matching = targets.filter(
    (t) =>
      typeof t.webSocketDebuggerUrl === 'string' &&
      (!regex || [t.title, t.description, t.deviceName, t.appId].some((v) => typeof v === 'string' && regex.test(v))),
  );
  const rank = (/** @type {any} */ t) =>
    (isModernHermesPage(t) ? 0 : 2) + (preferDevice && t.reactNative?.logicalDeviceId !== preferDevice ? 1 : 0);
  return [...matching].sort((a, b) => rank(a) - rank(b))[0];
}

/**
 * @param {ProfileOptions} options
 * @returns {TargetProfile}
 */
function hermesProfile(options) {
  const projectRoot = path.resolve(options.metroRoot ?? process.cwd());
  const watchFolders = (options.watchFolders ?? []).map((f) => path.resolve(projectRoot, f));
  /** @type {string | null} */
  let lastDevice = null;
  return {
    kind: 'hermes',
    capabilities: {
      supportsInstrumentation: false,
      multiClient: false,
      detachOnDefaultContextDestroyed: false,
      reportsEndLine: false,
      urlRegexBreakpoints: false,
      bindByExactUrl: true,
    },
    async discover({ host, port, target }) {
      const targets = await listTargets(host, port);
      const found = pickHermesTarget(targets, target, lastDevice);
      if (!found) {
        throw new DbgError('NO_TARGET', `no React Native page on Metro ${host}:${port}${target ? ` matching /${target}/` : ''} (is the app running?)`, {
          targets: targets.map((t) => ({ id: t.id, title: t.title, description: t.description, deviceName: t.deviceName })),
        });
      }
      lastDevice = found.reactNative?.logicalDeviceId ?? lastDevice;
      return found;
    },
    async beforeEnable(send) {
      // Identifies us as a Fusebox-aware client; without it the app shows an "unsupported debugging
      // client" warning. Older runtimes answer with an error, which is fine.
      await send('FuseboxClient.setClientMetadata', {}).catch(() => {});
    },
    classifyUrl(url) {
      // One bundle holds app and node_modules code alike; frames are classified from the map.
      return /^https?:\/\//i.test(url ?? '') ? 'user' : 'internal';
    },
    sourceMapOptions: () => ({ metro: { projectRoot, watchFolders } }),
    supportsReload: (t) => /** @type {any} */ (t).reactNative?.capabilities?.nativePageReloads === true,
    reloadHint: 'press r in the Metro terminal or reload the app from the dev menu',
  };
}

/** @type {Record<string, (options: ProfileOptions) => TargetProfile>} */
const PROFILES = { node: () => nodeProfile, chrome: chromeProfile, hermes: hermesProfile };

/**
 * @param {string} kind
 * @param {ProfileOptions} [options]
 * @returns {TargetProfile}
 */
export function getProfile(kind, options = {}) {
  const factory = PROFILES[kind];
  if (!factory) {
    throw new DbgError('BAD_ARGS', `unsupported --kind "${kind}" (supported: ${Object.keys(PROFILES).join(', ')})`);
  }
  return factory(options);
}
