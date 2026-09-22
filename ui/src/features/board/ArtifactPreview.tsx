import { useEffect, useState } from 'react';
import { Button, ModalDialog } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { taskText } from './taskCreationModel';
import { evidencePreviewKind, evidenceUrl } from './taskEvidenceModel';
import styles from './BoardPanel.module.css';

export function ArtifactPreview({ item, taskId, onClose }: { item: UnknownRecord; taskId: string; onClose: () => void }) {
  const title = taskText(item.title || item.filename, 'Artifact');
  const url = evidenceUrl(taskId, item); const kind = evidencePreviewKind(item);
  const inline = taskText(item.content ?? (item.storage as UnknownRecord | undefined)?.content);
  const [content, setContent] = useState(inline); const [error, setError] = useState('');
  const [loading, setLoading] = useState(kind === 'text' && !inline && Boolean(url));
  useEffect(() => {
    if (kind !== 'text' || inline || !url) return;
    const controller = new AbortController();
    void fetch(url, { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error('Could not load this file.');
      return response.text();
    }).then((value) => { if (!controller.signal.aborted) setContent(value); }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not load this file.'); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [inline, kind, url]);
  return <ModalDialog title={`Preview: ${title}`} description={taskText(item.type || item.mime_type)} size="large" isOpen onOpenChange={(open) => { if (!open) onClose(); }}>
    <div className={styles.artifactPreview}>
      {item.summary ? <p>{taskText(item.summary)}</p> : null}
      {item.path ? <p><code>{taskText(item.path)}</code> <Button onPress={() => { void navigator.clipboard.writeText(taskText(item.path)); }}>Copy path</Button></p> : null}
      {item.line_start ? <p>Lines {taskText(item.line_start)}{item.line_end ? `–${taskText(item.line_end)}` : ''}</p> : null}
      {loading ? <p role="status">Loading file preview…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {kind === 'image' && url ? <img src={url} alt={title} onError={() => setError('Could not load this image.')} /> : null}
      {kind === 'text' && !loading ? <pre>{content || taskText(item.summary || item.path, 'No preview content.')}</pre> : null}
      {kind === 'file' || (kind === 'image' && !url) ? <p>This reference has no inline preview.</p> : null}
      {url ? <a href={url} download>Download file</a> : null}
    </div>
  </ModalDialog>;
}
