import type { UnknownRecord } from '../../protocol';
export function aiRecord(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
export function aiText(value: unknown, fallback = ''): string { return typeof value === 'string' && value ? value : fallback; }
export function aiCount(value: unknown): number { const count = typeof value === 'number' || typeof value === 'string' ? Number(value) : 0; return Number.isFinite(count) ? Math.max(0, Math.trunc(count)) : 0; }
export function aiTime(value: unknown): string | null {
  const number = typeof value === 'number' || typeof value === 'string' ? Number(value) : 0;
  if (!Number.isFinite(number) || number <= 0) return null;
  const date = new Date(number > 100_000_000_000 ? number : number * 1000);
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}
export function aiTone(status: string) {
  if (['ready', 'available', 'complete', 'completed', 'success'].includes(status)) return 'success';
  if (['error', 'missing', 'dependency_missing', 'failed'].includes(status)) return 'danger';
  if (['disabled', 'not_built', 'idle', 'skipped'].includes(status)) return 'muted';
  return 'warning';
}
export function aiIndexControl(settings: UnknownRecord) {
  const index = aiRecord(settings.index); const counts = aiRecord(index.counts); const warning = aiRecord(index.rebuild_warning); const job = aiRecord(index.current_job);
  const missing = aiRecord(aiRecord(settings.embeddings).dependency).status === 'missing';
  const running = ['queued', 'running'].includes(aiText(job.status)) || index.status === 'building';
  const rebuild = Boolean(warning.required) || ['ready', 'rebuild_pending'].includes(aiText(index.status)) || aiCount(counts.chunks) > 0 || aiCount(counts.indexed) > 0;
  return { mode: rebuild ? 'rebuild' as const : 'incremental' as const, label: rebuild ? 'Rebuild index' : 'Build index', blocked: missing || running, reason: missing ? 'Install the embedding dependencies before starting the index.' : running ? 'An index job is already queued or running.' : '' };
}
