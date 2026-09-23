import { useEffect, useRef, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectGroupsState } from '../../app/store';

import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { AgentViewModel } from './model';
import { WorktreeDiff } from './WorktreeDiff';
import { useDiffDisclosure } from './worktreeDiffModel';
import { useWorktreeReads } from './useWorktreeReads';
import { useWorktreeRemovalReview } from './useWorktreeRemovalReview';
import { useWorktreeMutation } from './useWorktreeMutation';
import styles from './AgentWorkspace.module.css';

interface WorktreeInspectorProps {
  agent: AgentViewModel | null;
  active?: boolean;
  responses: Record<string, unknown>;
  onClose: () => void;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function list(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

function number(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function WorktreeInspector({ agent, responses, onClose, active = true }: WorktreeInspectorProps) {
  const [tab, setTab] = useState<'diff' | 'history'>('diff');
  const [message, setMessage] = useState('');
  const [mergeEdits, setMergeEdits] = useState<Partial<{ close: boolean; remove: boolean; preserve: boolean }>>({});
  const [prReview, setPrReview] = useState<{ id: string; path: string; branch: string; base: string } | null>(null);
  const [clearContext, setClearContext] = useState(false);
  const [forceDirect, setForceDirect] = useState(false);
  const [removalTarget, setRemovalTarget] = useState<AgentViewModel | null>(null);
  const [rollbackSha, setRollbackSha] = useState('');
  const confirmationReturnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (active && !prReview && !rollbackSha && !removalTarget) {
      const opener = confirmationReturnFocus.current;
      confirmationReturnFocus.current = null;
      opener?.focus();
    }
  }, [active, prReview, rollbackSha, removalTarget]);
  const [operationAgent, setOperationAgent] = useState<AgentViewModel | null>(null);
  const target = agent ?? operationAgent;
  const groups = useAppSelector(selectGroupsState);
  const defaults = record(groups.settings[target?.group ?? '']);
  const cleanupMode = text(defaults.worktree_merge_cleanup) || 'keep';
  const closeAgent = mergeEdits.close ?? ['close', 'close_remove', 'auto_sweep'].includes(cleanupMode);
  const removeAfterMerge = mergeEdits.remove ?? ['remove', 'close_remove', 'auto_sweep'].includes(cleanupMode);
  const preserveDiff = mergeEdits.preserve ?? Boolean(defaults.worktree_merge_preserve_diff);

  const mutation = useWorktreeMutation();
  const blocked = mutation.pending || mutation.uncertain;
  const removalComplete = mutation.result.type === 'worktree_remove' && mutation.result.ok === true;
  const reads = useWorktreeReads(target?.id ?? '', target?.worktreePath ?? '', target?.worktreeBranch ?? '', active && Boolean(agent) && !blocked && !removalComplete);
  const removal = useWorktreeRemovalReview(removalTarget?.id ?? '', removalTarget?.worktreePath ?? '', active && Boolean(removalTarget && agent?.worktreePath) && !blocked && !removalComplete);
  const [progressAtStart, setProgressAtStart] = useState<unknown>(null);
  async function finish(operation: Promise<Record<string, unknown> | null>) {
    const result = await operation;
    if (!result) return;
    if (result.type === 'worktree_pr') setPrReview(null);
    if (result.type === 'worktree_rollback') setRollbackSha('');
    if (result.type !== 'worktree_remove') reads.refresh();
  }
  function run(command: Record<string, unknown>) {
    if (!agent || blocked || (command.cmd === 'worktree_remove' ? !removal.ready : !reads.ready)) return;
    if (command.cmd === 'worktree_merge') setMergeEdits({ close: closeAgent, remove: removeAfterMerge, preserve: preserveDiff });
    setOperationAgent(agent);
    setProgressAtStart(agent ? responses[`worktree_merge_progress:${agent.id}`] : null);
    void finish(mutation.run(command));
  }
  const { refresh } = reads;
  const diff = reads.diff.data;
  const preflight = reads.preflight.data;
  const history = reads.history.data;
  const preflightCurrent = reads.preflight.status === 'ready';
  const result = mutation.result.ok === false ? {} : mutation.result;
  const progressFrame = target ? responses[`worktree_merge_progress:${target.id}`] : null;
  const progress = record(progressFrame);
  const files = list(diff.files);
  const diffWorkspace = useDiffDisclosure(files);
  const commits = list(history.commits);
  const conflicts = list(preflight.conflicts);
  const stats = record(diff.stats);
  const mergeClean = preflight.clean === true;
  const mergeDirty = preflight.dirty === true;
  const stale = preflight.stale_base === true || Boolean(preflight.stale_base_warning);
  const activeProgress = mutation.pending && mutation.command === 'worktree_merge' && progressFrame !== progressAtStart ? text(progress.message) || text(progress.phase) : '';
  const resultMessage = text(result.message) || (result.pending ? 'Pull request is pending. Merge has not completed.' : result.type === 'worktree_rebase' ? 'Worktree rebased.' : result.type === 'worktree_merge' ? 'Merge completed.' : '');
  const resultUrl = text(result.url) || text(result.pr_url);
  const cleanupErrors = record(result.cleanup).errors;
  const defaultMessage = text(preflight.default_message);
  const effectiveMessage = message || defaultMessage;
  const artifacts = list(diff.artifacts);
  const branch = target?.worktreeBranch || text(diff.branch) || 'worktree branch';
  const base = text(target?.raw.worktree_base_branch) || text(diff.base_branch) || 'main';
  const prAvailable = Boolean(agent) && reads.ready && preflightCurrent && !mergeDirty && !preflight.error;
  const prTargetChanged = Boolean(prReview && (prReview.id !== agent?.id || prReview.path !== agent?.worktreePath || prReview.branch !== branch || prReview.base !== base));

  if (!target) return null;
  return <ModalDialog
    title={removalTarget ? removal.preview?.review.mode === 'unlink' ? 'Remove worktree link?' : 'Remove worktree?' : prReview ? 'Create pull request?' : rollbackSha ? 'Rollback worktree?' : `${target.name} worktree`}
    description={removalTarget ? removalTarget.name : rollbackSha ? rollbackSha.slice(0, 12) : prReview ? target.name : `${branch} → ${base}`}
    size={prReview || rollbackSha || removalTarget ? 'small' : 'large'}
    isOpen={active}
    onOpenChange={(open) => { if (!open && !blocked) { if (removalComplete) onClose(); else if (removalTarget) setRemovalTarget(null); else if (prReview) setPrReview(null); else if (rollbackSha) setRollbackSha(''); else onClose(); } }}
  >
    <div className={styles.worktreeInspector} hidden={Boolean(prReview || rollbackSha || removalTarget)}>
      <nav aria-label="Worktree views">
        <button aria-current={tab === 'diff' ? 'page' : undefined} onClick={() => setTab('diff')}>Changes</button>
        <button aria-current={tab === 'history' ? 'page' : undefined} onClick={() => setTab('history')}>History <span>{commits.length}</span></button>
        <Button tone="quiet" isDisabled={!reads.ready} onPress={refresh}>Refresh</Button>
      </nav>

      {!reads.ready && !blocked ? <p role="status">{!agent ? 'Agent is no longer available. The operation result and loaded content are retained.' : agent.worktreePath ? 'Waiting for a synchronized connection. Loaded worktree content is retained.' : 'This agent no longer has a worktree.'}</p> : null}
      {(['diff', 'preflight', 'history'] as const).map((key) => reads[key].status === 'error' ? <p role="alert" key={key}>{key === 'diff' ? 'Changes' : key === 'preflight' ? 'Merge preflight' : 'History'} refresh failed: {reads[key].error} <Button tone="quiet" isDisabled={!reads.ready} onPress={refresh}>Retry {key === 'diff' ? 'changes' : key === 'preflight' ? 'preflight' : 'history'}</Button></p> : null)}
      {reads.ready && [reads.diff, reads.preflight, reads.history].some((read) => read.status === 'loading') ? <p role="status">Refreshing worktree… Loaded content remains available.</p> : null}
      <div className={styles.worktreeTab} hidden={tab !== 'diff'}>
        <div className={styles.diffSummary}>
          <span>{files.length} files</span>
          <span className={styles.diffAdd}>+{number(stats.insertions)}</span>
          <span className={styles.diffDelete}>−{number(stats.deletions)}</span>
          {preflight.error ? <strong className={styles.diffDanger}>{text(preflight.error)}</strong>
            : mergeDirty ? <strong className={styles.diffWarning}>Checkpoint required</strong>
              : stale ? <strong className={styles.diffWarning}>Base branch is stale</strong>
                : conflicts.length ? <strong className={styles.diffDanger}>{conflicts.length} conflicts</strong>
                  : mergeClean && preflightCurrent ? <strong className={styles.diffSuccess}>Clean merge</strong>
                    : <strong>{reads.preflight.status === 'error' ? 'Merge preflight unavailable' : reads.preflight.status === 'waiting' ? 'Preflight is out of date' : 'Checking merge readiness…'}</strong>}
        </div>
        {text(diff.error) ? <StateSurface title="Unable to load diff" description={text(diff.error)} tone="danger" /> : null}
        {text(preflight.stale_base_warning) ? <pre className={styles.worktreeWarning}>{text(preflight.stale_base_warning)}</pre> : null}
        {conflicts.length ? <section className={styles.conflictList}><h3>Conflicts</h3>{conflicts.map((conflict, index) => <div key={`${text(conflict.path)}-${index}`}><strong>{text(conflict.path) || 'Unknown path'}</strong><span>{text(conflict.reason)}</span></div>)}</section> : null}
        {artifacts.length ? <section className={styles.worktreeArtifacts}><h3>Boundary artifacts</h3>{artifacts.map((artifact, index) => <div key={`${text(artifact.id)}-${index}`}><strong>{text(artifact.name) || text(artifact.title) || `Artifact ${index + 1}`}</strong><span>{text(artifact.path) || text(artifact.url)}</span></div>)}</section> : null}
        <div>
          {!Object.keys(diff).length ? <StateSurface title={!agent?.worktreePath ? 'Worktree unavailable' : reads.diff.status === 'error' ? 'Changes unavailable' : 'Loading changes'} description={!agent?.worktreePath ? 'No further worktree reads can be made for this agent.' : reads.diff.status === 'error' ? 'Retry the Changes read above.' : 'Torque is building the complete worktree diff.'} /> : null}
          {Object.keys(diff).length && !files.length && !diff.error ? <StateSurface title="No changes" description="The worktree matches its base branch." /> : null}

        </div>
        <WorktreeDiff files={files} workspace={diffWorkspace} />
      </div><div className={styles.worktreeHistory} hidden={tab !== 'history'}>
        {!Object.keys(history).length ? <StateSurface title={!agent?.worktreePath ? 'Worktree unavailable' : reads.history.status === 'error' ? 'History unavailable' : 'Loading history'} description={!agent?.worktreePath ? 'No further worktree reads can be made for this agent.' : reads.history.status === 'error' ? 'Retry the History read above.' : 'Torque is loading worktree checkpoints.'} /> : null}
        {Object.keys(history).length && !commits.length ? <StateSurface title="No checkpoints" description="This branch has no worktree checkpoints yet." /> : null}
        {commits.map((commit, index) => <article key={text(commit.sha) || String(index)}>
          <div><strong>{text(commit.message) || text(commit.short_sha)}</strong><small>{text(commit.short_sha)} · {text(commit.date)} · +{number(commit.insertions)} −{number(commit.deletions)}</small>{text(commit.body) ? <p>{text(commit.body)}</p> : null}</div>
          {index === 0 ? <span>HEAD</span> : <Button tone="quiet" isDisabled={!reads.ready || blocked} onPress={(event) => { confirmationReturnFocus.current = event.target instanceof HTMLElement ? event.target : null; setRollbackSha(text(commit.sha)); }}>Rollback…</Button>}
        </article>)}
      </div>

      {mutation.pending ? <p role="status">Worktree operation in progress…</p> : null}
      {mutation.error && !prReview && !rollbackSha && !removalTarget ? <div role="alert" className={styles.worktreeResult}>{mutation.error}{mutation.uncertain ? <Button isDisabled={mutation.pending} onPress={() => { void finish(mutation.retry()); }}>Retry operation</Button> : null}</div> : null}
      {activeProgress ? <div role="status" className={styles.worktreeResult}>{activeProgress}</div> : null}
      {resultMessage ? <div role="status" className={styles.worktreeResult}>{resultMessage}</div> : null}
      {resultUrl ? <div className={styles.worktreeResult}>Pull request: <a href={resultUrl} target="_blank" rel="noreferrer">{resultUrl}</a></div> : null}
      {result.warning ? <div role="alert">{text(result.warning)}</div> : null}
      {Array.isArray(cleanupErrors) && cleanupErrors.length ? <div role="alert">Cleanup needs attention: {cleanupErrors.map(text).join('; ')}</div> : null}

      <section className={styles.mergeControls}>
        <p>Cleanup options run only after the merge completes, not when a pull request is created.</p>
        <label>Merge message<textarea disabled={blocked} rows={2} value={effectiveMessage} onChange={(event) => setMessage(event.target.value)} placeholder="Commit or pull-request message" /></label>
        <div className={styles.checkGrid}>
          <label className={styles.inlineCheck}><input type="checkbox" disabled={blocked} checked={closeAgent} onChange={(event) => setMergeEdits((current) => ({ ...current, close: event.target.checked }))} />Close agent after merge</label>
          <label className={styles.inlineCheck}><input type="checkbox" disabled={blocked} checked={removeAfterMerge} onChange={(event) => setMergeEdits((current) => ({ ...current, remove: event.target.checked }))} />Delete worktree after merge</label>
          <label className={styles.inlineCheck}><input type="checkbox" disabled={blocked} checked={preserveDiff} onChange={(event) => setMergeEdits((current) => ({ ...current, preserve: event.target.checked }))} />Preserve boundary diff</label>
          <label className={styles.inlineCheck}><input type="checkbox" disabled={blocked} checked={clearContext} onChange={(event) => setClearContext(event.target.checked)} />Clear context after merge</label>
          <label className={styles.inlineCheck}><input type="checkbox" disabled={blocked} checked={forceDirect} onChange={(event) => setForceDirect(event.target.checked)} />Force direct local merge</label>
        </div>
      </section>

      <footer className={styles.worktreeFooter}>
        <Button tone="danger" isDisabled={!reads.ready || blocked} onPress={(event) => { if (mutation.reset()) { confirmationReturnFocus.current = event.target instanceof HTMLElement ? event.target : null; setRemovalTarget(target); } }}>Delete worktree…</Button>
        <span />
        <Button tone="quiet" isDisabled={!reads.ready || blocked} onPress={() => run({ cmd: 'worktree_checkpoint', id: target.id })}>Checkpoint</Button>
        {(stale || conflicts.length) ? <Button tone="quiet" isDisabled={!reads.ready || blocked} onPress={() => run({ cmd: 'worktree_rebase', id: target.id })}>Rebase onto base</Button> : null}
        <Button tone="quiet" onPress={(event) => { if (mutation.reset()) { confirmationReturnFocus.current = event.target instanceof HTMLElement ? event.target : null; setPrReview({ id: target.id, path: target.worktreePath, branch, base }); } }} isDisabled={blocked || !prAvailable}>Create PR</Button>
        <Button tone="primary" onPress={() => run({ cmd: 'worktree_merge', id: target.id, message: effectiveMessage, close_agent_on_merge: closeAgent, remove_worktree_on_merge: removeAfterMerge, preserve_merge_diff: preserveDiff, clear_context: clearContext, ...(forceDirect ? { force_direct: true } : {}) })} isDisabled={blocked || !preflightCurrent || !mergeClean || Boolean(preflight.error)}>Create PR & merge</Button>
        <Button tone="quiet" isDisabled={blocked} onPress={onClose}>Close</Button>
      </footer>
    </div>

    {removalTarget ? <div className={styles.removeDialog}>
      {removal.preview ? <>
        <p>{removal.preview.review.mode === 'unlink' ? "Only this agent's link will be cleared. The shared worktree, files and branch will be kept." : 'Delete this worktree directory and discard its uncommitted files. Git will also try to delete the branch; a branch that Git cannot safely delete will be retained.'}</p>
        <p>Worktree: <code>{removal.preview.review.path}</code></p>
        {removal.preview.shared_with.length ? <p>Shared with: {removal.preview.shared_with.map((item) => item.name).join(', ')}.</p> : null}
        {removal.preview.review.dirty ? <p>This worktree has uncommitted changes.{removal.preview.review.mode === 'unlink' ? ' They will be retained in the shared worktree.' : ' These changes will be permanently discarded.'}</p> : null}
        {removal.preview.review.checkpoints > 0 ? <p>This worktree has {removal.preview.review.checkpoints} {removal.preview.review.checkpoints === 1 ? 'commit' : 'commits'} ahead of its base.{removal.preview.review.mode === 'unlink' ? ' They will be retained on the shared branch.' : ' The branch may be retained after the directory is deleted; this operation does not push commits.'}</p> : null}
        {removal.preview.review.ignored_files ? <p>{removal.preview.review.mode === 'unlink' ? 'Ignored files will also remain in the shared worktree.' : 'Ignored files in this directory will also be permanently discarded.'}</p> : null}
        <p>{removal.preview.review.session_id ? 'This agent has an attached session. Stop it before releasing the worktree.' : 'The agent is stopped. This operation will not start or restart a session.'}</p>
        {removal.preview.blocked_reason ? <p role="alert">{removal.preview.blocked_reason}</p> : null}
      </> : null}
      {!removalComplete && !removal.ready && !blocked && !removal.error ? <p role="status">Waiting for a current removal review and synchronized connection…</p> : null}
      {removal.error ? <p role="alert">{removal.error}</p> : null}
      {mutation.pending ? <p role="status">Releasing worktree…</p> : null}
      {mutation.error ? <p role="alert">{mutation.error}</p> : null}
      {mutation.uncertain ? <Button isDisabled={mutation.pending} onPress={() => { void finish(mutation.retry()); }}>Retry removal</Button> : null}
      {removalComplete ? <><p role="status">{text(mutation.result.message) || (mutation.result.mode === 'unlink' ? 'Agent link cleared. Shared worktree and branch retained.' : 'Worktree removed.')}</p><Button autoFocus onPress={onClose}>Done</Button></> : <footer><Button autoFocus tone="quiet" isDisabled={blocked} onPress={() => setRemovalTarget(null)}>Cancel</Button><Button tone="quiet" isDisabled={blocked} onPress={removal.refresh}>Refresh review</Button><Button tone="danger" isDisabled={blocked || !removal.ready || Boolean(removal.preview?.blocked_reason)} onPress={() => { if (removal.ready && removal.preview && !removal.preview.blocked_reason) run({ cmd: 'worktree_remove', id: removalTarget.id, removal_review: removal.preview.review, relaunch: false }); }}>{removal.preview?.review.mode === 'unlink' ? 'Remove link' : 'Delete worktree'}</Button></footer>}
    </div> : null}

    {prReview ? <>
      <div className={styles.removeDialog}>
        <p>Create a pull request from <strong>{prReview?.branch}</strong> into <strong>{prReview?.base}</strong>? The branch will be pushed to origin first.</p>
        {prTargetChanged ? <p role="alert">The worktree target has changed. Cancel and review the current branch before creating a pull request.</p> : null}
        {mutation.pending ? <p role="status">Creating pull request…</p> : null}
        {mutation.error ? <p role="alert">{mutation.error}</p> : null}
        {!reads.ready && !blocked ? <p role="status">Waiting for a synchronized connection and an available worktree.</p> : null}
        {mutation.uncertain ? <Button isDisabled={mutation.pending} onPress={() => { void finish(mutation.retry()); }}>Retry PR operation</Button> : null}
        <footer><Button autoFocus tone="quiet" isDisabled={blocked} onPress={() => setPrReview(null)}>Cancel</Button><Button tone="primary" isDisabled={blocked || !prAvailable || prTargetChanged} onPress={() => { if (prReview && !prTargetChanged) run({ cmd: 'worktree_create_pr', id: prReview.id }); }}>Push branch and create PR</Button></footer>
      </div>
    </> : null}

    {rollbackSha ? <>
      <div className={styles.removeDialog}><p>Changes after this checkpoint will be lost.</p>{mutation.pending ? <p role="status">Restoring checkpoint…</p> : null}{mutation.error ? <p role="alert">{mutation.error}</p> : null}{mutation.uncertain ? <Button isDisabled={mutation.pending} onPress={() => { void finish(mutation.retry()); }}>Retry rollback</Button> : null}<footer><Button autoFocus tone="quiet" isDisabled={blocked} onPress={() => setRollbackSha('')}>Cancel</Button><Button tone="danger" isDisabled={!reads.ready || blocked} onPress={() => run({ cmd: 'worktree_rollback', id: target.id, sha: rollbackSha })}>Rollback</Button></footer></div>
    </> : null}
  </ModalDialog>;
}
