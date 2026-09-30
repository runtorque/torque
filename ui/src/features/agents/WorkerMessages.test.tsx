import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { beforeEach, expect, it, vi } from 'vitest';
import { useAppSelector } from '../../app/hooks';
import { connectionActions, createAppStore, projectionActions, selectAuxiliaryResponseState } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import type { DeltaOperation } from '../../protocol';
import { AgentDetailWorkspace } from './AgentDetailWorkspace';
import { toAgentViewModel } from './model';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const message = { content: 'Earlier inline message', timestamp: 10, recipient_agent_id: 'worker', sender_agent_id: 'engineer' };
const task = { id: 'task', group: 'Foundation', task: 'Assigned work', agent_id: 'worker', messages_thread_summary: { count: 1, recipient_agent_ids: ['worker'], last_timestamp: 10 } };
beforeEach(() => { read.mockReset(); read.mockImplementation((command) => Promise.resolve(command.cmd === 'task_detail' ? { type: 'task_detail', id: 'task', task: { ...task, description: '', messages_thread: [message] } } : { type: 'cell_events', cell_id: 'worker', events: [] })); });
function mount(tasks: Record<string, unknown>) {
  const store = createAppStore(); const raw = { id: 'worker', kind: 'worker', name: 'Worker', group: 'Foundation', cell_type: 'agent' };
  const initial = { ...compactStateFixture, agents: { worker: raw }, board_tasks: tasks };
  store.dispatch(projectionActions.snapshotReceived(initial)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(initial));
  function Panel({ active }: { active: boolean }) {
    const responses = useAppSelector(selectAuxiliaryResponseState); const current = useAppSelector((state) => state.projection.data.board_tasks) as Record<string, unknown>;
    return <AgentDetailWorkspace active={active} agent={toAgentViewModel('worker', raw)} group="Foundation" responses={responses} tasks={current} directMessages={[]} peerThreads={[]} digestSettings={{}} digestBufferStats={{}} digestSentEvents={[]} sendCommand={() => true} onUnavailable={() => {}} />;
  }
  const view = render(<Provider store={store}><Panel active /></Provider>);
  return { store, active: (active: boolean) => view.rerender(<Provider store={store}><Panel active={active} /></Provider>), delta: (...ops: DeltaOperation[]) => act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: store.getState().projection.seq + 1, ops })); }) };
}
it('hydrates only relevant compact threads when Worker Messages becomes visible', async () => {
  mount({ task, unrelated: { ...task, id: 'unrelated', agent_id: 'other', messages_thread_summary: { count: 1, recipient_agent_ids: ['other'] } } });
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1)); expect(read.mock.calls.some(([cmd]) => cmd.cmd === 'task_detail')).toBe(false);
  fireEvent.click(screen.getByRole('tab', { name: 'Messages' })); await screen.findAllByText(message.content);
  expect(read.mock.calls.filter(([cmd]) => cmd.cmd === 'task_detail').map(([cmd]) => cmd.id)).toEqual(['task']);
});
it('keeps explicitly addressed messages after task reassignment and excludes unrelated recipients', async () => {
  mount({ task: { ...task, agent_id: 'other', messages_thread: [message, { ...message, content: 'Someone else', recipient_agent_id: 'other' }, { ...message, content: 'Implicit other recipient', recipient_agent_id: '' }] } });
  fireEvent.click(screen.getByRole('tab', { name: 'Messages' })); await screen.findAllByText(message.content); expect(screen.queryByText('Someone else')).not.toBeInTheDocument(); expect(screen.queryByText('Implicit other recipient')).not.toBeInTheDocument();
});
it('preserves the existing message disclosure and focus when a newer inline message arrives', async () => {
  const app = mount({ task: { ...task, messages_thread: [message] } }); fireEvent.click(screen.getByRole('tab', { name: 'Messages' }));
  const summary = (await screen.findAllByText(message.content)).find((node) => node.closest('summary'))!.closest('summary')!; fireEvent.click(summary); summary.focus();
  app.delta({ op: 'task_upsert', ...task, messages_thread_summary: { count: 2, recipient_agent_ids: ['worker'], last_timestamp: 20 }, messages_thread: [message, { ...message, timestamp: 20, content: 'Newer inline message' }] });
  await screen.findAllByText('Newer inline message'); expect(summary).toHaveFocus(); expect(summary.closest('details')).toHaveAttribute('open'); expect(summary).toHaveTextContent(message.content);
});
it('refreshes changed compact message summaries, retains readable history and ignores unrelated or hidden changes', async () => {
  const app = mount({ task }); fireEvent.click(screen.getByRole('tab', { name: 'Messages' })); await screen.findAllByText(message.content);
  const count = read.mock.calls.length;
  app.delta({ op: 'task_upsert', id: 'unrelated', group: 'Foundation', agent_id: 'other', messages_thread_summary: { count: 1, recipient_agent_ids: ['other'] } }); expect(read).toHaveBeenCalledTimes(count);
  let release!: (value: Awaited<ReturnType<typeof readCommand>>) => void;
  read.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  app.delta({ op: 'task_upsert', ...task, messages_thread_summary: { count: 2, recipient_agent_ids: ['worker'], last_timestamp: 20 } });
  expect(read).toHaveBeenCalledTimes(count + 1); expect(screen.getAllByText(message.content).length).toBeGreaterThan(0);
  await act(async () => { release({ type: 'task_detail', id: 'task', task: { ...task, description: '', messages_thread: [message, { ...message, timestamp: 20, content: 'Refreshed inline message' }] } }); await Promise.resolve(); });
  await screen.findAllByText('Refreshed inline message'); app.active(false); const hidden = read.mock.calls.length;
  app.delta({ op: 'task_upsert', ...task, messages_thread_summary: { count: 3, recipient_agent_ids: ['worker'], last_timestamp: 30 } }); expect(read).toHaveBeenCalledTimes(hidden);
});
it('rejects a mismatched detail response and recovers through explicit retry', async () => {
  read.mockImplementation((command) => Promise.resolve(command.cmd === 'task_detail' ? { type: 'task_detail', id: 'other', task: { id: 'other', group: 'Foundation', messages_thread: [{ ...message, content: 'Wrong task message' }] } } : { type: 'cell_events', cell_id: 'worker', events: [] }));
  mount({ task }); fireEvent.click(screen.getByRole('tab', { name: 'Messages' })); await screen.findByRole('alert'); expect(screen.queryByText('Wrong task message')).not.toBeInTheDocument();
  read.mockResolvedValueOnce({ type: 'task_detail', id: 'task', task: { ...task, messages_thread: [message] } }); fireEvent.click(screen.getByRole('button', { name: 'Retry activity' })); await screen.findAllByText(message.content); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
