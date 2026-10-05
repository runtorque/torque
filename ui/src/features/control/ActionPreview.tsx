import { useEffect, useRef, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import { Button } from '../../design/primitives';
import { settingsRequest } from './settingsRequests';
import type { UnknownRecord } from '../../protocol';
import { record, text } from './agentClassesModel';
import { actionDefinition, type ActionDraft } from './actionModel';
import styles from './ControlCenter.module.css';
export function ActionPreview({ draft, group, disabled }: { draft: ActionDraft; group: string; disabled: boolean }) {
  const connection = useAppSelector(selectConnection);
  const [variables, setVariables] = useState<UnknownRecord[]>([]); const [values, setValues] = useState<Record<string, string>>({ TASK: 'Preview task' });
  const [discoveryError, setDiscoveryError] = useState(''); const [error, setError] = useState(''); const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ signature: string; prompt: string } | null>(null); const [pending, setPending] = useState(false);
  const request = useRef<AbortController | null>(null); const prompt = text(draft.values.prompt);
  const signature = JSON.stringify([draft, values]);
  useEffect(() => { request.current?.abort(); }, [signature]);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (disabled || connection.status !== 'connected') return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void settingsRequest({ cmd: 'render_action', name: draft.name, scope: draft.scope, group, action: { prompt }, variables_only: true }, controller.signal, false, 'Action preview').then((frame) => {
        if (controller.signal.aborted) return;
        if (frame.type !== 'action_variables' || frame.scope !== draft.scope || frame.name !== draft.name || frame.workspace_group !== group || !Array.isArray(frame.vars)) throw new Error(text(frame.message, 'Variable discovery did not match this draft.'));
        setVariables(frame.vars.map(record)); setDiscoveryError('');
      }).catch((cause: unknown) => { if (!controller.signal.aborted) setDiscoveryError(cause instanceof Error ? cause.message : 'Variable discovery failed.'); });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [prompt, draft.name, draft.scope, group, retry, connection.status, connection.reconnectCount, disabled]);
  const preview = async () => {
    let definition: UnknownRecord;
    try { definition = actionDefinition(draft, false); } catch (cause: unknown) { setError(cause instanceof Error ? cause.message : 'Check the draft.'); return; }
    request.current?.abort(); const controller = new AbortController(); request.current = controller; setPending(true); setError('');
    const vars = { ...values };
    try {
      const frame = await settingsRequest({ cmd: 'render_action', name: draft.name, scope: draft.scope, group, action: definition, vars }, controller.signal, false, 'Action preview');
      if (controller.signal.aborted) return;
      if (frame.type !== 'action_rendered' || frame.name !== draft.name || frame.workspace_group !== group || frame.scope !== draft.scope) throw new Error(text(frame.message, 'Preview response did not match this draft.'));
      setResult({ signature, prompt: text(frame.prompt) });
    } catch (cause: unknown) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Preview failed.'); }
    finally { if (request.current === controller) { request.current = null; setPending(false); } }
  };
  return <section aria-label="Action preview" className={styles.actionPreview}><h3>Preview variables</h3><p className={styles.note}>Discovered from the current prompt. Values are only used for preview; Torque context uses safe example values.</p>
    {discoveryError ? <p role="alert">{discoveryError} <Button isDisabled={disabled} onPress={() => setRetry((value) => value + 1)}>Retry variables</Button></p> : null}
    <div className={styles.formGrid}>{variables.map((row) => { const name = text(row.name); return <label className={styles.field} key={name}><span>Preview {name}</span><input value={values[name] ?? ''} placeholder={text(row.default, 'Empty')} onChange={(event) => setValues((previous) => ({ ...previous, [name]: event.target.value }))} /></label>; })}</div>
    <Button isDisabled={disabled || connection.status !== 'connected'} onPress={() => { void preview(); }}>{pending ? 'Preview again' : 'Preview'}</Button>
    {error ? <p role="alert">{error}</p> : null}{result ? result.signature === signature ? <pre aria-label="Rendered action prompt" className={styles.json}>{result.prompt}</pre> : <p role="status">Draft changed. Preview again to see the current prompt.</p> : null}
  </section>;
}
