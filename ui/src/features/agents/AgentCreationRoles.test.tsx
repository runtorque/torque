import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import type * as httpModule from '../../protocol/http';
import { AgentCreateDialog } from './AgentCreateDialog';
vi.mock('../../protocol/http', async (original) => ({ ...await original<typeof httpModule>(), readCommand: vi.fn() }));
afterEach(() => vi.resetAllMocks());
it('owns creation roles and retains an unavailable selection without launching a substituted default', async () => {
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const reads: { command: TorqueCommand; resolve: (value: AuxiliaryFrame) => void }[] = [];
  vi.mocked(readCommand).mockImplementation((command) => {
    if (command.cmd === 'list_roles') return new Promise((resolve) => { reads.push({ command, resolve }); });
    return Promise.resolve(command.cmd === 'render_template' ? { type: 'template_rendered', group: 'A', name: command.name, config: {} } : { type: 'agent_classes', group: 'A', classes: [], issues: [] });
  });
  render(<Provider store={store}><AgentCreateDialog open group="A" agents={[]} onClose={vi.fn()} onCreated={vi.fn()} /></Provider>);
  expect(reads[0]?.command).toEqual({ cmd: 'list_roles', group: 'A' });
  expect(screen.queryByRole('option', { name: 'foreign' })).not.toBeInTheDocument();
  await act(async () => { reads[0]!.resolve({ type: 'roles', group: 'A', roles: [{ name: 'build', display_name: 'Project build' }, { name: 'build', display_name: 'Shadowed build', shadowed: true }] }); await Promise.resolve(); });
  fireEvent.change(screen.getByLabelText('Role / template'), { target: { value: 'build' } }); fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'My worker' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create worker' })).toBeEnabled());
  const model = screen.getByLabelText<HTMLInputElement>('Model'); fireEvent.change(model, { target: { value: 'local model' } }); model.focus(); model.setSelectionRange(1, 5);
  act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'roles', group: 'B', roles: [{ name: 'foreign' }] })); store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); });
  await act(async () => { reads[1]!.resolve({ type: 'roles', group: 'A', roles: [] }); await Promise.resolve(); });
  expect(screen.getByLabelText('Role / template')).toHaveValue('build'); expect(screen.getByRole('alert')).toHaveTextContent('no longer available'); expect(screen.getByRole('button', { name: 'Create worker' })).toBeDisabled();
  expect(model).toHaveFocus(); expect([model.selectionStart, model.selectionEnd]).toEqual([1, 5]); expect(model).toHaveValue('local model');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh creation roles' }));
  await act(async () => { reads[2]!.resolve({ type: 'roles', group: 'A', roles: [{ name: 'build' }] }); await Promise.resolve(); });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create worker' })).toBeEnabled());
  fireEvent.change(screen.getByRole('combobox', { name: 'Agent kind' }), { target: { value: 'terminal' } });
  const count = reads.length; act(() => { store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); }); expect(reads).toHaveLength(count);
});
it('pauses discovery during launch without asking to refresh an already reviewed role', async () => {
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  vi.mocked(readCommand).mockImplementation((command) => {
    if (command.cmd === 'add_worker') return new Promise(() => {});
    if (command.cmd === 'list_roles') return Promise.resolve({ type: 'roles', group: 'A', roles: [{ name: 'build' }] });
    return Promise.resolve(command.cmd === 'render_template' ? { type: 'template_rendered', group: 'A', name: command.name, config: {} } : { type: 'agent_classes', group: 'A', classes: [], issues: [] });
  });
  render(<Provider store={store}><AgentCreateDialog open group="A" agents={[]} onClose={vi.fn()} onCreated={vi.fn()} /></Provider>);
  await screen.findByRole('option', { name: 'build' }); fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Reviewed worker' } }); fireEvent.change(screen.getByLabelText('Role / template'), { target: { value: 'build' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create worker' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Create worker' })); expect(screen.getByRole('button', { name: 'Creating…' })).toBeDisabled(); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
