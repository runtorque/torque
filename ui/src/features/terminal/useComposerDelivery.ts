import { useAppDispatch, useAppStore } from '../../app/hooks';
import type { TorqueCommand } from '../../protocol';
import { CommandResponseError } from '../../protocol/http';
import type { AgentViewModel } from '../agents/model';
import { settingsRequest } from '../control/settingsRequests';
import { acknowledgedMessage, cancellationLabels, composerCommand, text } from './composerModel';
import { composerActions, composerDraftLocked, emptyComposerDraft, type ComposerAttempt, type ComposerDraft } from './composerState';

// The shared composer store owns writes across selection changes and unmounts.
// The bounded request settles once even if its expired fetch ignores abort.
export function useComposerDelivery(cell: AgentViewModel, target: AgentViewModel | null) {
  const dispatch = useAppDispatch(); const store = useAppStore();
  const currentDraft = () => store.getState().composer.drafts[cell.id] ?? emptyComposerDraft;
  const patch = (changes: Partial<ComposerDraft>) => dispatch(composerActions.patch({ cellId: cell.id, changes }));
  const submit = async () => {
    const current = currentDraft(); if (current.pending || current.uploading || current.composition) return;
    let owned: ComposerAttempt | undefined;
    try {
      if (current.attempt?.uncertain && current.attempt.command) owned = current.attempt;
      else {
        const payload = composerCommand(cell, target, current); const fingerprint = JSON.stringify(payload);
        const key = current.attempt?.fingerprint === fingerprint ? current.attempt.key : `react-message-${crypto.randomUUID()}`;
        owned = { fingerprint, key, command: { ...payload, idempotency_key: key }, targetName: target?.name ?? cell.name, sessionId: target?.sessionId ?? cell.sessionId };
        if (payload.cmd === 'user_agent_message') dispatch(composerActions.submission({ agentId: text(payload.agent_id), key }));
      }
      const command = owned.command!;
      patch({ pending: true, error: '', notice: '', attempt: owned });
      const frame = await settingsRequest(command, new AbortController().signal, true, 'Message submission', 'Message submission timed out; its outcome is unknown. Retry to recover the same message.');
      const result = acknowledgedMessage(frame, command);
      if (currentDraft().attempt?.key !== owned.key) return;
      dispatch(composerActions.submitted({ cellId: cell.id, key: owned.key, message: { id: result.id, message: text(command.message ?? command.text), at: Date.now() / 1000 }, notice: result.notice }));
      if (command.cmd === 'user_agent_message' && result.cancellable && owned.sessionId) dispatch(composerActions.turn({ agentId: text(command.agent_id), turn: { key: owned.key, sessionId: owned.sessionId, pending: false, cancelKey: '', error: '', notice: '' } }));
    } catch (cause) {
      if (owned && currentDraft().attempt?.key !== owned.key) return;
      const error = cause instanceof Error ? cause.message : 'Could not confirm message delivery. The draft is retained.';
      const refused = cause instanceof CommandResponseError && cause.deliveryRefused;
      patch({ error, ...(owned ? { pending: false } : {}), ...(owned ? { attempt: refused ? null : { ...owned, uncertain: true } } : {}) });
    } finally {
      if (owned && currentDraft().attempt?.key === owned.key) patch({ pending: false });
    }
  };
  const cancelTurn = async () => {
    const current = currentDraft();
    if (!target || composerDraftLocked(current) || current.uploading || current.composition) return;
    const submitted = store.getState().composer.turns[target.id];
    if (!submitted || submitted.pending || (!submitted.cancelKey && submitted.sessionId !== target.sessionId) || submitted.notice) return;
    const cancelKey = submitted.cancelKey || `react-cancel-${crypto.randomUUID()}`;
    const change = (changes: Partial<typeof submitted>) => dispatch(composerActions.patchTurn({ agentId: target.id, key: submitted.key, changes }));
    change({ pending: true, cancelKey, error: '' });
    try {
      const command: TorqueCommand = { cmd: 'user_agent_turn_cancel', agent_id: target.id, session_id: submitted.sessionId, turn_idempotency_key: submitted.key, idempotency_key: cancelKey };
      const frame = await settingsRequest(command, new AbortController().signal, true, 'Turn cancellation', 'Turn cancellation timed out; its outcome is unknown. Retry to recover the same cancellation.');
      if (frame.type === 'error') throw new Error(text(frame.message) || 'Cancellation refused.');
      const label = cancellationLabels[text(frame.outcome)];
      if (frame.type !== 'ok' || !text(frame.message_id) || !label) throw new Error('Could not confirm cancellation.');
      change({ notice: label });
    } catch (cause) { change({ error: cause instanceof Error ? cause.message : 'Could not confirm cancellation.' }); }
    finally { change({ pending: false }); }
  };
  return { submit, cancelTurn };
}
