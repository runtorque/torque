/* Regression coverage for first-mount terminal focus and the DM composer.
 *
 * An active agent can cause a terminal/workspace refresh while its PTY socket
 * is completing its initial open + snapshot.  That produces two automatic
 * focus requests in quick succession.  The second request used to run on the
 * next animation frame even if the operator had focused another control in
 * the meantime, so xterm's hidden input took keyboard focus away from that
 * control.  The DM composer is in the same workspace and was blamed for the
 * visible symptom.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadScript(context, relPath) {
  vm.runInContext(fs.readFileSync(path.join(repoRoot, relPath), 'utf8'), context, {
    filename: relPath,
  });
}

function buildHarness() {
  const frames = [];
  const body = { tagName: 'BODY' };
  const root = {
    classList: { add() {}, remove() {} },
    contains(node) { return node && node.inTerminalWorkspace === true; },
  };
  const dom = {
    shell: {},
    topbar: { _torqueLastHtml: '', innerHTML: '' },
    tabs: null,
    stage: { _torqueLastHtml: null },
    directMessages: {},
    compose: {},
    statusbar: { textContent: '', title: '' },
  };
  const sandbox = {
    console,
    state: {},
    document: {
      activeElement: body,
      body,
      getElementById(id) {
        if (id === 'terminal-workspace') return root;
        return null;
      },
      querySelector() { return null; },
    },
    requestAnimationFrame(callback) { frames.push(callback); return frames.length; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/terminal.js');
  sandbox.isEmbeddedTerminalMode = function() { return true; };
  return {
    context,
    sandbox,
    requestFirstMountFocus(focus) {
      sandbox._terminalFocusCalls = 0;
      vm.runInContext(
        "_embeddedTerminal = { focus: function() {"
          + '_terminalFocusCalls += 1;'
          + "if (typeof _onTerminalFocus === 'function') _onTerminalFocus();"
          + '} };'
          + "_embeddedTerminalSessionKey = 'agent-1:session-1';"
          + "_embeddedTerminalPendingFocusKey = 'agent-1:session-1';"
          + 'focusEmbeddedTerminalWorkspace(false);',
        context,
      );
      if (focus) sandbox.document.activeElement = focus;
    },
    flushFrames() {
      while (frames.length) frames.shift()();
    },
    focusCalls() { return sandbox._terminalFocusCalls || 0; },
    rerenderForAgentActivity(component) {
      // The real delta renderer calls this path when an agent_upsert affects
      // the currently viewed terminal. Stub only its DOM collaborators so the
      // production renderTerminalWorkspace function itself runs.
      const cell = { id: 'agent-1', name: 'Agent 1', session_id: 'session-1' };
      sandbox._terminalComposePersistFromDom = function() {};
      sandbox._detachedWindowActive = function() { return false; };
      sandbox._pruneEmbeddedTerminalSessions = function() {};
      sandbox._terminalCurrentGroupName = function() { return ''; };
      sandbox._terminalGroupCells = function() { return []; };
      sandbox._resolveTerminalWorkspaceCell = function() { return cell; };
      sandbox._terminalTargetAgent = function() { return null; };
      sandbox._terminalDisplayPath = function() { return ''; };
      sandbox._ensureTerminalWorkspaceDom = function() { return dom; };
      sandbox._captureTerminalWorkspaceState = function() { return null; };
      sandbox._terminalWorkspaceFocusedComposeHasDraft = function() { return false; };
      sandbox.esc = function(value) { return String(value || ''); };
      sandbox._findEmbeddedTerminalEntryForCell = function() { return null; };
      sandbox._createEmbeddedTerminalSurface = function() { return {}; };
      sandbox._connectEmbeddedTerminal = function() {};
      sandbox._activateEmbeddedTerminalSurface = function() {};
      sandbox._directMessagesRenderCalls = 0;
      sandbox._composerRenderCalls = 0;
      const composerIdentity = {};
      const directMessagesIdentity = {};
      const liveState = {
        composerIdentity,
        composerFocus: true,
        composerDraft: 'draft under streamed output',
        composerCaret: [7, 15],
        directMessagesIdentity,
        directMessagesScrollTop: 240,
        directMessagesPinnedToTail: false,
      };
      sandbox._renderTerminalDirectMessages = function() {
        sandbox._directMessagesRenderCalls += 1;
        liveState.directMessagesIdentity = {};
        liveState.directMessagesScrollTop = 0;
        liveState.directMessagesPinnedToTail = true;
      };
      sandbox._renderTerminalCompose = function() {
        sandbox._composerRenderCalls += 1;
        liveState.composerIdentity = {};
        liveState.composerFocus = false;
        liveState.composerDraft = '';
        liveState.composerCaret = [0, 0];
      };
      sandbox._terminalStatusLabel = function() { return 'working'; };
      sandbox._restoreTerminalWorkspaceState = function() {};
      sandbox._restoreTerminalWorkspaceTerminalState = function() {};
      sandbox._renderCycleComponent = component || '';
      if (component) dom.shell._torqueRenderedCellId = 'agent-1';
      vm.runInContext(
        '_renderCycleComponent'
          + " ? renderTerminalWorkspace({ component: _renderCycleComponent })"
          + ' : renderTerminalWorkspace()',
        context,
      );
      return {
        directMessages: sandbox._directMessagesRenderCalls,
        composer: sandbox._composerRenderCalls,
        liveState,
        composerIdentity,
        directMessagesIdentity,
      };
    },
  };
}

test('first terminal mount still focuses when the operator has not selected another control', () => {
  const h = buildHarness();
  h.requestFirstMountFocus();
  h.flushFrames();

  assert.equal(h.focusCalls(), 1,
    'first mount remains a deliberate terminal/composer-open focus path');
});

test('DM-driven render does not enter the composer render cycle', () => {
  const h = buildHarness();
  const calls = h.rerenderForAgentActivity('direct-messages');

  assert.equal(calls.directMessages, 1, 'the direct-message list is refreshed');
  assert.equal(calls.composer, 0,
    'new messages must preserve the composer DOM, focus, caret, draft, and selection');
  assert.equal(calls.liveState.composerIdentity, calls.composerIdentity);
  assert.equal(calls.liveState.composerFocus, true);
  assert.equal(calls.liveState.composerDraft, 'draft under streamed output');
  assert.deepEqual(calls.liveState.composerCaret, [7, 15]);
});

test('terminal-driven render does not enter the direct-message render cycle', () => {
  const h = buildHarness();
  const calls = h.rerenderForAgentActivity('terminal');

  assert.equal(calls.directMessages, 0,
    'streaming terminal output must preserve the DM list DOM and viewport state');
  assert.equal(calls.composer, 0,
    'streaming terminal output must preserve the composer DOM and live editing state');
  assert.equal(calls.liveState.directMessagesIdentity, calls.directMessagesIdentity);
  assert.equal(calls.liveState.directMessagesScrollTop, 240);
  assert.equal(calls.liveState.directMessagesPinnedToTail, false);
  assert.equal(calls.liveState.composerIdentity, calls.composerIdentity);
  assert.equal(calls.liveState.composerFocus, true);
  assert.equal(calls.liveState.composerDraft, 'draft under streamed output');
  assert.deepEqual(calls.liveState.composerCaret, [7, 15]);
});

test('a falsy desktop active element cannot license a queued first-mount focus steal', () => {
  const h = buildHarness();
  const messageControl = { type: 'desktop-message-box' };

  // WKWebView can report no active DOM element while the terminal socket
  // schedules its first-mount focus even though the desktop focus owner is the
  // message box. That unknown schedule-time state must not act as permission
  // for xterm to reclaim the desktop keyboard focus on the animation frame.
  h.sandbox._desktopFocusOwner = messageControl;
  h.sandbox._onTerminalFocus = function() {
    h.sandbox._desktopFocusOwner = 'terminal';
  };
  h.sandbox.document.activeElement = null;
  h.requestFirstMountFocus();
  h.flushFrames();

  assert.equal(h.focusCalls(), 0,
    'an unknown schedule-time focus owner must cancel automatic terminal focus');
  assert.equal(h.sandbox._desktopFocusOwner, messageControl,
    'the desktop message box retains keyboard focus');
});

test('agent-activity rerender cannot let a queued first-mount focus steal an unrelated control', () => {
  const h = buildHarness();
  const unrelatedControl = {
    tagName: 'BUTTON',
    selectionStart: 2,
    selectionEnd: 5,
    scrollTop: 41,
  };

  // Socket open requests the allowed first-mount focus. Before its animation
  // frame runs, an active-agent update refreshes the workspace and the
  // operator selects an unrelated control. This is the race that previously
  // moved focus into xterm (and appeared as the adjacent DM composer doing it).
  h.requestFirstMountFocus(unrelatedControl);
  h.rerenderForAgentActivity();
  h.flushFrames();

  assert.equal(h.focusCalls(), 0,
    'an automatic focus request must not reclaim focus after a rerender-time operator action');
  assert.equal(h.sandbox.document.activeElement, unrelatedControl,
    'the unrelated control retains keyboard focus');
  assert.equal(unrelatedControl.selectionStart, 2,
    'the unrelated control retains its selection start');
  assert.equal(unrelatedControl.selectionEnd, 5,
    'the unrelated control retains its selection end');
  assert.equal(unrelatedControl.scrollTop, 41,
    'the unrelated control retains its scroll state');
});
