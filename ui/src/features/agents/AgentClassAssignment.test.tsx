import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppSelector } from '../../app/hooks';
import { connectionActions, createAppStore, projectionActions, selectAuxiliaryResponseState } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { AgentDetailWorkspace } from './AgentDetailWorkspace';
import { toAgentViewModel } from './model';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
let saved: UnknownRecord;
const status = (id: string, extra: UnknownRecord = {}) => ({ agent_id: 'agent-1', assigned_class_id: id, next_launch_class_id: id, effective_class_id: 'default-worker', pending_next_launch: Boolean(id), ...extra });
beforeEach(() => {
  saved = status(''); read.mockReset();
  read.mockImplementation((command) => Promise.resolve(command.cmd === 'agent_class_list'
    ? { type: 'agent_classes', classes: ['a', 'b'].map((id) => ({ id, display_name: `Class ${id}`, base_kind: 'worker' })) }
    : command.cmd === 'agent_class_status' ? { type: 'agent_class_status', status: saved }
      : command.cmd === 'agent_class_audit' ? { type: 'agent_class_audit', agent_id: 'agent-1', events: [] }
        : { type: 'cell_events', cell_id: 'agent-1', events: [] }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
function mount() {
  const store = createAppStore();
  const reconnect = () => act(() => {
    store.dispatch(connectionActions.connected({ at: 1, reconnect: true }));
    store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
  }); reconnect();
  function Panel({ assignedAt = 0, agentStatus = 'idle' }: { assignedAt?: number; agentStatus?: string }) {
    const responses = useAppSelector(selectAuxiliaryResponseState);
    return <AgentDetailWorkspace agent={toAgentViewModel('agent-1', { id: 'agent-1', kind: 'worker', name: 'Worker', directory: '/project', agent_class_assigned_at: assignedAt, status: agentStatus })} group="Foundation" responses={responses} tasks={{}} directMessages={[]} peerThreads={[]} digestSettings={{}} digestBufferStats={{}} digestSentEvents={[]} sendCommand={() => true} onUnavailable={() => {}} />;
  }
  const view = render(<Provider store={store}><Panel /></Provider>);
  fireEvent.click(screen.getByRole('tab', { name: 'Agent Class' }));
  return { store, reconnect, leave: () => view.rerender(<Provider store={store}><div>Other agent</div></Provider>), returnToAgent: () => { view.rerender(<Provider store={store}><Panel /></Provider>); fireEvent.click(screen.getByRole('tab', { name: 'Agent Class' })); }, stop: () => view.rerender(<Provider store={store}><Panel agentStatus="stopped" /></Provider>), updateAssignment: () => view.rerender(<Provider store={store}><Panel assignedAt={10} /></Provider>) };
}
const select = () => screen.getByLabelText('Desired class for next launch');
const save = () => screen.getByRole('button', { name: 'Save assignment' });
const fact = (label: string) => within(screen.getByRole('region', { name: 'Agent Class assignment' })).getByText(label).nextElementSibling;
async function ready() { await waitFor(() => expect(save()).toBeEnabled()); }

describe('Agent Class assignment status and acknowledgement', () => {
  it('replaces earlier assignment replies with fresh status, follows saved selection and preserves edited drafts', async () => {
    const app = mount(); await ready();
    act(() => { app.store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'agent_class_assignment', status: status('a') })); });
    expect(select()).toHaveValue('a'); expect(fact('Desired')).toHaveTextContent('a');
    saved = status('b', { effective_class_id: 'b', effective_class_version: '2', next_launch_class_version: '2', pending_next_launch: false });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); await ready();
    expect(select()).toHaveValue('b'); expect(fact('Effective now')).toHaveTextContent('b'); expect(fact('Effective version')).toHaveTextContent('2'); expect(fact('Apply state')).toHaveTextContent('Current');
    fireEvent.change(select(), { target: { value: 'a' } }); act(() => { select().focus(); });
    saved = status(''); app.reconnect(); await ready();
    expect(select()).toHaveValue('a'); expect(select()).toHaveFocus(); expect(fact('Desired')).not.toHaveTextContent('b');
    act(() => { app.store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'agent_class_status', status: { ...status('b'), agent_id: 'other' } })); });
    expect(select()).toHaveValue('a'); expect(fact('Desired')).not.toHaveTextContent('b');
  });

  it('guards duplicate saves through reconnect, accepts only the matching acknowledgement and refreshes audit/status', async () => {
    let release!: (response: Response) => void;
    const send = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => { release = resolve; })); vi.stubGlobal('fetch', send);
    const app = mount(); await ready(); fireEvent.change(select(), { target: { value: 'a' } });
    fireEvent.click(save()); fireEvent.click(save()); expect(send).toHaveBeenCalledTimes(1); expect(save()).toBeDisabled(); expect(select()).toBeDisabled(); expect(screen.queryByText(/Waiting for a synchronized connection/)).not.toBeInTheDocument();
    expect(JSON.parse(send.mock.calls[0]?.[1]?.body as string)).toMatchObject({ cmd: 'agent_class_assign', agent_id: 'agent-1', class_id: 'a', base_dir: '/project' });
    app.reconnect(); expect(save()).toBeDisabled(); expect(send).toHaveBeenCalledTimes(1);
    saved = status('a'); const count = read.mock.calls.length;
    await act(async () => { release(new Response(JSON.stringify({ ok: true, data: { type: 'agent_class_assignment', status: saved } }))); await Promise.resolve(); }); await ready();
    expect(screen.getByText('Assignment saved. Applies at the next launch.')).toBeVisible(); expect(select()).toHaveValue('a'); expect(read.mock.calls.slice(count).map(([command]) => command.cmd)).toEqual(expect.arrayContaining(['agent_class_status', 'agent_class_audit']));
    saved = status('b'); fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); await ready(); expect(select()).toHaveValue('b');
  });

  it('retains selection on refusal, requires an explicit retry and acknowledges clearing', async () => {
    const send = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ ok: false, error: 'Assignment refused' })));
    vi.stubGlobal('fetch', send); const app = mount(); await ready(); fireEvent.change(select(), { target: { value: 'a' } }); fireEvent.click(save());
    await screen.findByText('Assignment refused'); await ready(); expect(select()).toHaveValue('a'); app.reconnect(); await ready(); expect(send).toHaveBeenCalledTimes(1); expect(select()).toHaveValue('a');
    send.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, data: { type: 'agent_class_assignment', status: status('a') } }))); saved = status('a'); fireEvent.click(save()); await ready(); expect(send).toHaveBeenCalledTimes(2);
    fireEvent.change(select(), { target: { value: '' } }); saved = status(''); send.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, data: { type: 'agent_class_assignment', status: saved } }))); fireEvent.click(save()); await ready();
    expect(JSON.parse(send.mock.calls[2]?.[1]?.body as string)).toMatchObject({ cmd: 'agent_class_clear', agent_id: 'agent-1' }); expect(select()).toHaveValue(''); expect(screen.getByText('Assignment cleared. The default applies at the next launch.')).toBeVisible();
  });

  it('does not report mismatched acknowledgements as success or replay an uncertain save', async () => {
    const send = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ ok: true, data: { type: 'agent_class_assignment', status: { ...status('a'), agent_id: 'other' } } }))); vi.stubGlobal('fetch', send);
    const app = mount(); await ready(); fireEvent.change(select(), { target: { value: 'a' } }); fireEvent.click(save());
    await screen.findByText(/outcome is unknown/i); expect(select()).toHaveValue('a'); expect(screen.queryByText('Assignment saved. Applies at the next launch.')).not.toBeInTheDocument();
    app.reconnect(); await ready(); expect(send).toHaveBeenCalledTimes(1);
  });
  it('refreshes when a live assignment changes without overwriting a draft', async () => {
    const app = mount(); await ready(); fireEvent.change(select(), { target: { value: 'a' } });
    const before = read.mock.calls.length; saved = status('b'); app.updateAssignment(); await ready();
    expect(read.mock.calls.slice(before).map(([command]) => command.cmd)).toContain('agent_class_status');
    expect(fact('Desired')).toHaveTextContent('b'); expect(select()).toHaveValue('a');
  });

  it('bounds a lost response and never resends it on reconnect', async () => {
    const send = vi.fn<typeof fetch>((_url, options) => new Promise((_resolve, reject) => { options?.signal?.addEventListener('abort', () => reject(new Error('Aborted'))); })); vi.stubGlobal('fetch', send);
    const app = mount(); await ready(); fireEvent.change(select(), { target: { value: 'a' } });
    vi.useFakeTimers(); fireEvent.click(save());
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); }); vi.useRealTimers();
    expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByRole('alert')).toHaveTextContent('timed out');
    await ready(); app.reconnect(); await ready(); expect(send).toHaveBeenCalledTimes(1); expect(select()).toHaveValue('a');
  });

  it('offers class relaunch only for stopped agents and explains running-session retention', async () => {
    saved = status('a'); const app = mount(); await ready();
    expect(screen.queryByRole('button', { name: 'Relaunch to apply' })).not.toBeInTheDocument();
    expect(screen.getByText('The running session keeps its current class. The desired class applies at the next launch or relaunch.')).toBeVisible();
    app.stop(); expect(screen.getByRole('button', { name: 'Relaunch to apply' })).toBeEnabled();
    expect(screen.getByText('Agent is stopped. Relaunch when you are ready to apply the desired class.')).toBeVisible();
  });

  it('keeps the pending guard and reviewed draft across unmount and return to the agent', async () => {
    let release!: (response: Response) => void;
    const send = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => { release = resolve; })); vi.stubGlobal('fetch', send);
    const app = mount(); await ready(); fireEvent.change(select(), { target: { value: 'a' } }); fireEvent.click(save());
    app.leave(); app.reconnect(); app.returnToAgent(); expect(save()).toBeDisabled(); expect(select()).toHaveValue('a'); expect(select()).toBeDisabled();
    fireEvent.click(save()); expect(send).toHaveBeenCalledTimes(1);
    app.leave(); await act(async () => { release(new Response(JSON.stringify({ ok: false, error: 'Assignment refused while away' }))); await Promise.resolve(); });
    app.returnToAgent(); await ready(); expect(screen.getByRole('alert')).toHaveTextContent('Assignment refused while away'); expect(select()).toHaveValue('a'); expect(send).toHaveBeenCalledTimes(1);
  });

});
