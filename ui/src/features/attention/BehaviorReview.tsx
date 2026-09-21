import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectConnection } from '../../app/store';
import { Button, ModalDialog } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { record, text } from './model';
import styles from './Attention.module.css';

export function BehaviorReview({ proposalId, onClose }: { proposalId: string; onClose: () => void }) {
  const dispatch = useAppDispatch();
  const reconnect = useAppSelector(selectConnection).reconnectCount;
  const [loaded, setLoaded] = useState<{ key: string; frame: UnknownRecord } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState('');
  const busy = useRef(false);
  const loadKey = `${proposalId}:${reconnect}:${refresh}`;
  const frame = loaded?.key === loadKey ? loaded.frame : {};
  const proposal = record(frame.proposal);
  const ready = proposal.id === proposalId && typeof frame.diff === 'string';
  const actionable = ready && text(proposal.next_actor_kind) === 'user' && ['proposed', 'approved'].includes(text(proposal.status));
  useEffect(() => {
    const controller = new AbortController();
    void readCommand({ cmd: 'behavior_overlay_diff', proposal_id: proposalId }, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (record(result.proposal).id !== proposalId || typeof result.diff !== 'string') throw new Error('The response did not contain this proposal’s diff.');
      setLoaded({ key: loadKey, frame: result }); setError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not load the behavior diff.'); });
    return () => controller.abort();
  }, [proposalId, loadKey]);
  const decide = async (approve: boolean) => {
    if (!actionable || busy.current || outcome || (approve && !text(proposal.proposed_text_sha256))) return;
    busy.current = true; setPending(true); setError('');
    try {
      const result = await readCommand({ cmd: approve ? 'behavior_overlay_user_approve' : 'behavior_overlay_user_reject', proposal_id: proposalId, expected_proposed_text_sha256: text(proposal.proposed_text_sha256), expected_base_version_id: text(proposal.base_version_id), note: note.trim() }, new AbortController().signal);
      if (result.type !== 'behavior_overlay_proposal' || text(result.proposal_id) !== proposalId) throw new Error('Could not confirm the decision. Reload the diff to check the proposal.');
      dispatch(projectionActions.auxiliaryResourceReceived(result));
      setLoaded((current) => current?.key === loadKey ? {
        ...current, frame: { ...current.frame, proposal: { ...record(current.frame.proposal), ...record(result.proposal) } },
      } : current);
      setOutcome(approve ? 'Behavior change approved.' : 'Behavior change rejected.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not confirm the decision.');
      setLoaded(null); // A retry requires a fresh rendered diff; preserve the operator’s note.
    } finally { busy.current = false; setPending(false); }
  };
  const warnings = Array.isArray(proposal.lint_warnings) ? proposal.lint_warnings.map(record) : [];
  return <ModalDialog title="Review behavior diff" description={proposalId} size="large" isOpen onOpenChange={(open) => { if (!open && !busy.current) onClose(); }}>
    <section className={styles.review} aria-busy={pending}>
      {error ? <p role="alert">{error}</p> : null}
      {!ready && !error ? <p role="status">Loading behavior diff…</p> : null}
      {ready ? <>
        <dl>{[['Target', [text(proposal.scope_group), text(proposal.scope_kind), text(proposal.scope_key) || text(proposal.agent_id)].filter(Boolean).join(' / ')], ['Author', [text(proposal.proposed_by_kind), text(proposal.proposed_by_agent_id)].filter(Boolean).join(' · ')], ['Base version', text(proposal.base_version_id) || 'Initial version'], ['Proposed text hash', text(proposal.proposed_text_sha256)], ['Status', `${text(proposal.status)} · next ${text(proposal.next_actor_kind) || 'none'}`]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        <h3>Rationale</h3><p>{text(proposal.rationale) || 'No rationale provided.'}</p>
        <h3>Advisory lint</h3>{warnings.length ? <ul>{warnings.map((warning, index) => <li key={index}><strong>{text(warning.code)}</strong> {text(warning.message)}{text(warning.excerpt) ? <pre>{text(warning.excerpt)}</pre> : null}</li>)}</ul> : <p>{Number(proposal.lint_warning_count) > 0 ? `${Number(proposal.lint_warning_count)} warnings; details unavailable.` : 'No warnings reported.'}</p>}
        <h3>Proposed changes</h3><pre aria-label="Behavior diff" className={styles.diff}>{String(frame.diff) || 'No text changes.'}</pre>
        {!actionable && !outcome ? <p>This proposal is not awaiting an operator decision.</p> : null}
      </> : null}
      <label>Review note<textarea value={note} disabled={pending || Boolean(outcome)} onChange={(event) => setNote(event.target.value)} /></label>
      {outcome ? <p role="status">{outcome}</p> : null}
      <footer><Button isDisabled={pending} onPress={onClose}>Close review</Button><Button isDisabled={pending || Boolean(outcome)} onPress={() => setRefresh((value) => value + 1)}>Reload diff</Button><Button tone="danger" isDisabled={!actionable || pending || Boolean(outcome)} onPress={() => { void decide(false); }}>Reject behavior change</Button><Button tone="primary" isDisabled={!actionable || !text(proposal.proposed_text_sha256) || pending || Boolean(outcome)} onPress={() => { void decide(true); }}>Approve behavior change</Button></footer>
    </section>
  </ModalDialog>;
}

export function BehaviorVersionReview({ command, onClose }: { command: TorqueCommand; onClose: () => void }) {
  const [diff, setDiff] = useState<string | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void readCommand(command, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'behavior_overlay_diff' || typeof frame.diff !== 'string') throw new Error('Version diff was not returned.');
      setDiff(frame.diff);
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not load version diff.'); });
    return () => controller.abort();
  }, [command]);
  return <ModalDialog title="Behavior version diff" isOpen size="large" onOpenChange={(open) => { if (!open) onClose(); }}><section className={styles.review}>{error ? <p role="alert">{error}</p> : diff === null ? <p role="status">Loading version diff…</p> : <pre className={styles.diff}>{diff || 'No text changes.'}</pre>}<Button onPress={onClose}>Close review</Button></section></ModalDialog>;
}
