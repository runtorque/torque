const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function source(file) {
  return fs.readFileSync(path.join(repoRoot, file), 'utf8');
}

test('shared content states define semantic, placement, hierarchy, and motion variants', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-state\s*\{[^}]*display:\s*grid;[^}]*min-height:\s*96px;[^}]*border:\s*1px dashed var\(--border\);[^}]*border-radius:\s*var\(--radius-lg\);[^}]*background:/s);
  assert.match(css, /\.ui-state--empty\s*\{[^}]*border-style:\s*dashed;/s);
  assert.match(css, /\.ui-state--compact\s*\{[^}]*min-height:\s*0;[^}]*padding:\s*var\(--space-2\) var\(--space-3\);/s);
  assert.match(css, /\.ui-state--inline\s*\{[^}]*display:\s*inline-flex;[^}]*border:\s*0;[^}]*background:\s*transparent;/s);
  assert.match(css, /\.ui-state--fill\s*\{[^}]*width:\s*100%;[^}]*height:\s*100%;/s);
  assert.match(css, /\.ui-state--note\s*\{[^}]*justify-items:\s*start;[^}]*border-style:\s*solid;/s);
  assert.match(css, /\.ui-state--loading\s*\{[^}]*border-style:\s*solid;[^}]*color:\s*var\(--accent\);/s);
  assert.match(css, /\.ui-state--loading::before\s*\{[^}]*border-radius:\s*50%;[^}]*animation:\s*ui-state-spin 700ms linear infinite;/s);
  assert.match(css, /\.ui-state--error\s*\{[^}]*border-color:[^}]*var\(--red\)[^}]*background:/s);
  assert.match(css, /\.ui-state__title\s*\{[^}]*font-weight:\s*700;/s);
  assert.match(css, /\.ui-state__actions\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap;/s);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.ui-state--loading::before\s*\{\s*animation:\s*none;/s);
});

test('native scrollbars share canonical thumb and hover colors', () => {
  const tokens = source('static/styles/tokens-base.css');
  const board = source('static/styles/board-panels.css');
  const grid = source('static/styles/workspace-grid.css');
  const shell = source('static/styles/workspace-shell.css');
  const features = source('static/styles/feature-panels.css');

  assert.match(tokens, /--scrollbar-thumb:\s*var\(--border\);/);
  assert.match(tokens, /--scrollbar-thumb-hover:\s*var\(--border-strong\);/);
  assert.match(tokens, /--scrollbar-size:\s*5px;/);
  assert.match(tokens, /--scrollbar-size-compact:\s*4px;/);
  assert.match(tokens, /--scrollbar-hit-size-nav:\s*8px;/);
  assert.match(board, /html\s*\{[^}]*scrollbar-color:\s*auto;[^}]*scrollbar-width:\s*auto;/s);
  assert.match(board, /::\-webkit-scrollbar\s*\{[^}]*width:\s*var\(--scrollbar-size\);[^}]*height:\s*var\(--scrollbar-size\);/s);
  assert.match(board, /::\-webkit-scrollbar-thumb\s*\{[^}]*background:\s*var\(--scrollbar-thumb\);/s);
  assert.match(board, /::\-webkit-scrollbar-thumb:hover\s*\{[^}]*background:\s*var\(--scrollbar-thumb-hover\);/s);
  assert.match(grid, /\.agent-group-tabs-list\s*\{[^}]*scrollbar-color:\s*auto;/s);
  assert.match(shell, /\.standalone-panel-zone-tabs\s*\{[^}]*scrollbar-color:\s*auto;/s);
  assert.match(features, /\.torque-markdown \.torque-md-code-block\s*\{[^}]*scrollbar-color:\s*auto;/s);
});

test('operator-facing content states opt into the canonical API and announcement semantics', () => {
  const board = source('static/js/board/model.js');
  const grid = source('static/js/grid/main.js');
  const terminal = source('static/js/terminal.js');
  const help = source('static/js/help.js');
  const mission = source('static/js/mission_control.js');
  const initiatives = source('static/js/initiatives.js');
  const thinking = source('static/js/thinking.js');
  const agent = source('static/js/agent_panel.js');
  const agentEvents = source('static/js/agent-panel/events.js');
  const diff = source('static/js/diff.js');
  const history = source('static/js/taskhistory.js');
  const artifacts = source('static/js/modals/task-artifacts.js');
  const settings = source('static/js/modals/group-settings.js');
  const navigation = source('static/js/navigation/palette.js');

  assert.match(board, /board-empty ui-state ui-state--empty/);
  assert.match(grid, /empty ui-state ui-state--empty ui-state--fill/);
  assert.match(grid, /ui-state__title">No groups yet/);
  assert.match(grid, /ui-state__actions.*?btn-secondary btn-sm/s);
  assert.match(terminal, /terminal-empty ui-state ui-state--empty ui-state--fill/);
  assert.match(help, /ui-state--loading.*?role="status" aria-live="polite"/s);
  assert.match(help, /ui-state--error.*?role="alert"/s);
  assert.match(mission, /mc-error ui-state ui-state--error/);
  assert.match(initiatives, /initiative-loading ui-state ui-state--loading/);
  assert.match(thinking, /thinking-error ui-state ui-state--error ui-state--compact/);
  assert.match(agent, /agent-panel-empty ui-state ui-state--empty ui-state--fill/);
  assert.match(agentEvents, /ui-state--loading ui-state--compact" role="status" aria-live="polite"/);
  assert.match(diff, /diff-empty ui-state ui-state--error ui-state--fill" role="alert"/);
  assert.match(history, /th-empty ui-state ui-state--loading ui-state--fill" role="status"/);
  assert.match(artifacts, /artifact-preview-loading ui-state ui-state--loading" role="status"/);
  assert.match(settings, /settings-map-error ui-state ui-state--error ui-state--compact/);
  assert.match(settings, /setAttribute\('role', 'alert'\)/);
  assert.match(navigation, /navigation-palette-empty ui-state ui-state--empty ui-state--compact/);
});

test('feature styles no longer rebuild canonical content-state geometry or intent', () => {
  const tokens = source('static/styles/tokens-base.css');
  const grid = source('static/styles/workspace-grid.css');
  const board = source('static/styles/board-panels.css');
  const agent = source('static/styles/agent-panel.css');
  const modals = source('static/styles/modals.css');
  const features = source('static/styles/feature-panels.css');

  assert.doesNotMatch(tokens, /^\.empty(?:-action|-icon)?\s*\{/m);
  assert.doesNotMatch(grid, /^\.terminal-empty\s*\{[^}]*(?:padding|border|border-radius|background|color):/ms);
  assert.doesNotMatch(board, /^\.(?:board-empty|artifact-empty)\s*\{[^}]*(?:padding|border|border-radius|background|color):/ms);
  assert.doesNotMatch(agent, /^\.agent-panel-(?:empty|event-empty)\s*\{[^}]*(?:padding|border|border-radius|background|color):/ms);
  assert.doesNotMatch(agent, /^\.th-empty\s*\{[^}]*(?:padding|border|border-radius|background|color):/ms);
  assert.doesNotMatch(modals, /^\.settings-map-empty\s*\{[^}]*(?:padding|border|border-radius|background|color):/ms);
  assert.doesNotMatch(features, /^\.(?:initiative|thinking)-(?:empty|loading|error)\s*\{[^}]*(?:padding|border|border-radius|background|color):/ms);
});

test('shared form controls define default, compact, and invalid states', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.form-control,\s*:where\(input:not\([\s\S]*?select,\s*textarea\s*\{[^}]*border:\s*var\(--control-border\);[^}]*border-radius:\s*var\(--radius\);[^}]*background:\s*var\(--bg-inset\);[^}]*font-size:\s*var\(--control-font-size-sm\);/s);
  assert.match(css, /\.form-control:not\(textarea\),\s*:where\(input:not\([\s\S]*?select\s*\{[^}]*min-height:\s*var\(--control-height-md\);[^}]*padding:\s*0 var\(--control-padding-x-sm\);/s);
  assert.match(css, /textarea\.form-control,\s*textarea\s*\{[^}]*min-height:\s*48px;[^}]*padding:\s*6px var\(--control-padding-x-sm\);[^}]*resize:\s*vertical;/s);
  assert.match(css, /\.form-control-sm,[\s\S]*?\.form-control-group-sm textarea\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
  assert.match(css, /\.form-control\.is-invalid,[\s\S]*?textarea\[aria-invalid="true"\]\s*\{[^}]*border-color:\s*var\(--red\);/s);
  assert.match(css, /\.form-error\s*\{[^}]*color:\s*var\(--red\);[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
});

test('form primitives live in the shared component stylesheet', () => {
  const tokens = source('static/styles/tokens-base.css');
  const modals = source('static/styles/modals.css');
  const features = source('static/styles/feature-panels.css');
  const agent = source('static/styles/agent-panel.css');
  const actions = source('static/js/actions.js');
  const templates = source('static/js/templates.js');
  const events = source('static/js/events.js');
  const agentEvents = source('static/js/agent-panel/events.js');
  const thinkingRule = features.match(/\.thinking-form input,\s*\.thinking-form select,\s*\.thinking-form textarea\s*\{([^}]*)\}/s);
  const agentFilterRule = agent.match(/\.agent-panel-mcp-filters input,\s*\.agent-panel-mcp-filters select\s*\{([^}]*)\}/s);

  assert.doesNotMatch(tokens, /^input, select, textarea\s*\{/m);
  assert.doesNotMatch(modals, /^textarea\s*\{/m);
  assert.doesNotMatch(features, /^\.initiative-form input,\s*$/m);
  assert.ok(thinkingRule, 'Thinking retains only its local min-width layout rule');
  assert.doesNotMatch(thinkingRule[1], /border|background|padding|font-size/);
  assert.ok(agentFilterRule, 'Agent filters retain only surface layout and background');
  assert.doesNotMatch(agentFilterRule[1], /border|padding|font-size/);
  assert.match(actions, /tpled-form form-control-group-sm/);
  assert.match(templates, /tpled-form form-control-group-sm/);
  assert.match(events, /form-control-sm events-resolve-textarea/);
  assert.match(agentEvents, /agent-panel-mcp-filters form-control-group-sm/);
  assert.doesNotMatch(source('static/styles/components.css'), /\.tpled-form (?:input|select|textarea)/);
});

test('shared button component defines canonical intents and sizes', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.btn,\s*\.btn-primary,[\s\S]*?\.btn-warning\s*\{[^}]*min-height:\s*var\(--control-height-md\);[^}]*padding:\s*0 var\(--control-padding-x-md\);[^}]*border-radius:\s*var\(--radius-sm\);/s);
  assert.match(css, /\.btn,\s*\.btn-secondary\s*\{[^}]*background:\s*var\(--bg-cell\);[^}]*border-color:\s*var\(--border\);/s);
  assert.match(css, /\.btn-primary\s*\{[^}]*background:\s*var\(--accent\);[^}]*border-color:\s*var\(--accent\);/s);
  assert.match(css, /\.btn-quiet,\s*\.btn-cancel\s*\{[^}]*background:\s*transparent;[^}]*border-color:\s*transparent;/s);
  assert.match(css, /\.btn-danger\s*\{[^}]*background:\s*var\(--red\);[^}]*border-color:\s*var\(--red\);/s);
  assert.match(css, /\.btn-success\s*\{[^}]*background:\s*var\(--green\);[^}]*border-color:\s*var\(--green\);/s);
  assert.match(css, /\.btn-warning\s*\{[^}]*color:\s*var\(--amber\);/s);
  assert.doesNotMatch(css, /\.btn-(?:green|rebase)(?:,|\s*\{)/);
  assert.match(css, /\.btn-sm\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*padding:\s*0 var\(--control-padding-x-sm\);/s);
  assert.match(css, /\.btn-xs\s*\{[^}]*min-height:\s*var\(--control-height-xs\);[^}]*padding:\s*0 var\(--control-padding-x-xs\);/s);
});

test('button primitives live only in the shared component stylesheet', () => {
  const modals = source('static/styles/modals.css');
  const board = source('static/styles/board-panels.css');

  assert.doesNotMatch(modals, /^\.btn-(primary|secondary|cancel|danger|green|link)\s*\{/m);
  assert.doesNotMatch(board, /^\.btn-(sm|xs|rebase)\s*\{/m);
});

test('shared navigation tabs define contained and underline variants', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-tab--contained\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*padding:\s*0 var\(--control-padding-x-sm\);[^}]*border-radius:\s*var\(--radius-sm\);/s);
  assert.match(css, /\.ui-tab--contained\.active,\s*\.ui-tab--contained\[aria-selected="true"\]\s*\{[^}]*border-color:\s*var\(--accent\);[^}]*background:\s*var\(--accent-soft\);/s);
  assert.match(css, /\.ui-tab--underline\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*border-bottom:\s*2px solid transparent;[^}]*border-radius:\s*0;/s);
  assert.match(css, /\.ui-tab--underline\.active,\s*\.ui-tab--underline\[aria-selected="true"\]\s*\{[^}]*color:\s*var\(--accent\);[^}]*border-bottom-color:\s*var\(--accent\);/s);
  assert.match(css, /\.ui-tabs--contained\s*\{[^}]*border-radius:\s*var\(--radius-sm\);/s);
});

test('feature stylesheets do not redefine shared tab visual primitives', () => {
  const modals = source('static/styles/modals.css');
  const features = source('static/styles/feature-panels.css');
  const agent = source('static/styles/agent-panel.css');
  const board = source('static/styles/board-panels.css');
  const initiatives = source('static/js/initiatives.js');
  const thinking = source('static/js/thinking.js');
  const agentPanel = source('static/js/agent_panel.js');
  const agentEvents = source('static/js/agent-panel/events.js');
  const boardRender = source('static/js/board/rendering.js');
  const html = source('webview.html');

  assert.doesNotMatch(features, /^\.(planning|thinking)-tab\s*\{/m);
  assert.doesNotMatch(agent, /^\.agent-panel-events-subtab\s*\{/m);
  assert.doesNotMatch(modals, /^\.gs-subtab\s*\{/m);
  assert.doesNotMatch(board, /^\.board-lane-tab\s*\{[^}]*border-bottom:/ms);
  assert.doesNotMatch(features, /^\.(planning|thinking)-tab[^\{]*\{[^}]*border-radius:\s*999px/ms);
  assert.doesNotMatch(agent, /^\.agent-panel-events-subtabs?[^\{]*\{[^}]*border-radius:\s*999px/ms);
  assert.doesNotMatch(source('static/styles/components.css'), /\.(?:planning-tab|thinking-tab|agent-panel-tab|agent-panel-events-subtab|gs-tab|gs-subtab|board-lane-tab)(?:,|\s*\{)/);
  assert.match(initiatives, /ui-tab ui-tab--contained planning-tab/);
  assert.match(thinking, /ui-tab ui-tab--contained thinking-tab/);
  assert.match(agentPanel, /ui-tab ui-tab--underline agent-panel-tab/);
  assert.match(agentEvents, /ui-tabs--contained ui-tablist agent-panel-events-subtabs/);
  assert.match(agentEvents, /ui-tab ui-tab--contained agent-panel-events-subtab/);
  assert.match(boardRender, /ui-tab ui-tab--underline board-lane-tab/);
  assert.match(html, /ui-tab gs-tab/);
  assert.match(html, /ui-tab ui-tab--underline gs-subtab/);
});

test('tablists expose roving keyboard navigation and compact overflow', () => {
  const core = source('static/js/render.js');
  const css = source('static/styles/components.css');
  const initiatives = source('static/js/initiatives.js');
  const thinking = source('static/js/thinking.js');
  const agent = source('static/js/agent_panel.js');
  const events = source('static/js/agent-panel/events.js');
  const templates = source('static/js/templates.js');
  const logs = source('static/js/log_viewer.js');
  const board = source('static/js/board/rendering.js');
  const html = source('webview.html');

  assert.match(core, /function uiTablistKeydown\(event\)[\s\S]*?ArrowLeft[\s\S]*?ArrowRight[\s\S]*?Home[\s\S]*?End[\s\S]*?next\.focus\(\)[\s\S]*?next\.click\(\)/);
  assert.match(core, /function uiRadioGroupKeydown\(event\)[\s\S]*?\[role="radio"\][\s\S]*?next\.click\(\)/);
  assert.match(css, /\.ui-tablist\s*\{[^}]*max-width:\s*100%;[^}]*overflow-x:\s*auto;[^}]*overscroll-behavior-inline:\s*contain;/s);
  for (const consumer of [initiatives, thinking, agent, events, templates, logs, board]) {
    assert.match(consumer, /ui-tablist/);
    assert.match(consumer, /uiTablistKeydown\(event\)/);
    assert.match(consumer, /tabindex=/);
  }
  assert.match(html, /settings-primary-nav[^>]*onkeydown="uiTablistKeydown\(event\)"/);
  assert.match(html, /gs-subtabs ui-tablist[^>]*role="tablist"/);
  assert.match(html, /role="radiogroup"[^>]*onkeydown="uiRadioGroupKeydown\(event\)"/);
  assert.match(html, /role="radio"[^>]*aria-checked="true" tabindex="0"/);
});

test('shared roving-focus handlers activate the expected choice', () => {
  const sandbox = { state: {}, AGENT_ICONS: [], PROCESS_MAP: {}, console };
  vm.createContext(sandbox);
  vm.runInContext(source('static/js/render.js'), sandbox);

  function choice(role, options = {}) {
    return {
      disabled: !!options.disabled,
      focused: false,
      clicked: false,
      getAttribute(name) {
        if (name === 'aria-disabled') return options.ariaDisabled ? 'true' : null;
        if (name === 'role') return role;
        return null;
      },
      closest(selector) {
        if (selector === `[role="${role}"]`) return this;
        if (selector === '[role="tablist"]') return options.list || null;
        return null;
      },
      focus() { this.focused = true; },
      click() { this.clicked = true; },
    };
  }

  const list = {
    getAttribute() { return null; },
    querySelectorAll() { return this.tabs; },
  };
  list.tabs = [choice('tab', { list }), choice('tab', { list }), choice('tab', { list })];
  let prevented = false;
  sandbox.uiTablistKeydown({
    key: 'ArrowRight', currentTarget: list, target: list.tabs[0],
    preventDefault() { prevented = true; },
  });
  assert.equal(prevented, true);
  assert.equal(list.tabs[1].focused, true);
  assert.equal(list.tabs[1].clicked, true);

  const group = { querySelectorAll() { return this.choices; } };
  group.choices = [choice('radio'), choice('radio'), choice('radio')];
  sandbox.uiRadioGroupKeydown({
    key: 'End', currentTarget: group, target: group.choices[0], preventDefault() {},
  });
  assert.equal(group.choices[2].focused, true);
  assert.equal(group.choices[2].clicked, true);

  const liveTabs = [choice('tab'), choice('tab')];
  const liveList = {
    getAttribute(name) { return name === 'aria-label' ? 'Planning sections' : null; },
    querySelectorAll() { return liveTabs; },
  };
  liveTabs.forEach((tab) => { tab.closest = () => liveList; });
  const oldList = {
    getAttribute(name) { return name === 'aria-label' ? 'Planning sections' : null; },
    querySelectorAll() { return this.tabs; },
  };
  oldList.tabs = [choice('tab', { list: oldList }), choice('tab', { list: oldList })];
  oldList.tabs[1].click = function() { this.clicked = true; };
  sandbox.document = { querySelectorAll() { return [liveList]; } };
  sandbox.requestAnimationFrame = (callback) => callback();
  sandbox.uiTablistKeydown({
    key: 'ArrowRight', currentTarget: oldList, target: oldList.tabs[0], preventDefault() {},
  });
  assert.equal(liveTabs[1].focused, true, 'focus follows a synchronously rerendered active tab');
});

test('compact dialogs, icon actions, and primary fields retain accessible affordances', () => {
  const css = source('static/styles/components.css');
  const modals = source('static/styles/modals.css');
  const html = source('webview.html');
  const grid = source('static/js/grid/agent-card.js');
  const initiatives = source('static/js/initiatives.js');
  const thinking = source('static/js/thinking.js');
  const taskArtifacts = source('static/js/modals/task-artifacts.js');
  const taskModal = source('static/js/modals/task-modal.js');
  const boardCards = source('static/js/board/card-rendering.js');
  const board = source('static/js/board/rendering.js');

  assert.match(css, /@media \(max-width: 600px\), \(max-height: 520px\)[\s\S]*?\.ui-modal\s*\{[^}]*max-width:\s*calc\(100vw - 16px\);[^}]*max-height:\s*calc\(100dvh - 16px\);/s);
  assert.match(css, /\.ui-modal__footer\s*\{[^}]*flex-wrap:\s*wrap;/s);
  assert.match(css, /\.ui-popover\s*\{[^}]*max-width:\s*calc\(100vw - 16px\);/s);
  assert.match(modals, /@media \(max-width: 600px\), \(max-height: 520px\)\s*\{\s*\.overlay \{ padding: 8px; \}/s);
  assert.match(grid, /aria-label="' \+ esc\(closeTitle \+ ' ' \+ \(\(a && a\.name\) \|\| 'agent'\)\)/);
  assert.match(initiatives, /aria-label="Refresh planning data"/);
  assert.match(thinking, /aria-label="Close Mind Map details"/);
  assert.match(thinking, /aria-label="Move node left"/);
  assert.match(thinking, /aria-label="Move node right"/);
  assert.match(taskArtifacts, /aria-label="Remove attachment /);
  assert.match(taskModal, /aria-label="Remove label /);
  assert.match(taskModal, /aria-label="Remove dependency /);
  assert.match(boardCards, /aria-label="Remove label /);
  assert.match(board, /aria-label="Remove image /);
  for (const pair of [
    ['add-name-input', 'Name'],
    ['gs-engineer-autonomy-mode', 'Autonomy'],
    ['gls-appearance-contrast', 'Contrast'],
    ['task-task-input', 'Task'],
    ['schedule-name-input', 'Name'],
  ]) {
    assert.match(html, new RegExp(`<label for="${pair[0]}">${pair[1]}`));
  }
});

test('residual responsive audit preserves compact actions, priority, and direct component intent', () => {
  const design = source('DESIGN.md');
  const components = source('static/styles/components.css');
  const gridCss = source('static/styles/workspace-grid.css');
  const shellCss = source('static/styles/workspace-shell.css');
  const groupTabs = source('static/js/grid/group-tabs.js');
  const panelManager = source('static/js/panel_manager.js');
  const history = source('static/js/history.js');
  const architect = source('static/js/agent-panel/architect.js');
  const board = source('static/js/board/rendering.js');
  const agentCard = source('static/js/grid/agent-card.js');
  const terminalRow = source('static/js/grid/terminal-row.js');
  const context = source('static/js/context.js');
  const health = source('static/js/health.js');

  assert.match(design, /D-021 — Narrow layouts preserve action priority and component parity/);
  assert.match(groupTabs, /agent-group-compact-settings btn btn-quiet btn-xs/);
  assert.match(groupTabs, /agent-group-quick-switcher ui-popover[\s\S]*?onkeydown="agentGroupQuickSwitcherKeydown\(event\)"/);
  assert.match(groupTabs, /\[data-group-switch-option\], \.agent-group-quick-new/);
  assert.match(groupTabs, /event\.key !== 'Home' && event\.key !== 'End'/);
  assert.match(gridCss, /@container agent-group-nav \(max-width:\s*380px\)[\s\S]*?\.agent-group-compact\s*\{\s*display:\s*flex;/s);

  assert.match(panelManager, /btn\.setAttribute\('role', 'tab'\)/);
  assert.match(panelManager, /btn\.setAttribute\('aria-selected', active \? 'true' : 'false'\)/);
  assert.match(panelManager, /tabs\.setAttribute\('role', 'tablist'\)/);
  assert.match(panelManager, /uiTablistKeydown\(event\)/);
  assert.match(panelManager, /function _standaloneCaptureZoneTabFocus\(\)[\s\S]*?standalone-panel-tab[\s\S]*?Right rail panels/);
  assert.match(panelManager, /function _standaloneRestoreZoneTabFocus\(snapshot\)[\s\S]*?preventScroll/);
  assert.match(panelManager, /var zoneTabFocus = _standaloneCaptureZoneTabFocus\(\)[\s\S]*?_standaloneRestoreZoneTabFocus\(zoneTabFocus\)/);
  assert.match(shellCss, /\.standalone-panel-zone-tabs\s*\{[^}]*flex:\s*1 1 auto;[^}]*max-width:\s*100%;[^}]*overflow-x:\s*auto;[^}]*overscroll-behavior-inline:\s*contain;[^}]*scrollbar-width:\s*auto;/s);

  assert.match(history, /ah-filters segmented-control" role="group" aria-label="History status"/);
  assert.match(history, /ah-filter-btn segmented-control__item/);
  assert.match(architect, /agent-panel-decisions-archive-toggle filter-chip/);
  assert.match(board, /board-filter-clear btn-link[^>]*>Clear filters</);
  assert.match(agentCard, /const cls = \['cell', 'ui-card', 'ui-card--interactive'\]/);
  assert.match(terminalRow, /const cls = \['term-row', 'ui-card', 'ui-card--interactive'\]/);

  assert.match(components, /\.ui-badge--micro\s*\{[^}]*font-size:\s*8px;/s);
  assert.match(shellCss, /\.statusbar-chip-attention\s*\{[^}]*flex-shrink:\s*0;/s);
  assert.match(shellCss, /@media \(max-width:\s*1280px\)\s*\{\s*\.statusbar-chip-agents \{ display: none; \}/s);
  assert.match(shellCss, /@media \(max-width:\s*860px\)[\s\S]*?#statusbar-claude-usage,[\s\S]*?#statusbar-codex-usage \{ display: none; \}/s);
  assert.doesNotMatch(shellCss, /\.statusbar-chip-attention\s*\{\s*display:\s*none;/s);

  assert.match(context, /context-empty ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite"/);
  assert.match(context, /context-empty ui-state ui-state--error ui-state--compact" role="alert"[^\n]*Refresh Context to try again/);
  assert.match(health, /health-error ui-state ui-state--error ui-state--compact" role="alert"[^\n]*Refresh Health to try again/);
  assert.match(health, /health-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite"/);
});

test('design inventory records the completed component-standardization baseline', () => {
  const design = source('DESIGN.md');
  assert.match(design, /The first component-standardization pass is complete as of 2026-07-15/);
  assert.match(design, /D-022 — The first component-standardization baseline is complete/);
  const families = [
    'Foundations and tokens',
    'Group tabs',
    'Panel tabs',
    'Feature navigation tabs',
    'Segmented controls',
    'Filter chips and presets',
    'Buttons',
    'Inputs and selectors',
    'Cards',
    'Toolbars and panel headers',
    'Status bar segments',
    'Menus and popovers',
    'Modals',
    'Badges, tags, and status',
    'Count indicators',
    'Empty/loading/error states',
  ];
  for (const family of families) {
    assert.ok(design.includes(`| ${family} | Standardized`), `${family} is not recorded as standardized`);
  }
});

test('shared segmented controls define one compact selected-state primitive', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.segmented-control\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*border:\s*var\(--control-border\);[^}]*border-radius:\s*var\(--radius-sm\);/s);
  assert.match(css, /\.segmented-control__item\s*\{[^}]*min-height:\s*calc\(var\(--control-height-sm\) - 2px\);[^}]*border-radius:\s*0;[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
  assert.match(css, /\.segmented-control__item\.is-active,[\s\S]*?\.segmented-control__item\[aria-selected="true"\]\s*\{[^}]*background:\s*var\(--accent-soft\);[^}]*outline:\s*1px solid var\(--accent-muted\);[^}]*outline-offset:\s*-1px;/s);
});

test('segmented-control consumers expose their selected state', () => {
  const grid = source('static/js/grid/main.js');
  const canvas = source('static/js/canvas.js');
  const actions = source('static/js/actions.js');
  const templates = source('static/js/templates.js');
  const schedules = source('static/js/modals/schedules.js');
  const html = source('webview.html');

  assert.match(grid, /data-agent-view-toggle="grid" aria-pressed="/);
  assert.match(grid, /segmented-control agent-view-toggle/);
  assert.match(grid, /segmented-control__item agent-view-toggle-btn/);
  assert.match(canvas, /setAttribute\('aria-pressed', target === active/);
  assert.match(actions, /role="group" aria-label="Action view"/);
  assert.match(actions, /segmented-control tpled-view-toggle/);
  assert.match(actions, /aria-pressed="' \+ \(_tplPanelView === 'editor'/);
  assert.match(templates, /role="tablist" aria-label="Library sections" onkeydown="uiTablistKeydown\(event\)"/);
  assert.match(templates, /role="tab" class="segmented-control__item tpled-view-btn[\s\S]*?aria-selected=/);
  assert.match(html, /class="segmented-control schedule-type-toggle" role="group" aria-labelledby="schedule-type-label"/);
  assert.match(schedules, /setAttribute\('aria-pressed', type === 'recurring'/);
});

test('segmented-control visual primitives do not drift back into feature styles', () => {
  const desktop = source('static/styles/desktop-features.css');
  const board = source('static/styles/board-panels.css');

  assert.doesNotMatch(desktop, /^\.log-viewer-targets?\s*\{/m);
  assert.doesNotMatch(desktop, /^\.agent-view-toggle-btn\s*\{[^}]*background:/ms);
  assert.doesNotMatch(board, /^\.tpled-view-btn\s*\{/m);
  assert.doesNotMatch(board, /^\.schedule-type-btn\s*\{[^}]*background:/ms);
  assert.doesNotMatch(source('static/styles/components.css'), /\.(?:agent-view-toggle|tpled-view-toggle|log-viewer-targets|schedule-type-toggle|agent-view-toggle-btn|tpled-view-btn|log-viewer-target|schedule-type-btn)(?:,|\s*\{)/);
});

test('shared filter chips and presets use compact rectangular action geometry', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.filter-chip,\s*\.preset-button\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*padding:\s*0 var\(--control-padding-x-sm\);[^}]*border-radius:\s*var\(--radius-sm\);[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
  assert.match(css, /\.filter-chip\.active,[\s\S]*?\.filter-chip\[aria-expanded="true"\]\s*\{[^}]*color:\s*var\(--accent\);[^}]*border-color:\s*var\(--accent-muted\);[^}]*background:\s*var\(--accent-soft\);/s);
  assert.match(css, /\.filter-chip:disabled,[\s\S]*?\.filter-chip\.disabled\s*\{[^}]*cursor:\s*not-allowed;/s);
});

test('filter consumers expose state while presets remain momentary actions', () => {
  const board = source('static/js/board/rendering.js');
  const filters = source('static/js/board/filters.js');
  const initiatives = source('static/js/initiatives.js');
  const thinking = source('static/js/thinking.js');
  const html = source('webview.html');

  assert.match(board, /class="filter-chip board-filter-btn[\s\S]*?aria-haspopup="dialog" aria-expanded=/);
  assert.match(board, /class="filter-chip board-filter-btn[\s\S]*?aria-pressed=/);
  assert.match(board, /<button type="button" class="filter-chip board-filter-active-chip[\s\S]*?aria-label="Remove/);
  assert.doesNotMatch(board, /<span class="board-filter-active-chip/);
  assert.match(filters, /setAttribute\('aria-expanded', 'true'\)/);
  assert.match(initiatives, /class="filter-chip initiative-secondary-toggle[\s\S]*?aria-pressed=/);
  assert.match(thinking, /class="filter-chip[\s\S]*?aria-pressed=/);
  assert.match(html, /class="preset-button schedule-preset"/);
  assert.match(html, /class="preset-button" onclick="_addWtSymlinkPreset/);
  assert.doesNotMatch(html, /class="preset-button[^>]*aria-pressed/);
});

test('filter and preset geometry does not drift back into feature styles', () => {
  const board = source('static/styles/board-panels.css');
  const features = source('static/styles/feature-panels.css');

  assert.doesNotMatch(board, /^body\.runtime-embedded \.board-filter-btn\s*\{[^}]*border-radius:/ms);
  assert.doesNotMatch(board, /^\.board-filter-active-chip\s*\{[^}]*border-radius:/ms);
  assert.doesNotMatch(board, /^\.schedule-preset\s*\{/m);
  assert.doesNotMatch(board, /^\.wt-symlink-presets \.preset-button\s*\{[^}]*(?:border|background):/ms);
  assert.doesNotMatch(features, /^\.initiative-secondary-toggle\s*\{[^}]*border-radius:/ms);
  assert.doesNotMatch(features, /^\.idea-brief-filter-row \.active\s*\{/m);
  assert.doesNotMatch(source('static/styles/components.css'), /\.(?:board-filter-btn|board-filter-active-chip|initiative-secondary-toggle|schedule-preset)(?:,|\s*\{)/);
});

test('shared cards define boundary, density, and interactive state primitives', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-card\s*\{[^}]*border:\s*1px solid var\(--border\);[^}]*border-radius:\s*var\(--radius\);[^}]*background:\s*var\(--bg-cell\);[^}]*transition:/s);
  assert.match(css, /\.ui-card--compact\s*\{[^}]*padding:\s*var\(--space-2\);/s);
  assert.match(css, /\.ui-card--comfortable\s*\{[^}]*padding:\s*10px;/s);
  assert.match(css, /\.ui-card--interactive:hover,\s*\.ui-card\.is-hovered\s*\{[^}]*border-color:\s*var\(--border-strong\);[^}]*background:\s*var\(--bg-hover\);/s);
  assert.match(css, /\.ui-card\.is-selected,\s*\.ui-card\[aria-selected="true"\]\s*\{[^}]*border-color:\s*var\(--accent-muted\);[^}]*background:\s*color-mix\(in srgb, var\(--accent\) 8%, var\(--bg-cell\)\);/s);
});

test('card consumers opt into the canonical API without losing surface identity', () => {
  const board = source('static/js/board/card-rendering.js');
  const context = source('static/js/context.js');
  const agent = source('static/js/agent_panel.js');
  const engineer = source('static/js/agent-panel/legacy-engineer.js');

  assert.match(board, /class="board-card ui-card ui-card--interactive/);
  assert.match(context, /class="context-card ui-card ui-card--comfortable ui-card--interactive/);
  assert.match(agent, /class="agent-panel-message-card ui-card ui-card--compact/);
  assert.match(engineer, /class="agent-panel-stream-card ui-card ui-card--compact/);
  assert.match(board, /sharedState = \(focused \|\| selected \? ' is-selected'/);
  assert.match(board, /hovered \? ' is-hovered'/);
  assert.match(context, /selected is-selected/);
});

test('card geometry does not drift back into feature styles', () => {
  const board = source('static/styles/board-panels.css');
  const agent = source('static/styles/agent-panel.css');

  assert.doesNotMatch(board, /^\.board-card\s*\{[^}]*border-radius:/ms);
  assert.doesNotMatch(board, /^\.context-card\s*\{[^}]*(?:border|background):/ms);
  assert.doesNotMatch(agent, /^\.agent-panel-message-card\s*\{[^}]*border-radius:/ms);
  assert.doesNotMatch(agent, /^\.agent-panel-stream-card\s*\{[^}]*(?:border|background|padding):/ms);
});

test('shared panel headers separate identity, actions, and toolbar rows', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-panel-header\s*\{[^}]*display:\s*flex;[^}]*gap:\s*var\(--space-2\) 10px;[^}]*padding:\s*var\(--space-2\) 10px;[^}]*border-bottom:\s*1px solid var\(--border\);/s);
  assert.match(css, /\.ui-panel-header__title\s*\{[^}]*margin:\s*0;[^}]*font-size:\s*12px;/s);
  assert.match(css, /\.ui-panel-header__subtitle\s*\{[^}]*margin:\s*2px 0 0;[^}]*font-size:\s*10px;/s);
  assert.match(css, /\.ui-panel-header__actions\s*\{[^}]*justify-content:\s*flex-end;[^}]*flex:\s*0 1 auto;[^}]*margin-left:\s*auto;[^}]*flex-wrap:\s*wrap;/s);
  assert.match(css, /\.ui-toolbar\s*\{[^}]*display:\s*flex;[^}]*align-items:\s*center;[^}]*flex-wrap:\s*wrap;[^}]*padding:\s*var\(--space-1\) var\(--space-2\);/s);
  assert.match(css, /\.ui-toolbar--bordered\s*\{[^}]*border-bottom:\s*1px solid var\(--border\);/s);
  assert.doesNotMatch(css, /\.(?:tpled-header|events-header|agent-panel-header)(?:,|\s*\{)/);
});

test('core panel-header and toolbar consumers opt into the canonical API', () => {
  const board = source('static/js/board/rendering.js');
  const events = source('static/js/events.js');
  const initiatives = source('static/js/initiatives.js');
  const thinking = source('static/js/thinking.js');
  const agent = source('static/js/agent_panel.js');
  const engineer = source('static/js/agent-panel/legacy-engineer.js');
  const actions = source('static/js/actions.js');
  const templates = source('static/js/templates.js');
  const history = source('static/js/history.js');
  const context = source('static/js/context.js');
  const help = source('static/js/help.js');
  const health = source('static/js/health.js');
  const supervisor = source('static/js/supervisor.js');
  const chat = source('static/js/chat.js');
  const mission = source('static/js/mission_control.js');
  const diff = source('static/js/diff.js');

  assert.match(board, /class="board-search-bar ui-toolbar ui-toolbar--bordered"/);
  assert.match(events, /class="events-header ui-panel-header ui-panel-header--surface"/);
  assert.match(events, /class="events-header-actions ui-panel-header__actions"/);
  assert.match(initiatives, /class="tpled-header initiatives-header ui-panel-header ui-panel-header--surface"/);
  assert.match(initiatives, /class="tpled-header-controls ui-panel-header__actions"/);
  assert.match(thinking, /class="tpled-header thinking-header ui-panel-header ui-panel-header--surface"/);
  assert.match(thinking, /class="tpled-header-controls ui-panel-header__actions"/);
  assert.match(agent, /class="agent-panel-header ui-panel-header"/);
  assert.match(agent, /class="agent-panel-header-right ui-panel-header__actions"/);
  assert.match(engineer, /class="agent-panel-header ui-panel-header"/);
  assert.match(actions, /class="tpled-header ui-panel-header ui-panel-header--surface"/);
  assert.match(actions, /class="tpled-header-controls ui-toolbar ui-toolbar--bordered"/);
  assert.match(templates, /class="tpled-header ui-panel-header ui-panel-header--surface"/);
  assert.match(templates, /class="tpled-header-controls ui-toolbar ui-toolbar--bordered"/);
  assert.match(history, /class="tpled-header-controls ui-panel-header__actions"/);
  assert.match(history, /class="ah-toolbar ui-toolbar ui-toolbar--bordered"/);
  assert.match(context, /class="context-header ui-panel-header ui-panel-header--surface"/);
  assert.match(context, /class="context-toolbar ui-toolbar ui-toolbar--bordered"/);
  assert.match(help, /class="help-header ui-panel-header ui-panel-header--surface"/);
  assert.match(help, /class="help-toolbar help-browser-toolbar ui-toolbar ui-toolbar--bordered"/);
  assert.match(health, /class="health-header ui-panel-header ui-panel-header--surface"/);
  assert.match(health, /class="health-toolbar ui-toolbar ui-toolbar--bordered"/);
  assert.match(supervisor, /class="supervisor-header ui-panel-header ui-panel-header--surface"/);
  assert.match(supervisor, /class="supervisor-toolbar ui-toolbar ui-toolbar--bordered"/);
  assert.match(chat, /listHeader\.className = 'chat-panel-header ui-panel-header ui-panel-header--surface'/);
  assert.match(mission, /class="tpled-header mc-header ui-panel-header ui-panel-header--surface"/);
  assert.match(mission, /class="tpled-header-controls mc-controls ui-toolbar ui-toolbar--bordered"/);
  assert.match(diff, /class="diff-view-toolbar ui-toolbar ui-toolbar--bordered"/);
});

test('panel-header geometry does not drift back into feature styles', () => {
  const board = source('static/styles/board-panels.css');
  const agent = source('static/styles/agent-panel.css');
  const features = source('static/styles/feature-panels.css');
  const desktop = source('static/styles/desktop-features.css');

  assert.doesNotMatch(board, /^\.tpled-header\s*\{/m);
  assert.doesNotMatch(board, /^\.events-header\s*\{/m);
  assert.doesNotMatch(board, /^\.board-search-bar\s*\{[^}]*(?:display|padding|border-bottom|flex-wrap):/ms);
  assert.doesNotMatch(agent, /^\.agent-panel-header\s*\{/m);
  assert.doesNotMatch(agent, /^\.agent-panel-header-(?:copy|right)\s*\{/m);
  assert.doesNotMatch(agent, /^\.agent-panel-(?:title|subtitle)\s*\{/m);
  assert.doesNotMatch(board, /^\.context-header\s*\{/m);
  assert.doesNotMatch(board, /^\.context-(?:header-copy|title|subtitle|header-actions|focus-row|toolbar)\s*\{[^}]*(?:display|padding|border-bottom|font-size):/ms);
  assert.doesNotMatch(board, /^\.chat-panel-header\s*\{/m);
  assert.doesNotMatch(board, /^\.ah-toolbar\s*\{[^}]*(?:display|padding|gap|flex-wrap):/ms);
  assert.doesNotMatch(board, /^\.events-search-row\s*\{[^}]*(?:padding|border-bottom):/ms);
  assert.doesNotMatch(board, /^\.diff-view-toolbar\s*\{[^}]*(?:display|align-items|gap|flex-wrap):/ms);
  assert.doesNotMatch(agent, /^\.agent-panel-(?:events-toolbar|session-map-toolbar)\s*\{[^}]*(?:display|align-items|gap|padding|border|border-radius):/ms);
  assert.doesNotMatch(features, /^\.help-header\s*\{[^}]*(?:display|padding|border-bottom):/ms);
  assert.doesNotMatch(features, /^\.health-toolbar\s*\{[^}]*(?:display|padding|border|border-radius):/ms);
  assert.doesNotMatch(features, /^\.thinking-list-toolbar\s*\{[^}]*(?:display|align-items|gap|padding|border-bottom):/ms);
  assert.doesNotMatch(desktop, /^\.supervisor-header\s*\{/m);
});

test('shared modals define raised size variants and structured regions', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-modal\s*\{[^}]*max-width:\s*360px;[^}]*border:\s*1px solid var\(--border-strong\);[^}]*border-radius:\s*var\(--radius-lg\);[^}]*background:\s*var\(--bg-raised\);[^}]*box-shadow:\s*var\(--shadow-float\);/s);
  assert.doesNotMatch(css, /\.ui-modal,\s*\.modal/);
  assert.match(css, /\.ui-modal\.ui-modal--sm\s*\{[^}]*max-width:\s*360px;/s);
  assert.match(css, /\.ui-modal\.ui-modal--md\s*\{[^}]*max-width:\s*520px;/s);
  assert.match(css, /\.ui-modal\.ui-modal--lg\s*\{[^}]*max-width:\s*760px;/s);
  assert.match(css, /\.ui-modal\.ui-modal--xl\s*\{[^}]*max-width:\s*920px;/s);
  assert.match(css, /\.ui-modal\.ui-modal--full\s*\{[^}]*max-width:\s*1100px;/s);
  assert.match(css, /\.ui-modal--tall\s*\{[^}]*max-height:\s*85vh;[^}]*display:\s*flex;[^}]*flex-direction:\s*column;/s);
  assert.match(css, /\.ui-modal--viewport\s*\{[^}]*max-height:\s*calc\(100vh - 40px\);/s);
  assert.match(css, /\.ui-modal__header\s*\{[^}]*padding:\s*14px 16px 10px;/s);
  assert.match(css, /\.ui-modal__body\s*\{[^}]*padding:\s*0 16px 16px;[^}]*overflow:\s*auto;/s);
  assert.match(css, /\.ui-modal--structured \.ui-modal__footer\s*\{[^}]*padding:\s*12px 16px 14px;[^}]*border-top:\s*1px solid var\(--border\);/s);
  assert.doesNotMatch(css, /\.modal h2/);
  assert.doesNotMatch(css, /\.modal-actions\s*,/);
});

test('large and multi-section modal consumers use canonical shells and regions', () => {
  const html = source('webview.html');
  const diff = source('static/js/diff.js');
  const artifacts = source('static/js/modals/task-artifacts.js');
  const help = source('static/js/help.js');
  const logs = source('static/js/log_viewer.js');
  const attachments = source('static/js/terminal/composer-attachments.js');

  for (const id of ['modal-task', 'modal-group-settings', 'modal-global-settings',
    'modal-task-artifacts', 'modal-system-prompt-preview', 'modal-task-history']) {
    const start = html.indexOf(`id="${id}"`);
    assert.notEqual(start, -1, `${id} should exist`);
    const slice = html.slice(start, start + (id.includes('settings') ? 7000 : 2600));
    assert.match(slice, /class="modal ui-modal ui-modal--(?:md|lg|xl)/);
    assert.match(slice, /role="dialog" aria-modal="true" aria-labelledby=/);
  }
  assert.match(html, /id="modal-group-settings"[\s\S]*?settings-dialog-workspace ui-modal__body ui-modal__body--flush[\s\S]*?settings-dialog-footer ui-modal__footer/);
  assert.match(html, /id="modal-global-settings"[\s\S]*?settings-dialog-workspace ui-modal__body ui-modal__body--flush[\s\S]*?settings-dialog-footer ui-modal__footer/);
  assert.match(html, /id="modal-task"[\s\S]*?ui-modal__header ui-modal__header--bordered[\s\S]*?task-modal-body ui-modal__body[\s\S]*?modal-actions ui-modal__footer/);
  assert.match(html, /id="modal-task-artifacts"[\s\S]*?ui-modal__header ui-modal__header--bordered[\s\S]*?task-artifacts-browser ui-modal__body[\s\S]*?modal-actions ui-modal__footer/);
  for (const id of ['modal-add', 'modal-architect-decision', 'modal-schedule',
    'modal-behavior-approval', 'modal-agent-class']) {
    assert.match(html, new RegExp(`id="${id}"[\\s\\S]*?ui-modal--structured[\\s\\S]*?ui-modal__header[\\s\\S]*?ui-modal__body[\\s\\S]*?ui-modal__footer`));
  }
  assert.match(diff, /ui-modal--xl ui-modal--viewport ui-modal--structured diff-view-modal diff-view/);
  assert.match(diff, /diff-view-header ui-modal__header ui-modal__header--bordered/);
  assert.match(diff, /diff-view-content ui-modal__body ui-modal__body--flush/);
  assert.match(diff, /diff-footer ui-modal__footer/);
  assert.match(artifacts, /ui-modal--full ui-modal--tall ui-modal--structured artifact-preview-modal/);
  assert.match(artifacts, /artifact-preview-head ui-modal__header ui-modal__header--bordered/);
  assert.match(artifacts, /artifact-preview-body ui-modal__body ui-modal__body--flush/);
  assert.match(help, /ui-modal--full ui-modal--tall ui-modal--structured help-topic-browser-modal/);
  assert.match(help, /help-topic-browser-head ui-modal__header ui-modal__header--bordered/);
  assert.match(help, /help-topic-browser-workspace ui-modal__body/);
  assert.match(help, /modal-actions ui-modal__footer/);
  assert.match(logs, /ui-modal--xl ui-modal--tall ui-modal--structured log-viewer-modal/);
  assert.match(logs, /log-viewer-header ui-modal__header ui-modal__header--bordered/);
  assert.match(logs, /log-viewer-body ui-modal__body/);
  assert.match(logs, /log-viewer-actions ui-modal__footer/);
  assert.match(attachments, /ui-modal--lg ui-modal--structured terminal-compose-attachment-preview-modal/);
  assert.match(attachments, /terminal-compose-attachment-preview-head ui-modal__header ui-modal__header--bordered/);
  assert.match(attachments, /terminal-compose-attachment-preview-body ui-modal__body ui-modal__body--flush/);
});

test('small modal consumers use canonical regions and visible labels', () => {
  const html = source('webview.html');

  for (const id of ['modal-group', 'modal-edit', 'modal-confirm', 'modal-input-dialog']) {
    const start = html.indexOf(`id="${id}"`);
    assert.notEqual(start, -1, `${id} should exist`);
    const slice = html.slice(start, start + 1800);
    assert.match(slice, /class="modal ui-modal ui-modal--sm ui-modal--structured"/);
    assert.match(slice, /class="ui-modal__header"/);
    assert.match(slice, /class="ui-modal__body"/);
    assert.match(slice, /class="modal-actions ui-modal__footer"/);
  }
  assert.match(html, /id="modal-confirm"[\s\S]*?aria-labelledby="confirm-title"[\s\S]*?<h2 id="confirm-title"/);
  assert.doesNotMatch(html, /id="modal-confirm"[\s\S]{0,400}aria-label="Confirm action"/);
  assert.match(html, /<label for="group-name-input">Group name<\/label>/);
  assert.match(html, /<label for="edit-name-input">Name<\/label>/);
  for (const id of ['modal-engineer-title', 'modal-architect-title',
    'pending-hire-reject-title', 'new-specialization-title', 'gs-title', 'gls-title']) {
    assert.match(html, new RegExp(`id="${id}" class="ui-modal__title"`));
  }
  assert.match(source('static/js/modals/task-artifacts.js'), /modal-actions ui-modal__footer/);
});

test('simple modal consumers use the shared focus lifecycle', () => {
  const core = source('static/js/modals/core.js');
  const modals = source('static/js/modals.js');
  const html = source('webview.html');

  assert.match(core, /function openAddGroup\(\)[\s\S]*?openModalDialog\(modal, \{[\s\S]*?labelledBy: 'modal-group-title',[\s\S]*?initialFocus: inp,[\s\S]*?submitOnEnter: true,[\s\S]*?onCancel:/);
  assert.match(modals, /function openEditCell\(id\)[\s\S]*?openModalDialog\(modal, \{[\s\S]*?labelledBy: 'edit-title',[\s\S]*?initialFocus: nameInput,[\s\S]*?submitOnEnter: true,[\s\S]*?onCancel:/);
  assert.doesNotMatch(html, /id="group-name-input"[^>]*onkeydown=/);
  assert.doesNotMatch(html, /id="edit-name-input"[^>]*onkeydown=/);
});

test('modal surface and footer geometry do not drift back into modal styles', () => {
  const css = source('static/styles/modals.css');
  const board = source('static/styles/board-panels.css');

  assert.doesNotMatch(css, /^\.modal\s*\{[^}]*(?:border-radius|background|box-shadow|max-width):/ms);
  assert.doesNotMatch(css, /^\.modal-actions\s*\{/m);
  assert.doesNotMatch(css, /^\.modal-tall\s*\{/m);
  assert.doesNotMatch(board, /^\.modal-wide\s*\{/m);
  assert.doesNotMatch(board, /^\s*\.modal(?:\.|\s*\{)[^}]*(?:max-width|border-radius|background|box-shadow):/ms);
  assert.doesNotMatch(css, /^\.settings-dialog\s*\{[^}]*(?:padding|overflow|border-radius|background|box-shadow):/ms);
});

test('shared menus define one floating surface and compact item state grammar', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-popover\s*\{[^}]*padding:\s*var\(--space-1\);[^}]*border:\s*1px solid var\(--border-strong\);[^}]*border-radius:\s*var\(--radius-lg\);[^}]*background:\s*var\(--bg-raised\);[^}]*box-shadow:\s*var\(--shadow-float\);[^}]*max-height:/s);
  assert.match(css, /\.ui-menu-item\s*\{[^}]*min-height:\s*var\(--control-height-md\);[^}]*padding:\s*var\(--space-1\) var\(--space-2\);[^}]*border-radius:\s*var\(--radius\);[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
  assert.match(css, /\.ui-menu-item\.is-selected,[\s\S]*?\.ui-menu-item\[aria-selected="true"\]\s*\{[^}]*border-color:\s*var\(--accent-muted\);[^}]*color:\s*var\(--accent\);[^}]*background:\s*var\(--accent-soft\);/s);
  assert.match(css, /\.ui-menu-item--danger:hover,[\s\S]*?\.ui-menu-item--danger:focus-visible\s*\{[^}]*color:\s*var\(--red\);[^}]*background:\s*color-mix\(in srgb, var\(--red\) 12%, var\(--bg-hover\)\);/s);
  assert.doesNotMatch(css, /#ctx-menu|agent-group-quick-switcher|board-filter-dropdown|board-view-menu/);
});

test('group navigation and Board popovers use the appropriate direct and transient semantics', () => {
  const html = source('webview.html');
  const groups = source('static/js/grid/group-tabs.js');
  const commands = source('static/js/commands.js');
  const board = source('static/js/board/rendering.js');
  const filters = source('static/js/board/filters.js');
  const view = source('static/js/board/view-state.js');

  assert.match(html, /id="ctx-menu" class="ui-popover ui-menu" role="menu" aria-hidden="true"/);
  assert.match(groups, /class="agent-group-tab-settings"[\s\S]*openGroupSettings\(/);
  assert.doesNotMatch(groups, /Group actions|openAgentGroupTabActions|aria-haspopup="menu"/);
  assert.match(groups, /class="agent-group-quick-switcher ui-popover" role="dialog"/);
  assert.match(groups, /class="agent-group-quick-option ui-menu-item/);
  assert.match(commands, /role="menuitem" class="ui-menu-item/);
  assert.match(board, /aria-haspopup="dialog" aria-expanded=/);
  assert.match(filters, /className = 'board-filter-dropdown ui-popover'/);
  assert.match(filters, /className = 'board-filter-dropdown-item ui-menu-item'/);
  assert.match(view, /className = 'board-view-menu ui-popover'/);
  assert.match(view, /class="board-view-menu-toggle ui-menu-item/);
});

test('remaining task, terminal, Board editor, and panel popovers use the canonical API', () => {
  const html = source('webview.html');
  const task = source('static/js/modals/task-modal.js');
  const terminal = source('static/js/terminal/composer.js');
  const board = source('static/js/board/rendering.js');
  const inline = source('static/js/board/inline-create.js');
  const selection = source('static/js/board/selection.js');
  const commands = source('static/js/commands.js');

  assert.match(html, /id="task-title-label-dropdown" class="deps-dropdown ui-popover" role="listbox"/);
  assert.match(html, /id="task-labels-dropdown" class="deps-dropdown ui-popover" role="listbox"/);
  assert.match(html, /id="task-deps-dropdown" class="deps-dropdown ui-popover" role="listbox"/);
  assert.match(html, /id="panel-nav-more-menu" class="panel-nav-more-menu ui-popover" role="dialog"/);
  assert.match(task, /class="deps-option ui-menu-item" role="option" aria-selected="false"/);
  assert.match(terminal, /class="deps-dropdown terminal-compose-task-dropdown ui-popover"/);
  assert.match(terminal, /class="deps-dropdown terminal-compose-slash-dropdown ui-popover"/);
  assert.match(terminal, /class="terminal-compose-history-menu ui-popover"/);
  assert.match(terminal, /class="terminal-compose-history-item ui-menu-item"/);
  assert.match(board, /class="deps-dropdown ui-popover" role="listbox"/);
  assert.match(inline, /board-add-agent-list ui-popover ui-menu/);
  assert.match(inline, /board-add-menu-item ui-menu-item/);
  assert.match(selection, /board-selection-dropdown ui-popover ui-menu/);
  assert.match(selection, /board-selection-dropdown-item ui-menu-item/);
  assert.match(selection, /board-selection-batch-panel ui-popover/);
  assert.match(commands, /function normalizeContextMenuMarkup\(menu\)[\s\S]*?button\.classList\.add\('ui-menu-item'\)/);
});

test('transient menus restore focus on Escape and support keyboard traversal', () => {
  const groups = source('static/js/grid/group-tabs.js');
  const commands = source('static/js/commands.js');
  const filters = source('static/js/board/filters.js');
  const view = source('static/js/board/view-state.js');
  const terminal = source('static/js/terminal/composer.js');
  const inline = source('static/js/board/inline-create.js');
  const selection = source('static/js/board/selection.js');
  const task = source('static/js/modals/task-modal.js');
  const panelLauncher = source('static/js/navigation/panel-launcher.js');

  assert.match(groups, /event\.key === 'Escape'[\s\S]*?closeAgentGroupQuickSwitcher\(true\)/);
  assert.match(commands, /function closeContextMenu\(options\)[\s\S]*?document\.getElementById\(invokerId\)[\s\S]*?options\.restoreFocus !== false[\s\S]*?focusTarget\.focus\(\)/);
  assert.match(commands, /function contextMenuKeydown\(event\)[\s\S]*?ArrowDown[\s\S]*?ArrowUp[\s\S]*?items\[index\]\.focus\(\)/);
  assert.match(source('static/js/board/card-rendering.js'), /id="board-task-actions-' \+ t\.id \+ '" class="board-card-menu-btn"/);
  assert.match(filters, /dd\.addEventListener\('keydown'[\s\S]*?e\.key !== 'Escape'[\s\S]*?_boardCloseFilterDropdown\(\{ restoreFocus: true \}\)/);
  assert.match(view, /menu\.addEventListener\('keydown'[\s\S]*?e\.key !== 'Escape'[\s\S]*?currentTrigger\.focus\(\)/);
  assert.match(terminal, /_terminalComposeHistoryHandleDocumentKeydown\(evt\)[\s\S]*?ArrowDown[\s\S]*?items\[index\]\.focus\(\)/);
  assert.match(inline, /function boardInlineMenuKeydown\(event, kind\)[\s\S]*?Escape[\s\S]*?_boardCloseInlineDropdown\(kind, true\)[\s\S]*?ArrowDown/);
  assert.match(selection, /function boardBulkMoveMenuKeydown\(evt\)[\s\S]*?Escape[\s\S]*?boardCloseSelectionMenus\(true\)[\s\S]*?ArrowDown/);
  assert.match(task, /function taskDepsKeydown\(e\)[\s\S]*?ArrowDown[\s\S]*?ArrowUp[\s\S]*?Enter[\s\S]*?Escape/);
  assert.match(panelLauncher, /function closePanelNavMore\(restoreFocus\)[\s\S]*?restoreFocus[\s\S]*?button\.focus\(\)/);
  assert.match(panelLauncher, /function panelNavMoreKeydown\(event\)[\s\S]*?Escape[\s\S]*?closePanelNavMore\(true\)/);
});

test('menu geometry does not drift back into feature styles', () => {
  const grid = source('static/styles/workspace-grid.css');
  const board = source('static/styles/board-panels.css');
  const modals = source('static/styles/modals.css');

  assert.doesNotMatch(grid, /^\.agent-group-quick-switcher\s*\{[^}]*(?:padding|border-radius|background|box-shadow):/ms);
  assert.doesNotMatch(board, /^\.board-filter-dropdown\s*\{[^}]*(?:background|border-radius|box-shadow):/ms);
  assert.doesNotMatch(board, /^\.board-view-menu\s*\{[^}]*(?:padding|background|border-radius|box-shadow):/ms);
  assert.doesNotMatch(board, /^\.deps-dropdown\s*\{[^}]*(?:padding|background|border|border-radius|box-shadow):/ms);
  assert.doesNotMatch(board, /^\.board-selection-dropdown\s*\{[^}]*(?:padding|background|border|border-radius|box-shadow):/ms);
  assert.doesNotMatch(board, /^\.board-add-agent-list\s*\{[^}]*(?:padding|background|border|border-radius|box-shadow):/ms);
  assert.doesNotMatch(grid, /^\.terminal-compose-history-menu\s*\{[^}]*(?:padding|background|border|border-radius|box-shadow):/ms);
  assert.doesNotMatch(modals, /^#ctx-menu\s*\{[^}]*(?:padding|background|border-radius|box-shadow):/ms);
  assert.doesNotMatch(modals, /^#ctx-menu button\s*\{[^}]*(?:padding|font-size|border-radius|color):/ms);
});

test('shared semantic badges define density and intent variants', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-badge\s*\{[^}]*display:\s*inline-flex;[^}]*min-height:\s*18px;[^}]*padding:\s*1px 6px;[^}]*border:\s*1px solid var\(--border\);[^}]*border-radius:\s*999px;[^}]*font-size:\s*9px;[^}]*max-width:\s*100%;[^}]*text-overflow:\s*ellipsis;/s);
  assert.match(css, /\.ui-badge--compact\s*\{[^}]*min-height:\s*14px;[^}]*font-size:\s*8px;/s);
  assert.match(css, /\.ui-badge--micro\s*\{[^}]*min-height:\s*12px;[^}]*padding:\s*0 4px;[^}]*font-size:\s*8px;/s);
  for (const intent of ['neutral', 'accent', 'success', 'warning', 'danger']) {
    assert.match(css, new RegExp(`\\.ui-badge--${intent}\\s*\\{[^}]*color:`));
  }
  assert.match(css, /\.ui-badge--count\s*\{[^}]*min-width:\s*18px;[^}]*font-variant-numeric:\s*tabular-nums;/s);
});

test('agent identity, journal, and Health badges use the canonical API', () => {
  const grid = source('static/js/grid/agent-card.js');
  const agent = source('static/js/agent_panel.js');
  const architect = source('static/js/agent-panel/architect.js');
  const engineer = source('static/js/agent-panel/engineer.js');
  const legacyEngineer = source('static/js/agent-panel/legacy-engineer.js');
  const health = source('static/js/health.js');

  assert.match(grid, /class="agent-card-kind ui-badge ui-badge--micro /);
  assert.match(agent, /function _agentPanelJournalBadgeClass\(type\)[\s\S]*?return 'agent-panel-badge ui-badge ui-badge--' \+ intent/);
  assert.match(architect, /var typeClass = _agentPanelJournalBadgeClass\(entryType\)/);
  assert.match(engineer, /_agentPanelJournalBadgeClass\(entry\.type \|\| 'note'\)/);
  assert.match(legacyEngineer, /var typeClass = _agentPanelJournalBadgeClass\(e\.type \|\| 'observation'\)/);
  assert.match(health, /up: \{ label: 'up', className: 'health-pill ui-badge ui-badge--success' \}/);
  assert.match(health, /down: \{ label: 'down', className: 'health-pill ui-badge ui-badge--danger' \}/);
  assert.match(health, /coverage\.partial \? 'ui-badge--warning' : 'ui-badge--success'/);
  assert.match(health, /class="health-metrics-overhead"[\s\S]*?class="ui-badge ui-badge--neutral"/);
  assert.match(health, /class="health-metrics-history-meta"[\s\S]*?class="ui-badge ui-badge--neutral"/);
});

test('Board metadata and count annotations use badges while clickable chips remain controls', () => {
  const card = source('static/js/board/card-rendering.js');
  const render = source('static/js/render.js');
  const board = source('static/js/board/rendering.js');
  const filters = source('static/js/board/filters.js');
  const selection = source('static/js/board/selection.js');
  const css = source('static/styles/board-panels.css');

  assert.match(card, /function _boardMetadataBadgeClass[\s\S]*?'board-card-label',[\s\S]*?'ui-badge',[\s\S]*?'ui-badge--compact',[\s\S]*?'ui-badge--' \+ \(intent \|\| 'neutral'\)/);
  assert.match(render, /_boardMetadataBadgeClass\([\s\S]*?'board-card-created-by board-card-created-by-' \+ meta\.kind,[\s\S]*?intent/);
  assert.match(card, /_boardMetadataBadgeClass\('board-card-status', 'success'\)/);
  assert.match(card, /_boardMetadataIntentForHealth\(healthState\)/);
  assert.match(card, /_boardMetadataIntentForVerification\(verificationState\)/);
  assert.match(card, /board-card-control-chip board-card-assigned-engineer/);
  assert.match(card, /board-card-control-chip board-card-external-chip board-card-github-chip/);
  assert.match(card, /board-card-control-chip ' \+ depClassName \+ ' board-card-badge-jump/);
  assert.match(card, /board-card-control-chip board-card-attachments/);

  assert.match(board, /board-wide-lane-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(board, /lane-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count/);
  assert.match(board, /board-filter-btn-count ui-badge ui-badge--micro ui-badge--accent ui-badge--count/);
  assert.match(filters, /board-filter-dropdown-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count/);
  assert.match(selection, /board-selection-count ui-badge ui-badge--compact ui-badge--accent ui-badge--count/);

  assert.match(css, /\.board-card-control-chip\s*\{[^}]*min-height:\s*14px;[^}]*border-radius:\s*4px;/s);
  assert.doesNotMatch(css, /^\.board-card-label\s*\{/m);
  assert.doesNotMatch(css, /^\.board-(?:filter-btn|filter-dropdown|wide-lane)-count\s*\{[^}]*(?:font-size|padding|border-radius|background):/ms);
});

test('Agent Profile assignment and preview metadata use canonical badges', () => {
  const classes = source('static/js/agent-panel/classes.js');
  const css = source('static/styles/feature-panels.css');

  assert.match(classes, /function _agentPanelClassBadgeIntent\(status, pending\)[\s\S]*?return 'neutral';/);
  assert.match(classes, /return classes \+ 'ui-badge ui-badge--compact ui-badge--' \+ \(intent \|\| 'neutral'\)/);
  assert.match(classes, /_agentPanelClassMetadataBadgeClass\([\s\S]*?localClasses,[\s\S]*?_agentPanelClassBadgeIntent\(status, state\.pending\)/);
  assert.match(classes, /_agentPanelClassMetadataBadgeClass\('agent-profile-chip', 'neutral'\)/);
  assert.match(classes, /_agentPanelClassBadgeIntent\(status, false\)/);
  assert.match(classes, /'agent-profile-chip agent-profile-chip-draft',[\s\S]*?'warning'/);

  assert.doesNotMatch(css, /^\.agent-profile-badge\s*\{[^}]*(?:font-size|padding|border-radius|background|color|font-weight|border:)/ms);
  assert.doesNotMatch(css, /^\.agent-profile-chip(?:,|\s*\{)/m);
  assert.doesNotMatch(css, /^\.agent-class-badge\s*\{/m);
  assert.doesNotMatch(css, /^\.agent-class-compact-chips span\s*\{/m);
});

test('History and event markers use canonical semantic badges', () => {
  const history = source('static/js/history.js');
  const events = source('static/js/events.js');
  const css = source('static/styles/board-panels.css');

  assert.match(history, /function _ahMetadataBadgeClass[\s\S]*?ui-badge ui-badge--compact ui-badge--/);
  assert.match(history, /_ahStatusBadgeIntent\(status\)/);
  assert.match(history, /_ahOutcomeBadgeIntent\(outcome\)/);
  assert.match(history, /_ahMetadataBadgeClass\('ah-type-badge', 'neutral'\)/);
  assert.match(events, /events-entry-dismissed-badge ui-badge ui-badge--compact ui-badge--neutral/);

  assert.doesNotMatch(css, /^\.ah-type-badge\s*\{[^}]*(?:font-size|padding|border-radius|background|color):/ms);
  assert.doesNotMatch(css, /^\.ah-badge-(?:active|removed|merged)\s*\{/m);
  assert.doesNotMatch(css, /^\.ah-outcome-(?:done|ready|answered|blocked|error)\s*\{/m);
  assert.doesNotMatch(css, /^\.events-entry-badge\s*\{[^}]*(?:padding|border-radius|font-size|line-height):/ms);
  assert.doesNotMatch(css, /^\.events-entry-dismissed-badge\s*\{/m);
});

test('remaining count annotations use canonical density and semantic intent', () => {
  const gridMain = source('static/js/grid/main.js');
  const groupTabs = source('static/js/grid/group-tabs.js');
  const agentCard = source('static/js/grid/agent-card.js');
  const render = source('static/js/render.js');
  const detail = source('static/js/agent-detail.js');
  const chat = source('static/js/chat.js');
  const actions = source('static/js/actions.js');
  const events = source('static/js/events.js');
  const mission = source('static/js/mission_control.js');
  const initiatives = source('static/js/initiatives.js');
  const hierarchy = source('static/js/agent-panel/hierarchy.js');
  const engineer = source('static/js/agent-panel/engineer.js');
  const worker = source('static/js/agent-panel/worker.js');
  const agentEvents = source('static/js/agent-panel/events.js');
  const architect = source('static/js/agent-panel/architect.js');
  const legacy = source('static/js/agent-panel/legacy-engineer.js');
  const thinking = source('static/js/thinking.js');
  const schedules = source('static/js/board/schedules.js');
  const behavior = source('static/js/behavior_overlay.js');

  assert.match(gridMain, /group-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(gridMain, /drawer-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(groupTabs, /agent-group-tab-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count/);
  assert.match(groupTabs, /agent-group-quick-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(agentCard, /agent-card-state-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count/);
  assert.match(agentCard, /agent-card-state-more ui-badge ui-badge--micro ui-badge--neutral ui-badge--count/);
  assert.match(agentCard, /cell-term-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count/);
  assert.match(render, /drawer-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(detail, /detail-section-count ui-badge ui-badge--micro ui-badge--warning ui-badge--count/);
  assert.match(chat, /chat-badge-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(chat, /chat-badge-ack' \? 'ui-badge--warning'/);
  assert.match(chat, /chat-badge-pending' \? 'ui-badge--accent'/);
  assert.match(actions, /tpled-transitions-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(events, /events-attention-count ui-badge ui-badge--compact ui-badge--danger ui-badge--count/);
  assert.match(mission, /mc-section-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(initiatives, /initiative-column-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(initiatives, /initiative-secondary-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count/);
  assert.match(initiatives, /area-filter-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(initiatives, /planning-tab-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count/);
  assert.match(initiatives, /initiative-total ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(thinking, /thinking-tab-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count/);
  assert.match(thinking, /thinking-total ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(mission, /mc-total ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(schedules, /board-schedule-runs ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(behavior, /behavior-overlay-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(hierarchy, /agent-panel-hierarchy-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count/);
  assert.match(engineer, /agent-panel-worklog-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(worker, /agent-panel-event-section-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(agentEvents, /agent-panel-event-section-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(architect, /agent-panel-message-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(legacy, /engineers-roster-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count/);
  assert.match(legacy, /agent-panel-health-total ui-badge ui-badge--compact ui-badge--danger ui-badge--count/);
  assert.match(legacy, /ui-badge ui-badge--compact ui-badge--count ' \+ _agentPanelHealthBadgeIntent/);
});

test('count geometry does not drift back into feature styles', () => {
  const tokens = source('static/styles/tokens-base.css');
  const grid = source('static/styles/workspace-grid.css');
  const board = source('static/styles/board-panels.css');
  const agent = source('static/styles/agent-panel.css');
  const features = source('static/styles/feature-panels.css');

  assert.doesNotMatch(tokens, /^\.group-count\s*\{/m);
  assert.doesNotMatch(grid, /^\.(?:agent-group-tab-count|cell-term-count|detail-section-count|drawer-count)\s*\{[^}]*(?:font-size|padding|border-radius|background|color):/ms);
  assert.doesNotMatch(board, /^\.(?:chat-badge|tpled-transitions-count|events-attention-count)\s*\{[^}]*(?:font-size|padding|border-radius|background|color):/ms);
  assert.doesNotMatch(agent, /^\.(?:engineers-roster-count|agent-panel-hierarchy-count|architect-roster-section-count|architect-decision-group-count|agent-panel-event-section-count|agent-panel-worklog-count|agent-panel-message-count|agent-panel-stream-summary-count|agent-panel-health-total)\s*\{/m);
  assert.doesNotMatch(agent, /^\.agent-panel-health-pill\s*\{[^}]*(?:font-size|padding|border-radius|background|color):/ms);
  assert.doesNotMatch(features, /^\.(?:mc-section-count|mc-total|initiative-column-count|initiative-total|area-filter-count|planning-tab-count|thinking-tab-count|thinking-total)\s*\{[^}]*(?:font-size|padding|border-radius|background|color):/ms);
});

test('status bar segments separate passive metadata from native actions', () => {
  const html = source('webview.html');
  const css = source('static/styles/workspace-shell.css');
  const status = source('static/js/status_bar.js');
  const relay = source('static/js/relay_status.js');

  assert.match(html, /daemon-status-indicator" class="[^"]*statusbar-segment statusbar-segment--passive/);
  assert.match(html, /statusbar-workload"[\s\S]*?statusbar-segment--passive/);
  assert.match(html, /<button id="statusbar-tasks"[\s\S]*?statusbar-segment--action[\s\S]*?type="button"/);
  assert.match(html, /<button id="statusbar-attention"[\s\S]*?statusbar-segment--action[\s\S]*?type="button"/);
  assert.doesNotMatch(html, /statusBarChipKeydown/);
  assert.match(relay, /relay-status statusbar-segment statusbar-segment--passive/);
  assert.match(status, /statusbar-segment--' \+ \(level === 'warn' \? 'warning' : level\)/);

  assert.match(css, /^\.statusbar-segment\s*\{[^}]*min-height:\s*24px;[^}]*border-radius:\s*0;/ms);
  assert.match(css, /^\.statusbar-segment--action:hover,[\s\S]*?\.statusbar-segment--action:focus-visible/m);
  assert.doesNotMatch(css, /^\.statusbar-chip\s*\{/m);
});

test('badge geometry does not drift back into feature styles', () => {
  const grid = source('static/styles/workspace-grid.css');
  const agent = source('static/styles/agent-panel.css');
  const features = source('static/styles/feature-panels.css');

  assert.doesNotMatch(grid, /^\.cell-(?:engineer|architect|worker|agent|dismissed)-badge\s*\{[^}]*(?:font-size|border-radius|padding|line-height):/ms);
  assert.doesNotMatch(agent, /^\.agent-panel-badge\s*\{[^}]*(?:font-size|padding|border-radius|font-weight):/ms);
  assert.doesNotMatch(features, /^\.health-pill\s*\{[^}]*(?:display|border|border-radius|padding|background):/ms);
  assert.doesNotMatch(features, /^\.health-pill-(?:warn|danger|neutral)\s*\{/m);
  assert.doesNotMatch(features, /^\.health-metrics-(?:overhead|history-meta) span\s*\{/m);
});
