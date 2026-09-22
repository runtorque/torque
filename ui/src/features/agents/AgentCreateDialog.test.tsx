import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { AgentCreateDialog } from './AgentCreateDialog';
import { toAgentViewModel } from './model';
const response = (data: UnknownRecord) => ({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
const rendered = (name = '', config: UnknownRecord = {}) => response({ type: 'template_rendered', group: 'Foundation', name, config });
function setup(kind: 'worker' | 'architect' | 'engineer' | 'terminal' = 'worker') {
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const close = vi.fn(); const created = vi.fn(); const send = vi.fn<(command: TorqueCommand) => boolean>(() => true);
  const content = (empty = false) => <Provider store={store}><AgentCreateDialog open initialKind={kind} group="Foundation" catalog={empty ? { agentClasses: [], roles: [], templates: [], specializations: [] } : { agentClasses: [{ id: 'worker-class', name: 'Worker Class', base_kind: 'worker' }], roles: [{ name: 'build', display_name: 'Project build' }, { name: 'build', display_name: 'Shadowed global build' }, { name: 'review' }], templates: [], specializations: [] }} agents={[toAgentViewModel('arch', { kind: 'architect', name: 'Architect', group: 'Foundation' })]} onClose={close} onCreated={created} sendCommand={send} /></Provider>;
  const view = render(content());
  return { store, close, created, send, refreshCatalog: () => view.rerender(content(true)), ...view };
}
const ready = () => waitFor(() => { expect(screen.queryByText('Resolving launch settings…')).not.toBeInTheDocument(); expect(screen.queryByText('Loading project Agent Classes…')).not.toBeInTheDocument(); });
const classResponse = () => response({ type: 'agent_classes', group: 'Foundation', classes: [{ id: 'worker-class', name: 'Worker Class', base_kind: 'worker', launchable: true }], issues: [] });
const mockFetch = (handler: (url: string, options: RequestInit) => unknown) => vi.fn((url: string, options: RequestInit) => commandFrom(options).cmd === 'agent_class_list' ? Promise.resolve(classResponse()) : handler(url, options));
const commandFrom = (options: RequestInit) => JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand;
afterEach(() => vi.unstubAllGlobals());
describe('agent creation', () => {
  it.each(['worker', 'architect', 'engineer', 'terminal'] as const)('acknowledges %s creation before closing and selects the returned target', async (kind) => {
    const calls: TorqueCommand[] = [];
    vi.stubGlobal('fetch', mockFetch((_url: string, options: RequestInit) => {
      const command = commandFrom(options); calls.push(command);
      return Promise.resolve(command.cmd === 'render_template' ? rendered() : response({ type: kind === 'terminal' ? 'terminal_created' : 'ok', id: 'created', kind, name: 'New target', group: 'Foundation', parent_id: '' }));
    }));
    const { close, created } = setup(kind); await ready();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: '  New target  ' } }); fireEvent.click(screen.getByRole('button', { name: `Create ${kind}` }));
    await waitFor(() => expect(close).toHaveBeenCalledOnce()); expect(created).toHaveBeenCalledWith('created');
    expect(calls.find((command) => command.cmd !== 'render_template')).toMatchObject({ cmd: `add_${kind}`, group: 'Foundation', name: 'New target', idempotency_key: expect.any(String) as unknown });
  });
  it('guards pending dismissal and repeats, retains refused drafts and reuses the same retry identity', async () => {
    const calls: TorqueCommand[] = []; let release: (value: UnknownRecord) => void = () => { throw new Error('not pending'); };
    vi.stubGlobal('fetch', mockFetch((_url: string, options: RequestInit) => {
      calls.push(commandFrom(options)); return new Promise((resolve) => { release = (value) => resolve({ ok: true, json: () => Promise.resolve(value) }); });
    }));
    const { close, created } = setup('engineer'); fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Draft' } }); fireEvent.change(screen.getByLabelText('Custom instructions'), { target: { value: 'Keep this' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create engineer' }));
    expect(screen.getByRole('button', { name: 'Creating…' })).toBeDisabled(); expect(screen.getByLabelText('Name')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' })); fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' }); fireEvent.submit(screen.getByLabelText('Name').closest('form')!);
    expect(close).not.toHaveBeenCalled(); expect(calls).toHaveLength(1);
    await act(async () => { release({ ok: false, error: 'Duplicate name' }); await Promise.resolve(); });
    expect(await screen.findByRole('alert')).toHaveTextContent('Duplicate name'); expect(screen.getByLabelText('Custom instructions')).toHaveValue('Keep this');
    fireEvent.click(screen.getByRole('button', { name: 'Create engineer' })); expect(calls[1]).toEqual(calls[0]);
    await act(async () => { release({ ok: true, data: { id: 'eng', name: 'Draft', kind: 'engineer' } }); await Promise.resolve(); });
    expect(close).toHaveBeenCalledOnce(); expect(created).toHaveBeenCalledWith('eng');
  });
  it('rejects mismatched acknowledgement and gives changed drafts a different retry key', async () => {
    const calls: TorqueCommand[] = [];
    vi.stubGlobal('fetch', mockFetch((_url: string, options: RequestInit) => { calls.push(commandFrom(options)); return Promise.resolve(response({ id: 'wrong', name: 'Wrong name', kind: 'worker' })); }));
    const { close } = setup('architect'); fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Original' } }); fireEvent.click(screen.getByRole('button', { name: 'Create architect' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not confirm'); expect(close).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Changed' } }); fireEvent.click(screen.getByRole('button', { name: 'Create architect' }));
    await waitFor(() => expect(calls).toHaveLength(2)); expect(calls[1]!.idempotency_key).not.toBe(calls[0]!.idempotency_key);
  });
  it('requires the nested Agent Class creation acknowledgement', async () => {
    let last: TorqueCommand | undefined;
    vi.stubGlobal('fetch', mockFetch((_url: string, options: RequestInit) => { const command = commandFrom(options); if (command.cmd !== 'render_template') last = command; return Promise.resolve(command.cmd === 'render_template' ? rendered() : response({ type: 'agent_class_launch', base_kind: 'worker', agent: { id: 'class-agent', name: 'From class', kind: 'worker' } })); }));
    const { close, created } = setup(); await ready(); fireEvent.change(screen.getByLabelText('Agent Class'), { target: { value: 'worker-class' } }); fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'From class' } }); fireEvent.click(screen.getByRole('button', { name: 'Create worker' }));
    await waitFor(() => expect(close).toHaveBeenCalledOnce()); expect(last).toMatchObject({ cmd: 'create_agent_from_class', class_id: 'worker-class', kind: 'worker' }); expect(created).toHaveBeenCalledWith('class-agent');
  });
  it('acknowledges a pending hire without claiming an Engineer already exists', async () => {
    let last: TorqueCommand | undefined;
    vi.stubGlobal('fetch', mockFetch((_url: string, options: RequestInit) => { last = commandFrom(options); return Promise.resolve(response({ hire_id: 'hire-1', status: 'pending' })); }));
    const { close, created } = setup('engineer'); fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Requested' } }); fireEvent.change(screen.getByLabelText('Hiring Architect'), { target: { value: 'arch' } }); fireEvent.change(screen.getByLabelText('Specializations'), { target: { value: 'frontend, ui-ux' } });
    expect(screen.getByText('The Engineer is created after approval in Planning.')).toBeVisible(); fireEvent.click(screen.getByRole('button', { name: 'Request hire' }));
    await waitFor(() => expect(close).toHaveBeenCalledOnce()); expect(last).toMatchObject({ cmd: 'architect_engineer_hire', architect_id: 'arch', specializations: ['frontend', 'ui-ux'] }); expect(created).not.toHaveBeenCalled();
  });
  it('loads resolved template fields, preserving explicit overrides across selection and reconnect', async () => {
    const calls: TorqueCommand[] = [];
    vi.stubGlobal('fetch', mockFetch((_url: string, options: RequestInit) => { const command = commandFrom(options); calls.push(command); return Promise.resolve(rendered(String(command.name), { provider: 'generic', command: '/bin/cat', model: command.name || 'group-model', shell: 'bash', env_vars: { MODE: 'qa' }, worktree: true, worktree_base_branch: 'main', worktree_merge_squash: false })); }));
    const { store, send, refreshCatalog } = setup(); await ready();
    expect(screen.getByLabelText('Provider')).toHaveValue('generic'); expect(screen.getByLabelText('Environment variables')).toHaveValue('MODE=qa'); expect(screen.getByLabelText('Create an isolated worktree')).toBeChecked(); expect(screen.getByLabelText('Squash merge')).not.toBeChecked();
    const model = screen.getByLabelText<HTMLInputElement>('Model'); fireEvent.change(model, { target: { value: 'explicit-model' } }); model.focus(); model.setSelectionRange(1, 4);
    fireEvent.change(screen.getByLabelText('Role / template'), { target: { value: 'build' } }); await ready(); expect(model).toHaveValue('explicit-model'); expect(model).toHaveFocus(); expect([model.selectionStart, model.selectionEnd]).toEqual([1, 4]);
    expect(screen.getByRole('option', { name: 'Project build' })).toBeInTheDocument(); expect(screen.queryByRole('option', { name: 'Shadowed global build' })).not.toBeInTheDocument();
    refreshCatalog(); expect(screen.getByLabelText('Role / template')).toHaveValue('build');
    act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await ready(); expect(model).toHaveValue('explicit-model'); expect(calls).toHaveLength(3); expect(send.mock.calls.filter(([command]) => command.cmd === 'list_roles')).toHaveLength(2);
  });
  it('ignores aborted template replies, blocks failed resolution and retries in place', async () => {
    const reads: { command: TorqueCommand; signal: AbortSignal; resolve: (response: ReturnType<typeof rendered>) => void }[] = [];
    vi.stubGlobal('fetch', mockFetch((_url: string, options: RequestInit) => new Promise((resolve) => reads.push({ command: commandFrom(options), signal: options.signal!, resolve }))));
    const { unmount } = setup(); fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Draft' } });
    fireEvent.change(screen.getByLabelText('Role / template'), { target: { value: 'review' } }); expect(reads[0]!.signal.aborted).toBe(true);
    await act(async () => { reads[1]!.resolve(rendered('wrong')); await Promise.resolve(); });
    expect(await screen.findByRole('alert')).toHaveTextContent('did not match'); expect(screen.getByRole('button', { name: 'Create worker' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry launch settings' }));
    await act(async () => { reads[2]!.resolve(rendered('review', { model: 'review-model' })); await Promise.resolve(); });
    await act(async () => { reads[0]!.resolve(rendered('', { model: 'stale' })); await Promise.resolve(); });
    expect(screen.getByLabelText('Model')).toHaveValue('review-model'); expect(screen.getByLabelText('Name')).toHaveValue('Draft'); expect(screen.getByRole('button', { name: 'Create worker' })).toBeEnabled(); unmount(); expect(reads[2]!.signal.aborted).toBe(true);
  });
});
