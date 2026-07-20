const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function source(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
}

function classList() {
  const values = new Set();
  return {
    add(name) { values.add(name); },
    remove(name) { values.delete(name); },
    toggle(name, force) {
      if (force === undefined) {
        if (values.has(name)) values.delete(name);
        else values.add(name);
      } else if (force) values.add(name);
      else values.delete(name);
    },
    contains(name) { return values.has(name); },
  };
}

function createInboxContext() {
  const sends = [];
  const badge = { hidden: true, textContent: '' };
  const terminalWorkspace = {
    hidden: false,
    classList: classList(),
  };
  const button = {
    classList: classList(),
    attributes: {},
    focused: false,
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getBoundingClientRect() {
      return { left: 120, right: 144, top: 4, bottom: 28 };
    },
    focus() { this.focused = true; },
    querySelector(selector) {
      return selector === '.inbox-bell-badge' ? badge : null;
    },
  };
  const panel = {
    hidden: true,
    innerHTML: '',
    classList: classList(),
    offsetWidth: 420,
    offsetHeight: 560,
    style: {},
    contains() { return false; },
    querySelector() { return null; },
  };
  const sandbox = {
    console,
    Date,
    Number,
    Object,
    String,
    state: {
      operator_notices: {},
      operator_notice_summary: {},
    },
    document: {
      hidden: false,
      hasFocus() { return sandbox.documentFocused; },
      getElementById(id) {
        if (id === 'inbox-popover') return panel;
        return id === 'terminal-workspace' ? terminalWorkspace : null;
      },
      querySelector(selector) {
        return selector === '.inbox-bell-button' ? button : null;
      },
      querySelectorAll(selector) {
        return selector === '.inbox-bell-button' ? [button] : [];
      },
      addEventListener() {},
    },
    window: {
      innerWidth: 1200,
      innerHeight: 800,
      addEventListener() {},
    },
    documentFocused: true,
    esc(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    },
    send(message) { sends.push(message); },
    _showNoticeToast(notice) { sandbox.shownNotice = notice; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(source('static/js/inbox.js'), context);
  return { context, sandbox, panel, button, badge, sends, terminalWorkspace };
}

function directMessageNotice(id, agentId) {
  return {
    id,
    notice_type: 'notification',
    severity: 'info',
    category: 'direct_message',
    title: 'Message from Agent',
    message: 'A direct message',
    agent_id: agentId,
    read_at: 0,
    archived_at: 0,
  };
}

function prepareFocusedDirectConversation(sandbox, terminalWorkspace, agentId) {
  terminalWorkspace.classList.add('active');
  sandbox.state.runtime = { embedded_terminal: true };
  sandbox.state.agents = {
    [agentId]: { id: agentId, cell_type: 'agent', kind: 'worker' },
  };
  sandbox.selectedTerminalId = agentId;
  sandbox.focusedItemId = agentId;
}

function createToastContext() {
  const timers = [];
  const focusedAgents = [];
  const inboxOpens = [];

  function createElement(tagName) {
    return {
      tagName: String(tagName || '').toUpperCase(),
      className: '',
      classList: classList(),
      children: [],
      attributes: {},
      parentNode: null,
      textContent: '',
      setAttribute(name, value) { this.attributes[name] = String(value); },
      appendChild(child) {
        child.parentNode = this;
        this.children.push(child);
        return child;
      },
      remove() {
        if (!this.parentNode || !this.parentNode.children) return;
        const index = this.parentNode.children.indexOf(this);
        if (index >= 0) this.parentNode.children.splice(index, 1);
        this.parentNode = null;
      },
      addEventListener() {},
    };
  }

  function findByClass(root, name) {
    if (!root) return null;
    if (String(root.className || '').split(/\s+/).includes(name)) return root;
    for (const child of root.children || []) {
      const found = findByClass(child, name);
      if (found) return found;
    }
    return null;
  }

  const stack = createElement('div');
  stack.id = 'toast-stack';
  const sandbox = {
    console,
    Date,
    Number,
    Object,
    String,
    state: {
      agents: {
        'agent-a': { id: 'agent-a', cell_type: 'agent', kind: 'worker' },
        'agent-b': { id: 'agent-b', cell_type: 'agent', kind: 'worker' },
        deleted: { id: 'deleted', cell_type: 'agent', kind: 'worker', deleted_at: 1 },
      },
    },
    document: {
      body: {
        children: [],
        appendChild(element) {
          element.parentNode = this;
          this.children.push(element);
          if (element.id === 'toast-stack') sandbox.toastStack = element;
        },
      },
      createElement,
      getElementById(id) {
        return id === 'toast-stack' ? sandbox.toastStack : null;
      },
    },
    requestAnimationFrame(callback) { callback(); },
    setTimeout(callback, delay) {
      timers.push({ callback, delay });
      return timers.length;
    },
    clearTimeout() {},
    openInboxPopover() { inboxOpens.push(true); },
    focusAgent(id) { focusedAgents.push(id); },
    _isTombstonedAgent(agent) { return Number((agent && agent.deleted_at) || 0) > 0; },
    _agentFocusVisibleRootAgentId(id) {
      return id === 'inaccessible' ? '' : id;
    },
  };
  sandbox.toastStack = stack;
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(source('static/js/commands.js'), context);
  sandbox.focusAgent = function(id) { focusedAgents.push(id); };
  return { context, sandbox, stack, focusedAgents, inboxOpens, findByClass };
}

test('Inbox is global chrome opened from a notification bell, not a dockable panel', () => {
  const html = source('webview.html');
  const manager = source('static/js/panel_manager.js');
  const navigation = source('static/js/navigation/panel-launcher.js');
  const main = source('static/js/main.js');
  const render = source('static/js/render.js');

  assert.match(html, /class="hdr-btn hdr-icon-btn inbox-bell-button inbox-bell-button--header"/);
  assert.ok(
    html.indexOf('aria-label="Go to"') < html.indexOf('inbox-bell-button--header'),
    'the notification bell should be the rightmost header control',
  );
  assert.match(source('static/styles/feature-panels.css'), /\.inbox-bell-button--header\s*\{\s*margin-left: auto;/);
  assert.match(html, /id="inbox-popover" class="inbox-popover ui-popover"/);
  assert.doesNotMatch(html, /id="panel-inbox"/);
  assert.doesNotMatch(html, /data-app="inbox"/);
  assert.match(html, /static\/js\/inbox\.js/);
  assert.doesNotMatch(manager, /['"]inbox['"]/);
  assert.doesNotMatch(navigation, /\{ id: 'inbox', label: 'Inbox'/);
  assert.doesNotMatch(main, /panel-inbox|renderInboxPanel/);
  assert.doesNotMatch(render, /surface === 'inbox'/);
});

test('Inbox renders alerts separately and exposes durable lifecycle actions', () => {
  const { context, sandbox, panel, sends } = createInboxContext();
  sandbox.state.operator_notices = {
    a1: {
      id: 'a1',
      notice_type: 'alert',
      severity: 'error',
      title: 'Sync failed',
      message: '<unsafe> remote rejected',
      category: 'board_sync',
      task_id: 'TASK-1',
      action_kind: 'retry_board_sync',
      action_payload: { task_id: 'TASK-1' },
      occurrence_count: 2,
      created_at: 1,
      last_occurred_at: 2,
      read_at: 0,
      resolved_at: 0,
      dismissed_at: 0,
      archived_at: 0,
    },
    n1: {
      id: 'n1',
      notice_type: 'notification',
      severity: 'success',
      title: 'Worker finished',
      message: 'Alpha finished',
      created_at: 3,
      last_occurred_at: 3,
      read_at: 0,
      archived_at: 0,
    },
  };
  sandbox.state.operator_notice_summary = {
    open_alerts: 1,
    unread_alerts: 1,
    unread_notifications: 1,
    unread_total: 2,
    active_total: 2,
  };

  context.renderInbox();
  assert.match(panel.innerHTML, /Sync failed/);
  assert.match(panel.innerHTML, /&lt;unsafe&gt; remote rejected/);
  assert.doesNotMatch(panel.innerHTML, /Worker finished/);
  assert.match(panel.innerHTML, /2 occurrences/);
  assert.match(panel.innerHTML, />Resolve</);
  assert.match(panel.innerHTML, />Retry</);

  context.inboxLifecycle('a1', 'resolve');
  assert.deepEqual(
    JSON.parse(JSON.stringify(sends.pop())),
    { cmd: 'operator_notice_resolve', id: 'a1' },
  );
  context.inboxMarkAllRead();
  assert.deepEqual(
    JSON.parse(JSON.stringify(sends.pop())),
    { cmd: 'operator_notices_mark_all_read' },
  );
});

test('notification bell opens and closes the anchored Inbox overlay', () => {
  const { context, panel, button, sends } = createInboxContext();
  const event = {
    currentTarget: button,
    preventDefault() {},
    stopPropagation() {},
  };

  assert.equal(context.openInboxPopover(event), true);
  assert.equal(panel.hidden, false);
  assert.equal(button.attributes['aria-expanded'], 'true');
  assert.match(panel.innerHTML, /No alerts/);
  assert.deepEqual(
    JSON.parse(JSON.stringify(sends.pop())),
    {
      cmd: 'operator_notices_list',
      include_archived: true,
      limit: 200,
      offset: 0,
    },
  );

  context.closeInboxPopover({ restoreFocus: true });
  assert.equal(panel.hidden, true);
  assert.equal(button.attributes['aria-expanded'], 'false');
  assert.equal(button.focused, true);
});

test('notice deltas update state, badge, visible panel, and delivery overlay', () => {
  const { context, sandbox, badge, button } = createInboxContext();
  const notice = {
    id: 'a2',
    notice_type: 'alert',
    severity: 'warning',
    title: 'Agent needs attention',
    message: 'Blocked on input',
    read_at: 0,
    resolved_at: 0,
    dismissed_at: 0,
    archived_at: 0,
  };
  context.inboxReceiveUpsert({
    event: 'publish',
    notice,
    summary: {
      open_alerts: 1,
      unread_alerts: 1,
      unread_notifications: 0,
      unread_total: 1,
      active_total: 1,
    },
  });

  assert.equal(sandbox.state.operator_notices.a2.title, 'Agent needs attention');
  assert.equal(badge.hidden, false);
  assert.equal(badge.textContent, '1');
  assert.equal(button.classList.contains('inbox-has-alerts'), true);
  assert.equal(sandbox.shownNotice.id, 'a2');

  delete sandbox.shownNotice;
  context.inboxReceiveUpsert({
    event: 'read',
    notice: Object.assign({}, notice, { read_at: 4 }),
    summary: {
      open_alerts: 1,
      unread_alerts: 0,
      unread_notifications: 0,
      unread_total: 0,
      active_total: 1,
    },
  });
  assert.equal(sandbox.shownNotice, undefined, 'lifecycle updates do not create new overlays');
});

test('focused visible direct-message conversation acknowledges the routine notice without a toast', () => {
  const { context, sandbox, badge, sends, terminalWorkspace } = createInboxContext();
  prepareFocusedDirectConversation(sandbox, terminalWorkspace, 'agent-a');

  context.inboxReceiveUpsert({
    event: 'publish',
    notice: directMessageNotice('dm-a', 'agent-a'),
    summary: {
      open_alerts: 0,
      unread_alerts: 0,
      unread_notifications: 1,
      unread_total: 1,
      active_total: 1,
    },
  });

  assert.equal(sandbox.shownNotice, undefined, 'the inline conversation is the delivery surface');
  assert.ok(sandbox.state.operator_notices['dm-a'].read_at > 0, 'notice is acknowledged locally');
  assert.equal(sandbox.state.operator_notice_summary.unread_notifications, 0);
  assert.equal(sandbox.state.operator_notice_summary.unread_total, 0);
  assert.equal(badge.hidden, true, 'no redundant unread badge remains');
  assert.deepEqual(
    JSON.parse(JSON.stringify(sends.pop())),
    { cmd: 'operator_notice_mark_read', id: 'dm-a' },
    'the normal lifecycle command persists the acknowledgement',
  );
});

test('different agent or hidden/unfocused conversation retains direct-message notification delivery', () => {
  const { context, sandbox, sends, terminalWorkspace } = createInboxContext();
  prepareFocusedDirectConversation(sandbox, terminalWorkspace, 'agent-a');

  context.inboxReceiveUpsert({
    event: 'publish',
    notice: directMessageNotice('dm-b', 'agent-b'),
    summary: { unread_notifications: 1, unread_total: 1, active_total: 1 },
  });
  assert.equal(sandbox.shownNotice.id, 'dm-b', 'another agent remains unattended');
  assert.equal(sandbox.state.operator_notices['dm-b'].read_at, 0);
  assert.equal(sends.length, 0);

  delete sandbox.shownNotice;
  sandbox.selectedTerminalId = 'agent-a';
  sandbox.focusedItemId = 'agent-a';
  terminalWorkspace.classList.remove('active');
  context.inboxReceiveUpsert({
    event: 'publish',
    notice: directMessageNotice('dm-other-panel', 'agent-a'),
    summary: { unread_notifications: 2, unread_total: 2, active_total: 2 },
  });
  assert.equal(sandbox.shownNotice.id, 'dm-other-panel',
    'a different visible panel is not the direct-message conversation');
  assert.equal(sandbox.state.operator_notices['dm-other-panel'].read_at, 0);
  assert.equal(sends.length, 0);

  delete sandbox.shownNotice;
  terminalWorkspace.classList.add('active');
  sandbox.document.hidden = true;
  context.inboxReceiveUpsert({
    event: 'publish',
    notice: directMessageNotice('dm-hidden', 'agent-a'),
    summary: { unread_notifications: 3, unread_total: 3, active_total: 3 },
  });
  assert.equal(sandbox.shownNotice.id, 'dm-hidden', 'hidden windows retain current delivery behavior');
  assert.equal(sandbox.state.operator_notices['dm-hidden'].read_at, 0);
  assert.equal(sends.length, 0);

  delete sandbox.shownNotice;
  sandbox.document.hidden = false;
  sandbox.documentFocused = false;
  context.inboxReceiveUpsert({
    event: 'publish',
    notice: directMessageNotice('dm-unfocused', 'agent-a'),
    summary: { unread_notifications: 4, unread_total: 4, active_total: 4 },
  });
  assert.equal(sandbox.shownNotice.id, 'dm-unfocused', 'unfocused windows retain current delivery behavior');
  assert.equal(sandbox.state.operator_notices['dm-unfocused'].read_at, 0);
  assert.equal(sends.length, 0);
});

test('missing or non-function document.hasFocus retains routine direct-message delivery', () => {
  const { context, sandbox, sends, terminalWorkspace } = createInboxContext();
  prepareFocusedDirectConversation(sandbox, terminalWorkspace, 'agent-a');

  delete sandbox.document.hasFocus;
  context.inboxReceiveUpsert({
    event: 'publish',
    notice: directMessageNotice('dm-no-focus-api', 'agent-a'),
    summary: { unread_notifications: 1, unread_total: 1, active_total: 1 },
  });
  assert.equal(sandbox.shownNotice.id, 'dm-no-focus-api');
  assert.equal(sandbox.state.operator_notices['dm-no-focus-api'].read_at, 0);
  assert.equal(sandbox.state.operator_notice_summary.unread_notifications, 1);
  assert.equal(sends.length, 0);

  delete sandbox.shownNotice;
  sandbox.document.hasFocus = true;
  context.inboxReceiveUpsert({
    event: 'publish',
    notice: directMessageNotice('dm-invalid-focus-api', 'agent-a'),
    summary: { unread_notifications: 2, unread_total: 2, active_total: 2 },
  });
  assert.equal(sandbox.shownNotice.id, 'dm-invalid-focus-api');
  assert.equal(sandbox.state.operator_notices['dm-invalid-focus-api'].read_at, 0);
  assert.equal(sandbox.state.operator_notice_summary.unread_notifications, 2);
  assert.equal(sends.length, 0);
});

test('error notices remain sticky and focus transitions preserve direct-message attention state', () => {
  const { context, sandbox, sends, terminalWorkspace } = createInboxContext();
  prepareFocusedDirectConversation(sandbox, terminalWorkspace, 'agent-a');
  const originalToast = sandbox._showNoticeToast;
  sandbox._showNoticeToast = function(notice) {
    sandbox.shownNotice = notice;
    return originalToast(notice);
  };

  const errorNotice = Object.assign(directMessageNotice('dm-error', 'agent-a'), {
    severity: 'error',
  });
  context.inboxReceiveUpsert({
    event: 'publish',
    notice: errorNotice,
    summary: { unread_notifications: 1, unread_total: 1, active_total: 1 },
  });
  assert.equal(sandbox.shownNotice.id, 'dm-error', 'error delivery is never suppressed');
  assert.equal(sandbox.state.operator_notices['dm-error'].read_at, 0);
  assert.equal(sends.length, 0);

  delete sandbox.shownNotice;
  sandbox.selectedTerminalId = 'agent-b';
  sandbox.focusedItemId = 'agent-b';
  sandbox.state.agents['agent-b'] = { id: 'agent-b', cell_type: 'agent', kind: 'worker' };
  context.inboxReceiveUpsert({
    event: 'publish',
    notice: directMessageNotice('dm-after-focus-change', 'agent-a'),
    summary: { unread_notifications: 2, unread_total: 2, active_total: 2 },
  });
  assert.equal(sandbox.shownNotice.id, 'dm-after-focus-change');
  assert.equal(sandbox.state.operator_notices['dm-after-focus-change'].read_at, 0);
  assert.equal(sends.length, 0, 'a new focused agent must not acknowledge the old conversation');

  sandbox.selectedTerminalId = 'agent-a';
  sandbox.focusedItemId = 'agent-a';
  delete sandbox.shownNotice;
  context.inboxReceiveUpsert({
    event: 'publish',
    notice: directMessageNotice('dm-after-return', 'agent-a'),
    summary: { unread_notifications: 3, unread_total: 3, active_total: 3 },
  });
  assert.equal(sandbox.shownNotice, undefined, 'returning to the conversation restores inline acknowledgement');
  assert.equal(sandbox.state.operator_notices['dm-after-return'].read_at > 0, true);
  assert.deepEqual(JSON.parse(JSON.stringify(sends.pop())), {
    cmd: 'operator_notice_mark_read', id: 'dm-after-return',
  });
  assert.equal(sandbox.state.operator_notices['dm-after-focus-change'].read_at, 0,
    'the unattended message remains unread after focus transitions');
});

test('durable client errors and toast controls use the notification substrate', () => {
  const board = source('static/js/board/external-sync.js');
  const nativeApi = source('static/js/native_api.js');
  const commands = source('static/js/commands.js');
  const css = source('static/styles/modals.css');
  const deltas = source('static/js/ws/delta-registry.js');

  assert.match(board, /inboxReportClientError\(message/);
  assert.match(board, /action_kind:\s*taskId \? 'retry_board_sync'/);
  assert.match(nativeApi, /inboxReportClientError\(message/);
  assert.match(commands, /function _showNoticeToast/);
  assert.match(commands, /openInboxPopover/);
  assert.doesNotMatch(commands, /panelNavOpenPanel\('inbox'\)/);
  assert.match(commands, /className = 'toast-close'/);
  assert.match(commands, /mouseenter', pauseTimer/);
  assert.match(commands, /kind === 'error' \? 0/);
  assert.match(css, /\.toast-stack\s*\{/);
  assert.match(css, /\.toast-close\s*\{/);
  assert.match(deltas, /operator_notice_upsert/);
  assert.match(deltas, /operator_notices_read_all/);
});

test('message toast body click and keyboard open the exact canonical source agent', () => {
  const { context, stack, focusedAgents, findByClass } = createToastContext();

  const clickToast = context._showNoticeToast(directMessageNotice('dm-a', 'agent-a'));
  const clickBody = findByClass(clickToast, 'toast-content');
  assert.equal(clickBody.attributes.role, 'button');
  assert.equal(clickBody.attributes.tabindex, '0');
  clickBody.onclick({ stopPropagation() {} });
  assert.deepEqual(focusedAgents, ['agent-a']);

  const enterToast = context._showNoticeToast(directMessageNotice('dm-b', 'agent-b'));
  const enterBody = findByClass(enterToast, 'toast-content');
  let enterPrevented = false;
  enterBody.onkeydown({
    key: 'Enter',
    preventDefault() { enterPrevented = true; },
    stopPropagation() {},
  });
  assert.equal(enterPrevented, true);

  const spaceToast = context._showNoticeToast(directMessageNotice('dm-space', 'agent-a'));
  const spaceBody = findByClass(spaceToast, 'toast-content');
  let spacePrevented = false;
  spaceBody.onkeydown({
    key: ' ',
    preventDefault() { spacePrevented = true; },
    stopPropagation() {},
  });
  assert.equal(spacePrevented, true);
  assert.deepEqual(focusedAgents, ['agent-a', 'agent-b', 'agent-a']);
  assert.equal(stack.children.length, 3, 'dismiss animation preserves existing timing');
});

test('message toast explicit inbox and dismiss actions never trigger the body action', () => {
  const { context, focusedAgents, inboxOpens, findByClass } = createToastContext();
  const inboxToast = context._showNoticeToast(directMessageNotice('dm-inbox', 'agent-a'));
  const inboxAction = findByClass(inboxToast, 'toast-action');
  let inboxStopped = false;
  inboxAction.onclick({
    stopPropagation() { inboxStopped = true; },
  });
  assert.equal(inboxStopped, true);
  assert.deepEqual(inboxOpens, [true]);
  assert.deepEqual(focusedAgents, [], 'View inbox opens only the Inbox');

  const dismissToast = context._showNoticeToast(directMessageNotice('dm-close', 'agent-a'));
  const close = findByClass(dismissToast, 'toast-close');
  let closeStopped = false;
  close.onclick({
    stopPropagation() { closeStopped = true; },
  });
  assert.equal(closeStopped, true);
  assert.deepEqual(focusedAgents, [], 'dismiss does not open the source agent');
});

test('only routine message toasts with an accessible live source agent are interactive', () => {
  const { context, sandbox, focusedAgents, findByClass } = createToastContext();
  sandbox.state.agents.inaccessible = {
    id: 'inaccessible', cell_type: 'agent', kind: 'worker',
  };
  const cases = [
    directMessageNotice('missing', 'missing'),
    directMessageNotice('deleted', 'deleted'),
    directMessageNotice('inaccessible', 'inaccessible'),
    Object.assign(directMessageNotice('system', 'agent-a'), { category: 'system' }),
    Object.assign(directMessageNotice('sticky', 'agent-a'), { severity: 'error' }),
    Object.assign(directMessageNotice('alert', 'agent-a'), { notice_type: 'alert' }),
  ];

  for (const notice of cases) {
    const toast = context._showNoticeToast(notice);
    const body = findByClass(toast, 'toast-content');
    assert.equal(body.attributes.role, undefined, notice.id + ' remains non-interactive');
    assert.equal(body.onclick, undefined, notice.id + ' has no unsafe fallback focus');
  }
  assert.deepEqual(focusedAgents, []);

  const removedAfterDelivery = context._showNoticeToast(
    directMessageNotice('removed-after-delivery', 'agent-a'),
  );
  delete sandbox.state.agents['agent-a'];
  findByClass(removedAfterDelivery, 'toast-content').onclick({ stopPropagation() {} });
  assert.deepEqual(focusedAgents, [], 'a source removed before activation safely no-ops');
});

test('toast variants share a translucent surface without fading their contents', () => {
  const css = source('static/styles/modals.css');
  const toastRule = css.match(/\.toast\s*\{(?<body>[\s\S]*?)\}/)?.groups?.body || '';
  const visibleRule = css.match(/\.toast\.visible\s*\{(?<body>[\s\S]*?)\}/)?.groups?.body || '';

  assert.match(
    toastRule,
    /background:\s*color-mix\(in srgb,\s*var\(--bg-surface\)\s*90%,\s*transparent\)/,
    'routine and sticky/error variants should inherit the same translucent surface',
  );
  assert.match(visibleRule, /opacity:\s*1\b/,
    'visible toasts must keep text, icons, borders, and controls fully opaque');
  assert.doesNotMatch(visibleRule, /opacity:\s*(?:0?\.)?9(?:0+)?\b/,
    'surface translucency must not be implemented as whole-toast opacity');
});

test('View inbox is a compact text action on its own right-aligned toast row', () => {
  const css = source('static/styles/modals.css');
  const commands = source('static/js/commands.js');
  const actionRow = css.match(/\.toast-actions\s*\{(?<body>[\s\S]*?)\}/)?.groups?.body || '';
  const actionRules = Array.from(css.matchAll(/\.toast-action\s*\{(?<body>[\s\S]*?)\}/g));
  const actionRule = actionRules.map((match) => match.groups?.body || '').join('\n');

  assert.match(commands, /actions\.className = 'toast-actions'/);
  assert.match(actionRow, /grid-column:\s*1\s*\/\s*-1/);
  assert.match(actionRow, /grid-row:\s*2/);
  assert.match(actionRow, /justify-content:\s*flex-end/);
  assert.match(actionRule, /border-color:\s*transparent/);
  assert.match(actionRule, /background:\s*transparent/);
  assert.doesNotMatch(actionRule, /border:\s*1px/);
});
