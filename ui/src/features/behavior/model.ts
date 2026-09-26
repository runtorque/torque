import type { UnknownRecord } from '../../protocol';
export type Scope = { group: string; kind: 'agent' | 'role'; target: string };
export const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
export const text = (value: unknown): string => typeof value === 'string' ? value : '';
export const items = (value: unknown): UnknownRecord[] => Array.isArray(value) ? value.map(record) : [];
export const scopeKey = (scope: Scope) => JSON.stringify([scope.group, scope.kind, scope.target]);
export const scopeArgs = (scope: Scope) => ({ group: scope.group, scope_kind: scope.kind, scope_group: scope.group, scope_key: scope.target, ...(scope.kind === 'agent' ? { agent_id: scope.target } : { role_kind: scope.target }) });
export function matchesScope(row: UnknownRecord, scope: Scope) {
  return text(row.scope_kind) === scope.kind && text(row.scope_group) === scope.group && text(row.scope_key || row.agent_id) === scope.target;
}
export function validateRead(frame: UnknownRecord, scope: Scope, type: 'behavior_overlay' | 'behavior_overlay_versions') {
  if (frame.type !== type || !matchesScope(frame, scope)) throw new Error('The behavior response belongs to a different scope or is invalid.');
  if (type === 'behavior_overlay' && (typeof frame.text !== 'string' || !text(record(frame.version).id))) throw new Error('The behavior response has no current text or base version.');
  if (type === 'behavior_overlay_versions' && (!Array.isArray(frame.versions) || items(frame.versions).some((row) => !text(row.id) || !matchesScope(row, scope)))) throw new Error('The behavior version list is invalid for this scope.');
}
// Match Classic's compact prefix/suffix preview. This is an unsaved local diff,
// independent of daemon-generated historical/proposal review diffs.
export function draftDiff(before: string, after: string) {
  if (before === after) return '';
  const a = before.split(/\r\n|\r|\n/); const b = after.split(/\r\n|\r|\n/); let prefix = 0; let suffix = 0;
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix++;
  while (suffix + prefix < a.length && suffix + prefix < b.length && a[a.length - 1 - suffix] === b[b.length - 1 - suffix]) suffix++;
  const start = Math.max(0, prefix - 3); const endA = Math.min(a.length, a.length - suffix + 3); const endB = Math.min(b.length, b.length - suffix + 3);
  return ['--- active', '+++ draft', `@@ -${start + 1},${endA - start} +${start + 1},${endB - start} @@`, ...a.slice(start, prefix).map((line) => ` ${line}`), ...a.slice(prefix, a.length - suffix).map((line) => `-${line}`), ...b.slice(prefix, b.length - suffix).map((line) => `+${line}`), ...a.slice(a.length - suffix, endA).map((line) => ` ${line}`)].join('\n');
}
