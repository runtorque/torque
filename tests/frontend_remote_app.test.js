// Remote web UI: DOM boot guard for invalid relay config.
// Verifies an invalid RemoteConfig is surfaced as an explicit terminal config
// error and never falls through to the relay client's generic disconnected loop.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function makeElement(id) {
  let html = '';
  return {
    id,
    value: '',
    disabled: id === 'remote-composer-input',
    listeners: {},
    get innerHTML() { return html; },
    set innerHTML(v) { html = String(v); },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    contains() { return false; },
    querySelector() { return null; },
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
  };
}

function loadApp(rawConfig) {
  const elements = {
    'torque-remote-config': { textContent: JSON.stringify(rawConfig || {}) },
    'remote-banner': makeElement('remote-banner'),
    'remote-agent-list': makeElement('remote-agent-list'),
    'remote-conversation': makeElement('remote-conversation'),
    'remote-composer': makeElement('remote-composer'),
    'remote-composer-input': makeElement('remote-composer-input'),
    'remote-history-notice': makeElement('remote-history-notice'),
  };
  const sandbox = {
    console,
    location: { search: '', hash: '' },
    document: {
      readyState: 'complete',
      getElementById(id) { return elements[id] || null; },
      addEventListener() { throw new Error('DOMContentLoaded listener should not be needed'); },
    },
    localStorage: {
      getItem() { return null; },
      setItem() {},
    },
    __connectCalls: 0,
    __clientConstructs: 0,
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;

  function RemoteStore() {
    this.snapshotApplied = false;
  }
  RemoteStore.prototype.subscribe = function() {};
  RemoteStore.prototype.agentList = function() { return []; };
  RemoteStore.prototype.activeConversation = function() { return null; };
  RemoteStore.prototype.setActiveAgent = function() {};
  sandbox.RemoteStore = RemoteStore;

  function RemoteRelayClient(opts) {
    sandbox.__clientConstructs += 1;
    this.opts = opts;
  }
  RemoteRelayClient.STATUS = {
    IDLE: 'idle',
    READY: 'ready',
  };
  RemoteRelayClient.prototype.connect = function() {
    sandbox.__connectCalls += 1;
  };
  RemoteRelayClient.prototype.sendUserMessage = function() {};
  sandbox.RemoteRelayClient = RemoteRelayClient;

  vm.createContext(sandbox);
  ['config.js', 'render_remote.js', 'app.js'].forEach((f) => {
    vm.runInContext(
      fs.readFileSync(path.join(repoRoot, 'ee/frontend/remote/js', f), 'utf8'),
      sandbox, { filename: f });
  });
  sandbox.__elements = elements;
  return sandbox;
}

test('app boot renders config-error and skips connect when daemonId is empty or missing', () => {
  const cases = [
    ['empty daemonId', { relayBaseUrl: 'https://relay.test', daemonId: '' }],
    ['missing daemonId', { relayBaseUrl: 'https://relay.test' }],
  ];
  for (const [label, raw] of cases) {
    const s = loadApp(raw);
    const banner = s.__elements['remote-banner'].innerHTML;
    assert.equal(s.__connectCalls, 0, label + ': relay connect was not attempted');
    assert.equal(s.__clientConstructs, 0, label + ': relay client was not constructed');
    assert.match(banner, /remote-banner-config-error/, label + ': config-error class rendered');
    assert.match(banner, /Relay config error/, label + ': explicit config error title rendered');
    assert.match(banner, /daemonId is required/, label + ': offending field is named');
    assert.doesNotMatch(banner, /Disconnected/, label + ': generic disconnected copy is not shown');
    assert.equal(s.__torqueRemote.configError, true, label + ': boot exposes terminal config error');
  }
});

test('app boot with valid config still follows the normal connect path', () => {
  const s = loadApp({
    relayBaseUrl: 'https://relay.test',
    daemonId: 'daemon-1',
    clientId: 'client-1',
  });
  assert.equal(s.__clientConstructs, 1, 'relay client constructed once');
  assert.equal(s.__connectCalls, 1, 'connect called once');
  assert.ok(s.__torqueRemote.client, 'client exposed for diagnostics');
  assert.equal(s.__torqueRemote.configError, undefined);
  assert.doesNotMatch(s.__elements['remote-banner'].innerHTML, /remote-banner-config-error/);
});
