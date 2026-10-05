import type { UnknownRecord } from '../../protocol';
import { defaultPrompt, taskText, uploadType } from './taskCreationModel';
export function evidenceFilename(item: UnknownRecord): string {
  if (item.type && item.path && !taskText(item.path).includes('/attachments/')) return '';
  const lifecycle = (item.lifecycle ?? {}) as UnknownRecord;
  return !lifecycle.owner || lifecycle.owner === 'task' ? taskText(item.filename) : '';
}
export async function uploadedEvidence(entry: UnknownRecord, file: File) {
  const type = uploadType(taskText(entry.filename), taskText(entry.mime_type || file.type));
  if (type === 'image') return { kind: 'attachment' as const, item: entry };
  const content = type !== 'file_ref' && file.size <= 262144 ? await file.text().catch(() => '') : '';
  return { kind: 'artifact' as const, item: { ...entry, id: `artifact-${crypto.randomUUID()}`, type, title: taskText(entry.filename), summary: `${String(file.size)} bytes`, content, prompt: { mode: defaultPrompt(type) }, storage: { kind: type === 'file_ref' ? 'file_ref' : 'path', path: entry.path, content }, lifecycle: { owner: 'task', cleanup: 'delete_with_task' }, metadata: { size_bytes: file.size } } };
}
function evidenceKey(item: UnknownRecord) { return taskText(item.id || item.filename || item.path); }
/** Apply only the local evidence edits to the latest collection. */
export function mergeEvidenceChanges(before: UnknownRecord[], draft: UnknownRecord[], latest: UnknownRecord[]) {
  const initial = new Map(before.map((item) => [evidenceKey(item), item]));
  const current = new Map(draft.map((item) => [evidenceKey(item), item]));
  const deleted = new Set([...initial.keys()].filter((key) => !current.has(key)));
  const result = latest.filter((item) => !deleted.has(evidenceKey(item)));
  for (const item of draft) {
    const key = evidenceKey(item); const original = initial.get(key);
    if (original && JSON.stringify(original) === JSON.stringify(item)) continue;
    const index = result.findIndex((entry) => evidenceKey(entry) === key);
    const changes = original ? Object.fromEntries(Object.entries(item).filter(([name, value]) => JSON.stringify(value) !== JSON.stringify(original[name]))) : item;
    if (index < 0) result.push(item); else result[index] = { ...result[index], ...changes };
  }
  return result;
}
export function evidenceUrl(taskId: string, item: UnknownRecord) {
  const filename = taskText(item.filename);
  const storage = (item.storage ?? {}) as UnknownRecord;
  const path = taskText(item.path || storage.path);
  const url = taskText(item.url);
  const explicitUrl = /^https?:\/\//i.test(url) || url.startsWith('/attachments/') ? url : '';
  if (storage.kind === 'inline' && !path) return explicitUrl;
  const externalReference = storage.kind === 'file_ref' && !path.includes('/attachments/');
  if (!externalReference && filename && (!item.type || !path || path.includes('/attachments/')) && (!item.lifecycle || !(item.lifecycle as UnknownRecord).owner || (item.lifecycle as UnknownRecord).owner === 'task' || item.taskId)) return `/attachments/${encodeURIComponent(taskText(item.taskId, taskId))}/${encodeURIComponent(filename)}`;
  if (path.startsWith('/attachments/')) return path;
  return explicitUrl;
}
export function evidencePreviewKind(item: UnknownRecord) {
  const type = taskText(item.type).trim().toLowerCase();
  const mime = taskText(item.mime_type).toLowerCase().split(';')[0]!.trim();
  const name = taskText(item.filename || item.path || (item.storage as UnknownRecord | undefined)?.path || item.title).toLowerCase();
  if (type === 'image' || mime.startsWith('image/') || /\.(png|jpe?g|gif|webp|svg)$/.test(name)) return 'image';
  const content = item.content ?? (item.storage as UnknownRecord | undefined)?.content;
  if (content || ['snippet', 'log', 'diff', 'test_report', 'generated_doc'].includes(type) || mime.startsWith('text/') || /json|xml|javascript/.test(mime) || /\.(md|markdown|txt|diff|patch|json|log|csv|ya?ml|xml|html?|js|css)$/.test(name)) return 'text';
  return 'file';
}
