import type { ReactNode } from 'react';
import { useState } from 'react';
import { Button, ModalDialog } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { decisionStatuses, planningStatuses, records, text } from './model';
import { usePlanningEditor } from './usePlanningEditor';
import { usePlanningMutation } from './usePlanningMutation';
import { InitiativeTaskCreator } from './InitiativeTaskCreator';
import styles from './PlanningWorkspace.module.css';

const initiativeFields = { title: '', summary: '', why: '', in_scope: '', out_of_scope: '', done_definition: '', planning_status: 'triage', priority: '', owner_kind: 'user', owner_id: '' };
const decisionFields = { title: '', rationale: '', status: 'proposed', supersedes: '' };
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className={styles.field}><span>{label}</span>{children}</label>;
}
function stringIds(value: unknown): string[] { return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : []; }

export function InitiativeEditor({ item, tasks, decisions, onClose, readStatus }: {
  item: UnknownRecord; tasks: UnknownRecord[]; decisions: UnknownRecord[]; onClose: () => void; readStatus?: ReactNode;
}) {
  const editor = usePlanningEditor('initiative', item, initiativeFields);
  const { detail, draft, change } = editor;
  const mutation = usePlanningMutation();
  const id = text(item.id);
  const [linkType, setLinkType] = useState<'task' | 'decision'>('task');
  const [target, setTarget] = useState('');
  const archived = Boolean(detail.archived || detail.archived_at);
  const grouped = detail.links && typeof detail.links === 'object' && !Array.isArray(detail.links) ? detail.links as UnknownRecord : {};
  const links = Array.isArray(grouped.tasks) || Array.isArray(grouped.decisions)
    ? ['task', 'decision'].flatMap((kind) => stringIds(grouped[`${kind}s`]).map((targetId) => ({ id: `${kind}:${targetId}`, link_type: kind, target_id: targetId })))
    : records(detail.links);
  const available = (linkType === 'task' ? tasks : decisions).filter((entry) => !links.some((link) => link.link_type === linkType && link.target_id === entry.id));
  const mutateLink = (command: TorqueCommand) => { void mutation.run(async (request) => { await request(command); setTarget(''); editor.reload(); }); };
  const save = (archive = false) => { void mutation.run(async (request) => {
    if (!editor.loaded || !draft.title?.trim() || archived) return;
    const patch = editor.patch();
    if (Object.keys(patch).length) await request({ cmd: 'initiative_update', id, ...patch });
    if (archive) await request({ cmd: 'initiative_archive', id });
    onClose();
  }); };
  return <ModalDialog title="Initiative" description={id} size="large" isOpen onOpenChange={(open) => { if (!open && !mutation.busy.current) onClose(); }}>
    <form className={styles.detailForm} onSubmit={(event) => { event.preventDefault(); save(); }}>
      {readStatus}
      {mutation.error ? <p role="alert">{mutation.error}</p> : null}
      {editor.loadError ? <p role="alert">{editor.loadError} <Button onPress={editor.reload}>Retry details</Button></p> : null}
      {!editor.loaded ? <p>Loading full details…</p> : null}
      <fieldset className={styles.editorFields} disabled={mutation.pending || archived}>
        <div className={styles.formGrid}>
          <Field label="Title"><input value={draft.title} onChange={(event) => change({ title: event.target.value })} /></Field>
          <Field label="Status"><select value={draft.planning_status} onChange={(event) => change({ planning_status: event.target.value })}>{planningStatuses.map((status) => <option key={status}>{status}</option>)}</select></Field>
          <Field label="Priority"><input value={draft.priority} onChange={(event) => change({ priority: event.target.value })} /></Field>
          <Field label="Owner kind"><select value={draft.owner_kind} onChange={(event) => change({ owner_kind: event.target.value, owner_id: '' })}>{['user', 'architect', 'engineer'].map((kind) => <option key={kind}>{kind}</option>)}</select></Field>
          <Field label="Owner ID"><input value={draft.owner_id} onChange={(event) => change({ owner_id: event.target.value })} /></Field>
        </div>
        {Object.entries({ summary: 'Summary', why: 'Why this matters', in_scope: 'In scope', out_of_scope: 'Out of scope', done_definition: 'Definition of done' }).map(([key, label]) => <Field key={key} label={label}><textarea aria-label={label} value={draft[key]} onChange={(event) => change({ [key]: event.target.value })} /></Field>)}
        <section className={styles.embeddedSection}><h3>Linked work</h3>
          <InitiativeTaskCreator initiative={{ ...detail, ...draft, id }} disabled={!editor.loaded || mutation.pending || archived} onLinked={editor.reload} />
          <div className={styles.linkRows}>{links.map((link) => {
            const kind = text(link.link_type); const targetId = text(link.target_id);
            const targetItem = (kind === 'task' ? tasks : decisions).find((entry) => entry.id === targetId);
            return <div key={link.id}><span><b>{kind}</b> {text(targetItem?.title, text(targetItem?.task, targetId))}</span><Button isDisabled={!editor.loaded} onPress={() => mutateLink({ cmd: `initiative_unlink_${kind}`, id, [`${kind}_id`]: targetId })}>Unlink</Button></div>;
          })}</div>{!links.length ? <p>No linked records.</p> : null}
          <div className={styles.inlineComposer}><select aria-label="Link type" value={linkType} onChange={(event) => { setLinkType(event.target.value as 'task' | 'decision'); setTarget(''); }}><option value="task">Task</option><option value="decision">Decision</option></select><select aria-label="Linked record" value={target} onChange={(event) => setTarget(event.target.value)}><option value="">Choose…</option>{available.map((entry) => <option key={text(entry.id)} value={text(entry.id)}>{text(entry.title, text(entry.task, text(entry.id)))}</option>)}</select><Button isDisabled={!editor.loaded || !target} onPress={() => mutateLink({ cmd: `initiative_link_${linkType}`, id, [`${linkType}_id`]: target })}>Link</Button></div>
        </section>
      </fieldset>
      <footer className={styles.detailFooter}>{!archived ? <Button tone="danger" isDisabled={mutation.pending || !editor.loaded || !draft.title?.trim()} onPress={() => save(true)}>Archive</Button> : <span>Archived · read only</span>}<span /><Button isDisabled={mutation.pending} onPress={onClose}>{archived ? 'Close' : 'Cancel'}</Button>{!archived ? <Button tone="primary" type="submit" isDisabled={mutation.pending || !editor.loaded || !draft.title?.trim()}>Save</Button> : null}</footer>
    </form>
  </ModalDialog>;
}

export function DecisionEditor({ item, tasks, engineers, decisions, onClose, readStatus }: {
  item: UnknownRecord; tasks: UnknownRecord[]; engineers: UnknownRecord[]; decisions: UnknownRecord[]; onClose: () => void; readStatus?: ReactNode;
}) {
  const editor = usePlanningEditor('decision', item, decisionFields);
  const { detail, draft, change } = editor;
  const mutation = usePlanningMutation();
  const id = text(item.id); const architectId = text(detail.architect_id);
  const [taskId, setTaskId] = useState(''); const [engineerId, setEngineerId] = useState('');
  const taskIds = stringIds(detail.linked_task_ids); const engineerIds = stringIds(detail.linked_engineer_ids);
  const archived = Boolean(detail.archived);
  const valid = Boolean(draft.title?.trim() && draft.rationale?.trim());
  const mutate = (command: TorqueCommand, close = false) => { void mutation.run(async (request) => {
    if (!editor.loaded) return;
    await request({ ...command, id, architect_id: architectId });
    if (close) onClose(); else { setTaskId(''); setEngineerId(''); editor.reload(); }
  }); };
  const save = () => { if (!valid || archived) return; const patch = editor.patch(); if (!Object.keys(patch).length) { onClose(); return; } mutate({ cmd: 'architect_decision_update', ...patch }, true); };
  return <ModalDialog title="Architect decision" description={id} size="large" isOpen onOpenChange={(open) => { if (!open && !mutation.busy.current) onClose(); }}>
    <form className={styles.detailForm} onSubmit={(event) => { event.preventDefault(); save(); }}>
      {readStatus}
      {mutation.error ? <p role="alert">{mutation.error}</p> : null}
      {editor.loadError ? <p role="alert">{editor.loadError} <Button onPress={editor.reload}>Retry details</Button></p> : null}
      {!editor.loaded ? <p>Loading full details…</p> : null}
      <fieldset disabled={mutation.pending || archived} className={styles.editorFields}>
        <div className={styles.formGrid}><Field label="Title"><input value={draft.title} onChange={(event) => change({ title: event.target.value })} /></Field><Field label="Status"><select aria-label="Status" value={draft.status} onChange={(event) => change({ status: event.target.value })}>{decisionStatuses.map((status) => <option key={status}>{status}</option>)}</select></Field></div>
        <Field label="Rationale"><textarea aria-label="Rationale" value={draft.rationale} onChange={(event) => change({ rationale: event.target.value })} /></Field>
        <Field label="Supersedes"><select aria-label="Supersedes" value={draft.supersedes} onChange={(event) => change({ supersedes: event.target.value })}><option value="">No prior decision</option>{draft.supersedes && !decisions.some((entry) => entry.id === draft.supersedes) ? <option value={draft.supersedes}>{draft.supersedes}</option> : null}{decisions.filter((entry) => entry.architect_id === architectId && entry.id !== id).map((entry) => <option key={text(entry.id)} value={text(entry.id)}>{text(entry.title, text(entry.id))}</option>)}</select></Field>
        <section className={styles.embeddedSection}><h3>Linked records</h3>
          <div className={styles.linkRows}>{taskIds.map((target) => <div key={`task:${target}`}><span>Task · {text(tasks.find((entry) => entry.id === target)?.task, target)}</span><Button isDisabled={!editor.loaded} onPress={() => mutate({ cmd: 'architect_decision_update', linked_task_ids: taskIds.filter((entry) => entry !== target) })}>Unlink task {target}</Button></div>)}{engineerIds.map((target) => <div key={`engineer:${target}`}><span>Engineer · {text(engineers.find((entry) => entry.id === target)?.name, target)}</span><Button isDisabled={!editor.loaded} onPress={() => mutate({ cmd: 'architect_decision_update', linked_engineer_ids: engineerIds.filter((entry) => entry !== target) })}>Unlink engineer {target}</Button></div>)}</div>
          {!taskIds.length && !engineerIds.length ? <p>No linked work.</p> : null}
          <div className={styles.inlineComposer}><select aria-label="Task to link" value={taskId} onChange={(event) => setTaskId(event.target.value)}><option value="">Link task…</option>{tasks.filter((entry) => !taskIds.includes(text(entry.id))).map((task) => <option key={text(task.id)} value={text(task.id)}>{text(task.title, text(task.task, text(task.id)))}</option>)}</select><Button isDisabled={!editor.loaded || !taskId} onPress={() => mutate({ cmd: 'architect_decision_link', task_id: taskId })}>Link task</Button><select aria-label="Engineer to link" value={engineerId} onChange={(event) => setEngineerId(event.target.value)}><option value="">Link engineer…</option>{engineers.filter((entry) => !engineerIds.includes(text(entry.id)) && (!entry.hired_by_architect_id || entry.hired_by_architect_id === architectId) && !entry.deleted_at && !entry.dismissed_at).map((agent) => <option key={text(agent.id)} value={text(agent.id)}>{text(agent.name, text(agent.id))}</option>)}</select><Button isDisabled={!editor.loaded || !engineerId} onPress={() => mutate({ cmd: 'architect_decision_link', engineer_id: engineerId })}>Link engineer</Button></div>
        </section>
      </fieldset>
      <footer className={styles.detailFooter}><Button tone={archived ? 'quiet' : 'danger'} isDisabled={mutation.pending || !editor.loaded || (!archived && !valid)} onPress={() => mutate({ cmd: 'architect_decision_update', ...(!archived ? editor.patch() : {}), archived: !archived }, true)}>{archived ? 'Restore' : 'Archive'}</Button><span /><Button isDisabled={mutation.pending} onPress={onClose}>{archived ? 'Close' : 'Cancel'}</Button>{!archived ? <Button tone="primary" type="submit" isDisabled={mutation.pending || !editor.loaded || !valid}>Save</Button> : null}</footer>
    </form>
  </ModalDialog>;
}
