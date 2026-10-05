import type { UnknownRecord } from '../../protocol';
import type { LoopCancellation } from './composerState';
import { record, text } from './composerModel';
export function messageLoopPanel(loops: unknown, operations: Record<string, LoopCancellation>, agentId: string): UnknownRecord | null {
  if (!agentId) return null;
  const active = Object.values(record(loops)).map(record).filter((loop) => loop.agent_id === agentId && loop.status === 'active').sort((a, b) => Number(b.updated_at || b.created_at || 0) - Number(a.updated_at || a.created_at || 0));
  if (active[0]) return active[0];
  const operation = Object.values(operations).reverse().find((item) => item.agentId === agentId);
  return operation ? record(record(loops)[text(operation.loop.id)] ?? operation.loop) : null;
}
export function loopInterval(seconds: unknown): string {
  const value = Number(seconds); if (!Number.isFinite(value) || value <= 0) return 'unknown interval';
  return value % 3600 === 0 ? `${value / 3600}h` : value % 60 === 0 ? `${value / 60}m` : `${value}s`;
}
export function loopNextRun(loop: UnknownRecord): string {
  if (Number(loop.deferred_at) > 0 && loop.deferred_reason === 'agent_busy') return 'Deferred until the agent is idle';
  const at = Number(loop.next_run_at); const date = new Date(at * 1000);
  return at > 0 && Number.isFinite(date.getTime()) ? `Next ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'Next run not scheduled';
}
export function assertLoopCancelled(frame: UnknownRecord, agentId: string, loopId: string): void {
  if (frame.type === 'error') throw new Error(text(frame.message) || 'Loop cancellation refused.');
  const loop = record(frame.loop);
  if (frame.type !== 'agent_message_loop' || loop.agent_id !== agentId || loop.id !== loopId || loop.status !== 'cancelled' || !text(frame.audit_message_id)) throw new Error('Could not confirm cancellation of this loop. Retry to check the same request.');
}
