import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { beforeEach, expect, it, vi } from 'vitest';
import { useAppSelector } from '../../app/hooks';
import { connectionActions, createAppStore, projectionActions, selectAuxiliaryResponseState } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import type { DeltaOperation, UnknownRecord } from '../../protocol';
import { AgentDetailWorkspace } from './AgentDetailWorkspace';
import { toAgentViewModel } from './model';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const message = { id: 'message', thread_id: 'logical-thread', message: 'Earlier peer message', sender_id: 'peer', recipient_id: 'architect', timestamp: 10 };
const pair = { thread_id: 'agent-pair:architect:peer', participant_ids: ['architect', 'peer'], messages: [message] };
const thread = { thread_id: 'logical-thread', peer_name: 'Peer Architect', last_message_at: 10, messages: [message] };
beforeEach(() => { read.mockReset(); read.mockImplementation((command) => Promise.resolve(command.cmd === 'architect_peer_inbox' ? { type: 'architect_peer_inbox', architect_id: 'architect', threads: [thread] } : { type: 'decisions_snapshot', decisions: {} })); });
function mount() {
  const store = createAppStore(); const raw = { id: 'architect', name: 'Architect', kind: 'architect', group: 'Foundation', cell_type: 'agent', mcp_messages: [message] };
  const initial = { ...compactStateFixture, agents: { architect: raw }, board_tasks: {}, agent_peer_threads: { [pair.thread_id]: pair } };
  store.dispatch(projectionActions.snapshotReceived(initial)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(initial));
  function Panel({ active }: { active: boolean }) { const responses = useAppSelector(selectAuxiliaryResponseState); const peers = useAppSelector((state) => state.projection.data.agent_peer_threads); const agents = useAppSelector((state) => state.projection.data.agents) as Record<string, UnknownRecord>; return <AgentDetailWorkspace active={active} agent={toAgentViewModel('architect', agents.architect!)} group="Foundation" responses={responses} tasks={{}} directMessages={[]} peerThreads={peers} digestSettings={{}} digestBufferStats={{}} digestSentEvents={[]} sendCommand={() => true} onUnavailable={() => {}} />; }
  const view = render(<Provider store={store}><Panel active /></Provider>);
  return { active: (active: boolean) => view.rerender(<Provider store={store}><Panel active={active} /></Provider>), delta: (...ops: DeltaOperation[]) => act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: store.getState().projection.seq + 1, ops })); }) };
}
it('updates a hydrated Architect peer thread after its live pair changes without reading unrelated or hidden updates', async () => {
  const app = mount(); fireEvent.click(screen.getByRole('tab', { name: 'Peer chat' })); await screen.findByRole('button', { name: /Peer Architect/ }); const count = read.mock.calls.length;
  app.delta({ op: 'agent_peer_thread_upsert', thread: { thread_id: 'unrelated', participant_ids: ['other', 'another'], messages: [] } }); expect(read).toHaveBeenCalledTimes(count);
  const newer = { ...message, id: 'new', timestamp: 20, message: 'New live peer message' };
  read.mockResolvedValueOnce({ type: 'architect_peer_inbox', architect_id: 'architect', threads: [{ ...thread, last_message_at: 20, messages: [message, newer] }] });
  app.delta({ op: 'agent_peer_thread_upsert', thread: { ...pair, messages: [message, newer] } }); await screen.findAllByText(newer.message); expect(read).toHaveBeenCalledTimes(count + 1);
  app.active(false); const hidden = read.mock.calls.length; app.delta({ op: 'agent_peer_thread_remove', thread_id: pair.thread_id }); expect(read).toHaveBeenCalledTimes(hidden);
});
it('renders one Architect message when the same persisted ID appears in both agent cache and peer inbox', async () => {
  mount(); fireEvent.click(screen.getByRole('tab', { name: 'Messages' })); await waitFor(() => expect(screen.queryByText('Refreshing activity…')).not.toBeInTheDocument());
  expect(screen.getAllByText(message.message).filter((node) => node.closest('summary'))).toHaveLength(1);
});
it('honors an explicitly empty peer inbox instead of resurrecting a cached snapshot thread', async () => {
  mount(); fireEvent.click(screen.getByRole('tab', { name: 'Peer chat' })); await screen.findByRole('button', { name: /Peer Architect/ });
  read.mockResolvedValueOnce({ type: 'architect_peer_inbox', architect_id: 'architect', threads: [] }); fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await screen.findByText('No peer threads'); expect(screen.queryByText(message.message)).not.toBeInTheDocument();
});
it('keeps the newer inbox when a prior refresh resolves after a matching pair update', async () => {
  const app = mount(); fireEvent.click(screen.getByRole('tab', { name: 'Peer chat' })); await screen.findByRole('button', { name: /Peer Architect/ });
  let release!: (value: Awaited<ReturnType<typeof readCommand>>) => void; read.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; })); fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  const newer = { ...message, id: 'new', timestamp: 20, message: 'Accepted current peer message' }; read.mockResolvedValueOnce({ type: 'architect_peer_inbox', architect_id: 'architect', threads: [{ ...thread, messages: [message, newer] }] });
  app.delta({ op: 'agent_peer_thread_upsert', thread: { ...pair, messages: [message, newer] } }); await screen.findAllByText(newer.message);
  await act(async () => { release({ type: 'architect_peer_inbox', architect_id: 'architect', threads: [thread] }); await Promise.resolve(); }); expect(screen.getAllByText(newer.message).length).toBeGreaterThan(0);
});
