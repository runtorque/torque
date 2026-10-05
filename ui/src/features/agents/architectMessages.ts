import type { UnknownRecord } from '../../protocol';
const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
const rows = (value: unknown): UnknownRecord[] => (Array.isArray(value) ? value : Object.values(record(value))).map(record);
export function architectPeerThreads(value: unknown, agentId: string): UnknownRecord[] {
  return rows(value).filter((thread) => (Array.isArray(thread.participant_ids) && thread.participant_ids.includes(agentId)) || rows(thread.messages).some((message) => message.sender_id === agentId || message.recipient_id === agentId));
}
export function architectMessageRows(current: unknown, threads: UnknownRecord[]): UnknownRecord[] {
  const seen = new Set<string>();
  return [...rows(current), ...threads.flatMap((thread) => rows(thread.messages))].filter((message) => {
    const id = typeof message.id === 'string' ? message.id : '';
    if (!id) return true;
    if (seen.has(id)) return false;
    seen.add(id); return true;
  });
}
