import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions, workspaceUiActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { BoardPanel } from './BoardPanel';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const task = { id: 'task', task: 'Saved task', description: 'Saved scope', group: 'Foundation', lane: 'Backlog', attachments: [{ filename: 'old.png', path: '/attachments/task/old.png' }], artifacts: [], action_vars: {} };
const ack: AuxiliaryFrame = { type: 'state', seq: 12, board_tasks: { task: { ...task, attachments: [] } } };
function held<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
const deadline = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(30_001); }); };
async function setup(write: (command: TorqueCommand, signal: AbortSignal) => Promise<AuxiliaryFrame>) {
  const writes: TorqueCommand[] = [];
  read.mockImplementation((command, signal) => {
    if (command.cmd === 'task_detail') return Promise.resolve({ type: 'task_detail', id: command.id, task: { ...task, id: command.id } });
    if (command.cmd === 'list_actions') return Promise.resolve({ type: 'actions', group: command.group, actions: [] });
    if (command.cmd === 'list_roles') return Promise.resolve({ type: 'roles', group: command.group, roles: [] });
    writes.push(command); return write(command, signal);
  });
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { task, other: { ...task, id: 'other', task: 'Other task' } } })); store.dispatch(workspaceUiActions.setDetailTask('task'));
  const send = vi.fn(() => true); const view = render(<Provider store={store}><BoardPanel group="Foundation" sendCommand={send} onCommandUnavailable={vi.fn()} /></Provider>); await flush();
  return { ...view, store, send, writes };
}
function removeOld() { fireEvent.click(screen.getByRole('tab', { name: 'Evidence' })); fireEvent.click(screen.getByRole('button', { name: 'Remove attachment old.png' })); }
beforeEach(() => { vi.useFakeTimers(); read.mockReset(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
it('releases an unacknowledged edit while retaining the draft and never starts dependent cleanup', async () => {
  const pending = held<AuxiliaryFrame>(); let signal: AbortSignal | undefined;
  const { writes } = await setup((_command, owner) => { signal = owner; return pending.promise; }); removeOld();
  const draft = screen.getByRole('textbox', { name: 'Description' }); fireEvent.change(draft, { target: { value: 'Retain unsaved edit' } }); fireEvent.click(screen.getByRole('button', { name: 'Save task' })); await deadline();
  expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByRole('button', { name: 'Save task' })).toBeEnabled(); expect(draft).toHaveValue('Retain unsaved edit'); expect(signal?.aborted).toBe(true);
  pending.resolve(ack); await flush(); expect(writes.map((command) => command.cmd)).toEqual(['board_update_task']); expect(screen.getByRole('dialog')).toBeVisible();
});
it('rejects an unrelated acknowledgement without clearing the draft or starting cleanup', async () => {
  let valid = false; const { writes } = await setup(() => Promise.resolve(valid ? ack : { type: 'state', seq: 12, board_tasks: { other: { id: 'other' } } })); removeOld();
  fireEvent.click(screen.getByRole('button', { name: 'Save task' })); await flush(); expect(screen.getByRole('alert')).toHaveTextContent('invalid acknowledgement'); expect(writes).toHaveLength(1); expect(screen.getByRole('dialog')).toBeVisible();
  valid = true; fireEvent.click(screen.getByRole('button', { name: 'Save task' })); await flush(); expect(writes.map((command) => command.cmd)).toEqual(['board_update_task', 'board_update_task', 'remove_attachment']); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('retains an acknowledged edit when attachment cleanup times out and retries cleanup only', async () => {
  let hold = true; const pending = held<AuxiliaryFrame>();
  const { writes } = await setup((command) => command.cmd === 'remove_attachment' && hold ? pending.promise : Promise.resolve(ack)); removeOld();
  fireEvent.click(screen.getByRole('button', { name: 'Save task' })); await flush(); await deadline(); expect(screen.getByRole('alert')).toHaveTextContent('Task changes saved, but attachment cleanup failed');
  hold = false; fireEvent.click(screen.getByRole('button', { name: 'Save task' })); await flush(); expect(writes.map((command) => command.cmd)).toEqual(['board_update_task', 'remove_attachment', 'remove_attachment']); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  pending.resolve(ack); await flush(); expect(writes).toHaveLength(3);
});
it('cancels an old editor sequence before it can clean files or close a replacement task', async () => {
  const pending = held<AuxiliaryFrame>(); let signal: AbortSignal | undefined;
  const { store, writes } = await setup((_command, owner) => { signal = owner; return pending.promise; }); removeOld(); fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  act(() => { store.dispatch(workspaceUiActions.setDetailTask('other')); }); await flush(); expect(signal?.aborted).toBe(true);
  pending.resolve(ack); await flush(); expect(writes.map((command) => command.cmd)).toEqual(['board_update_task']); expect(screen.getByRole('dialog')).toBeVisible(); expect(store.getState().workspaceUi.detailTaskId).toBe('other');
});
it('bounds an upload response body, preserves accepted evidence and ignores late metadata', async () => {
  await setup(() => Promise.resolve(ack)); const pending = held<unknown>(); let signal: AbortSignal | undefined; let uploads = 0;
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { signal = options.signal as AbortSignal; uploads++; return Promise.resolve({ ok: true, json: () => uploads === 1 ? Promise.resolve({ ok: true, data: [{ filename: 'first.png', path: '/attachments/task/first.png', mime_type: 'image/png' }] }) : pending.promise }); }));
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' })); fireEvent.change(screen.getByLabelText('Upload evidence files'), { target: { files: [new File(['a'], 'first.png', { type: 'image/png' }), new File(['b'], 'second.png', { type: 'image/png' })] } }); await flush(); await deadline();
  expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(signal?.aborted).toBe(true); expect(screen.getByRole('button', { name: 'Remove attachment first.png' })).toBeVisible(); expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled();
  pending.resolve({ ok: true, data: [{ filename: 'second.png', path: '/attachments/task/second.png', mime_type: 'image/png' }] }); await flush(); expect(screen.queryByRole('button', { name: 'Remove attachment second.png' })).not.toBeInTheDocument();
});
it('rejects upload metadata owned by another task', async () => {
  await setup(() => Promise.resolve(ack)); vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: [{ filename: 'wrong.png', path: '/attachments/other/wrong.png', mime_type: 'image/png' }] }) })));
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' })); fireEvent.change(screen.getByLabelText('Upload evidence files'), { target: { files: [new File(['x'], 'wrong.png', { type: 'image/png' })] } }); await flush();
  expect(screen.getByRole('alert')).toHaveTextContent('invalid upload acknowledgement'); expect(screen.queryByRole('button', { name: 'Remove attachment wrong.png' })).not.toBeInTheDocument();
});

it.each(['close', 'restore draft', 'retry revised draft'])('preserves files from an unknown save when choosing %s', async (choice) => {
  const pending = held<AuxiliaryFrame>(); const { writes } = await setup(() => Promise.resolve(ack)); let latest: UnknownRecord = { ...task }; let firstSave: UnknownRecord | undefined;
  read.mockImplementation((command) => {
    if (command.cmd === 'task_detail') return Promise.resolve({ type: 'task_detail', id: 'task', task: latest });
    if (command.cmd === 'list_actions') return Promise.resolve({ type: 'actions', group: command.group, actions: [] });
    if (command.cmd === 'list_roles') return Promise.resolve({ type: 'roles', group: command.group, roles: [] });
    writes.push(command);
    if (command.cmd === 'board_update_task') { latest = { ...latest, ...command }; if (!firstSave) { firstSave = latest; return pending.promise; } }
    if (command.cmd === 'remove_attachment') latest = { ...latest, attachments: (latest.attachments as UnknownRecord[]).filter((item) => item.filename !== command.filename) };
    return Promise.resolve({ type: 'state', seq: 13, board_tasks: { task: latest } });
  });
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: [{ filename: 'new.png', path: '/attachments/task/new.png', mime_type: 'image/png' }] }) })));
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' })); fireEvent.change(screen.getByLabelText('Upload evidence files'), { target: { files: [new File(['a'], 'new.png', { type: 'image/png' })] } }); await flush();
  fireEvent.click(screen.getByRole('button', { name: 'Save task' })); await deadline(); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown');
  if (choice === 'close') fireEvent.click(screen.getByRole('button', { name: /^Close$/ }));
  else {
    fireEvent.click(screen.getByRole('button', { name: 'Remove attachment new.png' }));
    if (choice === 'retry revised draft') fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'Revised title' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
  }
  await flush(); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  // The first write can complete after a later retry; neither path may delete its files.
  latest = firstSave!;
  expect(latest.attachments).toEqual(expect.arrayContaining([expect.objectContaining({ filename: 'new.png' })]));
  expect(writes.filter((command) => command.cmd === 'remove_attachment')).toHaveLength(0);
  pending.resolve({ type: 'state', seq: 12, board_tasks: { task: latest } }); await flush();
});

it('locks task edits after an uncertain discard until cleanup is retried', async () => {
  const pending = held<AuxiliaryFrame>(); let removals = 0;
  await setup((command) => command.cmd === 'remove_attachment' && ++removals === 1 ? pending.promise : Promise.resolve(ack));
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: [{ filename: 'new.png', path: '/attachments/task/new.png', mime_type: 'image/png' }] }) })));
  fireEvent.click(screen.getByRole('tab', { name: 'Evidence' })); fireEvent.change(screen.getByLabelText('Upload evidence files'), { target: { files: [new File(['a'], 'new.png', { type: 'image/png' })] } }); await flush();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); await deadline();
  expect(screen.getByRole('alert')).toHaveTextContent('Could not discard new uploads');
  expect(screen.getByRole('button', { name: 'Save task' })).toBeDisabled(); expect(screen.getByRole('textbox', { name: 'Title' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); await flush(); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  pending.resolve(ack); await flush(); expect(removals).toBe(2);
});
