import { useAppDispatch, useAppSelector, useAppStore } from '../../app/hooks';
import { relayProbeActions } from './relayProbeState';
import { Button } from '../../design/primitives';
import { readCommand } from '../../protocol/http';
import { relayProbePresentation } from './relayStatusModel';
import styles from './RelayStatus.module.css';
export function RelayProbe({ disabled = false }: { disabled?: boolean }) {
  const dispatch = useAppDispatch(); const store = useAppStore();
  const { phase, result, error } = useAppSelector((state) => state.relayProbe); const pending = phase === 'pending';
  async function probe() {
    if (disabled || store.getState().relayProbe.phase === 'pending') return;
    const id = crypto.randomUUID(); const controller = new AbortController(); dispatch(relayProbeActions.started(id));
    const current = () => store.getState().relayProbe.id === id && store.getState().relayProbe.phase === 'pending';
    const timer = window.setTimeout(() => {
      if (!current()) return;
      controller.abort(); dispatch(relayProbeActions.failed({ id, error: 'Connection test timed out. Retry when ready.' }));
    }, 15_000);
    try {
      const frame = await readCommand({ cmd: 'test_relay_connection' }, controller.signal);
      if (controller.signal.aborted || !current()) return;
      if (frame.type === 'error') throw new Error(typeof frame.message === 'string' ? frame.message : 'Connection test failed.');
      if (frame.type !== 'relay_test_result' || typeof frame.status !== 'string' || !frame.status.trim()) throw new Error('The daemon returned an invalid connection-test result.');
      dispatch(relayProbeActions.succeeded({ id, result: { status: frame.status, message: typeof frame.message === 'string' ? frame.message : '', detail: typeof frame.detail === 'string' ? frame.detail : '' } }));
    } catch (cause) {
      if (!controller.signal.aborted && current()) dispatch(relayProbeActions.failed({ id, error: cause instanceof Error ? cause.message : 'Connection test failed.' }));
    } finally { window.clearTimeout(timer); }
  }
  const presentation = result ? relayProbePresentation(result.status) : null;
  return <section className={styles.panel} aria-label="Relay connection test"><header><h4>Connection test</h4><Button tone="quiet" isDisabled={disabled || pending} onPress={() => { void probe(); }}>{pending ? 'Testing connection…' : error ? 'Retry connection test' : 'Test connection'}</Button></header>
    {pending ? <p role="status">Testing Relay connectivity…</p> : null}{error ? <p role="alert">{error}</p> : null}
    {result && presentation ? <div role="status" data-tone={presentation.tone}><strong>{presentation.label}</strong>{result.message ? <p>{result.message}</p> : null}{result.detail ? <p className={styles.detail}>{result.detail}</p> : null}</div> : null}
  </section>;
}
