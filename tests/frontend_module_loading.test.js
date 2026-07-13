const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  repoRoot,
  webviewScriptSources,
  webviewStylesheetSources,
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

test('webview stylesheet modules are unique, ordered, and exist on disk', () => {
  const sources = webviewStylesheetSources();
  const appStyles = sources.filter((source) => source.startsWith('static/styles/'));
  assert.deepEqual(appStyles, [
    'static/styles/tokens-base.css',
    'static/styles/workspace-grid.css',
    'static/styles/modals.css',
    'static/styles/workspace-shell.css',
    'static/styles/board-panels.css',
    'static/styles/agent-panel.css',
    'static/styles/desktop-features.css',
    'static/styles/feature-panels.css',
  ]);
  assert.equal(new Set(sources).size, sources.length, 'stylesheet sources must not be duplicated');
  for (const source of sources) {
    assert.equal(fs.existsSync(path.join(repoRoot, source)), true, `missing stylesheet: ${source}`);
  }
  const compatibilityCss = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  const compatibilityImports = Array.from(
    compatibilityCss.matchAll(/@import url\("\.\/styles\/([^"?]+)"\);/g),
    (match) => `static/styles/${match[1]}`,
  );
  assert.deepEqual(compatibilityImports, appStyles, 'compatibility imports must mirror runtime cascade order');
});

test('frontend load order preserves state, rendering, panels, and boot boundaries', () => {
  const sources = webviewScriptSources();
  const ws = indexOf(sources, 'static/js/ws.js');
  const wsInteraction = indexOf(sources, 'static/js/ws/interaction-guard.js');
  const wsFullState = indexOf(sources, 'static/js/ws/full-state.js');
  const wsInvalidation = indexOf(sources, 'static/js/ws/invalidation.js');
  const wsRegistry = indexOf(sources, 'static/js/ws/delta-registry.js');
  const wsDeltaApply = indexOf(sources, 'static/js/ws/delta-apply.js');
  const wsActionRouter = indexOf(sources, 'static/js/ws/action-router.js');
  const render = indexOf(sources, 'static/js/render.js');
  const terminal = indexOf(sources, 'static/js/terminal.js');
  const terminalDirectMessages = indexOf(sources, 'static/js/terminal/direct-messages.js');
  const terminalComposer = indexOf(sources, 'static/js/terminal/composer.js');
  const terminalAttachments = indexOf(sources, 'static/js/terminal/composer-attachments.js');
  const terminalXterm = indexOf(sources, 'static/js/terminal/xterm-runtime.js');
  const modalCore = indexOf(sources, 'static/js/modals/core.js');
  const modalSettingsShell = indexOf(sources, 'static/js/modals/settings-shell.js');
  const modals = indexOf(sources, 'static/js/modals.js');
  const modalTask = indexOf(sources, 'static/js/modals/task-modal.js');
  const modalGroupSettings = indexOf(sources, 'static/js/modals/group-settings.js');
  const modalWorktrees = indexOf(sources, 'static/js/modals/worktrees.js');
  const modalGlobalSettings = indexOf(sources, 'static/js/modals/global-settings.js');
  const modalSchedules = indexOf(sources, 'static/js/modals/schedules.js');
  const board = indexOf(sources, 'static/js/board.js');
  const boardViewState = indexOf(sources, 'static/js/board/view-state.js');
  const boardCardRendering = indexOf(sources, 'static/js/board/card-rendering.js');
  const boardCardActions = indexOf(sources, 'static/js/board/card-actions.js');
  const boardModel = indexOf(sources, 'static/js/board/model.js');
  const boardRendering = indexOf(sources, 'static/js/board/rendering.js');
  const boardInlineCreate = indexOf(sources, 'static/js/board/inline-create.js');
  const boardSelection = indexOf(sources, 'static/js/board/selection.js');
  const boardExternalSync = indexOf(sources, 'static/js/board/external-sync.js');
  const boardDragDrop = indexOf(sources, 'static/js/board/drag-drop.js');
  const boardFilters = indexOf(sources, 'static/js/board/filters.js');
  const boardSchedules = indexOf(sources, 'static/js/board/schedules.js');
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
  const panelNavigation = indexOf(sources, 'static/js/navigation/panel-launcher.js');
  const navigationPalette = indexOf(sources, 'static/js/navigation/palette.js');
  const main = indexOf(sources, 'static/js/main.js');

  assert.ok(ws < wsInteraction, 'canonical state must load before WS feature modules');
  assert.ok(wsInteraction < wsFullState, 'interaction guard must load before hydration');
  assert.ok(wsFullState < wsInvalidation, 'full-state hydration must load before delta invalidation');
  assert.ok(wsInvalidation < wsRegistry, 'delta helpers must load before registry initialization');
  assert.ok(wsRegistry < wsDeltaApply, 'delta registry must load before state application');
  assert.ok(wsDeltaApply < wsActionRouter, 'delta state application must load before message routing');
  assert.ok(wsActionRouter < render, 'all canonical WS modules must load before renderers');
  assert.ok(render < terminal, 'shared render helpers must load before terminal UI');
  assert.ok(terminal < terminalDirectMessages, 'Terminal core must load before feature modules');
  assert.ok(terminalDirectMessages < terminalComposer, 'DM UI must load before the composer');
  assert.ok(terminalComposer < terminalAttachments, 'composer primitives must load before attachments');
  assert.ok(terminalAttachments < terminalXterm, 'composer modules must load before xterm runtime');
  assert.ok(terminalXterm < modalCore, 'terminal globals must exist before modal composition');
  assert.ok(modalCore < modalSettingsShell, 'modal framework must load before the Settings shell');
  assert.ok(modalSettingsShell < modals, 'Settings shell must load before domain form modules');
  assert.ok(modals < modalTask, 'agent-management state must load before task forms');
  assert.ok(modalTask < modalGroupSettings, 'task forms must load before settings forms');
  assert.ok(modalGroupSettings < modalWorktrees, 'Group Settings must load before worktree history');
  assert.ok(modalWorktrees < modalGlobalSettings, 'worktree helpers must load before Global Settings');
  assert.ok(modalGlobalSettings < modalSchedules, 'Global Settings must load before schedule forms');
  assert.ok(modalSchedules < board, 'modal globals must exist before Board panel boot');
  assert.ok(board < boardViewState, 'Board core must load before feature modules');
  assert.ok(boardViewState < boardCardRendering, 'Board view state must load before card rendering');
  assert.ok(boardCardRendering < boardCardActions, 'card rendering must load before card actions');
  assert.ok(boardCardActions < boardModel, 'card modules must load before the Board model');
  assert.ok(boardModel < boardRendering, 'Board model must load before panel rendering');
  assert.ok(boardRendering < boardInlineCreate, 'Board rendering must load before inline controls');
  assert.ok(boardInlineCreate < boardSelection, 'inline creation must load before selection tools');
  assert.ok(boardSelection < boardExternalSync, 'selection tools must load before external actions');
  assert.ok(boardExternalSync < boardDragDrop, 'task actions must load before drag and drop');
  assert.ok(boardDragDrop < boardFilters, 'drag and drop must load before Board filters');
  assert.ok(boardFilters < boardSchedules, 'Board filters must load before schedules');
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
  assert.ok(panelManager < panelNavigation, 'panel placement must load before compact navigation');
  assert.ok(panelNavigation < navigationPalette, 'panel metadata must load before the Go To palette');
  assert.ok(navigationPalette < main, 'compact navigation must initialize before the main boot script');
  assert.equal(main, sources.length - 1, 'main.js must remain the final boot script');
});
