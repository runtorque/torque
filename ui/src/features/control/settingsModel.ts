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
