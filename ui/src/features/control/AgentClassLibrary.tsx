import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { selectConnection, projectionActions } from '../../app/store';
import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import { readCommand } from '../../protocol/http';
import type { UnknownRecord } from '../../protocol';
import { reconcileSettings, settingsEqual } from './settingsModel';
import { record, list, text, strings, classLabel, availableScopes, classDraft, classDefinition, duplicateClass, classError, type ClassDraft } from './agentClassesModel';
import { AgentClassPreview } from './AgentClassPreview';
import styles from './ControlCenter.module.css';

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className={styles.field}><span>{label}</span>{children}</label>;
}

function AgentClassEditor({ item, isNew, capabilities, baseDir, onSaved, onDuplicate, onBusy }: {
  item: UnknownRecord; isNew: boolean; capabilities: UnknownRecord[]; baseDir: string;
  onSaved: (frame: UnknownRecord) => void; onDuplicate: (item: UnknownRecord) => void; onBusy: (value: boolean) => void;
}) {
  const [draft, setDraft] = useState(() => classDraft(item));
  const [baseline, setBaseline] = useState(() => classDraft(item));
  const [previousItem, setPreviousItem] = useState(item);
  const [validationResult, setValidation] = useState<{ signature: string; frame: UnknownRecord } | null>(null);
  const [error, setError] = useState(''); const [pending, setPending] = useState(false);
  const [confirm, setConfirm] = useState<'archive' | 'delete' | null>(null);
  const operation = useRef<AbortController | null>(null); const validating = useRef<AbortController | null>(null);
  useEffect(() => () => { operation.current?.abort(); validating.current?.abort(); }, []);
  if (previousItem !== item) { const next = classDraft(item); setPreviousItem(item); setDraft(reconcileSettings(baseline, draft, next)); setBaseline(next); }
  const update = <K extends keyof ClassDraft>(key: K, value: ClassDraft[K]) => { setDraft((current) => ({ ...current, [key]: value })); setError(''); };
  const { id, version, kind, displayName, description, lifecycle, job, aclMode, selected, ui, scratchOnly } = draft;
  const setId = (value: ClassDraft['id']) => update('id', value);
  const setVersion = (value: ClassDraft['version']) => update('version', value);
  const setKind = (value: ClassDraft['kind']) => update('kind', value);
  const setDisplayName = (value: ClassDraft['displayName']) => update('displayName', value);
  const setDescription = (value: ClassDraft['description']) => update('description', value);
  const setLifecycle = (value: ClassDraft['lifecycle']) => { setDraft((current) => ({ ...current, lifecycle: value, scratchOnly: value === 'draft' })); setError(''); };
  const setJob = (value: ClassDraft['job']) => update('job', value);
  const setAclMode = (value: ClassDraft['aclMode']) => update('aclMode', value);
  const setUi = (value: ClassDraft['ui']) => update('ui', value);
  const setScratchOnly = (value: ClassDraft['scratchOnly']) => { setDraft((current) => ({ ...current, scratchOnly: value, lifecycle: value ? 'draft' : 'stable' })); setError(''); };
  const setSelected = (value: ClassDraft['selected'] | ((current: ClassDraft['selected']) => ClassDraft['selected'])) => update('selected', typeof value === 'function' ? value(selected) : value);
  const builtin = item.builtin === true || text(item.source) === 'builtin';
  const archived = item.archived === true || item.disabled === true;
  const unavailableDefinition = !isNew && record(item.acl).mode === 'deny' && !record(item.authoring_definition).id;
  const readOnly = !isNew && (builtin || archived || unavailableDefinition);
  const visibleCapabilities = capabilities.filter((capability) => { const kinds = strings(capability.base_kinds); return !kinds.length || kinds.includes(kind); });
  const definition = () => classDefinition(item, draft);
  const signature = JSON.stringify(definition()); const signatureRef = useRef(signature);
  useLayoutEffect(() => { signatureRef.current = signature; }, [signature]);
  const validation = validationResult?.signature === signature ? validationResult.frame : null;
  const isDirty = isNew || !settingsEqual(draft, baseline);
  const validate = async () => {
    validating.current?.abort(); const controller = new AbortController(); validating.current = controller; setError(''); setValidation(null);
    const requested = signature;
    try {
      const frame = await readCommand({ cmd: 'agent_class_validate', base_dir: baseDir, agent_class: definition() }, controller.signal);
      if (controller.signal.aborted || requested !== signatureRef.current) return;
      if (frame.type !== 'agent_class_validation' || (frame.valid === true && record(frame.agent_class).id !== definition().id)) throw new Error(classError(frame));
      setValidation({ signature: requested, frame });
    } catch (cause: unknown) { if (!controller.signal.aborted && requested === signatureRef.current) setError(cause instanceof Error ? cause.message : 'Validation failed.'); }
  };
  const mutate = async (cmd: string, extra: UnknownRecord) => {
    if (operation.current) return;
    validating.current?.abort(); const controller = new AbortController(); operation.current = controller; setPending(true); setError(''); onBusy(true);
    try {
      const frame = await readCommand({ cmd, base_dir: baseDir, ...extra }, controller.signal);
      if (controller.signal.aborted) return;
      const expected = cmd === 'agent_class_delete' ? 'agent_class_delete' : cmd === 'agent_class_archive' ? 'agent_class_archive' : 'agent_class_save';
      const receivedId = cmd === 'agent_class_delete' ? frame.class_id : record(frame.agent_class).id;
      const expectedId = cmd === 'agent_class_create' || cmd === 'agent_class_update' ? record(extra.agent_class).id : extra.class_id;
      if (frame.type !== expected || frame.ok !== true || receivedId !== expectedId || !Array.isArray(frame.classes)) throw new Error(classError(frame));
      if (cmd !== 'agent_class_delete') { const next = classDraft(record(frame.agent_class)); setDraft(next); setBaseline(next); }
      setValidation(null); setConfirm(null); onSaved(frame);
    } catch (cause: unknown) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Agent Class update failed.'); }
    finally { operation.current = null; if (!controller.signal.aborted) { setPending(false); onBusy(false); } }
  };
  const save = () => { if (!readOnly) void mutate(isNew ? 'agent_class_create' : 'agent_class_update', { agent_class: definition() }); };
  const duplicate = () => onDuplicate(duplicateClass({ ...item, ...definition() }));
  return <form className={styles.classEditor} onSubmit={(event) => { event.preventDefault(); save(); }}>
    <header><div><h2>{isNew ? 'Create project Agent Class' : builtin ? 'Built-in Agent Class' : archived ? 'Archived Agent Class' : 'Edit project Agent Class'}</h2><p>Describe the job, then select the authority that Torque may project at launch.</p></div><span>{readOnly ? 'Read only' : isDirty ? 'Unsaved changes' : 'Project YAML'}</span><Button tone="quiet" type="button" isDisabled={pending || unavailableDefinition} onPress={() => { void validate(); }}>Validate</Button>{!readOnly ? <Button tone="primary" type="submit" isDisabled={pending || !id.trim() || !displayName.trim()}>{pending ? 'Saving…' : 'Save'}</Button> : null}</header>
    {error ? <p role="alert">{error}</p> : null}
    {unavailableDefinition ? <p role="alert">The editable deny rules are unavailable. Refresh the catalog before editing or duplicating this class.</p> : null}
    {builtin ? <p className={styles.classNotice}>Built-in classes cannot be edited. Duplicate this definition into the project to customize it.</p> : null}
    {archived ? <p className={styles.classNotice}>Archived classes stay visible for audit but cannot be edited or launched.</p> : null}
    {validation?.type === 'agent_class_validation' ? <div className={validation.valid === true ? styles.classValid : styles.classInvalid}>{validation.valid === true ? 'Validation passed' : text(validation.message, 'Validation found issues.')}{list(validation.issues).map((issue, index) => <span key={index}>{text(issue.message, text(issue.code))}</span>)}</div> : null}
    <AgentClassPreview item={validation?.valid === true && record(validation.agent_class).id === definition().id ? record(validation.agent_class) : isNew ? {} : item} validated={validation?.valid === true && record(validation.agent_class).id === definition().id} dirty={isDirty} capabilities={capabilities} />
    <fieldset disabled={pending} className={styles.classFields}>
    <section className={styles.classIdentity}><h3>Identity and lifecycle</h3><div className={styles.formGrid}>
      <Field label="ID"><input value={id} readOnly={!isNew} onChange={(event) => setId(event.target.value)} placeholder="release-architect" /></Field>
      <Field label="Version"><input value={version} readOnly={readOnly} onChange={(event) => setVersion(event.target.value)} /></Field>
      <Field label="Base kind"><select value={kind} disabled={readOnly || (!isNew && Boolean(item.id))} onChange={(event) => { setKind(event.target.value); setSelected({}); }}><option value="worker">Worker</option><option value="engineer">Engineer</option><option value="architect">Architect</option></select></Field>
      <Field label="Lifecycle"><select value={lifecycle} disabled={readOnly} onChange={(event) => setLifecycle(event.target.value)}><option value="stable">Stable</option><option value="draft">Draft</option></select></Field>
      <Field label="Display name"><input value={displayName} readOnly={readOnly} onChange={(event) => setDisplayName(event.target.value)} /></Field>
      <Field label="Description"><input value={description} readOnly={readOnly} onChange={(event) => setDescription(event.target.value)} /></Field>
    </div><Field label="Class job prompt"><textarea value={job} readOnly={readOnly} onChange={(event) => setJob(event.target.value)} /></Field>
    <div className={styles.classUiFields}><Field label="UI label"><input value={ui.label} readOnly={readOnly} onChange={(event) => setUi({ ...ui, label: event.target.value })} /></Field><Field label="Icon"><input value={ui.icon} readOnly={readOnly} onChange={(event) => setUi({ ...ui, icon: event.target.value })} /></Field><Field label="Badge"><input value={ui.badge} readOnly={readOnly} onChange={(event) => setUi({ ...ui, badge: event.target.value })} /></Field><Field label="Color"><input value={ui.color} readOnly={readOnly} onChange={(event) => setUi({ ...ui, color: event.target.value })} /></Field></div>
    <label className={styles.classCheck}><input type="checkbox" checked={scratchOnly} disabled={readOnly} onChange={(event) => setScratchOnly(event.target.checked)} />Scratch-only draft class</label></section>
    <section className={styles.classCapabilities}><header><div><h3>Purpose and permissions</h3><p>{aclMode === 'allow' ? 'Only selected capabilities are granted.' : 'Selected capabilities are removed or narrowed from the base-kind ceiling.'}</p></div><label>ACL mode<select value={aclMode} disabled={readOnly} onChange={(event) => setAclMode(event.target.value)}><option value="allow">Allow selected</option><option value="deny">Deny selected</option></select></label></header>
      <div>{visibleCapabilities.length ? visibleCapabilities.map((capability) => {
        const capabilityId = text(capability.id);
        const scopes = availableScopes(capability, kind);
        const checked = Object.hasOwn(selected, capabilityId);
        return <label key={capabilityId} className={styles.capabilityOption} data-risk={text(capability.risk, 'normal')}><input type="checkbox" checked={checked} disabled={readOnly} onChange={(event) => setSelected((current) => { const next = { ...current }; if (event.target.checked) next[capabilityId] = scopes.at(-1) ?? ''; else delete next[capabilityId]; return next; })} /><span><strong>{text(capability.label, capabilityId)}</strong><small>{text(capability.description)}</small><em>{capabilityId} · {text(capability.risk, 'normal')}</em></span>{scopes.length ? <select aria-label={`${capabilityId} scope`} value={selected[capabilityId] || scopes.at(-1)} disabled={readOnly || !checked} onChange={(event) => setSelected({ ...selected, [capabilityId]: event.target.value })}>{scopes.map((scope) => <option key={scope} value={scope}>{scope}</option>)}</select> : null}</label>;
      }) : <StateSurface title="No authorable capabilities" description={`The server returned no normal capability buckets for ${kind}.`} />}</div>
    </section>
    </fieldset><footer><Button tone="quiet" type="button" isDisabled={pending || unavailableDefinition} onPress={duplicate}>{builtin ? 'Duplicate to project' : 'Duplicate'}</Button><span />{!builtin && !archived && !isNew ? <Button tone="danger" type="button" isDisabled={pending} onPress={() => setConfirm('archive')}>Archive</Button> : null}{!builtin && !isNew ? <Button tone="danger" type="button" isDisabled={pending} onPress={() => setConfirm('delete')}>Delete</Button> : null}</footer>
    <ModalDialog title={`${confirm === 'delete' ? 'Delete' : 'Archive'} Agent Class?`} description={id} size="small" isOpen={confirm !== null} onOpenChange={(open) => { if (!open && !pending) setConfirm(null); }}><div className={styles.classConfirm}>{error ? <p role="alert">{error}</p> : null}<p>{confirm === 'delete' ? 'This removes the project YAML definition. Existing frozen launch snapshots remain auditable.' : 'This disables future assignment and launch while keeping the definition visible for audit.'}</p><footer><Button tone="quiet" type="button" isDisabled={pending} onPress={() => setConfirm(null)}>Cancel</Button><Button tone="danger" type="button" isDisabled={pending} onPress={() => { void mutate(confirm === 'delete' ? 'agent_class_delete' : 'agent_class_archive', { class_id: id }); }}>{confirm === 'delete' ? 'Delete' : 'Archive'}</Button></footer></div></ModalDialog>
  </form>;
}

export function AgentClassLibrary({ baseDir, refreshVersion = 0 }: { baseDir: string; refreshVersion?: number }) {
  const connection = useAppSelector(selectConnection); const dispatch = useAppDispatch();
  const [catalog, setCatalog] = useState<UnknownRecord>({}); const [selection, setSelection] = useState('');
  const [newItem, setNewItem] = useState<UnknownRecord>({}); const [newVersion, setNewVersion] = useState(0);
  const [retained, setRetained] = useState<UnknownRecord>({});
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [retry, setRetry] = useState(0);
  const activeRead = useRef<AbortController | null>(null);
  const items = list(catalog.classes); const capabilities = list(catalog.capability_catalog ?? record(catalog.authoring_contract).capability_catalog);
  useEffect(() => {
    if (connection.status !== 'connected' || busy) return;
    const controller = new AbortController(); activeRead.current = controller;
    void readCommand({ cmd: 'agent_class_list', base_dir: baseDir }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'agent_classes' || !Array.isArray(frame.classes)) throw new Error(classError(frame));
      setCatalog(frame); setError(''); dispatch(projectionActions.auxiliaryResourceReceived(frame));
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not refresh Agent Classes.'); });
    return () => controller.abort();
  }, [baseDir, connection.status, connection.reconnectCount, refreshVersion, retry, busy, dispatch]);
  const isNew = selection === '__new__';
  const selected = isNew ? newItem : items.find((item) => text(item.id) === selection) ?? (selection ? retained : items[0]);
  if (!isNew && selected && selected !== retained) setRetained(selected);
  const create = (item: UnknownRecord = {}) => { setNotice(''); setNewItem(item); setNewVersion((value) => value + 1); setSelection('__new__'); };
  const saved = (frame: UnknownRecord) => { setNotice(`${text(frame.operation, 'Saved')} ${text(record(frame.agent_class).id, text(frame.class_id))}`); setCatalog((previous) => ({ ...previous, classes: frame.classes })); setSelection(frame.type === 'agent_class_delete' ? text(list(frame.classes)[0]?.id) : text(record(frame.agent_class).id)); };
  return <section className={styles.classLibrary}>
    <aside><header><div><h2>Agent Classes</h2><p>Trusted project authoring and launch authority.</p></div><Button tone="quiet" isDisabled={busy} onPress={() => create()}>＋ New</Button></header>
    {notice ? <p role="status">{notice}</p> : null}
    {error ? <p role="alert">Class refresh failed. {error} <Button isDisabled={busy} onPress={() => setRetry((value) => value + 1)}>Retry classes</Button></p> : null}
    {list(catalog.issues).length ? <details><summary>Catalog issues ({list(catalog.issues).length})</summary>{list(catalog.issues).map((issue, index) => <p key={index}>{text(issue.message)} <small>{text(issue.path)}</small></p>)}</details> : null}
    <div>{items.length ? items.map((item) => <button key={text(item.id)} disabled={busy} aria-current={text(item.id) === text(selected?.id) && !isNew ? 'page' : undefined} onClick={() => { setSelection(text(item.id)); setRetry((value) => value + 1); }}><span><strong>{classLabel(item)}</strong><small>{text(item.id)} · v{text(item.version, '1')}</small></span><span>{text(item.base_kind)}{item.archived === true ? ' · archived' : item.builtin === true ? ' · built-in' : ' · project'}</span></button>) : <StateSurface title={catalog.classes ? 'No Agent Classes' : 'Loading Agent Classes'} description="Create a project class or refresh the server catalog." />}</div></aside>
    {selected ? <AgentClassEditor key={isNew ? `new-${newVersion}` : text(selected.id)} item={selected} isNew={isNew} capabilities={capabilities} baseDir={baseDir} onSaved={saved} onDuplicate={create} onBusy={(value) => { if (value) activeRead.current?.abort(); setBusy(value); }} /> : <StateSurface title="Select or create an Agent Class" description="Inspect its purpose and launch authority." />}
  </section>;
}
