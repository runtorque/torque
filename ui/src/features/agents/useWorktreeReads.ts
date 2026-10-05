import { useEffect, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';

const commands = { diff: 'worktree_diff_full', preflight: 'worktree_check_merge', history: 'worktree_history' } as const;
export type WorktreeRead = keyof typeof commands;
interface Slot { scope: string; request: string; status: 'loading' | 'ready' | 'error'; data: UnknownRecord; error: string }
const empty: UnknownRecord = {};
function validate(value: UnknownRecord, key: WorktreeRead, id: string): void {
  if (value.type === 'error' || (value.error && key !== 'preflight')) throw new Error(typeof value.message === 'string' ? value.message : typeof value.error === 'string' ? value.error : 'Worktree read failed.');
  if (value.type !== commands[key] || value.id !== id) throw new Error('Worktree response did not match the requested target.');
  if ((key === 'diff' && !Array.isArray(value.files)) || (key === 'history' && !Array.isArray(value.commits)) || (key === 'preflight' && typeof value.clean !== 'boolean')) throw new Error('Worktree response was incomplete.');
}
export function useWorktreeReads(id: string, path: string, branch: string, active: boolean) {
  const synchronized = useAppSelector((state) => state.connection.status === 'connected' && state.connection.expectedSeq !== null && !state.connection.awaitingResync);
  const reconnect = useAppSelector((state) => state.connection.reconnectCount);
  const resync = useAppSelector((state) => state.connection.resyncCount);
  const [revision, setRevision] = useState(0);
  const [slots, setSlots] = useState<Partial<Record<WorktreeRead, Slot>>>({});
  const scope = JSON.stringify([id, path, branch]);
  const ready = active && synchronized && Boolean(id && path);
  const request = JSON.stringify([scope, ready, reconnect, resync, revision]);
  useEffect(() => {
    if (!ready) return;
    let disposed = false;
    const cleanups = (Object.keys(commands) as WorktreeRead[]).map((key) => {
      const controller = new AbortController();
      const update = (status: Slot['status'], error: string, data?: UnknownRecord) => {
        if (disposed) return;
        setSlots((previous) => ({ ...previous, [key]: { scope, request, status, error, data: data ?? (previous[key]?.scope === scope ? previous[key].data : empty) } }));
      };
      update('loading', '');
      const timer = window.setTimeout(() => { update('error', 'Worktree read timed out. Retry to refresh.'); controller.abort(); }, 30_000);
      void readCommand({ cmd: commands[key], id }, controller.signal).then((frame) => {
        if (disposed || controller.signal.aborted) return;
        validate(frame, key, id); update('ready', '', frame);
      }).catch((cause: unknown) => {
        if (!disposed && !controller.signal.aborted) update('error', cause instanceof Error ? cause.message : 'Worktree read failed.');
      }).finally(() => window.clearTimeout(timer));
      return () => { window.clearTimeout(timer); controller.abort(); };
    });
    return () => { disposed = true; cleanups.forEach((cleanup) => cleanup()); };
  }, [id, scope, request, ready]);
  const read = (key: WorktreeRead) => {
    const slot = slots[key]; const current = slot?.scope === scope;
    return { data: current ? slot.data : empty, status: ready ? current && slot.request === request ? slot.status : 'loading' : 'waiting', error: current && slot.request === request ? slot.error : '' };
  };
  return { ready, diff: read('diff'), preflight: read('preflight'), history: read('history'), refresh: () => setRevision((value) => value + 1) };
}
