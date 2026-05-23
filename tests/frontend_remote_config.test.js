// Remote web UI: bootstrap config parse, endpoint derivation, auth-mode + flag.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadConfig() {
  const sandbox = { console };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const src = fs.readFileSync(
    path.join(repoRoot, 'ee/frontend/remote/js/config.js'), 'utf8');
  vm.runInContext(src, sandbox, { filename: 'config.js' });
  return sandbox.RemoteConfig;
}

test('parseConfig validates required fields + defaults', () => {
  const C = loadConfig();
  const ok = C.parseConfig({ relayBaseUrl: 'https://relay.test/', daemonId: 'd1' });
  assert.equal(ok.valid, true);
  assert.equal(ok.relayBaseUrl, 'https://relay.test', 'trailing slash stripped');
  assert.equal(ok.authMode, 'cookie', 'cookie-mode default when no token');
  assert.equal(ok.askEnabled, true, 'ask UI enabled by default (P6-V1 completion)');

  const bad = C.parseConfig({ relayBaseUrl: '' });
  assert.equal(bad.valid, false);
  assert.ok(bad.errors.length >= 2);
});

test('ask UI can be opted out via ask_enabled=0; snapshotLimit parses', () => {
  const C = loadConfig();
  const off = C.parseConfig({ relayBaseUrl: 'https://r', daemonId: 'd', ask_enabled: '0' });
  assert.equal(off.askEnabled, false, 'ask_enabled=0 opts out');
  const explicit = C.parseConfig({ relayBaseUrl: 'https://r', daemonId: 'd', askEnabled: false });
  assert.equal(explicit.askEnabled, false);

  const lim = C.parseConfig({ relayBaseUrl: 'https://r', daemonId: 'd', snapshot_limit: '50' });
  assert.equal(lim.snapshotLimit, 50);
  const noLim = C.parseConfig({ relayBaseUrl: 'https://r', daemonId: 'd' });
  assert.equal(noLim.snapshotLimit, 0, 'omitted -> 0 (server default applies)');
});

test('bearer-mode inferred when a token is provided', () => {
  const C = loadConfig();
  const cfg = C.parseConfig({
    relayBaseUrl: 'https://r', daemonId: 'd', sessionToken: 'sess-x',
  });
  assert.equal(cfg.authMode, 'bearer');
});

test('wsUrl derives ws(s) scheme, client_id, and token only in bearer-mode', () => {
  const C = loadConfig();
  const cookie = C.parseConfig({ relayBaseUrl: 'https://r.test', daemonId: 'd 1', clientId: 'c1' });
  const ws = C.wsUrl(cookie);
  assert.ok(ws.startsWith('wss://r.test/v1/client/d%201/ws'), ws);
  assert.ok(ws.includes('client_id=c1'));
  assert.ok(!ws.includes('token='), 'cookie-mode never puts token in the URL');

  const bearer = C.parseConfig({ relayBaseUrl: 'http://127.0.0.1:8787', daemonId: 'd', sessionToken: 't' });
  const wsb = C.wsUrl(bearer);
  assert.ok(wsb.startsWith('ws://127.0.0.1:8787/v1/client/d/ws'), wsb);
  assert.ok(wsb.includes('token=t'));
});

test('messagesUrl + httpHeaders honor auth mode', () => {
  const C = loadConfig();
  const cookie = C.parseConfig({ relayBaseUrl: 'https://r', daemonId: 'd' });
  assert.equal(C.messagesUrl(cookie), 'https://r/v1/messages/d');
  assert.equal(C.httpHeaders(cookie).authorization, undefined);

  const bearer = C.parseConfig({ relayBaseUrl: 'https://r', daemonId: 'd', sessionToken: 'tok' });
  assert.equal(C.httpHeaders(bearer).authorization, 'Bearer tok');
});

test('loadFromDocument merges config tag + URL params (params override)', () => {
  const C = loadConfig();
  const docRef = {
    getElementById(id) {
      if (id === 'torque-remote-config') {
        return { textContent: JSON.stringify({ relayBaseUrl: 'https://tag', daemonId: 'dtag' }) };
      }
      return null;
    },
  };
  const loc = { search: '?daemonId=dparam&ask=1', hash: '' };
  const cfg = C.loadFromDocument(docRef, loc);
  assert.equal(cfg.relayBaseUrl, 'https://tag');
  assert.equal(cfg.daemonId, 'dparam', 'URL param overrides the tag');
  assert.equal(cfg.askEnabled, true);
});
