import type { ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectConnection } from '../../app/store';
import { Button, ModalDialog } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { planningRequest } from './planningRequests';
import { usePlanningMutation } from './usePlanningMutation';
import { areaLifecycles, areaLinks, areaNoteTypes, areaRelations, type AreaTarget } from './areaModel';
import { records, text } from './model';
import styles from './PlanningWorkspace.module.css';

const emptyNote = { title: '', body: '', note_type: 'caveat', target_type: '', target_id: '' };
export function AreaEditor({ item, targets, onClose, readStatus }: {
  item: UnknownRecord; targets: Record<AreaTarget, UnknownRecord[]>; onClose: () => void; readStatus?: ReactNode;
}) {
  const dispatch = useAppDispatch();
  const reconnect = useAppSelector(selectConnection).reconnectCount;
  const id = text(item.id);
  const [draft, setDraft] = useState(() => ({ title: text(item.title), area_type: text(item.area_type), lifecycle: text(item.lifecycle, 'planned'), summary: text(item.summary), user_purpose: text(item.user_purpose), system_purpose: text(item.system_purpose), in_scope: text(item.in_scope), out_of_scope: text(item.out_of_scope), owner_kind: text(item.owner_kind, 'user'), owner_id: text(item.owner_id) }));
  const dirtyFields = useRef(new Set<string>());
  const change = (patch: Partial<typeof draft>) => { Object.keys(patch).forEach((key) => dirtyFields.current.add(key)); setDraft((current) => ({ ...current, ...patch })); };
  const [linkType, setLinkType] = useState<AreaTarget>('task');
  const [target, setTarget] = useState('');
  const [relation, setRelation] = useState('related');
  const [note, setNote] = useState(emptyNote);
  const [editingNote, setEditingNote] = useState('');
  const { run, pending, error, busy } = usePlanningMutation();
  const [loadError, setLoadError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void planningRequest({ cmd: 'area_show', id }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'area' || frame.id !== id) throw new Error('Area detail was not returned.');
      dispatch(projectionActions.auxiliaryResourceReceived(frame));
      setDraft((current) => ({ ...current, ...Object.fromEntries(Object.keys(current).filter((key) => !dirtyFields.current.has(key) && key in frame).map((key) => [key, text(frame[key])])) }));
      setLoadError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setLoadError(cause instanceof Error ? cause.message : 'Could not refresh Area details.'); });
    return () => controller.abort();
  }, [id, reconnect, retry, dispatch]);
  const mutate = (command: TorqueCommand, done?: () => void) => run(async (request) => {
    await request(command);
    done?.(); setRetry((value) => value + 1);
  });
  const resetNote = () => { setNote(emptyNote); setEditingNote(''); };
  const links = areaLinks(item.links);
  const nameFor = (kind: AreaTarget, id: string) => { const target = targets[kind]?.find((entry) => text(entry.id) === id); return text(target?.title, text(target?.task, id)); };
  const targetOptions = (kind: AreaTarget, excludeSelf = true) => targets[kind].filter((entry) => !excludeSelf || kind !== 'area' || text(entry.id) !== id).map((entry) => <option key={text(entry.id)} value={text(entry.id)}>{text(entry.title, text(entry.task, text(entry.id)))}</option>);
  return <ModalDialog title="Area" description={id} size="large" isOpen onOpenChange={(open) => { if (!open && !busy.current) onClose(); }}>
    <form className={styles.detailForm} onSubmit={(event) => { event.preventDefault(); void mutate({ cmd: 'area_update', id, ...draft }, onClose); }}>
      {readStatus}
      {error ? <p role="alert">{error}</p> : null}{loadError ? <p role="alert">{loadError} <Button onPress={() => setRetry((value) => value + 1)}>Retry Area details</Button></p> : null}
      <fieldset disabled={pending} style={{ border: 0, margin: 0, padding: 0 }}>
        <div className={styles.formGrid}><label className={styles.field}>Title<input value={draft.title} onChange={(event) => change({ title: event.target.value })} /></label><label className={styles.field}>Type<input value={draft.area_type} onChange={(event) => change({ area_type: event.target.value })} /></label><label className={styles.field}>Lifecycle<select value={draft.lifecycle} onChange={(event) => change({ lifecycle: event.target.value })}>{areaLifecycles.map((value) => <option key={value}>{value}</option>)}</select></label><label className={styles.field}>Owner kind<select value={draft.owner_kind} onChange={(event) => change({ owner_kind: event.target.value })}>{['user', 'architect', 'engineer'].map((value) => <option key={value}>{value}</option>)}</select></label><label className={styles.field}>Owner ID<input value={draft.owner_id} onChange={(event) => change({ owner_id: event.target.value })} /></label></div>
        {(['summary', 'user_purpose', 'system_purpose', 'in_scope', 'out_of_scope'] as const).map((key) => <label key={key} className={styles.field}>{key.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())}<textarea aria-label={key.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())} value={draft[key]} onChange={(event) => change({ [key]: event.target.value })} /></label>)}
        <section className={styles.embeddedSection}><h3>Relationships</h3><div className={styles.linkRows}>{links.map((link) => <div key={`${text(link.link_type)}:${text(link.target_id)}:${text(link.relation)}`}><span><b>{text(link.link_type)}</b> {nameFor(text(link.link_type) as AreaTarget, text(link.target_id))}{text(link.relation) ? ` · ${text(link.relation)}` : ''}</span><Button onPress={() => { void mutate({ cmd: `area_unlink_${text(link.link_type)}`, id, target_id: text(link.target_id), relation: text(link.relation) }); }}>Unlink {text(link.link_type)} {text(link.target_id)}</Button></div>)}</div>{!links.length ? <p>No linked records.</p> : null}
          <div className={styles.inlineComposer}><select aria-label="Relationship type" value={linkType} onChange={(event) => { setLinkType(event.target.value as AreaTarget); setTarget(''); }}><option>task</option><option>decision</option><option>initiative</option><option>area</option></select><select aria-label="Relationship target" value={target} onChange={(event) => setTarget(event.target.value)}><option value="">Choose…</option>{targetOptions(linkType)}</select>{linkType === 'area' ? <select aria-label="Relationship label" value={relation} onChange={(event) => setRelation(event.target.value)}>{areaRelations.map((value) => <option key={value}>{value}</option>)}</select> : null}<Button isDisabled={!target || pending} onPress={() => { void mutate({ cmd: `area_link_${linkType}`, id, target_id: target, relation: linkType === 'area' ? relation : '' }, () => setTarget('')); }}>Link</Button></div>
        </section>
        <section className={styles.embeddedSection}><h3>Area notes</h3><p>Latest 50 active notes.</p><div className={styles.noteRows}>{records(item.notes).filter((entry) => !entry.archived && !text(entry.archived_at)).map((entry) => <article key={entry.id}><div><b>{text(entry.title, text(entry.note_type, 'Note'))}</b><p>{text(entry.body)}</p>{text(entry.target_type) ? <small>{text(entry.target_type)} · {nameFor(text(entry.target_type) as AreaTarget, text(entry.target_id))}</small> : null}</div><Button onPress={() => { setEditingNote(text(entry.id)); setNote({ title: text(entry.title), body: text(entry.body), note_type: text(entry.note_type), target_type: text(entry.target_type), target_id: text(entry.target_id) }); }}>Edit note {text(entry.title, text(entry.id))}</Button><Button onPress={() => { void mutate({ cmd: 'area_note_archive', id, note_id: text(entry.id) }, () => { if (editingNote === text(entry.id)) resetNote(); }); }}>Archive note {text(entry.title, text(entry.id))}</Button></article>)}</div>
          <div className={styles.noteComposer}><input aria-label="Note title" placeholder="Note title" value={note.title} onChange={(event) => setNote({ ...note, title: event.target.value })} /><select aria-label="Note type" value={note.note_type} onChange={(event) => setNote({ ...note, note_type: event.target.value })}>{areaNoteTypes.map((value) => <option key={value}>{value}</option>)}</select><textarea aria-label="Note body" placeholder="Durable context…" value={note.body} onChange={(event) => setNote({ ...note, body: event.target.value })} /><select aria-label="Note target type" value={note.target_type} onChange={(event) => setNote({ ...note, target_type: event.target.value, target_id: '' })}><option value="">No target</option>{(['task', 'decision', 'initiative', 'area'] as const).map((kind) => <option key={kind}>{kind}</option>)}</select>{note.target_type ? <select aria-label="Note target" value={note.target_id} onChange={(event) => setNote({ ...note, target_id: event.target.value })}><option value="">Choose…</option>{targetOptions(note.target_type as AreaTarget, false)}</select> : null}<Button isDisabled={pending || (!note.title.trim() && !note.body.trim()) || Boolean(note.target_type && !note.target_id)} onPress={() => { void mutate({ cmd: editingNote ? 'area_note_update' : 'area_note_create', id, ...(editingNote ? { note_id: editingNote } : {}), ...note }, resetNote); }}>{editingNote ? 'Save note' : 'Add note'}</Button>{editingNote ? <Button onPress={resetNote}>Cancel note edit</Button> : null}</div>
        </section>
        <footer className={styles.detailFooter}><Button tone="danger" isDisabled={pending} onPress={() => { void mutate({ cmd: 'area_archive', id }, onClose); }}>Archive Area</Button><span /><Button isDisabled={pending} onPress={onClose}>Cancel</Button><Button tone="primary" type="submit" isDisabled={pending || !draft.title.trim()}>Save</Button></footer>
      </fieldset>
    </form>
  </ModalDialog>;
}
