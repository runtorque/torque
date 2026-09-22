import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectConnection } from '../../app/store';
import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { record, text } from './agentClassesModel';
import { catalogDefinition, catalogDraft, catalogListKey, catalogTargetKey, targetFromRow, type CatalogDraft, type CatalogKind, type CatalogTarget } from './catalogModel';
import { reconcileSettings, settingsEqual } from './settingsModel';
import { CatalogFields } from './CatalogFields';
import styles from './ControlCenter.module.css';

type Editor = { draft: CatalogDraft; baseline: CatalogDraft };
export function CatalogEditor({ title, kind, group, refreshVersion = 0, onMutation }: { title: string; kind: CatalogKind; group: string; refreshVersion?: number; onMutation?: () => void }) {
  const dispatch = useAppDispatch(); const connection = useAppSelector(selectConnection); const key = catalogListKey(kind);
  const [rows, setRows] = useState<UnknownRecord[] | null>(null); const [selected, setSelected] = useState<CatalogTarget | null>(null); const [editor, setEditor] = useState<Editor | null>(null);
  const [listError, setListError] = useState(''); const [detailError, setDetailError] = useState(''); const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState(0); const [pending, setPending] = useState(false); const [confirm, setConfirm] = useState(false);
  const listRead = useRef<AbortController | null>(null); const detailRead = useRef<AbortController | null>(null); const operation = useRef<AbortController | null>(null);
  useEffect(() => () => operation.current?.abort(), []);
  useEffect(() => {
    if (connection.status !== 'connected' || pending) return;
    const controller = new AbortController(); listRead.current = controller;
    void readCommand({ cmd: `list_${key}`, group }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== key || frame.group !== group || !Array.isArray(frame[key])) throw new Error(text(frame.message, 'Catalog response did not match this group.'));
      setRows((frame[key] as unknown[]).map(record)); setListError(''); dispatch(projectionActions.auxiliaryResourceReceived(frame));
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setListError(cause instanceof Error ? cause.message : 'Catalog refresh failed.'); });
    return () => controller.abort();
  }, [connection.status, connection.reconnectCount, group, key, pending, refreshVersion, retry, dispatch]);
  useEffect(() => {
    if (!selected || connection.status !== 'connected' || pending) return;
    const controller = new AbortController(); detailRead.current = controller;
    const detailKind = kind === 'specialization' ? 'specialization' : 'template';
    void readCommand({ cmd: `get_${detailKind}`, group, ...selected }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== `${detailKind}_detail` || frame.name !== selected.name || !frame[detailKind] || typeof frame[detailKind] !== 'object' || Array.isArray(frame[detailKind])) throw new Error(text(frame.message, 'Definition response did not match the selected entry.'));
      const next = catalogDraft({ ...record(frame[detailKind]), name: selected.name }, selected.scope);
      setEditor((previous) => ({ draft: previous ? reconcileSettings(previous.baseline, previous.draft, next) : next, baseline: next })); setDetailError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setDetailError(cause instanceof Error ? cause.message : 'Definition refresh failed.'); });
    return () => controller.abort();
  }, [connection.status, connection.reconnectCount, group, kind, selected, pending, refreshVersion, retry]);
  const choose = (row: UnknownRecord) => { const target = targetFromRow(row); if (selected && catalogTargetKey(selected) === catalogTargetKey(target)) { setRetry((value) => value + 1); return; } detailRead.current?.abort(); setSelected(target); setEditor(null); setError(''); setDetailError(''); setNotice(''); };
  const create = (draft = catalogDraft({})) => { detailRead.current?.abort(); setSelected(null); setEditor({ draft, baseline: catalogDraft({}) }); setDetailError(''); setError(''); setNotice(''); };
  const mutate = async (remove = false) => {
    if (!editor || operation.current || (remove && !selected)) return;
    let definition: UnknownRecord;
    try { definition = remove ? {} : catalogDefinition(editor.draft, kind); } catch (cause: unknown) { setError(cause instanceof Error ? cause.message : 'Check the definition.'); return; }
    const target = remove ? selected! : { name: text(definition.name), scope: editor.draft.scope };
    const submitted = remove ? editor.draft : { ...editor.draft, name: target.name };
    const controller = new AbortController(); operation.current = controller; listRead.current?.abort(); detailRead.current?.abort(); setPending(true); setError('');
    try {
      const frame = await readCommand({ cmd: `${remove ? 'delete' : 'save'}_${kind}`, group, ...target, ...(remove ? {} : { data: definition, ...(selected ? { old_name: selected.name, old_scope: selected.scope } : {}) }) }, controller.signal);
      if (controller.signal.aborted) return;
      if (frame.type !== key || frame.group !== group || frame[remove ? 'deleted' : 'saved'] !== target.name || !Array.isArray(frame[key])) throw new Error(text(frame.message, 'Catalog acknowledgement did not match this operation.'));
      setRows((frame[key] as unknown[]).map(record)); dispatch(projectionActions.auxiliaryResourceReceived(frame)); setNotice(`${remove ? 'Deleted' : 'Saved'} ${target.scope} ${target.name}`); setConfirm(false); setDetailError('');
      if (remove) { setSelected(null); setEditor(null); } else { setEditor({ draft: submitted, baseline: submitted }); setSelected(target); }
      onMutation?.();
    } catch (cause: unknown) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Catalog update failed.'); }
    finally { operation.current = null; if (!controller.signal.aborted) setPending(false); }
  };
  const selectedRow = rows?.find((row) => selected && catalogTargetKey(targetFromRow(row)) === catalogTargetKey(selected));
  return <section className={styles.libraryEditor} aria-label={`${title} library`}>
    <aside><header><div><h2>{title}</h2><p>{rows?.length ?? 0} available</p></div><Button tone="quiet" aria-label={`New ${kind}`} isDisabled={pending} onPress={() => create()}>＋</Button></header>
      {notice ? <p role="status">{notice}</p> : null}{listError ? <p role="alert">Catalog refresh failed. {listError} <Button isDisabled={pending} onPress={() => setRetry((value) => value + 1)}>Retry catalog</Button></p> : null}
      <div>{rows?.length ? rows.map((row) => { const target = targetFromRow(row); return <button key={catalogTargetKey(target)} disabled={pending} aria-current={selected && catalogTargetKey(selected) === catalogTargetKey(target) ? 'page' : undefined} onClick={() => choose(row)}><strong>{target.name}</strong><small>{target.scope}{row.shadowed === true ? ' · overridden by project' : ''}</small></button>; }) : <StateSurface title={rows ? `No ${title.toLowerCase()}` : `Loading ${title.toLowerCase()}`} description="Select an entry or create a project definition." />}</div>
    </aside>
    <div className={styles.catalogEditor}>
      {detailError ? <p role="alert">Definition refresh failed. {detailError} <Button isDisabled={pending} onPress={() => setRetry((value) => value + 1)}>Retry definition</Button></p> : null}
      {editor ? <form onSubmit={(event) => { event.preventDefault(); void mutate(); }}>
        <header><div><h2>{selected ? `Edit ${selected.name}` : `New ${kind}`}</h2><p>{kind === 'template' ? 'Templates are compatibility aliases for worker roles.' : kind === 'role' ? 'Worker launch and behavior defaults.' : 'Engineer behavior and ordered priorities.'}</p></div><span>{!selected || !settingsEqual(editor.draft, editor.baseline) ? 'Unsaved changes' : 'Saved definition'}</span><Button tone="primary" type="submit" isDisabled={pending || !editor.draft.name.trim()}>{pending ? 'Saving…' : 'Save'}</Button></header>
        {selectedRow?.path ? <p className={styles.catalogSource}>Source: {text(selectedRow.path)}</p> : null}
        {error ? <p role="alert">{error}</p> : null}
        <fieldset disabled={pending} className={styles.classFields}><CatalogFields kind={kind} draft={editor.draft} change={(draft) => { setEditor({ ...editor, draft }); setError(''); }} /></fieldset>
        <footer><Button type="button" isDisabled={pending} onPress={() => create({ ...editor.draft, name: `${editor.draft.name || kind}-copy` })}>Duplicate</Button><span />{selected ? <Button type="button" tone="danger" isDisabled={pending} onPress={() => { setConfirm(true); setError(''); }}>Delete</Button> : null}</footer>
      </form> : <StateSurface title={selected ? 'Loading definition' : `Select or create a ${kind}`} description="Full scoped definitions load before editing." />}
    </div>
    <ModalDialog title={`Delete ${kind}?`} description={selected ? `${selected.scope}: ${selected.name}` : ''} size="small" isOpen={confirm} onOpenChange={(open) => { if (!pending) setConfirm(open); }}><p>Remove this scoped definition? Other scopes and existing running sessions remain unchanged.</p>{error ? <p role="alert">{error}</p> : null}<footer><Button isDisabled={pending} onPress={() => setConfirm(false)}>Cancel</Button><Button tone="danger" isDisabled={pending} onPress={() => { void mutate(true); }}>Delete</Button></footer></ModalDialog>
  </section>;
}
