import { useState } from 'react';

import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { AgentViewModel } from './model';
import { WorktreeDiff } from './WorktreeDiff';
import { useDiffDisclosure } from './worktreeDiffModel';
import { useWorktreeReads } from './useWorktreeReads';
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
  const [closeAgent, setCloseAgent] = useState(false);
  const [removeAfterMerge, setRemoveAfterMerge] = useState(false);
  const [preserveDiff, setPreserveDiff] = useState(false);
  const [clearContext, setClearContext] = useState(false);
  const [forceDirect, setForceDirect] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [rollbackSha, setRollbackSha] = useState('');
  const [operationAgent, setOperationAgent] = useState<AgentViewModel | null>(null);
  const target = agent ?? operationAgent;

  const mutation = useWorktreeMutation();
  const blocked = mutation.pending || mutation.uncertain;
  const reads = useWorktreeReads(target?.id ?? '', target?.worktreePath ?? '', target?.worktreeBranch ?? '', active && Boolean(agent) && !blocked);
  const [progressAtStart, setProgressAtStart] = useState<unknown>(null);
  async function finish(operation: Promise<Record<string, unknown> | null>) {
    const result = await operation;
    if (!result) return;
    if (result.type === 'worktree_rollback') setRollbackSha('');
    if (result.type === 'worktree_remove') { setConfirmRemove(false); onClose(); }
    else reads.refresh();
  }
  function run(command: Record<string, unknown>) {
    if (!agent || !reads.ready || blocked) return;
    setOperationAgent(agent);
    setProgressAtStart(agent ? responses[`worktree_merge_progress:${agent.id}`] : null);
    void finish(mutation.run(command));
  }
  const { refresh } = reads;
  const diff = reads.diff.data;
  const preflight = reads.preflight.data;
  const history = reads.history.data;
  const preflightCurrent = reads.preflight.status === 'ready';
  const result = mutation.result;
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

  if (!target) return null;
  return <ModalDialog
    title={`${target.name} worktree`}
    description={`${text(diff.branch) || target.worktreeBranch || 'branch'} → ${text(diff.base_branch) || text(target.raw.worktree_base_branch) || 'main'}`}
    size="large"
    isOpen={active}
    onOpenChange={(open) => { if (!open && !blocked) onClose(); }}
  >
    <div className={styles.worktreeInspector}>
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
          {index === 0 ? <span>HEAD</span> : <Button tone="quiet" isDisabled={!reads.ready || blocked} onPress={() => setRollbackSha(text(commit.sha))}>Rollback…</Button>}
        </article>)}
      </div>

      {mutation.pending ? <p role="status">Worktree operation in progress…</p> : null}
      {mutation.error ? <div role="alert" className={styles.worktreeResult}>{mutation.error}{mutation.uncertain ? <Button isDisabled={mutation.pending} onPress={() => { void finish(mutation.retry()); }}>Retry operation</Button> : null}</div> : null}
      {activeProgress ? <div role="status" className={styles.worktreeResult}>{activeProgress}</div> : null}
      {resultMessage ? <div role="status" className={styles.worktreeResult}>{resultMessage}</div> : null}
      {resultUrl ? <div className={styles.worktreeResult}>Pull request: <a href={resultUrl} target="_blank" rel="noreferrer">{resultUrl}</a></div> : null}
      {result.warning ? <div role="alert">{text(result.warning)}</div> : null}
      {Array.isArray(cleanupErrors) && cleanupErrors.length ? <div role="alert">Cleanup needs attention: {cleanupErrors.map(text).join('; ')}</div> : null}

      <section className={styles.mergeControls}>
        <label>Merge message<textarea disabled={blocked} rows={2} value={effectiveMessage} onChange={(event) => setMessage(event.target.value)} placeholder="Commit or pull-request message" /></label>
        <div className={styles.checkGrid}>
          <label className={styles.inlineCheck}><input type="checkbox" disabled={blocked} checked={closeAgent} onChange={(event) => setCloseAgent(event.target.checked)} />Close agent after merge</label>
          <label className={styles.inlineCheck}><input type="checkbox" disabled={blocked} checked={removeAfterMerge} onChange={(event) => setRemoveAfterMerge(event.target.checked)} />Delete worktree after merge</label>
          <label className={styles.inlineCheck}><input type="checkbox" disabled={blocked} checked={preserveDiff} onChange={(event) => setPreserveDiff(event.target.checked)} />Preserve boundary diff</label>
          <label className={styles.inlineCheck}><input type="checkbox" disabled={blocked} checked={clearContext} onChange={(event) => setClearContext(event.target.checked)} />Clear context after merge</label>
          <label className={styles.inlineCheck}><input type="checkbox" disabled={blocked} checked={forceDirect} onChange={(event) => setForceDirect(event.target.checked)} />Force direct local merge</label>
        </div>
      </section>

      <footer className={styles.worktreeFooter}>
        {confirmRemove ? <><span>Delete this worktree and relaunch the agent?</span><Button tone="quiet" isDisabled={blocked} onPress={() => setConfirmRemove(false)}>Cancel</Button><Button tone="danger" isDisabled={!reads.ready || blocked} onPress={() => run({ cmd: 'worktree_remove', id: target.id, relaunch: Boolean(target.sessionId) })}>Delete worktree</Button></> : <Button tone="danger" isDisabled={!reads.ready || blocked} onPress={() => setConfirmRemove(true)}>Delete worktree…</Button>}
        <span />
        <Button tone="quiet" isDisabled={!reads.ready || blocked} onPress={() => run({ cmd: 'worktree_checkpoint', id: target.id })}>Checkpoint</Button>
        {(stale || conflicts.length) ? <Button tone="quiet" isDisabled={!reads.ready || blocked} onPress={() => run({ cmd: 'worktree_rebase', id: target.id })}>Rebase onto base</Button> : null}
        <Button tone="quiet" onPress={() => run({ cmd: 'worktree_create_pr', id: target.id })} isDisabled={blocked || !preflightCurrent || mergeDirty || Boolean(preflight.error)}>Create PR</Button>
        <Button tone="primary" onPress={() => run({ cmd: 'worktree_merge', id: target.id, message: effectiveMessage, close_agent_on_merge: closeAgent, remove_worktree_on_merge: removeAfterMerge, preserve_merge_diff: preserveDiff, clear_context: clearContext, ...(forceDirect ? { force_direct: true } : {}) })} isDisabled={blocked || !preflightCurrent || !mergeClean || Boolean(preflight.error)}>Create PR & merge</Button>
        <Button tone="quiet" isDisabled={blocked} onPress={onClose}>Close</Button>
      </footer>
    </div>

    <ModalDialog title="Rollback worktree?" description={rollbackSha ? rollbackSha.slice(0, 12) : ''} size="small" isOpen={active && Boolean(rollbackSha)} onOpenChange={(open) => { if (!open && !blocked) setRollbackSha(''); }}>
      <div className={styles.removeDialog}><p>Changes after this checkpoint will be lost.</p>{mutation.pending ? <p role="status">Restoring checkpoint…</p> : null}{mutation.error ? <p role="alert">{mutation.error}</p> : null}{mutation.uncertain ? <Button isDisabled={mutation.pending} onPress={() => { void finish(mutation.retry()); }}>Retry rollback</Button> : null}<footer><Button tone="quiet" isDisabled={blocked} onPress={() => setRollbackSha('')}>Cancel</Button><Button tone="danger" isDisabled={!reads.ready || blocked} onPress={() => run({ cmd: 'worktree_rollback', id: target.id, sha: rollbackSha })}>Rollback</Button></footer></div>
    </ModalDialog>
  </ModalDialog>;
}
