import type { TorqueCommand, UnknownRecord } from '../../protocol';
const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
const text = (value: unknown): string => typeof value === 'string' ? value : '';
export const initialLaunchDraft = {
  provider: '', bootCommand: '', model: '', reasoningEffort: '', fastMode: 'inherit', directory: '', shell: '', environment: '', commandArgs: '', initScript: '', icon: '',
  worktree: false, worktreeBaseDir: '', worktreeBaseBranch: '', worktreeName: '', autoCheckpoint: true, checkpointOnProgress: false, mergeSquash: true,
};
export type LaunchDraft = typeof initialLaunchDraft;
export function resolvedLaunchDraft(config: UnknownRecord): LaunchDraft {
  return {
    provider: text(config.provider), bootCommand: text(config.command), model: text(config.model), reasoningEffort: text(config.reasoning_effort), fastMode: text(config.fast_mode) || 'inherit',
    directory: text(config.directory), shell: text(config.shell), environment: Object.entries(record(config.env_vars)).map(([key, value]) => `${key}=${typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : ''}`).join('\n'), commandArgs: text(config.command_args), initScript: text(config.init_script), icon: text(config.icon),
    worktree: config.worktree === true, worktreeBaseDir: text(config.worktree_base_dir), worktreeBaseBranch: text(config.worktree_base_branch), worktreeName: text(config.worktree_name), autoCheckpoint: config.worktree_auto_checkpoint === true, checkpointOnProgress: config.checkpoint_on_progress === true, mergeSquash: config.worktree_merge_squash !== false,
  };
}
export function validateTemplateResponse(frame: UnknownRecord, group: string, name: string): UnknownRecord {
  if (frame.type !== 'template_rendered' || frame.name !== name || frame.group !== group || !frame.config || typeof frame.config !== 'object' || Array.isArray(frame.config)) throw new Error('The resolved configuration did not match this group and role.');
  return record(frame.config);
}
/** A hire request acknowledges an approval item, not an already-created Engineer. */
export function createdTarget(frame: UnknownRecord, command: TorqueCommand, kind: string): string {
  if (command.cmd === 'architect_engineer_hire') {
    if (!text(frame.hire_id) || frame.status !== 'pending') throw new Error('Could not confirm the hire request. The draft is retained.');
    return '';
  }
  if (command.cmd === 'create_agent_from_class' && (frame.type !== 'agent_class_launch' || frame.base_kind !== kind)) throw new Error('Could not confirm the Agent Class launch.');
  const result = command.cmd === 'create_agent_from_class' ? record(frame.agent) : frame;
  if (!text(result.id) || result.name !== command.name || result.kind !== kind || (kind === 'terminal' && (frame.type !== 'terminal_created' || frame.parent_id !== (command.parent_id ?? '')))) throw new Error('Could not confirm the created target. The draft is retained.');
  return text(result.id);
}
