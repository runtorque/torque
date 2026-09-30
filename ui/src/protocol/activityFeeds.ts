import type { UnknownRecord } from './types';

function text(value: unknown): string { return typeof value === 'string' || typeof value === 'number' ? String(value) : ''; }

function rows(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.filter((row): row is UnknownRecord => Boolean(row) && typeof row === 'object' && !Array.isArray(row)) : [];
}
function merge(current: unknown, incoming: unknown, key: (row: UnknownRecord) => string, compare: (a: UnknownRecord, b: UnknownRecord) => number, limit: number) {
  const entries = new Map<string, UnknownRecord>();
  for (const row of [...rows(current), ...rows(incoming)]) entries.set(key(row), row);
  return [...entries.values()].sort(compare).slice(0, limit);
}
export function mergeMcpCalls(current: unknown, incoming: unknown): UnknownRecord[] {
  return merge(current, incoming,
    (row) => text(row.cursor) || text(row.idempotency_key) || `${text(row.tool_name)}:${text(row.appended_at)}:${text(row.session_id)}`,
    (a, b) => Number(b.appended_at ?? 0) - Number(a.appended_at ?? 0) || Number(b.cursor ?? 0) - Number(a.cursor ?? 0), 500);
}
export function mergeCellEvents(current: unknown, incoming: unknown): UnknownRecord[] {
  return merge(current, incoming,
    (row) => text(row.id) || `${text(row.kind)}:${text(row.timestamp)}:${text(row.message)}`,
    (a, b) => Number(b.timestamp ?? 0) - Number(a.timestamp ?? 0) || text(b.id).localeCompare(text(a.id)), 200);
}
