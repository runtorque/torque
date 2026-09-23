import { Button, ModalDialog } from '../../design/primitives';
import type { useWorktreeToolbar } from './useWorktreeToolbar';
import styles from './AgentWorkspace.module.css';

export function WorktreeOperationDialog({ controller, active }: { controller: ReturnType<typeof useWorktreeToolbar>; active: boolean }) {
  const { operation, mutation, live, blocked, ready, resumePath, confirm, retry, dismiss } = controller;
  if (!operation) return null;
  const creation = operation.kind === 'create';
  const complete = mutation.result.ok === true;
  const path = typeof mutation.result.worktree_path === 'string' ? mutation.result.worktree_path : '';
  const message = typeof mutation.result.message === 'string' ? mutation.result.message : '';
  const newSession = typeof mutation.result.session_id === 'string' ? mutation.result.session_id : '';
  const action = creation ? resumePath ? 'Retry relaunch' : live?.sessionId ? 'Create and restart agent' : 'Create worktree' : 'Retry checkpoint';
  return <ModalDialog title={creation ? `Create worktree for ${operation.agent.name}?` : `Checkpoint ${operation.agent.name}`} description={operation.agent.currentPath} isOpen={active} onOpenChange={(open) => { if (!open) dismiss(); }} size="medium">
    <div className={styles.removeDialog}>
      {creation && !complete ? <>
        {resumePath ? <p>The worktree was created. Retry only the agent relaunch; the existing worktree will be kept.</p> : <p>Create an isolated worktree for this agent.</p>}
        {live?.sessionId ? <p>Restarting replaces the current session. The current conversation will be lost.</p> : <p>{resumePath ? 'The agent is stopped. Retry will start a fresh session in the created worktree.' : 'The agent has no active session; creation will not start one.'}</p>}
        {live && live.sessionId !== operation.agent.sessionId ? <p>The session has changed since this dialog opened. Confirming applies to the current session.</p> : null}
      </> : null}
      {mutation.pending ? <p role="status">{creation ? 'Creating worktree and completing the requested session change…' : 'Creating checkpoint…'}</p> : null}
      {mutation.error ? <p role="alert">{mutation.error}</p> : null}
      {complete ? <p role="status">{message || 'Worktree operation completed.'}</p> : null}
      {path ? <p>Worktree: <code>{path}</code></p> : null}
      {complete && mutation.result.relaunched === true ? <p>New session: <code>{newSession}</code></p> : null}
      {!live && !complete ? <p role="alert">This agent is no longer available. An uncertain request can still be retried to retrieve its result.</p> : null}
      {!ready ? <p role="status">Waiting for a synchronized connection.</p> : null}
      <footer>
        <Button tone="quiet" isDisabled={blocked} onPress={dismiss}>{complete ? 'Close' : 'Cancel'}</Button>
        {mutation.uncertain ? <Button tone="primary" isDisabled={!ready || mutation.pending} onPress={retry}>Retry operation</Button>
          : !complete ? <Button tone="primary" isDisabled={!ready || blocked || !live || Boolean(creation && live.worktreePath && !resumePath)} onPress={confirm}>{action}</Button> : null}
      </footer>
    </div>
  </ModalDialog>;
}
