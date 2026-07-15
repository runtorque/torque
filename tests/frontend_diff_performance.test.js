const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'js', 'diff.js'),
  'utf8',
);

function makeContext() {
  const context = {
    console,
    state: { agents: {}, board_tasks: {} },
    esc(value) {
      return String(value == null ? '' : value);
    },
    send() {},
    document: {
      body: { classList: { toggle() {} } },
      getElementById() { return null; },
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  context.renderDiffView = function() {};
  return context;
}

function diffFile(filePath, lineCount) {
  const lines = [];
  for (let index = 0; index < lineCount; index++) {
    lines.push({ type: index % 2 ? 'add' : 'context', text: `line ${index}` });
  }
  return {
    path: filePath,
    status: 'modified',
    hunks: [{ header: '@@ -1 +1 @@', lines }],
  };
}

function test(name, fn) {
  try {
    fn();
    process.stdout.write(`ok - ${name}\n`);
  } catch (error) {
    process.stderr.write(`not ok - ${name}\n${error.stack}\n`);
    process.exitCode = 1;
  }
}

test('large multi-file diffs start collapsed with one reviewable file open', () => {
  const context = makeContext();
  context._diffViewOpen = true;
  context._diffViewAgentId = 'worker-1';
  const files = [];
  for (let index = 0; index < 13; index++) files.push(diffFile(`file-${index}.js`, 10));

  context.diffReceiveFull({ id: 'worker-1', files });

  assert.strictEqual(context._diffCollapseAllFiles, true);
  assert.strictEqual(context._isDiffFileCollapsed('file-0.js'), false);
  assert.strictEqual(context._diffCollapsedCount(files), 12);
});

test('very large single files stay collapsed until explicitly opened', () => {
  const context = makeContext();
  context._diffViewOpen = true;
  context._diffViewAgentId = 'worker-1';
  const files = [diffFile('huge.js', 1000)];

  context.diffReceiveFull({ id: 'worker-1', files });

  assert.strictEqual(context._isDiffFileCollapsed('huge.js'), true);
});

test('expanded files render lines in bounded chunks', () => {
  const context = makeContext();
  const file = diffFile('huge.js', 1000);
  context._diffCollapseAllFiles = false;

  const first = context._renderDiffFile(file);
  assert.strictEqual((first.match(/<div class="diff-line /g) || []).length, 400);
  assert.match(first, /600 remaining/);

  context.diffShowMoreLines('huge.js');
  const second = context._renderDiffFile(file);
  assert.strictEqual((second.match(/<div class="diff-line /g) || []).length, 800);
  assert.match(second, /200 remaining/);
});

test('small diffs preserve the existing fully expanded behavior', () => {
  const context = makeContext();
  context._diffViewOpen = true;
  context._diffViewAgentId = 'worker-1';
  const files = [diffFile('small.js', 50)];

  context.diffReceiveFull({ id: 'worker-1', files });

  assert.strictEqual(context._diffCollapseAllFiles, false);
  assert.strictEqual(context._isDiffFileCollapsed('small.js'), false);
  const html = context._renderDiffFile(files[0]);
  assert.strictEqual((html.match(/<div class="diff-line /g) || []).length, 50);
  assert.doesNotMatch(html, /diff-file-load-more/);
});
