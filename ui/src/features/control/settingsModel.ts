import type { UnknownRecord } from '../../protocol';

export const runtimeSettingKeys = ['group', 'engineer_agent_id', 'engineer_hint_snoozes', 'default_lanes'];
export function editableSettings(values: UnknownRecord, omit: string[] = []): UnknownRecord {
  return Object.fromEntries(Object.entries(values).filter(([key]) => !runtimeSettingKeys.includes(key) && !key.startsWith('pending_') && !omit.includes(key)));
}
export function settingsEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const a = left as UnknownRecord; const b = right as UnknownRecord;
  return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((key) => key in b && settingsEqual(a[key], b[key]));
}
export function changedSettings(baseline: UnknownRecord, draft: UnknownRecord): UnknownRecord {
  return Object.fromEntries(Object.entries(editableSettings(draft)).filter(([key, value]) => !settingsEqual(baseline[key], value)));
}
export function resetSettings(draft: UnknownRecord, defaults: UnknownRecord, omit: string[] = []): UnknownRecord {
  return { ...draft, ...structuredClone(editableSettings(defaults, omit)) };
}

// Display fallbacks mirror the classic GitHub form; untouched keys stay absent.
export const githubSettingsDefaults: UnknownRecord = { github_repo: '', github_project_owner: '', github_project_number: 0, github_project_id: '', github_project_status_field: 'Status', github_lane_status_map: {}, github_close_issues_via_pr: true, github_create_missing_labels: true, github_assignee_map: {} };

/** Three-way reconciliation: retain local edits/removals, adopt untouched fields. */
export function reconcileSettings<T extends UnknownRecord>(before: UnknownRecord, draft: T, latest: T): T {
  const merge = (base: unknown, local: unknown, remote: unknown): unknown => {
    if (settingsEqual(base, local)) return remote;
    if (!base || !local || !remote || typeof base !== 'object' || typeof local !== 'object' || typeof remote !== 'object' || Array.isArray(base) || Array.isArray(local) || Array.isArray(remote)) return local;
    const a = base as UnknownRecord; const b = local as UnknownRecord; const c = remote as UnknownRecord;
    const next: UnknownRecord = {};
    for (const key of new Set([...Object.keys(a), ...Object.keys(b), ...Object.keys(c)])) {
      if (key in a && !(key in b)) continue;
      if (!(key in c) && settingsEqual(a[key], b[key])) continue;
      if (!(key in b)) next[key] = c[key];
      else next[key] = key in a ? merge(a[key], b[key], c[key]) : b[key];
    }
    return next;
  };
  return merge(before, draft, latest) as T;
}
function object(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
function settingText(value: unknown, fallback = '') { return typeof value === 'string' ? value : fallback; }
function numericDraft(value: unknown): number | string { return Number(value); }
export function primaryGlobalSettings(source: UnknownRecord) {
  return { xterm_scrollback: numericDraft(source.xterm_scrollback ?? 2000), max_pipeline_depth: numericDraft(source.max_pipeline_depth ?? 10), max_event_log: numericDraft(source.max_event_log ?? 500), metrics_enabled: source.metrics_enabled !== false, keybindings: object(source.keybindings), status_bar_visibility: { daemon_status: false, claude_usage: false, codex_usage: false, deploy: true, health: false, workload: false, tasks: true, attention: true, ...object(source.status_bar_visibility) } };
}
export function primaryGroupSettings(source: UnknownRecord) {
  return { default_directory: settingText(source.default_directory), max_agents: numericDraft(source.max_agents ?? 0), git_worktree: source.git_worktree === true, engineer_merge_mode: settingText(source.engineer_merge_mode, 'pr') };
}
export function relaySettingsDraft(source: UnknownRecord) {
  return { relay_enabled: source.relay_enabled === true, relay_url: settingText(source.relay_url), relay_daemon_id: settingText(source.relay_daemon_id), relay_credential_id: settingText(source.relay_credential_id), relay_private_key_path: settingText(source.relay_private_key_path) };
}
export function aiSettingsDraft(source: UnknownRecord, global: UnknownRecord) {
  const generation = object(source.generation); const anthropic = object(generation.anthropic); const openai = object(generation.openai_compatible); const embeddings = object(source.embeddings); const boot = object(source.boot_summary);
  return { ai_enabled: typeof source.enabled === 'boolean' ? source.enabled : global.ai_enabled === true, ai_generation_provider: settingText(generation.provider, settingText(global.ai_generation_provider, 'anthropic')), ai_anthropic_model: settingText(anthropic.model, settingText(global.ai_anthropic_model)), ai_openai_compatible_base_url: settingText(openai.base_url, settingText(global.ai_openai_compatible_base_url)), ai_openai_compatible_model: settingText(openai.model, settingText(global.ai_openai_compatible_model)), ai_embedding_model: settingText(embeddings.model_id, settingText(global.ai_embedding_model)), ai_embedding_runtime: settingText(embeddings.runtime, settingText(global.ai_embedding_runtime, 'sentence_transformers')), ai_index_corpus: object(object(source.index).corpus), ai_boot_summary_enabled: boot.enabled !== false, ai_boot_summary_min_interval_seconds: numericDraft(boot.min_interval_seconds ?? global.ai_boot_summary_min_interval_seconds ?? 300), ai_boot_summary_max_refreshes_per_hour: numericDraft(boot.max_refreshes_per_hour ?? global.ai_boot_summary_max_refreshes_per_hour ?? 6) };
}
export interface SettingsSnapshot { global: UnknownRecord; group: UnknownRecord; ai: UnknownRecord }
