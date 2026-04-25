const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function buildContext(agents) {
  const hintEl = { style: { display: 'none' }, textContent: '' };
  const ctx = vm.createContext({
    document: {
      getElementById(id) {
        if (id === 'task-specialization-hint') return hintEl;
        return null;
      },
    },
    state: { agents },
    console,
    esc: (s) => String(s),
    labelColor: () => '#aaa',
    displayLabel: (s) => s,
    isSystemLabel: (s) => String(s || '').startsWith('loom:'),
  });
  const src = fs.readFileSync(
    path.join(repoRoot, 'static/js/modals/task-modal.js'),
    'utf8',
  );
  vm.runInContext(src, ctx);
  return { ctx, hintEl };
}

test('best-match picks the engineer with the most overlapping specializations', () => {
  const agents = {
    'eng-alice': {
      id: 'eng-alice',
      name: 'Alice',
      kind: 'engineer',
      engineer_specializations: ['ui-ux', 'security-focus'],
    },
    'eng-bob': {
      id: 'eng-bob',
      name: 'Bob',
      kind: 'engineer',
      engineer_specializations: ['events'],
    },
  };
  const { ctx } = buildContext(agents);
  const alice = ctx._taskSpecializationBestMatch(['ui-ux']);
  assert.equal(alice && alice.name, 'Alice');
  const bob = ctx._taskSpecializationBestMatch(['events']);
  assert.equal(bob && bob.name, 'Bob');
  const none = ctx._taskSpecializationBestMatch(['unknown-label']);
  assert.equal(none, null);
});

test('best-match skips non-engineer agents', () => {
  const agents = {
    'worker-a': {
      id: 'worker-a',
      name: 'Worker',
      kind: 'worker',
      engineer_specializations: ['ui-ux'],
    },
    'eng-bob': {
      id: 'eng-bob',
      name: 'Bob',
      kind: 'engineer',
      engineer_specializations: ['events'],
    },
  };
  const { ctx } = buildContext(agents);
  const match = ctx._taskSpecializationBestMatch(['ui-ux']);
  assert.equal(match, null);
});

test('render hides hint when no labels match any engineer specialization', () => {
  const agents = {
    'eng-alice': {
      id: 'eng-alice',
      name: 'Alice',
      kind: 'engineer',
      engineer_specializations: ['ui-ux'],
    },
  };
  const { ctx, hintEl } = buildContext(agents);
  vm.runInContext('_taskLabels = ["other-label"]; _taskSystemLabels = [];', ctx);
  vm.runInContext('_renderTaskSpecializationHint();', ctx);
  assert.equal(hintEl.style.display, 'none');
  assert.equal(hintEl.textContent, '');
});

test('render shows hint naming the best-match engineer', () => {
  const agents = {
    'eng-alice': {
      id: 'eng-alice',
      name: 'Alice',
      kind: 'engineer',
      engineer_specializations: ['ui-ux'],
    },
  };
  const { ctx, hintEl } = buildContext(agents);
  vm.runInContext('_taskLabels = ["ui-ux"]; _taskSystemLabels = [];', ctx);
  vm.runInContext('_renderTaskSpecializationHint();', ctx);
  assert.notEqual(hintEl.style.display, 'none');
  assert.match(hintEl.textContent, /best-match: Alice/);
});
