const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadScript(context, relPath) {
  const source = fs.readFileSync(path.join(repoRoot, relPath), 'utf8');
  vm.runInContext(source, context, { filename: relPath });
}

function createHarness() {
  const sandbox = {
    console,
    Date,
    state: {
      active_session_id: '',
      children: {},
      agents: {},
      board_tasks: {},
      group_settings: {},
      engineer_settings: {},
      agent_digest_settings: {},
    },
    selectedAgentId: '',
    focusedItemId: '',
    esc(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    },
    formatCode(value) { return String(value == null ? '' : value); },
    _agentCardTimestampSeconds(value) {
      if (!value) return 0;
      if (typeof value === 'number') return value > 1e12 ? value / 1000 : value;
      const parsed = Date.parse(value);
      return Number.isFinite(parsed) ? parsed / 1000 : 0;
    },
    agentStatusClass(agent) { return (agent && agent.status) === 'running' ? 'working' : 'idle'; },
    _getAgentDoneFlourish() { return null; },
    _getAgentTask() { return null; },
    _embeddedRuntimeEnabled() { return false; },
    _formatDisplayPath(value) { return value || ''; },
    _workerBranchLabel(agent) { return (agent && (agent.worktree_branch || agent.current_branch)) || ''; },
    _workerDiffLabel() { return ''; },
    _workersForEngineer() { return []; },
    _engineerQueueDepth() { return 0; },
    _architectEngineersForCard() { return []; },
    _architectPendingAskTasks() { return []; },
    _architectDecisionListForCard() { return []; },
    _architectJournalDecisionEntriesForCard() { return []; },
    _architectLatestJournalDecisionTs() { return 0; },
    _isLifecycleDismissedAgent() { return false; },
    _agentDismissedAt() { return 0; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/grid/agent-card.js');
  return context;
}

test('agent grid card promotes effective Product Manager Agent Class identity over Architect base kind', () => {
  const context = createHarness();
  const html = vm.runInContext(`renderAgentCell({
    id: 'blueprint',
    name: 'Blueprint',
    kind: 'architect',
    cell_type: 'agent',
    group: 'alpha',
    status: 'running',
    effective_agent_class_id: 'product-manager',
    effective_agent_class_version: '2',
    effective_agent_class_snapshot: {
      id: 'product-manager',
      version: '2',
      base_kind: 'architect',
      display_name: 'Product Manager',
      primary_identity_label: 'Product Manager',
      secondary_base_kind_label: 'Architect-derived',
      status: 'draft'
    }
  })`, context);

  assert.match(html, /cell-name[^>]*>Product Manager</);
  assert.match(html, /cell-agent-class-badge[^>]*>Product Manager</);
  assert.match(html, /Base kind: Architect/);
  assert.match(html, /Secondary metadata: Architect-derived/);
  assert.doesNotMatch(html, /agent-card-kind[^>]*>Architect</);
});

test('agent grid card preserves default Architect identity when no non-default Agent Class is effective', () => {
  const context = createHarness();
  const html = vm.runInContext(`renderAgentCell({
    id: 'blueprint',
    name: 'Blueprint',
    kind: 'architect',
    cell_type: 'agent',
    group: 'alpha',
    status: 'running',
    effective_agent_class_id: 'default-architect',
    effective_agent_class_version: '1',
    effective_agent_class_snapshot: {
      id: 'default-architect',
      version: '1',
      base_kind: 'architect',
      display_name: 'Default Architect',
      primary_identity_label: 'Default Architect',
      secondary_base_kind_label: 'Architect',
      status: 'full'
    }
  })`, context);

  assert.match(html, /cell-name[^>]*>Blueprint</);
  assert.match(html, /agent-card-kind[^>]*>Architect</);
  assert.doesNotMatch(html, /cell-agent-class-badge/);
});
