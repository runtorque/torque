import type { UnknownRecord } from '../../protocol';

export interface LaunchContext { group: UnknownRecord; runtime: UnknownRecord }
function text(value: unknown): string { return typeof value === 'string' ? value.trim() : ''; }
function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
function token(value: unknown): string { return text(value).split(/\s+/)[0] ?? ''; }

/** Display-only inheritance; never copy resolved values into editable overrides. */
export function launchInheritance(key: string, values: UnknownRecord, providers: unknown[], context?: LaunchContext): { choices: UnknownRecord; placeholder: string } {
  const unchanged = { choices: values, placeholder: '' };
  const match = /^(agent|worker|engineer|architect)_(provider|model|reasoning_effort|boot_command|directory|shell|env_file)$/.exec(key);
  if (!context || !match) return unchanged;
  const prefix = `${match[1]}_`; const field = match[2]; const shared = context.group; const role = prefix !== 'agent_';
  const rows = providers.map(record); const runtimeCommand = text(context.runtime.default_command);
  const runtimeProvider = token(runtimeCommand) ? rows.find((row) => token(row.command) === token(runtimeCommand)) : undefined;
  const inheritedProvider = (role ? text(shared.agent_provider) : '') || text(runtimeProvider?.name);
  const providerName = text(values[`${prefix}provider`]) || inheritedProvider;
  const provider = rows.find((row) => row.name === providerName);
  const models = Array.isArray(provider?.models) ? provider.models.map(record) : [];
  const defaultModel = models.find((row) => row.is_default === true);
  const inheritedModel = (role ? text(shared.agent_model) : '') || text(defaultModel?.id) || text(defaultModel?.model);
  const modelId = text(values[`${prefix}model`]) || inheritedModel;
  const model = models.find((row) => (row.id || row.model) === modelId);
  const inheritedModelRow = models.find((row) => (row.id || row.model) === inheritedModel);
  const choices = { ...values, [`${prefix}provider`]: providerName, [`${prefix}model`]: modelId };
  let fallback = '';
  if (field === 'provider') { const inherited = rows.find((row) => row.name === inheritedProvider); fallback = text(inherited?.display_name) || inheritedProvider || 'system default'; }
  if (field === 'model') fallback = text(inheritedModelRow?.display_name) || text(inheritedModelRow?.displayName) || inheritedModel || 'provider default';
  if (field === 'reasoning_effort') fallback = (role ? text(shared.agent_reasoning_effort) : '') || text(model?.default_reasoning_effort) || 'provider default';
  if (field === 'boot_command') fallback = (role ? text(shared.agent_boot_command) : '') || text(provider?.command) || runtimeCommand || 'system default';
  if (field === 'directory') fallback = (role ? text(shared.agent_directory) : '') || text(shared.default_directory) || 'current directory';
  if (field === 'shell') fallback = (role ? text(shared.agent_shell) : '') || text(shared.shell) || 'system default';
  if (field === 'env_file') fallback = (role ? text(shared.agent_env_file) : '') || text(shared.env_file) || 'none';
  return { choices, placeholder: `Inherit · ${fallback}` };
}
