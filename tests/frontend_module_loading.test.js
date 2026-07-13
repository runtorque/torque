const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  repoRoot,
  webviewScriptSources,
} = require('./frontend_script_loader');

function indexOf(sources, source) {
  const index = sources.indexOf(source);
  assert.notEqual(index, -1, `${source} must be loaded by webview.html`);
  return index;
}

test('webview frontend scripts are unique and exist on disk', () => {
  const sources = webviewScriptSources();
  assert.ok(sources.length > 20, 'expected the complete no-build frontend manifest');
  assert.equal(new Set(sources).size, sources.length, 'script sources must not be duplicated');
  for (const source of sources) {
    assert.equal(
      fs.existsSync(path.join(repoRoot, source)),
      true,
      `missing frontend script: ${source}`,
    );
  }
});

test('frontend load order preserves state, rendering, panels, and boot boundaries', () => {
  const sources = webviewScriptSources();
  const ws = indexOf(sources, 'static/js/ws.js');
  const render = indexOf(sources, 'static/js/render.js');
  const terminal = indexOf(sources, 'static/js/terminal.js');
  const terminalDirectMessages = indexOf(sources, 'static/js/terminal/direct-messages.js');
  const terminalComposer = indexOf(sources, 'static/js/terminal/composer.js');
  const terminalAttachments = indexOf(sources, 'static/js/terminal/composer-attachments.js');
  const terminalXterm = indexOf(sources, 'static/js/terminal/xterm-runtime.js');
  const board = indexOf(sources, 'static/js/board.js');
  const behavior = indexOf(sources, 'static/js/behavior_overlay.js');
  const agentPanel = indexOf(sources, 'static/js/agent_panel.js');
  const agentPanelVirtualLists = indexOf(sources, 'static/js/agent-panel/virtual-lists.js');
  const agentPanelEvents = indexOf(sources, 'static/js/agent-panel/events.js');
  const agentPanelArchitect = indexOf(sources, 'static/js/agent-panel/architect.js');
  const agentPanelEngineer = indexOf(sources, 'static/js/agent-panel/engineer.js');
  const agentPanelWorker = indexOf(sources, 'static/js/agent-panel/worker.js');
  const agentPanelHierarchy = indexOf(sources, 'static/js/agent-panel/hierarchy.js');
  const agentPanelLegacyEngineer = indexOf(sources, 'static/js/agent-panel/legacy-engineer.js');
  const agentPanelClasses = indexOf(sources, 'static/js/agent-panel/classes.js');
  const panelManager = indexOf(sources, 'static/js/panel_manager.js');
  const main = indexOf(sources, 'static/js/main.js');

  assert.ok(ws < render, 'canonical state must load before renderers');
  assert.ok(render < terminal, 'shared render helpers must load before terminal UI');
  assert.ok(terminal < terminalDirectMessages, 'Terminal core must load before feature modules');
  assert.ok(terminalDirectMessages < terminalComposer, 'DM UI must load before the composer');
  assert.ok(terminalComposer < terminalAttachments, 'composer primitives must load before attachments');
  assert.ok(terminalAttachments < terminalXterm, 'composer modules must load before xterm runtime');
  assert.ok(terminalXterm < board, 'terminal globals must exist before panel boot');
  assert.ok(board < behavior, 'feature panels load before agent panel composition');
  assert.ok(behavior < agentPanel, 'Behavior renderer must load before Agent panel');
  assert.ok(agentPanel < agentPanelVirtualLists, 'Agent panel core must load before feature modules');
  assert.ok(agentPanelVirtualLists < agentPanelEvents, 'virtual-list helpers must load before event rendering');
  assert.ok(agentPanelEvents < agentPanelArchitect, 'event rendering must load before role renderers');
  assert.ok(agentPanelArchitect < agentPanelEngineer, 'role modules retain documented order');
  assert.ok(agentPanelEngineer < agentPanelWorker, 'role modules retain documented order');
  assert.ok(agentPanelWorker < agentPanelHierarchy, 'role modules must load before shared hierarchy UI');
  assert.ok(agentPanelHierarchy < agentPanelLegacyEngineer, 'hierarchy helpers must load before legacy group UI');
  assert.ok(agentPanelLegacyEngineer < agentPanelClasses, 'legacy support must load before class management');
  assert.ok(agentPanelClasses < panelManager, 'Agent panel modules must load before panel manager');
  assert.equal(main, sources.length - 1, 'main.js must remain the final boot script');
});
