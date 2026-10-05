import { useEffect, useRef, useState } from 'react';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import styles from './ControlCenter.module.css';

type Kind = 'engineer' | 'architect';
interface Preview { id: string; key: string; kind: Kind; phase: 'loading' | 'ready' | 'error' | 'stale'; prompt: string; provider: string; error: string }
const label = (kind: Kind) => kind === 'engineer' ? 'Engineer' : 'Architect';

export function SettingsPromptPreview({ group, groupSettings, engineer, architect, disabled = false }: { group: string; groupSettings: UnknownRecord; engineer: UnknownRecord; architect: UnknownRecord; disabled?: boolean }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [copy, setCopy] = useState({ id: '', busy: false, message: '', error: false });
  const active = useRef<{ id: string; controller: AbortController; timer: number } | null>(null);
  const mounted = useRef(true);
  const copyAttempt = useRef(0);
  const settingsFor = (kind: Kind) => kind === 'engineer' ? engineer : architect;
  const keyFor = (kind: Kind) => JSON.stringify([group, kind, groupSettings, settingsFor(kind)]);
  const currentKey = preview ? keyFor(preview.kind) : '';
  const stale = preview?.phase === 'stale' || Boolean(preview && preview.key !== currentKey);
  // Invalidation is sticky: reverting an edit must not revive a cancelled read.
  if (preview && stale && preview.phase !== 'stale') setPreview({ ...preview, phase: 'stale', prompt: '', error: '' });
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; active.current?.controller.abort(); window.clearTimeout(active.current?.timer); active.current = null; };
  }, []);
  useEffect(() => {
    if (preview?.phase === 'stale' && active.current?.id === preview.id) {
      active.current.controller.abort(); window.clearTimeout(active.current.timer); active.current = null;
    }
  }, [preview?.phase, preview?.id]);

  async function renderPrompt(kind: Kind) {
    if (disabled) return;
    active.current?.controller.abort(); window.clearTimeout(active.current?.timer);
    const controller = new AbortController(); const id = crypto.randomUUID();
    const request: Preview = { id, key: keyFor(kind), kind, phase: 'loading', prompt: '', provider: '', error: '' };
    const command = { cmd: 'preview_system_prompt', request_id: id, group, kind, group_settings: groupSettings, settings: settingsFor(kind) };
    setPreview(request);
    const timer = window.setTimeout(() => {
      if (active.current?.id !== id) return;
      controller.abort(); active.current = null;
      setPreview({ ...request, phase: 'error', error: 'Prompt preview timed out. Retry when ready.' });
    }, 30_000);
    active.current = { id, controller, timer };
    try {
      const frame = await readCommand(command, controller.signal);
      if (controller.signal.aborted || active.current?.id !== id) return;
      if (frame.type === 'error') throw new Error(typeof frame.message === 'string' ? frame.message : 'Prompt preview failed.');
      if (frame.type !== 'system_prompt_preview' || frame.request_id !== id || frame.group !== group || frame.kind !== kind || typeof frame.prompt !== 'string') throw new Error('The preview response did not match this request.');
      const metadata = frame.metadata && typeof frame.metadata === 'object' ? frame.metadata as UnknownRecord : {};
      setPreview({ ...request, phase: 'ready', prompt: frame.prompt, provider: typeof metadata.provider === 'string' ? metadata.provider : '' });
    } catch (cause) {
      if (!controller.signal.aborted && active.current?.id === id) setPreview({ ...request, phase: 'error', error: cause instanceof Error ? cause.message : 'Prompt preview failed.' });
    } finally { window.clearTimeout(timer); if (active.current?.id === id) active.current = null; }
  }
  async function copyPrompt() {
    if (!preview || preview.phase !== 'ready' || stale || disabled || (copy.id === preview.id && copy.busy)) return;
    const id = preview.id; const attempt = ++copyAttempt.current; setCopy({ id, busy: true, message: '', error: false });
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(preview.prompt);
      if (mounted.current && copyAttempt.current === attempt) setCopy({ id, busy: false, message: 'Prompt copied.', error: false });
    } catch {
      if (mounted.current && copyAttempt.current === attempt) setCopy({ id, busy: false, message: 'Copy failed. Select the rendered prompt and copy it manually.', error: true });
    }
  }
  return <section className={styles.settingsPromptPreview} aria-label="System prompt preview">
    <div className={styles.settingsActions}>{(['engineer', 'architect'] as const).map((kind) => <Button key={kind} tone="quiet" isDisabled={disabled || (preview?.kind === kind && preview.phase === 'loading' && !stale)} onPress={() => { void renderPrompt(kind); }}>Preview {label(kind)} system prompt</Button>)}</div>
    {preview ? <>
      <header><h4>{label(preview.kind)} system prompt · {group || 'Default group'}</h4><Button tone="quiet" isDisabled={disabled || stale || preview.phase !== 'ready' || (copy.id === preview.id && copy.busy)} onPress={() => { void copyPrompt(); }}>Copy rendered prompt</Button></header>
      {stale ? <p role="status">Draft changed. Preview again to see the current prompt.</p> : preview.phase === 'loading' ? <p role="status">Rendering {label(preview.kind)} prompt from the current draft…</p> : preview.phase === 'error' ? <div role="alert"><p>{preview.error}</p><Button isDisabled={disabled} onPress={() => { void renderPrompt(preview.kind); }}>Retry prompt preview</Button></div> : <>
        <p>Provider: {preview.provider || 'Inherited provider'}</p>
        {preview.prompt ? <pre tabIndex={0} aria-label="Rendered system prompt">{preview.prompt}</pre> : <p role="status">The rendered prompt is empty.</p>}
        {copy.id === preview.id && copy.message ? <p role={copy.error ? 'alert' : 'status'}>{copy.message}</p> : null}
      </>}
    </> : null}
  </section>;
}
