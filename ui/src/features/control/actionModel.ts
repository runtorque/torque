import type { UnknownRecord } from '../../protocol';
import { record, text } from './agentClassesModel';
export function actionDraft(data: UnknownRecord = {}, name = '', scope = 'project') {
  const prompt = text(data.prompt) || ['task', 'instructions', 'context', 'criteria'].map((key) => text(data[key])).filter(Boolean).join('\n\n');
  return { name, scope, values: { ...data, prompt: prompt || (name ? '' : '{{ TASK }}'), review_required_above_loc: text(data.review_required_above_loc), max_depth: text(data.max_depth) } as UnknownRecord, labels: Array.isArray(data.labels) ? data.labels.map((value) => text(value)).join(', ') : '', transitions: Array.isArray(data.transitions) ? data.transitions.map(record) : [], terminals: Array.isArray(data.terminals) ? data.terminals.map(record) : [] };
}
export type ActionDraft = ReturnType<typeof actionDraft>;
export function actionNumber(value: unknown, label: string): number | undefined {
  if (!text(value).trim()) return undefined;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 0) throw new Error(`${label} must be a nonnegative whole number.`);
  return number;
}
export function actionDefinition(draft: ActionDraft, saving = true): UnknownRecord {
  const name = draft.name.trim();
  if (saving && (!/^[a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)*$/.test(name) || name.split('/').some((part) => part === '.' || part === '..'))) throw new Error('Enter an action name using letters, numbers, dots, dashes, underscores or namespace folders.');
  if (saving && !/\{\{\s*(?:TASK|torque\.task\.title)\s*(?:\|[^}]*)?\}\}/.test(text(draft.values.prompt))) throw new Error('Prompt must contain {{ TASK }} or {{ torque.task.title }}.');
  const values: UnknownRecord = { ...draft.values, name, labels: draft.labels.split(',').map((label) => label.trim()).filter(Boolean) };
  for (const [key, label] of [['review_required_above_loc', 'Review above LOC'], ['max_depth', 'Maximum pipeline depth']] as const) { const number = actionNumber(values[key], label); if (number === undefined) delete values[key]; else values[key] = number; }
  values.transitions = draft.transitions.map((row, index) => {
    const item = { ...row };
    if (item.ask === true) { for (const key of ['action', 'target', 'status', 'loc_gate']) delete item[key]; }
    else {
      if (!text(item.action).trim()) throw new Error(`Choose an action for transition ${index + 1}.`);
      if (item.loc_gate && typeof item.loc_gate === 'object') { const gate = { ...record(item.loc_gate) }; for (const [key, label] of [['ship_direct_max', 'Ship direct up to LOC'], ['review_default_above', 'Review above LOC']] as const) { const number = actionNumber(gate[key], `Transition ${index + 1} ${label}`); if (number === undefined) delete gate[key]; else gate[key] = number; } item.loc_gate = gate; }
    }
    return item;
  });
  values.terminals = draft.terminals.map((item) => ({ ...item, name: text(item.name).trim() || 'shell', command: text(item.command) }));
  return values;
}
