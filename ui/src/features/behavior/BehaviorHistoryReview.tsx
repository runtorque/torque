import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectConnection } from '../../app/store';
import { Button, ModalDialog } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { settingsRequest } from '../control/settingsRequests';
import { VersionProvenance } from './BehaviorVersionHistory';
import { matchesScope, record, scopeArgs, scopeKey, text, type Scope } from './model';
import { behaviorActions } from './session';
import styles from './BehaviorOverlayEditor.module.css';

export function BehaviorHistoryReview({ scope, base, target, scopeReady, refreshScope, onClose }: { scope: Scope; base: string; target: string; scopeReady: boolean; refreshScope: () => void; onClose: () => void }) {
  const dispatch = useAppDispatch(); const connection = useAppSelector(selectConnection); const draftKey = JSON.stringify([scopeKey(scope), target]);
  const draft = useAppSelector((state) => state.behaviorSession.rollbacks[draftKey]); const rationale = draft?.rationale ?? 'Rollback requested from Behavior history';
  const [retry, setRetry] = useState(0); const [accepted, setAccepted] = useState<{ key: string; frame: UnknownRecord } | null>(null); const [settled, setSettled] = useState({ key: '', error: '' });
  const [pending, setPending] = useState(false); const [error, setError] = useState(''); const owner = useRef<AbortController | null>(null);
  const { group, kind, target: scopeTarget } = scope;
  const loadKey = JSON.stringify([group, kind, scopeTarget, base, target, scopeReady, connection.status, connection.reconnectCount, retry]);
  const signature = JSON.stringify([base, target, rationale]); const submitted = draft?.submitted?.signature === signature ? draft.submitted : null;
  const current = scopeReady && connection.status === 'connected' && settled.key === loadKey && !settled.error && accepted?.key === loadKey;
  useEffect(() => () => { owner.current?.abort(); owner.current = null; }, []);
  useEffect(() => {
    if (!scopeReady || connection.status !== 'connected') return;
    const controller = new AbortController(); const currentScope = { group, kind, target: scopeTarget };
    void settingsRequest({ cmd: 'behavior_overlay_diff', ...scopeArgs(currentScope), from_version_id: base, to_version_id: target }, controller.signal, false, 'Behavior version diff').then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'behavior_overlay_diff' || typeof frame.diff !== 'string' || record(frame.from_version).id !== base || record(frame.to_version).id !== target || !matchesScope(record(frame.from_version), currentScope) || !matchesScope(record(frame.to_version), currentScope)) throw new Error('The version diff did not match this scope and version pair. Reload before requesting rollback.');
      setAccepted({ key: loadKey, frame }); setSettled({ key: loadKey, error: '' });
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setSettled({ key: loadKey, error: cause instanceof Error ? cause.message : 'Version diff could not be loaded.' }); });
    return () => controller.abort();
  }, [group, kind, scopeTarget, base, target, scopeReady, connection.status, loadKey]);
  const reload = () => { setRetry((value) => value + 1); refreshScope(); };
  const submit = async () => {
    if (!current || base === target || submitted || owner.current) return;
    const controller = new AbortController(); owner.current = controller; setPending(true); setError('');
    const token = draft?.attempt?.signature === signature ? draft.attempt.key : crypto.randomUUID(); dispatch(behaviorActions.rollbackAttempt({ key: draftKey, signature, token, rationale }));
    try {
      const frame = await settingsRequest({ cmd: 'behavior_overlay_propose', ...scopeArgs(scope), proposed_by_kind: 'user', proposed_by_agent_id: 'user', proposal_type: 'rollback', target_version_id: target, expected_base_version_id: base, rationale, idempotency_key: token }, controller.signal, true, 'Behavior rollback');
      if (controller.signal.aborted || owner.current !== controller) return;
      const proposal = record(frame.proposal);
      if (frame.type !== 'behavior_overlay_proposal' || !text(frame.proposal_id) || frame.proposal_id !== proposal.id || !matchesScope(proposal, scope) || proposal.base_version_id !== base || proposal.target_version_id !== target || proposal.proposal_type !== 'rollback') throw new Error('The rollback acknowledgement did not match this scope and version pair; its outcome is unknown. Reload before retrying.');
      dispatch(projectionActions.auxiliaryResourceReceived(frame)); dispatch(behaviorActions.rollbackSubmitted({ key: draftKey, signature, id: text(proposal.id), nextActor: text(proposal.next_actor_kind) })); refreshScope();
    } catch (cause: unknown) { if (!controller.signal.aborted && owner.current === controller) { setError(cause instanceof Error ? cause.message : 'Rollback could not be confirmed.'); setRetry((value) => value + 1); refreshScope(); } }
    finally { if (owner.current === controller) { owner.current = null; setPending(false); } }
  };
  return <ModalDialog title="Review behavior version" description={`${group} / ${kind} / ${scopeTarget}`} size="large" isOpen onOpenChange={(open) => { if (!open) onClose(); }}>
    <section className={styles.historyReview} aria-busy={pending}>
      <p>Current base: {base}</p><p>Selected version: {target}{base === target ? ' · Active' : ''}</p>
      {settled.key === loadKey && settled.error ? <p role="alert">{settled.error}</p> : !current ? <p role="status">{scopeReady && connection.status === 'connected' ? 'Refreshing version diff…' : 'Waiting for a fresh behavior scope. Reload or reconnect before requesting rollback.'}</p> : null}
      {accepted ? <><details><summary>Selected version provenance</summary><VersionProvenance version={record(accepted.frame.to_version)} /></details><h3>Changes from current base to selected version</h3>{!current ? <p>Previous diff retained while refreshing. Rollback is unavailable until the current comparison loads.</p> : null}<pre className={styles.diff} aria-label="Historical behavior diff">{text(accepted.frame.diff) || 'No text changes.'}</pre></> : null}
      {base !== target ? <><label>Rollback rationale<textarea value={rationale} disabled={pending || !!submitted} onChange={(event) => { dispatch(behaviorActions.rollbackEdit({ key: draftKey, rationale: event.target.value })); setError(''); }} /></label><p>Requesting rollback proposes the selected historical text against the current base shown above. The required approval remains separate; this request does not apply it.</p></> : <p>This is the active version. Choose a historical version to request rollback.</p>}
      {error ? <p role="alert">{error}</p> : null}{submitted ? <p role="status">Rollback proposal submitted: {submitted.id}. Next reviewer: {submitted.nextActor || 'see approval queue'}.</p> : null}
      <footer><Button onPress={onClose}>Close version review</Button><Button isDisabled={pending} onPress={reload}>Reload version comparison</Button>{base !== target ? <Button tone="primary" isDisabled={!current || pending || !!submitted} onPress={() => { void submit(); }}>{pending ? 'Requesting rollback…' : 'Confirm rollback request'}</Button> : null}</footer>
    </section>
  </ModalDialog>;
}
