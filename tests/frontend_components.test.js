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
