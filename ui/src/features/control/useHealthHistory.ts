import { useEffect, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';

const REFRESH_MS = 60_000;
const READ_TIMEOUT_MS = 20_000;
interface HealthSnapshot { key: string; health: UnknownRecord; history: UnknownRecord }

export function useHealthHistory(group: string, windowSize: string, refresh: number) {
  const ready = useAppSelector((state) => state.connection.status === 'connected' && state.connection.expectedSeq !== null && !state.connection.awaitingResync);
  const reconnect = useAppSelector((state) => state.connection.reconnectCount);
  const key = JSON.stringify([group, windowSize]);
  const [accepted, setAccepted] = useState<HealthSnapshot | null>(null);
  const [failure, setFailure] = useState({ key: '', message: '' });
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    const poll = async () => {
      const request = new AbortController(); controller = request;
      let timedOut = false;
      const deadline = setTimeout(() => { timedOut = true; request.abort(); }, READ_TIMEOUT_MS);
      try {
        const [health, history] = await Promise.all([
          readCommand({ cmd: 'get_system_health_metrics', group, window: windowSize }, request.signal),
          readCommand({ cmd: 'get_metrics_history', group, window: windowSize }, request.signal),
        ]);
        if (cancelled || request.signal.aborted) return;
        for (const [frame, type] of [[health, 'system_health_metrics'], [history, 'metrics_history']] as const) {
          // History performance is daemon-wide; its unused workflow scope defaults to
          // the server's active group for an empty request. Scoped workflow comes from health.
          const requiresGroup = type === 'system_health_metrics' || group !== '';
          if (frame.type !== type || (requiresGroup && frame.group !== group) || frame.window !== windowSize) throw new Error('Health response did not match the requested scope and window.');
        }
        setAccepted({ key, health, history }); setFailure({ key, message: '' });
      } catch (cause) {
        if (!cancelled) setFailure({ key, message: timedOut ? 'Health refresh timed out. Retry or wait for the next refresh.' : cause instanceof Error ? cause.message : 'Health unavailable.' });
      } finally {
        clearTimeout(deadline); request.abort();
        if (!cancelled) timer = setTimeout(() => { void poll(); }, REFRESH_MS);
      }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); controller?.abort(); };
  }, [group, windowSize, key, ready, reconnect, refresh]);
  return { current: accepted?.key === key ? accepted : null, error: failure.key === key ? failure.message : '', ready };
}
