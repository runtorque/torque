import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import type { UnknownRecord } from '../../protocol';
import { settingsRequest } from '../control/settingsRequests';
import { behaviorActions } from './session';
import { items, matchesScope, record, scopeArgs, scopeKey, text, validateRead, type Scope } from './model';
export function useBehaviorScope(scope: Scope, activeVersion: string, refreshVersion: number) {
  const dispatch = useAppDispatch(); const connection = useAppSelector(selectConnection); const [retry, setRetry] = useState(0);
  const [accepted, setAccepted] = useState<{ read: UnknownRecord; versions: UnknownRecord[]; proposals: UnknownRecord[] } | null>(null);
  const [settled, setSettled] = useState({ key: '', error: '' });
  const { group, kind, target } = scope; const targetKey = scopeKey(scope); const key = JSON.stringify([targetKey, activeVersion, refreshVersion, retry, connection.status, connection.reconnectCount]);
  useEffect(() => {
    if (connection.status !== 'connected') return;
    const controller = new AbortController(); const currentScope = { group, kind, target }; const args = scopeArgs(currentScope);
    void Promise.all([
      settingsRequest({ cmd: 'behavior_overlay_read', ...args, seed: true }, controller.signal, false, 'Behavior overlay'),
      settingsRequest({ cmd: 'behavior_overlay_versions', ...args, limit: 50 }, controller.signal, false, 'Behavior overlay'),
      settingsRequest({ cmd: 'behavior_overlay_proposals', ...args, limit: 200 }, controller.signal, false, 'Behavior overlay'),
    ]).then(([read, versions, proposals]) => {
      if (controller.signal.aborted) return;
      validateRead(read ?? {}, currentScope, 'behavior_overlay'); validateRead(versions ?? {}, currentScope, 'behavior_overlay_versions');
      if (proposals?.type !== 'behavior_overlay_proposals' || !Array.isArray(proposals.proposals)) throw new Error('The behavior proposal list is invalid.');
      if (activeVersion && text(record(read.version).id) !== activeVersion) throw new Error('The active behavior version changed. Reload the scope before proposing.');
      setAccepted({ read, versions: items(versions.versions), proposals: items(proposals.proposals).filter((row) => matchesScope(row, currentScope)) });
      dispatch(behaviorActions.seed({ key: targetKey, text: text(read.text) })); setSettled({ key, error: '' });
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setSettled({ key, error: cause instanceof Error ? cause.message : 'Behavior scope could not be loaded.' }); });
    return () => controller.abort();
  }, [dispatch, targetKey, group, kind, target, key, connection.status, activeVersion]);
  const pending = connection.status === 'connected' && settled.key !== key;
  return { accepted, pending, error: settled.key === key ? settled.error : '', ready: connection.status === 'connected' && settled.key === key && !settled.error && !!accepted, refresh: () => setRetry((value) => value + 1) };
}
