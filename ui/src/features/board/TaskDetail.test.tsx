import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions, workspaceUiActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { BoardPanel } from './BoardPanel';
import { localSchedule, taskEditChanges } from './taskEditModel';

afterEach(() => vi.unstubAllGlobals());
function setup(extra: UnknownRecord = {}) {
  const store = createAppStore();
  store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { task: { id: 'task', task: 'Saved task', description: 'Saved scope', group: 'Foundation', lane: 'Backlog', action_vars: {}, agent_id: 'agent-1', labels: ['original'], ...extra } } }));
  store.dispatch(workspaceUiActions.setDetailTask('task'));
  const calls: TorqueCommand[] = []; const detailReads: TorqueCommand[] = []; let failure = ''; let deferred: ((command: TorqueCommand) => Promise<unknown>) | null = null;
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
    if (_url === '/api/upload') { const file = (options.body as FormData).get('file') as File; return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: [{ filename: file.name, path: `/attachments/task/${file.name}`, mime_type: file.type }] }) }); }
    const command = JSON.parse(options.body as string) as TorqueCommand;
    // These editor tests inject full detail frames explicitly; mutation counts exclude reads.
    if (command.cmd === 'task_detail') { detailReads.push(command); return new Promise(() => {}); }
    if (command.cmd === 'list_actions' || command.cmd === 'list_roles') { const kind = command.cmd === 'list_actions' ? 'actions' : 'roles'; return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: kind, group: command.group, [kind]: [] } }) }); }
    calls.push(command);
    if (deferred) return deferred(command);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: command.cmd === failure ? { type: 'error', message: 'Task has active work in its assigned worker. Stop or complete that worker before editing.' } : command.cmd === 'preview_prompt' ? { type: 'prompt_preview', task_id: 'task', prompt: 'Correct draft preview' } : { type: 'state', seq: 10, board_tasks: { task: { id: 'task' } } } }) });
  }));
  const send = vi.fn(() => true);
  render(<Provider store={store}><BoardPanel group="Foundation" sendCommand={send} onCommandUnavailable={vi.fn()} /></Provider>);
  return { calls, detailReads, store, send, fail: (cmd: string) => { failure = cmd; }, defer: (fn: (command: TorqueCommand) => Promise<unknown>) => { deferred = fn; } };
}
it('retains rejected edits and saves only changed fields after a retry', async () => {
  const { calls, fail, store } = setup();
  fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: 'Unsaved scope' } });
  act(() => { store.dispatch(projectionActions.taskDetailReceived({ type: 'task_detail', id: 'task', task: { labels: ['concurrent'], description: 'Another operator edit' } })); });
  fail('board_update_task'); fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  await screen.findByRole('alert');
  expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('Unsaved scope');
  expect(calls[0]).toEqual({ cmd: 'board_update_task', id: 'task', description: 'Unsaved scope', enforce_dispatch_edit_gate: true });
  fail(''); fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(calls).toHaveLength(2);
});
it('blocks duplicate saves, close and mutation controls until acknowledgement', async () => {
  const { calls, defer } = setup(); let complete!: (response: unknown) => void;
  defer(() => new Promise((resolve) => { complete = resolve; }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: 'Pending scope' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  fireEvent.click(screen.getByRole('button', { name: 'Saving task…' }));
  fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
  expect(screen.getByRole('dialog')).toBeVisible(); expect(calls).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  expect(screen.getByRole('textbox', { name: 'Title' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Dispatch task' })).toBeDisabled();
  await act(async () => { complete({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'state', seq: 10, board_tasks: { task: { id: 'task' } } } }) }); await Promise.resolve(); });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('cleans removed files only after a successful save and retries cleanup without repeating the edit', async () => {
  const { calls, fail } = setup({ attachments: [{ filename: 'evidence.png', path: '/evidence.png' }] });
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove attachment evidence.png' }));
  fail('board_update_task'); fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  await screen.findByRole('alert'); expect(calls.some((cmd) => cmd.cmd === 'remove_attachment')).toBe(false);
  fail('remove_attachment'); fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  await screen.findByText(/Task changes saved, but attachment cleanup failed/);
  expect(calls.map((cmd) => cmd.cmd)).toEqual(['board_update_task', 'board_update_task', 'remove_attachment']);
  fail(''); fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(calls.map((cmd) => cmd.cmd)).toEqual(['board_update_task', 'board_update_task', 'remove_attachment', 'remove_attachment']);
});
it('previews explicitly cleared draft fields through a correlated request and ignores unrelated preview frames', async () => {
  const { calls, store } = setup({ agent_template: 'saved-role', action_name: 'build' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: '' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'Draft title' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview prompt' }));
  await screen.findByText('Correct draft preview');
  expect(calls[0]).toMatchObject({ cmd: 'preview_prompt', id: 'task', task: 'Draft title', description: '', agent_id: 'agent-1', agent_template: 'saved-role' });
  act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'prompt_preview', task_id: 'other', prompt: 'Wrong inspector prompt' })); });
  expect(screen.queryByText('Wrong inspector prompt')).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'New draft' } });
  expect(screen.queryByText('Correct draft preview')).not.toBeInTheDocument();
  expect(screen.getByText('Draft changed. Preview again to see the current prompt.')).toBeVisible();
});
it('keeps unchanged schedule seconds and unrelated verification metadata out of sparse edits', () => {
  const schedule = localSchedule('2026-10-01T12:30:45Z');
  expect(new Date(schedule).getMinutes()).toBe(30);
  const baseline = { action_vars: '{}', scheduled_at: schedule, verification_summary: { tests_run: 'old', manual_smoke_done: false }, description: 'old' };
  expect(taskEditChanges(baseline, { ...baseline, description: 'new' }, [], {})).toEqual({ description: 'new' });
  expect(taskEditChanges(baseline, { ...baseline, verification_summary: { tests_run: 'new', manual_smoke_done: false } }, [], { verification_summary: { tests_run: 'old', manual_smoke_done: true, provider_evidence: 'retained' } })).toEqual({ verification_summary: { tests_run: 'new', manual_smoke_done: true, provider_evidence: 'retained' } });
});

it('stages structured edits, preserves the composer across tabs and merges concurrent evidence on acknowledged save', async () => {
  const original = { id: 'report', type: 'snippet', title: 'Original', content: 'old', prompt: { mode: 'inline' }, metadata: { source: 'worker' } };
  const { calls, fail, store } = setup({ artifacts: [original] });
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' }));
  fireEvent.click(screen.getByRole('button', { name: 'Edit artifact Original' }));
  fireEvent.change(screen.getByLabelText('Artifact title'), { target: { value: 'Edited report' } });
  fireEvent.change(screen.getByLabelText('Artifact content'), { target: { value: 'draft evidence' } });
  fireEvent.click(screen.getByRole('tab', { name: 'Execution' }));
  expect(screen.getByRole('button', { name: 'Save task' })).toBeDisabled();
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' }));
  expect(screen.getByLabelText('Artifact title')).toHaveValue('Edited report');
  fireEvent.click(screen.getByRole('button', { name: 'Save artifact' }));
  expect(calls).toHaveLength(0);
  act(() => { store.dispatch(projectionActions.taskDetailReceived({ type: 'task_detail', id: 'task', task: { artifacts: [{ ...original, metadata: { source: 'updated worker' } }, { id: 'remote', title: 'Remote evidence' }] } })); });
  fireEvent.click(screen.getByRole('tab', { name: 'Execution' }));
  fireEvent.click(screen.getByRole('button', { name: 'Preview prompt' }));
  await screen.findByText('Correct draft preview');
  expect(calls[0]).toMatchObject({ artifacts: [expect.objectContaining({ title: 'Edited report', content: 'draft evidence' })] });
  fail('board_update_task'); fireEvent.click(screen.getByRole('button', { name: 'Save task' })); await screen.findByRole('alert');
  expect(calls[1]).toMatchObject({ artifacts: [expect.objectContaining({ title: 'Edited report', metadata: { source: 'updated worker' } }), { id: 'remote', title: 'Remote evidence' }] });
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' }));
  expect(screen.getByRole('button', { name: 'Edit artifact Edited report' })).toBeVisible();
  fail(''); fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});
it('Cancel preserves original evidence and retries cleanup of only new uploads', async () => {
  const { calls, fail } = setup({ attachments: [{ filename: 'original.png' }], artifacts: [{ id: 'external', title: 'External', filename: 'external.log', lifecycle: { owner: 'agent' } }] });
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove attachment original.png' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove artifact External' }));
  fireEvent.change(screen.getByLabelText('Upload evidence files'), { target: { files: [new File(['png'], 'new.png', { type: 'image/png' })] } });
  await screen.findByRole('button', { name: 'Remove attachment new.png' });
  fail('remove_attachment'); fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  await screen.findByText(/Could not discard new uploads/);
  expect(calls).toEqual([{ cmd: 'remove_attachment', task_id: 'task', filename: 'new.png' }]);
  fail(''); fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(calls).toHaveLength(2); expect(calls[1]).toEqual(calls[0]);
});
it('keeps files still referenced by another artifact after removing their attachment', async () => {
  const { calls } = setup({ attachments: [{ filename: 'shared.png' }], artifacts: [{ id: 'other', title: 'Shared', filename: 'shared.png' }] });
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove attachment shared.png' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(calls).toEqual([{ cmd: 'board_update_task', id: 'task', attachments: [], enforce_dispatch_edit_gate: true }]);
});
it('opens activity from a compact card after hydration and normal detail returns to Execution', async () => {
  const { store, detailReads } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  act(() => { store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { task: { id: 'task', task: 'Compact task', group: 'Foundation', lane: 'Backlog' } } })); });
  fireEvent.click(screen.getByRole('button', { name: 'Actions for Compact task' }));
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Task activity' }));
  expect(detailReads.at(-1)).toEqual({ cmd: 'task_detail', id: 'task' });
  expect(screen.getByText('Retrieving complete task fields.')).toBeVisible();
  act(() => { store.dispatch(projectionActions.taskDetailReceived({ type: 'task_detail', id: 'task', task: { description: '', messages: [{ action: 'progress', agent: 'Worker A', timestamp: 1700000000, message: 'Hydrated activity' }] } })); });
  expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByText('Hydrated activity')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.doubleClick(screen.getByLabelText('Compact task, Backlog'));
  expect(screen.getByRole('tab', { name: 'Execution' })).toHaveAttribute('aria-selected', 'true');
});
