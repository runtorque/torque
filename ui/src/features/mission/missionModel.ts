import type { UnknownRecord } from '../../protocol';
export const sectionDefinitions = [
  ['needs_operator_now', 'Needs operator now', 'Gates and recommended next actions that need a human decision or validation.', 'No operator gates are waiting right now.'],
  ['at_risk_watchlist', 'At-risk watchlist', 'Risks worth inspecting before they become blocking.', 'No active risks in this group.'],
  ['in_flight', 'In flight', 'Healthy active work, kept compact so gates stay first.', 'No healthy in-flight work to show.'],
  ['recently_completed', 'Recently completed', 'Recent completions for operator confidence.', 'No recent completions in the configured window.'],
] as const;
export function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
export function text(value: unknown, fallback = ''): string { return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback; }
export function rows(value: unknown): UnknownRecord[] { return Array.isArray(value) ? value.map(record) : []; }
export function count(value: unknown): number { const parsed = Number(value); return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 0; }
export function ownerLabel(card: UnknownRecord) { const owner = record(card.owner); return text(owner.agent_name || owner.agent_slug || owner.agent_id || owner.assigned_engineer_id || owner.assigned_architect_id || owner.created_by_engineer_id || owner.created_by_architect_id, 'Unassigned'); }
export function referenceLabel(card: UnknownRecord) { const ref = record(card.ref); return `${text(ref.kind || card.kind, 'ref')}:${text(ref.id || card.primary_task_id || card.stream_id || card.id, '—')}`; }
export function actionLabel(card: UnknownRecord) { return text(card.recommended_next_action, 'Inspect source surface').replaceAll('_', ' '); }
export function visibleSection(summary: UnknownRecord, key: string, dismissed: UnknownRecord) {
  const source = record(record(summary.sections)[key]); const all = rows(source.items); const items = all.filter((card) => !dismissed[text(card.id)]);
  return { items, count: Math.max(0, count(source.count ?? all.length) - (all.length - items.length)), truncated: source.truncated === true };
}
export function validateSummary(frame: UnknownRecord, group: string) {
  if (frame.type !== 'mission_control_summary' || frame.group !== group || !frame.sections || typeof frame.sections !== 'object' || Array.isArray(frame.sections)) throw new Error('The daemon returned a Mission Control summary for the wrong scope or an invalid response.');
  for (const [key] of sectionDefinitions) { const section = record(frame.sections)[key]; if (section !== undefined && (!Array.isArray(record(section).items) || rows(record(section).items).some((card) => !text(card.id)))) throw new Error('The daemon returned invalid Mission Control cards.'); }
}
export function matchesCard(card: UnknownRecord, query: string) { return !query.trim() || JSON.stringify(card).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()); }
export function dateValue(value: unknown): Date | null { const parsed = typeof value === 'number' ? value > 0 ? new Date(value < 1e12 ? value * 1000 : value) : null : typeof value === 'string' && value.trim() ? new Date(value) : null; return parsed && Number.isFinite(parsed.getTime()) ? parsed : null; }
