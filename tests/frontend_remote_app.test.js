// Remote web UI: DOM-bound app boot and composer regressions.
// Verifies invalid RemoteConfig is surfaced as an explicit terminal config error
// and that the textarea composer mirrors the main Torque UI Enter keybinding.

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
    listeners: Object.create(null),
    get innerHTML() { return html; },
    set innerHTML(v) { html = String(v); },
    addEventListener(type, fn) {
      if (!this.listeners[type]) this.listeners[type] = [];
      this.listeners[type].push(fn);
    },
    dispatchEvent(ev) {
      ev.target = ev.target || this;
      ev.currentTarget = ev.currentTarget || this;
      const fns = this.listeners[ev.type] || [];
      for (const fn of fns) fn(ev);
      return !ev.defaultPrevented;
    },
    requestSubmit() {
      this.dispatchEvent(makeEvent('submit'));
    },
    contains() { return false; },
    querySelector() { return null; },
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
  };
}

function makeEvent(type, props = {}) {
  return Object.assign({
    type,
    key: '',
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    isComposing: false,
    defaultPrevented: false,
    propagationStopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
  }, props);
}

function validConfig() {
  return {
    relayBaseUrl: 'https://relay.test',
    daemonId: 'daemon-1',
    clientId: 'client-1',
  };
}

function loadApp(rawConfig, opts = {}) {
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
    __sendCalls: [],
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;

  function RemoteStore() {
    this.snapshotApplied = false;
  }
  RemoteStore.prototype.subscribe = function() {};
  RemoteStore.prototype.agentList = function() { return []; };
  RemoteStore.prototype.activeConversation = function() {
    return opts.activeConversation === undefined ? null : opts.activeConversation;
  };
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
  RemoteRelayClient.prototype.sendUserMessage = function(agentId, text, options) {
    sandbox.__sendCalls.push({ agentId, text, options });
  };
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

function pressComposerKey(s, props = {}) {
  const input = s.__elements['remote-composer-input'];
  const ev = makeEvent('keydown', Object.assign({ key: 'Enter' }, props));
  input.dispatchEvent(ev);
  if (!ev.defaultPrevented && ev.key === 'Enter') {
    input.value += '\n';
  }
  return ev;
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
  const s = loadApp(validConfig());
  assert.equal(s.__clientConstructs, 1, 'relay client constructed once');
  assert.equal(s.__connectCalls, 1, 'connect called once');
  assert.ok(s.__torqueRemote.client, 'client exposed for diagnostics');
  assert.equal(s.__torqueRemote.configError, undefined);
  assert.doesNotMatch(s.__elements['remote-banner'].innerHTML, /remote-banner-config-error/);
});

test('composer Enter submits through the existing send path and clears input', () => {
  const s = loadApp(validConfig(), {
    activeConversation: { agentId: 'agent-1', messages: [] },
  });
  const input = s.__elements['remote-composer-input'];
  input.value = '  hello from app  ';

  const ev = pressComposerKey(s);

  assert.equal(ev.defaultPrevented, true, 'plain Enter does not insert a newline');
  assert.equal(ev.propagationStopped, true, 'plain Enter mirrors main composer propagation');
  assert.deepEqual(JSON.parse(JSON.stringify(s.__sendCalls)), [
    { agentId: 'agent-1', text: 'hello from app', options: { threadId: '' } },
  ]);
  assert.equal(input.value, '', 'existing submit handler clears after send');
});

test('composer Shift+Enter keeps the default textarea newline and does not send', () => {
  const s = loadApp(validConfig(), {
    activeConversation: { agentId: 'agent-1', messages: [] },
  });
  const input = s.__elements['remote-composer-input'];
  input.value = 'line one';

  const ev = pressComposerKey(s, { shiftKey: true });

  assert.equal(ev.defaultPrevented, false, 'Shift+Enter is left to the textarea');
  assert.deepEqual(s.__sendCalls, []);
  assert.equal(input.value, 'line one\n');
});

test('composer Enter on whitespace input follows the submit guard and does not send', () => {
  const s = loadApp(validConfig(), {
    activeConversation: { agentId: 'agent-1', messages: [] },
  });
  const input = s.__elements['remote-composer-input'];
  input.value = '   \n  ';

  const ev = pressComposerKey(s);

  assert.equal(ev.defaultPrevented, true, 'plain Enter still routes to submit');
  assert.deepEqual(s.__sendCalls, []);
  assert.equal(input.value, '   \n  ', 'empty submit guard leaves the draft intact');
});

test('composer Enter does not submit while IME composition is active', () => {
  const s = loadApp(validConfig(), {
    activeConversation: { agentId: 'agent-1', messages: [] },
  });
  const input = s.__elements['remote-composer-input'];
  input.value = 'composing';

  const ev = pressComposerKey(s, { isComposing: true });

  assert.equal(ev.defaultPrevented, false, 'composing Enter is not intercepted');
  assert.deepEqual(s.__sendCalls, []);
});
