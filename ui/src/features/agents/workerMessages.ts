import type { UnknownRecord } from '../../protocol';
import type { ActivityRead } from './activityReads';
const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
const rows = (value: unknown): UnknownRecord[] => Array.isArray(value) ? value.map(record) : [];
function loaded(task: UnknownRecord): boolean {
  if (!Array.isArray(task.messages_thread)) return false;
  if (!task.messages_thread_summary) return true;
  const summary = record(task.messages_thread_summary); const messages = rows(task.messages_thread);
  return Number(summary.count ?? 0) === messages.length && Number(summary.last_timestamp ?? 0) === Math.max(0, ...messages.map((message) => Number(message.timestamp ?? 0)));
}
export function workerMessageReads(tasks: Record<string, unknown>, agentId: string): ActivityRead[] {
  return Object.entries(tasks).flatMap(([id, value]) => {
    const task = record(value); const summary = record(task.messages_thread_summary);
    const relevant = task.agent_id === agentId || (Array.isArray(summary.recipient_agent_ids) && summary.recipient_agent_ids.includes(agentId));
    return relevant && Number(summary.count) > 0 && !loaded(task) ? [{ command: { cmd: 'task_detail', id }, type: 'task_detail', target: ['id', id] as [string, string] }] : [];
  }).sort((a, b) => String(a.command.id).localeCompare(String(b.command.id)));
}
export function workerMessageRevision(tasks: Record<string, unknown>, requests: ActivityRead[]): string {
  return JSON.stringify(requests.map((request) => { const task = record(tasks[String(request.command.id)]); return [request.command.id, task.group, task.agent_id, task.messages_thread_summary]; }));
}
export function workerMessageRows(tasks: Record<string, unknown>, details: Record<string, UnknownRecord>, agentId: string): UnknownRecord[] {
  return Object.entries(tasks).flatMap(([taskId, value]) => {
    const task = record(value); const summary = record(task.messages_thread_summary);
    const messages = task.messages_thread_summary && !Number(summary.count) ? [] : loaded(task) ? rows(task.messages_thread) : rows(details[taskId]?.messages_thread ?? task.messages_thread);
    return messages.flatMap((message, index) => {
      if (message.recipient_agent_id ? message.recipient_agent_id !== agentId : task.agent_id !== agentId) return [];
      return [{ ...message, id: `inline-thread:${taskId}:${index}:${String(Number(message.timestamp ?? 0))}`, task_id: taskId, action: 'engineer_message', direction: 'received' }];
    });
  });
}
