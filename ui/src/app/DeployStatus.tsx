import { useEffect, useState } from 'react';
import type { AuxiliaryFrame, UnknownRecord } from '../protocol';
import { readCommand } from '../protocol/http';

interface DeployResult { group: string; count: number; title: string; error: string }
export function DeployStatus({ group, enabled, ready, reconnect, onOpen }: { group: string; enabled: boolean; ready: boolean; reconnect: number; onOpen: () => void }) {
  const [result, setResult] = useState<DeployResult | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!enabled || !ready) return;
    let disposed = false; let generation = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    const stop = () => { generation++; clearTimeout(timer); clearTimeout(deadline); controller?.abort(); };
    const poll = () => {
      stop(); if (disposed || document.hidden) return;
      const current = generation; const request = new AbortController(); controller = request;
      const finish = (frame?: AuxiliaryFrame, cause?: unknown) => {
        if (disposed || current !== generation) return;
        generation++; clearTimeout(deadline);
        let error = cause instanceof Error ? cause.message : cause ? 'Deploy check failed.' : '';
        if (!error && (frame?.type !== 'deploy_state' || frame.group !== group)) error = 'Deploy response did not match the requested group.';
        if (!error && frame?.error) error = typeof frame.error === 'string' ? frame.error : 'Deploy check failed.';
        const pending = frame?.pending_deploy as UnknownRecord | undefined;
        const rawCount = Number(pending?.count ?? 0); const count = Number.isFinite(rawCount) ? Math.max(0, Math.round(rawCount)) : 0;
        const ids = Array.isArray(pending?.torque_task_ids) ? pending.torque_task_ids.map(String) : [];
        const uptime = frame?.daemon_uptime_seconds;
        const title = error ? `Deploy-pending check failed: ${error}` : `Merged but not yet deployed: ${count}${ids.length ? `\nTasks: ${ids.join(', ')}` : ''}${uptime != null && Number.isFinite(Number(uptime)) ? `\nDaemon uptime: ${Math.max(0, Math.floor(Number(uptime)))}s` : ''}`;
        setResult({ group, count, title, error });
        timer = setTimeout(poll, 90_000);
      };
      deadline = setTimeout(() => { finish(undefined, new Error('Deploy check timed out. Click to retry.')); request.abort(); }, 15_000);
      void readCommand({ cmd: 'get_deploy_state', group }, request.signal).then((frame) => finish(frame), (cause: unknown) => finish(undefined, cause));
    };
    const visibility = () => { if (document.hidden) stop(); else poll(); };
    document.addEventListener('visibilitychange', visibility); poll();
    return () => { disposed = true; stop(); document.removeEventListener('visibilitychange', visibility); };
  }, [group, enabled, ready, reconnect, refresh]);
  if (!enabled || result?.group !== group || (!result.error && result.count === 0)) return null;
  return <button data-tone={result.error ? 'danger' : 'warning'} title={result.title} onClick={() => { setRefresh((value) => value + 1); if (!result.error && result.count > 0) onOpen(); }}>{result.error ? 'Deploy ?' : `Deploy +${result.count}`}</button>;
}
