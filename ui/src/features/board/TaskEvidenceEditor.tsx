import { useEffect, useRef, useState } from 'react';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import styles from './BoardPanel.module.css';

import { evidenceFilename, evidenceUrl } from './taskEvidenceModel';
import { ArtifactPreview } from './ArtifactPreview';
import { artifactTypes, defaultPrompt, taskText } from './taskCreationModel';
function object(value: unknown): UnknownRecord { return value && typeof value === 'object' ? value as UnknownRecord : {}; }
interface Props {
  artifacts: UnknownRecord[]; attachments: UnknownRecord[]; draftId: string;
  onChange: (artifacts: UnknownRecord[]) => void;
  onRemove: (kind: 'artifact' | 'attachment', index: number) => void;
  onUpload: (files: File[]) => void;
  onEditingChange: (editing: boolean) => void;
}
export function TaskEvidenceEditor({ artifacts, attachments, draftId, onChange, onRemove, onUpload, onEditingChange }: Props) {
  const [editing, setEditing] = useState<{ index: number; value: UnknownRecord } | null>(null);
  const titleInput = useRef<HTMLInputElement>(null);
  const editingIndex = editing?.index;
  useEffect(() => { if (editingIndex !== undefined) titleInput.current?.focus(); }, [editingIndex]);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<UnknownRecord | null>(null);
  const edit = (next: typeof editing) => { setEditing(next); onEditingChange(Boolean(next)); };
  const change = (patch: UnknownRecord) => { if (editing) setEditing({ ...editing, value: { ...editing.value, ...patch } }); };
  const save = () => {
    if (!editing) return;
    const source = editing.value; const path = taskText(source.path ?? '').trim(); const content = taskText(source.content ?? '');
    if (source.type === 'file_ref' && !path) { setError('A file reference needs a path.'); return; }
    if (!taskText(source.title ?? '').trim() && !path && !content.trim()) { setError('Add a title, path or content.'); return; }
    const start = source.line_start === '' || source.line_start == null ? null : Number(source.line_start);
    const end = source.line_end === '' || source.line_end == null ? null : Number(source.line_end);
    if ([start, end].some((number) => number !== null && (!Number.isInteger(number) || number < 1)) || (start !== null && end !== null && end < start)) { setError('Line numbers must be positive and the end must follow the start.'); return; }
    const value = { ...source, id: source.id || `artifact-${crypto.randomUUID()}`, title: taskText(source.title || source.filename || path || source.type), path, content, filename: evidenceFilename(source) || path.split('/').pop() || '', line_start: start, line_end: end, storage: { ...object(source.storage), kind: path ? source.type === 'file_ref' ? 'file_ref' : 'path' : 'inline', path, content, line_start: start, line_end: end } };
    onChange(editing.index < 0 ? [...artifacts, value] : artifacts.map((item, index) => index === editing.index ? value : item));
    edit(null); setError('');
  };
  return <><section className={styles.artifacts} aria-label="Task evidence" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); onUpload([...event.dataTransfer.files]); }}>
    <header><h3>Attachments and artifacts</h3><label>Upload evidence files<input aria-label="Upload evidence files" type="file" disabled={Boolean(editing)} multiple onChange={(event) => { onUpload([...(event.target.files ?? [])]); event.target.value = ''; }} /></label></header>
    <p>Choose or drop files here, or paste an image into the form.</p>
    <ul>{attachments.map((item, index) => <li key={`attachment:${taskText(item.filename)}`}>
      <a href={evidenceUrl(draftId, item)} target="_blank" rel="noreferrer">{taskText(item.filename)}</a>
      <div className={styles.evidenceActions}><Button aria-label={`Preview attachment ${taskText(item.filename)}`} onPress={() => setPreview(item)}>Preview</Button><Button aria-label={`Remove attachment ${taskText(item.filename)}`} isDisabled={Boolean(editing)} onPress={() => onRemove('attachment', index)}>Remove</Button></div>
    </li>)}{artifacts.map((item, index) => <li key={taskText(item.id, `artifact-${index}`)}>
      <div><strong>{taskText(item.title)}</strong><small>{taskText(item.type)} · {taskText(object(item.prompt).mode ?? 'auto')}</small>{item.path ? <code>{taskText(item.path)}</code> : null}{item.content ? <details><summary>Preview content</summary><pre>{taskText(item.content)}</pre></details> : null}{evidenceUrl(draftId, item) ? <a href={evidenceUrl(draftId, item)} target="_blank" rel="noreferrer">Open file</a> : null}</div>
      <div className={styles.evidenceActions}><Button aria-label={`Preview artifact ${taskText(item.title)}`} onPress={() => setPreview(item)}>Preview</Button><Button aria-label={`Edit artifact ${taskText(item.title)}`} isDisabled={Boolean(editing)} onPress={() => { setError(''); edit({ index, value: structuredClone(item) }); }}>Edit</Button><Button aria-label={`Remove artifact ${taskText(item.title)}`} isDisabled={Boolean(editing)} onPress={() => onRemove('artifact', index)}>Remove</Button></div>
    </li>)}</ul>
    {!editing ? <Button onPress={() => edit({ index: -1, value: { type: 'snippet', title: '', summary: '', path: '', content: '', prompt: { mode: 'inline' } } })}>Add structured artifact</Button> : <section className={styles.artifactComposer} aria-label="Artifact editor">
      <label>Artifact type<select value={taskText(editing.value.type)} onChange={(event) => change({ type: event.target.value, prompt: { mode: defaultPrompt(event.target.value) } })}>{artifactTypes.map((type) => <option key={type}>{type}</option>)}</select></label>
      <label>Artifact title<input ref={titleInput} value={taskText(editing.value.title ?? '')} onChange={(event) => change({ title: event.target.value })} /></label>
      <label>Artifact summary<textarea value={taskText(editing.value.summary ?? '')} onChange={(event) => change({ summary: event.target.value })} /></label>
      <label>Prompt mode<select value={taskText(object(editing.value.prompt).mode ?? 'auto')} onChange={(event) => change({ prompt: { ...object(editing.value.prompt), mode: event.target.value } })}>{['auto', 'path', 'summary', 'inline', 'none'].map((mode) => <option key={mode}>{mode}</option>)}</select></label>
      <label>Artifact path<input readOnly={Boolean(evidenceFilename(editing.value))} value={taskText(editing.value.path ?? '')} onChange={(event) => change({ path: event.target.value })} /></label>
      <label>Line start<input type="number" min={1} value={taskText(editing.value.line_start ?? '')} onChange={(event) => change({ line_start: event.target.value })} /></label>
      <label>Line end<input type="number" min={1} value={taskText(editing.value.line_end ?? '')} onChange={(event) => change({ line_end: event.target.value })} /></label>
      <label>Artifact content<textarea value={taskText(editing.value.content ?? '')} onChange={(event) => change({ content: event.target.value })} /></label>
      {error ? <p role="alert">{error}</p> : null}<Button onPress={() => { edit(null); setError(''); }}>Cancel artifact edit</Button><Button onPress={save}>Save artifact</Button>
    </section>}
  </section>{preview ? <ArtifactPreview item={preview} taskId={draftId} onClose={() => setPreview(null)} /> : null}</>;
}
