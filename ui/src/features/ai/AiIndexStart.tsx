import { useAppDispatch, useAppSelector, useAppStore } from '../../app/hooks';
import { projectionActions, selectOperationsState } from '../../app/store';
import { Button } from '../../design/primitives';
import { readCommand } from '../../protocol/http';
import { redactAiSaveError } from '../control/aiSettingsSafety';
import { aiIndexStartActions } from './aiIndexStartState';
import { aiIndexControl, aiRecord, aiText } from './aiRuntimeModel';
import styles from './AiRuntime.module.css';

export function AiIndexStart({ disabled = false }: { disabled?: boolean }) {
  const store = useAppStore(); const dispatch = useAppDispatch();
  const settings = useAppSelector(selectOperationsState).aiSettings; const operation = useAppSelector((state) => state.aiIndexStart); const view = aiIndexControl(settings);
  const pending = operation.phase === 'pending';
  async function start() {
    const state = store.getState(); const live = selectOperationsState(state).aiSettings; const gate = aiIndexControl(live);
    if (disabled || gate.blocked || state.aiIndexStart.phase === 'pending') return;
    const id = crypto.randomUUID(); const controller = new AbortController(); const before = state.projection;
    dispatch(aiIndexStartActions.started(id));
    const current = () => store.getState().aiIndexStart.id === id && store.getState().aiIndexStart.phase === 'pending';
    const timer = window.setTimeout(() => {
      if (!current()) return; controller.abort(); dispatch(aiIndexStartActions.failed({ id, error: 'The index start request timed out; its outcome is unknown. Check the live job status before retrying.' }));
    }, 30_000);
    try {
      const frame = await readCommand({ cmd: 'ai_index_start', mode: gate.mode, confirm: true }, controller.signal);
      if (controller.signal.aborted || !current()) return;
      if (frame.type === 'error') throw new Error(aiText(frame.message, 'Index start failed.'));
      const job = aiRecord(frame.job);
      if (frame.type !== 'ai_index_job' || !aiText(job.id) || !aiText(job.status)) throw new Error('The daemon returned an invalid index-start acknowledgement; the outcome is unknown. Check the live job status before retrying.');
      const projection = store.getState().projection;
      // A queued acknowledgement may arrive after a running/completed delta or
      // reconnect snapshot. Never overwrite that newer diagnostic projection.
      if (projection.snapshotVersion === before.snapshotVersion && projection.data.ai_settings === before.data.ai_settings) {
        const accepted = Object.keys(aiRecord(frame.settings)).length ? aiRecord(frame.settings) : { ...live, index: { ...aiRecord(live.index), current_job: job, ...(['queued', 'running'].includes(aiText(job.status)) ? { status: 'building' } : {}) } };
        dispatch(projectionActions.auxiliaryResourceReceived({ type: 'ai_settings', settings: accepted }));
      }
      dispatch(aiIndexStartActions.accepted({ id, jobId: aiText(job.id) }));
    } catch (cause) {
      if (!controller.signal.aborted && current()) dispatch(aiIndexStartActions.failed({ id, error: redactAiSaveError(cause instanceof Error ? cause.message : 'Index start failed.', {}) }));
    } finally { window.clearTimeout(timer); }
  }
  return <div className={styles.start}><Button tone="quiet" isDisabled={disabled || pending || view.blocked} onPress={() => { void start(); }}>{pending ? 'Starting index…' : view.label}</Button>
    {view.reason ? <p>{view.reason}</p> : null}{pending ? <p role="status">Starting the index job…</p> : null}{operation.phase === 'ready' ? <p role="status">Start request accepted. Job: {operation.jobId}.</p> : null}{operation.error ? <p role="alert">{operation.error}</p> : null}
  </div>;
}
