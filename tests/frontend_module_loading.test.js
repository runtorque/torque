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
  const board = indexOf(sources, 'static/js/board.js');
  const behavior = indexOf(sources, 'static/js/behavior_overlay.js');
  const agentPanel = indexOf(sources, 'static/js/agent_panel.js');
  const agentPanelClasses = indexOf(sources, 'static/js/agent-panel/classes.js');
  const panelManager = indexOf(sources, 'static/js/panel_manager.js');
  const main = indexOf(sources, 'static/js/main.js');

  assert.ok(ws < render, 'canonical state must load before renderers');
  assert.ok(render < terminal, 'shared render helpers must load before terminal UI');
  assert.ok(terminal < board, 'terminal globals must exist before panel boot');
  assert.ok(board < behavior, 'feature panels load before agent panel composition');
  assert.ok(behavior < agentPanel, 'Behavior renderer must load before Agent panel');
  assert.ok(agentPanel < agentPanelClasses, 'Agent panel core must load before feature modules');
  assert.ok(agentPanelClasses < panelManager, 'Agent panel modules must load before panel manager');
  assert.equal(main, sources.length - 1, 'main.js must remain the final boot script');
});
