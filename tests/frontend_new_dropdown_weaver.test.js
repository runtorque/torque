const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function createSandbox() {
  const sandbox = {
    console,
    AGENT_ICONS: ['🤖'],
    PROCESS_MAP: {},
    state: {
      group_settings: {},
      agents: {},
    },
    sendCalls: [],
    modalCalls: [],
    esc(value) { return String(value); },
    _cachedAgentTemplates: [],
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.openWeaverLaunchDialog = function(group, agentId) {
    sandbox.modalCalls.push({ group, agentId: agentId || '' });
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return sandbox;
}

function loadScript(context, relPath) {
  const filename = path.join(repoRoot, relPath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

test('new dropdown renders Weaver entry only when a group has no weaver', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');

  const htmlWithoutWeaver = vm.runInContext(`_renderWeaverMenuItem('alpha', {})`, context);
  const htmlWithWeaver = vm.runInContext(`_renderWeaverMenuItem('alpha', { weaver_agent_id: 'weaver-1' })`, context);

  assert.match(htmlWithoutWeaver, />Weaver<\/button>/);
  assert.equal(htmlWithWeaver, '');
});

test('newWeaver opens the dedicated Weaver launch dialog', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/commands.js');

  vm.runInContext(`newWeaver('alpha')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.modalCalls)), [
    {
      group: 'alpha',
      agentId: '',
    },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), []);
});

test('newWeaver is a no-op when the group already has a weaver', () => {
  const sandbox = createSandbox();
  sandbox.state.group_settings.alpha = { weaver_agent_id: 'weaver-1' };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/commands.js');

  vm.runInContext(`newWeaver('alpha')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), []);
});

test('relaunchAgent routes stopped designated weavers through the launch dialog', () => {
  const sandbox = createSandbox();
  sandbox.state.group_settings.alpha = { weaver_agent_id: 'weaver-1' };
  sandbox.state.agents['weaver-1'] = {
    id: 'weaver-1',
    group: 'alpha',
    status: 'stopped',
  };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/commands.js');

  vm.runInContext(`relaunchAgent('weaver-1')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.modalCalls)), [
    { group: 'alpha', agentId: 'weaver-1' },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), []);
});

test('relaunchAgent keeps normal agents on the plain relaunch path', () => {
  const sandbox = createSandbox();
  sandbox.state.group_settings.alpha = { weaver_agent_id: 'weaver-1' };
  sandbox.state.agents['agent-1'] = {
    id: 'agent-1',
    group: 'alpha',
    status: 'stopped',
  };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/commands.js');

  vm.runInContext(`relaunchAgent('agent-1')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.modalCalls)), []);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'relaunch_agent', id: 'agent-1' },
  ]);
});
