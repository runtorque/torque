import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { beforeEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import { RejectHireDialog } from './RejectHireDialog';
import { PlanningWorkspace } from './PlanningWorkspace';
vi.mock('../../protocol/http', async (original) => ({ ...await original<Record<string, unknown>>(), readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const hire = { id: 'hire-local', architect_id: 'architect', requested_name: 'Proposed Engineer', status: 'pending' };
const entry = { id: 31, group: 'Foundation', author_cell_id: 'engineer', timestamp: 1710000000, type: 'checkpoint', entry: 'Verified the migration.\nDetailed second line remains readable.' };
function setup() {
  const snapshot = { ...compactStateFixture, agents: { architect: { id: 'architect', name: 'Planning Architect', kind: 'architect', group: 'Foundation' }, engineer: { id: 'engineer', name: 'Journal Engineer', kind: 'engineer', group: 'Foundation' } } };
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(snapshot)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(snapshot));
  const send = vi.fn(() => true); const view = render(<Provider store={store}><PlanningWorkspace group="Foundation" sendCommand={send} onCommandUnavailable={vi.fn()} /></Provider>);
  fireEvent.click(screen.getByRole('button', { name: 'Hires & journals' }));
  const reconnect = () => act(() => { store.dispatch(connectionActions.disconnected({ at: 2 })); store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); store.dispatch(projectionActions.snapshotReceived(snapshot)); store.dispatch(connectionActions.snapshotAccepted(snapshot)); });
  return { ...view, store, send, reconnect };
}
beforeEach(() => { read.mockReset(); read.mockImplementation((command) => Promise.resolve(
  command.cmd === 'pending_hires_snapshot' ? { type: 'pending_hires_snapshot', pending_hires: { [hire.id]: hire } }
    : command.cmd === 'engineer_journal_snapshot' ? { type: 'engineer_journal_snapshot', group: 'Foundation', engineer_journal: { engineer: [entry] } }
      : { type: 'initiative_list', group: 'Foundation', initiatives: [] },
)); });
it('reviews an optional hire rejection note and retains it through reconnect before sending the exact target', async () => {
  const app = setup(); await screen.findByText('Proposed Engineer');
  fireEvent.click(screen.getByRole('button', { name: /Reject/ }));
  const dialog = screen.getByRole('dialog', { name: 'Reject hire request' });
  expect(within(dialog).getByText(/Proposed Engineer/)).toBeVisible();
  const note = within(dialog).getByRole('textbox', { name: 'Optional note' });
  fireEvent.change(note, { target: { value: 'Need a different specialty.\nPlease revise.' } });
  note.focus(); (note as HTMLTextAreaElement).setSelectionRange(2, 8); app.reconnect();
  await waitFor(() => expect(screen.queryByText('Refreshing Planning…')).not.toBeInTheDocument());
  expect(note).toHaveFocus(); expect((note as HTMLTextAreaElement).selectionStart).toBe(2);
  expect(app.send).not.toHaveBeenCalled();
  const original = read.getMockImplementation()!;
  read.mockImplementation((command, signal) => command.cmd === 'pending_hire_reject' ? Promise.resolve({ type: 'ok', ok: true }) : original(command, signal));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Reject hire' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(read).toHaveBeenCalledWith({ cmd: 'pending_hire_reject', id: hire.id, note: 'Need a different specialty.\nPlease revise.' }, expect.any(AbortSignal));
});
it('renders real journal fields with author, timestamp and expandable full text across refresh', async () => {
  const app = setup();
  const summary = await screen.findByText('Verified the migration.');
  expect(screen.getByText('Journal Engineer')).toBeVisible(); expect(screen.getByText('checkpoint')).toBeVisible();
  const detail = summary.closest('details')!; expect(detail).not.toHaveAttribute('open');
  fireEvent.click(summary); expect(detail).toHaveAttribute('open');
  expect(within(detail).getByText(entry.entry, { exact: true, collapseWhitespace: false })).toBeVisible();
  expect(detail.querySelector('time')).toHaveAttribute('datetime', '2024-03-09T16:00:00.000Z');
  app.reconnect(); await waitFor(() => expect(screen.queryByText('Refreshing Planning…')).not.toBeInTheDocument());
  expect(screen.getByText('Verified the migration.').closest('details')).toBe(detail); expect(detail).toHaveAttribute('open');
});

it('retains the reviewed hire and note when a refusal or unrelated acknowledgement arrives', async () => {
  const close = vi.fn(); const rejected = vi.fn();
  const view = render(<RejectHireDialog hire={hire} architect="Planning Architect" onClose={close} onRejected={rejected} />);
  const note = screen.getByRole('textbox', { name: 'Optional note' }); fireEvent.change(note, { target: { value: '  Reviewed note\nSecond line  ' } });
  read.mockRejectedValueOnce(new Error('Injected refusal'));
  fireEvent.click(screen.getByRole('button', { name: 'Reject hire' })); await screen.findByText(/Injected refusal/);
  expect(note).toHaveValue('  Reviewed note\nSecond line  '); expect(note).toHaveAttribute('readonly');
  read.mockResolvedValueOnce({ type: 'mission_control_summary', ok: true });
  fireEvent.click(screen.getByRole('button', { name: 'Retry rejection' })); await screen.findByText(/invalid acknowledgement/);
  expect(rejected).not.toHaveBeenCalled(); expect(close).not.toHaveBeenCalled();
  view.rerender(<RejectHireDialog hire={{ ...hire, status: 'rejected' }} architect="Updated Architect" onClose={close} onRejected={rejected} />);
  expect(read).toHaveBeenCalledTimes(2); expect(note).toHaveValue('  Reviewed note\nSecond line  ');
  read.mockResolvedValueOnce({ type: 'ok', ok: true }); fireEvent.click(screen.getByRole('button', { name: 'Retry rejection' }));
  await waitFor(() => expect(rejected).toHaveBeenCalledTimes(1));
  expect(read.mock.calls.map(([command]) => command)).toEqual(Array.from({ length: 3 }, () => ({ cmd: 'pending_hire_reject', id: hire.id, note: 'Reviewed note\nSecond line' })));
});
it('bounds hire rejection, prevents pending dismissal and repetition, and ignores a late result after explicit retry', async () => {
  vi.useFakeTimers();
  try {
    const close = vi.fn(); const rejected = vi.fn(); let release!: (value: Awaited<ReturnType<typeof readCommand>>) => void;
    read.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    render(<RejectHireDialog hire={hire} architect="Planning Architect" onClose={close} onRejected={rejected} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reject hire' }));
    expect(screen.getByRole('button', { name: 'Rejecting…' })).toBeDisabled(); expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' })); expect(close).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_001); });
    expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(read.mock.calls[0]![1].aborted).toBe(true);
    expect(read).toHaveBeenCalledTimes(1);
    read.mockResolvedValueOnce({ type: 'ok', ok: true }); fireEvent.click(screen.getByRole('button', { name: 'Retry rejection' }));
    await act(async () => { await Promise.resolve(); }); expect(rejected).toHaveBeenCalledTimes(1);
    await act(async () => { release({ type: 'error', message: 'Obsolete refusal' }); await Promise.resolve(); });
    expect(rejected).toHaveBeenCalledTimes(1); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(read.mock.calls[1]![0]).toEqual({ cmd: 'pending_hire_reject', id: hire.id, note: '' });
  } finally { vi.useRealTimers(); }
});
it('cancels only the hire dialog observation on unmount without applying a late result', async () => {
  let release!: (value: Awaited<ReturnType<typeof readCommand>>) => void; const rejected = vi.fn();
  read.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const view = render(<RejectHireDialog hire={hire} architect="Planning Architect" onClose={vi.fn()} onRejected={rejected} />);
  fireEvent.click(screen.getByRole('button', { name: 'Reject hire' })); const signal = read.mock.calls[0]![1]; view.unmount(); expect(signal.aborted).toBe(true);
  await act(async () => { release({ type: 'ok', ok: true }); await Promise.resolve(); }); expect(rejected).not.toHaveBeenCalled(); expect(read).toHaveBeenCalledTimes(1);
});
