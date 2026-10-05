import type { UnknownRecord } from '../../protocol';
export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function list(value: unknown): Record<string, unknown>[] {
  const values = Array.isArray(value) ? value : Object.values(record(value));
  return values.map(record).filter((item) => Object.keys(item).length > 0);
}

export function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}

export function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => text(item)).filter(Boolean) : [];
}

export function promptJob(value: unknown): string {
  const prompt = record(value);
  return text(prompt.job) || text(prompt.system) || text(prompt.preamble) || text(value);
}

export function classLabel(item: Record<string, unknown>): string {
  return text(item.display_name) || text(item.primary_identity_label) || text(item.name) || text(item.id, 'Agent Class');
}

export function classRules(item: Record<string, unknown>): Record<string, string> {
  const acl = record(item.acl);
  const result: Record<string, string> = {};
  list(acl.rules).forEach((rule) => {
    const id = text(rule.capability);
    if (id) result[id] = text(rule.scope);
    strings(rule.capabilities).forEach((capability) => { result[capability] = text(rule.scope); });
  });
  Object.entries(record(acl.capabilities)).forEach(([id, scope]) => { result[id] = text(scope); });
  return result;
}

export function availableScopes(capability: Record<string, unknown>, kind: string): string[] {
  const order = ['self', 'children', 'group', 'global'];
  const scopes = strings(capability.scopes);
  if (!scopes.length) return [];
  const maximum = text(record(capability.maximum_scopes)[kind]) || text(capability.maximum_scope);
  const maxIndex = order.indexOf(maximum);
  return maxIndex < 0 ? scopes : scopes.filter((scope) => order.indexOf(scope) <= maxIndex);
}


export function classDraft(item: UnknownRecord) {
  item = record(item.authoring_definition).id ? { ...item, ...record(item.authoring_definition) } : item;
  const ui = record(record(item.metadata).ui);
  return { id: text(item.id), version: text(item.version, '1'), kind: text(item.base_kind, 'worker'), displayName: item.id ? classLabel(item) : '', description: text(item.description, text(item.purpose)), lifecycle: text(item.lifecycle, 'stable'), job: promptJob(item.prompt), aclMode: text(record(item.acl).mode, 'allow'), selected: classRules(item), ui: { label: text(ui.label), icon: text(ui.icon), badge: text(ui.badge), color: text(ui.color) }, scratchOnly: record(item.draft).scratch_only === true || item.scratch_only === true };
}
export type ClassDraft = ReturnType<typeof classDraft>;
export function classDefinition(item: UnknownRecord, draft: ClassDraft): UnknownRecord {
  item = record(item.authoring_definition).id ? { ...item, ...record(item.authoring_definition) } : item;
  const metadata = { ...record(item.metadata), ui: { ...record(record(item.metadata).ui), ...draft.ui } };
  const marker = { ...record(item.draft), scratch_only: draft.scratchOnly };
  return {
    agent_class_schema_version: 5, id: draft.id.trim(), version: draft.version.trim() || '1', base_kind: draft.kind,
    runtime: { ...record(item.runtime), base_kind: draft.kind }, identity: record(item.identity), operator_summary: record(item.operator_summary),
    display_name: draft.displayName.trim(), description: draft.description.trim(), lifecycle: draft.lifecycle,
    prompt: { ...record(item.prompt), job: draft.job }, metadata, ...(draft.lifecycle === 'draft' || draft.scratchOnly ? { draft: marker } : {}), warnings: strings(record(item.authoring_definition).warnings ?? item.class_warnings),
    acl: { mode: draft.aclMode, rules: Object.entries(draft.selected).map(([capability, scope]) => ({ capability, ...(scope ? { scope } : {}) })) },
  };
}
export function duplicateClass(item: UnknownRecord): UnknownRecord {
  item = { ...item }; delete item.authoring_definition;
  const metadata = { ...record(item.metadata) }; delete metadata.archived; delete metadata.disabled; delete metadata.archived_at;
  return { ...item, id: `${text(item.id, 'agent-class')}-copy`, display_name: `${classLabel(item)} Copy`, metadata, builtin: false, source: 'project', archived: false, disabled: false };
}
export function classError(frame: UnknownRecord): string {
  return text(frame.message) || list(frame.errors ?? frame.issues).map((issue) => text(issue.message, text(issue.code))).join(' ') || 'Agent Class request failed.';
}
