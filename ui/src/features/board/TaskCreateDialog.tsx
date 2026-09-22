import { useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectGroupsState, selectTasksState } from '../../app/store';
import { Button, ModalDialog } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { VerificationFields, type VerificationDraft } from './VerificationFields';
import { TaskEvidenceEditor } from './TaskEvidenceEditor';
import { taskText } from './taskCreationModel';
import { uploadedEvidence } from './taskEvidenceModel';
import { ActionVariableFields } from './ActionVariableFields';
import { actionVariableDefinitions, resolveActionVariables, useActionVariables } from './actionVariables';
import { TaskPromptPreview } from './TaskPromptPreview';
import styles from './BoardPanel.module.css';

function record(value: unknown): UnknownRecord { return value && typeof value === 'object' ? value as UnknownRecord : {}; }
function options(value: unknown, role = false) {
  const items = (Array.isArray(value) ? value : Object.values(record(value))).map(record);
  return [...new Map(items.map((item) => [taskText(role ? item.slug || item.name : item.name), item])).entries()].filter(([name]) => name && name !== 'undefined');
}
export interface TaskCreateDialogProps {
  group: string; lanes: string[]; actions: unknown; roles: unknown; onClose: () => void;
  initialValues?: { title: string; description: string };
  notice?: ReactNode;
  createdTaskId?: string;
  onCreated?: (id: string) => void;
  afterCreate?: (id: string) => Promise<void>;
}
export function TaskCreateDialog({ group, lanes, actions, roles, onClose, initialValues, createdTaskId = '', onCreated, afterCreate, notice }: TaskCreateDialogProps) {
  const dispatch = useAppDispatch();
  const { records: taskRecords } = useAppSelector(selectTasksState);
  const [title, setTitle] = useState(initialValues?.title ?? '');
  const [description, setDescription] = useState(initialValues?.description ?? '');
  const [lane, setLane] = useState(initialValues ? '' : lanes[0] ?? '');
  const [labels, setLabels] = useState('');
  const [actionName, setActionName] = useState(''); const [role, setRole] = useState('');
  const [scheduledAt, setScheduledAt] = useState('');
  const { settings } = useAppSelector(selectGroupsState);
  const effectiveAction = actionName || taskText(record(settings[group]).board_default_action);
  const definitions = actionVariableDefinitions(actions, effectiveAction);
  const [actionVars, setActionVars] = useActionVariables(effectiveAction);
  const [provider, setProvider] = useState(''); const [externalId, setExternalId] = useState(''); const [externalUrl, setExternalUrl] = useState('');
  const [dependsOn, setDependsOn] = useState<string[]>([]); const [search, setSearch] = useState(''); const [dependency, setDependency] = useState('');
  const [verification, setVerification] = useState<VerificationDraft>({ mode: '', state: '', notes: '', summary: {} });
  const [attachments, setAttachments] = useState<UnknownRecord[]>([]); const [artifacts, setArtifacts] = useState<UnknownRecord[]>([]);
  const [artifactEditing, setArtifactEditing] = useState(false);
  const [draftId] = useState(() => `draft-${crypto.randomUUID()}`);
  const uploaded = useRef(false); const busy = useRef(false); const created = useRef(createdTaskId);
  const [createdId, setCreatedId] = useState(createdTaskId); const [pending, setPending] = useState(false); const [error, setError] = useState('');
  const request = async (command: TorqueCommand) => {
    const frame = await readCommand(command, new AbortController().signal);
    if (frame.type === 'error') throw new Error(taskText(frame.message || 'The request failed.'));
    dispatch(projectionActions.auxiliaryResourceReceived(frame)); return frame;
  };
  const run = async (operation: () => Promise<void>) => {
    if (busy.current) return;
    busy.current = true; setPending(true); setError('');
    try { await operation(); } catch (cause) { setError(cause instanceof Error ? cause.message : 'The request failed. Your draft is retained.'); }
    finally { busy.current = false; setPending(false); }
  };
  const close = () => { void run(async () => {
    if (!created.current && uploaded.current) {
      const response = await fetch('/api/upload/cleanup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task_id: draftId }) });
      const result = await response.json() as { ok?: boolean; error?: string };
      if (!response.ok || !result.ok) throw new Error(result.error || 'Could not discard draft uploads. Retry closing.');
    }
    onClose();
  }); };
  const upload = (files: File[]) => { if (!files.length || created.current) return; void run(async () => {
    for (const file of files) {
      const body = new FormData(); body.append('task_id', draftId); body.append('file', file); uploaded.current = true;
      const response = await fetch('/api/upload', { method: 'POST', body });
      const result = await response.json() as { ok?: boolean; error?: string; data?: UnknownRecord[] };
      if (!response.ok || !result.ok || !result.data?.length) throw new Error(result.error || `Could not upload ${file.name}.`);
      for (const entry of result.data) {
        const prepared = await uploadedEvidence(entry, file);
        if (prepared.kind === 'attachment') setAttachments((current) => [...current, prepared.item]);
        else setArtifacts((current) => [...current, prepared.item]);
      }
    }
  }); };
  const remove = (kind: 'artifact' | 'attachment', index: number) => { void run(async () => {
    const item = (kind === 'artifact' ? artifacts : attachments)[index];
    if (item?.filename) await request({ cmd: 'remove_attachment', task_id: draftId, filename: item.filename });
    if (kind === 'artifact') setArtifacts((current) => current.filter((_, i) => i !== index));
    else setAttachments((current) => current.filter((_, i) => i !== index));
  }); };
  const submit = (event: FormEvent) => {
    event.preventDefault(); event.stopPropagation();
    if (busy.current || artifactEditing || (!created.current && !title.trim())) return;
    void run(async () => {
      if (!created.current) {
        const parsed = resolveActionVariables(actionVars, definitions);
        const result = await request({ cmd: 'board_add_task', id: draftId, task: title.trim(), description: description.trim(), group, lane, labels: labels.split(',').map((value) => value.trim()).filter(Boolean), action_name: actionName, agent_template: role, action_vars: parsed, provider: provider.trim(), external_id: externalId.trim(), external_url: externalUrl.trim(), scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : '', depends_on: dependsOn, verification_mode: verification.mode, verification_state: verification.state, verification_notes: verification.notes, verification_summary: verification.summary, attachments, artifacts });
        const id = typeof result.task_id === 'string' ? result.task_id : '';
        if (!id) throw new Error('Task creation returned no ID. Check the Board before retrying.');
        created.current = id; setCreatedId(id); onCreated?.(id);
      }
      await afterCreate?.(created.current);
      onClose();
    });
  };
  const matches = Object.entries(taskRecords).map<UnknownRecord & { id: string }>(([id, value]) => ({ ...record(value), id })).filter((task) => !dependsOn.includes(task.id) && `${task.id} ${taskText(task.task ?? '')}`.toLowerCase().includes(search.toLowerCase()));
  return <ModalDialog title={initialValues ? 'Create Board task' : 'Create task'} description={`Add work to ${group}`} size="large" isOpen onOpenChange={(open) => { if (!open) close(); }}>
    {notice}
    <form className={styles.detailForm} onSubmit={submit} onPaste={(event) => { if (event.clipboardData.files.length) { event.preventDefault(); upload([...event.clipboardData.files]); } }}>
      {createdId ? <p role="status">Task {createdId} was created. Retry linking this task to the Initiative.</p> : null}
      <fieldset className={styles.createFields} disabled={pending || Boolean(createdId)}>
        <label>Title<input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>Description<textarea rows={4} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        <div className={styles.formGrid}>
          <label>Lane<select value={lane} onChange={(event) => setLane(event.target.value)}><option value="">Group default</option>{lanes.map((name) => <option key={name}>{name}</option>)}</select></label>
          <label>Labels<input value={labels} onChange={(event) => setLabels(event.target.value)} /></label>
          <label>Action<select value={actionName} onChange={(event) => setActionName(event.target.value)}><option value="">Group default</option>{options(actions).map(([name]) => <option key={name}>{name}</option>)}</select></label>
          <label>Worker role<select value={role} onChange={(event) => setRole(event.target.value)}><option value="">Action/default role</option>{options(roles, true).map(([name, item]) => <option key={name} value={name}>{taskText(item.name || name)}</option>)}</select></label>
          <label>Schedule<input type="datetime-local" value={scheduledAt} onChange={(event) => setScheduledAt(event.target.value)} /></label>
        </div>
        <ActionVariableFields definitions={definitions} value={actionVars} onChange={setActionVars} />
        <details><summary>External ticket</summary><div className={styles.formGrid}><label>Provider<input value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="github" /></label><label>External ID<input value={externalId} onChange={(event) => setExternalId(event.target.value)} placeholder="owner/repo#123" /></label><label>External URL<input value={externalUrl} onChange={(event) => setExternalUrl(event.target.value)} /></label></div></details>
        <details><summary>Dependencies · {dependsOn.length}</summary><div className={styles.detailSection}>
          <ul>{dependsOn.map((id) => <li key={id}>{taskText(record(taskRecords[id]).task || id)} · {id} <Button onPress={() => setDependsOn((current) => current.filter((value) => value !== id))}>Remove dependency {id}</Button></li>)}</ul>
          <label>Search dependencies<input value={search} onChange={(event) => { setSearch(event.target.value); setDependency(''); }} /></label>
          <label>Dependency to add<select value={dependency} onChange={(event) => setDependency(event.target.value)}><option value="">Choose task…</option>{matches.map((task) => <option key={task.id} value={task.id}>{taskText(task.task)} · {task.id}</option>)}</select></label>
          <Button isDisabled={!dependency} onPress={() => { setDependsOn((current) => [...new Set([...current, dependency])]); setDependency(''); setSearch(''); }}>Add dependency</Button>
        </div></details>
        <details><summary>Verification</summary><div className={styles.detailSection}><VerificationFields value={verification} onChange={setVerification} /></div></details>
        <details><summary>Attachments and artifacts · {attachments.length + artifacts.length}</summary><TaskEvidenceEditor artifacts={artifacts} attachments={attachments} draftId={createdId || draftId} onChange={setArtifacts} onRemove={remove} onUpload={upload} onEditingChange={setArtifactEditing} /></details>
      </fieldset>
      {!createdId ? <TaskPromptPreview disabled={pending || artifactEditing || !title.trim()} inputsKey={JSON.stringify([title, description, effectiveAction, role, actionVars, definitions, group, attachments, artifacts])} command={() => ({ cmd: 'preview_prompt', task: title.trim(), description: description.trim(), action_name: effectiveAction, agent_template: role, action_vars: resolveActionVariables(actionVars, definitions), group, attachments, artifacts })} /> : null}
      {error ? <p role="alert" className={styles.formError}>{error}</p> : null}
      <footer className={styles.detailFooter}><span /><Button isDisabled={pending} onPress={close}>{createdId ? 'Close' : 'Cancel'}</Button><Button tone="primary" type="submit" isDisabled={pending || artifactEditing || (!createdId && !title.trim())}>{createdId ? 'Retry link' : 'Create task'}</Button></footer>
    </form>
  </ModalDialog>;
}
