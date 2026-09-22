import type { TorqueCommand, UnknownRecord } from '../../protocol';
import type { AgentViewModel } from '../agents/model';
import type { ComposerDraft, SentMessage } from './composerState';
export const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
export const text = (value: unknown): string => typeof value === 'string' || typeof value === 'number' ? String(value) : '';
export const rows = (value: unknown): UnknownRecord[] => Array.isArray(value) ? value.map(record) : [];
export function messageTime(value: unknown): number {
  const numeric = Number(value); if (Number.isFinite(numeric) && numeric > 0) return numeric > 100_000_000_000 ? numeric / 1000 : numeric;
  const parsed = Date.parse(text(value)); return Number.isFinite(parsed) ? parsed / 1000 : 0;
}
export function composerCommand(cell: AgentViewModel, target: AgentViewModel | null, draft: ComposerDraft): TorqueCommand {
  const message = [draft.text, ...draft.attachments.map((entry) => entry.path)].filter(Boolean).join('\n');
  if (!message.trim()) throw new Error('Enter a message or attach a file.');
  if (draft.reply && draft.reply.agentId !== target?.id) throw new Error('The reply target changed. Cancel the reply before sending to this target.');
  if (target) return { cmd: 'user_agent_message', agent_id: target.id, message, thread_id: `user-agent:user:${target.id}`, ...(draft.reply ? { reply_to_id: draft.reply.id } : {}) };
  if (!cell.sessionId) throw new Error('This terminal has no active session. Relaunch it before sending.');
  return { cmd: 'send_user_message', cell_id: cell.id, session_id: cell.sessionId, text: message };
}
export function acknowledgedMessage(frame: UnknownRecord, command: TorqueCommand): { notice: string; cancellable: boolean; id: string } {
  if (frame.type === 'error') throw new Error(text(frame.message) || 'Message refused.');
  if (command.cmd === 'send_user_message') {
    if (frame.type !== 'terminal_message_sent' || frame.cell_id !== command.cell_id || frame.session_id !== command.session_id) throw new Error('Could not confirm terminal delivery. The draft is retained.');
    return { notice: 'Sent to the terminal.', cancellable: false, id: text(command.idempotency_key) };
  }
  const message = text(command.message).trim(); const loop = record(frame.loop);
  if (frame.type === 'agent_message_loop' && /^\/loop(?:\s|$)/.test(message) && loop.agent_id === command.agent_id && text(frame.audit_message_id)) return { notice: `Message loop ${text(loop.status)}.`, cancellable: false, id: text(frame.audit_message_id) };
  if (frame.type === 'agent_restart' && message === '/restart' && frame.agent_id === command.agent_id && frame.status === 'succeeded' && text(frame.audit_message_id)) return { notice: 'Agent restarted.', cancellable: false, id: text(frame.audit_message_id) };
  if (frame.type !== 'ok' || frame.agent_id !== command.agent_id || frame.thread_id !== command.thread_id || !text(frame.message_id)) throw new Error('Could not confirm this message target. The draft is retained.');
  if (frame.delivery_state && text(frame.reply_to_id) !== text(command.reply_to_id)) throw new Error('The acknowledged reply target did not match. The draft is retained.');
  if (frame.delivery_state === 'failed' || frame.delivery_state === 'cancelled') throw new Error(`Message was saved but ${frame.delivery_state === 'failed' ? 'delivery failed' : 'it was cancelled'}. ${text(frame.delivery_reason)} The draft is retained.`);
  if (!(frame.delivery_state === 'buffered' || frame.delivery_state === 'delivered' || frame.delivered === true || frame.buffered === true)) throw new Error('Could not confirm message delivery state. The draft is retained.');
  return { notice: frame.buffered === true || frame.delivery_state === 'buffered' ? 'Message saved; waiting for delivery.' : 'Message acknowledged.', cancellable: Boolean(frame.delivery_state), id: text(frame.message_id) };
}
export const cancellationLabels: Record<string, string> = {
  cancelled_queued: 'Queued message cancelled.', interrupted: 'Active message turn interrupted.',
  unsupported_provider: 'This provider cannot safely interrupt the active turn.', session_replaced: 'The session changed; no turn was interrupted.',
  no_active_turn: 'No active turn remains for that message.', interrupt_failed: 'Could not interrupt the active turn; it was left unchanged.',
};
export function recallMessages(history: unknown, messages: unknown, sent: SentMessage[]): SentMessage[] {
  const candidates = [...sent, ...rows(history).map((row) => ({ id: text(row.id), message: text(row.message), at: messageTime(row.sent_at) })), ...rows(messages).filter((row) => row.sender_kind === 'user' && row.delivery_state !== 'failed' && row.delivery_state !== 'cancelled').map((row) => ({ id: text(row.id ?? row.message_id), message: text(row.message), at: messageTime(row.created_at) }))].sort((a, b) => b.at - a.at);
  const seen = new Set<string>(); return candidates.filter((row) => { if (!row.message.trim() || seen.has(row.message)) return false; seen.add(row.message); return true; }).slice(0, 100);
}
