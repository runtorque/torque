import type { UnknownRecord } from '../../protocol';
import { record, text } from './agentClassesModel';
export type CatalogKind = 'role' | 'template' | 'specialization';
export type CatalogTarget = { name: string; scope: string };
export const catalogListKey = (kind: CatalogKind) => kind === 'specialization' ? 'specializations' : kind === 'template' ? 'templates' : 'roles';
export const catalogTargetKey = (target: CatalogTarget) => `${target.scope}:${target.name}`;
export const targetFromRow = (row: UnknownRecord): CatalogTarget => ({ name: text(row.name), scope: row.global === true || row.scope === 'user' ? 'user' : 'project' });
export function catalogDraft(data: UnknownRecord, scope = 'project') {
  return { name: text(data.name), scope, values: { ...data, max_turns: text(data.max_turns), idle_timeout: text(data.idle_timeout) } as UnknownRecord, priorities: Array.isArray(data.priorities) ? data.priorities.map((item) => text(item)).join('\n') : '', environment: Object.entries(record(data.env_vars)).map(([key, value]) => `${key}=${text(value)}`).join('\n'), terminals: Array.isArray(data.terminals) ? data.terminals.map(record) : [] };
}
export type CatalogDraft = ReturnType<typeof catalogDraft>;
export function catalogDefinition(draft: CatalogDraft, kind: CatalogKind): UnknownRecord {
  const name = draft.name.trim();
  if (!name || !/^[a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)*$/.test(name) || name.split('/').some((part) => part === '.' || part === '..')) throw new Error('Use a name with letters, numbers, dots, dashes or underscores; folders may be separated by /.');
  const values: UnknownRecord = { ...draft.values, name, priorities: draft.priorities.split('\n').map((value) => value.trim()).filter(Boolean) };
  // Blank numeric values inherit; an explicit zero remains an authored value.
  for (const key of ['max_turns', 'idle_timeout']) {
    if (!text(values[key]).trim()) delete values[key];
    else { const value = Number(values[key]); if (!Number.isSafeInteger(value) || value < 0) throw new Error(`${key === 'max_turns' ? 'Max turns' : 'Idle timeout'} must be a nonnegative whole number.`); values[key] = value; }
  }
  if (kind !== 'specialization') {
    const environment: Record<string, string> = {};
    for (const line of draft.environment.split('\n')) { if (!line.trim()) continue; const index = line.indexOf('='); const key = line.slice(0, index).trim(); if (index < 1 || !key) throw new Error('Enter environment values as KEY=VALUE, one per line.'); Object.defineProperty(environment, key, { value: line.slice(index + 1), enumerable: true, configurable: true, writable: true }); }
    values.env_vars = environment;
    values.terminals = draft.terminals.map((item) => ({ name: text(item.name).trim(), command: text(item.command).trim() })).filter((item) => item.name || item.command);
  }
  return values;
}
