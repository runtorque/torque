import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { useLayoutEffect } from 'react';
import { SettingsNavigationProvider } from '../../app/SettingsNavigationGuard';
import { useSettingsNavigation } from '../../app/settingsNavigation';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { CommandResponseError, readCommand } from '../../protocol/http';
import type * as httpModule from '../../protocol/http';
import { AgentCreateDialog } from './AgentCreateDialog';
import { toAgentViewModel } from './model';
vi.mock('../../protocol/http', async (original) => ({ ...await original<typeof httpModule>(), readCommand: vi.fn() }));
type Write = { command: TorqueCommand; signal: AbortSignal; resolve: (frame: AuxiliaryFrame) => void; reject: (error: Error) => void };
const tick = async (time = 0) => act(async () => { await vi.advanceTimersByTimeAsync(time); });
function setup(kind: 'terminal' | 'engineer' = 'terminal') {
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const writes: Write[] = [];
  vi.mocked(readCommand).mockImplementation((command, signal) => {
    if (command.cmd === 'agent_class_list') return Promise.resolve({ type: 'agent_classes', group: 'QA', classes: [], issues: [] });
    if (command.cmd === 'list_specializations') return Promise.resolve({ type: 'specializations', group: 'QA', specializations: [] });
    return new Promise((resolve, reject) => writes.push({ command, signal, resolve, reject }));
  });
  const close = vi.fn(); const created = vi.fn(); const navigated = vi.fn();
  let navigate = () => {}; let visible = true;
  function Probe() { const navigation = useSettingsNavigation(); useLayoutEffect(() => { navigate = () => navigation.request(navigated); }, [navigation]); return null; }
  const content = () => <Provider store={store}><SettingsNavigationProvider><Probe />{visible ? <AgentCreateDialog open initialKind={kind} group="QA" agents={[toAgentViewModel('arch', { kind: 'architect', name: 'Architect', group: 'QA' })]} onClose={close} onCreated={created} /> : null}</SettingsNavigationProvider></Provider>;
  const view = render(content());
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Reviewed' } });
  return { store, writes, close, created, navigated, navigate: () => act(() => navigate()), hideOwner: () => { visible = false; view.rerender(content()); }, ...view };
}
const submit = () => fireEvent.click(screen.getByRole('button', { name: 'Create terminal' }));
const success = { type: 'terminal_created', id: 'terminal-1', name: 'Reviewed', kind: 'terminal', parent_id: '' } as AuxiliaryFrame;
const partial = (write: Write, changes = {}) => ({ type: 'creation_incomplete', command: write.command.cmd, idempotency_key: write.command.idempotency_key, requested_name: write.command.name, message: 'PTY startup failed', target: { type: 'agent', id: 'partial-1', name: 'Reviewed', kind: 'terminal' }, ...changes }) as AuxiliaryFrame;
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });
describe('agent creation delivery recovery', () => {
  it('bounds delivery, freezes the request through reconnect, and ignores its late acknowledgement', async () => {
    vi.useFakeTimers(); const { writes, store, close, created } = setup(); submit();
    await tick(30_000); expect(writes[0]!.signal.aborted).toBe(true);
    expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.getByLabelText('Name')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' })); fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(close).not.toHaveBeenCalled();
    act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await tick();
    expect(writes).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Retry same creation' })); expect(writes[1]!.command).toEqual(writes[0]!.command);
    await act(async () => { writes[0]!.resolve(success); await Promise.resolve(); }); expect(created).not.toHaveBeenCalled();
    await act(async () => { writes[1]!.resolve(success); await Promise.resolve(); }); expect(created).toHaveBeenCalledExactlyOnceWith('terminal-1'); expect(close).toHaveBeenCalledOnce();
  });
  it('releases editable fields only after a verified refusal and uses a new key for the corrected draft', async () => {
    vi.useFakeTimers(); const { writes } = setup(); submit();
    await act(async () => { writes[0]!.reject(new CommandResponseError('Name refused', 200, true)); await Promise.resolve(); });
    expect(screen.getByLabelText('Name')).toBeEnabled(); expect(screen.queryByRole('button', { name: 'Retry same creation' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Corrected' } }); submit();
    expect(writes[1]!.command.name).toBe('Corrected'); expect(writes[1]!.command.idempotency_key).not.toBe(writes[0]!.command.idempotency_key);
  });
  it.each([new Error('Connection dropped'), new CommandResponseError('Unknown failure', 500, true), new CommandResponseError('Key conflict', 409, true)])('retains frozen intent after %s', async (error) => {
    vi.useFakeTimers(); const { writes } = setup(); submit();
    await act(async () => { writes[0]!.reject(error); await Promise.resolve(); }); expect(screen.getByLabelText('Name')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry same creation' })); expect(writes[1]!.command).toEqual(writes[0]!.command);
  });
  it('offers deliberate inspection of the exact incomplete target without claiming successful launch', async () => {
    vi.useFakeTimers(); const { writes, created, close } = setup(); submit();
    await act(async () => { writes[0]!.resolve(partial(writes[0]!)); await Promise.resolve(); });
    expect(screen.getByRole('region', { name: 'Incomplete launch' })).toHaveTextContent('partial-1'); expect(created).not.toHaveBeenCalled(); expect(close).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Retry same creation' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect created target' })); expect(created).toHaveBeenCalledExactlyOnceWith('partial-1'); expect(close).toHaveBeenCalledOnce(); expect(writes).toHaveLength(1);
  });
  it('rejects a partial outcome belonging to another request', async () => {
    vi.useFakeTimers(); const { writes, created } = setup(); submit();
    await act(async () => { writes[0]!.resolve(partial(writes[0]!, { idempotency_key: 'wrong' })); await Promise.resolve(); });
    expect(screen.getByRole('alert')).toHaveTextContent('Could not confirm'); expect(created).not.toHaveBeenCalled(); expect(screen.getByRole('button', { name: 'Retry same creation' })).toBeEnabled();
  });
  it('retains a saved hire without selecting a nonexistent engineer', async () => {
    vi.useFakeTimers(); const { writes, created, close } = setup('engineer'); await tick();
    fireEvent.change(screen.getByLabelText('Hiring Architect'), { target: { value: 'arch' } }); fireEvent.click(screen.getByRole('button', { name: 'Request hire' }));
    const write = writes.find((item) => item.command.cmd === 'architect_engineer_hire')!;
    await act(async () => { write.resolve(partial(write, { target: { type: 'pending_hire', id: 'hire-1', name: 'Reviewed', architect_id: 'arch', status: 'pending' } })); await Promise.resolve(); });
    expect(screen.getByRole('region', { name: 'Incomplete launch' })).toHaveTextContent('hire-1');
    fireEvent.click(screen.getByRole('button', { name: 'Keep saved hire request' })); expect(close).toHaveBeenCalledOnce(); expect(created).not.toHaveBeenCalled();
  });
  it('cancels observation on unmount and ignores a later result', async () => {
    vi.useFakeTimers(); const { writes, close, created, unmount } = setup(); submit(); unmount();
    expect(writes[0]!.signal.aborted).toBe(true); await act(async () => { writes[0]!.resolve(success); await Promise.resolve(); }); expect(close).not.toHaveBeenCalled(); expect(created).not.toHaveBeenCalled();
  });
});

it('protects pending and uncertain creation from navigation until its exact outcome is recovered', async () => {
  vi.useFakeTimers(); const { writes, navigate, navigated } = setup(); submit(); navigate();
  expect(screen.getByRole('dialog', { name: 'Creation needs recovery' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Continue navigation' })).not.toBeInTheDocument(); expect(navigated).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Return to creation' })); await tick(30_000);
  navigate(); expect(screen.getByRole('dialog', { name: 'Creation needs recovery' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Return to creation' })); fireEvent.click(screen.getByRole('button', { name: 'Retry same creation' }));
  await act(async () => { writes[1]!.reject(new CommandResponseError('Verified refusal', 200, true)); await Promise.resolve(); });
  navigate(); expect(navigated).toHaveBeenCalledOnce(); expect(writes[1]!.command).toEqual(writes[0]!.command);
});
it('clears a queued navigation guard when the recovered creation owner closes', async () => {
  vi.useFakeTimers(); const { writes, navigate, navigated, hideOwner, created } = setup(); submit(); navigate();
  await act(async () => { writes[0]!.resolve(success); await Promise.resolve(); }); expect(created).toHaveBeenCalledWith('terminal-1');
  hideOwner(); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(navigated).not.toHaveBeenCalled();
  navigate(); expect(navigated).toHaveBeenCalledOnce();
});
