import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { ActionsWorkspace } from './ActionsWorkspace';
import { actionDefinition, actionDraft } from './actionModel';
import { record, text } from './agentClassesModel';
afterEach(() => vi.unstubAllGlobals());
const original: UnknownRecord = { name: 'build', description: 'Project build', prompt: '{{ TASK }}', agent: { name_prefix: 'runner', env_vars: { KEEP: 'yes' } }, deliverable: { required: true, kind: 'report' }, max_depth: 3, worktree: false, transitions: [{ action: 'review', when: 'Ready', loc_gate: { ship_direct_max: 0, review_default_above: 100, self_review_bypass_allowed: false } }], terminals: [{ name: 'logs', command: 'echo logs' }] };
function setup(initialName = '', initiallyHeld = '') {
  let entries: Record<string, UnknownRecord> = { 'project:build': structuredClone(original), 'user:build': { ...original, description: 'User build' } };
  let refusal = ''; let held = initiallyHeld; let mismatch = false; const calls: TorqueCommand[] = []; const signals: AbortSignal[] = []; const waiting: (() => void)[] = [];
  const rows = () => Object.entries(entries).map(([id, data]) => ({ name: data.name, global: id.startsWith('user:'), dir: `/catalog/${id}`, shadowed: id.startsWith('user:') }));
  vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command); signals.push(options?.signal as AbortSignal);
    let data: UnknownRecord = { type: 'ok' };
    if (command.cmd === refusal) data = { type: 'error', message: 'Injected action refusal' };
    else if (command.cmd === 'list_actions') data = { type: 'actions', group: 'g', actions: rows() };
    else if (command.cmd === 'list_roles') data = { type: 'roles', group: 'g', roles: [{ name: 'reviewer' }] };
    else if (command.cmd === 'get_action') data = { type: 'action_detail', group: 'g', scope: mismatch ? 'other' : command.scope, name: command.name, action: structuredClone(entries[`${text(command.scope)}:${text(command.name)}`]) };
    else if (command.cmd === 'render_action') data = { type: command.variables_only ? 'action_variables' : 'action_rendered', workspace_group: 'g', name: command.name, scope: mismatch ? 'other' : command.scope, vars: [{ name: 'TASK' }, { name: 'SCOPE', default: 'all' }], prompt: `${text(record(command.action).prompt)} ${text(record(command.vars).TASK)}` };
    else if (command.cmd === 'save_action') { if (!mismatch) { if (command.old_name) delete entries[`${text(command.old_scope)}:${text(command.old_name)}`]; entries[`${text(command.scope)}:${text(command.name)}`] = record(command.action); } data = { type: 'actions', group: 'g', scope: mismatch ? 'other' : command.scope, saved: command.name, actions: rows() }; }
    else if (command.cmd === 'delete_action') { delete entries[`${text(command.scope)}:${text(command.name)}`]; data = { type: 'actions', group: 'g', scope: command.scope, deleted: command.name, actions: rows() }; }
    const result = { ok: true, json: () => Promise.resolve({ ok: true, data }) }; return command.cmd === held ? new Promise((resolve) => waiting.push(() => resolve(result))) : Promise.resolve(result);
  }));
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); const view = render(<Provider store={store}><ActionsWorkspace group="g" initialName={initialName} onPipelines={() => {}} /></Provider>);
  return { ...view, calls, signals, store, refuse: (cmd: string) => { refusal = cmd; }, hold: (cmd: string) => { held = cmd; }, mismatch: (value: boolean) => { mismatch = value; }, remote: (data: UnknownRecord) => { entries = { ...entries, 'project:build': { ...entries['project:build'], ...data } }; }, release: async () => { await act(async () => { waiting.shift()!(); await Promise.resolve(); }); }, reconnect: async () => { await act(async () => { store.dispatch(connectionActions.connected({ at: Date.now(), reconnect: true })); await Promise.resolve(); }); }, select: async (scope = 'project') => { fireEvent.click(await screen.findByRole('button', { name: new RegExp(`build${scope}`) })); return screen.findByRole('textbox', { name: 'Prompt' }); } };
}
it('preserves full definitions, legacy prompts, explicit false/zero and validates numeric drafts', () => {
  const draft = actionDraft(original, 'build', 'user'); const definition = actionDefinition({ ...draft, values: { ...draft.values, review_required_above_loc: '0', max_depth: '' } });
  expect(definition).toMatchObject({ deliverable: original.deliverable, agent: original.agent, worktree: false, review_required_above_loc: 0 }); expect(definition).not.toHaveProperty('max_depth');
  expect(actionDraft({ task: '{{ TASK }}', instructions: 'Keep context' }, 'old').values.prompt).toBe('{{ TASK }}\n\nKeep context');
  for (const value of ['-1', '1.5', '9007199254740992']) expect(() => actionDefinition({ ...draft, values: { ...draft.values, max_depth: value } })).toThrow('nonnegative whole');
  expect(() => actionDefinition({ ...draft, name: '../outside' })).toThrow('action name');
  expect(actionDefinition({ ...draft, values: { prompt: '{{ torque.task.title | default("example") }}' } }).prompt).toContain('torque.task.title');
});
it('loads the intended scope and retains unexposed fields on unchanged save', async () => {
  const test = setup(); await test.select('user'); fireEvent.click(screen.getByRole('button', { name: 'Save action' })); await screen.findByText('Saved user build');
  expect(test.calls.find((call) => call.cmd === 'save_action')).toMatchObject({ name: 'build', scope: 'user', old_name: 'build', old_scope: 'user', action: { deliverable: original.deliverable, agent: original.agent, worktree: false } });
});
it('reconciles untouched reconnect fields while keeping prompt DOM, focus, caret and failed-read drafts', async () => {
  const test = setup(); const prompt = await test.select() as HTMLTextAreaElement; fireEvent.change(prompt, { target: { value: 'Draft {{ TASK }}' } }); prompt.focus(); prompt.setSelectionRange(2, 5);
  test.remote({ description: 'Remote description', prompt: 'Remote {{ TASK }}' }); await test.reconnect(); await waitFor(() => expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('Remote description')); expect(prompt).toHaveValue('Draft {{ TASK }}'); expect(prompt).toHaveFocus(); expect([prompt.selectionStart, prompt.selectionEnd]).toEqual([2, 5]);
  test.refuse('get_action'); await test.reconnect(); await screen.findByText(/Definition refresh failed/); expect(prompt).toHaveValue('Draft {{ TASK }}'); test.refuse(''); fireEvent.click(screen.getByRole('button', { name: 'Retry definition' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
});
it('keeps failed and delayed rename acknowledgements on the original identity and stages duplicate', async () => {
  const test = setup(); await test.select(); fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'renamed' } }); fireEvent.change(screen.getByRole('combobox', { name: 'Scope' }), { target: { value: 'user' } });
  test.refuse('save_action'); fireEvent.click(screen.getByRole('button', { name: 'Save action' })); await screen.findByRole('alert'); expect(screen.getByRole('heading', { name: 'Edit build' })).toBeVisible();
  test.refuse(''); test.hold('save_action'); fireEvent.click(screen.getByRole('button', { name: 'Save action' })); expect(screen.getByRole('heading', { name: 'Edit build' })).toBeVisible(); expect(screen.getByRole('button', { name: 'New action' })).toBeDisabled(); await test.release(); await screen.findByRole('heading', { name: 'Edit renamed' });
  expect(test.calls.filter((call) => call.cmd === 'save_action').at(-1)).toMatchObject({ old_name: 'build', old_scope: 'project', scope: 'user', name: 'renamed' });
  const count = test.calls.filter((call) => call.cmd === 'save_action').length; fireEvent.click(screen.getByRole('button', { name: 'Duplicate' })); expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('renamed-copy'); expect(test.calls.filter((call) => call.cmd === 'save_action')).toHaveLength(count);
});
it('retains delete confirmation on failure and only deletes the selected scope', async () => {
  const test = setup(); await test.select('user'); fireEvent.click(screen.getByRole('button', { name: 'Delete' })); test.refuse('delete_action'); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' })); await within(screen.getByRole('dialog')).findByRole('alert');
  test.refuse(''); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' })); await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument()); expect(screen.getByRole('button', { name: 'buildproject' })).toBeVisible(); expect(test.calls.find((call) => call.cmd === 'delete_action')).toMatchObject({ scope: 'user' });
});
it('rejects mismatched detail/save and cancels pre-save and hidden reads', async () => {
  const test = setup(); test.mismatch(true); fireEvent.click(await screen.findByRole('button', { name: 'buildproject' })); await screen.findByRole('alert'); expect(screen.queryByRole('button', { name: 'Save action' })).not.toBeInTheDocument(); test.mismatch(false); fireEvent.click(screen.getByRole('button', { name: 'Retry definition' })); const prompt = await screen.findByRole('textbox', { name: 'Prompt' });
  test.hold('get_action'); await test.reconnect(); const index = test.calls.map((call) => call.cmd).lastIndexOf('get_action'); fireEvent.change(prompt, { target: { value: 'Keep {{ TASK }}' } }); test.mismatch(true); fireEvent.click(screen.getByRole('button', { name: 'Save action' })); await screen.findByRole('alert'); expect(test.signals[index]?.aborted).toBe(true); await test.release(); expect(prompt).toHaveValue('Keep {{ TASK }}');
  test.unmount(); const count = test.calls.length; await test.reconnect(); expect(test.calls).toHaveLength(count);
});
it('previews current drafts with editable discovered variables and hides stale or mismatched responses', async () => {
  const test = setup(); const prompt = await test.select(); const task = await screen.findByRole('textbox', { name: 'Preview TASK' }); fireEvent.change(task, { target: { value: 'Preview input' } }); fireEvent.change(prompt, { target: { value: 'Unsaved {{ TASK }} {{ SCOPE }}' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' })); await screen.findByLabelText('Rendered action prompt'); expect(test.calls.filter((call) => call.cmd === 'render_action' && !call.variables_only).at(-1)).toMatchObject({ action: { prompt: 'Unsaved {{ TASK }} {{ SCOPE }}' }, vars: { TASK: 'Preview input' } });
  fireEvent.change(prompt, { target: { value: 'Changed {{ TASK }}' } }); expect(screen.queryByLabelText('Rendered action prompt')).not.toBeInTheDocument(); expect(screen.getByText(/Draft changed/)).toBeVisible();
  test.mismatch(true); fireEvent.click(screen.getByRole('button', { name: 'Preview' })); await screen.findByText('Preview response did not match this draft.'); expect(test.calls.some((call) => call.cmd === 'save_action')).toBe(false);
});
it('authors typed inline agents, transitions, human asks, LOC gates and companion terminals', async () => {
  const test = setup(); await test.select(); fireEvent.change(screen.getByRole('textbox', { name: 'Boot command' }), { target: { value: 'worker-cli' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Transition 1 target' }), { target: { value: 'parent' } }); fireEvent.change(screen.getByRole('textbox', { name: 'Transition 1 status' }), { target: { value: 'Reviewing' } }); fireEvent.change(screen.getByRole('spinbutton', { name: 'Transition 1 Ship direct up to LOC' }), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add transition' })); fireEvent.change(screen.getByRole('combobox', { name: 'Transition 2 type' }), { target: { value: 'ask' } }); fireEvent.change(screen.getByRole('textbox', { name: 'Transition 2 when' }), { target: { value: 'Need approval' } });
  fireEvent.click(screen.getByText('Companion terminals', { exact: true })); fireEvent.change(screen.getByRole('textbox', { name: 'Terminal 1 command' }), { target: { value: 'echo changed' } }); fireEvent.click(screen.getByRole('button', { name: 'Add terminal' })); fireEvent.change(screen.getByRole('textbox', { name: 'Terminal 2 name' }), { target: { value: 'watch' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save action' })); await screen.findByText('Saved project build'); expect(test.calls.find((call) => call.cmd === 'save_action')?.action).toMatchObject({ agent: { command: 'worker-cli', env_vars: { KEEP: 'yes' } }, transitions: [{ action: 'review', target: 'parent', status: 'Reviewing', loc_gate: { ship_direct_max: 0 } }, { ask: true, when: 'Need approval' }], terminals: [{ name: 'logs', command: 'echo changed' }, { name: 'watch', command: '' }] });
});
it('blocks incomplete transitions, resolves pipeline selection and retries post-save lists without rewriting', async () => {
  const test = setup('build'); await screen.findByRole('textbox', { name: 'Prompt' }); fireEvent.click(screen.getByRole('button', { name: 'Add transition' })); fireEvent.click(screen.getByRole('button', { name: 'Save action' })); await screen.findByText('Choose an action for transition 2.'); expect(test.calls.some((call) => call.cmd === 'save_action')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Remove transition 2' })); test.refuse('list_actions'); fireEvent.click(screen.getByRole('button', { name: 'Save action' })); await screen.findByText(/Catalog refresh failed/); const count = test.calls.filter((call) => call.cmd === 'save_action').length;
  test.refuse(''); fireEvent.click(screen.getByRole('button', { name: 'Retry catalog' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument()); expect(test.calls.filter((call) => call.cmd === 'save_action')).toHaveLength(count);
});

it('ignores a late preview after the draft changes and cancels hidden previews', async () => {
  const test = setup(); const prompt = await test.select(); await screen.findByRole('textbox', { name: 'Preview TASK' }); test.hold('render_action');
  fireEvent.click(screen.getByRole('button', { name: 'Preview' })); const index = test.calls.length - 1;
  fireEvent.change(prompt, { target: { value: 'Changed before preview {{ TASK }}' } }); expect(test.signals[index]?.aborted).toBe(true); await test.release(); expect(screen.queryByLabelText('Rendered action prompt')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Preview' })); const last = test.calls.length - 1; test.unmount(); expect(test.signals[last]?.aborted).toBe(true); await test.release();
});

it('keeps a new draft when an earlier pipeline-selection list finally arrives', async () => {
  const test = setup('build', 'list_actions'); fireEvent.click(screen.getByRole('button', { name: 'New action' })); fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'draft-before-list' } });
  await test.release(); expect(screen.getByRole('heading', { name: 'New action' })).toBeVisible(); expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('draft-before-list'); expect(test.calls.some((call) => call.cmd === 'get_action')).toBe(false);
});
