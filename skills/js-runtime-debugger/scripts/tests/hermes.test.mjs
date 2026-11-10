/**
 * `dbg --kind hermes` behavior that does not need a device: Metro page selection, the Fusebox
 * handshake order, single-debugger displacement and `dbg reload`, against a minimal fake of
 * Metro's inspector proxy (tests/fake-metro.mjs). Breakpoint binding through a real Hermes runtime
 * is verified live (see the skill's Hermes reference), not here.
 */

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, test } from 'node:test';

import { pickHermesTarget } from '../lib/targets.mjs';
import { startFakeMetro } from './fake-metro.mjs';
import { alive, eventually, runDbg } from './helpers.mjs';

/** Two devices, each with a legacy "UI" page and a modern Fusebox page (shape of a real RN 0.83 Metro). */
const METRO_LIST = [
  {
    id: 'aaa-5',
    title: 'io.app (iPhone 17 Pro Max)',
    description: 'UI [C++ connection]',
    deviceName: 'iPhone 17 Pro Max',
    webSocketDebuggerUrl: 'ws://localhost:8081/inspector/debug?device=aaa&page=5',
    reactNative: { logicalDeviceId: 'aaa', capabilities: { nativePageReloads: false, prefersFuseboxFrontend: false } },
  },
  {
    id: 'aaa-1',
    title: 'io.app (iPhone 17 Pro Max)',
    description: 'React Native Bridgeless [C++ connection]',
    deviceName: 'iPhone 17 Pro Max',
    webSocketDebuggerUrl: 'ws://localhost:8081/inspector/debug?device=aaa&page=1',
    reactNative: { logicalDeviceId: 'aaa', capabilities: { nativePageReloads: true, prefersFuseboxFrontend: true } },
  },
  {
    id: 'bbb-1',
    title: 'io.app (iPhone 17 Pro)',
    description: 'React Native Bridgeless [C++ connection]',
    deviceName: 'iPhone 17 Pro',
    webSocketDebuggerUrl: 'ws://localhost:8081/inspector/debug?device=bbb&page=1',
    reactNative: { logicalDeviceId: 'bbb', capabilities: { nativePageReloads: true, prefersFuseboxFrontend: true } },
  },
];

describe('Metro page selection', () => {
  test('modern (Fusebox) page wins over the legacy UI page of the same device', () => {
    assert.equal(pickHermesTarget(METRO_LIST, undefined)?.id, 'aaa-1');
  });

  test('--target matches title / description / deviceName', () => {
    assert.equal(pickHermesTarget(METRO_LIST, 'iPhone 17 Pro$')?.id, 'bbb-1');
    assert.equal(pickHermesTarget(METRO_LIST, '^UI ')?.id, 'aaa-5');
    assert.equal(pickHermesTarget(METRO_LIST, 'Android'), undefined);
  });

  test('a reattach stays on the previously attached device', () => {
    assert.equal(pickHermesTarget(METRO_LIST, undefined, 'bbb')?.id, 'bbb-1');
  });
});

describe('dbg --kind hermes against a fake Metro proxy', { timeout: 30_000 }, () => {
  /** @type {string} */ let home;
  /** @type {Awaited<ReturnType<typeof startFakeMetro>>} */ let metro;
  /** @type {number | null} */ let daemonPid = null;
  const run = (/** @type {string[]} */ ...args) => runDbg({ home, cwd: home }, args);

  before(async () => {
    home = mkdtempSync(path.join(os.tmpdir(), 'dbg-hermes-'));
    metro = await startFakeMetro();
  });

  after(async () => {
    await run('stop');
    if (daemonPid !== null && alive(daemonPid)) process.kill(daemonPid, 'SIGKILL');
    await metro.close();
    rmSync(home, { recursive: true, force: true });
  });

  test('attaches to the modern page; Fusebox metadata is sent before the domains are enabled', async () => {
    const started = await run('start', '--kind', 'hermes', '--port', String(metro.port), '--metro-root', '.');
    assert.equal(started.code, 0, JSON.stringify(started.json));
    daemonPid = started.json.pid;
    assert.equal(started.json.attached, true);
    assert.equal(started.json.target.id, 'dev1-1');
    assert.equal(started.json.target.deviceName, 'Fake Phone');
    assert.equal(started.json.instrumentation, false);

    const methods = metro.connections[0].methods;
    const at = (/** @type {string} */ m) => methods.indexOf(m);
    assert.ok(at('FuseboxClient.setClientMetadata') !== -1, JSON.stringify(methods));
    assert.ok(at('FuseboxClient.setClientMetadata') < at('Runtime.enable'));
    assert.ok(at('FuseboxClient.setClientMetadata') < at('Debugger.enable'));
    assert.equal(at('Debugger.setInstrumentationBreakpoint'), -1, 'Hermes has no instrumentation breakpoints');
  });

  test('reload sends Page.reload when the page supports native reloads', async () => {
    const res = await run('reload');
    assert.equal(res.code, 0, JSON.stringify(res.json));
    assert.equal(res.json.reloaded, true);
    assert.ok(metro.connections[0].methods.includes('Page.reload'));
  });

  test('a normal disconnect (app restart) reattaches on its own', async () => {
    metro.kick('[CONNECTION_LOST] Connection lost to corresponding device.');
    await eventually(async () => (await run('status')).json.reconnects === 1 && (await run('status')).json.attached, 10_000, 'reattach');
    assert.equal(metro.connections.length, 2);
  });

  test('being displaced by another debugger is reported and never auto-reconnects', async () => {
    metro.kick('[NEW_DEBUGGER_OPENED] New debugger opened for the same app instance');
    await eventually(async () => (await run('status')).json.state === 'displaced', 5000, 'displaced state');
    const status = await run('status');
    assert.equal(status.json.attached, false);
    assert.match(status.json.displaced.reason, /NEW_DEBUGGER_OPENED/);
    assert.match(status.json.displaced.hint, /dbg start/);

    const refused = await run('eval', '1 + 1');
    assert.equal(refused.json.error.code, 'DISPLACED');

    // Give an (incorrect) reconnect loop time to show up: no new connection may be opened.
    await new Promise((r) => setTimeout(r, 1000));
    assert.equal(metro.connections.length, 2, 'must not kick the other debugger back');
  });

  test('explicit start reattaches a displaced session', async () => {
    const res = await run('start', '--kind', 'hermes', '--port', String(metro.port));
    assert.equal(res.code, 0, JSON.stringify(res.json));
    assert.equal(res.json.reattached, true);
    assert.equal(res.json.attached, true);
    assert.equal(res.json.state, undefined);
    assert.equal(metro.connections.length, 3);
  });
});
