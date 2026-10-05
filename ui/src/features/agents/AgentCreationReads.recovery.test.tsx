import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import type * as httpModule from '../../protocol/http';
import { AgentCreateDialog } from './AgentCreateDialog';
vi.mock('../../protocol/http', async (original) => ({ ...await original<typeof httpModule>(), readCommand: vi.fn() }));
const classes = { type: 'agent_classes', group: 'Foundation', classes: [{ id: 'local', base_kind: 'worker', name: 'Local worker', launchable: true }], issues: [] } as AuxiliaryFrame;
const launch = (model = 'accepted') => ({ type: 'template_rendered', group: 'Foundation', name: '', config: { model } }) as AuxiliaryFrame;
const tick = async (time = 0) => act(async () => { await vi.advanceTimersByTimeAsync(time); });
function setup(connected = true) {
  const store = createAppStore(); if (connected) store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const reads: { command: TorqueCommand; signal: AbortSignal; resolve: (frame: AuxiliaryFrame) => void }[] = [];
  vi.mocked(readCommand).mockImplementation((command, signal) => command.cmd === 'list_roles' ? Promise.resolve({ type: 'roles', group: 'Foundation', roles: [] }) : new Promise((resolve) => { reads.push({ command, signal, resolve }); }));
  const view = render(<Provider store={store}><AgentCreateDialog open group="Foundation" agents={[]} onClose={vi.fn()} onCreated={vi.fn()} /></Provider>);
  return { store, reads, ...view };
}
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });
describe('agent creation read recovery', () => {
  it('bounds template observation and retries without losing overrides, focus or caret', async () => {
    vi.useFakeTimers(); const { reads, unmount } = setup();
    await act(async () => { reads.find((r) => r.command.cmd === 'agent_class_list')!.resolve(classes); await Promise.resolve(); });
    const old = reads.find((r) => r.command.cmd === 'render_template')!;
    const model = screen.getByLabelText<HTMLInputElement>('Model'); fireEvent.change(model, { target: { value: 'draft model' } }); model.focus(); model.setSelectionRange(2, 5);
    await tick(15_000); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(old.signal.aborted).toBe(true);
    expect(model).toHaveFocus(); expect([model.selectionStart, model.selectionEnd]).toEqual([2, 5]); expect(model).toHaveValue('draft model');
    fireEvent.click(screen.getByRole('button', { name: 'Retry launch settings' }));
    await act(async () => { reads.filter((r) => r.command.cmd === 'render_template').at(-1)!.resolve(launch('new default')); await Promise.resolve(); });
    await act(async () => { old.resolve(launch('late default')); await Promise.resolve(); });
    expect(model).toHaveValue('draft model'); expect(screen.queryByText('Resolving launch settings…')).not.toBeInTheDocument();
    act(() => { fireEvent.click(screen.getByRole('button', { name: 'Refresh Agent Classes' })); }); unmount(); expect(reads.at(-1)!.signal.aborted).toBe(true);
  });
  it('bounds a reconnect class refresh and retains the selected accepted class through retry', async () => {
    vi.useFakeTimers(); const { store, reads } = setup();
    await act(async () => { reads.find((r) => r.command.cmd === 'agent_class_list')!.resolve(classes); reads.find((r) => r.command.cmd === 'render_template')!.resolve(launch()); await Promise.resolve(); });
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Draft worker' } }); fireEvent.change(screen.getByLabelText('Agent Class'), { target: { value: 'local' } });
    act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); });
    await act(async () => { reads.filter((r) => r.command.cmd === 'render_template').at(-1)!.resolve(launch()); await Promise.resolve(); });
    const old = reads.filter((r) => r.command.cmd === 'agent_class_list').at(-1)!;
    await tick(15_000); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(old.signal.aborted).toBe(true);
    expect(screen.getByLabelText('Agent Class')).toHaveValue('local'); expect(screen.getByRole('button', { name: 'Create worker' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry Agent Classes' }));
    await act(async () => { reads.filter((r) => r.command.cmd === 'agent_class_list').at(-1)!.resolve(classes); await Promise.resolve(); });
    await act(async () => { old.resolve({ ...classes, classes: [] }); await Promise.resolve(); });
    expect(screen.getByLabelText('Agent Class')).toHaveValue('local'); expect(screen.getByRole('button', { name: 'Create worker' })).toBeEnabled();
  });
  it('waits for connection before resolving launch defaults', async () => {
    vi.useFakeTimers(); const { store, reads } = setup(false); expect(reads).toHaveLength(0);
    act(() => { store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); }); await tick();
    expect(reads.map((r) => r.command.cmd).sort()).toEqual(['agent_class_list', 'render_template']);
  });
});
