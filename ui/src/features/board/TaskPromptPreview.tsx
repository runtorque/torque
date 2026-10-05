import { useEffect, useRef, useState } from 'react';
import { Button } from '../../design/primitives';
import type { TorqueCommand } from '../../protocol';
import { boardReadRequest } from './boardReadRequest';
import styles from './BoardPanel.module.css';

export function TaskPromptPreview({ inputsKey, command, disabled = false }: { inputsKey: string; command: () => TorqueCommand; disabled?: boolean }) {
  const controller = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<{ key: string; prompt: string; warning: string } | null>(null);
  const [error, setError] = useState('');
  useEffect(() => () => controller.current?.abort(), []);
  const preview = async () => {
    if (controller.current) return;
    const request = new AbortController(); controller.current = request; setPending(true); setError(''); setResult(null);
    try {
      const frame = await boardReadRequest(command(), request.signal);
      if (frame.type === 'error') throw new Error(typeof frame.message === 'string' ? frame.message : 'Prompt preview failed.');
      if (frame.type !== 'prompt_preview' || typeof frame.prompt !== 'string') throw new Error('The server returned no prompt preview.');
      if (!request.signal.aborted) setResult({ key: inputsKey, prompt: frame.prompt, warning: typeof frame.warning === 'string' ? frame.warning : '' });
    } catch (cause) { if (!request.signal.aborted) setError(cause instanceof Error ? cause.message : 'Prompt preview failed.'); }
    finally { if (controller.current === request) { controller.current = null; if (!request.signal.aborted) setPending(false); } }
  };
  return <div className={styles.detailSection}>
    <Button onPress={() => { void preview(); }} isDisabled={disabled || pending}>{pending ? 'Rendering prompt…' : 'Preview prompt'}</Button>
    {error ? <p role="alert">{error}</p> : null}
    {result ? result.key === inputsKey ? <details className={styles.promptPreview} open><summary>Rendered prompt preview</summary>{result.warning ? <p role="status">{result.warning}</p> : null}<pre>{result.prompt}</pre></details> : <p role="status">Draft changed. Preview again to see the current prompt.</p> : null}
  </div>;
}
