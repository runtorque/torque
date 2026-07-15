const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..');

function source(file) {
  return fs.readFileSync(path.join(repoRoot, file), 'utf8');
}

test('shared form controls define default, compact, and invalid states', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.form-control,\s*:where\(input:not\([\s\S]*?select,\s*textarea\s*\{[^}]*border:\s*var\(--control-border\);[^}]*border-radius:\s*var\(--radius\);[^}]*background:\s*var\(--bg-inset\);[^}]*font-size:\s*var\(--control-font-size-sm\);/s);
  assert.match(css, /\.form-control:not\(textarea\),\s*:where\(input:not\([\s\S]*?select\s*\{[^}]*min-height:\s*var\(--control-height-md\);[^}]*padding:\s*0 var\(--control-padding-x-sm\);/s);
  assert.match(css, /textarea\.form-control,\s*textarea\s*\{[^}]*min-height:\s*48px;[^}]*padding:\s*6px var\(--control-padding-x-sm\);[^}]*resize:\s*vertical;/s);
  assert.match(css, /\.form-control-sm,[\s\S]*?\.events-resolve-textarea\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
  assert.match(css, /\.form-control\.is-invalid,[\s\S]*?textarea\[aria-invalid="true"\]\s*\{[^}]*border-color:\s*var\(--red\);/s);
  assert.match(css, /\.form-error\s*\{[^}]*color:\s*var\(--red\);[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
});

test('form primitives live in the shared component stylesheet', () => {
  const tokens = source('static/styles/tokens-base.css');
  const modals = source('static/styles/modals.css');
  const features = source('static/styles/feature-panels.css');
  const agent = source('static/styles/agent-panel.css');
  const thinkingRule = features.match(/\.thinking-form input,\s*\.thinking-form select,\s*\.thinking-form textarea\s*\{([^}]*)\}/s);
  const agentFilterRule = agent.match(/\.agent-panel-mcp-filters input,\s*\.agent-panel-mcp-filters select\s*\{([^}]*)\}/s);

  assert.doesNotMatch(tokens, /^input, select, textarea\s*\{/m);
  assert.doesNotMatch(modals, /^textarea\s*\{/m);
  assert.doesNotMatch(features, /^\.initiative-form input,\s*$/m);
  assert.ok(thinkingRule, 'Thinking retains only its local min-width layout rule');
  assert.doesNotMatch(thinkingRule[1], /border|background|padding|font-size/);
  assert.ok(agentFilterRule, 'Agent filters retain only surface layout and background');
  assert.doesNotMatch(agentFilterRule[1], /border|padding|font-size/);
});

test('shared button component defines canonical intents and sizes', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.btn,\s*\.btn-primary,[\s\S]*?\.btn-rebase\s*\{[^}]*min-height:\s*var\(--control-height-md\);[^}]*padding:\s*0 var\(--control-padding-x-md\);[^}]*border-radius:\s*var\(--radius-sm\);/s);
  assert.match(css, /\.btn,\s*\.btn-secondary\s*\{[^}]*background:\s*var\(--bg-cell\);[^}]*border-color:\s*var\(--border\);/s);
  assert.match(css, /\.btn-primary\s*\{[^}]*background:\s*var\(--accent\);[^}]*border-color:\s*var\(--accent\);/s);
  assert.match(css, /\.btn-quiet,\s*\.btn-cancel\s*\{[^}]*background:\s*transparent;[^}]*border-color:\s*transparent;/s);
  assert.match(css, /\.btn-danger\s*\{[^}]*background:\s*var\(--red\);[^}]*border-color:\s*var\(--red\);/s);
  assert.match(css, /\.btn-success,\s*\.btn-green\s*\{[^}]*background:\s*var\(--green\);[^}]*border-color:\s*var\(--green\);/s);
  assert.match(css, /\.btn-warning,\s*\.btn-rebase\s*\{[^}]*color:\s*var\(--amber\);/s);
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

  assert.match(css, /\.ui-tab--contained,\s*\.planning-tab,\s*\.thinking-tab,\s*\.agent-panel-events-subtab\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*padding:\s*0 var\(--control-padding-x-sm\);[^}]*border-radius:\s*var\(--radius-sm\);/s);
  assert.match(css, /\.planning-tab\.active,[\s\S]*?\.agent-panel-events-subtab\[aria-selected="true"\]\s*\{[^}]*border-color:\s*var\(--accent\);[^}]*background:\s*var\(--accent-soft\);/s);
  assert.match(css, /\.ui-tab--underline,\s*\.agent-panel-tab,\s*\.gs-subtab,\s*\.board-lane-tab\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*border-bottom:\s*2px solid transparent;[^}]*border-radius:\s*0;/s);
  assert.match(css, /\.agent-panel-tab\.active,[\s\S]*?\.board-lane-tab\[aria-selected="true"\]\s*\{[^}]*color:\s*var\(--accent\);[^}]*border-bottom-color:\s*var\(--accent\);/s);
  assert.match(css, /\.ui-tabs--contained,\s*\.agent-panel-events-subtabs\s*\{[^}]*border-radius:\s*var\(--radius-sm\);/s);
});

test('feature stylesheets do not redefine shared tab visual primitives', () => {
  const modals = source('static/styles/modals.css');
  const features = source('static/styles/feature-panels.css');
  const agent = source('static/styles/agent-panel.css');
  const board = source('static/styles/board-panels.css');

  assert.doesNotMatch(features, /^\.(planning|thinking)-tab\s*\{/m);
  assert.doesNotMatch(agent, /^\.agent-panel-events-subtab\s*\{/m);
  assert.doesNotMatch(modals, /^\.gs-subtab\s*\{/m);
  assert.doesNotMatch(board, /^\.board-lane-tab\s*\{[^}]*border-bottom:/ms);
  assert.doesNotMatch(features, /^\.(planning|thinking)-tab[^\{]*\{[^}]*border-radius:\s*999px/ms);
  assert.doesNotMatch(agent, /^\.agent-panel-events-subtabs?[^\{]*\{[^}]*border-radius:\s*999px/ms);
});

test('shared segmented controls define one compact selected-state primitive', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.segmented-control,\s*\.agent-view-toggle,\s*\.tpled-view-toggle,\s*\.log-viewer-targets,\s*\.schedule-type-toggle\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*border:\s*var\(--control-border\);[^}]*border-radius:\s*var\(--radius-sm\);/s);
  assert.match(css, /\.segmented-control__item,\s*\.agent-view-toggle-btn,\s*\.tpled-view-btn,\s*\.log-viewer-target,\s*\.schedule-type-btn\s*\{[^}]*min-height:\s*calc\(var\(--control-height-sm\) - 2px\);[^}]*border-radius:\s*0;[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
  assert.match(css, /\.segmented-control__item\.is-active,[\s\S]*?\.schedule-type-btn\[aria-pressed="true"\]\s*\{[^}]*background:\s*var\(--accent-soft\);[^}]*outline:\s*1px solid var\(--accent-muted\);[^}]*outline-offset:\s*-1px;/s);
});

test('segmented-control consumers expose their selected state', () => {
  const grid = source('static/js/grid/main.js');
  const canvas = source('static/js/canvas.js');
  const actions = source('static/js/actions.js');
  const templates = source('static/js/templates.js');
  const schedules = source('static/js/modals/schedules.js');
  const html = source('webview.html');

  assert.match(grid, /data-agent-view-toggle="grid" aria-pressed="/);
  assert.match(canvas, /setAttribute\('aria-pressed', target === active/);
  assert.match(actions, /role="group" aria-label="Action view"/);
  assert.match(actions, /aria-pressed="' \+ \(_tplPanelView === 'editor'/);
  assert.match(templates, /role="tablist" aria-label="Library sections"/);
  assert.match(templates, /role="tab" class="tpled-view-btn[\s\S]*?aria-selected=/);
  assert.match(html, /class="schedule-type-toggle" role="group" aria-label="Schedule type"/);
  assert.match(schedules, /setAttribute\('aria-pressed', type === 'recurring'/);
});

test('segmented-control visual primitives do not drift back into feature styles', () => {
  const desktop = source('static/styles/desktop-features.css');
  const board = source('static/styles/board-panels.css');

  assert.doesNotMatch(desktop, /^\.log-viewer-targets?\s*\{/m);
  assert.doesNotMatch(desktop, /^\.agent-view-toggle-btn\s*\{[^}]*background:/ms);
  assert.doesNotMatch(board, /^\.tpled-view-btn\s*\{/m);
  assert.doesNotMatch(board, /^\.schedule-type-btn\s*\{[^}]*background:/ms);
});

test('shared filter chips and presets use compact rectangular action geometry', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.filter-chip,\s*\.preset-button,[\s\S]*?\.schedule-preset\s*\{[^}]*min-height:\s*var\(--control-height-sm\);[^}]*padding:\s*0 var\(--control-padding-x-sm\);[^}]*border-radius:\s*var\(--radius-sm\);[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
  assert.match(css, /\.filter-chip\.active,[\s\S]*?\.initiative-secondary-toggle\.active\s*\{[^}]*color:\s*var\(--accent\);[^}]*border-color:\s*var\(--accent-muted\);[^}]*background:\s*var\(--accent-soft\);/s);
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
});

test('shared cards define boundary, density, and interactive state primitives', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-card,\s*\.board-card,\s*\.context-card,\s*\.agent-panel-message-card,\s*\.agent-panel-stream-card\s*\{[^}]*border:\s*1px solid var\(--border\);[^}]*border-radius:\s*var\(--radius\);[^}]*background:\s*var\(--bg-cell\);[^}]*transition:/s);
  assert.match(css, /\.ui-card--compact,\s*\.agent-panel-message-card,\s*\.agent-panel-stream-card\s*\{[^}]*padding:\s*var\(--space-2\);/s);
  assert.match(css, /\.ui-card--comfortable,\s*\.context-card\s*\{[^}]*padding:\s*10px;/s);
  assert.match(css, /\.ui-card--interactive:hover,[\s\S]*?\.context-card:hover\s*\{[^}]*border-color:\s*var\(--border-strong\);[^}]*background:\s*var\(--bg-hover\);/s);
  assert.match(css, /\.ui-card\.is-selected,[\s\S]*?\.context-card\.selected\s*\{[^}]*border-color:\s*var\(--accent-muted\);[^}]*background:\s*color-mix\(in srgb, var\(--accent\) 8%, var\(--bg-cell\)\);/s);
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

  assert.match(css, /\.ui-panel-header,\s*\.tpled-header,\s*\.events-header,\s*\.agent-panel-header\s*\{[^}]*display:\s*flex;[^}]*gap:\s*var\(--space-2\) 10px;[^}]*padding:\s*var\(--space-2\) 10px;[^}]*border-bottom:\s*1px solid var\(--border\);/s);
  assert.match(css, /\.ui-panel-header__actions,[\s\S]*?\.agent-panel-header-right\s*\{[^}]*justify-content:\s*flex-end;[^}]*flex:\s*0 1 auto;[^}]*margin-left:\s*auto;[^}]*flex-wrap:\s*wrap;/s);
  assert.match(css, /\.tpled-header-controls\.ui-panel-header__actions\s*\{[^}]*flex:\s*0 1 auto;[^}]*justify-content:\s*flex-end;/s);
  assert.match(css, /\.ui-toolbar\s*\{[^}]*display:\s*flex;[^}]*align-items:\s*center;[^}]*flex-wrap:\s*wrap;[^}]*padding:\s*var\(--space-1\) var\(--space-2\);/s);
  assert.match(css, /\.ui-toolbar--bordered\s*\{[^}]*border-bottom:\s*1px solid var\(--border\);/s);
});

test('core panel-header and toolbar consumers opt into the canonical API', () => {
  const board = source('static/js/board/rendering.js');
  const events = source('static/js/events.js');
  const initiatives = source('static/js/initiatives.js');
  const thinking = source('static/js/thinking.js');
  const agent = source('static/js/agent_panel.js');
  const engineer = source('static/js/agent-panel/legacy-engineer.js');

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
});

test('panel-header geometry does not drift back into feature styles', () => {
  const board = source('static/styles/board-panels.css');
  const agent = source('static/styles/agent-panel.css');

  assert.doesNotMatch(board, /^\.tpled-header\s*\{/m);
  assert.doesNotMatch(board, /^\.events-header\s*\{/m);
  assert.doesNotMatch(board, /^\.board-search-bar\s*\{[^}]*(?:display|padding|border-bottom|flex-wrap):/ms);
  assert.doesNotMatch(agent, /^\.agent-panel-header\s*\{/m);
  assert.doesNotMatch(agent, /^\.agent-panel-header-(?:copy|right)\s*\{/m);
  assert.doesNotMatch(agent, /^\.agent-panel-(?:title|subtitle)\s*\{/m);
});

test('shared modals define raised size variants and structured regions', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-modal,\s*\.modal\s*\{[^}]*max-width:\s*360px;[^}]*border:\s*1px solid var\(--border-strong\);[^}]*border-radius:\s*var\(--radius-lg\);[^}]*background:\s*var\(--bg-raised\);[^}]*box-shadow:\s*var\(--shadow-float\);/s);
  assert.match(css, /\.ui-modal\.ui-modal--sm\s*\{[^}]*max-width:\s*360px;/s);
  assert.match(css, /\.ui-modal\.ui-modal--md\s*\{[^}]*max-width:\s*520px;/s);
  assert.match(css, /\.ui-modal\.ui-modal--lg\s*\{[^}]*max-width:\s*760px;/s);
  assert.match(css, /\.ui-modal__header\s*\{[^}]*padding:\s*14px 16px 10px;/s);
  assert.match(css, /\.ui-modal__body\s*\{[^}]*padding:\s*0 16px 16px;[^}]*overflow:\s*auto;/s);
  assert.match(css, /\.ui-modal--structured > \.ui-modal__footer\s*\{[^}]*padding:\s*12px 16px 14px;[^}]*border-top:\s*1px solid var\(--border\);/s);
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

  assert.doesNotMatch(css, /^\.modal\s*\{[^}]*(?:border-radius|background|box-shadow|max-width):/ms);
  assert.doesNotMatch(css, /^\.modal-actions\s*\{/m);
});

test('shared menus define one floating surface and compact item state grammar', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-popover,\s*#ctx-menu,\s*\.agent-group-quick-switcher,\s*\.board-filter-dropdown,\s*\.board-view-menu\s*\{[^}]*padding:\s*var\(--space-1\);[^}]*border:\s*1px solid var\(--border-strong\);[^}]*border-radius:\s*var\(--radius-lg\);[^}]*background:\s*var\(--bg-raised\);[^}]*box-shadow:\s*var\(--shadow-float\);[^}]*max-height:/s);
  assert.match(css, /\.ui-menu-item,\s*#ctx-menu button:not\(\.ctx-label\),[\s\S]*?\.board-view-menu-toggle\s*\{[^}]*min-height:\s*var\(--control-height-md\);[^}]*padding:\s*var\(--space-1\) var\(--space-2\);[^}]*border-radius:\s*var\(--radius\);[^}]*font-size:\s*var\(--control-font-size-xs\);/s);
  assert.match(css, /\.ui-menu-item\.is-selected,[\s\S]*?\.board-view-menu-toggle\.active\s*\{[^}]*border-color:\s*var\(--accent-muted\);[^}]*color:\s*var\(--accent\);[^}]*background:\s*var\(--accent-soft\);/s);
  assert.match(css, /\.ui-menu-item--danger:hover,[\s\S]*?#ctx-menu button\.danger:focus-visible\s*\{[^}]*color:\s*var\(--red\);[^}]*background:\s*color-mix\(in srgb, var\(--red\) 12%, var\(--bg-hover\)\);/s);
});

test('group and Board popovers opt into canonical markup and semantics', () => {
  const html = source('webview.html');
  const groups = source('static/js/grid/group-tabs.js');
  const commands = source('static/js/commands.js');
  const board = source('static/js/board/rendering.js');
  const filters = source('static/js/board/filters.js');
  const view = source('static/js/board/view-state.js');

  assert.match(html, /id="ctx-menu" class="ui-popover ui-menu" role="menu" aria-hidden="true"/);
  assert.match(groups, /aria-haspopup="menu" aria-expanded="false"/);
  assert.match(groups, /class="agent-group-quick-switcher ui-popover" role="dialog"/);
  assert.match(groups, /class="agent-group-quick-option ui-menu-item/);
  assert.match(commands, /role="menuitem" class="ui-menu-item/);
  assert.match(board, /aria-haspopup="dialog" aria-expanded=/);
  assert.match(filters, /className = 'board-filter-dropdown ui-popover'/);
  assert.match(filters, /className = 'board-filter-dropdown-item ui-menu-item'/);
  assert.match(view, /className = 'board-view-menu ui-popover'/);
  assert.match(view, /class="board-view-menu-toggle ui-menu-item/);
});

test('transient menus restore focus on Escape and support keyboard traversal', () => {
  const groups = source('static/js/grid/group-tabs.js');
  const commands = source('static/js/commands.js');
  const filters = source('static/js/board/filters.js');
  const view = source('static/js/board/view-state.js');

  assert.match(groups, /event\.key === 'Escape'[\s\S]*?closeAgentGroupQuickSwitcher\(true\)/);
  assert.match(commands, /function closeContextMenu\(options\)[\s\S]*?options\.restoreFocus !== false[\s\S]*?invoker\.focus\(\)/);
  assert.match(commands, /function contextMenuKeydown\(event\)[\s\S]*?ArrowDown[\s\S]*?ArrowUp[\s\S]*?items\[index\]\.focus\(\)/);
  assert.match(filters, /dd\.addEventListener\('keydown'[\s\S]*?e\.key !== 'Escape'[\s\S]*?_boardCloseFilterDropdown\(\{ restoreFocus: true \}\)/);
  assert.match(view, /menu\.addEventListener\('keydown'[\s\S]*?e\.key !== 'Escape'[\s\S]*?currentTrigger\.focus\(\)/);
});

test('menu geometry does not drift back into feature styles', () => {
  const grid = source('static/styles/workspace-grid.css');
  const board = source('static/styles/board-panels.css');
  const modals = source('static/styles/modals.css');

  assert.doesNotMatch(grid, /^\.agent-group-quick-switcher\s*\{[^}]*(?:padding|border-radius|background|box-shadow):/ms);
  assert.doesNotMatch(board, /^\.board-filter-dropdown\s*\{[^}]*(?:background|border-radius|box-shadow):/ms);
  assert.doesNotMatch(board, /^\.board-view-menu\s*\{[^}]*(?:padding|background|border-radius|box-shadow):/ms);
  assert.doesNotMatch(modals, /^#ctx-menu\s*\{[^}]*(?:padding|background|border-radius|box-shadow):/ms);
  assert.doesNotMatch(modals, /^#ctx-menu button\s*\{[^}]*(?:padding|font-size|border-radius|color):/ms);
});

test('shared semantic badges define density and intent variants', () => {
  const css = source('static/styles/components.css');

  assert.match(css, /\.ui-badge\s*\{[^}]*display:\s*inline-flex;[^}]*min-height:\s*18px;[^}]*padding:\s*1px 6px;[^}]*border:\s*1px solid var\(--border\);[^}]*border-radius:\s*999px;[^}]*font-size:\s*9px;/s);
  assert.match(css, /\.ui-badge--compact\s*\{[^}]*min-height:\s*14px;[^}]*font-size:\s*8px;/s);
  assert.match(css, /\.ui-badge--micro\s*\{[^}]*min-height:\s*12px;[^}]*padding:\s*0 4px;[^}]*font-size:\s*6\.5px;/s);
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
