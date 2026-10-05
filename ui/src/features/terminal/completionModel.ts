import { record, rows, text } from './composerModel';
export interface CompletionItem { id: string; label: string; detail: string; insert: string }
export interface Completion { kind: 'command' | 'task'; start: number; end: number; items: CompletionItem[] }
export interface CompletionScope { catalog: unknown; tasks: unknown; group: string; provider: string; hasTarget: boolean }
export function composerCompletion(value: string, selection: [number, number], scope: CompletionScope): Completion | null {
  if (selection[0] !== selection[1]) return null;
  const caret = Math.max(0, Math.min(value.length, selection[0])); const before = value.slice(0, caret);
  if (scope.hasTarget && before.startsWith('/') && !before.includes('\n')) {
    const query = before.slice(1).trim().toLowerCase().replace(/\s+/g, ' ');
    const items = rows(scope.catalog).filter((row) => {
      const providers = Array.isArray(row.providers) ? row.providers.map((provider) => text(provider).trim().toLowerCase()) : [];
      return (!providers.length || providers.includes(scope.provider.trim().toLowerCase())) && `${text(row.search)} ${text(row.label)} ${text(row.usage)}`.toLowerCase().replace(/\s+/g, ' ').includes(query);
    }).map((row) => ({ id: text(row.id), label: text(row.label), detail: [text(row.usage) !== text(row.label) ? text(row.usage) : '', text(row.help)].filter(Boolean).join(' · '), insert: (text(row.insert) || text(row.label)).trimStart() })).filter((row) => row.id && row.label && row.insert);
    if (items.length) return { kind: 'command', start: 0, end: value.length, items };
  }
  const trigger = /(^|\s):([\w:-]*)$/.exec(before);
  if (!trigger || !scope.group) return null;
  const query = (trigger[2] ?? '').toLowerCase();
  const items: CompletionItem[] = [];
  for (const [key, value] of Object.entries(record(scope.tasks))) {
    const row = record(value); const id = text(row.id) || key; const title = text(row.task) || text(row.title);
    if (row.archived_at || row.lane === 'Archived' || row.group !== scope.group || !id || !`${id} ${title}`.toLowerCase().includes(query)) continue;
    items.push({ id, label: id, detail: title, insert: `${id} ` }); if (items.length === 8) break;
  }
  return items.length ? { kind: 'task', start: caret - query.length - 1, end: caret, items } : null;
}
export function insertCompletion(value: string, completion: Completion, item: CompletionItem): { text: string; selection: [number, number] } {
  const next = value.slice(0, completion.start) + item.insert + value.slice(completion.end);
  const caret = completion.start + item.insert.length; return { text: next, selection: [caret, caret] };
}
