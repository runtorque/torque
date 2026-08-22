export type AgentKind = 'architect' | 'engineer' | 'worker' | 'terminal';

export interface AgentViewModel {
  id: string;
  name: string;
  group: string;
  kind: AgentKind;
  cellType: 'agent' | 'terminal';
  status: string;
  activity: string;
  activityDetail: string;
  currentTaskId: string;
  currentPath: string;
  currentBranch: string;
  sessionId: string;
  ownerEngineerId: string;
  hiredByArchitectId: string;
  parentId: string;
  provider: string;
  role: string;
  needsAttention: boolean;
  worktreePath: string;
  worktreeBranch: string;
  worktreeDirty: boolean;
  worktreeAhead: number;
  worktreeBehind: number;
  contextPercent: number;
  raw: Record<string, unknown>;
}

export interface AgentHierarchy {
  architects: AgentViewModel[];
  userEngineers: AgentViewModel[];
  engineersByArchitect: Record<string, AgentViewModel[]>;
  workersByEngineer: Record<string, AgentViewModel[]>;
  userWorkers: AgentViewModel[];
  terminalsByParent: Record<string, AgentViewModel[]>;
  looseTerminals: AgentViewModel[];
  all: AgentViewModel[];
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function normalizedKind(source: Record<string, unknown>): AgentKind {
  if (source.cell_type === 'terminal') return 'terminal';
  const kind = text(source.kind).toLocaleLowerCase();
  return kind === 'architect' || kind === 'engineer' || kind === 'worker'
    ? kind
    : 'worker';
}

export function toAgentViewModel(id: string, value: unknown): AgentViewModel {
  const source = record(value);
  const context = record(source.context_window);
  return {
    id,
    name: text(source.name) || id,
    group: text(source.group),
    kind: normalizedKind(source),
    cellType: source.cell_type === 'terminal' ? 'terminal' : 'agent',
    status: text(source.status) || 'stopped',
    activity: text(source.activity),
    activityDetail: text(source.activity_detail),
    currentTaskId: text(source.current_task_id),
    currentPath: text(source.current_path) || text(source.directory),
    currentBranch: text(source.current_branch),
    sessionId: text(source.session_id),
    ownerEngineerId: text(source.owner_engineer_id),
    hiredByArchitectId: text(source.hired_by_architect_id),
    parentId: text(source.parent_id),
    provider: text(source.agent_type) || text(source.profile),
    role: text(source.role),
    needsAttention: Boolean(source.needs_attention),
    worktreePath: text(source.worktree_path),
    worktreeBranch: text(source.worktree_branch),
    worktreeDirty: Boolean(source.worktree_dirty),
    worktreeAhead: numberValue(source.worktree_ahead),
    worktreeBehind: numberValue(source.worktree_behind),
    contextPercent: numberValue(context.used_percentage ?? context.percent ?? context.percentage),
    raw: source,
  };
}

function byName(a: AgentViewModel, b: AgentViewModel): number {
  return a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
}

export function buildAgentHierarchy(
  records: Record<string, unknown>,
  group: string,
): AgentHierarchy {
  const all = Object.entries(records)
    .map(([id, value]) => toAgentViewModel(id, value))
    .filter((agent) => !group || agent.group === group)
    .filter((agent) => !Number(agent.raw.deleted_at ?? 0))
    .sort(byName);
  const architects = all.filter((agent) => agent.kind === 'architect');
  const engineers = all.filter((agent) => agent.kind === 'engineer');
  const workers = all.filter((agent) => agent.kind === 'worker');
  const terminals = all.filter((agent) => agent.kind === 'terminal');
  const engineersByArchitect: Record<string, AgentViewModel[]> = {};
  const workersByEngineer: Record<string, AgentViewModel[]> = {};
  const terminalsByParent: Record<string, AgentViewModel[]> = {};

  for (const engineer of engineers) {
    if (!engineer.hiredByArchitectId) continue;
    (engineersByArchitect[engineer.hiredByArchitectId] ??= []).push(engineer);
  }
  for (const worker of workers) {
    if (!worker.ownerEngineerId) continue;
    (workersByEngineer[worker.ownerEngineerId] ??= []).push(worker);
  }
  for (const terminal of terminals) {
    if (!terminal.parentId) continue;
    (terminalsByParent[terminal.parentId] ??= []).push(terminal);
  }

  return {
    architects,
    userEngineers: engineers.filter((agent) => !agent.hiredByArchitectId),
    engineersByArchitect,
    workersByEngineer,
    userWorkers: workers.filter((agent) => !agent.ownerEngineerId),
    terminalsByParent,
    looseTerminals: terminals.filter((agent) => !agent.parentId),
    all,
  };
}

export function agentStatusLabel(agent: AgentViewModel): string {
  if (agent.needsAttention) return 'needs attention';
  if (agent.activityDetail) return agent.activityDetail;
  if (agent.activity) return agent.activity;
  return agent.status;
}
