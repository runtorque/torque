const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..');

function source(file) {
  return fs.readFileSync(path.join(repoRoot, file), 'utf8');
}

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
