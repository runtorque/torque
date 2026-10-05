import { useEffect, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';

export interface RemovalPreview {
  review: UnknownRecord & { path: string; branch: string; repo_root: string; mode: 'remove' | 'unlink'; session_id: string; shared_ids: string[]; dirty: boolean; ignored_files: boolean; checkpoints: number; head: string; base_head: string; changes_digest: string };
  shared_with: { id: string; name: string }[];
  blocked_reason: string;
}
function validate(frame: UnknownRecord, id: string, path: string): RemovalPreview {
  if (frame.type !== 'worktree_remove_preview' || frame.id !== id) throw new Error('Removal review did not match this agent.');
  if (frame.ok !== true || frame.error) throw new Error(typeof frame.error === 'string' ? frame.error : 'Removal review was refused.');
  const value = frame as unknown as RemovalPreview; const review = value.review;
  if (!review || review.path !== path || typeof review.branch !== 'string' || typeof review.repo_root !== 'string' || typeof review.session_id !== 'string' || !['remove', 'unlink'].includes(review.mode) || typeof review.dirty !== 'boolean' || typeof review.ignored_files !== 'boolean' || typeof review.head !== 'string' || !review.head || typeof review.base_head !== 'string' || !review.base_head || typeof review.changes_digest !== 'string' || !review.changes_digest || !Number.isInteger(review.checkpoints) || review.checkpoints < 0 || !Array.isArray(review.shared_ids) || review.shared_ids.some((key) => typeof key !== 'string') || !Array.isArray(value.shared_with) || value.shared_with.some((item) => !item || typeof item.id !== 'string' || typeof item.name !== 'string') || typeof value.blocked_reason !== 'string') throw new Error('Removal review was incomplete.');
  if (JSON.stringify([...review.shared_ids].sort()) !== JSON.stringify(value.shared_with.map((item) => item.id).sort()) || (review.mode === 'unlink') !== Boolean(review.shared_ids.length)) throw new Error('Removal sharing details did not match.');
  return value;
}
export function useWorktreeRemovalReview(id: string, path: string, active: boolean) {
  const synchronized = useAppSelector((state) => state.connection.status === 'connected' && state.connection.expectedSeq !== null && !state.connection.awaitingResync);
  const reconnect = useAppSelector((state) => state.connection.reconnectCount);
  const resync = useAppSelector((state) => state.connection.resyncCount);
  const [revision, setRevision] = useState(0);
  const [slot, setSlot] = useState<{ scope: string; request: string; value: RemovalPreview | null; status: 'loading' | 'ready' | 'error'; error: string } | null>(null);
  const scope = JSON.stringify([id, path]);
  const enabled = active && synchronized && Boolean(id && path);
  const request = JSON.stringify([scope, enabled, reconnect, resync, revision]);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController(); let disposed = false;
    const update = (status: 'loading' | 'ready' | 'error', error = '', value?: RemovalPreview) => {
      if (!disposed) setSlot((previous) => ({ scope, request, status, error, value: value ?? (previous?.scope === scope ? previous.value : null) }));
    };
    update('loading');
    const timer = window.setTimeout(() => { update('error', 'Removal review timed out. Refresh to retry.'); controller.abort(); }, 30_000);
    void readCommand({ cmd: 'worktree_remove_preview', id, expected_worktree_path: path }, controller.signal).then((frame) => {
      if (!disposed && !controller.signal.aborted) update('ready', '', validate(frame, id, path));
    }).catch((cause: unknown) => { if (!disposed && !controller.signal.aborted) update('error', cause instanceof Error ? cause.message : 'Removal review failed.'); }).finally(() => window.clearTimeout(timer));
    return () => { disposed = true; controller.abort(); window.clearTimeout(timer); };
  }, [id, path, scope, request, enabled]);
  const current = slot?.scope === scope && slot.request === request;
  return { preview: slot?.scope === scope ? slot.value : null, ready: enabled && current && slot.status === 'ready', error: current ? slot.error : '', refresh: () => setRevision((value) => value + 1) };
}
