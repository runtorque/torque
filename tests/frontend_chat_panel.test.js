const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadScript(context, relPath) {
  const source = fs.readFileSync(path.join(repoRoot, relPath), 'utf8');
  vm.runInContext(source, context, { filename: relPath });
}

function createHarness() {
  const sandbox = {
    console,
    Date,
    state: {
      agents: {
        blueprint: { id: 'blueprint', name: 'Blueprint', slug: 'blueprint', kind: 'architect', group: 'alpha' },
        torqly: { id: 'torqly', name: 'Torqly', slug: 'torqly', kind: 'architect', group: 'alpha' },
      },
      agent_peer_threads: {},
    },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/chat.js');
  return context;
}

test('chat panel resolves architect message sender and recipient IDs to visible names', () => {
  const context = createHarness();
  const html = vm.runInContext(`_chatMessageCardHtml({
    id: 'msg-1',
    thread_id: 'thread-1',
    action: 'architect_peer_message',
    sender_id: 'blueprint',
    sender_kind: 'architect',
    recipient_id: 'torqly',
    recipient_kind: 'architect',
    message: 'Please review the PM polish scope.',
    timestamp: 100
  }, 0, {
    thread_id: 'thread-1',
    participants: [
      { id: 'blueprint', kind: 'architect' },
      { id: 'torqly', kind: 'architect' }
    ]
  })`, context);

  assert.match(html, /chat-message-sender[^>]*>Blueprint</);
  assert.match(html, /chat-message-recipient[^>]*>To Torqly</);
  assert.doesNotMatch(html, /chat-message-sender[^>]*>blueprint</);
  assert.doesNotMatch(html, /chat-message-recipient[^>]*>To torqly</);
});

test('chat participant chips and thread rows prefer names while preserving ID fallback', () => {
  const context = createHarness();
  const named = vm.runInContext(`_chatSelectedHeaderHtml({
    thread_id: 'thread-1',
    title: 'Blueprint ↔ Torqly',
    group: 'alpha',
    participants: [
      { id: 'blueprint', kind: 'architect' },
      { id: 'torqly', kind: 'architect' }
    ],
    messages: []
  })`, context);
  assert.match(named, /Blueprint/);
  assert.match(named, /Torqly/);

  const fallback = vm.runInContext(`_chatSelectedHeaderHtml({
    thread_id: 'thread-2',
    title: 'Hidden participant fallback',
    participants: [{ id: 'arch-hidden', kind: 'architect' }],
    messages: []
  })`, context);
  assert.match(fallback, /arch-hidden/);
});
