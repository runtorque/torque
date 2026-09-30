import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector, useAppStore } from '../../app/hooks';
import { projectionActions } from '../../app/store';
import type { AuxiliaryFrame, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { validateActivityRead, type ActivityRead } from './activityReads';

function liveReadSources(data: UnknownRecord, request: ActivityRead): unknown[] {
  const bucket = (key: string, target: unknown) => {
    const value = data[key];
    return value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord)[String(target)] : undefined;
  };
  if (request.type === 'engineer_journal_snapshot') return [bucket('engineer_journal', request.command.engineer_id), bucket('engineer_worklog', request.command.group)];
  if (request.type === 'architect_journal_entries') return [bucket('architect_journals', request.command.architect_id)];
  if (request.type === 'decisions_snapshot') return [data.decisions];
  if (request.type === 'task_detail') return [bucket('board_tasks', request.command.id)];
  return [];
}

export function useActivityReads(requests: ActivityRead[], active: boolean, invalidation = '') {
  const dispatch = useAppDispatch();
  const store = useAppStore();
  const ready = useAppSelector((state) => state.connection.status === 'connected' && state.connection.expectedSeq !== null && !state.connection.awaitingResync) && active;
  const reconnect = useAppSelector((state) => state.connection.reconnectCount);
  const snapshot = useAppSelector((state) => state.projection.snapshotVersion);
  const [revision, setRevision] = useState(0);
  const [taskDetails, setTaskDetails] = useState<Record<string, UnknownRecord>>({});
  const [catalog, setCatalog] = useState<{ key: string; frame: UnknownRecord } | null>(null);
  const [result, setResult] = useState({ key: '', error: '' });
  const plan = JSON.stringify(requests); const key = JSON.stringify([plan, ready, reconnect, snapshot, revision, invalidation]);
  useEffect(() => {
    const reads = JSON.parse(plan) as ActivityRead[];
    if (!ready || !reads.length) return;
    let disposed = false; const controller = new AbortController();
    const timer = window.setTimeout(() => { if (!disposed) { setResult({ key, error: 'Activity read timed out. Retry to refresh.' }); controller.abort(); } }, 30_000);
    void Promise.allSettled(reads.map(async (request) => {
      let frame: AuxiliaryFrame;
      // A read started before a live append/delete may return older rows. Retry
      // only that colliding read within the original deadline; routine deltas
      // update the displayed collection directly without issuing new reads.
      for (;;) {
        const before = liveReadSources(store.getState().projection.data, request);
        frame = await readCommand(request.command, controller.signal);
        if (disposed || controller.signal.aborted) return;
        validateActivityRead(frame, request);
        const after = liveReadSources(store.getState().projection.data, request);
        if (before.every((value, index) => value === after[index])) break;
      }
      if (request.type === 'task_detail') {
        const id = String(request.command.id);
        const task = frame.task as UnknownRecord | undefined;
        const current = (store.getState().projection.data.board_tasks as Record<string, UnknownRecord> | undefined)?.[id];
        if (!task || Array.isArray(task) || task.id !== id || !current || task.group !== current.group || !Array.isArray(task.messages_thread)) throw new Error('Message details did not match the requested task.');
        // Keep this read scoped to Activity. Do not overwrite Board edits with
        // a message-only hydration response or make every task delta refetch.
        setTaskDetails((previous) => ({ ...previous, [id]: task }));
        return;
      }
      if (request.type === 'agent_classes') {
        // The shared Catalog projection may belong to a different project. Keep
        // only this owned response, keyed by its complete requested scope.
        setCatalog({ key: JSON.stringify(request.command), frame });
        return;
      }
      const target = request.target;
      const correlated = target && !target[0].includes('.') && frame[target[0]] === undefined ? { ...frame, [target[0]]: target[1] } : frame;
      dispatch(projectionActions.auxiliaryResourceReceived(request.type === 'mcp_calls' ? { ...correlated, _activity_query_key: JSON.stringify(request.command) } : correlated));
    })).then((results) => {
      if (disposed || controller.signal.aborted) return;
      const errors = results.flatMap((entry) => entry.status === 'rejected' ? [entry.reason instanceof Error ? entry.reason.message : 'Activity read failed.'] : []);
      setResult({ key, error: [...new Set(errors)].join(' ') });
    }).finally(() => window.clearTimeout(timer));
    return () => { disposed = true; controller.abort(); window.clearTimeout(timer); };
  }, [plan, key, ready, dispatch, store]);
  const catalogRequest = requests.find((request) => request.type === 'agent_classes');
  const classCatalog = catalog?.key === JSON.stringify(catalogRequest?.command) ? catalog?.frame : undefined;
  return { taskDetails, classCatalog, ready, pending: ready && requests.length > 0 && result.key !== key, error: result.key === key ? result.error : '', refresh: () => setRevision((value) => value + 1) };
}
