const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function createSandbox() {
  const sandbox = {
    console,
    state: { weaver_settings: {} },
    document: {
      activeElement: null,
      getElementById() { return null; },
    },
    window: {},
    _cachedProviders: [],
    _esc(value) { return String(value); },
    _currentGroup() { return 'alpha'; },
    sendCalls: [],
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return sandbox;
}

function loadWeaver(context) {
  const filename = path.join(repoRoot, 'static/js/weaver.js');
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

test('weaver settings render separate idle heartbeat controls', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  const html = vm.runInContext(
    `_weaverRenderSettings("alpha", {
      push_interval: 60,
      max_interval: 300,
      heartbeat_interval: 0,
      enabled_events: []
    }, { name: "Weaver", status: "running" })`,
    context,
  );

  assert.match(html, /<label>Push interval<\/label>/);
  assert.match(html, /<label>Max interval<\/label>/);
  assert.match(html, /<label>Idle heartbeat<\/label>/);
  assert.match(html, /Send idle heartbeat if no digest was sent for:/);
  assert.match(html, /option value="0" selected>Off<\/option>/);
});

test('weaverUpdateSetting sends heartbeat interval payload', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext(`weaverUpdateSetting("heartbeat_interval", 600)`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'weaver_update_settings',
      group: 'alpha',
      heartbeat_interval: 600,
    },
  ]);
});

test('weaver journal distinguishes blocking asks from non-blocking notes', () => {
  const sandbox = createSandbox();
  sandbox.state.weaver_settings.alpha = {
    pending_question: 'Need approval to merge?',
    pending_note: 'FYI: branch is ready for review',
    pending_note_kind: 'question',
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  const html = vm.runInContext(`_weaverRenderJournal("alpha")`, context);

  assert.match(html, /class="weaver-ask-banner"/);
  assert.match(html, /Weaver is asking:/);
  assert.match(html, /class="weaver-note-banner"/);
  assert.match(html, /Weaver asks \(non-blocking\):/);
  assert.match(html, /weaverDismissNote/);
});

test('weaverDismissNote clears the non-blocking banner without resuming', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext(`weaverDismissNote()`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'weaver_dismiss_note',
      group: 'alpha',
    },
  ]);
});
