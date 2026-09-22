import { useMemo, useState } from 'react';

import { Button, ModalDialog } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { records, text } from './model';
import styles from './PlanningWorkspace.module.css';

type Send = (command: TorqueCommand) => void;

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className={styles.field}><span>{label}</span>{children}</label>;
}

function LinkRows({ links, onRemove }: { links: unknown; onRemove: (item: UnknownRecord) => void }) {
  const grouped = links && typeof links === 'object' && !Array.isArray(links) ? links as UnknownRecord : {};
  const items: (UnknownRecord & { id: string })[] = Array.isArray(grouped.tasks) || Array.isArray(grouped.decisions)
    ? ['task', 'decision'].flatMap((kind) => ((grouped[`${kind}s`] as unknown[] | undefined) || []).map((id) => ({ id: `${kind}:${text(id)}`, link_type: kind, target_id: text(id) })))
    : records(links);
  return items.length ? <div className={styles.linkRows}>{items.map((link) => <div key={link.id}>
    <span><b>{text(link.link_type, text(link.target_type, 'item'))}</b> {text(link.target_title, text(link.target_id, text(link.id)))}</span>
    <Button tone="quiet" onPress={() => onRemove(link)}>Unlink</Button>
  </div>)}</div> : <p className={styles.muted}>No linked records.</p>;
}

export function InitiativeEditor({ item, tasks, decisions, send, onClose }: {
  item: UnknownRecord; tasks: UnknownRecord[]; decisions: UnknownRecord[]; send: Send; onClose: () => void;
}) {
  const [draft, setDraft] = useState(() => ({
    title: text(item.title), summary: text(item.summary), why: text(item.why),
    in_scope: text(item.in_scope), out_of_scope: text(item.out_of_scope),
    done_definition: text(item.done_definition), planning_status: text(item.planning_status, 'triage'),
    priority: text(item.priority), owner_kind: text(item.owner_kind, 'user'), owner_id: text(item.owner_id),
  }));
  const [linkType, setLinkType] = useState<'task' | 'decision'>('task');
  const [target, setTarget] = useState('');
  const update = (patch: Partial<typeof draft>) => setDraft((value) => ({ ...value, ...patch }));
  const save = () => { send({ cmd: 'initiative_update', id: text(item.id), ...draft }); onClose(); };
  const remove = (link: UnknownRecord) => {
    const type = text(link.link_type, text(link.target_type));
    const targetId = text(link.target_id, text(link.id));
    if (type === 'task') send({ cmd: 'initiative_unlink_task', id: text(item.id), task_id: targetId });
    if (type === 'decision') send({ cmd: 'initiative_unlink_decision', id: text(item.id), decision_id: targetId });
  };
  return <ModalDialog title="Initiative" description={text(item.id)} size="large" isOpen onOpenChange={(open) => { if (!open) onClose(); }}>
    <form className={styles.detailForm} onSubmit={(event) => { event.preventDefault(); save(); }}>
      <div className={styles.formGrid}>
        <Field label="Title"><input value={draft.title} onChange={(e) => update({ title: e.target.value })} /></Field>
        <Field label="Status"><select value={draft.planning_status} onChange={(e) => update({ planning_status: e.target.value })}>{['triage', 'now', 'next', 'later', 'done'].map((value) => <option key={value}>{value}</option>)}</select></Field>
        <Field label="Priority"><input value={draft.priority} onChange={(e) => update({ priority: e.target.value })} /></Field>
        <Field label="Owner kind"><select value={draft.owner_kind} onChange={(e) => update({ owner_kind: e.target.value })}>{['user', 'architect', 'engineer'].map((value) => <option key={value}>{value}</option>)}</select></Field>
        <Field label="Owner ID"><input value={draft.owner_id} onChange={(e) => update({ owner_id: e.target.value })} /></Field>
      </div>
      <Field label="Summary"><textarea value={draft.summary} onChange={(e) => update({ summary: e.target.value })} /></Field>
      <Field label="Why this matters"><textarea value={draft.why} onChange={(e) => update({ why: e.target.value })} /></Field>
      <div className={styles.formGrid}><Field label="In scope"><textarea value={draft.in_scope} onChange={(e) => update({ in_scope: e.target.value })} /></Field><Field label="Out of scope"><textarea value={draft.out_of_scope} onChange={(e) => update({ out_of_scope: e.target.value })} /></Field></div>
      <Field label="Definition of done"><textarea value={draft.done_definition} onChange={(e) => update({ done_definition: e.target.value })} /></Field>
      <section className={styles.embeddedSection}><h3>Linked work</h3><LinkRows links={item.links} onRemove={remove} />
        <div className={styles.inlineComposer}><select aria-label="Link type" value={linkType} onChange={(e) => { setLinkType(e.target.value as 'task' | 'decision'); setTarget(''); }}><option value="task">Task</option><option value="decision">Decision</option></select><select aria-label="Linked record" value={target} onChange={(e) => setTarget(e.target.value)}><option value="">Choose…</option>{(linkType === 'task' ? tasks : decisions).map((entry) => <option key={text(entry.id)} value={text(entry.id)}>{text(entry.title, text(entry.task, text(entry.id)))}</option>)}</select><Button tone="quiet" isDisabled={!target} onPress={() => { send({ cmd: linkType === 'task' ? 'initiative_link_task' : 'initiative_link_decision', id: text(item.id), [`${linkType}_id`]: target }); setTarget(''); }}>Link</Button></div>
      </section>
      <footer className={styles.detailFooter}><Button tone="danger" onPress={() => { send({ cmd: 'initiative_archive', id: text(item.id) }); onClose(); }}>Archive</Button><span /><Button tone="quiet" onPress={onClose}>Cancel</Button><Button tone="primary" type="submit" isDisabled={!draft.title.trim()}>Save</Button></footer>
    </form>
  </ModalDialog>;
}

export function DecisionEditor({ item, tasks, engineers, send, onClose }: { item: UnknownRecord; tasks: UnknownRecord[]; engineers: UnknownRecord[]; send: Send; onClose: () => void }) {
  const [draft, setDraft] = useState({ title: text(item.title), rationale: text(item.rationale), status: text(item.status, 'proposed') });
  const [taskId, setTaskId] = useState(''); const [engineerId, setEngineerId] = useState('');
  const architectId = text(item.architect_id);
  const links = useMemo(() => [...records(item.linked_tasks ?? item.tasks), ...records(item.linked_engineers ?? item.engineers)], [item]);
  return <ModalDialog title="Architect decision" description={text(item.id)} size="large" isOpen onOpenChange={(open) => { if (!open) onClose(); }}><form className={styles.detailForm} onSubmit={(event) => { event.preventDefault(); send({ cmd: 'architect_decision_update', architect_id: architectId, id: text(item.id), ...draft }); onClose(); }}><div className={styles.formGrid}><Field label="Title"><input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></Field><Field label="Status"><select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>{['proposed', 'accepted', 'superseded', 'rejected'].map((value) => <option key={value}>{value}</option>)}</select></Field></div><Field label="Rationale"><textarea value={draft.rationale} onChange={(e) => setDraft({ ...draft, rationale: e.target.value })} /></Field><section className={styles.embeddedSection}><h3>Linked records</h3>{links.length ? links.map((link) => <p key={link.id}>{text(link.title, text(link.name, link.id))}</p>) : <p className={styles.muted}>No linked work.</p>}<div className={styles.inlineComposer}><select aria-label="Task to link" value={taskId} onChange={(e) => setTaskId(e.target.value)}><option value="">Link task…</option>{tasks.map((task) => <option key={text(task.id)} value={text(task.id)}>{text(task.title, text(task.task, text(task.id)))}</option>)}</select><Button tone="quiet" isDisabled={!taskId} onPress={() => { send({ cmd: 'architect_decision_link', architect_id: architectId, id: text(item.id), task_id: taskId }); setTaskId(''); }}>Link</Button><select aria-label="Engineer to link" value={engineerId} onChange={(e) => setEngineerId(e.target.value)}><option value="">Link engineer…</option>{engineers.map((agent) => <option key={text(agent.id)} value={text(agent.id)}>{text(agent.name, text(agent.id))}</option>)}</select><Button tone="quiet" isDisabled={!engineerId} onPress={() => { send({ cmd: 'architect_decision_link', architect_id: architectId, id: text(item.id), engineer_id: engineerId }); setEngineerId(''); }}>Link</Button></div></section><footer className={styles.detailFooter}><Button tone="danger" onPress={() => { send({ cmd: 'architect_decision_update', architect_id: architectId, id: text(item.id), archived: true }); onClose(); }}>Archive</Button><span /><Button tone="quiet" onPress={onClose}>Cancel</Button><Button tone="primary" type="submit">Save</Button></footer></form></ModalDialog>;
}
