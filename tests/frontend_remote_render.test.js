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

test('agentListHtml: renders role-kind badges for each known agent kind', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestSnapshot({
    kind: 'snapshot', created_at: new Date().toISOString(),
    payload: { agents: [
      { id: 'architect-a', name: 'Architect Alpha', kind: 'architect' },
      { id: 'engineer-a', name: 'Engineer Alpha', kind: 'engineer' },
      { id: 'worker-a', name: 'Worker Alpha', kind: 'worker' },
      { id: 'terminal-a', name: 'Terminal Alpha', kind: 'terminal' },
    ] },
  });

  const html = s.RemoteRender.agentListHtml(store);

  assert.match(html, /class="remote-badge remote-badge-kind remote-badge-kind-architect">Architect<\/span>/);
  assert.match(html, /class="remote-badge remote-badge-kind remote-badge-kind-engineer">Engineer<\/span>/);
  assert.match(html, /class="remote-badge remote-badge-kind remote-badge-kind-worker">Worker<\/span>/);
  assert.match(html, /class="remote-badge remote-badge-kind remote-badge-kind-terminal">Terminal<\/span>/);
});

test('agentListHtml: omits role-kind badges for blank or unknown agent kinds', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestSnapshot({
    kind: 'snapshot', created_at: new Date().toISOString(),
    payload: { agents: [
      { id: 'agent-blank', name: 'Blank Kind', kind: '' },
      { id: 'agent-unknown', name: 'Unknown Kind', kind: 'bot' },
    ] },
  });

  const html = s.RemoteRender.agentListHtml(store);

  assert.match(html, /Blank Kind/);
  assert.match(html, /Unknown Kind/);
  assert.doesNotMatch(html, /remote-badge-kind/);
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
  assert.match(html, /<option value="worker-a">Worker · Worker Alpha<\/option>/);
  assert.match(html, /<option value="worker-b" selected>Worker · Worker Beta<\/option>/);
});

test('agentPickerHtml: prefixes options with known kind labels only', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestSnapshot({
    kind: 'snapshot', created_at: new Date().toISOString(),
    payload: { agents: [
      { id: 'architect-a', name: 'Architect Alpha', kind: 'architect' },
      { id: 'engineer-a', name: 'Engineer Alpha', kind: 'engineer' },
      { id: 'worker-a', name: 'Worker Alpha', kind: 'worker' },
      { id: 'terminal-a', name: 'Terminal Alpha', kind: 'terminal' },
      { id: 'agent-unknown', name: 'Unknown Kind', kind: 'bot' },
      { id: 'agent-blank', name: 'Blank Kind', kind: '' },
    ] },
  });

  const html = s.RemoteRender.agentPickerHtml(store);

  assert.match(html, /<option value="architect-a" selected>Architect · Architect Alpha<\/option>/);
  assert.match(html, /<option value="engineer-a">Engineer · Engineer Alpha<\/option>/);
  assert.match(html, /<option value="worker-a">Worker · Worker Alpha<\/option>/);
  assert.match(html, /<option value="terminal-a">Terminal · Terminal Alpha<\/option>/);
  assert.match(html, /<option value="agent-unknown">Unknown Kind<\/option>/);
  assert.match(html, /<option value="agent-blank">Blank Kind<\/option>/);
});

test('agentListHtml remains the desktop list render path', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestInbound(agentMsg('m1', 'worker-a', 'hello'));

  assert.equal(s.RemoteRender.agentListHtml(store),
    '<button type="button" class="remote-agent-item is-active" data-agent-id="worker-a">'
      + '<span class="remote-agent-heading">'
      + '<span class="remote-agent-name">worker-a</span>'
      + '</span>'
      + '<span class="remote-agent-preview">hello</span></button>');
});

test('agentListHtml: renders live status/activity/context chips in the sidebar only', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestInbound(agentMsg('m1', 'worker-a', 'hello'));
  store.ingestAgentState({
    agent_id: 'worker-a',
    agent_type: 'claude-code',
    kind: 'worker',
    status: 'running',
    activity_detail: 'Editing render_remote.js',
    needs_attention: true,
    context_window: { used_percentage: 72 },
    provider_usage: {
      five_hour: { available: true, used_percentage: 64,
        resets_at: new Date(Date.now() + 90 * 60 * 1000).toISOString() },
      seven_day: { available: false, used_percentage: null, resets_at: null },
    },
    lastStateMs: 1000,
  });

  const list = s.RemoteRender.agentListHtml(store);
  assert.match(list, /remote-agent-status-attention/);
  assert.match(list, /needs attention/);
  assert.match(list, /Editing render_remote\.js/);
  assert.match(list, /ctx 72%/);
  assert.doesNotMatch(list, /5h 36% left/, 'provider usage moved out of per-agent rows');
  assert.doesNotMatch(list, /7d/, 'provider usage moved out of per-agent rows');
  assert.match(list, /remote-agent-attention/);

  const usage = s.RemoteRender.providerUsageSummaryHtml(store);
  assert.match(usage, /Provider usage/);
  assert.match(usage, /data-provider="claude-code"/);
  assert.match(usage, /Claude Code/);
  assert.match(usage, /5h 36% left/);
  assert.match(usage, /7d —/);

  const conv = s.RemoteRender.conversationHtml(store.activeConversation(), { askEnabled: true });
  assert.doesNotMatch(conv, /ctx 72%/, 'state chips stay out of the conversation pane');
  assert.doesNotMatch(conv, /5h 36% left/, 'provider usage stays out of the conversation pane');
});

test('agentListHtml: state clears remove context chips and heartbeats do not reorder', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  const base = Date.parse('2026-05-24T12:00:00.000Z');
  store.ingestInbound(agentMsg('old', 'worker-old', 'older'));
  store.conversations['worker-old'].lastActivityMs = base;
  store.ingestInbound(agentMsg('new', 'worker-new', 'newer'));
  store.conversations['worker-new'].lastActivityMs = base + 1000;
  const before = store.agentList().map((a) => a.agentId);

  store.ingestAgentState({
    agent_id: 'worker-old',
    agent_type: 'claude-code',
    status: 'running',
    activity_detail: 'heartbeat',
    context_window: { used_percentage: 91 },
    provider_usage: {
      five_hour: { available: true, used_percentage: 25,
        resets_at: new Date(Date.now() + 60 * 60 * 1000).toISOString() },
      seven_day: { available: false, used_percentage: null, resets_at: null },
    },
    lastStateMs: 1000,
  });
  assert.deepEqual(store.agentList().map((a) => a.agentId), before,
    'state-only heartbeat preserves list order');
  assert.match(s.RemoteRender.agentListHtml(store), /ctx 91%/);
  assert.doesNotMatch(s.RemoteRender.agentListHtml(store), /5h 75% left/);
  assert.match(s.RemoteRender.providerUsageSummaryHtml(store), /5h 75% left/);

  store.ingestAgentState({
    agent_id: 'worker-old',
    context_window: null,
    provider_usage: null,
    lastStateMs: 2000,
  });
  const afterClear = s.RemoteRender.agentListHtml(store);
  assert.doesNotMatch(afterClear, /ctx 91%/);
  assert.doesNotMatch(afterClear, /5h 75% left/);
  assert.match(s.RemoteRender.providerUsageSummaryHtml(store), /Claude Code/);
  assert.match(s.RemoteRender.providerUsageSummaryHtml(store), /remote-provider-usage-row--unknown/);
});

test('providerUsageSummaryHtml: provider windows render no-data when unavailable', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestInbound(agentMsg('m1', 'worker-a', 'hello'));
  store.ingestAgentState({
    agent_id: 'worker-a',
    agent_type: 'codex',
    provider_usage: {
      five_hour: { available: false, used_percentage: null, resets_at: null },
      seven_day: { available: false, used_percentage: null, resets_at: null },
    },
    lastStateMs: 1000,
  });

  const list = s.RemoteRender.agentListHtml(store);
  assert.doesNotMatch(list, /5h/);
  assert.doesNotMatch(list, /7d/);
  const html = s.RemoteRender.providerUsageSummaryHtml(store);
  assert.match(html, /data-provider="codex"/);
  assert.match(html, /Codex/);
  assert.match(html, /remote-provider-usage-value">—<\/span>/);
});

test('providerUsageSummaryHtml: groups by agent_type and selects one available freshest payload', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  const reset = new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString();
  store.ingestAgentState({
    agent_id: 'claude-stale',
    agent_type: 'claude-code',
    provider_usage: {
      five_hour: { available: true, used_percentage: 90, resets_at: reset },
      seven_day: { available: true, used_percentage: 10, resets_at: reset },
    },
    lastStateMs: 1000,
  });
  store.ingestAgentState({
    agent_id: 'claude-fresh',
    agent_type: 'claude-code',
    provider_usage: {
      five_hour: { available: true, used_percentage: 20, resets_at: reset },
      seven_day: { available: true, used_percentage: 30, resets_at: reset },
    },
    lastStateMs: 2000,
  });

  const html = s.RemoteRender.providerUsageSummaryHtml(store);
  assert.equal((html.match(/data-provider="claude-code"/g) || []).length, 1,
    'Claude Code renders once globally, not once per agent');
  assert.match(html, /5h 80% left/);
  assert.match(html, /7d 70% left/);
  assert.doesNotMatch(html, /5h 10% left/, 'older payload is not rendered');
});

test('providerUsageSummaryHtml: omits rows without agent_type until S2 projection lands', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestAgentState({
    agent_id: 'missing-provider',
    provider_usage: {
      five_hour: { available: true, used_percentage: 10,
        resets_at: new Date(Date.now() + 60 * 60 * 1000).toISOString() },
      seven_day: { available: true, used_percentage: 20,
        resets_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString() },
    },
    lastStateMs: 1000,
  });

  assert.equal(s.RemoteRender.providerUsageSummaryHtml(store), '');
});

test('agentListHtml: provider_usage-only changes do not dirty the per-agent list HTML', () => {
  const s = buildSandbox();
  const store = new s.RemoteStore({});
  store.ingestInbound(agentMsg('m1', 'worker-a', 'hello'));
  store.ingestAgentState({
    agent_id: 'worker-a',
    agent_type: 'claude-code',
    context_window: { used_percentage: 44 },
    provider_usage: {
      five_hour: { available: true, used_percentage: 10,
        resets_at: new Date(Date.now() + 60 * 60 * 1000).toISOString() },
      seven_day: { available: true, used_percentage: 20,
        resets_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString() },
    },
    lastStateMs: 1000,
  });
  const before = s.RemoteRender.agentListHtml(store);
  store.ingestAgentState({
    agent_id: 'worker-a',
    agent_type: 'claude-code',
    provider_usage: {
      five_hour: { available: true, used_percentage: 70,
        resets_at: new Date(Date.now() + 3 * 60 * 60 * 1000).toISOString() },
      seven_day: { available: true, used_percentage: 80,
        resets_at: new Date(Date.now() + 4 * 60 * 60 * 1000).toISOString() },
    },
    lastStateMs: 2000,
  });
  const after = s.RemoteRender.agentListHtml(store);

  assert.equal(after, before, 'global usage changes avoid per-agent list rewrites');
  assert.match(s.RemoteRender.providerUsageSummaryHtml(store), /5h 30% left/);
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
