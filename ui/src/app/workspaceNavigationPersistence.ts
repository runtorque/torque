import type { AuxiliaryFrame, TorqueCommand } from '../protocol';
import { readCommand } from '../protocol/http';
import { workspaceUiActions, type AppStore } from './store';
import { restoredNavigation, validNavigation, type WorkspaceNavigation } from './workspaceNavigation';

export const workspaceSaveTimeout = 30_000;
function observeWorkspaceSave(command: TorqueCommand, controller: AbortController): Promise<AuxiliaryFrame> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (frame?: AuxiliaryFrame, error?: Error) => {
      if (settled) return;
      settled = true; clearTimeout(timer); controller.signal.removeEventListener('abort', cancel);
      if (error) { controller.abort(); reject(error); } else resolve(frame!);
    };
    const cancel = () => finish(undefined, new Error('Workspace save cancelled.'));
    const timer = setTimeout(() => finish(undefined, new Error('Workspace save timed out. Navigation is retained; retry saving.')), workspaceSaveTimeout);
    controller.signal.addEventListener('abort', cancel, { once: true });
    void readCommand(command, controller.signal).then((frame) => finish(frame), (cause: unknown) => finish(undefined, cause instanceof Error ? cause : new Error('Workspace preference could not be saved.')));
  });
}

/** Restore once, then serialize local navigation writes without echoing server deltas. */
export function installWorkspaceNavigation(store: AppStore, onError: (message: string) => void) {
  const writerId = crypto.randomUUID();
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
      const frame = await observeWorkspaceSave({ cmd: 'ui_set_react_workspace_state', state: preference, writer_id: writerId, revision }, controller);
      if (disposed) return;
      if (frame.type === 'error') throw new Error(typeof frame.message === 'string' ? frame.message : 'Workspace preference could not be saved.');
      const saved = validNavigation(frame.state);
      if (frame.type !== 'react_workspace_state' || frame.writer_id !== writerId || frame.revision !== revision || !saved || saved.activePanel !== preference.activePanel || saved.controlTab !== preference.controlTab) throw new Error('Could not confirm the saved workspace preference.');
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
