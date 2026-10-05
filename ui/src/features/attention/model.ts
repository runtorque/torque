import type { UnknownRecord } from '../../protocol';

export function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
export function text(value: unknown): string { return typeof value === 'string' ? value.trim() : ''; }
export function labels(task: UnknownRecord): unknown[] { return Array.isArray(task.labels) ? task.labels : []; }
export function isOpenAsk(task: UnknownRecord): boolean {
  return labels(task).includes('torque:human') && !labels(task).includes('torque:non-user-ask') && !['Done', 'Archive', 'Archived'].includes(text(task.lane));
}
export function proposalId(task: UnknownRecord): string {
  const label = labels(task).find((value) => typeof value === 'string' && value.startsWith('proposal:'));
  return typeof label === 'string' ? label.slice(9).trim() : /Proposal:\s*(\S+)/i.exec(text(task.description))?.[1] ?? '';
}
export function askTarget(task: UnknownRecord, tasks: UnknownRecord, agents: UnknownRecord) {
  const id = text(task.reply_agent_id) || (labels(task).includes('architect-ask') ? text(task.created_by_architect_id) : '') || text(record(tasks[text(task.parent_task_id)]).agent_id);
  const agent = record(agents[id]);
  const reason = !id || !agents[id] ? 'Reply target could not be found.'
    : agent.cell_type !== 'agent' ? 'The reply target is not an agent.'
    : Number(agent.deleted_at) > 0 ? 'The reply target has been deleted.'
    : Number(agent.dismissed_at) > 0 ? 'The reply target has been dismissed.'
    : !text(agent.session_id) ? 'The reply target has no live session.'
    : !['idle', 'running'].includes(text(agent.status)) ? 'The reply session is not active.' : '';
  return { id, agent, reason, answerable: !reason };
}

export function attentionTimestamp(item: UnknownRecord, task: boolean): number {
  const stamp = task ? Date.parse(text(item.created_at)) / 1000 : Number(item.last_event_at);
  return Number.isFinite(stamp) && stamp > 0 ? stamp : 0;
}
export function attentionDismissed(dismissed: UnknownRecord, id: string, stamp: number): boolean {
  const saved = Number(dismissed[id]);
  return Number.isFinite(saved) && saved > 0 && (!stamp || stamp <= saved);
}
