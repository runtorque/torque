import { useEffect, useRef, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import { Button, ModalDialog } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { observeBoardRequest } from './boardReadRequest';
import { taskText } from './taskCreationModel';
import { evidencePreviewKind, evidenceUrl } from './taskEvidenceModel';
import styles from './BoardPanel.module.css';

function ImagePreview({ url, title, requestKey, retry }: { url: string; title: string; requestKey: string; retry: () => void }) {
  const [result, setResult] = useState({ key: '', error: '' });
  const owner = useRef<{ key: string; controller: AbortController; timer: ReturnType<typeof setTimeout> } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => { controller.abort(); setResult({ key: requestKey, error: 'Image preview timed out. Retry when ready.' }); }, 15_000);
    owner.current = { key: requestKey, controller, timer };
    return () => { controller.abort(); clearTimeout(timer); };
  }, [requestKey]);
  const settle = (error = '') => {
    const current = owner.current;
    if (!current || current.key !== requestKey || current.controller.signal.aborted) return;
    clearTimeout(current.timer); current.controller.abort(); setResult({ key: requestKey, error });
  };
  const error = result.key === requestKey ? result.error : '';
  return <>
    {result.key !== requestKey ? <p role="status">Loading image preview…</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    <Button onPress={retry}>{error ? 'Retry image preview' : 'Refresh image preview'}</Button>
    {!error ? <img key={requestKey} src={url} alt={title} onLoad={() => settle()} onError={() => settle('Could not load this image.')} /> : null}
  </>;
}

export function ArtifactPreview({ item, taskId, onClose }: { item: UnknownRecord; taskId: string; onClose: () => void }) {
  const reconnect = useAppSelector(selectConnection).reconnectCount;
  const title = taskText(item.title || item.filename, 'Artifact');
  const path = taskText(item.path || (item.storage as UnknownRecord | undefined)?.path);
  const url = evidenceUrl(taskId, item); const kind = evidencePreviewKind(item);
  const inline = taskText(item.content ?? (item.storage as UnknownRecord | undefined)?.content);
  const identity = JSON.stringify([taskId, item.taskId, item.id, url, kind, inline]);
  const [revision, setRevision] = useState(0);
  const [accepted, setAccepted] = useState({ identity: '', content: '' });
  const [result, setResult] = useState({ key: '', error: '' });
  const key = JSON.stringify([identity, reconnect, revision]);
  const readsFile = kind === 'text' && !inline && Boolean(url);
  const loading = readsFile && result.key !== key;
  const error = result.key === key ? result.error : '';
  const hasContent = Boolean(inline) || accepted.identity === identity;
  const content = inline || (accepted.identity === identity ? accepted.content : taskText(item.summary || path));
  const retry = () => setRevision((value) => value + 1);
  useEffect(() => {
    if (!readsFile) return;
    const controller = new AbortController();
    void observeBoardRequest(async (signal) => {
      const response = await fetch(url, { signal });
      if (!response.ok) throw new Error('Could not load this file.');
      return response.text();
    }, controller.signal, false).then((value) => {
      if (controller.signal.aborted) return;
      setAccepted({ identity, content: value }); setResult({ key, error: '' });
    }).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      const message = cause instanceof Error ? cause.message : 'Could not load this file.';
      setResult({ key, error: message.includes('timed out') ? 'File preview timed out. Retry when ready.' : message });
    });
    return () => controller.abort();
  }, [identity, key, readsFile, url]);
  return <ModalDialog title={`Preview: ${title}`} description={taskText(item.type || item.mime_type)} size="large" isOpen onOpenChange={(open) => { if (!open) onClose(); }}>
    <div className={styles.artifactPreview}>
      {item.summary ? <p>{taskText(item.summary)}</p> : null}
      {path ? <p><code>{path}</code> <Button onPress={() => { void navigator.clipboard.writeText(path); }}>Copy path</Button></p> : null}
      {item.line_start ? <p>Lines {taskText(item.line_start)}{item.line_end ? `–${taskText(item.line_end)}` : ''}</p> : null}
      {loading ? <p role="status">{hasContent ? 'Refreshing file preview…' : 'Loading file preview…'}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {readsFile ? <Button onPress={retry}>{error ? 'Retry file preview' : 'Refresh file preview'}</Button> : null}
      {kind === 'image' && url ? <ImagePreview requestKey={key} url={url} title={title} retry={retry} /> : null}
      {kind === 'text' && (hasContent || !loading) ? <pre>{content || (hasContent ? 'This file is empty.' : 'No preview content.')}</pre> : null}
      {kind === 'file' || (kind === 'image' && !url) ? <p>This reference has no inline preview.</p> : null}
      {url ? <a href={url} download>Download file</a> : null}
    </div>
  </ModalDialog>;
}
