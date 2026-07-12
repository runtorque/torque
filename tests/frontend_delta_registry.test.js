const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'js', 'ws.js'),
  'utf8',
);

const targetedOps = [
  'agent_message_history_append',
  'context_update',
  'provider_usage',
  'relay_config',
  'relay_connection',
  'runtime',
  'worktree_merge_progress',
];

test('targeted delta operations use one apply/invalidation registry', () => {
  const applyStart = source.indexOf('function _applyDelta(ops)');
  const applyEnd = source.indexOf('function _rebuildChildren()', applyStart);
  const invalidationStart = source.indexOf('function _deltaSurfaceInvalidations');
  const invalidationEnd = source.indexOf(
    'function _taskNextFromDelta',
    invalidationStart,
  );
  assert.ok(applyStart > 0 && applyEnd > applyStart);
  assert.ok(invalidationStart > 0 && invalidationEnd > invalidationStart);

  const applySwitch = source.slice(applyStart, applyEnd);
  const invalidationSwitch = source.slice(invalidationStart, invalidationEnd);
  for (const op of targetedOps) {
    assert.match(
      source,
      new RegExp(`_registerDeltaOperations\\('${op}'`),
      `${op} should be registered`,
    );
    assert.doesNotMatch(
      applySwitch,
      new RegExp(`case ['"]${op}['"]`),
      `${op} apply logic must not drift back into the switch`,
    );
    assert.doesNotMatch(
      invalidationSwitch,
      new RegExp(`case ['"]${op}['"]`),
      `${op} invalidation must not drift back into the switch`,
    );
  }
});

test('every registered targeted operation declares apply and invalidation', () => {
  for (const op of targetedOps) {
    const start = source.indexOf(`_registerDeltaOperations('${op}'`);
    const end = source.indexOf('\n});', start);
    assert.ok(start > 0 && end > start, `${op} registration should be bounded`);
    const registration = source.slice(start, end);
    assert.match(registration, /invalidate\s*:/, `${op} needs invalidation policy`);
    assert.match(registration, /apply\s*:/, `${op} needs apply policy`);
  }
});
