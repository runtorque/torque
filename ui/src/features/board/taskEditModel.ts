import type { UnknownRecord } from '../../protocol';
import { mergeEvidenceChanges } from './taskEvidenceModel';
import { resolveActionVariables } from './actionVariables';

export function localSchedule(value: string) {
  if (!value) return '';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
export function taskEditChanges(baseline: UnknownRecord, draft: UnknownRecord, definitions: UnknownRecord[], latest: UnknownRecord): UnknownRecord {
  const variables = resolveActionVariables(String(draft.action_vars), []);
  const original = resolveActionVariables(String(baseline.action_vars), []);
  const fields = Object.fromEntries(Object.entries(draft).filter(([key, value]) => key !== 'action_vars' && JSON.stringify(value) !== JSON.stringify(baseline[key])));
  if ('action_name' in fields || JSON.stringify(variables) !== JSON.stringify(original)) fields.action_vars = resolveActionVariables(String(draft.action_vars), definitions);
  if ('scheduled_at' in fields) fields.scheduled_at = typeof draft.scheduled_at === 'string' && draft.scheduled_at ? new Date(draft.scheduled_at).toISOString() : '';
  if (['provider', 'external_id', 'external_url'].some((key) => key in fields)) {
    for (const key of ['provider', 'external_id', 'external_url']) fields[key] = draft[key];
  }
  // Preserve concurrently refreshed metadata outside the edited summary keys.
  for (const key of ['verification_summary', 'board_sync']) {
    if (!(key in fields)) continue;
    const before = baseline[key] as UnknownRecord; const after = draft[key] as UnknownRecord;
    const changed = Object.fromEntries(Object.entries(after).filter(([name, value]) => JSON.stringify(value) !== JSON.stringify(before[name])));
    fields[key] = { ...(latest[key] ?? {}), ...changed };
  }
  for (const key of ['attachments', 'artifacts']) {
    if (key in fields) fields[key] = mergeEvidenceChanges(baseline[key] as UnknownRecord[], draft[key] as UnknownRecord[], (latest[key] ?? []) as UnknownRecord[]);
  }
  return fields;
}
