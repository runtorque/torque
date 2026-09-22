import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions, workspaceUiActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { BoardPanel } from './BoardPanel';
import { localSchedule, taskEditChanges } from './taskEditModel';

afterEach(() => vi.unstubAllGlobals());
function setup(extra: UnknownRecord = {}) {
  const store = createAppStore();
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { task: { id: 'task', task: 'Saved task', description: 'Saved scope', group: 'Foundation', lane: 'Backlog', action_vars: {}, agent_id: 'agent-1', labels: ['original'], ...extra } } }));
  store.dispatch(workspaceUiActions.setDetailTask('task'));
  const calls: TorqueCommand[] = []; let failure = ''; let deferred: ((command: TorqueCommand) => Promise<unknown>) | null = null;
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
    const command = JSON.parse(options.body as string) as TorqueCommand; calls.push(command);
    if (deferred) return deferred(command);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: command.cmd === failure ? { type: 'error', message: 'Task has active work in its assigned worker. Stop or complete that worker before editing.' } : command.cmd === 'preview_prompt' ? { type: 'prompt_preview', task_id: 'task', prompt: 'Correct draft preview' } : { type: 'state', seq: 10, board_tasks: {} } }) });
  }));
  const send = vi.fn(() => true);
  render(<Provider store={store}><BoardPanel group="Foundation" sendCommand={send} onCommandUnavailable={vi.fn()} /></Provider>);
  return { calls, store, send, fail: (cmd: string) => { failure = cmd; }, defer: (fn: (command: TorqueCommand) => Promise<unknown>) => { deferred = fn; } };
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
  await act(async () => { complete({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'state' } }) }); await Promise.resolve(); });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('cleans removed files only after a successful save and retries cleanup without repeating the edit', async () => {
  const { calls, fail } = setup({ attachments: [{ filename: 'evidence.png', path: '/evidence.png' }] });
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
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
