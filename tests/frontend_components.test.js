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

  assert.match(board, /class="filter-chip board-filter-btn[\s\S]*?aria-haspopup="true" aria-expanded=/);
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
