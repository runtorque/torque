import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppSelector } from '../../app/hooks';
import { connectionActions, createAppStore, projectionActions, selectAuxiliaryResponseState } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import { AgentDetailWorkspace } from './AgentDetailWorkspace';
import { toAgentViewModel } from './model';
import { activityReads, agentClassBaseDir, validateActivityRead, type ActivityTab } from './activityReads';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const limits = { events: 20, journal: 20, mcp: 20, history: 20, class: 20 };
const filter = { tool: '', outcome: 'all', range: '24h' };
beforeEach(() => { read.mockReset(); });
function mount() {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
  const agent = toAgentViewModel('agent-1', { id: 'agent-1', name: 'Worker', group: 'Foundation', kind: 'worker', status: 'idle' });
  function Panel({ active }: { active: boolean }) { const responses = useAppSelector(selectAuxiliaryResponseState); return <AgentDetailWorkspace active={active} agent={agent} group="Foundation" responses={responses} tasks={{}} directMessages={[]} peerThreads={[]} digestSettings={{}} digestBufferStats={{}} digestSentEvents={[]} sendCommand={() => true} onUnavailable={() => {}} />; }
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

  it('uses the agent project rather than the shared catalog and rejects obsolete project replies', async () => {
    expect(agentClassBaseDir({ worktree_repo_root: '/repo', directory: '/worktree', current_path: '/cwd' })).toBe('/repo');
    expect(agentClassBaseDir({ directory: '/project', current_path: '/cwd' })).toBe('/project'); expect(agentClassBaseDir({ current_path: '/cwd' })).toBe('/cwd');
    const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
    const sent = vi.fn(() => true); let release!: (frame: { type: string; base_dir: string; classes: unknown[] }) => void;
    const classDefinition = { id: 'shared-id', base_kind: 'worker', display_name: 'Project B class' };
    read.mockImplementation((command) => {
      if (command.cmd === 'agent_class_list' && command.base_dir === '/a') return new Promise((resolve) => { release = resolve; });
      if (command.cmd === 'agent_class_list') return Promise.resolve({ type: 'agent_classes', base_dir: '/b', classes: [classDefinition, { id: 'disabled-class', base_kind: 'worker', display_name: 'Unavailable class', launchable: false }] });
      if (command.cmd === 'agent_class_status') return Promise.resolve({ type: 'agent_class_status', status: { agent_id: 'agent-1' } });
      if (command.cmd === 'agent_class_audit') return Promise.resolve({ type: 'agent_class_audit', agent_id: 'agent-1', events: [] });
      return Promise.resolve({ type: 'cell_events', cell_id: 'agent-1', events: [] });
    });
    function Panel({ directory }: { directory: string }) { const responses = useAppSelector(selectAuxiliaryResponseState); return <AgentDetailWorkspace agent={toAgentViewModel('agent-1', { id: 'agent-1', kind: 'worker', name: 'Worker', directory })} group="Foundation" responses={responses} tasks={{}} directMessages={[]} peerThreads={[]} digestSettings={{}} digestBufferStats={{}} digestSentEvents={[]} sendCommand={sent} onUnavailable={() => {}} />; }
    const view = render(<Provider store={store}><Panel directory="/a" /></Provider>); fireEvent.click(screen.getByRole('tab', { name: 'Agent Class' }));
    await waitFor(() => expect(read.mock.calls.some(([command]) => command.cmd === 'agent_class_list' && command.base_dir === '/a')).toBe(true));
    const signal = read.mock.calls.find(([command]) => command.cmd === 'agent_class_list')![1];
    act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'agent_classes', classes: [{ id: 'wrong', display_name: 'Unrelated global catalog', base_kind: 'worker' }] })); });
    expect(screen.queryByRole('option', { name: 'Unrelated global catalog' })).not.toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Save assignment' })).toBeDisabled();
    view.rerender(<Provider store={store}><Panel directory="/b" /></Provider>); await screen.findByRole('option', { name: 'Project B class' }); expect(screen.getByRole('option', { name: 'Unavailable class (unavailable)' })).toBeDisabled(); expect(signal.aborted).toBe(true);
    await act(async () => { release({ type: 'agent_classes', base_dir: '/a', classes: [{ ...classDefinition, display_name: 'Obsolete A class' }] }); await Promise.resolve(); }); expect(screen.queryByRole('option', { name: 'Obsolete A class' })).not.toBeInTheDocument();
    const select = screen.getByLabelText('Desired class for next launch'); fireEvent.change(select, { target: { value: 'shared-id' } }); await waitFor(() => expect(screen.getByRole('button', { name: 'Save assignment' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Save assignment' })); expect(sent).toHaveBeenCalledWith({ cmd: 'agent_class_assign', agent_id: 'agent-1', base_dir: '/b', class_id: 'shared-id', actor_label: 'trusted-user-react-ui' });
    read.mockImplementation((command) => Promise.resolve(command.cmd === 'agent_class_list' ? { type: 'agent_classes', base_dir: '/WRONG', classes: [] } : command.cmd === 'agent_class_status' ? { type: 'agent_class_status', status: { agent_id: 'agent-1' } } : { type: 'agent_class_audit', agent_id: 'agent-1', events: [] }));
    act(() => { select.focus(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); });
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('requested target')); expect(select).toHaveFocus(); expect(select).toHaveValue('shared-id'); expect(screen.getByRole('option', { name: 'Project B class' })).toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Save assignment' })).toBeDisabled();
    read.mockImplementation((command) => Promise.resolve(command.cmd === 'agent_class_list' ? { type: 'agent_classes', base_dir: '/b', classes: [] } : command.cmd === 'agent_class_status' ? { type: 'agent_class_status', status: { agent_id: 'agent-1' } } : { type: 'agent_class_audit', agent_id: 'agent-1', events: [] }));
    fireEvent.click(screen.getByRole('button', { name: 'Retry activity' })); await screen.findByRole('option', { name: 'shared-id (unavailable)' }); expect(select).toHaveValue('shared-id'); expect(screen.getByRole('button', { name: 'Save assignment' })).toBeDisabled();
  });

});
