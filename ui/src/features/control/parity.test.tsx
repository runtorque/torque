import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { browserHost } from '../../host';
import { compactStateFixture } from '../../protocol/fixtures';
import { LogViewer } from './LogViewer';
import { matchesLog } from './logModel';
import { PeerChat } from './PeerChat';
import { PipelineExplorer } from './PipelineExplorer';
import { layoutPipeline, type Pipeline } from './pipelineModel';

const pipeline: Pipeline = { name: 'Build', actions: ['implement', 'review', 'ship'], edges: [{ from: 'implement', to: 'review', when: 'ready' }, { from: 'review', to: 'implement', when: 'changes needed' }, { from: 'review', to: 'ship', when: 'approved' }], asks: [{ from: 'ship', when: 'deployment approval' }] };
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('log viewer', () => {
  it('filters levels and regex, with literal fallback for invalid patterns', () => {
    expect(matchesLog({ level: 'ERROR', raw: 'failed [task]' }, 'ERROR', 'fail.*task')).toBe(true);
    expect(matchesLog({ level: 'INFO', raw: 'failed' }, 'ERROR', '')).toBe(false);
    expect(matchesLog({ raw: 'failed [task]' }, '', '[task')).toBe(true);
  });
  it('reads real logs, filters locally, switches targets and aborts on close', async () => {
    const requests: AbortSignal[] = []; let hold = false;
    const fetcher = vi.fn((url: string, options: RequestInit) => { requests.push(options.signal as AbortSignal); if (hold) return new Promise(() => {}); return Promise.resolve({ ok: true, json: () => Promise.resolve({ target: url.includes('supervisor') ? 'supervisor' : 'daemon', cursor: 42, lines: [{ ts: 1, level: 'ERROR', message: url.includes('supervisor') ? 'PTY failed' : 'Daemon failed' }, { ts: 2, level: 'INFO', message: 'Ready' }] }) }); });
    vi.stubGlobal('fetch', fetcher);
    const view = render(<Provider store={createAppStore()}><LogViewer host={browserHost} /></Provider>);
    expect(await screen.findByText('Daemon failed')).toBeVisible();
    expect(fetcher.mock.calls[0]?.[0]).toContain('/logs?target=daemon&since=0');
    fireEvent.change(screen.getByLabelText('Level'), { target: { value: 'ERROR' } });
    expect(screen.queryByText('Ready')).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Reveal log folder' })).not.toBeInTheDocument();
    hold = true; fireEvent.click(screen.getByRole('button', { name: 'Refresh logs' }));
    const pending = requests.at(-1)!; expect(pending.aborted).toBe(false); hold = false;
    fireEvent.change(screen.getByLabelText('Log target'), { target: { value: 'supervisor' } });
    expect(await screen.findByText('PTY failed')).toBeVisible();
    expect(screen.queryByText('Daemon failed')).not.toBeInTheDocument();
    expect(pending.aborted).toBe(true);
    hold = true; fireEvent.click(screen.getByRole('button', { name: 'Refresh logs' }));
    expect(requests.at(-1)?.aborted).toBe(false);
    view.unmount(); expect(requests.at(-1)?.aborted).toBe(true);
  });
  it('pauses follow polling and bounds retained lines', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ cursor: 10, lines: Array.from({ length: 500 }, (_, i) => ({ message: `line ${i}` })) }) }));
    vi.stubGlobal('fetch', fetcher);
    render(<Provider store={createAppStore()}><LogViewer host={browserHost} /></Provider>);
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(screen.getByRole('log').children).toHaveLength(2000);
    fireEvent.click(screen.getByLabelText('Follow'));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const calls = fetcher.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(fetcher).toHaveBeenCalledTimes(calls);
  });
  it('shows recoverable errors instead of an empty successful log', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    render(<Provider store={createAppStore()}><LogViewer host={browserHost} /></Provider>);
    expect(await screen.findByRole('alert')).toHaveTextContent('offline');
    expect(screen.getByRole('button', { name: 'Refresh logs' })).toBeEnabled();
  });
});

describe('aggregate peer Chat', () => {
  it('shows cross-group threads, progressive history, context and stable selection across deltas', () => {
    const messages = Array.from({ length: 65 }, (_, i) => ({ id: `m${i}`, message: `Message ${i}`, sender_name: 'Engineer A', recipient_name: 'Engineer B', context: { summary: 'Task evidence', task_ids: ['task-7'] } }));
    const threads = { old: { title: 'Other group', last_activity_at: 1, messages: [] }, recent: { title: 'Engineers', last_activity_at: 2, messages } };
    const view = render(<PeerChat threads={threads} agents={{}} />);
    expect(screen.getByRole('button', { name: /Other group/ })).toBeVisible();
    expect(screen.queryByText('Message 0')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Load older messages/ }));
    expect(screen.getByText('Message 0')).toBeVisible();
    const detail = screen.getAllByText('Context details')[0]!.closest('details')!;
    detail.open = true;
    view.rerender(<PeerChat threads={{ ...threads, recent: { ...threads.recent, messages: [...messages, { id: 'new', message: 'New update' }] } }} agents={{}} />);
    expect(screen.getByText('Message 0')).toBeVisible();
    expect(detail.open).toBe(true);
    expect(screen.getAllByText('task-7').length).toBe(65);
    expect(screen.queryByRole('textbox', { name: /compose|message/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^send/i })).not.toBeInTheDocument();
  });
});

describe('pipeline explorer', () => {
  it('lays out cycles without recursion failure or overlapping nodes', () => {
    const layout = layoutPipeline(pipeline);
    expect(layout.nodes).toHaveLength(3);
    expect(new Set(layout.nodes.map((node) => `${node.x},${node.y}`)).size).toBe(3);
    expect(layout.width).toBeGreaterThan(400);
  });
  it('projects scoped discovery, renders conditions/asks and opens the editor from a node', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'pipelines', pipelines: [pipeline] } }) });
    vi.stubGlobal('fetch', fetcher);
    const store = createAppStore(); const edit = vi.fn();
    store.dispatch(projectionActions.snapshotReceived(compactStateFixture));
    store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
    store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
    render(<Provider store={store}><PipelineExplorer group="A" onEdit={edit} /></Provider>);
    const graph = await screen.findByRole('group', { name: 'Pipeline graph' });
    expect(screen.getByText(/deployment approval/)).toBeVisible();
    expect(within(screen.getByLabelText('Pipeline transitions')).getByText(/changes needed/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in pipeline' }));
    expect(screen.getByText('125%')).toBeVisible();
    fireEvent.keyDown(graph, { key: 'ArrowRight' });
    expect(graph.getAttribute('viewBox')).not.toMatch(/^0 0 /);
    fireEvent.click(screen.getByRole('button', { name: 'Fit pipeline' }));
    expect(screen.getByText('100%')).toBeVisible();
    fireEvent.keyDown(within(graph).getByRole('button', { name: 'Edit action review' }), { key: 'Enter' });
    expect(edit).toHaveBeenCalledWith('review');
    act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'events_page', events: [] })); });
    expect(screen.getByRole('group', { name: 'Pipeline graph' })).toBe(graph);
    expect(store.getState().projection.data.auxiliary_responses).toHaveProperty('pipelines:A');
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  });
});

describe('operational depth', () => {
  it('sorts supervisor rows, persists preferences, and stops polling when unmounted', async () => {
    const { SupervisorDetails } = await import('./OperationalDetails');
    const sessions = [{ session_id: 'b', pid: 2, alive: true, owner: { name: 'Beta', group: 'G' }, display_command: 'shell-b', current_path: '/tmp/b', cols: 80, rows: 24 }, { session_id: 'a', pid: 1, alive: true, owner: { name: 'Alpha', group: 'G' }, display_command: 'shell-a' }];
    const signals: AbortSignal[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { signals.push(options.signal as AbortSignal); return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'supervisor_sessions', sessions } }) }); }));
    const store = createAppStore(); const send = vi.fn();
    const view = render(<Provider store={store}><SupervisorDetails supervisor={{ sessions }} send={send} onTerminate={vi.fn()} /></Provider>);
    await screen.findByText(/Updated/);
    const rows = screen.getAllByRole('button', { name: /shell-/ });
    expect(rows[0]).toHaveTextContent('Alpha');
    fireEvent.change(screen.getByLabelText('Sort sessions'), { target: { value: 'pid' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ascending' }));
    expect(screen.getAllByRole('button', { name: /shell-/ })[0]).toHaveTextContent('Beta');
    fireEvent.click(screen.getAllByRole('button', { name: /shell-/ })[0]!);
    expect(screen.getByText('/tmp/b')).toBeVisible();
    expect(screen.getByText('80 × 24')).toBeVisible();
    const persisted = send.mock.calls.at(-1)?.[0] as { cmd: string; state: Record<string, unknown> };
    expect(persisted.cmd).toBe('ui_set_supervisor_panel_state');
    expect(persisted.state).toMatchObject({ sortKey: 'pid', sortDirection: 'desc', expandedSessionId: 'b' });
    vi.mocked(fetch).mockImplementationOnce((_url, options) => { if (options?.signal) signals.push(options.signal); return new Promise<Response>(() => {}); });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh sessions' })); view.unmount(); expect(signals.at(-1)?.aborted).toBe(true);
  });
  it('uses supported health windows and scoped workflow series with accessible samples', async () => {
    const { HealthDetails } = await import('./OperationalDetails');
    const commands: Record<string, unknown>[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}' ) as Record<string, unknown>; commands.push(command); return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: command.cmd === 'get_metrics_history' ? { type: 'metrics_history', group: command.group, window: command.window, bucket_seconds: 3600, buckets: [{ label: '01:00' }, { label: '02:00' }], perf: { rss_mb: [100, 110] } } : { type: 'system_health_metrics', group: command.group, window: command.window, scope: command.group ? 'group' : 'all_groups', buckets: [{ label: '01:00' }, { label: '02:00' }], series: { dispatches: [1, 2] } } }) }); }));
    const healthStore = createAppStore();
    healthStore.dispatch(connectionActions.connected({ at: 1, reconnect: false })); healthStore.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
    render(<Provider store={healthStore}><HealthDetails group="A" runtime={{ supervisor: { status: 'connected', sessions: 2 } }} /></Provider>);
    expect(await screen.findByRole('img', { name: /Process memory history/ })).toBeVisible();
    expect(screen.getByRole('img', { name: /dispatches history/ })).toBeVisible();
    fireEvent.change(screen.getByLabelText('Health scope'), { target: { value: 'all' } });
    fireEvent.change(screen.getByLabelText('Health history window'), { target: { value: '30d' } });
    await waitFor(() => expect(commands).toContainEqual({ cmd: 'get_system_health_metrics', group: '', window: '30d' }));
    expect(screen.queryByRole('option', { name: '1 hour' })).not.toBeInTheDocument();
    expect(screen.getByText('connected')).toBeVisible();
  });
});

describe('typed settings and attention workflows', () => {
  it('edits typed values and adds/removes environment entries without JSON', async () => {
    const { useState } = await import('react');
    const { StructuredSettings } = await import('./StructuredSettings');
    function Form() {
      const [value, setValue] = useState<Record<string, unknown>>({ metrics_enabled: true, max_event_log: 500, worktree_symlinks: ['node_modules'], env_vars: { SAMPLE: 'value' } });
      return <><StructuredSettings value={value} onChange={setValue} /><output>{JSON.stringify(value)}</output></>;
    }
    render(<Form />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Metrics enabled' }), { target: { value: 'false' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Max event log' }), { target: { value: '600' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Worktree symlinks' }), { target: { value: 'node_modules\n.cache\n' } });
    expect(screen.getByRole('textbox', { name: 'Worktree symlinks' })).toHaveValue('node_modules\n.cache\n');
    fireEvent.click(screen.getByRole('button', { name: 'Remove Env vars: SAMPLE' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'New env vars key' }), { target: { value: 'NEW' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add entry' }));
    const result = JSON.parse(screen.getByRole('status').textContent || '{}') as Record<string, unknown>;
    expect(result).toEqual({ metrics_enabled: false, max_event_log: 600, worktree_symlinks: ['node_modules', '.cache'], env_vars: { NEW: '' } });
  });
  it('resolves an explicit reply target, preserves the answer draft, and copies filtered events', async () => {
    const { ActivityPanel } = await import('./OperatorPanels');
    const { compactStateFixture } = await import('../../protocol/fixtures');
    const store = createAppStore(); const send = vi.fn(); const copy = vi.fn().mockResolvedValue(undefined);
    store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: copy }, configurable: true });
    store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, agents: { worker: { id: 'worker', cell_type: 'agent', session_id: 'live', status: 'running' } }, board_tasks: { ask: { id: 'ask', task: 'Approve review', lane: 'Backlog', labels: ['torque:human'], reply_agent_id: 'worker' } } }));
    const fetcher = vi.fn((_url: string, options: RequestInit) => { const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as Record<string, unknown>; return Promise.resolve({ ok: true, json: () => Promise.resolve(command.cmd === 'task_detail' ? { ok: true, data: { type: 'task_detail', id: 'ask', task: { id: 'ask', description: 'Complete question', lane: 'Backlog', labels: ['torque:human'], reply_agent_id: 'worker' } } } : { ok: false, error: 'Delivery unavailable' }) }); });
    vi.stubGlobal('fetch', fetcher);
    const events = [{ id: 42, kind: 'error', message: 'Actual event body' }, { id: 43, kind: 'info', message: 'Informational' }];
    render(<Provider store={store}><ActivityPanel events={events} send={send} /></Provider>);
    fireEvent.change(screen.getByRole('textbox', { name: 'Answer Approve review' }), { target: { value: 'Proceed' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Kind' }), { target: { value: 'error' } });
    expect(screen.queryByText('Informational')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Actual event body'));
    fireEvent.click(screen.getByRole('button', { name: 'Copy event' }));
    await screen.findByText('Event copied'); expect(copy).toHaveBeenCalledWith('Actual event body');
    fireEvent.click(screen.getByRole('button', { name: 'Resolve ask' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Delivery unavailable');
    expect(JSON.parse(fetcher.mock.calls.at(-1)?.[1].body as string)).toMatchObject({ cmd: 'resolve_ask', id: 'ask', answer: 'Proceed' });
    expect(screen.getByRole('textbox', { name: 'Answer Approve review' })).toHaveValue('Proceed');
    fireEvent.click(screen.getByRole('button', { name: 'Load older' }));
    expect(send).toHaveBeenCalledWith({ cmd: 'get_events', before_id: 42, limit: 200 });
  });
});
