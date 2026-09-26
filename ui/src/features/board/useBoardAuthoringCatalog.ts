import { useEffect, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import type { UnknownRecord } from '../../protocol';
import { boardReadRequest } from './boardReadRequest';

export function catalogItems(value: unknown): UnknownRecord[] {
  if (!value || typeof value !== 'object') return [];
  return (Array.isArray(value) ? value : Object.values(value)).filter((item): item is UnknownRecord => Boolean(item && typeof item === 'object' && !Array.isArray(item)));
}

/** Catalogs belong to their requesting group, never the last global WS response. */
export function useBoardAuthoringCatalog(group: string, active = true) {
  const connection = useAppSelector(selectConnection);
  const [revision, setRevision] = useState(0);
  const [cache, setCache] = useState<Record<string, { actions?: UnknownRecord[]; roles?: UnknownRecord[] }>>({});
  const [outcome, setOutcome] = useState({ key: '', error: '' });
  const key = JSON.stringify([group, active, connection.status, connection.reconnectCount, revision]);
  useEffect(() => {
    if (!active || !group || connection.status !== 'connected') return;
    const owner = new AbortController();
    void Promise.allSettled((['actions', 'roles'] as const).map(async (kind) => {
      const frame = await boardReadRequest({ cmd: `list_${kind}`, group }, owner.signal);
      if (owner.signal.aborted) return;
      if (frame.type !== kind || !frame[kind] || typeof frame[kind] !== 'object' || (frame.group !== undefined && frame.group !== group)) throw new Error(`Could not load ${kind} for ${group}: unrelated or invalid response.`);
      const items = catalogItems(frame[kind]);
      setCache((current) => ({ ...current, [group]: { ...current[group], [kind]: items } }));
    })).then((results) => {
      if (owner.signal.aborted) return;
      const failures = results.filter((result): result is PromiseRejectedResult => result.status === 'rejected');
      setOutcome({ key, error: failures.map((result) => result.reason instanceof Error ? result.reason.message : 'Could not load task options.').join(' ') });
    });
    return () => owner.abort();
  }, [group, active, connection.status, key]);
  return { actions: cache[group]?.actions ?? [], roles: cache[group]?.roles ?? [], error: outcome.key === key ? outcome.error : '', loading: active && connection.status === 'connected' && outcome.key !== key, retry: () => setRevision((value) => value + 1) };
}
