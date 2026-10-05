import type { UnknownRecord } from '../../protocol';

type Choice = { value: string; label: string };
function record(value: unknown): UnknownRecord { return value && typeof value === 'object' ? value as UnknownRecord : {}; }
function text(value: unknown): string { return typeof value === 'string' ? value : ''; }
function unique(items: Choice[]): Choice[] { const seen = new Set<string>(); return items.filter((item) => { if (!item.value || seen.has(item.value)) return false; seen.add(item.value); return true; }); }
export function providerChoices(providers: unknown[], key: string, values: UnknownRecord): Choice[] {
  const rows = providers.map(record);
  if (!/^(agent_|worker_|engineer_|architect_)?(provider|model|reasoning_effort)$/.test(key)) return [];
  if (/(^|_)provider$/.test(key)) return unique(rows.map((row) => ({ value: text(row.name), label: text(row.display_name) || text(row.name) })));
  const match = /^(.*?)(model|reasoning_effort)$/.exec(key);
  if (!match) return [];
  const prefix = match[1] || '';
  const provider = rows.find((row) => row.name === values[`${prefix}provider`]);
  if (!provider) return [];
  const models = Array.isArray(provider.models) ? provider.models.map(record) : [];
  if (match[2] === 'model') return unique(models.map((row) => ({ value: text(row.id) || text(row.model), label: text(row.display_name) || text(row.name) || text(row.description) || text(row.id) })));
  const model = models.find((row) => (row.id || row.model) === values[`${prefix}model`]);
  const modelEfforts = model?.reasoning_efforts ?? model?.supported_reasoning_efforts;
  const efforts = Array.isArray(modelEfforts) && modelEfforts.length ? modelEfforts : Array.isArray(provider.reasoning_efforts) ? provider.reasoning_efforts : [];
  return unique(efforts.map((item: unknown) => { const row = record(item); const value = typeof item === 'string' ? item : text(row.value) || text(row.reasoning_effort) || text(row.reasoningEffort); return { value, label: text(row.description) || value }; }));
}
