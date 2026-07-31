'use strict';

/* Regression: the DM composer and xterm share #terminal-workspace, but only
 * focus inside .terminal-stage may draw the terminal's active border. */

const test = require('node:test');
const assert = require('node:assert/strict');
const { appStylesheetSource } = require('./frontend_stylesheet_loader');

function terminalFocusRule(css) {
  return css.match(/^body\.runtime-embedded\s+[^\{]*:focus-within\s+\.terminal-surface\s*\{[^}]*\}/m);
}

test('composer focus cannot apply embedded terminal focused styling', () => {
  const rule = terminalFocusRule(appStylesheetSource());

  assert.ok(rule, 'embedded terminal focused styling exists');
  assert.match(rule[0], /\.terminal-stage:focus-within\s+\.terminal-surface/,
    'the terminal stage, not the shared workspace, is the focus-within scope');
  assert.doesNotMatch(rule[0], /#terminal-workspace:focus-within/,
    'the composer remains outside the terminal focused-style scope');
});
