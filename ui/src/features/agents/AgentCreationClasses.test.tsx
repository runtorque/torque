import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { AgentCreateDialog } from './AgentCreateDialog';
import { creationClassDisabledReason } from './useCreationClasses';
const response = (data: UnknownRecord) => ({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
const available = { id: 'local', base_kind: 'engineer', display_name: 'Local Engineer', launchable: true, version: '2' };
const catalogFrame = (classes: UnknownRecord[] = [available], group = 'Foundation', issues: UnknownRecord[] = []) => ({ type: 'agent_classes', group, base_dir: '/project', classes, issues });
function setup() {
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const reads: { command: TorqueCommand; signal: AbortSignal; resolve: (data: UnknownRecord) => void }[] = [];
  const writes: TorqueCommand[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
    const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand;
    if (command.cmd === 'agent_class_list') return new Promise((resolve) => reads.push({ command, signal: options.signal!, resolve: (data) => resolve(response(data)) }));
    writes.push(command); return Promise.resolve(response({ type: 'error', message: 'Creation refused' }));
  }));
  const view = render(<Provider store={store}><AgentCreateDialog open initialKind="engineer" group="Foundation" catalog={{ agentClasses: [{ ...available, id: 'foreign', display_name: 'Wrong project' }], roles: [], templates: [], specializations: [] }} agents={[]} sendCommand={() => true} onClose={vi.fn()} onCreated={vi.fn()} /></Provider>);
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Draft' } });
  const reply = async (index: number, frame: UnknownRecord) => { await act(async () => { reads[index]!.resolve(frame); await Promise.resolve(); }); };
  const reconnect = () => act(() => { store.dispatch(connectionActions.connected({ at: Date.now(), reconnect: true })); });
  return { reads, writes, reply, reconnect, ...view };
}
afterEach(() => vi.unstubAllGlobals());
describe('project Agent Class creation discovery', () => {
  it('owns its correlated catalog, ignores foreign projection data and cancels hidden reads', async () => {
    const { reads, reply, reconnect, unmount } = setup();
    expect(reads[0]!.command).toEqual({ cmd: 'agent_class_list', group: 'Foundation' });
    expect(screen.queryByRole('option', { name: /Wrong project/ })).not.toBeInTheDocument();
    await reply(0, catalogFrame([available], 'Other group'));
    expect(await screen.findByRole('alert')).toHaveTextContent('did not match');
    expect(screen.queryByRole('option', { name: /Local Engineer/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry Agent Classes' }));
    await reply(1, catalogFrame());
    expect(screen.getByRole('option', { name: /Local Engineer/ })).toBeEnabled();
    reconnect(); expect(reads).toHaveLength(3); unmount(); expect(reads[2]!.signal.aborted).toBe(true);
  });
  it('retains selection and caret through reconnect, rejects archived classes and preserves default launch', async () => {
    const { reply, reconnect, writes } = setup(); await reply(0, catalogFrame());
    fireEvent.change(screen.getByLabelText('Agent Class'), { target: { value: 'local' } });
    const name = screen.getByLabelText<HTMLInputElement>('Name'); name.focus(); name.setSelectionRange(1, 3);
    reconnect();
    expect(screen.getByRole('button', { name: 'Create engineer' })).toBeDisabled(); expect(screen.getByLabelText('Agent Class')).toHaveValue('local');
    await reply(1, catalogFrame([{ ...available, archived: true }]));
    expect(await screen.findByRole('alert')).toHaveTextContent('Archived or disabled');
    expect(name).toHaveFocus(); expect([name.selectionStart, name.selectionEnd]).toEqual([1, 3]);
    fireEvent.submit(name.closest('form')!); expect(writes).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('Agent Class'), { target: { value: '' } });
    expect(screen.getByRole('button', { name: 'Create engineer' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Create engineer' }));
    await waitFor(() => expect(writes).toHaveLength(1)); expect(writes[0]!.cmd).toBe('add_engineer');
  });
  it('retains failed and missing selections until retry or an explicit new choice', async () => {
    const { reply, reconnect, writes } = setup(); await reply(0, catalogFrame());
    fireEvent.change(screen.getByLabelText('Agent Class'), { target: { value: 'local' } });
    reconnect(); await reply(1, { type: 'error', message: 'Read refused' });
    expect(await screen.findByRole('alert')).toHaveTextContent('Read refused'); expect(screen.getByLabelText('Agent Class')).toHaveValue('local'); expect(screen.getByRole('button', { name: 'Create engineer' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry Agent Classes' })); await reply(2, catalogFrame([]));
    expect(await screen.findByRole('alert')).toHaveTextContent('no longer available'); expect(screen.getByLabelText('Agent Class')).toHaveValue('local');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh Agent Classes' })); await reply(3, catalogFrame());
    fireEvent.click(screen.getByRole('button', { name: 'Create engineer' }));
    await waitFor(() => expect(writes).toHaveLength(1)); expect(writes[0]).toMatchObject({ cmd: 'create_agent_from_class', class_id: 'local', group: 'Foundation' });
  });
  it('ignores aborted responses and displays registry errors that prohibit class launch', async () => {
    const { reads, reply, reconnect } = setup(); reconnect(); expect(reads[0]!.signal.aborted).toBe(true);
    await reply(1, catalogFrame()); await reply(0, catalogFrame([{ ...available, display_name: 'Stale' }]));
    expect(screen.queryByRole('option', { name: /Stale/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Agent Class'), { target: { value: 'local' } }); reconnect();
    await reply(2, catalogFrame([available], 'Foundation', [{ severity: 'error', message: 'Duplicate class ID', path: '/project/bad.yaml' }]));
    expect(await screen.findByRole('alert')).toHaveTextContent('catalog errors'); expect(screen.getByText('Duplicate class ID')).toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Create engineer' })).toBeDisabled();
  });
  it.each([{ disabled: true }, { metadata: { archived_at: 'today' } }, { launchable: false }, { status: 'invalid' }, { base_kind: 'worker' }])('explains unavailable class %j', (extra) => {
    expect(creationClassDisabledReason({ ...available, ...extra }, 'engineer')).not.toBe('');
  });
});
