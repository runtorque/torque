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
        "_embeddedTerminal = { focus: function() { _terminalFocusCalls += 1; } };"
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
    rerenderForAgentActivity() {
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
      sandbox._renderTerminalDirectMessages = function() {};
      sandbox._renderTerminalCompose = function() {};
      sandbox._terminalStatusLabel = function() { return 'working'; };
      sandbox._restoreTerminalWorkspaceState = function() {};
      vm.runInContext('renderTerminalWorkspace()', context);
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
