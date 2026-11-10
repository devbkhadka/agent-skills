/**
 * Minimal stand-in for Metro's inspector proxy (not a test file): `/json/list` with one React
 * Native page and a WebSocket endpoint that answers every CDP request with an empty result and
 * records the methods it received. `kick(reason)` closes the newest debugger socket the way Metro
 * does (close frame with a reason), e.g. `[NEW_DEBUGGER_OPENED] ...`.
 *
 * Only what the dbg daemon needs: RFC 6455 handshake, masked client text frames, server text and
 * close frames. Built on node:http upgrade so the suite stays dependency-free.
 */

import { createHash } from 'node:crypto';
import http from 'node:http';

const WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

/**
 * @typedef {{ socket: import('node:net').Socket, methods: string[], closed: boolean }} FakeConnection
 */

/**
 * @param {number} opcode
 * @param {Buffer} payload
 */
function frame(opcode, payload) {
  const len = payload.length;
  const header = len < 126 ? Buffer.from([0x80 | opcode, len]) : Buffer.alloc(4);
  if (len >= 126) {
    if (len > 0xffff) throw new Error('fake-metro: frame too large');
    header[0] = 0x80 | opcode;
    header[1] = 126;
    header.writeUInt16BE(len, 2);
  }
  return Buffer.concat([header, payload]);
}

/**
 * Pop complete client frames off `buffer`.
 * @param {Buffer} buffer
 * @returns {{ frames: { opcode: number, payload: Buffer }[], rest: Buffer }}
 */
function parseFrames(buffer) {
  /** @type {{ opcode: number, payload: Buffer }[]} */
  const frames = [];
  let offset = 0;
  while (buffer.length - offset >= 2) {
    const opcode = buffer[offset] & 0x0f;
    const masked = (buffer[offset + 1] & 0x80) !== 0;
    let len = buffer[offset + 1] & 0x7f;
    let pos = offset + 2;
    if (len === 126) {
      if (buffer.length < pos + 2) break;
      len = buffer.readUInt16BE(pos);
      pos += 2;
    } else if (len === 127) {
      if (buffer.length < pos + 8) break;
      len = Number(buffer.readBigUInt64BE(pos));
      pos += 8;
    }
    const maskLen = masked ? 4 : 0;
    if (buffer.length < pos + maskLen + len) break;
    const mask = masked ? buffer.subarray(pos, pos + 4) : null;
    pos += maskLen;
    const payload = Buffer.from(buffer.subarray(pos, pos + len));
    if (mask) for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
    frames.push({ opcode, payload });
    offset = pos + len;
  }
  return { frames, rest: buffer.subarray(offset) };
}

/**
 * @returns {Promise<{ port: number, connections: FakeConnection[], kick: (reason: string) => void, close: () => Promise<void> }>}
 */
export async function startFakeMetro() {
  /** @type {FakeConnection[]} */
  const connections = [];
  let port = 0;
  const server = http.createServer((req, res) => {
    if (req.url === '/json/list') {
      const ws = `ws://127.0.0.1:${port}/inspector/debug?device=dev1&page=1`;
      res.setHeader('content-type', 'application/json');
      res.end(
        JSON.stringify([
          {
            id: 'dev1-2',
            title: 'com.example (Fake Phone)',
            description: 'UI [C++ connection]',
            appId: 'com.example',
            type: 'node',
            webSocketDebuggerUrl: `ws://127.0.0.1:${port}/inspector/debug?device=dev1&page=2`,
            deviceName: 'Fake Phone',
            reactNative: { logicalDeviceId: 'dev1', capabilities: { nativePageReloads: false, prefersFuseboxFrontend: false } },
          },
          {
            id: 'dev1-1',
            title: 'com.example (Fake Phone)',
            description: 'React Native Bridgeless [C++ connection]',
            appId: 'com.example',
            type: 'node',
            webSocketDebuggerUrl: ws,
            deviceName: 'Fake Phone',
            reactNative: { logicalDeviceId: 'dev1', capabilities: { nativePageReloads: true, prefersFuseboxFrontend: true } },
          },
        ]),
      );
      return;
    }
    res.statusCode = 404;
    res.end();
  });
  server.on('upgrade', (req, socket) => {
    const key = String(req.headers['sec-websocket-key'] ?? '');
    const accept = createHash('sha1').update(key + WS_GUID).digest('base64');
    socket.write(
      'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n' +
        `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
    );
    /** @type {FakeConnection} */
    const conn = { socket, methods: [], closed: false };
    connections.push(conn);
    let buffer = Buffer.alloc(0);
    socket.on('data', (chunk) => {
      const parsed = parseFrames(Buffer.concat([buffer, chunk]));
      buffer = parsed.rest;
      for (const f of parsed.frames) {
        if (f.opcode === 0x8) {
          if (!conn.closed) socket.end(frame(0x8, f.payload.subarray(0, 2)));
          conn.closed = true;
          continue;
        }
        if (f.opcode !== 0x1) continue;
        const msg = JSON.parse(f.payload.toString('utf8'));
        conn.methods.push(msg.method);
        socket.write(frame(0x1, Buffer.from(JSON.stringify({ id: msg.id, result: {} }))));
      }
    });
    socket.on('close', () => (conn.closed = true));
    socket.on('error', () => (conn.closed = true));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(undefined)));
  port = /** @type {import('node:net').AddressInfo} */ (server.address()).port;

  return {
    port,
    connections,
    kick(reason) {
      const conn = [...connections].reverse().find((c) => !c.closed);
      if (!conn) throw new Error('fake-metro: no open debugger connection');
      const payload = Buffer.concat([Buffer.from([0x03, 0xe8]), Buffer.from(reason, 'utf8')]);
      conn.closed = true;
      conn.socket.end(frame(0x8, payload));
    },
    close: () =>
      new Promise((resolve) => {
        for (const c of connections) c.socket.destroy();
        server.close(() => resolve());
      }),
  };
}
