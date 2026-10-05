import { describe, expect, it } from 'vitest';
import { normalizeSettingsText } from './settingsText';

describe('Classic settings text boundaries', () => {
  it.each([
    ['global', { default_command: '  runner --arg "two words"  ', relay_private_key_path: '  /keys/my key  ' }, { default_command: 'runner --arg "two words"', relay_private_key_path: '/keys/my key' }],
    ['group', { default_directory: '  /my project  ', agent_env_file: '  .env.local  ', worktree_base_branch: '  release  ', board_default_action: '  feature/implement  ', board_default_lane: '  In Review  ', board_default_labels: ['  one  ', '', '  ', 'two words'] }, { default_directory: '/my project', agent_env_file: '.env.local', worktree_base_branch: 'release', board_default_action: 'feature/implement', board_default_lane: 'In Review', board_default_labels: ['one', 'two words'] }],
    ['engineer', { engineer_directory: '  /my project  ', engineer_boot_command: '  runner --arg  ', engineer_model: '  custom  ' }, { engineer_directory: '/my project', engineer_boot_command: 'runner --arg', engineer_model: 'custom' }],
    ['architect', { architect_directory: '  /my project  ', architect_boot_command: '  runner --arg  ', architect_reasoning_effort: '  high  ' }, { architect_directory: '/my project', architect_boot_command: 'runner --arg', architect_reasoning_effort: 'high' }],
  ] as const)('normalizes %s payloads without changing the editing source', (scope, draft, expected) => {
    const before = structuredClone(draft);
    expect(normalizeSettingsText(scope, draft)).toEqual(expected); expect(draft).toEqual(before);
  });
  it('uses Classic empty defaults only for explicitly submitted keys', () => {
    expect(normalizeSettingsText('group', {})).toEqual({});
    expect(normalizeSettingsText('group', { default_directory: ' ', worktree_base_dir: ' ', dispatch_lane: ' ' })).toEqual({ default_directory: '', worktree_base_dir: '.torque/worktrees', dispatch_lane: 'In Progress' });
    expect(normalizeSettingsText('architect', { architect_journal_checkpoint_frequency: ' ' })).toEqual({ architect_journal_checkpoint_frequency: 'every_10_actions' });
  });
  it('preserves instruction text, environment values, unknown fields and map entries', () => {
    const values = { custom_instructions: '  first\n  second\n', architect_custom_instructions: '  exact  ', env_vars: { default_directory: '  literal  ' }, worktree_merge_instructions: '  exact  ', future_field: '  exact  ', board_sync_github: { github_repo: '  owner/repo  ', github_project_status_field: '  ', github_lane_status_map: { ' In Review ': '  exact  ' } } };
    const before = structuredClone(values);
    expect(normalizeSettingsText('group', values)).toEqual({ ...values, board_sync_github: { ...values.board_sync_github, github_repo: 'owner/repo', github_project_status_field: 'Status' } });
    expect(values).toEqual(before);
    expect(normalizeSettingsText('engineer', { default_directory: '  literal  ', custom_instructions: values.custom_instructions })).toEqual({ default_directory: '  literal  ', custom_instructions: values.custom_instructions });
  });
  it('trims full-capture tool names without adding absent settings', () => {
    expect(normalizeSettingsText('global', { mcp_call_log_full_capture_tools: ['  mcp__torque__context  ', ' '] })).toEqual({ mcp_call_log_full_capture_tools: ['mcp__torque__context'] });
  });
});
