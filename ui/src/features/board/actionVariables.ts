import { useState } from 'react';
import type { UnknownRecord } from '../../protocol';

export function actionVariableDefinitions(actions: unknown, name: string): UnknownRecord[] {
  const items: unknown[] = Array.isArray(actions) ? actions : Object.values((actions ?? {}) as UnknownRecord);
  const action = items.find((item) => item && typeof item === 'object' && (item as UnknownRecord).name === name) as UnknownRecord | undefined;
  return Array.isArray(action?.vars) ? (action.vars as unknown[]).filter((item): item is UnknownRecord => Boolean(item && typeof item === 'object' && 'name' in item && typeof item.name === 'string' && item.name !== 'TASK' && item.name !== 'torque')) : [];
}
export function resolveActionVariables(raw: string, definitions: UnknownRecord[]): UnknownRecord {
  let values: unknown;
  try { values = JSON.parse(raw); } catch { throw new Error('Action variables must be a JSON object.'); }
  if (!values || typeof values !== 'object' || Array.isArray(values)) throw new Error('Action variables must be a JSON object.');
  const defaults = Object.fromEntries(definitions.filter((item) => item.default !== undefined && item.default !== '').map((item) => [String(item.name), item.default]));
  return { ...defaults, ...values };
}
/** Drafts are scoped to each action and live only for this mounted editor. */
export function useActionVariables(name: string, initial: UnknownRecord = {}) {
  const [drafts, setDrafts] = useState<Record<string, string>>(() => ({ [name]: JSON.stringify(initial, null, 2) }));
  return [drafts[name] ?? '{}', (value: string) => setDrafts((current) => ({ ...current, [name]: value }))] as const;
}
