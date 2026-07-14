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
