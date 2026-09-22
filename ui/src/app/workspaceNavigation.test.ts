import { afterEach, describe, expect, it, vi } from 'vitest';
import { compactStateFixture } from '../protocol/fixtures';
import type { TorqueCommand, UnknownRecord } from '../protocol';
import { connectionActions, createAppStore, projectionActions, workspaceUiActions } from './store';
import { defaultNavigation, restoredNavigation, validNavigation, type WorkspaceNavigation } from './workspaceNavigation';
import { installWorkspaceNavigation } from './workspaceNavigationPersistence';
const preference: WorkspaceNavigation = { version: 1, activePanel: 'control', controlTab: 'context' };
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const cleanups: (() => void)[] = [];
afterEach(() => { cleanups.splice(0).forEach((cleanup) => cleanup()); vi.unstubAllGlobals(); });
function setup(hydrate = true) {
  const store = createAppStore(); const errors = vi.fn(); const calls: { command: TorqueCommand; resolve: (frame: UnknownRecord) => void }[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((resolve) => calls.push({ command: JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand, resolve: (frame) => resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: frame }) }) }))));
  const snapshot = (saved: WorkspaceNavigation = preference) => { const frame = { ...compactStateFixture, react_workspace_state: saved }; store.dispatch(projectionActions.snapshotReceived(frame)); store.dispatch(connectionActions.snapshotAccepted(frame)); };
  store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); if (hydrate) snapshot();
  const persistence = installWorkspaceNavigation(store, errors); cleanups.push(persistence.dispose);
  const ack = async (index: number) => { calls[index]!.resolve({ type: 'react_workspace_state', state: calls[index]!.command.state }); await flush(); };
  return { store, errors, calls, snapshot, ack, ...persistence };
}
describe('workspace preference projection', () => {
  it('restores only bounded known preferences and migrates Classic panel ownership', () => {
    expect(validNavigation({ ...preference, discarded: 'field' })).toEqual(preference);
    for (const raw of [null, [], {}, { ...preference, version: true }, { ...preference, version: 2 }, { ...preference, activePanel: 'legacy' }, { ...preference, controlTab: {} }]) expect(validNavigation(raw)).toBeNull();
    expect(restoredNavigation({ react_workspace_state: preference, panel_active: 'help' })).toEqual(preference);
    expect(restoredNavigation({ panel_active: 'help' })).toEqual({ ...preference, controlTab: 'help' });
    expect(restoredNavigation({ panel_active: 'engineer' })).toEqual({ ...defaultNavigation, activePanel: 'agents' });
    expect(restoredNavigation({ panel_active: 'initiatives' })).toEqual({ ...defaultNavigation, activePanel: 'planning' });
    expect(restoredNavigation({ runtime: { embedded_terminal: true }, panel_active: 'help', standalone_panel_layout: { last_active: 'context' } })).toEqual(preference);
    expect(restoredNavigation({ runtime: { embedded_terminal: false }, panel_active: 'help', standalone_panel_layout: { last_active: 'context' } })).toEqual({ ...preference, controlTab: 'help' });
    expect(restoredNavigation({ panel_active: 'supervisor' })).toEqual({ ...defaultNavigation, activePanel: 'control' });
    expect(restoredNavigation({ panel_active: 'removed' })).toEqual(defaultNavigation);
  });
  it('restores once without writes, then ignores server echoes and reconnect snapshots', async () => {
    const h = setup(); await flush(); expect(h.store.getState().workspaceUi).toMatchObject({ activePanel: preference.activePanel, controlTab: preference.controlTab }); expect(h.calls).toHaveLength(0);
    h.snapshot({ ...defaultNavigation, activePanel: 'agents' }); await flush(); expect(h.store.getState().workspaceUi.activePanel).toBe('control');
    h.store.dispatch(workspaceUiActions.setActivePanel('planning')); await flush(); expect(h.calls).toHaveLength(1);
    h.snapshot(defaultNavigation); await h.ack(0); expect(h.store.getState().workspaceUi.activePanel).toBe('planning'); expect(h.calls).toHaveLength(1);
  });
  it('retains navigation chosen before hydration and waits for a connected snapshot to persist it', async () => {
    const h = setup(false); h.store.dispatch(workspaceUiActions.setActivePanel('agents')); await flush(); expect(h.calls).toHaveLength(0);
    h.snapshot(); await flush(); expect(h.store.getState().workspaceUi.activePanel).toBe('agents'); expect(h.calls[0]?.command.state).toEqual({ ...defaultNavigation, activePanel: 'agents' }); await h.ack(0);
    h.store.dispatch(connectionActions.disconnected({ at: 2 })); h.store.dispatch(workspaceUiActions.setActivePanel('planning')); await flush(); expect(h.calls).toHaveLength(1);
    h.store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); await flush(); expect(h.calls).toHaveLength(1);
    h.snapshot(); await flush(); expect(h.calls[1]?.command.state).toEqual({ ...defaultNavigation, activePanel: 'planning' }); await h.ack(1);
  });
  it('coalesces same-event navigation and serializes later changes behind the acknowledged request', async () => {
    const h = setup(); await flush(); h.store.dispatch(workspaceUiActions.setActivePanel('control')); h.store.dispatch(workspaceUiActions.setControlTab('help')); await flush();
    expect(h.calls).toHaveLength(1); expect(h.calls[0]?.command.state).toEqual({ ...preference, controlTab: 'help' });
    h.store.dispatch(workspaceUiActions.setActivePanel('agents')); h.store.dispatch(workspaceUiActions.setActivePanel('planning')); await flush(); expect(h.calls).toHaveLength(1);
    await h.ack(0); expect(h.calls).toHaveLength(2); expect(h.calls[1]?.command.state).toEqual({ ...preference, activePanel: 'planning', controlTab: 'help' }); await h.ack(1);
    expect(h.store.getState().workspaceUi.activePanel).toBe('planning');
  });
  it('retains refused navigation, offers explicit retry and never retries on unrelated deltas', async () => {
    const h = setup(); await flush(); h.store.dispatch(workspaceUiActions.setActivePanel('agents')); await flush();
    h.calls[0]!.resolve({ type: 'react_workspace_state', state: preference }); await flush(); expect(h.errors).toHaveBeenLastCalledWith('Could not confirm the saved workspace preference.');
    h.snapshot(); await flush(); expect(h.calls).toHaveLength(1); expect(h.store.getState().workspaceUi.activePanel).toBe('agents');
    h.retry(); await flush(); expect(h.calls[1]!.command).toEqual(h.calls[0]!.command); await h.ack(1); expect(h.errors).toHaveBeenLastCalledWith('');
  });
  it('retries refused writes after reconnect and disposal stops queued writes', async () => {
    const h = setup(); await flush(); h.store.dispatch(workspaceUiActions.setActivePanel('agents')); await flush(); h.calls[0]!.resolve({ type: 'error', message: 'Storage refused' }); await flush();
    h.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); h.snapshot(); await flush(); expect(h.calls).toHaveLength(2); await h.ack(1);
    h.store.dispatch(workspaceUiActions.setActivePanel('planning')); h.dispose(); await flush(); expect(h.calls).toHaveLength(2);
  });
});
