import { useEffect, useRef, useState } from 'react';
import type { UnknownRecord } from '../../protocol';

const responses: Record<string, string> = {
  worktree_create: 'worktree_create', worktree_checkpoint: 'worktree_checkpoint', worktree_rollback: 'worktree_rollback',
  worktree_rebase: 'worktree_rebase', worktree_remove: 'worktree_remove',
  worktree_create_pr: 'worktree_pr', worktree_merge: 'worktree_merge',
};
class Refused extends Error {
  readonly frame: UnknownRecord | undefined;
  constructor(message: string, frame?: UnknownRecord) { super(message); this.frame = frame; }
}
function acknowledgement(frame: UnknownRecord, command: UnknownRecord) {
  if (frame.type !== responses[String(command.cmd)] || frame.id !== command.id) throw new Error('The acknowledgement did not match this worktree operation.');
  if (frame.ok === false || frame.error) throw new Refused(typeof frame.error === 'string' ? frame.error : typeof frame.message === 'string' ? frame.message : 'Worktree operation refused.', frame);
  if (command.cmd === 'worktree_remove') {
    if (frame.worktree_removed !== true) throw new Error('Worktree removal was not confirmed.');
  } else if (frame.ok !== true) throw new Error('The worktree acknowledgement was incomplete.');
  if (command.cmd === 'worktree_create' && (frame.created !== true || typeof frame.worktree_path !== 'string' || !frame.worktree_path || typeof frame.relaunched !== 'boolean' || (command.relaunch === true && (frame.relaunched !== true || typeof frame.session_id !== 'string' || !frame.session_id || frame.session_id === command.expected_session_id)))) throw new Error('Worktree creation or its requested relaunch was not confirmed.');
  if (command.cmd === 'worktree_checkpoint' && (typeof frame.created !== 'boolean' || (frame.created && !frame.sha))) throw new Error('The checkpoint result was incomplete.');
  if (command.cmd === 'worktree_rollback' && frame.sha !== command.sha) throw new Error('The restored checkpoint did not match the request.');
}

/** Explicit retries retain the exact key/payload only while the server outcome is unknown. */
export function useWorktreeMutation() {
  const mounted = useRef(true);
  const busy = useRef(false);
  const attempt = useRef<UnknownRecord | null>(null);
  const [pending, setPending] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<UnknownRecord>({});
  const [command, setCommand] = useState('');
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function execute(payload: UnknownRecord): Promise<UnknownRecord | null> {
    if (busy.current) return null;
    busy.current = true; setPending(true); setError(''); setResult((previous) => payload.resume_worktree_path ? previous : {}); setCommand(String(payload.cmd));
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 120_000);
    try {
      const response = await fetch('/api/cmd', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: controller.signal });
      const body = await response.json() as { ok?: boolean; error?: string; data?: UnknownRecord };
      if (body.ok === false) throw new Refused(body.error || `Worktree operation refused (${response.status}).`);
      if (!response.ok || body.ok !== true || !body.data) throw new Error('The server did not confirm the worktree operation.');
      acknowledgement(body.data, payload);
      attempt.current = null;
      if (!mounted.current) return null;
      setUncertain(false); setResult(body.data); return body.data;
    } catch (cause) {
      const refused = cause instanceof Refused;
      if (refused) {
        attempt.current = null;
        if (mounted.current) setResult(cause.frame ?? {});
      }
      if (mounted.current) {
        setUncertain(!refused);
        setError(refused ? cause.message : `The outcome is unknown. ${controller.signal.aborted ? 'The request timed out.' : cause instanceof Error ? cause.message : 'The connection failed.'} Retry to retrieve the outcome of the same operation.`);
      }
      return null;
    } finally {
      window.clearTimeout(timer); busy.current = false;
      if (mounted.current) setPending(false);
    }
  }
  return {
    pending, uncertain, error, result, command,
    reset: () => {
      if (busy.current || attempt.current) return false;
      setError(''); setResult({}); setCommand(''); setUncertain(false); return true;
    },
    run: (payload: UnknownRecord) => {
      if (busy.current || attempt.current) return Promise.resolve(null);
      const keyed = { ...payload, idempotency_key: crypto.randomUUID() }; attempt.current = keyed;
      return execute(keyed);
    },
    retry: () => attempt.current ? execute(attempt.current) : Promise.resolve(null),
  };
}
