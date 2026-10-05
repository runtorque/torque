import type { TorqueCommand, UnknownRecord } from '../../protocol';
import type { AgentViewModel } from './model';
export const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
export const settingText = (value: unknown): string => Array.isArray(value) ? value.join(', ') : typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : '';
export const splitSettingsList = (value: string): string[] => value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
export const principalSettingFields = [
  ['provider', 'Provider', 'text'],
  ['boot_command', 'Boot command', 'text'],
  ['model', 'Model', 'text'],
  ['reasoning_effort', 'Reasoning effort', 'text'],
  ['fast_mode', 'Fast mode', 'fast'],
  ['autonomy_mode', 'Autonomy mode', 'text'],
  ['custom_instructions', 'Custom instructions', 'textarea'],
] as const;

export const engineerSettingFields = [
  ['default_worker_concurrency', 'Default worker concurrency', 'number'],
  ['wave_size_preference', 'Wave size preference', 'text'],
  ['same_agent_follow_up_preference', 'Same-agent follow-up', 'text'],
  ['escalation_style', 'Escalation style', 'text'],
  ['engineer_can_override_worker_provider', 'Worker provider override', 'boolean'],
  ['restrict_to_created_agents', 'Restrict to created agents', 'boolean'],
] as const;

export const digestSettingFields = [
  ['paused', 'Digest delivery', 'paused'],
  ['push_interval', 'Push interval (seconds)', 'number'],
  ['max_interval', 'Maximum interval (seconds)', 'number'],
  ['heartbeat_interval', 'Heartbeat interval (seconds)', 'number'],
  ['digest_verbosity', 'Digest verbosity', 'text'],
  ['enabled_events', 'Enabled events', 'list'],
] as const;


export interface SettingsEditor {
  baseline: Record<string, string>;
  draft: Record<string, string>;
  resolved: UnknownRecord;
  intents: Record<string, 'set' | 'inherit'>;
  relaunch: boolean;
}
export const identityKeys = ['name', 'icon', 'tab_color'];
export const allSettingFields = [...principalSettingFields, ...engineerSettingFields, ...digestSettingFields];
// Ignore digest delivery counters and other runtime fields when deciding to reread settings.
const refreshKeys = [...new Set([...allSettingFields.map(([key]) => key), 'agent_class_id', 'engineer_specializations', 'override_fields'].flatMap((key) => [key, `agent_${key}`, `engineer_${key}`, `architect_${key}`]))];
export function settingsRefreshValues(value: unknown): UnknownRecord {
  const source = record(value);
  return Object.fromEntries(refreshKeys.filter((key) => key in source).map((key) => [key, source[key]]));
}
export function identityValues(agent: AgentViewModel): Record<string, string> {
  return { name: agent.name, icon: settingText(agent.raw.icon), tab_color: settingText(agent.raw.tab_color) };
}
export function settingsValues(raw: unknown, digest: unknown, resolved: UnknownRecord): Record<string, string> {
  const fallback = { ...record(raw), ...record(digest) };
  return Object.fromEntries([...allSettingFields.map(([key]) => key), 'engineer_specializations'].filter((key) => key in resolved || key in fallback).map((key) => [key, settingText('value' in record(resolved[key]) ? record(resolved[key]).value : fallback[key])]));
}
export function createSettingsEditor(agent: AgentViewModel, raw: unknown, digest: unknown, resolved: unknown): SettingsEditor {
  const metadata = record(resolved);
  const baseline = { ...Object.fromEntries(allSettingFields.map(([key]) => [key, ''])), ...identityValues(agent), engineer_specializations: settingText(agent.raw.engineer_specializations), ...settingsValues(raw, digest, metadata) };
  return { baseline, draft: { ...baseline }, resolved: metadata, intents: {}, relaunch: false };
}
const inheritedValue = (resolved: UnknownRecord, key: string) => {
  const entry = record(resolved[key]);
  return settingText(entry.origin === 'per-agent' ? record(entry.inherited).value : entry.value);
};
export function editSetting(editor: SettingsEditor, key: string, value: string): SettingsEditor {
  const intents = { ...editor.intents };
  const canonical = (v: string) => key === 'engineer_specializations' ? splitSettingsList(v).join(', ') : identityKeys.includes(key) ? v.trim() : v;
  if (canonical(value) === canonical(editor.baseline[key] ?? '') && (identityKeys.includes(key) || key === 'engineer_specializations' || record(editor.resolved[key]).origin === 'per-agent')) delete intents[key];
  else intents[key] = 'set';
  return { ...editor, draft: { ...editor.draft, [key]: value }, intents };
}
export function inheritSetting(editor: SettingsEditor, key: string): SettingsEditor {
  const intents = { ...editor.intents };
  if (record(editor.resolved[key]).origin === 'per-agent') intents[key] = 'inherit'; else delete intents[key];
  return { ...editor, draft: { ...editor.draft, [key]: inheritedValue(editor.resolved, key) }, intents };
}
/** Reconcile only untouched fields; a pending reset follows the latest inherited value. */
export function refreshSettings(editor: SettingsEditor, values: Record<string, string>, metadata: UnknownRecord = {}): SettingsEditor {
  const resolved = { ...editor.resolved, ...metadata }; const draft = { ...editor.draft };
  for (const [key, value] of Object.entries(values)) {
    if (!editor.intents[key]) draft[key] = value;
    else if (editor.intents[key] === 'inherit') draft[key] = inheritedValue(resolved, key);
  }
  return { ...editor, baseline: { ...editor.baseline, ...values }, draft, resolved };
}
export function acceptSettings(editor: SettingsEditor, values: Record<string, string>, keys: string[], metadata: UnknownRecord = {}): SettingsEditor {
  const intents = { ...editor.intents }; for (const key of keys) delete intents[key];
  return refreshSettings({ ...editor, intents }, values, metadata);
}
export const settingsDirty = (editor: SettingsEditor): boolean => Boolean(Object.keys(editor.intents).length || editor.relaunch);
export function validateSettingsFrame(frame: UnknownRecord, agentId: string): void {
  if (frame.type !== 'agent_settings' || frame.agent_id !== agentId || !frame.resolved || typeof frame.resolved !== 'object' || Array.isArray(frame.resolved)) throw new Error('Settings response did not match this agent.');
}
export function settingsCommands(editor: SettingsEditor, agent: AgentViewModel): TorqueCommand[] {
  const commands: TorqueCommand[] = []; const identity: UnknownRecord = {};
  for (const key of identityKeys) if (editor.intents[key]) identity[key] = editor.draft[key]?.trim() ?? '';
  if ('name' in identity && agent.kind === 'engineer') { commands.push({ cmd: 'rename_engineer', id: agent.id, new_name: identity.name }); delete identity.name; }
  if (Object.keys(identity).length) commands.push({ cmd: 'update_agent', id: agent.id, ...identity });
  if (['architect', 'engineer'].includes(agent.kind)) {
    for (const [cmd, fields] of [['update_agent_settings', [...principalSettingFields, ...(agent.kind === 'engineer' ? engineerSettingFields : [])]], ['update_agent_digest_settings', digestSettingFields]] as const) {
      const settings: UnknownRecord = {};
      for (const [key, label, type] of fields) {
        if (!editor.intents[key]) continue;
        const value = editor.draft[key] ?? '';
        if (type === 'number' && editor.intents[key] !== 'inherit' && value.trim() && (!Number.isSafeInteger(Number(value)) || Number(value) < (key === 'default_worker_concurrency' ? 1 : 0))) throw new Error(`${label} must be a whole number of at least ${key === 'default_worker_concurrency' ? 1 : 0}, or blank to inherit.`);
        settings[key] = editor.intents[key] === 'inherit' ? null : type === 'number' ? (value.trim() ? Number(value) : null) : type === 'list' ? splitSettingsList(value) : (type === 'boolean' || type === 'paused') ? (value ? value === 'true' : null) : value;
      }
      if (Object.keys(settings).length) commands.push({ cmd, agent_id: agent.id, settings });
    }
  }
  if (agent.kind === 'engineer' && editor.intents.engineer_specializations) commands.push({ cmd: 'set_engineer_specializations', engineer_id: agent.id, specializations: splitSettingsList(editor.draft.engineer_specializations ?? '') });
  if (editor.relaunch) commands.push({ cmd: 'relaunch_agent', id: agent.id });
  return commands;
}
