import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectConnection } from '../../app/store';
import { Button, ModalDialog } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { settingsRequest } from '../control/settingsRequests';
import { record, text } from './model';
import styles from './Attention.module.css';

export function BehaviorReview(props: { proposalId: string; onClose: () => void }) {
  return <ProposalReview key={props.proposalId} {...props} />;
}
function ProposalReview({ proposalId, onClose }: { proposalId: string; onClose: () => void }) {
  const dispatch = useAppDispatch(); const connection = useAppSelector(selectConnection);
  const [loaded, setLoaded] = useState<{ key: string; frame: UnknownRecord } | null>(null);
  const [settled, setSettled] = useState({ key: '', error: '' }); const [refresh, setRefresh] = useState(0);
  const [note, setNote] = useState(''); const [decisionError, setDecisionError] = useState(''); const [blocked, setBlocked] = useState(false);
  const [pending, setPending] = useState(false); const [outcome, setOutcome] = useState('');
  const owner = useRef<AbortController | null>(null); const reader = useRef<AbortController | null>(null);
  const loadKey = JSON.stringify([proposalId, connection.status, connection.reconnectCount, refresh]);
  const frame = loaded?.frame ?? {}; const proposal = record(frame.proposal);
  const readError = settled.key === loadKey ? settled.error : ''; const error = decisionError || readError;
  const ready = connection.status === 'connected' && loaded?.key === loadKey && settled.key === loadKey && !readError && !blocked;
  const actionable = ready && text(proposal.next_actor_kind) === 'user' && ['proposed', 'approved'].includes(text(proposal.status));
  useEffect(() => () => { owner.current?.abort(); owner.current = null; }, []);
  useEffect(() => {
    if (connection.status !== 'connected' || owner.current || outcome) return;
    const controller = new AbortController(); reader.current = controller;
    void settingsRequest({ cmd: 'behavior_overlay_diff', proposal_id: proposalId }, controller.signal, false, 'Behavior review').then((result) => {
      if (controller.signal.aborted) return;
      const received = record(result.proposal);
      if (result.type !== 'behavior_overlay_diff' || received.id !== proposalId || typeof result.diff !== 'string' || typeof received.base_version_id !== 'string' || !text(received.proposed_text_sha256)) throw new Error('The response did not contain this proposal’s diff.');
      setLoaded({ key: loadKey, frame: result }); setSettled({ key: loadKey, error: '' }); setDecisionError(''); setBlocked(false);
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setSettled({ key: loadKey, error: cause instanceof Error ? cause.message : 'Could not load the behavior diff.' }); });
    return () => { controller.abort(); if (reader.current === controller) reader.current = null; };
  }, [proposalId, loadKey, connection.status, outcome]);
  const decide = async (approve: boolean) => {
    if (!actionable || owner.current || outcome || !text(proposal.proposed_text_sha256)) return;
    const controller = new AbortController(); owner.current = controller; reader.current?.abort(); setPending(true); setDecisionError('');
    try {
      const result = await settingsRequest({ cmd: approve ? 'behavior_overlay_user_approve' : 'behavior_overlay_user_reject', proposal_id: proposalId, expected_proposed_text_sha256: text(proposal.proposed_text_sha256), expected_base_version_id: text(proposal.base_version_id), note: note.trim() }, controller.signal, true, 'Behavior decision');
      if (controller.signal.aborted || owner.current !== controller) return;
      const received = record(result.proposal);
      if (result.type !== 'behavior_overlay_proposal' || text(result.proposal_id) !== proposalId || received.id !== proposalId || received.status !== (approve ? 'applied' : 'rejected') || ['base_version_id', 'proposed_text_sha256', 'scope_kind', 'scope_group', 'scope_key'].some((field) => received[field] !== proposal[field])) throw new Error('Could not confirm the decision; its outcome is unknown. Reload the diff to check the proposal.');
      dispatch(projectionActions.auxiliaryResourceReceived(result));
      setLoaded((current) => current ? { ...current, frame: { ...current.frame, proposal: { ...record(current.frame.proposal), ...received } } } : current);
      setOutcome(approve ? 'Behavior change approved.' : 'Behavior change rejected.');
    } catch (cause: unknown) {
      if (!controller.signal.aborted && owner.current === controller) { setDecisionError(cause instanceof Error ? cause.message : 'Could not confirm the decision.'); setBlocked(true); }
    } finally { if (owner.current === controller) { owner.current = null; setPending(false); } }
  };
  const warnings = Array.isArray(proposal.lint_warnings) ? proposal.lint_warnings.map(record) : [];
  return <ModalDialog title="Review behavior diff" description={proposalId} size="large" isOpen onOpenChange={(open) => { if (!open) onClose(); }}>
    <section className={styles.review} aria-busy={pending}>
      {error ? <p role="alert">{error}</p> : null}
      {!ready && !error && !outcome ? <p role="status">{connection.status === 'connected' ? 'Loading behavior diff…' : 'Reconnect to refresh this behavior review.'}</p> : null}
      {loaded ? <>
        <dl>{[['Target', [text(proposal.scope_group), text(proposal.scope_kind), text(proposal.scope_key) || text(proposal.agent_id)].filter(Boolean).join(' / ')], ['Author', [text(proposal.proposed_by_kind), text(proposal.proposed_by_agent_id)].filter(Boolean).join(' · ')], ['Base version', text(proposal.base_version_id) || 'Initial version'], ['Proposed text hash', text(proposal.proposed_text_sha256)], ['Status', `${text(proposal.status)} · next ${text(proposal.next_actor_kind) || 'none'}`]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        <h3>Rationale</h3><p>{text(proposal.rationale) || 'No rationale provided.'}</p>
        <h3>Advisory lint</h3>{warnings.length ? <ul>{warnings.map((warning, index) => <li key={index}><strong>{text(warning.code)}</strong> {text(warning.message)}{text(warning.excerpt) ? <pre>{text(warning.excerpt)}</pre> : null}</li>)}</ul> : <p>{Number(proposal.lint_warning_count) > 0 ? `${Number(proposal.lint_warning_count)} warnings; details unavailable.` : 'No warnings reported.'}</p>}
        {!ready && !outcome ? <p>Previous diff retained. Reload or reconnect and inspect the current proposal before deciding.</p> : null}<h3>Proposed changes</h3><pre aria-label="Behavior diff" className={styles.diff}>{String(frame.diff) || 'No text changes.'}</pre>
        {ready && !actionable && !outcome ? <p>This proposal is not awaiting an operator decision.</p> : null}
      </> : null}
      <label>Review note<textarea value={note} disabled={pending || Boolean(outcome)} onChange={(event) => setNote(event.target.value)} /></label>
      {outcome ? <p role="status">{outcome}</p> : null}
      <footer><Button onPress={onClose}>Close review</Button><Button isDisabled={pending || Boolean(outcome)} onPress={() => setRefresh((value) => value + 1)}>Reload diff</Button><Button tone="danger" isDisabled={!actionable || pending || Boolean(outcome)} onPress={() => { void decide(false); }}>Reject behavior change</Button><Button tone="primary" isDisabled={!actionable || !text(proposal.proposed_text_sha256) || pending || Boolean(outcome)} onPress={() => { void decide(true); }}>Approve behavior change</Button></footer>
    </section>
  </ModalDialog>;
}
