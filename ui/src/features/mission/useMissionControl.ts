import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import { readCommand } from '../../protocol/http';
import { missionSessionActions } from './missionSession';
import { validateSummary, text } from './missionModel';
export const missionRequestTimeout = 15_000;
export function useMissionControl(group: string, refreshVersion: number) {
  const dispatch = useAppDispatch(); const connection = useAppSelector(selectConnection); const session = useAppSelector((state) => state.missionSession);
  const [retry, setRetry] = useState(0); const [settled, setSettled] = useState({ key: '', error: '' });
  const key = JSON.stringify([group, refreshVersion, retry, connection.status, connection.reconnectCount]);
  useEffect(() => {
    dispatch(missionSessionActions.enter(group));
    if (!group || connection.status !== 'connected') return;
    const controller = new AbortController();
    const timer = setTimeout(() => { controller.abort(); setSettled({ key, error: 'Mission Control refresh timed out. Retry when ready.' }); }, missionRequestTimeout);
    void readCommand({ cmd: 'get_mission_control', group, limit_per_section: 20, include_recent_completed: true }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return; validateSummary(frame, group); dispatch(missionSessionActions.received({ group, summary: frame })); setSettled({ key, error: '' });
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setSettled({ key, error: cause instanceof Error ? cause.message : 'Mission Control refresh failed.' }); }).finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); };
  }, [dispatch, group, key, connection.status]);
  return { summary: session.group === group ? session.summary : null, pending: !!group && connection.status === 'connected' && settled.key !== key, error: settled.key === key ? settled.error : '', disconnected: connection.status !== 'connected', refresh: () => setRetry((value) => value + 1) };
}
export function useMissionDismiss(group: string) {
  const dispatch = useAppDispatch();
  const operations = useRef(new Map<string, { controller: AbortController; timer: ReturnType<typeof setTimeout> }>());
  const [outcomes, setOutcomes] = useState<Record<string, { pending: boolean; error: string }>>({});
  useEffect(() => { const active = operations.current; return () => { for (const operation of active.values()) { clearTimeout(operation.timer); operation.controller.abort(); } active.clear(); }; }, [group]);
  const dismiss = (id: string, beforeRemoval: () => void) => {
    if (!id || operations.current.has(id)) return;
    const controller = new AbortController(); setOutcomes((current) => ({ ...current, [id]: { pending: true, error: '' } }));
    const fail = (error: string) => setOutcomes((current) => ({ ...current, [id]: { pending: false, error } }));
    const timer = setTimeout(() => { controller.abort(); operations.current.delete(id); fail('Dismissal outcome is unknown. Refresh to check the card before retrying.'); }, missionRequestTimeout);
    operations.current.set(id, { controller, timer });
    void readCommand({ cmd: 'mission_control_dismiss', id, timestamp: Date.now() / 1000 }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'ok' && frame.type !== 'state') throw new Error(text(frame.message, 'The daemon returned an invalid dismissal acknowledgement.'));
      beforeRemoval(); dispatch(missionSessionActions.dismissed(id)); setOutcomes((current) => ({ ...current, [id]: { pending: false, error: '' } }));
    }).catch((cause: unknown) => { if (!controller.signal.aborted) fail(cause instanceof Error ? cause.message : 'Dismissal failed. Retry when ready.'); }).finally(() => { clearTimeout(timer); if (operations.current.get(id)?.controller === controller) operations.current.delete(id); });
  };
  return { dismiss, outcomes };
}
