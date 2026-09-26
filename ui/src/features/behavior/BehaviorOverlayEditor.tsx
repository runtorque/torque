import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions } from '../../app/store';
import { Button, StateSurface } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { BehaviorReview } from '../attention/BehaviorReview';
import { settingsRequest } from '../control/settingsRequests';
import { behaviorActions } from './session';
import { draftDiff, matchesScope, record, scopeArgs, scopeKey, text, type Scope } from './model';
import { useBehaviorScope } from './useBehaviorScope';
import { BehaviorProposalOverview } from './BehaviorProposalOverview';
import { BehaviorVersionHistory } from './BehaviorVersionHistory';
import { BehaviorHistoryReview } from './BehaviorHistoryReview';
import styles from './BehaviorOverlayEditor.module.css';
export function BehaviorOverlayEditor({ group, active, proposals, agents, refreshVersion = 0 }: { group: string; active: unknown; proposals: unknown; agents: UnknownRecord[]; refreshVersion?: number }) {
  const dispatch = useAppDispatch(); const selected = useAppSelector((state) => state.behaviorSession.selections[group]);
  const kind = selected?.kind ?? 'agent'; const target = selected?.target ?? ''; const scope = { group, kind, target };
  const targets = agents.filter((agent) => ['architect', 'engineer'].includes(text(agent.kind)));
  const valid = !!group && !!target && (kind === 'role' || targets.some((agent) => text(agent.id) === target));
  const activeRows = Object.values(record(active)).map(record); const activeVersion = text(activeRows.find((row) => matchesScope(row, scope))?.active_version_id);
  return <section className={styles.root} aria-label="Dynamic Behavior">
    <header><h2>Dynamic Behavior</h2><p>Inspect scoped behavior, preview edits and submit a proposal for its required review.</p></header>
    <div className={styles.scope}><label>Behavior scope<select value={kind} onChange={(event) => dispatch(behaviorActions.select({ group, kind: event.target.value as Scope['kind'], target: '' }))}><option value="agent">Agent</option><option value="role">Role</option></select></label><label>Behavior target<select value={target} onChange={(event) => dispatch(behaviorActions.select({ group, kind, target: event.target.value }))}><option value="">Choose…</option>{kind === 'agent' ? targets.map((agent) => <option key={text(agent.id)} value={text(agent.id)}>{text(agent.name) || text(agent.id)}</option>) : ['architect', 'engineer', 'worker'].map((role) => <option key={role} value={role}>{role}</option>)}</select></label></div>
    {valid ? <ScopeEditor key={scopeKey(scope)} scope={scope} activeVersion={activeVersion} proposals={proposals} refreshVersion={refreshVersion} /> : <><StateSurface title="Choose a behavior target" description="Select an agent or a group role to load its current text and review queue." /><BehaviorProposalOverview key={group} group={group} projected={proposals} /></>}
  </section>;
}
function ScopeEditor({ scope, activeVersion, proposals, refreshVersion }: { scope: Scope; activeVersion: string; proposals: unknown; refreshVersion: number }) {
  const dispatch = useAppDispatch(); const key = scopeKey(scope); const draft = useAppSelector((state) => state.behaviorSession.drafts[key]); const read = useBehaviorScope(scope, activeVersion, refreshVersion);
  const [preview, setPreview] = useState(false); const [selectedProposal, setSelectedProposal] = useState(''); const [selectedVersion, setSelectedVersion] = useState('');
  const [pending, setPending] = useState(false); const [error, setError] = useState(''); const owner = useRef<AbortController | null>(null);
  useEffect(() => () => { owner.current?.abort(); owner.current = null; }, []);
  const base = text(record(read.accepted?.read.version).id); const signature = JSON.stringify([base, draft?.text ?? '', draft?.rationale ?? '']); const submitted = draft?.submitted?.signature === signature ? draft.submitted : null;
  const queue = new Map((read.accepted?.proposals ?? []).map((row) => [text(row.id), row]));
  Object.values(record(proposals)).map(record).filter((row) => matchesScope(row, scope)).forEach((row) => { const previous = queue.get(text(row.id)); if (!previous || Number(row.updated_at ?? 0) >= Number(previous.updated_at ?? 0)) queue.set(text(row.id), row); });
  const openProposals = [...queue.values()].filter((row) => ['proposed', 'approved'].includes(text(row.status))).sort((a, b) => Number(b.updated_at ?? b.created_at ?? 0) - Number(a.updated_at ?? a.created_at ?? 0));
  const submit = async () => {
    if (!read.ready || !draft || submitted || owner.current) return;
    const controller = new AbortController(); owner.current = controller; setPending(true); setError('');
    const token = draft.attempt?.signature === signature ? draft.attempt.key : crypto.randomUUID(); dispatch(behaviorActions.attempt({ key, signature, token }));
    try {
      const frame = await settingsRequest({ cmd: 'behavior_overlay_propose', ...scopeArgs(scope), proposed_by_kind: 'user', proposed_by_agent_id: 'user', proposal_type: 'set_text', text: draft.text, rationale: draft.rationale, expected_base_version_id: base, idempotency_key: token }, controller.signal, true, 'Behavior proposal');
      if (controller.signal.aborted || owner.current !== controller) return;
      const proposal = record(frame.proposal);
      if (frame.type !== 'behavior_overlay_proposal' || !text(frame.proposal_id) || frame.proposal_id !== proposal.id || !matchesScope(proposal, scope) || proposal.base_version_id !== base) throw new Error('The proposal acknowledgement did not match this scope and base version; its outcome is unknown. Reload the scope before retrying.');
      dispatch(projectionActions.auxiliaryResourceReceived(frame)); dispatch(behaviorActions.submitted({ key, signature, id: text(proposal.id) })); read.refresh();
    } catch (cause: unknown) { if (!controller.signal.aborted && owner.current === controller) { setError(cause instanceof Error ? cause.message : 'Proposal could not be confirmed.'); read.refresh(); } }
    finally { if (owner.current === controller) { owner.current = null; setPending(false); } }
  };
  const change = (field: 'text' | 'rationale', value: string) => { dispatch(behaviorActions.edit({ key, field, value })); setError(''); };
  return <>
    <div className={styles.toolbar}><span>{scope.group} / {scope.kind} / {scope.target}</span><Button isDisabled={read.pending || pending} onPress={read.refresh}>Reload behavior scope</Button></div>
    {read.pending ? <p role="status">{read.accepted ? 'Refreshing behavior scope…' : 'Loading behavior scope…'}</p> : null}
    {read.error ? <p role="alert">{read.error} <Button onPress={read.refresh}>Retry behavior scope</Button></p> : null}
    {!read.ready && !read.pending && !read.error ? <p role="status">Reconnect to refresh this behavior scope.</p> : null}
    {read.accepted && draft ? <div className={styles.columns}><section><h3>Behavior draft</h3><p>Base version: {base}</p><label>Behavior instructions<textarea disabled={pending} value={draft.text} onChange={(event) => change('text', event.target.value)} /></label><label>Proposal rationale<input disabled={pending} value={draft.rationale} onChange={(event) => change('rationale', event.target.value)} /></label>
      <div className={styles.toolbar}><Button onPress={() => setPreview((value) => !value)} aria-expanded={preview}>{preview ? 'Hide draft preview' : 'Preview draft diff'}</Button><Button tone="primary" isDisabled={!read.ready || pending || !!submitted} onPress={() => { void submit(); }}>{pending ? 'Submitting proposal…' : 'Propose change'}</Button></div>
      {preview ? <pre className={styles.diff} aria-label="Behavior draft diff">{draftDiff(text(read.accepted.read.text), draft.text) || 'No text changes.'}</pre> : null}
      {error ? <p role="alert">{error}</p> : null}{submitted ? <p role="status">Proposal submitted: {submitted.id}. Required review is still separate.</p> : null}
      <BehaviorVersionHistory versions={read.accepted.versions} activeId={base} ready={read.ready && !pending} onInspect={setSelectedVersion} />
    </section><section><h3>Approval queue</h3>{openProposals.length ? openProposals.map((proposal) => <article key={text(proposal.id)}><strong>{text(proposal.id)}</strong><p>{text(proposal.status)} · next {text(proposal.next_actor_kind) || 'none'}</p><p>{text(proposal.rationale) || 'No rationale.'}</p><Button onPress={() => setSelectedProposal(text(proposal.id))}>Review behavior diff</Button></article>) : <p>No open proposals for this scope.</p>}</section></div> : null}
    {selectedProposal ? <BehaviorReview key={selectedProposal} proposalId={selectedProposal} onClose={() => { setSelectedProposal(''); read.refresh(); }} /> : null}
    {selectedVersion ? <BehaviorHistoryReview key={selectedVersion} scope={scope} base={base} target={selectedVersion} scopeReady={read.ready} refreshScope={read.refresh} onClose={() => setSelectedVersion('')} /> : null}
  </>;
}
