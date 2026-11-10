/**
 * Minimal Chrome DevTools Protocol client over the global WebSocket (Node >= 22).
 *
 * One instance = one WebSocket connection. It never reconnects: the session layer owns
 * reconnect policy and creates a fresh client per attach.
 */

import { DbgError } from './ipc.mjs';

export const CDP_TIMEOUT_MS = 10_000;

/**
 * @typedef {(params: any) => void} CdpListener
 */

export class CdpClient {
  /** @type {WebSocket} */ #ws;
  #nextId = 1;
  /** @type {Map<number, { resolve: (v: any) => void, reject: (e: Error) => void, timer: NodeJS.Timeout, method: string }>} */
  #pending = new Map();
  /** @type {Map<string, Set<CdpListener>>} */
  #listeners = new Map();
  /** @type {Set<(reason: string) => void>} */
  #closeListeners = new Set();
  #closed = false;

  /** @param {WebSocket} ws an OPEN socket */
  constructor(ws) {
    this.#ws = ws;
    ws.addEventListener('message', (event) => this.#onMessage(event.data));
    // The peer's close reason matters: Metro kicks a displaced debugger with "[NEW_DEBUGGER_OPENED] ...".
    ws.addEventListener('close', (event) => this.#onClose(event.reason ? `socket closed: ${event.reason}` : 'socket closed'));
    ws.addEventListener('error', () => this.#onClose('socket error'));
  }

  /**
   * @param {string} wsUrl
   * @param {{ timeoutMs?: number }} [options]
   * @returns {Promise<CdpClient>}
   */
  static connect(wsUrl, options = {}) {
    const timeoutMs = options.timeoutMs ?? CDP_TIMEOUT_MS;
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      const timer = setTimeout(() => {
        ws.close();
        reject(new DbgError('CDP_CONNECT', `timed out connecting to ${wsUrl}`));
      }, timeoutMs);
      ws.addEventListener('open', () => {
        clearTimeout(timer);
        resolve(new CdpClient(ws));
      });
      ws.addEventListener('error', () => {
        clearTimeout(timer);
        reject(new DbgError('CDP_CONNECT', `cannot connect to ${wsUrl}`));
      });
    });
  }

  get closed() {
    return this.#closed;
  }

  /**
   * @param {string} method
   * @param {Record<string, unknown>} [params]
   * @param {{ timeoutMs?: number }} [options]
   * @returns {Promise<any>}
   */
  send(method, params = {}, options = {}) {
    if (this.#closed) return Promise.reject(new DbgError('DETACHED', `not attached (cannot send ${method})`));
    const id = this.#nextId++;
    const timeoutMs = options.timeoutMs ?? CDP_TIMEOUT_MS;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        reject(new DbgError('CDP_TIMEOUT', `${method} timed out after ${timeoutMs} ms`));
      }, timeoutMs);
      this.#pending.set(id, { resolve, reject, timer, method });
      try {
        this.#ws.send(JSON.stringify({ id, method, params }));
      } catch (err) {
        clearTimeout(timer);
        this.#pending.delete(id);
        reject(new DbgError('DETACHED', `send ${method} failed: ${/** @type {Error} */ (err).message}`));
      }
    });
  }

  /**
   * @param {string} event CDP event name, e.g. `Debugger.paused`
   * @param {CdpListener} listener
   */
  on(event, listener) {
    let set = this.#listeners.get(event);
    if (!set) this.#listeners.set(event, (set = new Set()));
    set.add(listener);
  }

  /** @param {(reason: string) => void} listener */
  onClose(listener) {
    this.#closeListeners.add(listener);
  }

  close() {
    if (this.#closed) return;
    try {
      this.#ws.close();
    } catch {
      // already closing
    }
    this.#onClose('closed by client');
  }

  /** @param {unknown} data */
  #onMessage(data) {
    let msg;
    try {
      msg = JSON.parse(typeof data === 'string' ? data : Buffer.from(/** @type {ArrayBuffer} */ (data)).toString('utf8'));
    } catch {
      return;
    }
    if (typeof msg.id === 'number') {
      const pending = this.#pending.get(msg.id);
      if (!pending) return;
      this.#pending.delete(msg.id);
      clearTimeout(pending.timer);
      if (msg.error) {
        pending.reject(new DbgError('CDP_ERROR', `${pending.method}: ${msg.error.message ?? 'error'}`, { cdpCode: msg.error.code }));
      } else pending.resolve(msg.result ?? {});
      return;
    }
    if (typeof msg.method === 'string') {
      const set = this.#listeners.get(msg.method);
      if (!set) return;
      for (const listener of set) {
        try {
          listener(msg.params ?? {});
        } catch (err) {
          process.stderr.write(`[cdp] listener for ${msg.method} threw: ${/** @type {Error} */ (err).stack}\n`);
        }
      }
    }
  }

  /** @param {string} reason */
  #onClose(reason) {
    if (this.#closed) return;
    this.#closed = true;
    for (const [id, pending] of this.#pending) {
      clearTimeout(pending.timer);
      pending.reject(new DbgError('DETACHED', `${pending.method} aborted: ${reason}`));
      this.#pending.delete(id);
    }
    for (const listener of this.#closeListeners) listener(reason);
    this.#closeListeners.clear();
  }
}
