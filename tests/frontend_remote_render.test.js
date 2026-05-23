// Remote web UI: render builders + paint discipline.
//   - agent list / conversation / ask-card HTML
//   - ask cards GATED behind cfg.askEnabled (option (i))
//   - history affordance reflects snapshot vs replay-only
//   - paintSurface innerHTML-cache discipline (bc9e1d0f9f66) + surface preserve

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function makeFakeTextarea(id) {
  const focusCalls = [];
  return {
    id, value: 'draft in progress', selectionStart: 3, selectionEnd: 3,
    scrollTop: 0, scrollLeft: 0, focusCalls,
    focus(opts) { focusCalls.push(opts === undefined ? null : opts); },
  };
}

function buildSandbox(activeElement, lookup) {
  const sandbox = {
    console,
    document: {
      activeElement: activeElement || null,
      getElementById(id) { return (lookup && lookup[id]) || null; },
    },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  ['markdown.js', 'surface.js', 'store.js', 'render_remote.js'].forEach((f) => {
    vm.runInContext(
      fs.readFileSync(path.join(repoRoot, 'ee/frontend/remote/js', f), 'utf8'),
      sandbox, { filename: f });
  });
  return sandbox;
}

function agentMsg(id, agentId, body, type) {
  return { v: 1, id, daemon_id: 'd', source: { kind: 'daemon', id: 'd' },
    target: { kind: 'remote-client', id: 'user', user_id: 'user' },
    kind: type === 'ask' ? 'ask' : 'agent_message', created_at: new Date().toISOString(),
    payload: { agent_id: agentId, sender_kind: 'worker', sender_name: agentId,
      message: body, message_id: id, message_type: type || 'message',
      blocking: type === 'ask' } };
}

test('agentListHtml: empty state + populated rows', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  assert.match(s.RemoteRender.agentListHtml(store), /No conversations yet/);
  store.ingestInbound(agentMsg('m1', 'worker-a', 'hello'));
  const html = s.RemoteRender.agentListHtml(store);
  assert.match(html, /data-agent-id="worker-a"/);
  assert.match(html, /worker-a/);
});

test('ask cards are gated behind cfg.askEnabled (no client-side recipient filtering)', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestInbound(agentMsg('ask-1', 'w', 'approve the deploy?', 'ask'));
  const conv = store.activeConversation();

  const gatedOff = s.RemoteRender.conversationHtml(conv, { askEnabled: false });
  assert.doesNotMatch(gatedOff, /data-ask-reply-for/, 'no answerable card when gated off');
  assert.match(gatedOff, /approve the deploy\?/, 'ask body still shown as a plain message');

  const gatedOn = s.RemoteRender.conversationHtml(conv, { askEnabled: true });
  assert.match(gatedOn, /remote-ask-card/);
  assert.match(gatedOn, /data-ask-reply-for="ask-1"/);
});

test('messageHtml escapes raw text via the markdown renderer', () => {
  const s = buildSandbox();
  const html = s.RemoteRender.messageHtml({ id: 'x', sender: 'agent',
    body: '<img src=x onerror=alert(1)>', messageType: 'message' });
  assert.doesNotMatch(html, /<img/, 'raw HTML is escaped, not parsed');
  assert.match(html, /&lt;img/);
});

test('history affordance: hidden after snapshot, shown for replay-only', () => {
  const s = buildSandbox();
  assert.equal(s.RemoteRender.historyNoticeHtml(true), '');
  assert.match(s.RemoteRender.historyNoticeHtml(false), /Showing new messages/);
});

test('paintSurface caches HTML and skips an identical repaint (cache discipline)', () => {
  const s = buildSandbox();
  let writes = 0; let stored = '';
  const el = {
    get innerHTML() { return stored; },
    set innerHTML(v) { stored = v; writes += 1; },
    contains() { return false; }, querySelector() { return null; },
    scrollTop: 0, scrollLeft: 0,
  };
  assert.equal(s.RemoteRender.paintSurface(el, '<p>one</p>'), true);
  assert.equal(writes, 1);
  assert.equal(s.RemoteRender.paintSurface(el, '<p>one</p>'), false, 'identical repaint skipped');
  assert.equal(writes, 1, 'innerHTML not rewritten when unchanged');
  assert.equal(s.RemoteRender.paintSurface(el, '<p>two</p>'), true);
  assert.equal(writes, 2);
});

test('paintSurface preserve restores focus with preventScroll across a repaint', () => {
  const textarea = makeFakeTextarea('ask-input-x');
  const s = buildSandbox(textarea, { 'ask-input-x': textarea });
  let stored = '';
  const panel = {
    get innerHTML() { return stored; },
    set innerHTML(v) { stored = v; },
    contains(node) { return node === textarea; },
    querySelector() { return null; },
    scrollTop: 0, scrollLeft: 0,
  };
  // First paint establishes content; second paint changes it (rebuild), and the
  // focused ask-reply draft must survive.
  s.RemoteRender.paintSurface(panel, '<p>old</p>', { preserve: true });
  textarea.focusCalls.length = 0;
  s.RemoteRender.paintSurface(panel, '<p>new</p>', { preserve: true });
  assert.equal(textarea.focusCalls.length, 1, 'focus re-asserted after rebuild');
  assert.ok(textarea.focusCalls[0] && textarea.focusCalls[0].preventScroll === true,
    'focus uses {preventScroll:true} so scroll restoration is not overridden');
});
