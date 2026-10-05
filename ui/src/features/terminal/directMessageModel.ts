import type { UnknownRecord } from '../../protocol';
import { messageTime, rows, text } from './composerModel';
export const MESSAGE_PAGE_SIZE = 30;
export const messageId = (row: UnknownRecord): string => text(row.message_id) || text(row.id);
export const messageBody = (row: UnknownRecord): string => text(row.message) || text(row.text) || text(row.body);
export function orderedMessages(value: unknown): UnknownRecord[] {
  return rows(value).sort((a, b) => messageTime(a.created_at || a.timestamp || a.sent_at) - messageTime(b.created_at || b.timestamp || b.sent_at) || messageId(a).localeCompare(messageId(b)));
}
export function messageView(row: UnknownRecord, agent: { id: string; name: string }) {
  const type = text(row.message_type) || 'message';
  const direction = type === 'system' || type === 'reminder' ? 'system' : row.sender_kind === 'user' || row.direction === 'outbound' ? 'outbound' : row.recipient_kind === 'user' || row.sender_id === agent.id || row.direction === 'sent' || row.direction === 'inbound' ? 'inbound' : row.recipient_id === agent.id || row.direction === 'received' ? 'outbound' : 'inbound';
  const sender = direction === 'system' ? type === 'reminder' ? 'Torque reminder' : 'System' : text(row.sender_name) || (direction === 'outbound' ? 'You' : agent.name);
  const label = type === 'ask' ? row.blocking || row.ack_required ? 'Blocking ask' : 'Ask' : type === 'ask_reply' ? 'Ask reply' : type === 'message' ? '' : type.replaceAll('_', ' ');
  const delivery = text(row.delivery_state) || (row.buffered === true ? 'buffered' : row.delivered === true ? 'delivered' : '');
  const deliveryLabel = ({ buffered: 'Waiting for delivery', failed: 'Delivery failed', cancelled: 'Cancelled', delivered: 'Delivered' } as Record<string, string>)[delivery] ?? (delivery ? `Delivery: ${delivery}` : '');
  const at = messageTime(row.created_at || row.timestamp || row.sent_at); const date = new Date(at * 1000);
  return { id: messageId(row), body: messageBody(row), direction, sender, type, label, delivery, deliveryLabel, reason: text(row.delivery_reason), replyTo: text(row.reply_to_id), iso: at > 0 && Number.isFinite(date.getTime()) ? date.toISOString() : '' };
}
