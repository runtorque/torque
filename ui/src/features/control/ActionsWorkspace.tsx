import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectConnection } from '../../app/store';
import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { record, text } from './agentClassesModel';
import { catalogTargetKey, targetFromRow, type CatalogTarget } from './catalogModel';
import { actionDefinition, actionDraft, type ActionDraft } from './actionModel';
import { reconcileSettings, settingsEqual } from './settingsModel';
import { ActionFields } from './ActionFields';
import { ActionPreview } from './ActionPreview';
import styles from './ControlCenter.module.css';

type Editor = { draft: ActionDraft; baseline: ActionDraft };
export function ActionsWorkspace({ group, initialName = '', refreshVersion = 0, onPipelines }: { group: string; initialName?: string; refreshVersion?: number; onPipelines: () => void }) {
  const dispatch = useAppDispatch(); const connection = useAppSelector(selectConnection); const key = 'actions';
  const initialResolved = useRef(false); const [roles, setRoles] = useState<UnknownRecord[]>([]); const [roleError, setRoleError] = useState('');
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
      const nextRows = (frame[key] as unknown[]).map(record); setRows(nextRows); setListError('');
      if (!initialResolved.current && initialName) { initialResolved.current = true; const row = nextRows.find((item) => item.name === initialName && item.shadowed !== true); setSelected(row ? targetFromRow(row) : { name: initialName, scope: 'project' }); }
      dispatch(projectionActions.auxiliaryResourceReceived(frame));
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setListError(cause instanceof Error ? cause.message : 'Catalog refresh failed.'); });
    return () => controller.abort();
  }, [connection.status, connection.reconnectCount, group, key, pending, refreshVersion, retry, dispatch, initialName]);
  useEffect(() => {
    if (connection.status !== 'connected' || pending) return;
    const controller = new AbortController();
    void readCommand({ cmd: 'list_roles', group }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'roles' || frame.group !== group || !Array.isArray(frame.roles)) throw new Error(text(frame.message, 'Role choices unavailable.'));
      setRoles(frame.roles.map(record)); setRoleError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setRoleError(cause instanceof Error ? cause.message : 'Role choices unavailable.'); });
    return () => controller.abort();
  }, [group, connection.status, connection.reconnectCount, pending, retry, refreshVersion]);
  useEffect(() => {
    if (!selected || connection.status !== 'connected' || pending) return;
    const controller = new AbortController(); detailRead.current = controller;
    const detailKind = 'action';
    void readCommand({ cmd: `get_${detailKind}`, group, ...selected }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== `${detailKind}_detail` || frame.name !== selected.name || frame.group !== group || frame.scope !== selected.scope || !frame[detailKind] || typeof frame[detailKind] !== 'object' || Array.isArray(frame[detailKind])) throw new Error(text(frame.message, 'Definition response did not match the selected entry.'));
      const next = actionDraft(record(frame[detailKind]), selected.name, selected.scope);
      setEditor((previous) => ({ draft: previous ? reconcileSettings(previous.baseline, previous.draft, next) : next, baseline: next })); setDetailError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setDetailError(cause instanceof Error ? cause.message : 'Definition refresh failed.'); });
    return () => controller.abort();
  }, [connection.status, connection.reconnectCount, group, selected, pending, refreshVersion, retry]);
  const choose = (row: UnknownRecord) => { initialResolved.current = true; const target = targetFromRow(row); if (selected && catalogTargetKey(selected) === catalogTargetKey(target)) { setRetry((value) => value + 1); return; } detailRead.current?.abort(); setSelected(target); setEditor(null); setError(''); setDetailError(''); setNotice(''); };
  const create = (draft = actionDraft()) => { initialResolved.current = true; detailRead.current?.abort(); setSelected(null); setEditor({ draft, baseline: actionDraft() }); setDetailError(''); setError(''); setNotice(''); };
  const mutate = async (remove = false) => {
    if (!editor || operation.current || (remove && !selected)) return;
    let definition: UnknownRecord;
    try { definition = remove ? {} : actionDefinition(editor.draft); } catch (cause: unknown) { setError(cause instanceof Error ? cause.message : 'Check the definition.'); return; }
    const target = remove ? selected! : { name: text(definition.name), scope: editor.draft.scope };
    const submitted = remove ? editor.draft : { ...editor.draft, name: target.name };
    const controller = new AbortController(); operation.current = controller; listRead.current?.abort(); detailRead.current?.abort(); setPending(true); setError('');
    try {
      const frame = await readCommand({ cmd: `${remove ? 'delete' : 'save'}_action`, group, ...target, ...(remove ? {} : { action: definition, ...(selected ? { old_name: selected.name, old_scope: selected.scope } : {}) }) }, controller.signal);
      if (controller.signal.aborted) return;
      if (frame.type !== key || frame.group !== group || frame.scope !== target.scope || frame[remove ? 'deleted' : 'saved'] !== target.name || !Array.isArray(frame[key])) throw new Error(text(frame.message, 'Catalog acknowledgement did not match this operation.'));
      setRows((frame[key] as unknown[]).map(record)); dispatch(projectionActions.auxiliaryResourceReceived(frame)); setNotice(`${remove ? 'Deleted' : 'Saved'} ${target.scope} ${target.name}`); setConfirm(false); setDetailError('');
      if (remove) { setSelected(null); setEditor(null); } else { setEditor({ draft: submitted, baseline: submitted }); setSelected(target); }
    } catch (cause: unknown) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Catalog update failed.'); }
    finally { operation.current = null; if (!controller.signal.aborted) setPending(false); }
  };
  const selectedRow = rows?.find((row) => selected && catalogTargetKey(targetFromRow(row)) === catalogTargetKey(selected));
  return <section className={styles.libraryEditor} aria-label="Actions library">
    <aside><header><div><h2>Actions</h2><p>{rows?.length ?? 0} available</p></div><Button tone="quiet" aria-label="New action" isDisabled={pending} onPress={() => create()}>＋</Button></header>
      {notice ? <p role="status">{notice}</p> : null}{listError ? <p role="alert">Catalog refresh failed. {listError} <Button isDisabled={pending} onPress={() => setRetry((value) => value + 1)}>Retry catalog</Button></p> : null}
      <div>{rows?.length ? rows.map((row) => { const target = targetFromRow(row); return <button key={catalogTargetKey(target)} disabled={pending} aria-current={selected && catalogTargetKey(selected) === catalogTargetKey(target) ? 'page' : undefined} onClick={() => choose(row)}><strong>{target.name}</strong><small>{target.scope}{row.shadowed === true ? ' · overridden by project' : ''}</small></button>; }) : <StateSurface title={rows ? 'No actions' : 'Loading actions'} description="Select an entry or create a project definition." />}</div>
      <Button onPress={onPipelines} isDisabled={pending}>Discover pipelines</Button>
    </aside>
    <div className={styles.catalogEditor}>
      {detailError ? <p role="alert">Definition refresh failed. {detailError} <Button isDisabled={pending} onPress={() => setRetry((value) => value + 1)}>Retry definition</Button></p> : null}
      {editor ? <form onSubmit={(event) => { event.preventDefault(); void mutate(); }}>
        <header><div><h2>{selected ? `Edit ${selected.name}` : 'New action'}</h2><p>Action prompt, agent configuration and transitions.</p></div><span>{!selected || !settingsEqual(editor.draft, editor.baseline) ? 'Unsaved changes' : 'Saved definition'}</span><Button tone="primary" type="submit" isDisabled={pending || !editor.draft.name.trim()}>{pending ? 'Saving…' : 'Save action'}</Button></header>
        {selectedRow?.dir ? <p className={styles.catalogSource}>Source directory: {text(selectedRow.dir)}</p> : null}
        {error ? <p role="alert">{error}</p> : null}
        {roleError ? <p role="alert">{roleError} <Button isDisabled={pending} onPress={() => setRetry((value) => value + 1)}>Retry roles</Button></p> : null}
        <fieldset disabled={pending} className={styles.classFields}><ActionFields draft={editor.draft} actions={rows ?? []} roles={roles} change={(draft) => { setEditor({ ...editor, draft }); setError(''); }} /></fieldset>
        <ActionPreview draft={editor.draft} group={group} disabled={pending} />
        <footer><Button type="button" isDisabled={pending} onPress={() => create({ ...editor.draft, name: `${editor.draft.name || 'action'}-copy` })}>Duplicate</Button><span />{selected ? <Button type="button" tone="danger" isDisabled={pending} onPress={() => { setConfirm(true); setError(''); }}>Delete</Button> : null}</footer>
      </form> : <StateSurface title={selected ? 'Loading definition' : 'Select or create an action'} description="Full scoped definitions load before editing." />}
    </div>
    <ModalDialog title="Delete action?" description={selected ? `${selected.scope}: ${selected.name}` : ''} size="small" isOpen={confirm} onOpenChange={(open) => { if (!pending) setConfirm(open); }}><p>Remove this scoped definition? Other scopes and existing running sessions remain unchanged.</p>{error ? <p role="alert">{error}</p> : null}<footer><Button isDisabled={pending} onPress={() => setConfirm(false)}>Cancel</Button><Button tone="danger" isDisabled={pending} onPress={() => { void mutate(true); }}>Delete</Button></footer></ModalDialog>
  </section>;
}
