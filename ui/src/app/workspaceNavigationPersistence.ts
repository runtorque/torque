import { readCommand } from '../protocol/http';
import { workspaceUiActions, type AppStore } from './store';
import { restoredNavigation, validNavigation, type WorkspaceNavigation } from './workspaceNavigation';

/** Restore once, then serialize local navigation writes without echoing server deltas. */
export function installWorkspaceNavigation(store: AppStore, onError: (message: string) => void) {
  let disposed = false; let hydrated = false; let queued = false; let pending = false;
  let savedRevision = 0; let failedRevision = -1; let connectionEpoch: number | null = null;
  let controller: AbortController | null = null;
  const schedule = () => {
    if (queued || disposed) return; queued = true;
    queueMicrotask(() => { queued = false; void pump(); });
  };
  const pump = async () => {
    if (disposed) return;
    const state = store.getState();
    if (!state.projection.hydrated) return;
    if (!hydrated) {
      hydrated = true;
      store.dispatch(workspaceUiActions.restoreNavigation(restoredNavigation(state.projection.data)));
    }
    const connection = state.connection;
    if (connectionEpoch !== connection.lastConnectedAt) { connectionEpoch = connection.lastConnectedAt; failedRevision = -1; }
    const current = store.getState().workspaceUi; const revision = current.navigationRevision;
    if (pending || connection.status !== 'connected' || connection.expectedSeq === null || connection.awaitingResync || revision <= savedRevision || revision === failedRevision) return;
    const preference: WorkspaceNavigation = { version: 1, activePanel: current.activePanel, controlTab: current.controlTab };
    controller = new AbortController(); pending = true; onError('');
    try {
      const frame = await readCommand({ cmd: 'ui_set_react_workspace_state', state: preference }, controller.signal);
      if (disposed) return;
      if (frame.type === 'error') throw new Error(typeof frame.message === 'string' ? frame.message : 'Workspace preference could not be saved.');
      const saved = validNavigation(frame.state);
      if (frame.type !== 'react_workspace_state' || !saved || saved.activePanel !== preference.activePanel || saved.controlTab !== preference.controlTab) throw new Error('Could not confirm the saved workspace preference.');
      savedRevision = revision;
    } catch (cause) {
      if (disposed) return;
      failedRevision = revision;
      if (store.getState().workspaceUi.navigationRevision === revision) onError(cause instanceof Error ? cause.message : 'Workspace preference could not be saved.');
    } finally { pending = false; controller = null; schedule(); }
  };
  const unsubscribe = store.subscribe(schedule); schedule();
  return {
    retry: () => { failedRevision = -1; schedule(); },
    dispose: () => { disposed = true; unsubscribe(); controller?.abort(); },
  };
}
