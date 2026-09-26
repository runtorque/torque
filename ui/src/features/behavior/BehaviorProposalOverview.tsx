import { useEffect, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { BehaviorReview } from '../attention/BehaviorReview';
import { settingsRequest } from '../control/settingsRequests';
import { BehaviorProposalSummary } from './BehaviorGuidance';
import { items, record, text } from './model';
// Preserve discovery of group-wide pending reviews before a target is chosen.
// It is a separately owned, explicit read, never a latest-frame fallback.
export function BehaviorProposalOverview({ group, projected }: { group: string; projected: unknown }) {
  const connection = useAppSelector(selectConnection); const [request, setRequest] = useState(0); const [rows, setRows] = useState<UnknownRecord[] | null>(null); const [error, setError] = useState(''); const [settled, setSettled] = useState(''); const [selected, setSelected] = useState('');
  const key = JSON.stringify([request, connection.status, connection.reconnectCount]); const pending = request > 0 && connection.status === 'connected' && settled !== key;
  useEffect(() => {
    if (!request || connection.status !== 'connected') return;
    const controller = new AbortController();
    void settingsRequest({ cmd: 'behavior_overlay_proposals', group, limit: 200 }, controller.signal, false, 'Behavior proposals').then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'behavior_overlay_proposals' || !Array.isArray(frame.proposals)) throw new Error('The group proposal list is invalid.');
      setRows(items(frame.proposals).filter((row) => row.scope_group === group)); setError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not load group proposals.'); }).finally(() => { if (!controller.signal.aborted) setSettled(key); });
    return () => controller.abort();
  }, [group, request, key, connection.status]);
  const queue = new Map((rows ?? []).map((row) => [text(row.id), row]));
  Object.values(record(projected)).map(record).filter((row) => row.scope_group === group).forEach((row) => { const previous = queue.get(text(row.id)); if (!previous || Number(row.updated_at ?? 0) >= Number(previous.updated_at ?? 0)) queue.set(text(row.id), row); });
  const open = [...queue.values()].filter((row) => ['proposed', 'approved'].includes(text(row.status)));
  return <section aria-label="Group behavior proposals"><h3>Group approval queue</h3><Button isDisabled={pending && connection.status === 'connected'} onPress={() => setRequest((value) => value + 1)}>Refresh proposals</Button>{pending && connection.status === 'connected' ? <p role="status">Loading group proposals…</p> : null}{error && !pending ? <p role="alert">{error}</p> : null}{rows ? open.length ? open.map((row) => <article key={text(row.id)}><strong>{text(row.scope_kind)} / {text(row.scope_key)}</strong><p>{text(row.rationale) || 'No rationale.'}</p><p>{text(row.status)} · next {text(row.next_actor_kind) || 'none'}</p><BehaviorProposalSummary proposal={row} /><Button onPress={() => setSelected(text(row.id))}>Review behavior diff</Button></article>) : <p>No open proposals in this group.</p> : <p>Refresh proposals to discover pending reviews before choosing a target.</p>}{selected ? <BehaviorReview proposalId={selected} onClose={() => { setSelected(''); setRequest((value) => value + 1); }} /> : null}</section>;
}
