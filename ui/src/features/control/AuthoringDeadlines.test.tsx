import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { CatalogEditor } from './CatalogLibrary';
import { ActionsWorkspace } from './ActionsWorkspace';
import { AgentClassLibrary } from './AgentClassLibrary';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
beforeEach(() => { vi.mocked(readCommand).mockReset(); });
afterEach(() => { vi.useRealTimers(); });
const types = ['role', 'template', 'specialization', 'action', 'class'] as const;
type Kind = typeof types[number];
function setup(kind: Kind, initiallyHeld = '') {
  const key = kind === 'class' ? 'agent_classes' : kind === 'specialization' ? 'specializations' : `${kind}s`;
  const listCommand = kind === 'class' ? 'agent_class_list' : `list_${key}`; const saveCommand = kind === 'class' ? 'agent_class_update' : `save_${kind}`; const detailKind = kind === 'specialization' ? 'specialization' : kind === 'action' ? 'action' : 'template';
  let held = initiallyHeld; let exists = true; let data: UnknownRecord = kind === 'class' ? { id: 'example', display_name: 'Example class', base_kind: 'engineer', version: '1', lifecycle: 'stable', acl: { mode: 'allow', rules: [] }, prompt: { job: 'Original job' } } : { name: 'example', description: 'Original description', preamble: 'Original preamble', prompt: 'Original {{ TASK }}' };
  const calls: { command: TorqueCommand; signal: AbortSignal }[] = []; const waiting: { resolve: (frame: AuxiliaryFrame) => void; frame: AuxiliaryFrame }[] = [];
  const rows = () => exists ? [{ name: 'example', scope: 'project' }] : [];
  const classes = () => exists ? [{ ...data, source: 'project', authoring_definition: data, effective_capabilities: [], launchable: true }] : [];
  vi.mocked(readCommand).mockImplementation((command, signal) => {
    calls.push({ command, signal }); let frame: AuxiliaryFrame = { type: 'ok' };
    if (command.cmd === listCommand) frame = kind === 'class' ? { type: key, classes: classes(), capability_catalog: [] } : { type: key, group: 'g', [key]: rows() };
    else if (command.cmd === `get_${detailKind}`) frame = exists ? { type: `${detailKind}_detail`, group: 'g', scope: command.scope, name: command.name, [detailKind]: data } : { type: 'error', message: 'Definition no longer exists' };
    else if (command.cmd === 'agent_class_validate') frame = { type: 'agent_class_validation', valid: true, agent_class: command.agent_class };
    else if (command.cmd === saveCommand || command.cmd === 'agent_class_create') { data = (command.agent_class ?? command.action ?? command.data) as UnknownRecord; frame = kind === 'class' ? { type: 'agent_class_save', ok: true, agent_class: classes()[0], classes: classes() } : { type: key, group: 'g', scope: command.scope, saved: command.name, [key]: rows() }; }
    else if (command.cmd === `delete_${kind}` || command.cmd === 'agent_class_delete') { exists = false; frame = kind === 'class' ? { type: 'agent_class_delete', ok: true, class_id: 'example', classes: [] } : { type: key, group: 'g', scope: command.scope, deleted: command.name, [key]: [] }; }
    else if (command.cmd === 'agent_class_archive') { data = { ...data, archived: true }; frame = { type: 'agent_class_archive', ok: true, agent_class: classes()[0], classes: classes() }; }
    else if (command.cmd === 'list_roles') frame = { type: 'roles', group: 'g', roles: [] };
    else if (command.cmd === 'render_action') frame = { type: command.variables_only ? 'action_variables' : 'action_rendered', workspace_group: 'g', scope: command.scope, name: command.name, vars: [{ name: 'TASK' }], prompt: 'Current rendered prompt' };
    return command.cmd === held ? new Promise((resolve) => waiting.push({ resolve, frame })) : Promise.resolve(frame);
  });
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); const dispatch = vi.spyOn(store, 'dispatch'); const mutation = vi.fn();
  const view = render(<Provider store={store}>{kind === 'class' ? <AgentClassLibrary baseDir="/project" /> : kind === 'action' ? <ActionsWorkspace group="g" onPipelines={vi.fn()} /> : <CatalogEditor kind={kind} title="Catalog" group="g" onMutation={mutation} />}</Provider>);
  const flush = async () => { await act(async () => { await Promise.resolve(); }); };
  const select = async () => { await flush(); if (kind !== 'class') fireEvent.click(screen.getByRole('button', { name: /exampleproject/ })); await flush(); };
  const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
  const release = async (index = 0, replacement?: AuxiliaryFrame) => { await act(async () => { waiting[index]!.resolve(replacement ?? waiting[index]!.frame); await Promise.resolve(); }); };
  const reconnect = async () => { await act(async () => { store.dispatch(connectionActions.connected({ at: Date.now(), reconnect: true })); await Promise.resolve(); }); };
  return { ...view, calls, waiting, store, dispatch, mutation, key, listCommand, saveCommand, detailKind, hold: (value: string) => { held = value; }, flush, select, advance, release, reconnect, newButton: () => screen.getByRole('button', { name: kind === 'class' ? '＋ New' : `New ${kind}` }), save: () => screen.getByRole('button', { name: kind === 'action' ? 'Save action' : 'Save' }) };
}
it.each(types)('bounds a stalled %s listing and accepts only the explicit retry result', async (kind) => {
  vi.useFakeTimers(); const key = kind === 'class' ? 'agent_class_list' : kind === 'specialization' ? 'list_specializations' : `list_${kind}s`; const test = setup(kind, key); await test.advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('refresh timed out'); test.hold(''); fireEvent.click(screen.getByRole('button', { name: kind === 'class' ? 'Retry classes' : 'Retry catalog' })); await test.flush(); expect(test.newButton()).toBeEnabled(); await test.release(0, kind === 'class' ? { type: 'agent_classes', classes: [], capability_catalog: [] } : { type: test.key, group: 'g', [test.key]: [] }); expect(screen.queryByText(kind === 'class' ? 'No Agent Classes' : 'No Catalog')).not.toBeInTheDocument();
});
it.each(types)('unlocks a timed-out %s save, retains the draft and ignores the late acknowledgement', async (kind) => {
  const test = setup(kind); await test.select(); const description = screen.getByRole('textbox', { name: 'Description' }); fireEvent.change(description, { target: { value: 'Retained local evidence' } }); test.hold(test.saveCommand); vi.useFakeTimers(); fireEvent.click(test.save()); expect(test.newButton()).toBeDisabled(); await test.advance(30_001); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(test.newButton()).toBeEnabled(); expect(description).toHaveValue('Retained local evidence'); await test.release(); expect(screen.queryByRole('status')).not.toBeInTheDocument(); await test.reconnect(); expect(test.calls.filter((call) => call.command.cmd === test.saveCommand)).toHaveLength(1); expect(test.mutation).not.toHaveBeenCalled();
});

it.each(types)('retains a mounted %s draft and caret through a timed-out detail/catalog refresh', async (kind) => {
  const test = setup(kind); await test.select(); const input = screen.getByRole<HTMLInputElement>('textbox', { name: 'Description' }); fireEvent.change(input, { target: { value: 'Keep the current draft' } }); input.focus(); input.setSelectionRange(2, 8);
  test.hold(kind === 'class' ? 'agent_class_list' : `get_${test.detailKind}`); vi.useFakeTimers(); await test.reconnect(); await test.advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('refresh timed out'); expect(input).toHaveFocus(); expect(input).toHaveValue('Keep the current draft'); expect([input.selectionStart, input.selectionEnd]).toEqual([2, 8]);
  test.hold(''); fireEvent.click(screen.getByRole('button', { name: kind === 'class' ? 'Retry classes' : 'Retry definition' })); await test.flush(); await test.release(); expect(screen.getByRole('textbox', { name: 'Description' })).toBe(input); expect(input).toHaveValue('Keep the current draft');
});
it.each(types)('ends %s save observation on unmount and does not publish a late acknowledgement', async (kind) => {
  const test = setup(kind); await test.select(); test.hold(test.saveCommand); fireEvent.click(test.save()); test.dispatch.mockClear(); test.unmount(); expect(test.calls.filter((call) => call.command.cmd === test.saveCommand).at(-1)?.signal.aborted).toBe(true); await test.release(); expect(test.dispatch).not.toHaveBeenCalled(); expect(test.mutation).not.toHaveBeenCalled();
});
it('bounds class validation and creation while retaining the new class draft', async () => {
  const test = setup('class'); await test.select(); test.hold('agent_class_validate'); vi.useFakeTimers(); fireEvent.click(screen.getByRole('button', { name: 'Validate' })); await test.advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('refresh timed out'); test.hold(''); fireEvent.click(screen.getByRole('button', { name: 'Validate' })); await test.flush(); expect(screen.getByText('Validation passed')).toBeVisible(); await test.release();
  fireEvent.click(test.newButton()); fireEvent.change(screen.getByRole('textbox', { name: 'ID' }), { target: { value: 'new-class' } }); fireEvent.change(screen.getByRole('textbox', { name: 'Display name' }), { target: { value: 'New class' } }); test.hold('agent_class_create'); fireEvent.click(test.save()); await test.advance(30_001); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByRole('textbox', { name: 'ID' })).toHaveValue('new-class'); expect(test.newButton()).toBeEnabled(); await test.release(1); expect(screen.queryByRole('status')).not.toBeInTheDocument();
});
it('bounds action variable discovery and rendering, ignores late previews and allows retry', async () => {
  const test = setup('action'); await test.select(); test.hold('render_action'); vi.useFakeTimers(); fireEvent.change(screen.getByRole('textbox', { name: 'Prompt' }), { target: { value: 'New {{ TASK }}' } }); await test.advance(15_251); expect(screen.getByRole('alert')).toHaveTextContent('refresh timed out'); test.hold(''); fireEvent.click(screen.getByRole('button', { name: 'Retry variables' })); await test.advance(251); expect(screen.getByRole('textbox', { name: 'Preview TASK' })).toBeVisible(); await test.release();
  test.hold('render_action'); fireEvent.click(screen.getByRole('button', { name: 'Preview' })); await test.advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('refresh timed out'); expect(screen.getByRole('button', { name: 'Preview' })).toBeEnabled(); test.hold(''); fireEvent.click(screen.getByRole('button', { name: 'Preview' })); await test.flush(); expect(screen.getByLabelText('Rendered action prompt')).toHaveTextContent('Current rendered prompt'); await test.release(1, { type: 'action_rendered', workspace_group: 'g', scope: 'project', name: 'example', prompt: 'Obsolete rendered text' }); expect(screen.getByLabelText('Rendered action prompt')).not.toHaveTextContent('Obsolete');
});
it.each(types)('unlocks a stalled %s delete confirmation without replay or late success', async (kind) => {
  const test = setup(kind); await test.select(); if (kind === 'class') fireEvent.click(screen.getByRole('button', { name: /Example class/ })); await test.flush();
  const cmd = kind === 'class' ? 'agent_class_delete' : `delete_${kind}`; test.hold(cmd); vi.useFakeTimers(); fireEvent.click(screen.getByRole('button', { name: 'Delete' })); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' })); await test.advance(30_001); expect(within(screen.getByRole('dialog')).getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' })).toBeEnabled(); await test.release(); expect(screen.getByRole('dialog')).toBeVisible(); expect(test.calls.filter((call) => call.command.cmd === cmd)).toHaveLength(1); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' })); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('unlocks a timed-out class archive and leaves the authoritative archived class read only', async () => {
  const test = setup('class'); await test.select(); test.hold('agent_class_archive'); vi.useFakeTimers(); fireEvent.click(screen.getByRole('button', { name: 'Archive' })); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Archive' })); await test.advance(30_001); expect(within(screen.getByRole('dialog')).getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByRole('heading', { name: 'Archived Agent Class', hidden: true })).toBeVisible(); await test.release(); expect(screen.queryByRole('status')).not.toBeInTheDocument(); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' })); expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
});
it('retains the initially displayed class when refreshed catalog order changes', async () => {
  const test = setup('class'); await test.select(); const input = screen.getByRole('textbox', { name: 'Description' }); fireEvent.change(input, { target: { value: 'Unsaved first class' } }); test.hold('agent_class_list'); await test.reconnect(); const existing = (test.waiting[0]!.frame.classes as UnknownRecord[])[0]!; await test.release(0, { ...test.waiting[0]!.frame, classes: [{ ...existing, id: 'another', display_name: 'Another class', authoring_definition: { ...(existing.authoring_definition as object), id: 'another', display_name: 'Another class' } }, existing] }); expect(screen.getByRole('textbox', { name: 'ID' })).toHaveValue('example'); expect(screen.getByRole('textbox', { name: 'Description' })).toBe(input); expect(input).toHaveValue('Unsaved first class');
});
