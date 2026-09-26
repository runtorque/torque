import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectConnection } from '../../app/store';
import type { UnknownRecord } from '../../protocol';
import { planningRequest } from './planningRequests';
import { records, text } from './model';

/** Server fields stay in the projection; local state contains only edits. */
export function usePlanningEditor(kind: 'initiative' | 'decision', item: UnknownRecord, defaults: Record<string, string>) {
  const id = text(item.id);
  const dispatch = useAppDispatch();
  const reconnect = useAppSelector(selectConnection).reconnectCount;
  const [edits, setEdits] = useState<Record<string, string>>({});
  const draft = Object.fromEntries(Object.entries(defaults).map(([key, fallback]) => [key, edits[key] ?? text(item[key], fallback)]));
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void planningRequest(kind === 'initiative' ? { cmd: 'initiative_show', id } : { cmd: 'decisions_snapshot', include_archived: true }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      const record = kind === 'initiative' && frame.type === 'initiative' ? frame : kind === 'decision' && frame.type === 'decisions_snapshot' ? records(frame.decisions).find((entry) => entry.id === id) : undefined;
      if (!record || record.id !== id) throw new Error('Planning detail was not returned.');
      dispatch(projectionActions.auxiliaryResourceReceived({ ...record, type: kind }));
      setLoaded(true); setLoadError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setLoadError(cause instanceof Error ? cause.message : 'Could not load planning details.'); });
    return () => controller.abort();
  }, [kind, id, reconnect, revision, item.updated_at, dispatch]);
  return {
    detail: item, draft, loaded, loadError,
    change: (patch: Record<string, string>) => setEdits((current) => ({ ...current, ...patch })),
    reload: () => setRevision((value) => value + 1), patch: () => edits,
    acknowledge: (patch: Record<string, string>) => setEdits((current) => Object.fromEntries(Object.entries(current).filter(([key, value]) => !(key in patch) || value !== patch[key]))),
  };
}
