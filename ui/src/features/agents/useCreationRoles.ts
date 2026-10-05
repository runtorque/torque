import { useEffect, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import type { UnknownRecord } from '../../protocol';
import { settingsRequest } from '../control/settingsRequests';

// list_templates is an alias of list_roles; creation owns one scoped response.
export function useCreationRoles(group: string, active: boolean) {
  const connection = useAppSelector(selectConnection);
  const [revision, setRevision] = useState(0);
  const [accepted, setAccepted] = useState<{ group: string; roles: UnknownRecord[] }>({ group: '', roles: [] });
  const [outcome, setOutcome] = useState({ key: '', error: '' });
  const key = JSON.stringify([group, active, connection.status, connection.reconnectCount, revision]);
  useEffect(() => {
    if (!active || !group || connection.status !== 'connected') return;
    const owner = new AbortController();
    void settingsRequest({ cmd: 'list_roles', group }, owner.signal, false, 'Creation roles').then((frame) => {
      if (owner.signal.aborted) return;
      if (frame.type !== 'roles' || frame.group !== group || !Array.isArray(frame.roles)) throw new Error('The role response did not match this creation group.');
      const roles: UnknownRecord[] = [];
      for (const value of frame.roles) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The role catalog contains an invalid entry.');
        const role = value as UnknownRecord;
        if (typeof role.name !== 'string' || !role.name.trim()) throw new Error('The role catalog contains an unnamed entry.');
        roles.push(role);
      }
      setAccepted({ group, roles }); setOutcome({ key, error: '' });
    }).catch((cause: unknown) => {
      if (!owner.signal.aborted) setOutcome({ key, error: cause instanceof Error ? cause.message : 'Could not load creation roles.' });
    });
    return () => owner.abort();
  }, [group, active, connection.status, key]);
  const error = outcome.key === key ? outcome.error : '';
  return { roles: accepted.group === group ? accepted.roles : [], error,
    loading: active && connection.status === 'connected' && outcome.key !== key,
    verified: active && connection.status === 'connected' && outcome.key === key && !error,
    refresh: () => setRevision((value) => value + 1) };
}
