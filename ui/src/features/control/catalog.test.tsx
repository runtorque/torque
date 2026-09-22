import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { CatalogEditor } from './CatalogLibrary';
import { catalogDefinition, catalogDraft, catalogListKey, type CatalogKind } from './catalogModel';
import { record, text } from './agentClassesModel';
afterEach(() => vi.unstubAllGlobals());
const original: UnknownRecord = { name: 'reviewer', description: 'Project reviewer', preamble: 'Project preamble', model: 'preserved-model', system_prompt: 'Preserved full prompt', priorities: ['Correctness'], env_vars: { KEEP: 'yes' }, terminals: [{ name: 'watch', command: 'make watch' }], session_resume: false, max_turns: 0 };
function setup(kind: CatalogKind = 'role') {
  const key = catalogListKey(kind); const detailKind = kind === 'specialization' ? 'specialization' : 'template';
  let entries: Record<string, UnknownRecord> = { 'project:reviewer': structuredClone(original), 'user:reviewer': { ...original, description: 'User reviewer' } };
  let refusal = ''; let held = ''; let mismatch = false; const calls: TorqueCommand[] = []; const signals: AbortSignal[] = []; const waiting: (() => void)[] = [];
  const rows = () => Object.entries(entries).map(([id, data]) => ({ name: data.name, global: id.startsWith('user:'), path: `/catalog/${id}.yaml`, shadowed: id.startsWith('user:') }));
  vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command); signals.push(options?.signal as AbortSignal);
    let data: UnknownRecord = { type: 'ok' };
    if (command.cmd === refusal) data = { type: 'error', message: 'Injected catalog refusal' };
    else if (command.cmd === `list_${key}`) data = { type: key, group: 'g', [key]: rows() };
    else if (command.cmd === `get_${detailKind}`) data = { type: `${detailKind}_detail`, name: mismatch ? 'other' : command.name, [detailKind]: structuredClone(entries[`${text(command.scope)}:${text(command.name)}`]) };
    else if (command.cmd === `save_${kind}`) { if (command.old_name) delete entries[`${text(command.old_scope)}:${text(command.old_name)}`]; entries[`${text(command.scope)}:${text(command.name)}`] = record(command.data); data = { type: key, group: 'g', saved: mismatch ? 'other' : command.name, [key]: rows() }; }
    else if (command.cmd === `delete_${kind}`) { delete entries[`${text(command.scope)}:${text(command.name)}`]; data = { type: key, group: 'g', deleted: command.name, [key]: rows() }; }
    const result = { ok: true, json: () => Promise.resolve({ ok: true, data }) }; return command.cmd === held ? new Promise((resolve) => waiting.push(() => resolve(result))) : Promise.resolve(result);
  }));
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); const view = render(<Provider store={store}><CatalogEditor title="Catalog" kind={kind} group="g" /></Provider>);
  return { ...view, calls, signals, store, refuse: (cmd: string) => { refusal = cmd; }, hold: (cmd: string) => { held = cmd; }, mismatch: (value: boolean) => { mismatch = value; }, remote: (data: UnknownRecord) => { entries = { ...entries, 'project:reviewer': { ...entries['project:reviewer'], ...data } }; }, release: async () => { await act(async () => { waiting.shift()!(); await Promise.resolve(); }); }, reconnect: async () => { await act(async () => { store.dispatch(connectionActions.connected({ at: Date.now(), reconnect: true })); await Promise.resolve(); }); }, select: async (scope = 'project') => { fireEvent.click(await screen.findByRole('button', { name: new RegExp(`reviewer${scope}`) })); return screen.findByRole('textbox', { name: 'Preamble' }); } };
}
it('serializes full definitions with explicit zero, ordered priorities and child terminals; rejects invalid numbers, names and environment', () => {
  const draft = catalogDraft(original); expect(catalogDefinition(draft, 'role')).toMatchObject(original);
  expect(catalogDefinition({ ...draft, values: { ...draft.values, max_turns: '' } }, 'role')).not.toHaveProperty('max_turns');
  expect(() => catalogDefinition({ ...draft, values: { ...draft.values, max_turns: '1.2' } }, 'role')).toThrow('whole number');
  expect(() => catalogDefinition({ ...draft, environment: 'missing equals' }, 'role')).toThrow('KEY=VALUE');
  expect(() => catalogDefinition({ ...draft, name: '../outside' }, 'role')).toThrow('Use a name');
});
it.each(['role', 'template', 'specialization'] as const)('loads scoped %s details instead of listing metadata and acknowledges a complete save', async (kind) => {
  const test = setup(kind); await test.select('user'); expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('User reviewer');
  fireEvent.change(screen.getByRole('textbox', { name: 'Preamble' }), { target: { value: 'New preamble' } }); fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('status');
  const save = test.calls.find((call) => call.cmd === `save_${kind}`)!; expect(save).toMatchObject({ group: 'g', name: 'reviewer', scope: 'user', old_name: 'reviewer', old_scope: 'user', data: { preamble: 'New preamble', system_prompt: 'Preserved full prompt' } }); expect(save.data).not.toHaveProperty('path'); expect(save.data).not.toHaveProperty('global');
});
it('reconciles reconnects without losing edited fields, selection, focus or caret and retries failed reads', async () => {
  const test = setup(); const preamble = await test.select() as HTMLTextAreaElement; fireEvent.change(preamble, { target: { value: 'Local draft' } }); preamble.focus(); preamble.setSelectionRange(1, 4);
  test.remote({ description: 'Remote description', preamble: 'Remote preamble', model: 'new-model' }); await test.reconnect(); await waitFor(() => expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('Remote description')); expect(preamble).toHaveValue('Local draft'); expect(preamble).toHaveFocus(); expect([preamble.selectionStart, preamble.selectionEnd]).toEqual([1, 4]);
  test.refuse('get_template'); await test.reconnect(); await screen.findByText(/Definition refresh failed/); expect(preamble).toHaveValue('Local draft'); test.refuse(''); fireEvent.click(screen.getByRole('button', { name: 'Retry definition' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('status'); expect(test.calls.find((call) => call.cmd === 'save_role')?.data).toMatchObject({ preamble: 'Local draft', model: 'new-model', description: 'Remote description' });
});
it('stages duplicate and retains original identity through failed rename or scope changes', async () => {
  const test = setup(); await test.select(); fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'renamed' } }); fireEvent.change(screen.getByRole('combobox', { name: 'Scope' }), { target: { value: 'user' } });
  test.refuse('save_role'); fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('alert'); expect(screen.getByRole('heading', { name: 'Edit reviewer' })).toBeVisible(); expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('renamed');
  test.refuse(''); test.hold('save_role'); fireEvent.click(screen.getByRole('button', { name: 'Save' })); expect(screen.getByRole('button', { name: 'New role' })).toBeDisabled(); expect(screen.getByRole('heading', { name: 'Edit reviewer' })).toBeVisible(); await test.release(); await screen.findByRole('heading', { name: 'Edit renamed' });
  expect(test.calls.filter((call) => call.cmd === 'save_role').at(-1)).toMatchObject({ name: 'renamed', scope: 'user', old_name: 'reviewer', old_scope: 'project' });
  const count = test.calls.length; fireEvent.click(screen.getByRole('button', { name: 'Duplicate' })); expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('renamed-copy'); expect(test.calls).toHaveLength(count);
});
it('keeps delete confirmation and definition on refusal and deletes only the selected scope', async () => {
  const test = setup(); await test.select('user'); fireEvent.click(screen.getByRole('button', { name: 'Delete' })); test.refuse('delete_role'); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' })); await within(screen.getByRole('dialog')).findByRole('alert');
  test.refuse(''); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' })); await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument()); expect(test.calls.find((call) => call.cmd === 'delete_role')).toMatchObject({ scope: 'user', name: 'reviewer' }); expect(screen.getByRole('button', { name: /reviewerproject/ })).toBeVisible();
});
it('rejects mismatched detail, aborts pre-save reads and performs no hidden reads', async () => {
  const test = setup(); test.mismatch(true); fireEvent.click(await screen.findByRole('button', { name: /reviewerproject/ })); await screen.findByRole('alert'); expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument(); test.mismatch(false); fireEvent.click(screen.getByRole('button', { name: 'Retry definition' })); const preamble = await screen.findByRole('textbox', { name: 'Preamble' });
  test.hold('get_template'); await test.reconnect(); const signal = test.signals.at(-1)!; fireEvent.change(preamble, { target: { value: 'Keep through old read' } }); fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('status'); expect(signal.aborted).toBe(true); await test.release(); expect(preamble).toHaveValue('Keep through old read'); test.unmount(); const count = test.calls.length; await test.reconnect(); expect(test.calls).toHaveLength(count);
});

it('retains a new draft after a mismatched save and retries failed lists without repeating an acknowledged write', async () => {
  const test = setup(); await test.select(); fireEvent.click(screen.getByRole('button', { name: 'Duplicate' })); test.mismatch(true); fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('alert'); expect(screen.getByRole('heading', { name: 'New role' })).toBeVisible(); expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('reviewer-copy');
  test.mismatch(false); test.refuse('list_roles'); fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('heading', { name: 'Edit reviewer-copy' }); await screen.findByText(/Catalog refresh failed/); const writes = test.calls.filter((call) => call.cmd === 'save_role').length;
  test.refuse(''); fireEvent.click(screen.getByRole('button', { name: 'Retry catalog' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument()); expect(test.calls.filter((call) => call.cmd === 'save_role')).toHaveLength(writes);
});

it('authors every Classic role field through typed controls and preserves explicit inheritance and zero', async () => {
  const test = setup(); await test.select();
  for (const section of ['Appearance', 'Worktree', 'Environment and terminals']) fireEvent.click(screen.getByText(section, { exact: true }));
  const fields: Record<string, [string, string]> = {
    display_name: ['Display name', 'Review role'], description: ['Description', 'Complete definition'], provider: ['Agent CLI', 'generic'], command: ['Command override', 'python receiver.py'], model: ['Model', 'custom-model'], permissions: ['Permissions', 'allowlist'], reasoning_effort: ['Reasoning effort', 'high'], fast_mode: ['Fast mode', 'on'], runner_backend: ['Runner backend', 'local'], preamble: ['Preamble', 'Role guidance'], system_prompt: ['System prompt', 'System instructions'], initial_prompt: ['Initial prompt', 'Start here'], tab_color: ['Tab color', '#abcdef'], icon: ['Icon', 'R'], worktree_base_branch: ['Base branch', 'main'], env_file: ['Environment file', '.env.test'],
  };
  for (const [label, value] of Object.values(fields)) fireEvent.change(screen.getByRole(label === 'Agent CLI' ? 'combobox' : 'textbox', { name: label }), { target: { value } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Max turns' }), { target: { value: '0' } }); fireEvent.change(screen.getByRole('spinbutton', { name: 'Idle timeout (minutes)' }), { target: { value: '' } });
  for (const [label, value] of [['Resume session on relaunch', ''], ['Enable git worktree', 'true'], ['Auto-checkpoint on stop', 'true'], ['Checkpoint on progress', 'false'], ['Squash on merge', 'false']] as const) fireEvent.change(screen.getByRole('combobox', { name: label }), { target: { value } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Priorities (one per line)' }), { target: { value: 'First\nSecond' } }); fireEvent.change(screen.getByRole('textbox', { name: 'Environment (KEY=VALUE per line)' }), { target: { value: 'EXPLICIT=0\nWITH_EQUALS=a=b' } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove terminal 1' })); fireEvent.click(screen.getByRole('button', { name: 'Add terminal' })); fireEvent.change(screen.getByRole('textbox', { name: 'Terminal 1 name' }), { target: { value: 'logs' } }); fireEvent.change(screen.getByRole('textbox', { name: 'Terminal 1 command' }), { target: { value: 'tail -f app.log' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('status'); const saved = test.calls.find((call) => call.cmd === 'save_role')?.data;
  expect(saved).toMatchObject({ ...Object.fromEntries(Object.entries(fields).map(([key, [, value]]) => [key, value])), max_turns: 0, priorities: ['First', 'Second'], env_vars: { EXPLICIT: '0', WITH_EQUALS: 'a=b' }, terminals: [{ name: 'logs', command: 'tail -f app.log' }], worktree: true, worktree_auto_checkpoint: true, checkpoint_on_progress: false, worktree_merge_squash: false });
  expect(saved).not.toHaveProperty('idle_timeout'); expect(saved).not.toHaveProperty('session_resume'); expect(record(record(saved).env_vars)).not.toHaveProperty('KEEP');
});
