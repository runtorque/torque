import type { UnknownRecord } from '../../protocol';

export type ContextLinkKind = 'task' | 'pipeline' | 'agent';
export interface ContextLink { target_kind: ContextLinkKind; target_ref: string }
export interface ContextTarget { kind: ContextLinkKind; id: string; name: string; group: string }
export const contextLinkKinds: ContextLinkKind[] = ['task', 'pipeline', 'agent'];
const text = (value: unknown) => typeof value === 'string' ? value : '';
function pipelineRoot(task: UnknownRecord, tasks: Map<string, UnknownRecord>): string {
  const seen = new Set<string>(); let current = task;
  while (true) {
    const explicit = text(current.pipeline_root_id); if (explicit) return explicit;
    const id = text(current.id); if (seen.has(id)) return text(task.id); seen.add(id);
    const parent = text(current.parent_task_id); if (!parent) return id;
    const next = tasks.get(parent); if (!next) return parent;
    current = next;
  }
}
export function contextTargets(tasks: UnknownRecord[], agents: UnknownRecord[]): ContextTarget[] {
  const taskMap = new Map(tasks.map((task) => [text(task.id), task]));
  const result: ContextTarget[] = []; const roots = new Set<string>();
  for (const task of tasks) {
    const id = text(task.id); if (!id) continue;
    result.push({ kind: 'task', id, name: text(task.task) || text(task.title) || id, group: text(task.group) });
    const rootId = pipelineRoot(task, taskMap); const root = taskMap.get(rootId);
    if (!roots.has(rootId)) { roots.add(rootId); result.push({ kind: 'pipeline', id: rootId, name: text(root?.task) || text(root?.title) || rootId, group: text(root?.group) || text(task.group) }); }
  }
  for (const agent of agents) if (text(agent.id) && agent.cell_type !== 'terminal' && agent.kind !== 'terminal') result.push({ kind: 'agent', id: text(agent.id), name: text(agent.name) || text(agent.id), group: text(agent.group) });
  return result;
}
export function contextLinkTarget(link: ContextLink, targets: ContextTarget[]) { return targets.find((target) => target.kind === link.target_kind && target.id === link.target_ref); }
export function savedContextLinks(value: unknown): ContextLink[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item: unknown): item is ContextLink => Boolean(item && typeof item === 'object' && 'target_kind' in item && contextLinkKinds.includes(item.target_kind as ContextLinkKind) && 'target_ref' in item && typeof item.target_ref === 'string'));
}
