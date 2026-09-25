import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppSelector } from '../../app/hooks';
import { connectionActions, createAppStore, projectionActions, selectAuxiliaryResponseState } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import { AgentDetailWorkspace } from './AgentDetailWorkspace';
import { toAgentViewModel } from './model';
import { activityReads, validateActivityRead, type ActivityTab } from './activityReads';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const limits = { events: 20, journal: 20, mcp: 20, history: 20, class: 20 };
const filter = { tool: '', outcome: 'all', range: '24h' };
beforeEach(() => { read.mockReset(); });
function mount() {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
  const agent = toAgentViewModel('agent-1', { id: 'agent-1', name: 'Worker', group: 'Foundation', kind: 'worker', status: 'idle' });
  function Panel({ active }: { active: boolean }) { const responses = useAppSelector(selectAuxiliaryResponseState); return <AgentDetailWorkspace active={active} agent={agent} group="Foundation" catalog={{ agentClasses: [] }} responses={responses} tasks={{}} directMessages={[]} peerThreads={[]} digestSettings={{}} digestBufferStats={{}} digestSentEvents={[]} sendCommand={() => true} onUnavailable={() => {}} />; }
  const view = render(<Provider store={store}><Panel active /></Provider>);
  return { store, hide: () => view.rerender(<Provider store={store}><Panel active={false} /></Provider>), show: () => view.rerender(<Provider store={store}><Panel active /></Provider>), reconnect: () => act(() => { store.dispatch(connectionActions.disconnected({ at: 2 })); store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); }) };
}

describe('visible Agent Activity reads', () => {
  it('maps each role/tab to only the resources it displays', () => {
    const expected: Partial<Record<ActivityTab, string[]>> = { decisions: ['decisions_snapshot'], journal: ['architect_journal_read'], messages: ['architect_peer_inbox'], events: ['get_cell_events'], queued: [], worklog: [], mcp: ['mcp_calls'], history: ['get_agent_history_detail'], class: ['agent_class_list', 'agent_class_status', 'agent_class_audit'], chat: ['architect_peer_inbox'] };
    for (const [tab, commands] of Object.entries(expected)) expect(activityReads(tab as ActivityTab, 'a', 'architect', 'G', limits, filter, 100_000).map((item) => item.command.cmd)).toEqual(commands);
    expect(activityReads('journal', 'e', 'engineer', 'G', limits, filter, 100_000).map((item) => item.command.cmd)).toEqual(['engineer_journal_snapshot', 'engineer_session_map_read', 'get_group_settings']);
    expect(activityReads('messages', 'w', 'worker', 'G', limits, filter, 100_000)).toEqual([]);
    const [mcp] = activityReads('mcp', 'w', 'worker', 'G', { ...limits, mcp: 40 }, { tool: 'progress', outcome: 'error', range: '1h' }, 100_000);
    expect(mcp!.command).toMatchObject({ tool_name_pattern: '*progress*', success_filter: 'error', limit: 40, since: 96400 });
    expect(() => validateActivityRead({ type: 'mcp_calls', cell_id: 'other' }, mcp!)).toThrow('requested target');
  });
  it('retains visible content, disclosure and focus through snapshot refresh, failure and retry', async () => {
    const event = { id: 'retained', kind: 'task_progress', message: 'Retained event', timestamp: 100 };
    read.mockResolvedValueOnce({ type: 'cell_events', cell_id: 'agent-1', events: [event] });
    const app = mount(); await waitFor(() => expect(screen.getByText('task progress')).toBeVisible());
    const summary = screen.getByText('task progress').closest('summary')!; fireEvent.click(summary); act(() => summary.focus());
    let release!: (value: { type: string; cell_id: string; events: unknown[] }) => void;
    read.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; })); app.reconnect();
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2)); expect(screen.getByText('task progress').closest('summary')).toBe(summary); expect(summary).toHaveFocus(); expect(summary.closest('details')).toHaveAttribute('open');
    await act(async () => { release({ type: 'cell_events', cell_id: 'wrong', events: [] }); await Promise.resolve(); }); await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('requested target')); expect(screen.getByText('Retained event')).toBeVisible();
    read.mockResolvedValueOnce({ type: 'cell_events', cell_id: 'agent-1', events: [event, { ...event, id: 'new', message: 'New event' }] }); fireEvent.click(screen.getByRole('button', { name: 'Retry activity' })); await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument()); expect(screen.getByText('Retained event')).toBeVisible(); expect(summary.closest('details')).toHaveAttribute('open');
  });
  it('does not apply unfinished MCP filters on refresh and ignores responses from hidden or obsolete reads', async () => {
    read.mockImplementation((command) => Promise.resolve({ type: command.cmd === 'get_cell_events' ? 'cell_events' : 'mcp_calls', cell_id: 'agent-1', events: [], calls: [] }));
    const app = mount(); await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('tab', { name: 'MCP' })); await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    fireEvent.change(screen.getByLabelText('Tool contains'), { target: { value: 'unsaved_filter' } }); expect(read).toHaveBeenCalledTimes(2); fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); await waitFor(() => expect(read).toHaveBeenCalledTimes(3)); expect(read.mock.calls[2]![0]).toMatchObject({ tool_name_pattern: 'mcp__torque__%' });
    let release!: (value: { type: string; cell_id: string; calls: unknown[] }) => void; read.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; })); fireEvent.click(screen.getByRole('button', { name: 'Apply' })); await waitFor(() => expect(read).toHaveBeenCalledTimes(4)); expect(read.mock.calls[3]![0]).toMatchObject({ tool_name_pattern: '*unsaved_filter*' });
    const signal = read.mock.calls[3]![1]; app.hide(); expect(signal?.aborted).toBe(true); app.reconnect(); expect(read).toHaveBeenCalledTimes(4);
    await act(async () => { release({ type: 'mcp_calls', cell_id: 'agent-1', calls: [{ tool_name: 'OBSOLETE' }] }); await Promise.resolve(); }); expect(screen.queryByText('OBSOLETE')).not.toBeInTheDocument();
    app.show(); await waitFor(() => expect(read).toHaveBeenCalledTimes(5)); expect(read.mock.calls[4]![0]).toMatchObject({ tool_name_pattern: '*unsaved_filter*' }); expect(screen.getByLabelText('Tool contains')).toHaveValue('unsaved_filter');
  });
  it('bounds stalled reads and exposes retry instead of an endless refresh', async () => {
    vi.useFakeTimers();
    try {
      read.mockImplementation(() => new Promise(() => {})); mount(); const signal = read.mock.calls[0]![1];
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      expect(signal?.aborted).toBe(true); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.getByRole('button', { name: 'Retry activity' })).toBeEnabled();
    } finally { vi.useRealTimers(); }
  });

});
