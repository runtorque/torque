import { useAppDispatch, useAppSelector, useAppStore } from '../../app/hooks';
import { selectMessagesState } from '../../app/store';
import { Button } from '../../design/primitives';
import { settingsRequest } from '../control/settingsRequests';
import { composerActions } from './composerState';
import { text } from './composerModel';
import { assertLoopCancelled, loopInterval, loopNextRun, messageLoopPanel } from './messageLoopModel';
import styles from './AgentMessageLoop.module.css';
export function AgentMessageLoop({ agentId, disabled }: { agentId: string; disabled: boolean }) {
  const dispatch = useAppDispatch(); const store = useAppStore();
  const { loops } = useAppSelector(selectMessagesState); const operations = useAppSelector((state) => state.composer.loopCancellations);
  const loop = messageLoopPanel(loops, operations, agentId); if (!loop) return null;
  const loopId = text(loop.id); const operation = operations[loopId];
  const cancel = async () => {
    const previous = store.getState().composer.loopCancellations[loopId]; if (disabled || previous?.pending || previous?.notice) return;
    const key = previous?.key || `react-loop-cancel-${crypto.randomUUID()}`;
    dispatch(composerActions.loopCancellation({ loopId, operation: { agentId, loop, key, pending: true, error: '', notice: '' } }));
    // The retained store owns this operation across agent selection/unmount.
    // Bound observation without making an old acknowledgement settle a retry.
    try {
      const frame = await settingsRequest({ cmd: 'user_agent_message', agent_id: agentId, thread_id: `user-agent:user:${agentId}`, message: '/loop cancel', expected_loop_id: loopId, idempotency_key: key }, new AbortController().signal, true, 'Loop cancellation', 'Loop cancellation timed out; its outcome is unknown. Retry to recover the same loop request.');
      assertLoopCancelled(frame, agentId, loopId);
      dispatch(composerActions.settleLoopCancellation({ loopId, key, notice: 'Message loop cancelled.' }));
    } catch (cause) { dispatch(composerActions.settleLoopCancellation({ loopId, key, error: cause instanceof Error ? cause.message : 'Loop cancellation failed.' })); }
  };
  return <section className={styles.loop} data-message-loop={loopId} aria-label="Scheduled message loop">
    <div><strong>/loop · Every {loopInterval(loop.interval_seconds)}</strong><span>{operation?.notice ? 'Cancelled' : loop.status === 'active' ? loopNextRun(loop) : `Loop ${text(loop.status)}`}</span>
      <Button tone="quiet" isDisabled={disabled || operation?.pending || Boolean(operation?.notice) || (!operation?.error && loop.status !== 'active')} onPress={() => { void cancel(); }}>{operation?.pending ? 'Cancelling loop…' : operation?.error ? 'Retry loop cancellation' : 'Cancel loop'}</Button>
      {operation && !operation.pending ? <Button tone="quiet" aria-label="Dismiss loop cancellation status" onPress={() => dispatch(composerActions.dismissLoopCancellation(loopId))}>×</Button> : null}
    </div>
    <p title={text(loop.message)}>{text(loop.message)}</p>
    {operation?.error ? <p role="alert">{operation.error}</p> : operation?.notice ? <p role="status">{operation.notice}</p> : null}
  </section>;
}
