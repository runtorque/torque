import { describe, expect, it } from 'vitest';
import { acceptSettings, createSettingsEditor, editSetting, inheritSetting, refreshSettings, settingsCommands, settingsDirty, settingsValues, validateSettingsFrame } from './agentSettingsModel';
import { toAgentViewModel } from './model';
const agent = toAgentViewModel('eng', { name: 'Original', kind: 'engineer' });
const resolved = { model: { value: 'override', origin: 'per-agent', inherited: { value: 'group-model', origin: 'group' } }, provider: { value: 'generic', origin: 'group' }, heartbeat_interval: { value: 300, origin: 'default' } };
describe('per-agent settings edits', () => {
  it('keeps clean forms sparse, tracks explicit overrides and cancels identity reversals', () => {
    let editor = createSettingsEditor(agent, {}, {}, resolved);
    expect(settingsDirty(editor)).toBe(false); expect(settingsCommands(editor, agent)).toEqual([]);
    editor = editSetting(editor, 'name', 'Changed'); editor = editSetting(editor, 'name', ' Original ');
    expect(settingsDirty(editor)).toBe(false);
    editor = editSetting(editor, 'provider', 'generic');
    expect(settingsCommands(editor, agent)).toEqual([{ cmd: 'update_agent_settings', agent_id: 'eng', settings: { provider: 'generic' } }]);
    expect(settingsDirty(inheritSetting(editor, 'provider'))).toBe(false);
  });
  it('refreshes clean fields, retains explicit drafts and follows new inheritance for staged resets', () => {
    let editor = createSettingsEditor(agent, {}, {}, resolved);
    editor = inheritSetting(editSetting(editor, 'provider', 'draft'), 'model');
    const latest = { model: { value: 'external', origin: 'per-agent', inherited: { value: 'new-group-model', origin: 'group' } }, provider: { value: 'external-provider', origin: 'group' }, heartbeat_interval: { value: 0, origin: 'group' } };
    editor = refreshSettings(editor, settingsValues({}, {}, latest), latest);
    expect(editor.draft).toMatchObject({ model: 'new-group-model', provider: 'draft', heartbeat_interval: '0' });
    expect(settingsCommands(editor, agent)).toEqual([{ cmd: 'update_agent_settings', agent_id: 'eng', settings: { provider: 'draft', model: null } }]);
  });
  it('acknowledges only completed scopes, allowing later external changes and explicit reversals', () => {
    let editor = createSettingsEditor(agent, {}, {}, resolved);
    for (const [key, value] of [['model', 'saved'], ['heartbeat_interval', '0'], ['engineer_specializations', 'frontend, ui-ux']]) editor = editSetting(editor, key!, value!);
    editor = acceptSettings(editor, { model: 'saved' }, ['model']);
    editor = refreshSettings(editor, { model: 'external' });
    expect(editor.draft.model).toBe('external');
    expect(settingsCommands(editor, agent)).toEqual([{ cmd: 'update_agent_digest_settings', agent_id: 'eng', settings: { heartbeat_interval: 0 } }, { cmd: 'set_engineer_specializations', engineer_id: 'eng', specializations: ['frontend', 'ui-ux'] }]);
    editor = editSetting(editor, 'model', 'override');
    expect(settingsCommands(editor, agent)[0]).toMatchObject({ settings: { model: 'override' } });
  });
  it('preserves explicit zero, false, empty lists and inheritance separately', () => {
    let editor = createSettingsEditor(agent, {}, {}, resolved);
    for (const [key, value] of [['heartbeat_interval', '0'], ['push_interval', ''], ['paused', 'false'], ['enabled_events', '']]) editor = editSetting(editor, key!, value!);
    expect(settingsCommands(editor, agent)).toEqual([{ cmd: 'update_agent_digest_settings', agent_id: 'eng', settings: { heartbeat_interval: 0, push_interval: null, paused: false, enabled_events: [] } }]);
    expect(settingsValues({ model: 'wrong' }, {}, { model: { value: null, origin: 'default' } })).toEqual({ model: '' });
  });
  it('rejects invalid numeric drafts before transport instead of serializing NaN as inheritance', () => {
    for (const value of ['oops', 'Infinity', '-1', '1.5', '1e100']) expect(() => settingsCommands(editSetting(createSettingsEditor(agent, {}, {}, resolved), 'heartbeat_interval', value), agent)).toThrow('whole number');
    expect(() => settingsCommands(editSetting(createSettingsEditor(agent, {}, {}, resolved), 'default_worker_concurrency', '0'), agent)).toThrow('at least 1');
  });
  it('rejects responses for another agent or with malformed resolved settings', () => {
    for (const frame of [{ type: 'agent_settings', agent_id: 'other', resolved: {} }, { type: 'ok', agent_id: 'eng', resolved: {} }, { type: 'agent_settings', agent_id: 'eng', resolved: [] }]) expect(() => validateSettingsFrame(frame, 'eng')).toThrow('did not match');
    expect(() => validateSettingsFrame({ type: 'agent_settings', agent_id: 'eng', resolved: {} }, 'eng')).not.toThrow();
  });
});
