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
const now = Date.now() / 1000;
const journal = { id: 1, architect_id: 'principal', entry: 'Earlier architect journal', timestamp: now };
const decision = { id: 'decision', architect_id: 'principal', title: 'Reviewed decision', status: 'proposed', archived: false };
const success = { cursor: 1, cell_id: 'principal', hook_event_name: 'PostToolUse', tool_name: 'mcp__torque__task_progress', success: true, appended_at: now };
const failure = { ...success, cursor: 2, tool_name: 'mcp__torque__task_error', success: false };
beforeEach(() => {
  read.mockReset();
  read.mockImplementation((command) => Promise.resolve(command.cmd === 'architect_journal_read' ? { type: 'architect_journal_entries', architect_id: 'principal', entries: [journal] }
    : command.cmd === 'decisions_snapshot' ? { type: 'decisions_snapshot', decisions: { decision } }
      : command.cmd === 'mcp_calls' ? { type: 'mcp_calls', cell_id: 'principal', calls: [success, failure] }
        : { type: 'cell_events', cell_id: 'principal', events: [{ id: 1, cell_id: 'principal', kind: 'agent_started', timestamp: now, message: 'Earlier event' }] }));
});
function mount(kind = 'architect') {
  const store = createAppStore(); const send = vi.fn(() => true);
  const raw = { id: 'principal', name: 'Selected principal', kind, group: 'Foundation', cell_type: 'agent', status: 'running' };
  const initial = { ...compactStateFixture, agents: { principal: raw }, board_tasks: {} };
  store.dispatch(projectionActions.snapshotReceived(initial)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(initial));
  function Panel() {
    const responses = useAppSelector(selectAuxiliaryResponseState); const agents = useAppSelector((state) => state.projection.data.agents) as Record<string, UnknownRecord>;
    return <AgentDetailWorkspace agent={toAgentViewModel('principal', agents.principal!)} group="Foundation" responses={responses} tasks={{}} directMessages={[]} peerThreads={[]} digestSettings={{}} digestBufferStats={{}} digestSentEvents={[]} sendCommand={send} onUnavailable={() => {}} />;
  }
  render(<Provider store={store}><Panel /></Provider>);
  return { store, send, delta: (...ops: DeltaOperation[]) => act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: store.getState().projection.seq + 1, ops })); }) };
}

it('updates only the selected Architect journal from a live append and retains the current disclosure', async () => {
  const app = mount(); fireEvent.click(screen.getByRole('tab', { name: 'Journal' })); await screen.findAllByText('Earlier architect journal');
  const summary = screen.getAllByText('Earlier architect journal').find((node) => node.closest('summary'))!.closest('summary')!; fireEvent.click(summary); summary.focus(); const count = read.mock.calls.length;
  app.delta({ op: 'architect_journal_append', architect_id: 'other', id: 2, entry: 'Other architect entry' }, { op: 'architect_journal_append', architect_id: 'principal', id: 3, entry: 'New architect entry' });
  await screen.findAllByText('New architect entry'); expect(screen.queryByText('Other architect entry')).not.toBeInTheDocument(); expect(summary).toHaveFocus(); expect(summary.closest('details')).toHaveAttribute('open'); expect(read).toHaveBeenCalledTimes(count);
});

it('applies external decision update, archive and removal to the selected Architect', async () => {
  const app = mount(); await screen.findByText('Reviewed decision'); fireEvent.click(screen.getByText('Reviewed decision').closest('summary')!);
  app.delta({ op: 'decision_upsert', ...decision, status: 'accepted' });
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument());
  app.delta({ op: 'decision_upsert', ...decision, status: 'accepted', archived: true });
  await screen.findByRole('button', { name: 'Show archived (1)' }); expect(screen.queryByText('Reviewed decision')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Show archived (1)' })); await screen.findByText('Reviewed decision');
  app.delta({ op: 'decision_remove', id: 'decision' }); expect(screen.queryByText('Reviewed decision')).not.toBeInTheDocument();
});

it('applies the MCP outcome filter even when the read endpoint returns both outcomes', async () => {
  mount('worker'); fireEvent.click(screen.getByRole('tab', { name: 'MCP' })); await screen.findByText(success.tool_name); await screen.findByText(failure.tool_name);
  fireEvent.change(screen.getByLabelText('Outcome'), { target: { value: 'error' } }); fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(screen.queryByText(success.tool_name)).not.toBeInTheDocument()); expect(screen.getByText(failure.tool_name)).toBeInTheDocument();
});

it('merges matching live MCP calls while retaining the applied filters and an unfinished filter draft', async () => {
  const app = mount('worker'); fireEvent.click(screen.getByRole('tab', { name: 'MCP' })); await screen.findByText(success.tool_name);
  const existing = screen.getByText(success.tool_name).closest('details')!; fireEvent.click(existing.querySelector('summary')!);
  const input = screen.getByLabelText('Tool contains'); fireEvent.change(input, { target: { value: 'unfinished_filter' } }); input.focus(); const count = read.mock.calls.length;
  app.delta({ op: 'mcp_call_append', call: { ...success, cursor: 3, tool_name: 'mcp__torque__task_complete' } }, { op: 'mcp_call_append', call: { ...success, cursor: 4, cell_id: 'other', tool_name: 'mcp__torque__other_agent' } }, { op: 'mcp_call_append', call: { ...success, cursor: 5, hook_event_name: 'PreToolUse', tool_name: 'mcp__torque__unfinished_call' } });
  await screen.findByText('mcp__torque__task_complete'); expect(screen.queryByText('mcp__torque__other_agent')).not.toBeInTheDocument(); expect(screen.queryByText('mcp__torque__unfinished_call')).not.toBeInTheDocument(); expect(input).toHaveValue('unfinished_filter'); expect(input).toHaveFocus(); expect(existing).toHaveAttribute('open'); expect(read).toHaveBeenCalledTimes(count);
});

it('shows a matching live cell event immediately and retains previously loaded event history', async () => {
  const app = mount('worker'); await screen.findByText('agent started'); const summary = screen.getByText('agent started').closest('summary')!; fireEvent.click(summary); summary.focus();
  app.delta({ op: 'event_append', id: 2, cell_id: 'other', kind: 'task_complete', message: 'Other event', timestamp: now + 1 }, { op: 'event_append', id: 3, cell_id: 'principal', kind: 'task_complete', message: 'Live selected event', timestamp: now + 2 });
  await screen.findByText('Live selected event'); expect(screen.queryByText('Other event')).not.toBeInTheDocument(); expect(screen.getByText('Earlier event')).toBeInTheDocument(); expect(summary).toHaveFocus(); expect(summary.closest('details')).toHaveAttribute('open');
});


it('offers older MCP pages when outcome filtering removes every row from a full page', async () => {
  read.mockImplementation((command) => Promise.resolve(command.cmd === 'mcp_calls' ? { type: 'mcp_calls', cell_id: 'principal', calls: Number(command.limit) > 20 ? [...Array.from({ length: 20 }, (_, index) => ({ ...success, cursor: index + 1 })), { ...failure, cursor: 21 }] : Array.from({ length: 20 }, (_, index) => ({ ...success, cursor: index + 1 })) } : { type: 'cell_events', cell_id: 'principal', events: [] }));
  mount('worker'); fireEvent.click(screen.getByRole('tab', { name: 'MCP' })); await screen.findAllByText(success.tool_name);
  fireEvent.change(screen.getByLabelText('Outcome'), { target: { value: 'error' } }); fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(screen.queryAllByText(success.tool_name)).toHaveLength(0));
  fireEvent.click(screen.getByRole('button', { name: /^Load more principal-mcp/ })); await screen.findByText(failure.tool_name);
});

it('refreshes low-level events only when the selected agent event timestamp changes', async () => {
  const app = mount('worker'); await screen.findByText('agent started'); const count = read.mock.calls.length;
  app.delta({ op: 'agent_upsert', id: 'other', kind: 'worker', last_event_at: now + 1 }); expect(read).toHaveBeenCalledTimes(count);
  read.mockResolvedValueOnce({ type: 'cell_events', cell_id: 'principal', events: [{ id: 'live:2', cell_id: 'principal', kind: 'tool_end', message: 'Low-level tool finished', timestamp: now + 2 }] });
  app.delta({ op: 'agent_upsert', id: 'principal', last_event_at: now + 2 }); await screen.findByText('Low-level tool finished'); expect(read).toHaveBeenCalledTimes(count + 1);
  fireEvent.click(screen.getByRole('tab', { name: 'History' })); const historyCount = read.mock.calls.length;
  app.delta({ op: 'agent_upsert', id: 'principal', last_event_at: now + 3 }); expect(read).toHaveBeenCalledTimes(historyCount);
});


it('does not use the previous filter page to advance a newly submitted pending MCP query', async () => {
  read.mockImplementation((command) => Promise.resolve(command.cmd === 'mcp_calls' ? { type: 'mcp_calls', cell_id: 'principal', calls: Array.from({ length: 20 }, (_, index) => ({ ...success, cursor: index + 1 })) } : { type: 'cell_events', cell_id: 'principal', events: [] }));
  mount('worker'); fireEvent.click(screen.getByRole('tab', { name: 'MCP' })); await screen.findAllByText(success.tool_name);
  read.mockImplementation(() => new Promise(() => {})); fireEvent.change(screen.getByLabelText('Tool contains'), { target: { value: 'new-unmatched-query' } }); fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  expect(screen.queryByRole('button', { name: /^Load more principal-mcp/ })).not.toBeInTheDocument();
});
