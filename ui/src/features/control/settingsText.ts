import type { UnknownRecord } from '../../protocol';

type SettingsScope = 'global' | 'group' | 'engineer' | 'architect';
// Match Classic's explicit settings collectors. This is deliberately scoped:
// prompts, environment values and arbitrary map entries keep their whitespace.
const trimmedFields: Record<SettingsScope, readonly string[]> = {
  global: ['default_command', 'relay_url', 'relay_daemon_id', 'relay_credential_id', 'relay_private_key_path'],
  group: ['default_directory', 'env_file', 'agent_directory', 'agent_env_file', 'agent_boot_command', 'agent_model', 'agent_reasoning_effort', 'worker_boot_command', 'worker_model', 'worker_reasoning_effort', 'worktree_base_dir', 'worktree_base_branch', 'board_default_action', 'board_default_lane', 'dispatch_lane'],
  engineer: ['engineer_boot_command', 'engineer_model', 'engineer_reasoning_effort', 'engineer_directory'],
  architect: ['architect_boot_command', 'architect_model', 'architect_reasoning_effort', 'architect_directory', 'architect_journal_checkpoint_frequency'],
};
const emptyDefaults: Partial<Record<SettingsScope, Record<string, string>>> = {
  group: { worktree_base_dir: '.torque/worktrees', dispatch_lane: 'In Progress' },
  architect: { architect_journal_checkpoint_frequency: 'every_10_actions' },
};
export function normalizeSettingsText(scope: SettingsScope, changes: UnknownRecord): UnknownRecord {
  const next = { ...changes };
  for (const key of trimmedFields[scope]) {
    if (typeof next[key] === 'string') next[key] = next[key].trim() || emptyDefaults[scope]?.[key] || '';
  }
  const list = scope === 'group' ? 'board_default_labels' : scope === 'global' ? 'mcp_call_log_full_capture_tools' : '';
  if (list && Array.isArray(next[list])) next[list] = next[list].map((value: unknown) => typeof value === 'string' ? value.trim() : value).filter((value: unknown) => value !== '');
  if (scope === 'group' && next.board_sync_github && typeof next.board_sync_github === 'object' && !Array.isArray(next.board_sync_github)) {
    const github = { ...next.board_sync_github as UnknownRecord };
    for (const key of ['github_repo', 'github_project_owner', 'github_project_id', 'github_project_status_field']) {
      if (typeof github[key] === 'string') github[key] = github[key].trim() || (key === 'github_project_status_field' ? 'Status' : '');
    }
    next.board_sync_github = github;
  }
  return next;
}
