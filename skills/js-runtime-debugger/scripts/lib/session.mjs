/**
 * Debug session core: owns the CDP connection, the breakpoint registry (original coordinates are
 * the source of truth; CDP bindings are derived), script/source-map tracking, pause state,
 * logpoint capture and automatic reattach.
 *
 * User-facing lines/columns are 1-based; CDP and sourcemap.mjs are 0-based. Conversion happens
 * only in this file at the edges (`#locationOut`, breakpoint parsing).
 */

import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { CdpClient } from './cdp.mjs';
import { displayPath, fromObjectPreview, previewValue, sourceExcerpt, toFsPath, truncate } from './format.mjs';
import { DbgError } from './ipc.mjs';
import { SourceMapIndex, decodeDataUrl } from './sourcemap.mjs';

const RECONNECT_INTERVAL_MS = 250;
const LOG_RING_SIZE = 500;
const MAX_STACK_FRAMES = 60;
/** User frames shown in a compact stack (library runs are collapsed and do not count). */
const MAX_USER_FRAMES = 10;
/** A closure/module scope with more variables than this is summarized unless `--all`. */
const MAX_CLOSURE_VARS = 15;
const SCOPE_TYPES = new Set(['local', 'block', 'catch', 'closure', 'module']);
const SMART_STEP_LIMIT = 10;
const SHUTDOWN_CDP_TIMEOUT_MS = 2000;
const CDP_GATE_TIMEOUT_MS = 10_000;

/**
 * Script URL without query/hash, used to detect re-parses of the same file (`?t=` HMR stamps).
 * @param {string} url
 */
function urlPathKey(url) {
  return url ? url.replace(/[?#].*$/, '') : '';
}

/**
 * Why a closure/module scope is noise for a pause snapshot (module wrapper, bundler runtime,
 * TS/webpack import bindings), or null when it should be shown.
 * @param {string[]} names variable names in the scope
 * @returns {string | null}
 */
export function wrapperScopeKind(names) {
  const set = new Set(names);
  if (set.has('__webpack_require__') || set.has('__webpack_modules__')) return 'bundler runtime';
  if (['require', 'module', 'exports', '__filename'].every((n) => set.has(n))) return 'module wrapper';
  if (names.length > 0 && names.filter((n) => /_\d+$/.test(n)).length > names.length / 2) return 'import bindings';
  return null;
}

/**
 * Package a library frame belongs to: `express`, `@nestjs/core`, `node:internal`, or `internal`.
 * @param {string} file display path or URL of the frame
 */
export function framePackage(file) {
  const i = file.lastIndexOf('node_modules/');
  if (i !== -1) {
    const parts = file.slice(i + 'node_modules/'.length).split('/');
    return parts[0]?.startsWith('@') && parts[1] ? `${parts[0]}/${parts[1]}` : (parts[0] ?? 'node_modules');
  }
  return file.startsWith('node:') ? 'node:internal' : 'internal';
}

/**
 * Compact stack: frame 0 and user frames in full (first MAX_USER_FRAMES), each consecutive run of
 * node_modules/internal frames collapsed to `{ collapsed: n, from: "pkg[, pkg2]" }`.
 * @param {Record<string, any>[]} frames described frames (`tag` set on non-user frames)
 * @param {number} total frames in the real stack
 * @returns {Record<string, unknown>[]}
 */
export function collapseStack(frames, total) {
  /** @type {Record<string, unknown>[]} */
  const out = [];
  /** @type {{ collapsed: number, from: string[] } | null} */
  let run = null;
  let users = 0;
  const flush = () => {
    if (run) out.push({ collapsed: run.collapsed, from: run.from.slice(0, 3).join(', ') + (run.from.length > 3 ? ', …' : '') });
    run = null;
  };
  let i = 0;
  for (; i < frames.length; i++) {
    const f = frames[i];
    if (f.tag && i !== 0) {
      run ??= { collapsed: 0, from: [] };
      run.collapsed++;
      const pkg = framePackage(String(f.file ?? ''));
      if (!run.from.includes(pkg)) run.from.push(pkg);
      continue;
    }
    if (users >= MAX_USER_FRAMES) break;
    flush();
    out.push(f);
    users++;
  }
  flush();
  const rest = total - i;
  if (rest > 0) out.push({ more: rest, hint: 'dbg stack --all' });
  return out;
}

/**
 * @typedef {import('./targets.mjs').TargetProfile} TargetProfile
 * @typedef {import('./targets.mjs').TargetInfo} TargetInfo
 * @typedef {import('./format.mjs').RemoteObject} RemoteObject
 * @typedef {{ scriptId: string, url: string, sourceMapURL: string, cls: import('./targets.mjs').UrlClass,
 *   endLine: number, binding: Promise<void>, source?: Promise<string | null>,
 *   mapVerdict?: Promise<string | null> }} ScriptRecord
 * @typedef {{ cdpId: string, scriptId: string, url: string, generated: { line: number, column: number },
 *   original: { source: string, line: number, column: number } | null }} Binding
 * @typedef {{ id: string, kind: 'breakpoint' | 'logpoint', mode: 'mapped' | 'url', spec: string, file: string,
 *   line: number, column?: number, condition?: string, expr?: string, cdpCondition?: string,
 *   resolvedSource: string | null, bindings: Binding[], tried: Set<string>, urlCdpId?: string, warning?: string,
 *   staleMap?: { scriptId: string, url: string, message: string }, hits: number }} Breakpoint
 * @typedef {{ seq: number, ts: string, breakpoint: string, location: string, value: unknown }} LogHit
 * @typedef {{ seq: number, params: any }} PauseState
 */

/**
 * Parse `file:line[:col]` (1-based).
 * @param {unknown} spec
 * @returns {{ file: string, line: number, column?: number }}
 */
export function parseLocationSpec(spec) {
  if (typeof spec !== 'string') throw new DbgError('BAD_ARGS', 'expected <file>:<line>[:<col>]');
  const match = /^(.+?):(\d+)(?::(\d+))?$/.exec(spec.trim());
  if (!match) throw new DbgError('BAD_ARGS', `expected <file>:<line>[:<col>], got "${spec}"`);
  const line = Number(match[2]);
  const column = match[3] === undefined ? undefined : Number(match[3]);
  if (line < 1 || (column !== undefined && column < 1)) throw new DbgError('BAD_ARGS', 'line and column are 1-based');
  return { file: match[1], line, column };
}

/** @param {string} text */
function escapeRegex(text) {
  return text.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');
}

/**
 * Trailing path segments shared by a source identifier and a user path.
 * @param {string} source
 * @param {string} wanted
 */
function suffixScore(source, wanted) {
  const a = toFsPath(source).replace(/\\/g, '/').split('/').filter(Boolean);
  const b = wanted.replace(/\\/g, '/').split('/').filter((s) => s && s !== '.' && s !== '..');
  let n = 0;
  while (n < a.length && n < b.length && a[a.length - 1 - n] === b[b.length - 1 - n]) n++;
  return n;
}

/**
 * Resolve a script's sourceMappingURL against the script URL.
 * @param {string} sourceMapURL
 * @param {string} scriptUrl
 * @returns {string | null}
 */
export function resolveMapUrl(sourceMapURL, scriptUrl) {
  if (/^data:/i.test(sourceMapURL)) return sourceMapURL;
  if (path.isAbsolute(sourceMapURL) && !sourceMapURL.startsWith('//')) return pathToFileURL(sourceMapURL).href;
  let base = scriptUrl;
  if (base && path.isAbsolute(base)) base = pathToFileURL(base).href;
  try {
    return new URL(sourceMapURL, base || undefined).href;
  } catch {
    return null;
  }
}

/**
 * Load map text for a resolved map URL.
 * @param {string} mapUrl
 * @returns {Promise<string>}
 */
async function loadMapText(mapUrl) {
  if (/^data:/i.test(mapUrl)) {
    const text = decodeDataUrl(mapUrl);
    if (text === null) throw new DbgError('SOURCEMAP', 'unsupported data: source map encoding');
    return text;
  }
  if (/^file:/i.test(mapUrl)) return readFile(fileURLToPath(mapUrl), 'utf8');
  if (/^https?:/i.test(mapUrl)) {
    const res = await fetch(mapUrl, { signal: AbortSignal.timeout(30_000) });
    if (!res.ok) throw new DbgError('SOURCEMAP', `GET ${mapUrl} -> HTTP ${res.status}`);
    return res.text();
  }
  throw new DbgError('SOURCEMAP', `unsupported source map URL scheme: ${truncate(mapUrl, 80)}`);
}

/**
 * Filesystem path of a script that runs straight from disk (file:// URL or absolute path), else null.
 * @param {string} url
 */
function diskPathOf(url) {
  if (/^file:\/\//i.test(url)) {
    try {
      return fileURLToPath(url);
    } catch {
      return null;
    }
  }
  return path.isAbsolute(url) ? url : null;
}

/**
 * Whether the running script text is the file text (allowing a CommonJS wrapper around it).
 * @param {string} running
 * @param {string} disk
 */
function sameScriptText(running, disk) {
  const strip = (/** @type {string} */ t) => t.replace(/^\uFEFF/, '').replace(/\r\n/g, '\n');
  const a = strip(running);
  const b = strip(disk);
  return a === b || (a.length - b.length < 256 && a.length > b.length && a.includes(b));
}

/** Close reason Metro's inspector proxy gives the debugger it replaces with a newer one. */
const DISPLACED_MARKER = '[NEW_DEBUGGER_OPENED]';

const STALE_HINT = 'restart the process (or wait for the watcher to re-fork) so code and source map agree';

export class DebugSession {
  /** @type {TargetProfile} */ #profile;
  #host;
  #port;
  /** @type {string | undefined} */ #targetPattern;
  #cwd;
  /** @type {(msg: string) => void} */ #log;

  /** @type {CdpClient | null} */ #cdp = null;
  /** @type {TargetInfo | null} */ #target = null;
  #attached = false;
  #stopping = false;
  #attaching = false;
  #reconnects = 0;
  /** @type {string | null} */ #lastAttachError = null;
  /** @type {(() => Promise<{ host: string, port: number } | null>) | undefined} */ #resolveEndpoint;
  /** Set when another debugger took over a single-client target; no automatic reattach. */
  /** @type {{ at: string, reason: string } | null} */ #displaced = null;
  /** @type {number | null} */ #defaultContextId = null;
  /** @type {string | null} */ #instrumentationId = null;
  #instrumentationSupported = false;
  /** @type {string | null} */ #trapId = null;
  /** CDP ids of every load trap ever set on this connection (pauses can arrive after removal). */
  /** @type {Set<string>} */ #trapIds = new Set();
  #trapSync = Promise.resolve();
  /** Settles when the current internal (instrumentation / load-trap) pause has been resumed. */
  /** @type {Promise<void> | null} */ #internalPause = null;

  /** @type {Map<string, ScriptRecord>} */ #scripts = new Map();
  /** @type {Map<string, Promise<SourceMapIndex | null>>} */ #maps = new Map();
  /** @type {Map<string, string>} */ #sourceTexts = new Map();
  // Map identity separates original text across HMR generations, even when source paths repeat.
  /** @type {WeakMap<SourceMapIndex, Map<number, string>>} */ #originalTexts = new WeakMap();

  /** @type {Map<string, Breakpoint>} */ #breakpoints = new Map();
  /** @type {Map<string, string>} */ #cdpToBp = new Map();
  #nextBpId = 1;
  #marker = `__dbg_lp_${randomBytes(4).toString('hex')}_`;

  /** @type {PauseState | null} */ #paused = null;
  #pauseSeq = 0;
  /** @type {Set<{ afterSeq: number, resolve: (p: PauseState) => void, reject: (e: Error) => void }>} */
  #pauseWaiters = new Set();
  /** @type {'none' | 'uncaught' | 'all'} */ #exceptions = 'none';

  /** @type {LogHit[]} */ #logs = [];
  #logSeq = 0;

  /**
   * @param {{ profile: TargetProfile, host: string, port: number, target?: string, cwd: string,
   *   log?: (msg: string) => void, resolveEndpoint?: () => Promise<{ host: string, port: number } | null> }} options
   *   `resolveEndpoint`: re-locate the inspector before each attach (e.g. a `--pid` target that
   *   re-forked onto a new random port); null keeps the current host/port.
   */
  constructor(options) {
    this.#profile = options.profile;
    this.#host = options.host;
    this.#port = options.port;
    this.#targetPattern = options.target;
    this.#cwd = options.cwd;
    this.#log = options.log ?? (() => {});
    this.#resolveEndpoint = options.resolveEndpoint;
  }

  /** Begin attaching (and keep reattaching after disconnects) until `shutdown`. */
  start() {
    void this.#attachLoop();
  }

  // -------------------------------------------------------------------------
  // Connection lifecycle
  // -------------------------------------------------------------------------

  async #attachLoop() {
    if (this.#attaching) return;
    this.#attaching = true;
    try {
      while (!this.#stopping && !this.#attached && !this.#displaced) {
        try {
          await this.#attachOnce();
        } catch (err) {
          const message = /** @type {Error} */ (err).message;
          if (message !== this.#lastAttachError) this.#log(`attach failed: ${message}`);
          this.#lastAttachError = message;
          await new Promise((r) => setTimeout(r, RECONNECT_INTERVAL_MS));
        }
      }
    } finally {
      this.#attaching = false;
    }
  }

  async #attachOnce() {
    if (this.#resolveEndpoint) {
      const endpoint = await this.#resolveEndpoint();
      if (!endpoint) throw new DbgError('NO_TARGET', 'target process is gone and no restarted twin is listening yet');
      if (endpoint.host !== this.#host || endpoint.port !== this.#port) {
        this.#log(`inspector moved to ${endpoint.host}:${endpoint.port}`);
        this.#host = endpoint.host;
        this.#port = endpoint.port;
      }
    }
    const target = await this.#profile.discover({ host: this.#host, port: this.#port, target: this.#targetPattern });
    const cdp = await CdpClient.connect(target.webSocketDebuggerUrl);
    if (this.#stopping) {
      cdp.close();
      return;
    }
    this.#cdp = cdp;
    this.#target = target;
    cdp.onClose((reason) => this.#onDetached(cdp, reason));
    cdp.on('Debugger.scriptParsed', (p) => this.#onScriptParsed(p));
    cdp.on('Debugger.paused', (p) => this.#onPaused(p));
    cdp.on('Debugger.resumed', () => {
      this.#paused = null;
    });
    cdp.on('Debugger.breakpointResolved', (p) => this.#onBreakpointResolved(p));
    cdp.on('Runtime.consoleAPICalled', (p) => this.#onConsole(p));
    cdp.on('Runtime.executionContextCreated', (p) => {
      if (p.context?.auxData?.isDefault !== false && this.#defaultContextId === null) this.#defaultContextId = p.context.id;
    });
    cdp.on('Runtime.executionContextsCleared', () => this.#onContextsCleared());
    cdp.on('Runtime.executionContextDestroyed', (p) => {
      // Node keeps the socket open after the main context ends ("Waiting for the debugger to
      // disconnect"); treat that as a detach so we go back to polling for the next process.
      // Pages destroy it on every reload and keep running, so only profiles that opt in detach.
      if (this.#profile.capabilities.detachOnDefaultContextDestroyed && p.executionContextId === this.#defaultContextId) {
        cdp.close();
      }
    });

    const send = (/** @type {string} */ method, /** @type {Record<string, unknown>} */ params = {}) => cdp.send(method, params);
    await this.#profile.beforeEnable?.(send);
    await send('Runtime.enable');
    await send('Debugger.enable');
    this.#instrumentationSupported = false;
    if (this.#profile.capabilities.supportsInstrumentation) {
      try {
        const { breakpointId } = await send('Debugger.setInstrumentationBreakpoint', {
          instrumentation: 'beforeScriptWithSourceMapExecution',
        });
        this.#instrumentationId = breakpointId;
        this.#instrumentationSupported = true;
      } catch (err) {
        this.#log(`instrumentation breakpoints unavailable, binding on scriptParsed: ${/** @type {Error} */ (err).message}`);
      }
    }
    if (this.#exceptions !== 'none') await send('Debugger.setPauseOnExceptions', { state: this.#exceptions });
    for (const bp of this.#breakpoints.values()) {
      if (bp.mode === 'url') await this.#bindUrlBreakpoint(bp).catch((err) => this.#log(`rebind ${bp.id}: ${err.message}`));
    }
    this.#attached = !cdp.closed;
    await this.#syncLoadTrap();
    await send('Runtime.runIfWaitingForDebugger').catch(() => {});
    this.#lastAttachError = null;
    if (this.#attached) this.#log(`attached to ${target.id} (${target.url || target.title})`);
  }

  /**
   * Module-load trap. CommonJS/ESM module bodies are compiled as functions, so V8's
   * `beforeScriptWithSourceMapExecution` instrumentation never fires for them (verified on Node 22:
   * a lazily `require`d module gets scriptParsed but no instrumentation pause). While a mapped
   * breakpoint is pending, a URL breakpoint on line 1 of every user script pauses each newly loaded
   * module before its body runs; that pause is internal (bind, then resume) and never surfaced.
   */
  #syncLoadTrap() {
    this.#trapSync = this.#trapSync
      .then(async () => {
        const cdp = this.#cdp;
        const urlRegex = this.#profile.loadTrapUrlRegex;
        if (!cdp || !urlRegex) return;
        const wanted =
          !this.#stopping && [...this.#breakpoints.values()].some((bp) => bp.mode === 'mapped' && bp.bindings.length === 0);
        if (wanted && !this.#trapId) {
          const { breakpointId } = await cdp.send('Debugger.setBreakpointByUrl', { urlRegex, lineNumber: 0, columnNumber: 0 });
          this.#trapIds.add(breakpointId);
          if (cdp === this.#cdp) this.#trapId = breakpointId;
        } else if (!wanted && this.#trapId) {
          const breakpointId = this.#trapId;
          this.#trapId = null;
          await cdp.send('Debugger.removeBreakpoint', { breakpointId });
        }
      })
      .catch((err) => this.#log(`load trap: ${err.message}`));
    return this.#trapSync;
  }

  /**
   * @param {CdpClient} cdp
   * @param {string} reason
   */
  #onDetached(cdp, reason) {
    if (cdp !== this.#cdp) return;
    this.#log(`detached: ${reason}`);
    this.#cdp = null;
    this.#attached = false;
    this.#paused = null;
    this.#defaultContextId = null;
    this.#instrumentationId = null;
    this.#trapId = null;
    this.#trapIds.clear();
    this.#scripts.clear();
    // A rebuild produces new maps for the same URLs; never reuse a parsed map across processes.
    this.#maps.clear();
    this.#sourceTexts.clear();
    this.#cdpToBp.clear();
    for (const bp of this.#breakpoints.values()) {
      bp.bindings = [];
      bp.tried = new Set();
      delete bp.urlCdpId;
      delete bp.staleMap;
    }
    if (this.#stopping) return;
    if (!this.#profile.capabilities.multiClient && reason.includes(DISPLACED_MARKER)) {
      // Another debugger (e.g. React Native DevTools) took the device. Reconnecting would kick it in
      // turn and the two would fight; wait for an explicit `dbg start` instead.
      this.#displaced = { at: new Date().toISOString(), reason };
      this.#log('displaced by another debugger; not reattaching until `dbg start`');
      for (const waiter of this.#pauseWaiters) waiter.reject(this.#displacedError());
      this.#pauseWaiters.clear();
      return;
    }
    this.#reconnects++;
    void this.#attachLoop();
  }

  #displacedError() {
    return new DbgError('DISPLACED', 'another debugger connected to this target and took it over; run `dbg start` again to reattach (that disconnects the other debugger)', {
      ...(this.#displaced ?? {}),
    });
  }

  /** Explicit reattach after being displaced (`dbg start` on a running session). */
  reattach() {
    if (!this.#displaced) return { reattaching: false, attached: this.#attached };
    this.#displaced = null;
    this.#reconnects++;
    void this.#attachLoop();
    return { reattaching: true };
  }

  /** Reload the page (`Page.reload`); breakpoints re-bind when the new script is parsed. */
  async reload() {
    const cdp = this.#requireCdp();
    const target = /** @type {TargetInfo} */ (this.#target);
    if (!this.#profile.supportsReload?.(target)) {
      throw new DbgError('RELOAD_UNSUPPORTED', `${this.#profile.kind} target cannot be reloaded over CDP`, {
        hint: this.#profile.reloadHint ?? 'restart the process',
      });
    }
    if (this.#paused) await cdp.send('Debugger.resume').catch(() => {});
    await cdp.send('Page.reload', {});
    return { reloaded: true, note: 'breakpoints re-bind when the new script is parsed; check `dbg list`' };
  }

  /** @returns {CdpClient} */
  #requireCdp() {
    if (this.#displaced) throw this.#displacedError();
    if (!this.#cdp || !this.#attached) {
      throw new DbgError('DETACHED', `not attached to a target on ${this.#host}:${this.#port} (reconnecting)`);
    }
    return this.#cdp;
  }

  /** @returns {PauseState} */
  #requirePaused() {
    this.#requireCdp();
    if (!this.#paused) throw new DbgError('NOT_PAUSED', 'target is running (use `dbg wait` or `dbg pause`)');
    return this.#paused;
  }

  // -------------------------------------------------------------------------
  // Scripts and source maps
  // -------------------------------------------------------------------------

  /** @param {any} p Debugger.scriptParsed params */
  #onScriptParsed(p) {
    /** @type {ScriptRecord} */
    const record = {
      scriptId: p.scriptId,
      url: p.url ?? '',
      sourceMapURL: p.sourceMapURL ?? '',
      cls: this.#profile.classifyUrl(p.url ?? ''),
      endLine: typeof p.endLine === 'number' ? p.endLine : Number.POSITIVE_INFINITY,
      binding: Promise.resolve(),
    };
    // A re-parse of the same URL path (HMR `?t=`, re-required module) supersedes the old script:
    // its scriptId bindings are stale and its map may have changed.
    const pathKey = urlPathKey(record.url);
    if (pathKey) {
      const stale = [...this.#scripts.values()].filter((s) => s.scriptId !== record.scriptId && urlPathKey(s.url) === pathKey);
      if (stale.length) this.#dropScripts(stale);
    }
    this.#scripts.set(record.scriptId, record);
    if (record.sourceMapURL && record.cls === 'user' && this.#hasUnboundMapped(record.scriptId)) {
      // Fetch the running text now, before any instrumentation / load-trap pause for this script.
      if (this.#needsDiskCheck(record)) void this.#scriptSource(record);
      void this.#queueBind(record);
    }
  }

  /**
   * Forget scripts and every binding derived from them (breakpoints stay registered).
   * @param {ScriptRecord[]} records
   */
  #dropScripts(records) {
    const ids = new Set(records.map((r) => r.scriptId));
    const cdp = this.#cdp;
    for (const r of records) {
      this.#scripts.delete(r.scriptId);
      this.#sourceTexts.delete(`script:${r.scriptId}`);
      const mapUrl = r.sourceMapURL ? resolveMapUrl(r.sourceMapURL, r.url) : null;
      if (mapUrl) this.#maps.delete(mapUrl);
    }
    for (const bp of this.#breakpoints.values()) {
      for (const id of ids) bp.tried.delete(id);
      if (bp.staleMap && ids.has(bp.staleMap.scriptId)) delete bp.staleMap;
      const keep = [];
      for (const b of bp.bindings) {
        if (!ids.has(b.scriptId)) keep.push(b);
        else if (bp.mode === 'mapped') {
          this.#cdpToBp.delete(b.cdpId);
          cdp?.send('Debugger.removeBreakpoint', { breakpointId: b.cdpId }).catch(() => {});
        }
      }
      bp.bindings = keep;
    }
    void this.#syncLoadTrap();
  }

  /** All scripts are gone (navigation / context reset): rebuild derived bindings from scratch. */
  #onContextsCleared() {
    this.#log('execution contexts cleared; dropping script bindings');
    this.#dropScripts([...this.#scripts.values()]);
    this.#maps.clear();
    this.#sourceTexts.clear();
  }

  /** @param {string} scriptId */
  #hasUnboundMapped(scriptId) {
    for (const bp of this.#breakpoints.values()) {
      if (bp.mode === 'mapped' && !bp.tried.has(scriptId)) return true;
    }
    return false;
  }

  /**
   * Serialize binding work per script so an instrumentation pause can await all of it.
   * @param {ScriptRecord} record
   */
  #queueBind(record) {
    record.binding = record.binding.then(async () => {
      await this.#bindScript(record).catch((err) => this.#log(`bind ${record.url}: ${err.stack ?? err.message}`));
      await this.#syncLoadTrap();
    });
    return record.binding;
  }

  /**
   * @param {ScriptRecord | undefined} record
   * @returns {Promise<SourceMapIndex | null>}
   */
  #mapFor(record) {
    if (!record || !record.sourceMapURL || record.cls !== 'user') return Promise.resolve(null);
    const mapUrl = resolveMapUrl(record.sourceMapURL, record.url);
    if (!mapUrl) return Promise.resolve(null);
    let pending = this.#maps.get(mapUrl);
    if (!pending) {
      const started = Date.now();
      pending = loadMapText(mapUrl)
        .then((text) => {
          const index = SourceMapIndex.fromJSON(text, {
            mapUrl,
            // Relative sources resolve against the script's file path when the profile knows it
            // (dev-server URLs), so they come out as repo paths rather than http URLs.
            scriptUrl: this.#profile.scriptPath?.(record.url) ?? record.url,
            ...(this.#profile.sourceMapOptions?.() ?? {}),
          });
          this.#log(`loaded source map ${truncate(mapUrl, 120)} (${index.sources.length} sources, ${Date.now() - started} ms)`);
          return index;
        })
        .catch((err) => {
          this.#log(`source map ${truncate(mapUrl, 120)} failed: ${err.message}`);
          return null;
        });
      this.#maps.set(mapUrl, pending);
    }
    return pending;
  }

  /**
   * Whether the generated file on disk must be compared with the running script: only when both
   * the script and its map come from disk (inline data: maps travel with the code, so are fresh).
   * @param {ScriptRecord} record
   */
  #needsDiskCheck(record) {
    return diskPathOf(record.url) !== null && !/^data:/i.test(record.sourceMapURL);
  }

  /**
   * Running script text, cached per scriptId (null when unavailable).
   * @param {ScriptRecord} record
   */
  #scriptSource(record) {
    const cdp = this.#cdp;
    if (!record.source) {
      record.source = cdp
        ? cdp.send('Debugger.getScriptSource', { scriptId: record.scriptId }).then(
            (r) => (typeof r.scriptSource === 'string' ? r.scriptSource : null),
            () => null,
          )
        : Promise.resolve(null);
    }
    return record.source;
  }

  /**
   * Null when `map` describes the code that is actually running in `record`, else a STALE_SOURCE_MAP
   * message. A build that rewrites dist/ after the process loaded it leaves a map whose generated
   * positions point at different statements: binding through it silently never hits.
   * @param {ScriptRecord} record
   * @param {SourceMapIndex} map
   */
  #verifyMap(record, map) {
    record.mapVerdict ??= (async () => {
      const where = displayPath(record.url, this.#cwd);
      // Strong check (map and code both read from disk): the running text must be the file text.
      const diskPath = diskPathOf(record.url);
      if (diskPath !== null && this.#needsDiskCheck(record)) {
        const running = await this.#scriptSource(record);
        const disk = await readFile(diskPath, 'utf8').catch(() => null);
        if (running !== null && disk !== null && !sameScriptText(running, disk)) {
          return `STALE_SOURCE_MAP: running ${where} differs from the build output on disk; ${STALE_HINT}`;
        }
      }
      // Cheap check: the map cannot describe lines the running script does not have (only where the
      // runtime reports a real endLine). Map and script served together over http (Metro, dev
      // servers) are consistent by construction, so no strong check applies to them.
      if (this.#profile.capabilities.reportsEndLine && map.lastGeneratedLine > record.endLine) {
        return `STALE_SOURCE_MAP: source map for ${where} maps generated line ${map.lastGeneratedLine + 1} but the running script has ${record.endLine + 1} lines; ${STALE_HINT}`;
      }
      return null;
    })();
    return record.mapVerdict;
  }

  /** Scripts that may carry a user source map. */
  #mappableScripts() {
    return [...this.#scripts.values()].filter((s) => s.sourceMapURL && s.cls === 'user');
  }

  /**
   * Bind every mapped breakpoint not yet tried against this script.
   * @param {ScriptRecord} record
   */
  async #bindScript(record) {
    const cdp = this.#cdp;
    if (!cdp) return;
    const candidates = [...this.#breakpoints.values()].filter((bp) => bp.mode === 'mapped' && !bp.tried.has(record.scriptId));
    if (candidates.length === 0) return;
    const map = await this.#mapFor(record);
    if (cdp !== this.#cdp) return;
    /** @type {Map<Breakpoint, number>} */
    const matched = new Map();
    for (const bp of candidates) {
      if (!this.#breakpoints.has(bp.id) || bp.tried.has(record.scriptId)) continue;
      bp.tried.add(record.scriptId);
      if (!map) continue;
      const sourceIdx = this.#pickSource(map, bp);
      if (sourceIdx !== -1) matched.set(bp, sourceIdx);
    }
    if (!map || matched.size === 0) return;
    const stale = await this.#verifyMap(record, map);
    if (cdp !== this.#cdp) return;
    if (stale) {
      this.#log(stale);
      for (const bp of matched.keys()) {
        if (bp.bindings.length === 0) bp.staleMap = { scriptId: record.scriptId, url: record.url, message: stale };
      }
      return;
    }
    /** @type {Promise<void>[]} */
    const work = [];
    for (const [bp, sourceIdx] of matched) {
      if (!this.#breakpoints.has(bp.id)) continue;
      delete bp.staleMap;
      const positions = map.originalToGenerated(sourceIdx, bp.line - 1, bp.column === undefined ? undefined : bp.column - 1);
      if (positions.length === 0) {
        bp.warning = `no generated code at or within 5 lines after ${bp.file}:${bp.line}`;
        continue;
      }
      bp.resolvedSource ??= map.sources[sourceIdx];
      for (const pos of positions) {
        work.push(
          this.#setGeneratedBreakpoint(cdp, record, pos, bp.cdpCondition)
            .then(({ breakpointId, actualLocation }) => {
              if (!this.#breakpoints.has(bp.id)) {
                cdp.send('Debugger.removeBreakpoint', { breakpointId }).catch(() => {});
                return;
              }
              const gen = actualLocation ?? { lineNumber: pos.line, columnNumber: pos.column };
              const orig = map.generatedToOriginal(gen.lineNumber, gen.columnNumber ?? 0);
              this.#cdpToBp.set(breakpointId, bp.id);
              bp.bindings.push({
                cdpId: breakpointId,
                scriptId: record.scriptId,
                url: record.url,
                generated: { line: gen.lineNumber, column: gen.columnNumber ?? 0 },
                original: orig
                  ? { source: orig.source, line: orig.line, column: orig.column }
                  : { source: map.sources[sourceIdx], line: pos.originalLine, column: pos.originalColumn },
              });
              if (pos.snappedLine !== undefined) bp.warning = `line ${bp.line} has no code; bound at line ${pos.snappedLine + 1}`;
            })
            .catch((err) => {
              bp.warning = `setBreakpoint in ${displayPath(record.url, this.#cwd)} failed: ${err.message}`;
            }),
        );
      }
    }
    await Promise.all(work);
  }

  /**
   * One CDP breakpoint at a generated position. Profiles with `bindByExactUrl` use
   * `setBreakpointByUrl` with the script's exact URL (Hermes ignores `condition` on
   * `Debugger.setBreakpoint`, which would turn logpoints into hard pauses).
   * @param {CdpClient} cdp
   * @param {ScriptRecord} record
   * @param {{ line: number, column: number }} pos
   * @param {string | undefined} condition
   * @returns {Promise<{ breakpointId: string, actualLocation?: { lineNumber: number, columnNumber?: number } }>}
   */
  async #setGeneratedBreakpoint(cdp, record, pos, condition) {
    const cond = condition ? { condition } : {};
    if (!this.#profile.capabilities.bindByExactUrl) {
      return cdp.send('Debugger.setBreakpoint', {
        location: { scriptId: record.scriptId, lineNumber: pos.line, columnNumber: pos.column },
        ...cond,
      });
    }
    const { breakpointId, locations } = await cdp.send('Debugger.setBreakpointByUrl', {
      url: record.url,
      lineNumber: pos.line,
      columnNumber: pos.column,
      ...cond,
    });
    const actualLocation = (locations ?? []).find((/** @type {any} */ l) => l.scriptId === record.scriptId) ?? locations?.[0];
    return { breakpointId, actualLocation };
  }

  /**
   * Source index in `map` for a breakpoint, honoring the source chosen at registration time.
   * @param {SourceMapIndex} map
   * @param {Breakpoint} bp
   */
  #pickSource(map, bp) {
    const idxs = map.findSources(bp.file, { cwd: this.#cwd });
    if (idxs.length === 0) return -1;
    const sources = map.sources;
    if (bp.resolvedSource !== null) return idxs.find((i) => sources[i] === bp.resolvedSource) ?? -1;
    if (idxs.length > 1) {
      bp.warning = `ambiguous in ${map.sources.length}-source map: ${idxs.map((i) => displayPath(sources[i], this.#cwd)).join(', ')}`;
      return -1;
    }
    return idxs[0];
  }

  /**
   * Original position for a generated location in a script, or null when unmapped.
   * @param {ScriptRecord | undefined} record
   * @param {number} line
   * @param {number} column
   */
  async #originalOf(record, line, column) {
    const map = await this.#mapFor(record);
    if (!map) return null;
    const orig = map.generatedToOriginal(line, column);
    return orig ? { ...orig, map } : null;
  }

  // -------------------------------------------------------------------------
  // Breakpoints
  // -------------------------------------------------------------------------

  /**
   * @param {{ location: string, condition?: string }} args
   */
  async addBreakpoint(args) {
    const condition = typeof args.condition === 'string' && args.condition.trim() !== '' ? args.condition : undefined;
    const bp = await this.#register('breakpoint', args.location, { condition, cdpCondition: condition });
    return this.#breakpointJSON(bp);
  }

  /**
   * @param {{ location: string, expr: string }} args
   */
  async addLogpoint(args) {
    if (typeof args.expr !== 'string' || args.expr.trim() === '') throw new DbgError('BAD_ARGS', 'logpoint needs an expression');
    const id = String(this.#nextBpId);
    const tag = JSON.stringify(`${this.#marker}${id}`);
    // Never pauses (returns false) and never lets the user expression throw into the program.
    const cdpCondition =
      `(() => { try { globalThis.console.debug(${tag}, (${args.expr}\n)); } ` +
      `catch (e) { try { globalThis.console.debug(${tag}, { logpointError: String(e) }); } catch {} } return false; })()`;
    const bp = await this.#register('logpoint', args.location, { expr: args.expr, cdpCondition });
    return this.#breakpointJSON(bp);
  }

  /**
   * @param {'breakpoint' | 'logpoint'} kind
   * @param {string} spec
   * @param {{ condition?: string, expr?: string, cdpCondition?: string }} extra
   */
  async #register(kind, spec, extra) {
    const cdp = this.#requireCdp();
    const { file, line, column } = parseLocationSpec(spec);

    // Decide mapped vs plain-URL mode against every loaded map, rejecting ambiguity up front.
    const maps = await Promise.all(this.#mappableScripts().map((s) => this.#mapFor(s)));
    /** @type {Map<string, number>} */
    const scored = new Map();
    for (const map of maps) {
      if (!map) continue;
      for (const i of map.findSources(file, { cwd: this.#cwd })) scored.set(map.sources[i], suffixScore(map.sources[i], file));
    }
    const best = Math.max(0, ...scored.values());
    const candidates = [...scored].filter(([, s]) => s === best).map(([source]) => source);
    const absolute = path.isAbsolute(toFsPath(file));
    const exact = absolute ? candidates.filter((c) => toFsPath(c) === path.resolve(toFsPath(file))) : [];
    const chosen = exact.length === 1 ? exact : candidates;
    if (chosen.length > 1) {
      throw new DbgError('AMBIGUOUS', `"${file}" matches ${chosen.length} sources; use a longer path`, {
        candidates: chosen.map((c) => displayPath(c, this.#cwd)),
      });
    }
    // Generated-URL mode needs `urlRegex` breakpoints; without them (Hermes) a .js path stays mapped.
    const mode = chosen.length === 0 && /\.[cm]?js$/i.test(file) && this.#profile.capabilities.urlRegexBreakpoints ? 'url' : 'mapped';

    /** @type {Breakpoint} */
    const bp = {
      id: String(this.#nextBpId++),
      kind,
      mode,
      spec: `${file}:${line}${column ? `:${column}` : ''}`,
      file,
      line,
      column,
      ...extra,
      resolvedSource: chosen[0] ?? null,
      bindings: [],
      tried: new Set(),
      hits: 0,
    };
    this.#breakpoints.set(bp.id, bp);
    if (mode === 'url') await this.#bindUrlBreakpoint(bp);
    else await Promise.all(this.#mappableScripts().map((s) => this.#queueBind(s)));
    await this.#syncLoadTrap();
    if (cdp !== this.#cdp) this.#log(`breakpoint ${bp.id} registered while detaching; will bind on reattach`);
    return bp;
  }

  /** @param {Breakpoint} bp */
  async #bindUrlBreakpoint(bp) {
    const cdp = this.#cdp;
    if (!cdp) return;
    const segments = toFsPath(bp.file).replace(/\\/g, '/').split('/').filter((s) => s && s !== '.' && s !== '..');
    const urlRegex = `(^|/)${escapeRegex(segments.join('/'))}$`;
    const { breakpointId, locations } = await cdp.send('Debugger.setBreakpointByUrl', {
      urlRegex,
      lineNumber: bp.line - 1,
      ...(bp.column ? { columnNumber: bp.column - 1 } : {}),
      ...(bp.cdpCondition ? { condition: bp.cdpCondition } : {}),
    });
    bp.urlCdpId = breakpointId;
    this.#cdpToBp.set(breakpointId, bp.id);
    for (const loc of locations ?? []) this.#addUrlBinding(bp, breakpointId, loc);
  }

  /** @param {any} p Debugger.breakpointResolved params */
  #onBreakpointResolved(p) {
    const bp = this.#breakpoints.get(this.#cdpToBp.get(p.breakpointId) ?? '');
    if (bp && bp.mode === 'url') this.#addUrlBinding(bp, p.breakpointId, p.location);
  }

  /**
   * @param {Breakpoint} bp
   * @param {string} cdpId
   * @param {{ scriptId: string, lineNumber: number, columnNumber?: number }} loc
   */
  #addUrlBinding(bp, cdpId, loc) {
    if (bp.bindings.some((b) => b.scriptId === loc.scriptId && b.generated.line === loc.lineNumber)) return;
    bp.bindings.push({
      cdpId,
      scriptId: loc.scriptId,
      url: this.#scripts.get(loc.scriptId)?.url ?? '',
      generated: { line: loc.lineNumber, column: loc.columnNumber ?? 0 },
      original: null,
    });
  }

  /** @param {Breakpoint} bp */
  #breakpointJSON(bp) {
    /** @type {Record<string, unknown>} */
    const out = {
      id: bp.id,
      kind: bp.kind,
      location: bp.spec,
      state: bp.bindings.length > 0 ? 'bound' : bp.staleMap ? 'stale-map' : 'pending',
      locations: bp.bindings.map((b) => ({
        file: displayPath(b.original?.source ?? b.url, this.#cwd),
        line: (b.original?.line ?? b.generated.line) + 1,
        column: (b.original?.column ?? b.generated.column) + 1,
        generated: { url: b.url, line: b.generated.line + 1, column: b.generated.column + 1 },
      })),
    };
    if (bp.condition) out.condition = bp.condition;
    if (bp.expr) out.expr = bp.expr;
    if (bp.mode === 'url') out.mode = 'generated-url';
    if (bp.hits) out.hits = bp.hits;
    if (bp.warning) out.warning = bp.warning;
    if (bp.staleMap && bp.bindings.length === 0) out.error = { code: 'STALE_SOURCE_MAP', message: bp.staleMap.message };
    return out;
  }

  list() {
    return { breakpoints: [...this.#breakpoints.values()].map((bp) => this.#breakpointJSON(bp)) };
  }

  /** @param {{ id: string }} args */
  async remove(args) {
    const bp = this.#breakpoints.get(String(args.id));
    if (!bp) throw new DbgError('NOT_FOUND', `no breakpoint with id ${args.id}`);
    await this.#unbind(bp);
    return { removed: bp.id };
  }

  async clear() {
    const ids = [...this.#breakpoints.keys()];
    await Promise.all([...this.#breakpoints.values()].map((bp) => this.#unbind(bp)));
    return { removed: ids };
  }

  /**
   * @param {Breakpoint} bp
   * @param {number} [timeoutMs]
   */
  async #unbind(bp, timeoutMs) {
    this.#breakpoints.delete(bp.id);
    const cdpIds = new Set(bp.bindings.map((b) => b.cdpId));
    if (bp.urlCdpId) cdpIds.add(bp.urlCdpId);
    const cdp = this.#cdp;
    await Promise.all(
      [...cdpIds].map((breakpointId) => {
        this.#cdpToBp.delete(breakpointId);
        return cdp?.send('Debugger.removeBreakpoint', { breakpointId }, { timeoutMs }).catch(() => {});
      }),
    );
    await this.#syncLoadTrap();
  }

  // -------------------------------------------------------------------------
  // Pause handling
  // -------------------------------------------------------------------------

  /** @param {any} p Debugger.paused params */
  #onPaused(p) {
    const hits = /** @type {string[]} */ (p.hitBreakpoints ?? []);
    const trapOnly = hits.length > 0 && hits.every((id) => this.#trapIds.has(id));
    if (p.reason === 'instrumentation' || trapOnly) {
      void this.#onInternalPause(p);
      return;
    }
    const state = { seq: ++this.#pauseSeq, params: p };
    this.#paused = state;
    for (const id of p.hitBreakpoints ?? []) {
      const bp = this.#breakpoints.get(this.#cdpToBp.get(id) ?? '');
      if (bp) bp.hits++;
    }
    this.#log(`paused: ${p.reason} at ${p.callFrames?.[0]?.url}:${(p.callFrames?.[0]?.location?.lineNumber ?? 0) + 1}`);
    for (const waiter of this.#pauseWaiters) {
      if (state.seq > waiter.afterSeq) {
        this.#pauseWaiters.delete(waiter);
        waiter.resolve(state);
      }
    }
  }

  /**
   * Internal pause (instrumentation or load trap) before a script body runs: bind pending
   * breakpoints, then resume.
   * @param {any} p
   */
  async #onInternalPause(p) {
    const cdp = this.#cdp;
    /** @type {() => void} */
    let done = () => {};
    const gate = new Promise((resolve) => (done = () => resolve(undefined)));
    this.#internalPause = gate;
    try {
      const record = this.#scripts.get(p.data?.scriptId ?? p.callFrames?.[0]?.location?.scriptId ?? '');
      if (record && record.cls === 'user' && record.sourceMapURL && this.#hasUnboundMapped(record.scriptId)) {
        await this.#queueBind(record);
      } else if (record) {
        await record.binding;
      }
    } catch (err) {
      this.#log(`internal pause bind failed: ${/** @type {Error} */ (err).message}`);
    } finally {
      // Only Debugger.setBreakpoint* was sent while paused: Runtime.evaluate/getProperties hang
      // during an instrumentation pause, so Runtime-using commands wait on this gate.
      await cdp?.send('Debugger.resume').catch(() => {});
      if (this.#internalPause === gate) this.#internalPause = null;
      done();
    }
  }

  /** Wait (bounded) for an in-flight internal pause to resume before any Runtime.* call. */
  async #afterInternalPause() {
    const gate = this.#internalPause;
    if (!gate) return;
    const timedOut = await Promise.race([gate.then(() => false), new Promise((r) => setTimeout(() => r(true), CDP_GATE_TIMEOUT_MS))]);
    if (timedOut) throw new DbgError('INTERNAL_PAUSE', 'target is in an internal source-map pause; retry shortly');
  }

  /**
   * @param {number} afterSeq
   * @param {number} timeoutMs
   * @returns {Promise<PauseState>}
   */
  #waitForPause(afterSeq, timeoutMs) {
    if (this.#paused && this.#paused.seq > afterSeq) return Promise.resolve(this.#paused);
    return new Promise((resolve, reject) => {
      const waiter = {
        afterSeq,
        resolve: (/** @type {PauseState} */ s) => {
          clearTimeout(timer);
          resolve(s);
        },
        reject: (/** @type {Error} */ e) => {
          clearTimeout(timer);
          reject(e);
        },
      };
      const timer = setTimeout(() => {
        this.#pauseWaiters.delete(waiter);
        reject(new DbgError('WAIT_TIMEOUT', `no pause within ${Math.round(timeoutMs / 1000)} s`));
      }, timeoutMs);
      this.#pauseWaiters.add(waiter);
    });
  }

  /** @param {{ timeout?: number }} args seconds */
  async wait(args) {
    const timeoutMs = Math.max(0, Number(args.timeout ?? 300)) * 1000;
    const state = this.#paused ?? (await this.#waitForPause(this.#pauseSeq, timeoutMs));
    return this.#snapshot(state, {});
  }

  /** @param {string} objectId */
  #getProperties = async (objectId) => {
    await this.#afterInternalPause();
    const cdp = this.#requireCdp();
    const { result } = await cdp.send('Runtime.getProperties', { objectId, ownProperties: true, generatePreview: true });
    return result ?? [];
  };

  /**
   * @param {any} frame CDP CallFrame
   * @param {number} index
   */
  async #describeFrame(frame, index) {
    const record = this.#scripts.get(frame.location.scriptId);
    const url = frame.url || record?.url || '';
    const cls = record?.cls ?? this.#profile.classifyUrl(url);
    const line = frame.location.lineNumber;
    const column = frame.location.columnNumber ?? 0;
    const orig = await this.#originalOf(record, line, column);
    /** @type {Record<string, unknown>} */
    const out = {
      index,
      function: frame.functionName || orig?.name || '(anonymous)',
      file: displayPath(orig?.source ?? url, this.#cwd),
      line: (orig?.line ?? line) + 1,
      column: (orig?.column ?? column) + 1,
    };
    if (orig) out.generated = `${displayPath(url, this.#cwd)}:${line + 1}:${column + 1}`;
    // A bundle (Hermes, webpack) is one user script; its library frames are known from the map.
    const tag = cls === 'user' && orig && /[\\/]node_modules[\\/]/.test(orig.source) ? 'node_modules' : cls;
    if (tag !== 'user') out.tag = tag;
    return { out, orig, record, url, line };
  }

  /**
   * Up to 5 lines of source around the frame location.
   * @param {Awaited<ReturnType<DebugSession['#describeFrame']>>} described
   */
  async #excerptFor(described) {
    const { orig, record, line } = described;
    try {
      if (orig) {
        let texts = this.#originalTexts.get(orig.map);
        if (!texts) this.#originalTexts.set(orig.map, (texts = new Map()));
        let text = texts.get(orig.sourceIdx) ?? orig.map.sourcesContent[orig.sourceIdx] ?? null;
        const fsPath = toFsPath(orig.source);
        if (text === null && path.isAbsolute(fsPath) && existsSync(fsPath)) text = readFileSync(fsPath, 'utf8');
        if (text === null) return undefined;
        texts.set(orig.sourceIdx, text);
        return sourceExcerpt(text, orig.line);
      }
      if (!record) return undefined;
      const key = `script:${record.scriptId}`;
      let text = this.#sourceTexts.get(key);
      if (text === undefined) {
        ({ scriptSource: text } = await this.#requireCdp().send('Debugger.getScriptSource', { scriptId: record.scriptId }));
        this.#sourceTexts.set(key, text ?? '');
      }
      return text ? sourceExcerpt(text, line) : undefined;
    } catch {
      return undefined;
    }
  }

  /**
   * @param {any} frame CDP CallFrame
   * @param {number} depth
   * @param {{ all?: boolean, frameIndex?: number, vars?: Set<string>, scope?: string }} [options] `all`: never summarize closure/module scopes
   */
  async #scopesOf(frame, depth, options = {}) {
    const scopes = [];
    for (const scope of frame.scopeChain ?? []) {
      if (!SCOPE_TYPES.has(scope.type) || !scope.object?.objectId) continue;
      if (options.scope && scope.type !== options.scope && scope.name !== options.scope) continue;
      let props = await this.#getProperties(scope.object.objectId);
      if (options.vars) props = props.filter((p) => options.vars.has(p.name));
      if (!options.all && !options.vars && (scope.type === 'closure' || scope.type === 'module')) {
        const why = wrapperScopeKind(props.map((p) => p.name)) ?? (props.length > MAX_CLOSURE_VARS ? 'large' : null);
        if (why) {
          scopes.push({
            type: scope.type,
            ...(scope.name ? { name: scope.name } : {}),
            omitted: props.length,
            why,
            hint: `dbg scope --frame ${options.frameIndex ?? 0} --all`,
          });
          continue;
        }
      }
      /** @type {Record<string, unknown>} */
      const variables = {};
      const cap = options.all ? 200 : 50;
      for (const prop of props.slice(0, cap)) {
        variables[prop.name] = await previewValue(prop.value, depth, this.#getProperties);
      }
      if (props.length > cap) variables['…'] = `+${props.length - cap} more`;
      scopes.push({ type: scope.type, ...(scope.name ? { name: scope.name } : {}), variables });
    }
    return scopes;
  }

  /**
   * @param {PauseState} state
   * @param {{ depth?: number }} options
   */
  async #snapshot(state, options) {
    const p = state.params;
    const frames = p.callFrames ?? [];
    const described = await Promise.all(frames.slice(0, MAX_STACK_FRAMES).map((f, i) => this.#describeFrame(f, i)));
    /** @type {Record<string, unknown>} */
    const out = { paused: true, reason: p.reason };
    const hit = (p.hitBreakpoints ?? []).map((/** @type {string} */ id) => this.#cdpToBp.get(id)).filter(Boolean);
    if (hit.length) out.hitBreakpoints = hit;
    if (described[0]) {
      out.frame = { ...described[0].out, source: await this.#excerptFor(described[0]) };
    }
    out.stack = collapseStack(described.map((d) => d.out), frames.length);
    if (frames[0]) out.scopes = await this.#scopesOf(frames[0], options.depth ?? 1);
    if ((p.reason === 'exception' || p.reason === 'promiseRejection') && p.data) {
      out.exception = await previewValue(p.data, 2, this.#getProperties);
    }
    return out;
  }

  /** @param {{ all?: boolean, offset?: number, limit?: number }} [args] */
  async stack(args = {}) {
    const state = this.#requirePaused();
    const frames = state.params.callFrames ?? [];
    const offset = Number(args.offset ?? 0);
    const limit = Number(args.limit ?? MAX_STACK_FRAMES);
    if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > MAX_STACK_FRAMES) {
      throw new DbgError('BAD_ARGS', `stack offset must be a nonnegative integer; limit must be 1..${MAX_STACK_FRAMES}`);
    }
    const shown = frames.slice(offset, offset + limit);
    const described = await Promise.all(shown.map((/** @type {any} */ f, /** @type {number} */ i) => this.#describeFrame(f, offset + i)));
    const outs = described.map((d) => d.out);
    const paged = args.all || args.offset !== undefined || args.limit !== undefined;
    const nextOffset = Math.min(offset + shown.length, frames.length);
    return {
      reason: state.params.reason,
      stack: paged ? outs : collapseStack(outs, frames.length),
      total: frames.length,
      ...(paged ? { offset, nextOffset, hasMore: nextOffset < frames.length } : {}),
    };
  }

  /** @param {{ frame?: number, depth?: number, all?: boolean, vars?: string, scope?: string }} args */
  async scope(args) {
    const state = this.#requirePaused();
    const frame = this.#frameAt(state, args.frame);
    const depth = Math.min(5, Math.max(0, Number(args.depth ?? 2)));
    const frameIndex = Number(args.frame ?? 0);
    const vars = args.vars === undefined ? undefined : new Set(String(args.vars).split(',').map((v) => v.trim()).filter(Boolean));
    if (vars && !vars.size) throw new DbgError('BAD_ARGS', 'vars needs comma-separated variable names');
    const described = await this.#describeFrame(frame, frameIndex);
    return { frame: described.out, scopes: await this.#scopesOf(frame, depth, { all: args.all === true, frameIndex, vars, scope: args.scope }) };
  }

  /**
   * @param {PauseState} state
   * @param {unknown} index
   */
  #frameAt(state, index) {
    const i = Number(index ?? 0);
    const frame = state.params.callFrames?.[i];
    if (!Number.isInteger(i) || !frame) throw new DbgError('BAD_ARGS', `no frame ${index} (stack has ${state.params.callFrames?.length ?? 0})`);
    return frame;
  }

  /** @param {{ expr: string, frame?: number, depth?: number }} args */
  async evaluate(args) {
    await this.#afterInternalPause();
    const cdp = this.#requireCdp();
    if (typeof args.expr !== 'string' || args.expr.trim() === '') throw new DbgError('BAD_ARGS', 'eval needs an expression');
    const depth = Math.min(5, Math.max(0, Number(args.depth ?? 2)));
    let reply;
    if (this.#paused) {
      const frame = this.#frameAt(this.#paused, args.frame);
      reply = await cdp.send('Debugger.evaluateOnCallFrame', {
        callFrameId: frame.callFrameId,
        expression: args.expr,
        generatePreview: true,
        returnByValue: false,
        objectGroup: 'dbg-eval',
      });
    } else {
      reply = await cdp.send('Runtime.evaluate', {
        expression: args.expr,
        generatePreview: true,
        returnByValue: false,
        replMode: true,
        awaitPromise: true,
        objectGroup: 'dbg-eval',
      });
    }
    /** @type {Record<string, unknown>} */
    const out = {
      context: this.#paused ? `frame ${Number(args.frame ?? 0)}` : 'global',
      type: reply.result?.subtype ?? reply.result?.type,
      value: await previewValue(reply.result, depth, this.#getProperties),
    };
    if (reply.exceptionDetails) {
      out.exception = reply.exceptionDetails.exception
        ? await previewValue(reply.exceptionDetails.exception, 1)
        : reply.exceptionDetails.text;
    }
    return out;
  }

  /** @param {{ kind: string, timeout?: number }} args */
  async step(args) {
    const methods = /** @type {Record<string, string>} */ ({ over: 'Debugger.stepOver', into: 'Debugger.stepInto', out: 'Debugger.stepOut' });
    const method = methods[args.kind];
    if (!method) throw new DbgError('BAD_ARGS', 'step needs over|into|out');
    const timeoutMs = Math.max(1, Number(args.timeout ?? 30)) * 1000;
    let state = this.#requirePaused();
    const cdp = this.#requireCdp();
    const startKey = await this.#lineKey(state);
    const startDepth = state.params.callFrames?.length ?? 0;
    for (let i = 0; i < SMART_STEP_LIMIT; i++) {
      const seq = this.#pauseSeq;
      const from = state;
      await cdp.send(method);
      // Same ack-before-Debugger.resumed window as resume(): never leave the pre-step pause visible.
      if (this.#paused === from) this.#paused = null;
      state = await this.#waitForPause(seq, timeoutMs);
      // Step over generated statements until the ORIGINAL line changes (one TS line can be many JS statements).
      if (args.kind !== 'over' || !['other', 'step'].includes(state.params.reason) || state.params.hitBreakpoints?.length) break;
      if ((state.params.callFrames?.length ?? 0) !== startDepth || (await this.#lineKey(state)) !== startKey) break;
    }
    return this.#snapshot(state, {});
  }

  /** @param {PauseState} state */
  async #lineKey(state) {
    const frame = state.params.callFrames?.[0];
    if (!frame) return '';
    const record = this.#scripts.get(frame.location.scriptId);
    const orig = await this.#originalOf(record, frame.location.lineNumber, frame.location.columnNumber ?? 0);
    return orig ? `${orig.source}:${orig.line}` : `${frame.url}:${frame.location.lineNumber}`;
  }

  async resume() {
    const state = this.#requirePaused();
    await this.#requireCdp().send('Debugger.resume');
    // V8 acks Debugger.resume before emitting Debugger.resumed; retire the pause now so a `wait`
    // issued in that window cannot return it. A newer pause (seq differs) is left untouched.
    if (this.#paused === state) this.#paused = null;
    return { resumed: true };
  }

  async pause() {
    await this.#afterInternalPause();
    const cdp = this.#requireCdp();
    if (this.#paused) return this.#snapshot(this.#paused, {});
    const seq = this.#pauseSeq;
    await cdp.send('Debugger.pause');
    try {
      return await this.#snapshot(await this.#waitForPause(seq, 3000), {});
    } catch (err) {
      if (err instanceof DbgError && err.code === 'WAIT_TIMEOUT') {
        return { paused: false, pauseRequested: true, note: 'target is idle; it will pause when JavaScript next runs (use `dbg wait`)' };
      }
      throw err;
    }
  }

  /** @param {{ state: string }} args */
  async setExceptions(args) {
    if (!['none', 'uncaught', 'all'].includes(args.state)) throw new DbgError('BAD_ARGS', 'exceptions needs none|uncaught|all');
    const cdp = this.#requireCdp();
    await cdp.send('Debugger.setPauseOnExceptions', { state: args.state });
    this.#exceptions = /** @type {'none' | 'uncaught' | 'all'} */ (args.state);
    return { exceptions: this.#exceptions };
  }

  // -------------------------------------------------------------------------
  // Logpoints
  // -------------------------------------------------------------------------

  /** @param {any} p Runtime.consoleAPICalled params */
  #onConsole(p) {
    const head = p.args?.[0];
    if (head?.type !== 'string' || typeof head.value !== 'string' || !head.value.startsWith(this.#marker)) return;
    const bp = this.#breakpoints.get(head.value.slice(this.#marker.length));
    if (!bp) return;
    bp.hits++;
    const binding = bp.bindings[0];
    const location = binding?.original
      ? `${displayPath(binding.original.source, this.#cwd)}:${binding.original.line + 1}`
      : bp.spec;
    /** @type {LogHit} */
    const hit = {
      seq: ++this.#logSeq,
      ts: new Date(p.timestamp ?? Date.now()).toISOString(),
      breakpoint: bp.id,
      location,
      value: null,
    };
    const valueObj = p.args[1];
    hit.value = valueObj?.type === 'object' && valueObj.preview ? fromObjectPreview(valueObj.preview) : null;
    if (hit.value === null) void previewValue(valueObj, 1).then((v) => (hit.value = v));
    this.#logs.push(hit);
    if (this.#logs.length > LOG_RING_SIZE) this.#logs.splice(0, this.#logs.length - LOG_RING_SIZE);
    // Logged objects are retained by the inspector's console storage; release them periodically.
    if (this.#logSeq % 100 === 0) this.#cdp?.send('Runtime.discardConsoleEntries').catch(() => {});
  }

  /** @param {{ since?: number, limit?: number, tail?: boolean }} args */
  logs(args) {
    const since = Number(args.since ?? 0);
    const limit = Number(args.limit ?? 20);
    if (!Number.isSafeInteger(since) || since < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > LOG_RING_SIZE) {
      throw new DbgError('BAD_ARGS', `logs since must be a nonnegative integer; limit must be 1..${LOG_RING_SIZE}`);
    }
    const matching = this.#logs.filter((h) => h.seq > since);
    const hits = args.tail ? matching.slice(-limit) : matching.slice(0, limit);
    return {
      hits,
      nextSeq: hits.at(-1)?.seq ?? since,
      hasMore: !args.tail && matching.length > hits.length,
      latestSeq: this.#logSeq,
      // Compatibility only: nextSeq is the cursor for lossless pagination.
      lastSeq: this.#logSeq,
      ...(matching.length > hits.length ? { omitted: matching.length - hits.length } : {}),
      ...(this.#logs[0] && this.#logs[0].seq > since + 1 ? { dropped: this.#logs[0].seq - since - 1 } : {}),
    };
  }

  // -------------------------------------------------------------------------
  // Status and shutdown
  // -------------------------------------------------------------------------

  status() {
    const bps = [...this.#breakpoints.values()];
    const bound = bps.filter((bp) => bp.bindings.length > 0).length;
    const stale = bps.filter((bp) => bp.bindings.length === 0 && bp.staleMap);
    const scripts = [...this.#scripts.values()];
    return {
      attached: this.#attached,
      kind: this.#profile.kind,
      inspector: `${this.#host}:${this.#port}`,
      target: this.#target ? this.#targetJSON(this.#target) : null,
      ...(this.#displaced ? { state: 'displaced', displaced: { ...this.#displaced, hint: 'run `dbg start` again to reattach (disconnects the other debugger)' } } : {}),
      paused: this.#paused ? { reason: this.#paused.params.reason } : false,
      breakpoints: {
        total: bps.length,
        bound,
        pending: bps.length - bound - stale.length,
        staleMap: stale.length,
        logpoints: bps.filter((b) => b.kind === 'logpoint').length,
      },
      ...(stale.length
        ? { staleSourceMaps: stale.map((bp) => ({ breakpoint: bp.id, script: bp.staleMap?.url, message: bp.staleMap?.message })) }
        : {}),
      scripts: { parsed: scripts.length, withSourceMap: scripts.filter((s) => s.sourceMapURL).length, mapsLoaded: this.#maps.size },
      instrumentation: this.#instrumentationSupported,
      loadTrap: this.#trapId !== null,
      exceptions: this.#exceptions,
      reconnects: this.#reconnects,
      logHits: this.#logSeq,
      ...(this.#lastAttachError && !this.#attached ? { lastError: this.#lastAttachError } : {}),
    };
  }

  /** @param {any} t */
  #targetJSON(t) {
    /** @type {Record<string, unknown>} */
    const out = { id: t.id, title: t.title };
    if (t.url) out.url = t.url;
    if (t.description) out.description = t.description;
    if (t.deviceName) out.deviceName = t.deviceName;
    return out;
  }

  /** Remove our breakpoints, resume, close. Never throws. */
  async shutdown() {
    this.#stopping = true;
    for (const waiter of this.#pauseWaiters) waiter.reject(new DbgError('STOPPED', 'daemon is stopping'));
    this.#pauseWaiters.clear();
    const cdp = this.#cdp;
    if (!cdp) return;
    const t = { timeoutMs: SHUTDOWN_CDP_TIMEOUT_MS };
    await Promise.all([...this.#breakpoints.values()].map((bp) => this.#unbind(bp, SHUTDOWN_CDP_TIMEOUT_MS)));
    if (this.#trapId) await cdp.send('Debugger.removeBreakpoint', { breakpointId: this.#trapId }, t).catch(() => {});
    if (this.#instrumentationId) await cdp.send('Debugger.removeBreakpoint', { breakpointId: this.#instrumentationId }, t).catch(() => {});
    if (this.#exceptions !== 'none') await cdp.send('Debugger.setPauseOnExceptions', { state: 'none' }, t).catch(() => {});
    if (this.#paused) await cdp.send('Debugger.resume', {}, t).catch(() => {});
    await cdp.send('Debugger.disable', {}, t).catch(() => {});
    cdp.close();
  }
}
