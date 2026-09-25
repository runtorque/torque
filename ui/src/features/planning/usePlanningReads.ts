import { useCallback, useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectPlanningState } from '../../app/store';
import { readCommand } from '../../protocol/http';
import type { PlanningRead } from './planningReads';

type Planning = ReturnType<typeof selectPlanningState>;
type Field = keyof Planning;
export function usePlanningReads(requests: PlanningRead[]) {
  const dispatch = useAppDispatch();
  const source = useAppSelector(selectPlanningState);
  const snapshot = useAppSelector((state) => state.projection.snapshotVersion);
  const ready = useAppSelector((state) => state.connection.status === 'connected' && state.connection.expectedSeq !== null && !state.connection.awaitingResync);
  const reconnect = useAppSelector((state) => state.connection.reconnectCount);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState({ key: '', error: '' });
  const [accepted, setAccepted] = useState<Record<string, number>>(() => Object.fromEntries(Object.keys(source).map((field) => [field, snapshot])));
  const [cache, setCache] = useState({ source, snapshot, accepted, values: source });
  // Compact snapshots omit hydrated lists. Keep each accepted collection until
  // its own full read succeeds in the new epoch; then resume normal live deltas.
  const changed = cache.source !== source || cache.snapshot !== snapshot || cache.accepted !== accepted;
  const values = changed ? Object.fromEntries(Object.keys(source).map((field) => [field, accepted[field] === snapshot ? source[field as Field] : cache.values[field as Field]])) as Planning : cache.values;
  if (changed) setCache({ source, snapshot, accepted, values });
  const plan = JSON.stringify(requests); const key = JSON.stringify([plan, ready, snapshot, reconnect, revision]);
  useEffect(() => {
    const reads = JSON.parse(plan) as PlanningRead[];
    if (!ready || !reads.length) return;
    const controller = new AbortController(); let disposed = false;
    const timer = window.setTimeout(() => { if (!disposed) { setResult({ key, error: 'Planning read timed out. Retry to refresh.' }); controller.abort(); } }, 30_000);
    void Promise.allSettled(reads.map(async (read) => {
      const frame = await readCommand(read.command, controller.signal);
      if (disposed || controller.signal.aborted) return;
      if (frame.type === 'error') throw new Error(typeof frame.message === 'string' ? frame.message : 'Planning read failed.');
      if (frame.type !== read.type || (read.command.group !== undefined && frame.group !== undefined && frame.group !== read.command.group)) throw new Error('Planning response did not match the requested section or group.');
      dispatch(projectionActions.auxiliaryResourceReceived(frame));
      setAccepted((current) => ({ ...current, [read.field]: snapshot }));
    })).then((results) => {
      if (disposed || controller.signal.aborted) return;
      const errors = results.flatMap((entry) => entry.status === 'rejected' ? [entry.reason instanceof Error ? entry.reason.message : 'Planning read failed.'] : []);
      setResult({ key, error: [...new Set(errors)].join(' ') });
    }).finally(() => window.clearTimeout(timer));
    return () => { disposed = true; controller.abort(); window.clearTimeout(timer); };
  }, [plan, key, ready, snapshot, dispatch]);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  return { planning: values, ready, pending: ready && requests.length > 0 && result.key !== key, error: result.key === key ? result.error : '', refresh };
}
