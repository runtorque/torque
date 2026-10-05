import type { DeltaOperation, UnknownRecord } from './types';
function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }

/** Normalize Classic's full-settings and partial live-status AI payloads. */
export function applyAiDelta(current: unknown, operation: DeltaOperation): UnknownRecord {
  if (operation.settings && typeof operation.settings === 'object' && !Array.isArray(operation.settings)) return { ...operation.settings as UnknownRecord };
  const op = operation.op; const payload = { ...operation } as UnknownRecord;
  delete payload.op; delete payload.schema_version; delete payload.changed_keys;
  if (op === 'ai_settings_update') return payload;
  const previous = record(current);
  const scope = op === 'ai_index_status_update' ? 'index' : 'boot_summary';
  const partial = record(payload[scope] ?? payload);
  const before = record(previous[scope]);
  const next = { ...before, ...partial };
  for (const key of ['counts', 'rebuild_warning']) if (partial[key] && typeof partial[key] === 'object') next[key] = { ...record(before[key]), ...record(partial[key]) };
  if (scope === 'index' && 'job' in partial && !('current_job' in partial)) next.current_job = partial.job;
  const result = { ...previous, [scope]: next };
  const companion = scope === 'index' ? 'embeddings' : 'metering';
  if (payload[companion] && typeof payload[companion] === 'object') result[companion] = { ...record(previous[companion]), ...record(payload[companion]) };
  return result;
}
