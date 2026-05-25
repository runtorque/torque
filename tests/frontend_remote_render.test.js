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

test('agentPickerHtml: one option per agent with the active agent selected', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestSnapshot({
    kind: 'snapshot', created_at: new Date().toISOString(),
    payload: { agents: [
      { id: 'worker-a', name: 'Worker Alpha', kind: 'worker' },
      { id: 'worker-b', name: 'Worker Beta', kind: 'worker' },
    ] },
  });
  store.setActiveAgent('worker-b');

  const html = s.RemoteRender.agentPickerHtml(store);

  assert.equal((html.match(/<option/g) || []).length, 2);
  assert.match(html, /<select[^>]+data-agent-picker/);
  assert.match(html, /<option value="worker-a">Worker Alpha<\/option>/);
  assert.match(html, /<option value="worker-b" selected>Worker Beta<\/option>/);
});

test('agentListHtml remains the desktop list render path', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestInbound(agentMsg('m1', 'worker-a', 'hello'));

  assert.equal(s.RemoteRender.agentListHtml(store),
    '<button type="button" class="remote-agent-item is-active" data-agent-id="worker-a">'
      + '<span class="remote-agent-name">worker-a</span>'
      + '<span class="remote-agent-preview">hello</span></button>');
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

// --- #2 generic relay error banner ---------------------------------------
test('bannerHtml surfaces a generic relay error (message, then code)', () => {
  const s = buildSandbox();
  const withMsg = s.RemoteRender.bannerHtml('error', { payload: { code: 'remote_ingress_failed', message: 'agent is offline' } });
  assert.match(withMsg, /remote-banner-error/);
  assert.match(withMsg, /role="alert"/);
  assert.match(withMsg, /agent is offline/);

  const codeOnly = s.RemoteRender.bannerHtml('error', { payload: { code: 'boom' } });
  assert.match(codeOnly, /boom/, 'falls back to code when no message');

  const empty = s.RemoteRender.bannerHtml('error', {});
  assert.match(empty, /Relay error/, 'safe default when payload is empty');
});

test('error banner does not regress normal connection banners', () => {
  const s = buildSandbox();
  assert.match(s.RemoteRender.bannerHtml('reconnecting', {}), /Reconnecting/);
  assert.equal(s.RemoteRender.bannerHtml('ready', {}), '', 'ready stays silent');
  assert.match(s.RemoteRender.bannerHtml('reauth_required', {}), /re-pair/);
});

test('bannerHtml surfaces config errors as terminal config-error state', () => {
  const s = buildSandbox();
  const html = s.RemoteRender.bannerHtml('config_error', {
    errors: ['daemonId is required'],
  });
  assert.match(html, /remote-banner-config-error/);
  assert.match(html, /role="alert"/);
  assert.match(html, /Relay config error/);
  assert.match(html, /daemonId is required/);
  assert.doesNotMatch(html, /Disconnected/);
});

// --- #3 stick-to-bottom on inbound (at-tail gated) ------------------------
function makeConvEl(initial) {
  let stored = '';
  const el = {
    scrollTop: initial.scrollTop,
    scrollHeight: initial.scrollHeight,
    clientHeight: initial.clientHeight,
    get innerHTML() { return stored; },
    set innerHTML(v) { stored = v; el.scrollHeight = initial.grownScrollHeight; },
    contains() { return false; }, querySelector() { return null; },
  };
  return el;
}

test('stick-to-bottom: at tail + inbound repaint sticks to the new bottom', () => {
  const s = buildSandbox(); // no focused element
  // At bottom: scrollHeight 100, clientHeight 50, scrollTop 50 -> distance 0.
  const el = makeConvEl({ scrollTop: 50, scrollHeight: 100, clientHeight: 50, grownScrollHeight: 160 });
  el._remoteLastHtml = '<p>old</p>'; // force a repaint without tripping the scrollHeight setter
  const repainted = s.RemoteRender.paintSurface(el, '<p>old</p><p>new</p>',
    { preserve: true, surfaceOpts: { stickToBottom: ':root' } });
  assert.equal(repainted, true);
  assert.equal(el.scrollTop, 160, 're-pinned to the grown bottom');
});

test('stick-to-bottom: scrolled up + inbound repaint preserves position', () => {
  const s = buildSandbox();
  // Scrolled up: scrollTop 0, distance = 100-50-0 = 50 > threshold(24).
  const el = makeConvEl({ scrollTop: 0, scrollHeight: 100, clientHeight: 50, grownScrollHeight: 160 });
  el._remoteLastHtml = '<p>old</p>';
  const repainted = s.RemoteRender.paintSurface(el, '<p>old</p><p>new</p>',
    { preserve: true, surfaceOpts: { stickToBottom: ':root' } });
  assert.equal(repainted, true);
  assert.equal(el.scrollTop, 0, 'did not yank a scrolled-up reader to the bottom');
});
