import type { UnknownRecord } from '../../protocol/types';

export interface BoardTask {
  id: string;
  task: string;
  description: string;
  group: string;
  lane: string;
  position: number;
  labels: string[];
  agentId: string;
  assignedEngineerId: string;
  assignedArchitectId: string;
  createdByArchitectId: string;
  createdByEngineerId: string;
  actionName: string;
  agentTemplate: string;
  dispatchState: string;
  healthState: string;
  status: string;
  parentTaskId: string;
  pipelineDepth: number;
  dependsOn: string[];
  scheduledAt: string;
  updatedAt: string;
  provider: string;
  externalId: string;
  externalUrl: string;
  artifacts: UnknownRecord[];
  attachments: UnknownRecord[];
  messages: UnknownRecord[];
  verificationState: string;
  boardSync: UnknownRecord;
  raw: UnknownRecord;
}

export interface BoardFilterState {
  search_query: string;
  quick_view: string;
  filter_labels: string[];
  filter_actions: string[];
  filter_agents: string[];
  filter_health: string[];
}

export const emptyBoardFilters: BoardFilterState = {
  search_query: '',
  quick_view: '',
  filter_labels: [],
  filter_actions: [],
  filter_agents: [],
  filter_health: [],
};

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function recordValue(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
}

function recordList(value: unknown): UnknownRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is UnknownRecord => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    : [];
}

export function normalizeTask(id: string, rawValue: unknown): BoardTask | null {
  const raw = recordValue(rawValue);
  const taskId = stringValue(raw.id) || id;
  const title = stringValue(raw.task);
  if (!taskId || !title) return null;
  return {
    id: taskId,
    task: title,
    description: stringValue(raw.description),
    group: stringValue(raw.group),
    lane: stringValue(raw.lane) || 'Backlog',
    position: typeof raw.position === 'number' ? raw.position : 0,
    labels: stringList(raw.labels),
    agentId: stringValue(raw.agent_id),
    assignedEngineerId: stringValue(raw.assigned_engineer_id),
    assignedArchitectId: stringValue(raw.assigned_architect_id),
    createdByArchitectId: stringValue(raw.created_by_architect_id),
    createdByEngineerId: stringValue(raw.created_by_engineer_id),
    actionName: stringValue(raw.action_name),
    agentTemplate: stringValue(raw.agent_template),
    dispatchState: stringValue(raw.dispatch_state),
    healthState: stringValue(raw.health_state) || 'healthy',
    status: stringValue(raw.status),
    parentTaskId: stringValue(raw.parent_task_id),
    pipelineDepth: typeof raw.pipeline_depth === 'number' ? raw.pipeline_depth : 0,
    dependsOn: stringList(raw.depends_on),
    scheduledAt: stringValue(raw.scheduled_at),
    updatedAt: stringValue(raw.updated_at) || stringValue(raw.created_at),
    provider: stringValue(raw.provider),
    externalId: stringValue(raw.external_id),
    externalUrl: stringValue(raw.external_url),
    artifacts: recordList(raw.artifacts),
    attachments: recordList(raw.attachments),
    messages: recordList(raw.messages),
    verificationState: stringValue(raw.verification_state),
    boardSync: recordValue(raw.board_sync),
    raw,
  };
}

export function normalizeTasks(records: Record<string, unknown>): BoardTask[] {
  return Object.entries(records)
    .map(([id, value]) => normalizeTask(id, value))
    .filter((task): task is BoardTask => task !== null);
}

/** Classic uses updated_at (created_at fallback), at an inclusive seven-day cutoff. */
export function staleCompletedTasks(tasks: BoardTask[], group: string, now: number): BoardTask[] {
  const cutoff = now - 7 * 24 * 60 * 60 * 1000;
  return tasks.filter((task) => {
    const timestamp = Date.parse(task.updatedAt);
    return task.group === group && task.lane === 'Done' && !task.labels.includes('torque:archived') && Number.isFinite(timestamp) && timestamp !== 0 && timestamp <= cutoff;
  }).sort((a, b) => Date.parse(a.updatedAt) - Date.parse(b.updatedAt));
}

export function normalizeFilters(value: unknown): BoardFilterState {
  const raw = recordValue(value);
  return {
    search_query: stringValue(raw.search_query),
    quick_view: stringValue(raw.quick_view),
    filter_labels: stringList(raw.filter_labels),
    filter_actions: stringList(raw.filter_actions),
    filter_agents: stringList(raw.filter_agents),
    filter_health: stringList(raw.filter_health),
  };
}

export function taskMatchesFilters(task: BoardTask, filters: BoardFilterState): boolean {
  const query = filters.search_query.trim().toLocaleLowerCase();
  if (query) {
    const haystack = [task.id, task.task, task.description, task.actionName, ...task.labels]
      .join('\n')
      .toLocaleLowerCase();
    if (!haystack.includes(query)) return false;
  }
  if (filters.filter_labels.length && !filters.filter_labels.some((label) => task.labels.includes(label))) return false;
  if (filters.filter_actions.length && !filters.filter_actions.includes(task.actionName)) return false;
  if (filters.filter_agents.length && !filters.filter_agents.includes(task.agentId || task.assignedEngineerId)) return false;
  if (filters.filter_health.length && !filters.filter_health.includes(task.healthState)) return false;
  if (filters.quick_view === 'mine' && !task.agentId && !task.assignedEngineerId) return false;
  if (filters.quick_view === 'scheduled' && !task.scheduledAt) return false;
  if (filters.quick_view === 'blocked' && task.healthState !== 'blocked' && !task.labels.includes('torque:blocked')) return false;
  if (filters.quick_view === 'unassigned' && (task.agentId || task.assignedEngineerId)) return false;
  return true;
}

export function orderedLaneTasks(
  tasks: BoardTask[],
  lane: string,
  sortMode: unknown,
): BoardTask[] {
  const laneTasks = tasks.filter((task) => task.lane === lane);
  if (sortMode === 'newest' || sortMode === 'oldest') {
    const direction = sortMode === 'newest' ? -1 : 1;
    return laneTasks.sort((a, b) => direction * a.updatedAt.localeCompare(b.updatedAt));
  }
  if (sortMode === 'due') {
    return laneTasks.sort((a, b) => (a.scheduledAt || '9999').localeCompare(b.scheduledAt || '9999'));
  }
  return laneTasks.sort((a, b) => a.position - b.position || a.task.localeCompare(b.task));
}

export function visibleHierarchy(tasks: BoardTask[], collapsedIds: string[]): Array<{ task: BoardTask; depth: number; childCount: number }> {
  const byParent = new Map<string, BoardTask[]>();
  const ids = new Set(tasks.map((task) => task.id));
  for (const task of tasks) {
    const parent = task.parentTaskId && ids.has(task.parentTaskId) ? task.parentTaskId : '';
    byParent.set(parent, [...(byParent.get(parent) ?? []), task]);
  }
  const result: Array<{ task: BoardTask; depth: number; childCount: number }> = [];
  const visit = (task: BoardTask, depth: number) => {
    const children = byParent.get(task.id) ?? [];
    result.push({ task, depth, childCount: children.length });
    if (collapsedIds.includes(task.id)) return;
    children.forEach((child) => visit(child, depth + 1));
  };
  (byParent.get('') ?? []).forEach((task) => visit(task, 0));
  return result;
}

export function displayTime(value: string): string {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(parsed);
}

/** Classic's quick completion review; manual verification editing is separate. */
export function canMarkTaskVerified(task: BoardTask): boolean {
  return (task.lane === 'Done' || task.lane === 'Archived' && task.raw.archived_from_lane === 'Done')
    && ['pending', 'attempted'].includes(task.verificationState);
}
