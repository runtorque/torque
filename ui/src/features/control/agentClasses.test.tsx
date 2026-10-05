import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { AgentClassLibrary } from './AgentClassLibrary';
import { classDraft, classDefinition, duplicateClass, record } from './agentClassesModel';

afterEach(() => vi.unstubAllGlobals());
const capabilities = [{ id: 'self.read', label: 'Read own context', risk: 'normal', scopes: ['self'], base_kinds: ['worker', 'engineer'] }, { id: 'task.read', label: 'Read tasks', scopes: ['self', 'group'], base_kinds: ['engineer'] }];
const definition = { id: 'project-class', display_name: 'Project Class', version: '1', base_kind: 'engineer', lifecycle: 'stable', acl: { mode: 'deny', rules: [] }, prompt: { job: 'Original job', identity: 'Keep identity', boot_checklist: ['Keep checklist'] }, metadata: { marker: 'preserved', ui: { badge: 'keep', extra: 'preserved' } }, draft: { scratch_only: false }, warnings: ['Authored warning'] };
function preview(value: UnknownRecord): UnknownRecord { return { ...value, authoring_definition: structuredClone(value), acl: { mode: 'deny', rules: [{ capability: 'self.read', scope: 'self' }], capabilities: { 'self.read': 'self' } }, source: 'project', launchable: true, class_warnings: value.warnings, warnings: ['Authored warning'], effective_authority: { capabilities: { 'self.read': 'self', 'task.read': 'group' } }, apply_state: { mutates_running_sessions: false, applies_at: 'next_launch_or_relaunch' } }; }
function setup(initial = preview(definition)) {
  let items: UnknownRecord[] = [initial]; let failure = ''; let mismatch = false; let held = '';
  const calls: TorqueCommand[] = []; const signals: AbortSignal[] = []; const waiting: (() => void)[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command); signals.push(options?.signal as AbortSignal);
    let frame: UnknownRecord = { type: 'ok' };
    if (command.cmd === failure) frame = { type: 'error', message: 'Injected class refusal' };
    else if (command.cmd === 'agent_class_list') frame = { type: 'agent_classes', classes: structuredClone(items), capability_catalog: capabilities };
    else if (command.cmd === 'agent_class_validate') frame = { type: 'agent_class_validation', valid: true, agent_class: preview(record(command.agent_class)) };
    else if (command.cmd === 'agent_class_delete') { items = items.filter((item) => item.id !== command.class_id); frame = { type: 'agent_class_delete', ok: true, class_id: command.class_id, classes: items, operation: 'deleted' }; }
    else {
      const next = command.cmd === 'agent_class_archive' ? { ...items.find((item) => item.id === command.class_id), archived: true, launchable: false } : preview(record(command.agent_class));
      if (mismatch) next.id = 'wrong-class'; else items = [...items.filter((item) => item.id !== next.id), next];
      frame = { type: command.cmd === 'agent_class_archive' ? 'agent_class_archive' : 'agent_class_save', ok: true, agent_class: next, classes: items, operation: 'saved' };
    }
    const response = { ok: true, json: () => Promise.resolve({ ok: true, data: frame }) };
    return held === command.cmd ? new Promise((resolve) => waiting.push(() => resolve(response))) : Promise.resolve(response);
  }));
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const view = render(<Provider store={store}><AgentClassLibrary baseDir="/project" /></Provider>);
  const reconnect = async () => { await act(async () => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); await Promise.resolve(); }); };
  return { ...view, store, calls, signals, reconnect, fail: (value: string) => { failure = value; }, mismatch: (value: boolean) => { mismatch = value; }, hold: (value: string) => { held = value; }, release: async () => { await act(async () => { waiting.shift()!(); await Promise.resolve(); }); }, remote: (patch: UnknownRecord) => { items = [preview({ ...record(items[0]?.authoring_definition), ...patch })]; } };
}

it('keeps authored deny rules and fields that the form does not edit', () => {
  const item = preview(definition); const draft = classDraft(item); expect(draft.selected).toEqual({});
  const edited = classDefinition(item, { ...draft, description: 'Only description changed' });
  expect(edited).not.toHaveProperty('draft');
  expect(classDefinition(item, { ...draft, lifecycle: 'draft', scratchOnly: true })).toMatchObject({ draft: { scratch_only: true } });
  expect(edited).toMatchObject({ acl: { mode: 'deny', rules: [] }, prompt: definition.prompt, metadata: definition.metadata, warnings: definition.warnings });
  const copy = duplicateClass({ ...item, ...edited, metadata: { ...definition.metadata, archived: true, archived_at: 'old' }, archived: true });
  expect(copy).toMatchObject({ id: 'project-class-copy', archived: false, builtin: false }); expect(record(copy.metadata)).not.toHaveProperty('archived'); expect(copy).not.toHaveProperty('authoring_definition');
});

it('renders effective authority and reconciles untouched fields without replacing edits, focus or caret on reconnect', async () => {
  const test = setup(); const job = await screen.findByRole<HTMLTextAreaElement>('textbox', { name: 'Class job prompt' });
  expect(within(screen.getByRole('region', { name: 'Agent Class authority preview' })).getByText('Read tasks')).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /Read own context/ })).not.toBeChecked();
  fireEvent.change(job, { target: { value: 'Local job draft' } }); job.focus(); job.setSelectionRange(1, 5);
  test.remote({ display_name: 'Remote class name', prompt: { ...definition.prompt, job: 'Remote job', operating_guidelines: ['Remote guideline'] } }); await test.reconnect();
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Display name' })).toHaveValue('Remote class name')); expect(job).toHaveValue('Local job draft'); expect(job).toHaveFocus(); expect([job.selectionStart, job.selectionEnd]).toEqual([1, 5]);
  fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('status');
  expect(test.calls.find((call) => call.cmd === 'agent_class_update')?.agent_class).toMatchObject({ prompt: { ...definition.prompt, job: 'Local job draft', operating_guidelines: ['Remote guideline'] }, acl: { mode: 'deny', rules: [] } });
});

it('validates the current draft only and ignores a late result after editing or selection changes', async () => {
  const test = setup(); await screen.findByRole('textbox', { name: 'Class job prompt' }); test.hold('agent_class_validate'); fireEvent.click(screen.getByRole('button', { name: 'Validate' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: 'Newer edit' } }); await test.release(); expect(screen.queryByText('Validation passed')).not.toBeInTheDocument();
  test.hold(''); fireEvent.click(screen.getByRole('button', { name: 'Validate' })); await screen.findByText('Validation passed'); expect(screen.getByRole('heading', { name: 'Validated draft authority' })).toBeVisible();
  act(() => { test.store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'agent_class_validation', valid: false, message: 'Unrelated validation' })); }); expect(screen.queryByText('Unrelated validation')).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: 'Invalidate preview' } }); expect(screen.queryByText('Validation passed')).not.toBeInTheDocument();
  test.hold('agent_class_validate'); fireEvent.click(screen.getByRole('button', { name: 'Validate' })); fireEvent.click(screen.getByRole('button', { name: '＋ New' })); expect(test.signals.at(-1)?.aborted).toBe(true); await test.release(); expect(screen.queryByText('Validation passed')).not.toBeInTheDocument();
});

it('stages a duplicate without writing and retains its draft through failed or mismatched creation', async () => {
  const test = setup(); fireEvent.click(await screen.findByRole('button', { name: 'Duplicate' })); expect(test.calls.filter((call) => call.cmd !== 'agent_class_list')).toHaveLength(0);
  const id = screen.getByRole('textbox', { name: 'ID' }); expect(id).toHaveValue('project-class-copy'); fireEvent.change(id, { target: { value: ' project-class-copy ' } });
  test.fail('agent_class_create'); fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('alert'); expect(id).toHaveValue(' project-class-copy '); expect(screen.getByRole('heading', { name: 'Create project Agent Class' })).toBeVisible();
  test.fail(''); test.mismatch(true); fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('alert'); expect(id).toHaveValue(' project-class-copy ');
  test.mismatch(false); test.hold('agent_class_create'); fireEvent.click(screen.getByRole('button', { name: 'Save' })); expect(screen.getByRole('button', { name: '＋ New' })).toBeDisabled(); expect(screen.getByRole('heading', { name: 'Create project Agent Class' })).toBeVisible();
  await test.release(); await screen.findByRole('heading', { name: 'Edit project Agent Class' }); expect(screen.getByRole('textbox', { name: 'ID' })).toHaveValue('project-class-copy');
});

it('retains confirmation and selection on failed archive/delete and updates them only on acknowledgement', async () => {
  const test = setup(); fireEvent.click(await screen.findByRole('button', { name: 'Archive' })); test.fail('agent_class_archive'); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Archive' })); await within(screen.getByRole('dialog')).findByRole('alert');
  expect(screen.getByRole('dialog')).toBeVisible(); test.fail(''); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Archive' })); await screen.findByRole('heading', { name: 'Archived Agent Class' }); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Delete' })); test.fail('agent_class_delete'); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' })); await within(screen.getByRole('dialog')).findByRole('alert');
  test.fail(''); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' })); await screen.findByText('No Agent Classes'); expect(screen.queryByRole('textbox', { name: 'ID' })).not.toBeInTheDocument();
});

it('retains drafts after failed catalog reads, retries, aborts pre-save reads and issues no hidden reads', async () => {
  const test = setup(); const job = await screen.findByRole('textbox', { name: 'Class job prompt' }); fireEvent.change(job, { target: { value: 'Retain me' } });
  test.fail('agent_class_list'); await test.reconnect(); await screen.findByRole('alert'); expect(job).toHaveValue('Retain me'); test.fail(''); fireEvent.click(screen.getByRole('button', { name: 'Retry classes' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  test.hold('agent_class_list'); await test.reconnect(); const pendingRead = test.signals.at(-1)!; fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('status'); expect(pendingRead.aborted).toBe(true); await test.release(); expect(job).toHaveValue('Retain me');
  test.unmount(); const count = test.calls.length; expect(test.signals.at(-1)?.aborted).toBe(true); await test.reconnect(); expect(test.calls).toHaveLength(count);
});


it('refuses to edit a deny preview without authored rules until a complete refresh arrives', async () => {
  const incomplete = preview(definition); delete incomplete.authoring_definition; const test = setup(incomplete);
  await screen.findByText(/The editable deny rules are unavailable/);
  expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Duplicate' })).toBeDisabled(); expect(screen.getByRole('button', { name: 'Validate' })).toBeDisabled();
  test.remote(definition); await test.reconnect(); await screen.findByRole('button', { name: 'Save' }); expect(screen.queryByText(/The editable deny rules are unavailable/)).not.toBeInTheDocument(); expect(screen.getByRole('checkbox', { name: /Read own context/ })).not.toBeChecked();
});

it('keeps lifecycle and scratch-only controls consistent with stable/draft authoring', async () => {
  const test = setup(); const lifecycle = await screen.findByRole('combobox', { name: 'Lifecycle' }); const scratch = screen.getByRole('checkbox', { name: 'Scratch-only draft class' });
  fireEvent.change(lifecycle, { target: { value: 'draft' } }); expect(scratch).toBeChecked(); fireEvent.click(screen.getByRole('button', { name: 'Save' })); await screen.findByRole('status'); expect(test.calls.filter((call) => call.cmd === 'agent_class_update').at(-1)?.agent_class).toMatchObject({ lifecycle: 'draft', draft: { scratch_only: true } });
  fireEvent.click(scratch); expect(lifecycle).toHaveValue('stable'); fireEvent.click(screen.getByRole('button', { name: 'Save' })); await waitFor(() => expect(test.calls.filter((call) => call.cmd === 'agent_class_update')).toHaveLength(2)); expect(test.calls.filter((call) => call.cmd === 'agent_class_update').at(-1)?.agent_class).not.toHaveProperty('draft');
});
