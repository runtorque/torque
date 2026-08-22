import { useCallback, useEffect, useState } from 'react';

import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { CommandSender } from '../board/BoardPanel';
import type { AgentViewModel } from './model';
import styles from './AgentWorkspace.module.css';

interface WorktreeInspectorProps {
  agent: AgentViewModel | null;
  responses: Record<string, unknown>;
  sendCommand: CommandSender;
  onUnavailable: () => void;
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

export function WorktreeInspector({ agent, responses, sendCommand, onUnavailable, onClose }: WorktreeInspectorProps) {
  const [tab, setTab] = useState<'diff' | 'history'>('diff');
  const [message, setMessage] = useState('');
  const [closeAgent, setCloseAgent] = useState(false);
  const [removeAfterMerge, setRemoveAfterMerge] = useState(false);
  const [preserveDiff, setPreserveDiff] = useState(false);
  const [clearContext, setClearContext] = useState(false);
  const [forceDirect, setForceDirect] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [rollbackSha, setRollbackSha] = useState('');

  const run = useCallback((command: Record<string, unknown>) => {
    if (!sendCommand(command as { cmd: string })) onUnavailable();
  }, [onUnavailable, sendCommand]);
  const refresh = useCallback(() => {
    if (!agent?.id || !agent.worktreePath) return;
    run({ cmd: 'worktree_diff_full', id: agent.id });
    run({ cmd: 'worktree_check_merge', id: agent.id });
    run({ cmd: 'worktree_history', id: agent.id });
  }, [agent, run]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const diff = record(agent ? responses[`worktree_diff_full:${agent.id}`] : null);
  const preflight = record(agent ? responses[`worktree_check_merge:${agent.id}`] : null);
  const history = record(agent ? responses[`worktree_history:${agent.id}`] : null);
  const pr = record(agent ? responses[`worktree_pr:${agent.id}`] : null);
  const merge = record(agent ? responses[`worktree_merge:${agent.id}`] : null);
  const progress = record(agent ? responses[`worktree_merge_progress:${agent.id}`] : null);
  const rebase = record(agent ? responses[`worktree_rebase:${agent.id}`] : null);
  const files = list(diff.files);
  const commits = list(history.commits);
  const conflicts = list(preflight.conflicts);
  const stats = record(diff.stats);
  const mergeClean = preflight.clean === true;
  const mergeDirty = preflight.dirty === true;
  const stale = preflight.stale_base === true || Boolean(preflight.stale_base_warning);
  const mergeResult = text(merge.message) || text(merge.error);
  const activeProgress = text(progress.message) || text(progress.phase);
  const defaultMessage = text(preflight.default_message);
  const effectiveMessage = message || defaultMessage;
  const artifacts = list(diff.artifacts);

  if (!agent) return null;
  return <ModalDialog
    title={`${agent.name} worktree`}
    description={`${text(diff.branch) || agent.worktreeBranch || 'branch'} → ${text(diff.base_branch) || text(agent.raw.worktree_base_branch) || 'main'}`}
    size="large"
    isOpen
    onOpenChange={(open) => { if (!open) onClose(); }}
  >
    <div className={styles.worktreeInspector}>
      <nav aria-label="Worktree views">
        <button aria-current={tab === 'diff' ? 'page' : undefined} onClick={() => setTab('diff')}>Changes</button>
        <button aria-current={tab === 'history' ? 'page' : undefined} onClick={() => setTab('history')}>History <span>{commits.length}</span></button>
        <Button tone="quiet" onPress={refresh}>Refresh</Button>
      </nav>

      {tab === 'diff' ? <>
        <div className={styles.diffSummary}>
          <span>{files.length} files</span>
          <span className={styles.diffAdd}>+{number(stats.insertions)}</span>
          <span className={styles.diffDelete}>−{number(stats.deletions)}</span>
          {preflight.error ? <strong className={styles.diffDanger}>{text(preflight.error)}</strong>
            : mergeDirty ? <strong className={styles.diffWarning}>Checkpoint required</strong>
              : stale ? <strong className={styles.diffWarning}>Base branch is stale</strong>
                : conflicts.length ? <strong className={styles.diffDanger}>{conflicts.length} conflicts</strong>
                  : mergeClean ? <strong className={styles.diffSuccess}>Clean merge</strong>
                    : <strong>Checking merge readiness…</strong>}
        </div>
        {text(diff.error) ? <StateSurface title="Unable to load diff" description={text(diff.error)} tone="danger" /> : null}
        {text(preflight.stale_base_warning) ? <pre className={styles.worktreeWarning}>{text(preflight.stale_base_warning)}</pre> : null}
        {conflicts.length ? <section className={styles.conflictList}><h3>Conflicts</h3>{conflicts.map((conflict, index) => <div key={`${text(conflict.path)}-${index}`}><strong>{text(conflict.path) || 'Unknown path'}</strong><span>{text(conflict.reason)}</span></div>)}</section> : null}
        {artifacts.length ? <section className={styles.worktreeArtifacts}><h3>Boundary artifacts</h3>{artifacts.map((artifact, index) => <div key={`${text(artifact.id)}-${index}`}><strong>{text(artifact.name) || text(artifact.title) || `Artifact ${index + 1}`}</strong><span>{text(artifact.path) || text(artifact.url)}</span></div>)}</section> : null}
        <div className={styles.diffFiles}>
          {!Object.keys(diff).length ? <StateSurface title="Loading changes" description="Torque is building the complete worktree diff." /> : null}
          {Object.keys(diff).length && !files.length && !diff.error ? <StateSurface title="No changes" description="The worktree matches its base branch." /> : null}
          {files.map((file, index) => <details key={`${text(file.path)}-${index}`} open={files.length < 8}>
            <summary><span>{text(file.path) || '(unknown file)'}</span><small>{text(file.status)} · <b className={styles.diffAdd}>+{number(file.insertions)}</b> <b className={styles.diffDelete}>−{number(file.deletions)}</b></small></summary>
            {file.binary ? <p>Binary file changed.</p> : list(file.hunks).map((hunk, hunkIndex) => <section key={`${text(hunk.header)}-${hunkIndex}`} className={styles.diffHunk}>
              <header>{text(hunk.header)}</header>
              <pre>{list(hunk.lines).map((line, lineIndex) => {
                const type = text(line.type);
                const prefix = type === 'add' ? '+' : type === 'del' ? '−' : ' ';
                return <span key={lineIndex} className={type === 'add' ? styles.diffLineAdd : type === 'del' ? styles.diffLineDelete : ''}>{prefix}{text(line.text)}{'\n'}</span>;
              })}</pre>
            </section>)}
          </details>)}
        </div>
      </> : <div className={styles.worktreeHistory}>
        {!Object.keys(history).length ? <StateSurface title="Loading history" description="Torque is loading worktree checkpoints." /> : null}
        {Object.keys(history).length && !commits.length ? <StateSurface title="No checkpoints" description="This branch has no worktree checkpoints yet." /> : null}
        {commits.map((commit, index) => <article key={text(commit.sha) || String(index)}>
          <div><strong>{text(commit.message) || text(commit.short_sha)}</strong><small>{text(commit.short_sha)} · {text(commit.date)} · +{number(commit.insertions)} −{number(commit.deletions)}</small>{text(commit.body) ? <p>{text(commit.body)}</p> : null}</div>
          {index === 0 ? <span>HEAD</span> : <Button tone="quiet" onPress={() => setRollbackSha(text(commit.sha))}>Rollback…</Button>}
        </article>)}
      </div>}

      {text(rebase.error) ? <div role="alert" className={styles.worktreeResult}>{text(rebase.error)}</div> : null}
      {pr.url ? <div className={styles.worktreeResult}>Pull request created: <a href={text(pr.url)} target="_blank" rel="noreferrer">{text(pr.url)}</a></div> : null}
      {activeProgress ? <div role="status" className={styles.worktreeResult}>{activeProgress}</div> : null}
      {mergeResult ? <div role={merge.error ? 'alert' : 'status'} className={styles.worktreeResult}>{mergeResult}</div> : null}

      <section className={styles.mergeControls}>
        <label>Merge message<textarea rows={2} value={effectiveMessage} onChange={(event) => setMessage(event.target.value)} placeholder="Commit or pull-request message" /></label>
        <div className={styles.checkGrid}>
          <label className={styles.inlineCheck}><input type="checkbox" checked={closeAgent} onChange={(event) => setCloseAgent(event.target.checked)} />Close agent after merge</label>
          <label className={styles.inlineCheck}><input type="checkbox" checked={removeAfterMerge} onChange={(event) => setRemoveAfterMerge(event.target.checked)} />Delete worktree after merge</label>
          <label className={styles.inlineCheck}><input type="checkbox" checked={preserveDiff} onChange={(event) => setPreserveDiff(event.target.checked)} />Preserve boundary diff</label>
          <label className={styles.inlineCheck}><input type="checkbox" checked={clearContext} onChange={(event) => setClearContext(event.target.checked)} />Clear context after merge</label>
          <label className={styles.inlineCheck}><input type="checkbox" checked={forceDirect} onChange={(event) => setForceDirect(event.target.checked)} />Force direct local merge</label>
        </div>
      </section>

      <footer className={styles.worktreeFooter}>
        {confirmRemove ? <><span>Delete this worktree and relaunch the agent?</span><Button tone="quiet" onPress={() => setConfirmRemove(false)}>Cancel</Button><Button tone="danger" onPress={() => { run({ cmd: 'worktree_remove', id: agent.id, relaunch: Boolean(agent.sessionId) }); setConfirmRemove(false); onClose(); }}>Delete worktree</Button></> : <Button tone="danger" onPress={() => setConfirmRemove(true)}>Delete worktree…</Button>}
        <span />
        <Button tone="quiet" onPress={() => run({ cmd: 'worktree_checkpoint', id: agent.id })}>Checkpoint</Button>
        {(stale || conflicts.length) ? <Button tone="quiet" onPress={() => run({ cmd: 'worktree_rebase', id: agent.id })}>Rebase onto base</Button> : null}
        <Button tone="quiet" onPress={() => run({ cmd: 'worktree_create_pr', id: agent.id })} isDisabled={mergeDirty || Boolean(preflight.error)}>Create PR</Button>
        <Button tone="primary" onPress={() => run({ cmd: 'worktree_merge', id: agent.id, message: effectiveMessage, close_agent_on_merge: closeAgent, remove_worktree_on_merge: removeAfterMerge, preserve_merge_diff: preserveDiff, clear_context: clearContext, ...(forceDirect ? { force_direct: true } : {}) })} isDisabled={!mergeClean || Boolean(activeProgress)}>Create PR & merge</Button>
        <Button tone="quiet" onPress={onClose}>Close</Button>
      </footer>
    </div>

    <ModalDialog title="Rollback worktree?" description={rollbackSha ? rollbackSha.slice(0, 12) : ''} size="small" isOpen={Boolean(rollbackSha)} onOpenChange={(open) => { if (!open) setRollbackSha(''); }}>
      <div className={styles.removeDialog}><p>Changes after this checkpoint will be lost.</p><footer><Button tone="quiet" onPress={() => setRollbackSha('')}>Cancel</Button><Button tone="danger" onPress={() => { run({ cmd: 'worktree_rollback', id: agent.id, sha: rollbackSha }); setRollbackSha(''); }}>Rollback</Button></footer></div>
    </ModalDialog>
  </ModalDialog>;
}
