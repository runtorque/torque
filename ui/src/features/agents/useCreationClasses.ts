import { useEffect, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import { readCommand } from '../../protocol/http';
import type { UnknownRecord } from '../../protocol';

const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
const text = (value: unknown): string => typeof value === 'string' ? value : '';
export const creationClassLabel = (item: UnknownRecord): string => text(item.primary_identity_label) || text(item.display_name) || text(item.name) || text(item.id);
export function creationClassDisabledReason(item: UnknownRecord, kind: string): string {
  const metadata = record(item.metadata);
  if (item.base_kind !== kind) return 'Agent Class kind does not match this launch.';
  if (item.archived || item.disabled || metadata.archived || metadata.disabled || metadata.archived_at) return 'Archived or disabled Agent Classes cannot launch.';
  if (item.launchable === false) return 'The server reports that this Agent Class cannot launch.';
  if ((item.status || item.lifecycle) === 'invalid') return 'Invalid Agent Classes cannot launch.';
  return '';
}

/** Creation owns its catalog so other panels and projects cannot replace it. */
export function useCreationClasses(active: boolean, group: string, paused: boolean) {
  const connection = useAppSelector(selectConnection);
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState({ key: '', group: '', classes: [] as UnknownRecord[], issues: [] as UnknownRecord[], error: '' });
  const key = JSON.stringify([group, connection.reconnectCount, retry]);
  const connected = connection.status === 'connected';
  useEffect(() => {
    if (!active || !connected || paused || result.key === key) return;
    const controller = new AbortController();
    void readCommand({ cmd: 'agent_class_list', group }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type === 'error') throw new Error(text(frame.message) || 'Could not load Agent Classes.');
      if (frame.type !== 'agent_classes' || frame.group !== group || !Array.isArray(frame.classes)) throw new Error('The Agent Class response did not match this creation group.');
      setResult({ key, group, classes: frame.classes.map(record), issues: Array.isArray(frame.issues) ? frame.issues.map(record) : [], error: '' });
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setResult((previous) => ({ ...previous, key, error: cause instanceof Error ? cause.message : 'Could not load Agent Classes.' }));
    });
    return () => controller.abort();
  }, [active, connected, group, key, paused, result.key]);
  const error = result.key === key ? result.error : '';
  return {
    classes: result.group === group ? result.classes : [],
    issues: result.group === group ? result.issues : [],
    loading: active && connected && result.key !== key,
    error,
    unavailable: !connected ? 'Reconnect to verify the selected Agent Class.' : result.key !== key ? 'Verifying Agent Classes…' : error,
    refresh: () => setRetry((value) => value + 1),
  };
}
