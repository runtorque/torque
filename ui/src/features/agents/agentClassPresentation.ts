import type { UnknownRecord } from '../../protocol';

export const classRecord = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
export const classText = (value: unknown): string => typeof value === 'string' ? value.trim() : typeof value === 'number' ? String(value) : '';
export const classIdentity = (item: UnknownRecord): string => classText(item.primary_identity_label) || classText(item.primary_display_name) || classText(item.display_name) || classText(item.name) || classText(item.id);
export function classKindLabel(item: UnknownRecord, kind: string): string {
  const base = classText(item.base_kind) || kind;
  return classText(item.secondary_base_kind_label) || classText(classRecord(item.secondary_base_kind_metadata).base_kind_label) || ({ worker: 'Worker', engineer: 'Engineer', architect: 'Architect' }[base] ?? base);
}
export function classStatusLabel(item: UnknownRecord): string {
  const metadata = classRecord(item.metadata);
  return item.archived || item.disabled || metadata.archived || metadata.disabled || metadata.archived_at ? 'archived' : classText(item.status) || classText(item.lifecycle) || 'full';
}
const noticeKey = (value: string) => value.toLowerCase().replace(/&amp;/g, '&').replace(/\bagent classes?\b/g, 'agent class').replace(/[^\w]+/g, ' ').trim();
export function classWarnings(value: unknown): string[] {
  const seen = new Set<string>();
  return (Array.isArray(value) ? value : []).flatMap((entry: unknown) => {
    const text = typeof entry === 'string' ? entry.trim() : '';
    const key = noticeKey(text);
    // Match the maintained Classic surface: generic connector-governance copy
    // is intentionally omitted, while actionable connector warnings remain.
    const genericConnector = (key.includes('external connector') || key.includes('connector exposure')) && ['not govern', 'not enforced', 'separate', 'manage connector access separately'].some((phrase) => key.includes(phrase));
    if (!key || seen.has(key) || genericConnector) return [];
    seen.add(key); return [text];
  });
}
export function selectedClassPreview(classes: UnknownRecord[], selectedId: string, kind: string, status: UnknownRecord): UnknownRecord {
  const id = selectedId || `default-${kind}`;
  return classes.find((item) => item.id === id) ?? [classRecord(status.assigned_class), classRecord(status.effective_class)].find((item) => item.id === id) ?? {};
}
interface Permission { capability: string; scope: string }
function rules(value: unknown): Permission[] {
  return (Array.isArray(value) ? value : []).map((entry: unknown) => { const item = classRecord(entry); return { capability: classText(item.capability), scope: classText(item.scope) }; }).filter((item) => item.capability);
}
export function classPermissions(item: UnknownRecord) {
  const acl = classRecord(item.acl); const authored = classRecord(classRecord(item.authoring_definition).acl);
  const authority = classRecord(item.effective_authority);
  const mode = classText(authored.mode) || classText(acl.mode) || classText(authority.acl_mode);
  const rawGrants = authority.capabilities ?? acl.capabilities;
  const hasGrants = rawGrants !== null && typeof rawGrants === 'object' && !Array.isArray(rawGrants);
  // The daemon's compact ACL rules are resolved grants, not authored denials.
  const grants = hasGrants ? Object.entries(classRecord(rawGrants)).map(([capability, scope]) => ({ capability, scope: classText(scope) })) : rules(acl.rules);
  return {
    mode, grants,
    available: hasGrants || Array.isArray(acl.rules),
    denials: mode === 'deny' && authored.mode === 'deny' ? rules(authored.rules) : [],
    denialsAvailable: mode === 'allow' || authored.mode === 'deny',
  };
}
export function classTime(value: unknown): { iso: string; label: string } | null {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric) || numeric <= 0) return null;
  const date = new Date(numeric < 1_000_000_000_000 ? numeric * 1000 : numeric);
  return Number.isNaN(date.getTime()) ? null : { iso: date.toISOString(), label: date.toLocaleString() };
}
