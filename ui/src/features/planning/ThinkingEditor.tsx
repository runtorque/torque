import type { ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import { Button, ModalDialog } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { planningRequest } from './planningRequests';
import { records, text } from './model';
import { usePlanningMutation } from './usePlanningMutation';
import styles from './PlanningWorkspace.module.css';

const briefFields = {
  title: 'Title', problem_opportunity: 'Problem or opportunity', why_it_matters: 'Why it matters',
  proposed_shape: 'Proposed shape', smallest_useful_version: 'Smallest useful version',
  risks_tradeoffs: 'Risks and tradeoffs', open_questions: 'Open questions',
};
const noteFields = { title: 'Title', body: 'Body' };
export function ThinkingEditor({ kind, item, notes, onClose, readStatus }: {
  kind: 'note' | 'brief'; item: UnknownRecord; notes: UnknownRecord[]; onClose: () => void; readStatus?: ReactNode;
}) {
  const id = text(item.id);
  const prefix = kind === 'note' ? 'scratchpad_note' : 'idea_brief';
  const fields = kind === 'note' ? noteFields : briefFields;
  const [detail, setDetail] = useState(item);
  const [draft, setDraft] = useState<Record<string, string>>(() => Object.fromEntries(Object.keys(fields).map((key) => [key, text(item[key])])));
  const dirty = useRef(new Set<string>());
  const [links, setLinks] = useState(() => records(item.thinking_links));
  const [linkId, setLinkId] = useState('');
  const [linkContext, setLinkContext] = useState('');
  const [refinementNote, setRefinementNote] = useState('');
  const [lifecycleNote, setLifecycleNote] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [retry, setRetry] = useState(0);
  const { run, pending, error, busy } = usePlanningMutation();
  const reconnect = useAppSelector(selectConnection).reconnectCount;
  useEffect(() => {
    const controller = new AbortController();
    void planningRequest({ cmd: `${prefix}_show`, id, include_archived: true }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== prefix || frame.id !== id) throw new Error('Planning detail was not returned.');
      setDetail(frame);
      setDraft((current) => ({ ...current, ...Object.fromEntries(Object.keys(current).filter((key) => !dirty.current.has(key)).map((key) => [key, text(frame[key])])) }));
      if (!dirty.current.has('thinking_links')) setLinks(records(frame.thinking_links));
      setLoaded(true); setLoadError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setLoadError(cause instanceof Error ? cause.message : 'Could not load details.'); });
    return () => controller.abort();
  }, [id, prefix, reconnect, retry, item.updated_at]);
  const archived = Boolean(detail.archived || detail.archived_at || detail.status === 'archived');
  const patch = () => Object.fromEntries([...dirty.current].map((key) => [key, key === 'thinking_links' ? links : draft[key]]));
  const valid = Boolean(draft.title?.trim()) && (kind === 'note' || Boolean(draft.problem_opportunity?.trim()));
  const mutate = (action: 'save' | 'refine' | 'park' | 'propose' | 'draft' | 'archive' | 'delete') => {
    if (!loaded || (action !== 'delete' && (!valid || archived))) return;
    void run(async (request) => {
      // Lifecycle commands do not accept body edits. A failed save must never
      // submit an older version for review or park it behind the draft.
      const changes = patch();
      let frame;
      if (action === 'refine') frame = await request({ cmd: 'idea_brief_refine', id, ...changes, refinement_note: refinementNote });
      else {
        if (Object.keys(changes).length && action !== 'delete' && !archived) {
          frame = await request({ cmd: `${prefix}_update`, id, ...changes });
          dirty.current.clear();
        }
        if (action !== 'save') frame = await request(action === 'draft'
          ? { cmd: 'idea_brief_update', id, status: 'draft' }
          : { cmd: `${prefix}_${action}`, id, reason: lifecycleNote, note: lifecycleNote, review_target: 'user' });
      }
      dirty.current.clear();
      if (frame) setDetail((frame.idea_brief ?? frame.note ?? detail) as UnknownRecord);
      if (['save', 'archive', 'delete'].includes(action)) onClose();
      else { setRefinementNote(''); setRetry((value) => value + 1); }
    });
  };
  const available = notes.filter((note) => !note.archived && !note.deleted && !links.some((link) => link.type === 'scratchpad_note' && link.id === note.id));
  return <ModalDialog title={kind === 'note' ? 'Scratchpad note' : 'Idea Brief'} description={id} size="large" isOpen onOpenChange={(open) => { if (!open && !busy.current) onClose(); }}>
    <form className={styles.detailForm} onSubmit={(event) => { event.preventDefault(); if (loaded && valid && !archived) mutate('save'); }}>
      {readStatus}
      {error ? <p role="alert">{error}</p> : null}
      {loadError ? <p role="alert">{loadError} <Button onPress={() => setRetry((value) => value + 1)}>Retry details</Button></p> : null}
      {!loaded ? <p>Loading full details…</p> : null}
      <p role="status">{archived ? 'Archived · read only' : kind === 'brief' ? `Status: ${text(detail.status, 'draft')}` : 'Scratchpad'}</p>
      <fieldset disabled={pending || archived} className={styles.editorFields}>
        {Object.entries(fields).map(([key, label]) => <label key={key} className={styles.field}>{label}{key === 'title'
          ? <input aria-label={label} value={draft[key]} onChange={(event) => { dirty.current.add(key); setDraft({ ...draft, [key]: event.target.value }); }} />
          : <textarea aria-label={label} value={draft[key]} onChange={(event) => { dirty.current.add(key); setDraft({ ...draft, [key]: event.target.value }); }} />}</label>)}
        {kind === 'brief' ? <>
          <section className={styles.embeddedSection}><h3>Linked Thinking</h3>
            {links.map((link, index) => <article key={`${text(link.type)}:${link.id}:${index}`}><b>{text(link.title, link.id)}</b><p>{text(link.context, text(link.summary))}</p>{link.type === 'scratchpad_note' ? <details><summary>Read linked note</summary><p>{text(notes.find((note) => note.id === link.id)?.body, 'This note is not in the loaded collection. Show archived Thinking to include archived notes.')}</p></details> : null}<Button onPress={() => { dirty.current.add('thinking_links'); setLinks(links.filter((_, position) => position !== index)); }}>Remove link {text(link.title, link.id)}</Button></article>)}
            <label className={styles.field}>Scratchpad note<select aria-label="Scratchpad note" value={linkId} onChange={(event) => setLinkId(event.target.value)}><option value="">Choose a note…</option>{available.map((note) => <option key={text(note.id)} value={text(note.id)}>{text(note.title, text(note.id))}</option>)}</select></label>
            <label className={styles.field}>Why this link matters<textarea aria-label="Why this link matters" value={linkContext} onChange={(event) => setLinkContext(event.target.value)} /></label>
            <Button isDisabled={!linkId} onPress={() => { const note = available.find((entry) => entry.id === linkId); if (!note) return; dirty.current.add('thinking_links'); setLinks([...links, { type: 'scratchpad_note', id: linkId, title: text(note.title), group: text(note.group_name, text(note.group)), summary: (text(note.body).trim().split('\n')[0] ?? '').slice(0, 140), context: linkContext }]); setLinkId(''); setLinkContext(''); }}>Add link</Button>
          </section>
          <section className={styles.embeddedSection}><h3>Refinement and review</h3>
            <label className={styles.field}>Refinement note<textarea aria-label="Refinement note" value={refinementNote} onChange={(event) => setRefinementNote(event.target.value)} /></label>
            <Button isDisabled={!loaded || !valid} onPress={() => mutate('refine')}>Record refinement</Button>
            <label className={styles.field}>Lifecycle note<textarea aria-label="Lifecycle note" value={lifecycleNote} onChange={(event) => setLifecycleNote(event.target.value)} /></label>
            <p>Proposal requests product review only. It creates no task or assignment.</p>
            <div className={styles.actionRow}><Button isDisabled={!loaded || !valid} onPress={() => mutate('park')}>Park</Button><Button isDisabled={!loaded || !valid} onPress={() => mutate('propose')}>Propose for review</Button>{detail.status !== 'draft' ? <Button isDisabled={!loaded || !valid} onPress={() => mutate('draft')}>Return to draft</Button> : null}</div>
          </section>
        </> : null}
      </fieldset>
      {kind === 'brief' && records(detail.refinement_log).length ? <details><summary>Refinement history</summary>{records(detail.refinement_log).map((entry, index) => <p key={index}>{text(entry.note, text(entry.refinement_note))} <small>{text(entry.at, text(entry.created_at))}</small></p>)}</details> : null}
      {confirmDelete ? <p role="alert">Delete this scratchpad note permanently? <Button tone="danger" isDisabled={pending} onPress={() => mutate('delete')}>Confirm delete</Button><Button isDisabled={pending} onPress={() => setConfirmDelete(false)}>Keep note</Button></p> : null}
      <footer className={styles.detailFooter}>{!archived ? <Button tone="danger" isDisabled={pending || !loaded || !valid} onPress={() => mutate('archive')}>Archive</Button> : null}{kind === 'note' ? <Button tone="danger" isDisabled={pending || !loaded} onPress={() => setConfirmDelete(true)}>Delete</Button> : null}<span /><Button isDisabled={pending} onPress={onClose}>{archived ? 'Close' : 'Cancel'}</Button>{!archived ? <Button tone="primary" type="submit" isDisabled={pending || !loaded || !valid}>Save</Button> : null}</footer>
    </form>
  </ModalDialog>;
}
