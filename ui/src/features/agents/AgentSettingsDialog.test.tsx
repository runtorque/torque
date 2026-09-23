import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { AgentSettingsDialog } from './AgentSettingsDialog';
import { toAgentViewModel } from './model';
const agent = { id: 'eng', name: 'Original', kind: 'engineer', group: 'Foundation' };
const metadata = () => ({ model: { value: 'override', origin: 'per-agent', inherited: { value: 'group-model', origin: 'group' } }, provider: { value: 'generic', origin: 'group' }, heartbeat_interval: { value: 300, origin: 'group' } });
const response = (data: UnknownRecord) => ({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
function setup(kind = 'engineer') {
  const target = { ...agent, kind };
  const store = createAppStore();
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, agents: { eng: target }, resolved_agent_settings: { eng: metadata() } }));
  store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const close = vi.fn(); const view = render(<Provider store={store}><AgentSettingsDialog target={toAgentViewModel('eng', target)} onClose={close} /></Provider>);
  return { store, close, ...view };
}
function requests() {
  let current: UnknownRecord = metadata(); let error = ''; const calls: TorqueCommand[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
    const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command);
    return Promise.resolve(error ? { ok: true, json: () => Promise.resolve({ ok: false, error }) } : response({ type: 'agent_settings', agent_id: 'eng', resolved: current, settings: {} }));
  }));
  return { calls, replace: (value: UnknownRecord) => { current = value; }, fail: (value: string) => { error = value; } };
}
const ready = () => waitFor(() => expect(screen.queryByText('Refreshing agent settings…')).not.toBeInTheDocument());
afterEach(() => vi.unstubAllGlobals());
describe('Agent settings lifecycle', () => {
  it('reconciles defaults and reconnect reads without replacing drafts, focus or reset intent', async () => {
    const api = requests(); const { store, unmount } = setup(); await ready();
    const provider = screen.getByLabelText<HTMLInputElement>('Provider');
    fireEvent.change(provider, { target: { value: 'local-draft' } }); provider.focus(); provider.setSelectionRange(2, 5);
    fireEvent.click(within(screen.getByLabelText('Model').parentElement!).getByRole('button', { name: 'Use inherited' }));
    api.replace({ ...metadata(), model: { value: 'external', origin: 'per-agent', inherited: { value: 'updated-group', origin: 'group' } }, heartbeat_interval: { value: 0, origin: 'group' } });
    act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await ready();
    expect(provider).toHaveValue('local-draft'); expect(provider).toHaveFocus(); expect([provider.selectionStart, provider.selectionEnd]).toEqual([2, 5]);
    expect(screen.getByLabelText('Model')).toHaveValue('updated-group'); expect(screen.getByLabelText('Heartbeat interval (seconds)')).toHaveValue(0);
    api.replace({ ...metadata(), heartbeat_interval: { value: 600, origin: 'group' } });
    act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'group_settings', group: 'Foundation', settings: { agent_model: 'changed' } })); }); await ready();
    expect(screen.getByLabelText('Heartbeat interval (seconds)')).toHaveValue(600);
    expect(api.calls).toHaveLength(3);
    await act(async () => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'agent_digest_update', agent_id: 'eng', buffered_events: 9, last_sent_at: 12345 }] })); await Promise.resolve(); });
    expect(api.calls).toHaveLength(3); unmount();
    act(() => { store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); }); expect(api.calls).toHaveLength(3);
  });
  it('requires confirmation only for dirty dismissal and retains edits on Keep editing', async () => {
    requests(); const { close } = setup(); await ready();
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Draft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' })); expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Discard agent settings changes?' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' })); expect(screen.getByLabelText('Name')).toHaveValue('Draft');
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' })); fireEvent.click(screen.getByRole('button', { name: 'Discard changes' })); expect(close).toHaveBeenCalledOnce();
  });
  it('retains failed-read drafts and retries without permitting a write before initial hydration', async () => {
    const api = requests(); api.fail('Read refused'); const { close } = setup(); await ready();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Draft' } }); expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('Read refused'); api.fail(''); fireEvent.click(screen.getByRole('button', { name: 'Retry settings' })); await ready();
    expect(screen.getByLabelText('Name')).toHaveValue('Draft'); expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled(); expect(close).not.toHaveBeenCalled();
  });
  it('ignores an aborted old read when a newer reconnect response arrives', async () => {
    const pending: { signal: AbortSignal; resolve: (value: ReturnType<typeof response>) => void }[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((resolve) => pending.push({ signal: options.signal!, resolve }))));
    const { store } = setup(); expect(pending).toHaveLength(1);
    act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(pending[0]!.signal.aborted).toBe(true);
    await act(async () => { pending[1]!.resolve(response({ type: 'agent_settings', agent_id: 'eng', resolved: { model: { value: 'fresh', origin: 'group' } } })); await Promise.resolve(); });
    await act(async () => { pending[0]!.resolve(response({ type: 'agent_settings', agent_id: 'eng', resolved: { model: { value: 'stale', origin: 'group' } } })); await Promise.resolve(); });
    expect(screen.getByLabelText('Model')).toHaveValue('fresh');
  });
  it('does not replay acknowledged launch edits after digest refusal and an external launch change', async () => {
    const calls: TorqueCommand[] = []; let resolved: UnknownRecord = metadata(); let refuse = true;
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand;
      if (command.cmd !== 'get_agent_settings') calls.push(command);
      if (command.cmd === 'update_agent_digest_settings' && refuse) return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: false, error: 'Digest refused' }) });
      if (command.cmd === 'update_agent_settings') resolved = { ...resolved, model: { value: 'saved-model', origin: 'per-agent' } };
      if (command.cmd === 'update_agent_digest_settings') resolved = { ...resolved, heartbeat_interval: { value: 0, origin: 'per-agent' } };
      return Promise.resolve(response({ type: 'agent_settings', agent_id: 'eng', resolved, settings: {} }));
    }));
    const { store, close } = setup(); await ready();
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'saved-model' } }); fireEvent.change(screen.getByLabelText('Heartbeat interval (seconds)'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })); expect(await screen.findByRole('alert')).toHaveTextContent('Some changes were saved. Digest refused'); await ready();
    resolved = { ...resolved, model: { value: 'external-model', origin: 'per-agent' } };
    act(() => { store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); }); await ready();
    expect(screen.getByLabelText('Model')).toHaveValue('external-model'); expect(screen.getByLabelText('Heartbeat interval (seconds)')).toHaveValue(0);
    refuse = false; fireEvent.click(screen.getByRole('button', { name: 'Save settings' })); await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(calls.map((command) => command.cmd)).toEqual(['update_agent_settings', 'update_agent_digest_settings', 'update_agent_digest_settings']);
  });
  it('retains acknowledged digest and specialization scopes when only relaunch fails', async () => {
    const calls: TorqueCommand[] = []; let refuse = true; let resolved: UnknownRecord = metadata();
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand;
      if (command.cmd !== 'get_agent_settings') calls.push(command);
      if (command.cmd === 'relaunch_agent') return Promise.resolve({ ok: true, json: () => Promise.resolve(refuse ? { ok: false, error: 'Relaunch refused' } : { ok: true, data: { type: 'ok' } }) });
      if (command.cmd === 'set_engineer_specializations') {
        resolved = { ...resolved, engineer_specializations: { value: ['frontend'], origin: 'per-agent' } };
        return Promise.resolve(response({ type: 'engineer_specializations', engineer_id: 'eng', specializations: ['frontend'] }));
      }
      if (command.cmd === 'update_agent_digest_settings') resolved = { ...resolved, heartbeat_interval: { value: 0, origin: 'per-agent' } };
      return Promise.resolve(response({ type: 'agent_settings', agent_id: 'eng', resolved, settings: {} }));
    }));
    const { close } = setup(); await ready();
    fireEvent.change(screen.getByLabelText('Heartbeat interval (seconds)'), { target: { value: '0' } });
    fireEvent.change(screen.getByLabelText('Ordered specialization slugs'), { target: { value: 'frontend' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Relaunch after saving launch-bound changes' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Some changes were saved. Relaunch refused'); await ready();
    expect(screen.getByRole('checkbox')).toBeChecked(); refuse = false;
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })); await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(calls.map((command) => command.cmd)).toEqual(['update_agent_digest_settings', 'set_engineer_specializations', 'relaunch_agent', 'relaunch_agent']);
  });

  it('uses Architect defaults without exposing Engineer-only fields', async () => {
    const api = requests(); const { store } = setup('architect'); await ready();
    expect(screen.queryByLabelText('Default worker concurrency')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Ordered specialization slugs')).not.toBeInTheDocument();
    api.replace({ ...metadata(), model: { value: 'architect-model', origin: 'group' } });
    act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'architect_settings_update', group: 'Foundation', architect_model: 'architect-model' }] })); }); await ready();
    expect(screen.getByLabelText('Model')).toHaveValue('architect-model'); expect(api.calls).toHaveLength(2);
    expect(api.calls.every((command) => command.agent_id === 'eng')).toBe(true);
  });

});
it('retains a selected notification preset through a refused save and acknowledges only its five fields', async () => {
  let refuse = true; const writes: TorqueCommand[] = [];
  const normal: UnknownRecord = { digest_verbosity: 'balanced', push_interval: 60, max_interval: 300, heartbeat_interval: 300, enabled_events: ['agent_started', 'task_dispatched', 'task_derived', 'task_health_alert'] };
  let resolved = { ...metadata(), ...Object.fromEntries(Object.entries(normal).map(([key, value]) => [key, { value, origin: 'group' }])) };
  vi.stubGlobal('fetch', vi.fn((_url, options: RequestInit) => {
    const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand;
    if (command.cmd === 'update_agent_digest_settings') {
      writes.push(command); if (refuse) return Promise.resolve(response({ type: 'error', message: 'Digest preset refused' }));
      resolved = { ...resolved, ...Object.fromEntries(Object.entries(command.settings as UnknownRecord).map(([key, value]) => [key, { value, origin: 'per-agent' }])) };
    }
    return Promise.resolve(response({ type: 'agent_settings', agent_id: 'eng', settings: {}, resolved }));
  }));
  const { close } = setup(); await ready(); const picker = screen.getByRole('combobox', { name: 'Engineer notification preset' }); expect(picker).toHaveValue('normal');
  fireEvent.change(picker, { target: { value: 'quiet' } }); fireEvent.click(screen.getByRole('button', { name: 'Save settings' })); expect(await screen.findByRole('alert')).toHaveTextContent('Digest preset refused'); expect(picker).toHaveValue('quiet');
  await ready(); refuse = false; fireEvent.click(screen.getByRole('button', { name: 'Save settings' })); await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(writes).toEqual(Array.from({ length: 2 }, () => ({ cmd: 'update_agent_digest_settings', agent_id: 'eng', settings: { digest_verbosity: 'compact', push_interval: 120, max_interval: 600, heartbeat_interval: 0, enabled_events: ['task_derived', 'task_health_alert'] } })));
});
