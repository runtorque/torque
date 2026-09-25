import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions } from '../../app/store';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { validateActivityRead, type ActivityRead } from './activityReads';

export function useActivityReads(requests: ActivityRead[], active: boolean, invalidation = '') {
  const dispatch = useAppDispatch();
  const ready = useAppSelector((state) => state.connection.status === 'connected' && state.connection.expectedSeq !== null && !state.connection.awaitingResync) && active;
  const reconnect = useAppSelector((state) => state.connection.reconnectCount);
  const snapshot = useAppSelector((state) => state.projection.snapshotVersion);
  const [revision, setRevision] = useState(0);
  const [catalog, setCatalog] = useState<{ key: string; frame: UnknownRecord } | null>(null);
  const [result, setResult] = useState({ key: '', error: '' });
  const plan = JSON.stringify(requests); const key = JSON.stringify([plan, ready, reconnect, snapshot, revision, invalidation]);
  useEffect(() => {
    const reads = JSON.parse(plan) as ActivityRead[];
    if (!ready || !reads.length) return;
    let disposed = false; const controller = new AbortController();
    const timer = window.setTimeout(() => { if (!disposed) { setResult({ key, error: 'Activity read timed out. Retry to refresh.' }); controller.abort(); } }, 30_000);
    void Promise.allSettled(reads.map(async (request) => {
      const frame = await readCommand(request.command, controller.signal);
      if (disposed || controller.signal.aborted) return;
      validateActivityRead(frame, request);
      if (request.type === 'agent_classes') {
        // The shared Catalog projection may belong to a different project. Keep
        // only this owned response, keyed by its complete requested scope.
        setCatalog({ key: JSON.stringify(request.command), frame });
        return;
      }
      const target = request.target;
      const correlated = target && !target[0].includes('.') && frame[target[0]] === undefined ? { ...frame, [target[0]]: target[1] } : frame;
      dispatch(projectionActions.auxiliaryResourceReceived(correlated));
    })).then((results) => {
      if (disposed || controller.signal.aborted) return;
      const errors = results.flatMap((entry) => entry.status === 'rejected' ? [entry.reason instanceof Error ? entry.reason.message : 'Activity read failed.'] : []);
      setResult({ key, error: [...new Set(errors)].join(' ') });
    }).finally(() => window.clearTimeout(timer));
    return () => { disposed = true; controller.abort(); window.clearTimeout(timer); };
  }, [plan, key, ready, dispatch]);
  const catalogRequest = requests.find((request) => request.type === 'agent_classes');
  const classCatalog = catalog?.key === JSON.stringify(catalogRequest?.command) ? catalog?.frame : undefined;
  return { classCatalog, ready, pending: ready && requests.length > 0 && result.key !== key, error: result.key === key ? result.error : '', refresh: () => setRevision((value) => value + 1) };
}
