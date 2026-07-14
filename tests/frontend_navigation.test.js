const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function source(file) {
  return fs.readFileSync(path.join(repoRoot, file), 'utf8');
}

function load(context, file) {
  vm.runInContext(source(file), context, { filename: file });
}

test('group navigation dedicates the rail to groups and moves view controls into the grid toolbar', () => {
  const html = source('webview.html');
  const tabs = source('static/js/grid/group-tabs.js');
  const grid = source('static/js/grid/main.js');
  const css = source('static/styles/workspace-grid.css');

  assert.match(html, /id="add-group-header-btn"[\s\S]*title="New group"[\s\S]*aria-label="New group"/);
  assert.match(html, /onclick="openNavigationPalette\('all'\)"[\s\S]*aria-label="Go to"/);
  assert.match(tabs, /agent-group-tab-menu/);
  assert.match(tabs, /agent-group-compact-trigger/);
  assert.match(tabs, /agent-group-quick-search/);
  assert.match(tabs, /_scrollAgentGroupTabs/);
  assert.match(tabs, /agentGroupTabKeydown/);
  assert.doesNotMatch(tabs, /agent-group-tab-actions|agent-view-toggle--tabs/);
  assert.match(grid, /agent-view-toggle--grid/);
  assert.match(grid, /data-agent-view-toggle="grid"/);
  assert.match(grid, /data-agent-view-toggle="canvas"/);
  assert.match(grid, /data-agent-view-toggle="grid" aria-pressed="/);
  assert.match(grid, /data-agent-view-toggle="canvas" aria-pressed="/);
  assert.match(grid, /class="agent-grid-new-icon"[\s\S]*<span>New<\/span>/);
  assert.match(css, /\.agent-grid-new-btn\s*\{[^}]*border:\s*1px solid var\(--border\);[^}]*border-radius:\s*var\(--radius\);[^}]*background:\s*transparent;/s);
  assert.match(css, /\.agent-group-tab\s*\{[^}]*min-height:\s*var\(--control-height-xs\);[^}]*padding:\s*2px var\(--control-padding-x-xs\);[^}]*border-radius:\s*var\(--radius-sm\);[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
  assert.match(css, /container:\s*agent-group-nav\s*\/\s*inline-size/);
  assert.match(css, /@container agent-group-nav \(max-width:\s*380px\)/);
});

test('panel launcher is compact, pinned, searchable, and backed by consistent SVG icons', () => {
  const html = source('webview.html');
  const nav = source('static/js/navigation/panel-launcher.js');
  const palette = source('static/js/navigation/palette.js');
  const css = source('static/styles/workspace-shell.css');

  assert.match(html, /id="panel-nav-more-button"/);
  assert.match(html, /id="panel-nav-more-search"/);
  assert.match(html, /id="panel-nav-more-results"/);
  assert.match(html, /id="modal-navigation-palette"/);
  assert.match(nav, /_panelNavDefaultPins = \['board', 'engineer', 'events', 'health'\]/);
  assert.match(nav, /function panelNavTogglePin/);
  assert.match(nav, /function panelNavMove/);
  assert.match(nav, /function panelNavDragStart/);
  assert.match(palette, /function openNavigationPalette/);
  assert.match(nav, /return '<span class="taskbar-app-icon"[\s\S]*<svg/);
  assert.match(css, /body\.runtime-embedded #panelbar\s*\{[^}]*flex-basis:\s*24px;[^}]*height:\s*24px;/s);
  assert.match(css, /\.standalone-panel-zone-header\s*\{[^}]*gap:\s*6px;[^}]*padding:\s*3px 6px;/s);
  assert.match(css, /\.standalone-panel-tab,[\s\S]*?\.standalone-panel-zone-btn\s*\{[^}]*border:\s*var\(--control-border\);[^}]*font-size:\s*var\(--control-font-size-xs\);[^}]*height:\s*var\(--control-height-sm\);[^}]*padding:\s*0 var\(--control-padding-x-xs\);/s);
  assert.match(css, /\.standalone-panel-tab\s*\{[^}]*border-radius:\s*var\(--radius-sm\);/s);
  assert.match(css, /\.standalone-panel-zone-btn-icon\s*\{[^}]*width:\s*24px;[^}]*height:\s*24px;/s);
  assert.match(css, /\.taskbar-app\.panel-nav-hidden\s*\{\s*display:\s*none !important;/);
  assert.match(css, /@container panel-launcher \(max-width:\s*1180px\)/);
});

test('design foundations expose a shared compact control scale', () => {
  const tokens = source('static/styles/tokens-base.css');

  assert.match(tokens, /--control-height-xs:\s*22px;/);
  assert.match(tokens, /--control-height-sm:\s*24px;/);
  assert.match(tokens, /--control-height-md:\s*28px;/);
  assert.match(tokens, /--control-padding-x-xs:\s*7px;/);
  assert.match(tokens, /--control-padding-x-sm:\s*8px;/);
  assert.match(tokens, /--control-padding-x-md:\s*10px;/);
  assert.match(tokens, /--control-font-size-xs:\s*10px;/);
  assert.match(tokens, /--control-font-size-sm:\s*11px;/);
  assert.match(tokens, /--control-border:\s*1px solid var\(--border\);/);
  assert.match(tokens, /\.hdr-btn\s*\{[^}]*font-size:\s*var\(--control-font-size-xs\);[^}]*border:\s*var\(--control-border\);/s);
  assert.match(tokens, /\.hdr-icon-btn\s*\{[^}]*width:\s*var\(--control-height-sm\);[^}]*height:\s*var\(--control-height-sm\);/s);
});

function createNavigationContext() {
  const storage = new Map();
  const sandbox = {
    console,
    JSON,
    state: {
      runtime: {},
      groups: { Alpha: ['a1', 'a2'], Beta: ['b1'] },
      agents: {
        a1: { id: 'a1', name: 'Architect One', cell_type: 'agent', kind: 'architect', group: 'Alpha', session_id: 's1' },
        a2: { id: 'a2', name: 'Stopped Worker', cell_type: 'agent', kind: 'worker', group: 'Alpha', session_id: '' },
        b1: { id: 'b1', name: 'Terminal', cell_type: 'terminal', group: 'Beta' },
      },
    },
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
    },
    document: {
      getElementById() { return null; },
      querySelector() { return null; },
    },
    window: { innerWidth: 1200, innerHeight: 800 },
    navigator: { platform: 'MacIntel' },
    esc(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    },
    _groupNamesSorted() { return ['Alpha', 'Beta']; },
    _activeGroup() { return 'Alpha'; },
    _standalonePanelsEnabled() { return false; },
    _activePanelApp: 'health',
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  load(context, 'static/js/navigation/panel-launcher.js');
  load(context, 'static/js/navigation/palette.js');
  return { context, storage };
}

test('panel pin preferences persist order while keeping active unpinned panels addressable', () => {
  const { context } = createNavigationContext();
  assert.deepEqual(JSON.parse(JSON.stringify(context._panelNavReadPins())), ['board', 'engineer', 'events', 'health']);

  context.panelNavTogglePin('thinking');
  assert.deepEqual(JSON.parse(JSON.stringify(context._panelNavReadPins())), ['board', 'engineer', 'events', 'health', 'thinking']);

  context.panelNavMove('thinking', -1);
  assert.deepEqual(JSON.parse(JSON.stringify(context._panelNavReadPins())), ['board', 'engineer', 'events', 'thinking', 'health']);

  context.panelNavTogglePin('events');
  assert.deepEqual(JSON.parse(JSON.stringify(context._panelNavReadPins())), ['board', 'engineer', 'thinking', 'health']);
  assert.deepEqual(JSON.parse(JSON.stringify(context._panelNavVisibleApps())), ['health']);
});

test('Go To index includes every group, stopped agents, and all panels', () => {
  const { context } = createNavigationContext();
  const items = JSON.parse(JSON.stringify(context._navigationPaletteBuildItems('all')));
  assert.ok(items.some((item) => item.id === 'group:Alpha' && item.active));
  assert.ok(items.some((item) => item.id === 'agent:a2' && /stopped/.test(item.meta)));
  assert.equal(items.some((item) => item.id === 'agent:b1'), false, 'terminals are not duplicated in the agent index');
  assert.ok(items.some((item) => item.id === 'panel:board'));
  assert.ok(items.some((item) => item.id === 'panel:health' && item.active));
});

test('keyboard registry exposes Go To, group, and panel switchers on both platform modifiers', () => {
  const calls = [];
  const sandbox = {
    console,
    JSON,
    state: { global_settings: { keybindings: {} } },
    openNavigationPalette(scope) { calls.push(['all', scope]); return true; },
    openGroupNavigator() { calls.push(['groups']); return true; },
    openPanelNavigator() { calls.push(['panels']); return true; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  load(context, 'static/js/keybindings.js');

  function event(key, opts = {}) {
    return {
      key,
      metaKey: !!opts.meta,
      ctrlKey: !!opts.ctrl,
      altKey: false,
      shiftKey: false,
      preventDefault() { this.defaultPrevented = true; },
    };
  }

  const goTo = event('k', { meta: true });
  const groups = event('g', { ctrl: true });
  const panels = event('p', { meta: true });
  assert.equal(context.dispatchKeybindingEvent(goTo), true);
  assert.equal(context.dispatchKeybindingEvent(groups), true);
  assert.equal(context.dispatchKeybindingEvent(panels), true);
  assert.deepEqual(calls, [['all', 'all'], ['groups'], ['panels']]);
  assert.equal(goTo.defaultPrevented, true);
  assert.equal(groups.defaultPrevented, true);
  assert.equal(panels.defaultPrevented, true);
});
