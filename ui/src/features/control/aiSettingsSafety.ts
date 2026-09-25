import type { UnknownRecord } from '../../protocol';

function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
function text(value: unknown): string { return typeof value === 'string' ? value : ''; }
const corpusKeys = ['architect_journals', 'engineer_journals', 'decisions', 'tasks', 'engineer_peer_threads'];
export function aiRebuildPrompt(current: UnknownRecord, draft: UnknownRecord): string | null {
  const index = record(current.index); const warning = record(index.rebuild_warning); const counts = record(index.counts); const embeddings = record(current.embeddings);
  const entries = [warning.estimated_entries, counts.chunks, counts.indexed, counts.sources].map(Number).find((n) => Number.isFinite(n) && n > 0) ?? 0;
  if (entries <= 0) return null;
  const currentModel = text(embeddings.model_id) || text(embeddings.desired_model_id); const nextModel = text(draft.ai_embedding_model).trim();
  const corpus = record(index.corpus); const nextCorpus = record(draft.ai_index_corpus);
  const changed = corpusKeys.some((key) => (corpus[key] !== false) !== (nextCorpus[key] !== false));
  if (!(currentModel && nextModel && currentModel !== nextModel) && !changed && !warning.required) return null;
  return `Changing embedding settings rebuilds the vector index (${Math.trunc(entries)} entries). Continue with these settings?`;
}

/** Never echo write-only drafts back through server/transport error text. */
export function redactAiSaveError(message: string, secrets: Record<string, string>): string {
  let sanitized = message;
  const values = [...new Set(Object.values(secrets).map((value) => value.trim()).filter(Boolean))].sort((a, b) => b.length - a.length);
  for (const value of values) sanitized = sanitized.split(value).join('[redacted]');
  return sanitized.replace(/(api[_-]?key|secret|token|password|authorization)\s*[:=]\s*\S+/ig, '$1: [redacted]');
}
