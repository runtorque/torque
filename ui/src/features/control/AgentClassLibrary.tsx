import { useMemo, useState, type ReactNode } from 'react';

import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { TorqueCommand } from '../../protocol';
import styles from './ControlCenter.module.css';

interface AgentClassLibraryProps {
  classes: unknown;
  contract: unknown;
  capabilityCatalog: unknown;
  responses: Record<string, unknown>;
  baseDir: string;
  send: (command: TorqueCommand) => void;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function list(value: unknown): Record<string, unknown>[] {
  const values = Array.isArray(value) ? value : Object.values(record(value));
  return values.map(record).filter((item) => Object.keys(item).length > 0);
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => text(item)).filter(Boolean) : [];
}

function promptJob(value: unknown): string {
  const prompt = record(value);
  return text(prompt.job) || text(prompt.system) || text(prompt.preamble) || text(value);
}

function classLabel(item: Record<string, unknown>): string {
  return text(item.display_name) || text(item.primary_identity_label) || text(item.name) || text(item.id, 'Agent Class');
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className={styles.field}><span>{label}</span>{children}</label>;
}

function classRules(item: Record<string, unknown>): Record<string, string> {
  const acl = record(item.acl);
  const result: Record<string, string> = {};
  list(acl.rules).forEach((rule) => {
    const id = text(rule.capability);
    if (id) result[id] = text(rule.scope);
    strings(rule.capabilities).forEach((capability) => { result[capability] = text(rule.scope); });
  });
  Object.entries(record(acl.capabilities)).forEach(([id, scope]) => { result[id] = text(scope); });
  return result;
}

function availableScopes(capability: Record<string, unknown>, kind: string): string[] {
  const order = ['self', 'children', 'group', 'global'];
  const scopes = strings(capability.scopes);
  if (!scopes.length) return [];
  const maximum = text(record(capability.maximum_scopes)[kind]) || text(capability.maximum_scope);
  const maxIndex = order.indexOf(maximum);
  return maxIndex < 0 ? scopes : scopes.filter((scope) => order.indexOf(scope) <= maxIndex);
}

function AgentClassEditor({ item, isNew, capabilities, baseDir, responses, send, onSaved }: {
  item: Record<string, unknown>;
  isNew: boolean;
  capabilities: Record<string, unknown>[];
  baseDir: string;
  responses: Record<string, unknown>;
  send: (command: TorqueCommand) => void;
  onSaved: (id: string) => void;
}) {
  const metadata = record(item.metadata);
  const itemUi = record(metadata.ui);
  const [id, setId] = useState(isNew ? '' : text(item.id));
  const [version, setVersion] = useState(text(item.version, '1'));
  const [kind, setKind] = useState(text(item.base_kind, 'worker'));
  const [displayName, setDisplayName] = useState(isNew ? '' : classLabel(item));
  const [description, setDescription] = useState(text(item.description, text(item.purpose)));
  const [lifecycle, setLifecycle] = useState(text(item.lifecycle, 'stable'));
  const [job, setJob] = useState(promptJob(item.prompt));
  const [aclMode, setAclMode] = useState(text(record(item.acl).mode, 'allow'));
  const [selected, setSelected] = useState<Record<string, string>>(() => classRules(item));
  const [ui, setUi] = useState({ label: text(itemUi.label), icon: text(itemUi.icon), badge: text(itemUi.badge), color: text(itemUi.color) });
  const [scratchOnly, setScratchOnly] = useState(record(item.draft).scratch_only === true || item.scratch_only === true);
  const [confirm, setConfirm] = useState<'archive' | 'delete' | null>(null);
  const builtin = item.builtin === true || text(item.source) === 'builtin';
  const archived = item.archived === true || item.disabled === true;
  const readOnly = builtin || archived;
  const visibleCapabilities = capabilities.filter((capability) => {
    const kinds = strings(capability.base_kinds);
    return !kinds.length || kinds.includes(kind);
  });
  const validation = record(responses['agent_class_validation:latest']);
  const mutation = record(responses['agent_class_save:latest']);

  const definition = (): Record<string, unknown> => {
    const rules = Object.entries(selected).map(([capability, scope]) => ({ capability, ...(scope ? { scope } : {}) }));
    const metadataUi = Object.fromEntries(Object.entries(ui).filter(([, value]) => value.trim()));
    return {
      agent_class_schema_version: 5,
      id: id.trim(),
      version: version.trim() || '1',
      base_kind: kind,
      display_name: displayName.trim(),
      description: description.trim(),
      lifecycle,
      prompt: { job },
      acl: { mode: aclMode, rules },
      ...(Object.keys(metadataUi).length ? { metadata: { ui: metadataUi } } : {}),
      ...(scratchOnly || lifecycle === 'draft' ? { draft: { scratch_only: scratchOnly, approved_for_live_dogfood: false } } : {}),
    };
  };
  const request = (cmd: string, extra: Record<string, unknown> = {}) => send({ cmd, base_dir: baseDir, request_id: `react-agent-class-${Date.now()}`, ...extra });
  const save = () => {
    const classDefinition = definition();
    request(isNew ? 'agent_class_create' : 'agent_class_update', { agent_class: classDefinition });
    onSaved(id.trim());
  };
  const duplicate = () => {
    const sourceId = text(item.id, 'agent-class');
    const nextId = `${sourceId}-copy`;
    const copy = { ...definition(), id: nextId, display_name: `${classLabel(item)} Copy` };
    request('agent_class_create', { agent_class: copy });
    onSaved(nextId);
  };

  return <form className={styles.classEditor} onSubmit={(event) => { event.preventDefault(); save(); }}>
    <header><div><h2>{isNew ? 'Create project Agent Class' : builtin ? 'Built-in Agent Class' : archived ? 'Archived Agent Class' : 'Edit project Agent Class'}</h2><p>Describe the job, then select the authority that Torque may project at launch.</p></div><span>{readOnly ? 'Read only' : 'Project YAML'}</span><Button tone="quiet" type="button" onPress={() => request('agent_class_validate', { agent_class: definition() })}>Validate</Button>{!readOnly ? <Button tone="primary" type="submit" isDisabled={!id.trim() || !displayName.trim()}>Save</Button> : null}</header>
    {builtin ? <p className={styles.classNotice}>Built-in classes cannot be edited. Duplicate this definition into the project to customize it.</p> : null}
    {archived ? <p className={styles.classNotice}>Archived classes stay visible for audit but cannot be edited or launched.</p> : null}
    {validation.type === 'agent_class_validation' ? <div className={validation.valid === true ? styles.classValid : styles.classInvalid}>{validation.valid === true ? 'Validation passed' : text(validation.message, 'Validation found issues.')}{list(validation.issues).map((issue, index) => <span key={index}>{text(issue.message, text(issue.code))}</span>)}</div> : null}
    {mutation.ok === false ? <div className={styles.classInvalid}>{text(mutation.message, 'Save failed.')}</div> : null}
    <section className={styles.classIdentity}><h3>Identity and lifecycle</h3><div className={styles.formGrid}>
      <Field label="ID"><input value={id} readOnly={!isNew} onChange={(event) => setId(event.target.value)} placeholder="release-architect" /></Field>
      <Field label="Version"><input value={version} readOnly={readOnly} onChange={(event) => setVersion(event.target.value)} /></Field>
      <Field label="Base kind"><select value={kind} disabled={readOnly || (!isNew && Boolean(item.id))} onChange={(event) => { setKind(event.target.value); setSelected({}); }}><option value="worker">Worker</option><option value="engineer">Engineer</option><option value="architect">Architect</option></select></Field>
      <Field label="Lifecycle"><select value={lifecycle} disabled={readOnly} onChange={(event) => setLifecycle(event.target.value)}><option value="stable">Stable</option><option value="draft">Draft</option><option value="deprecated">Deprecated</option></select></Field>
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
    <footer><Button tone="quiet" type="button" onPress={duplicate}>{builtin ? 'Duplicate to project' : 'Duplicate'}</Button><span />{!builtin && !archived && !isNew ? <Button tone="danger" type="button" onPress={() => setConfirm('archive')}>Archive</Button> : null}{!builtin && !isNew ? <Button tone="danger" type="button" onPress={() => setConfirm('delete')}>Delete</Button> : null}</footer>
    <ModalDialog title={`${confirm === 'delete' ? 'Delete' : 'Archive'} Agent Class?`} description={id} size="small" isOpen={confirm !== null} onOpenChange={(open) => { if (!open) setConfirm(null); }}><div className={styles.classConfirm}><p>{confirm === 'delete' ? 'This removes the project YAML definition. Existing frozen launch snapshots remain auditable.' : 'This disables future assignment and launch while keeping the definition visible for audit.'}</p><footer><Button tone="quiet" type="button" onPress={() => setConfirm(null)}>Cancel</Button><Button tone="danger" type="button" onPress={() => { request(confirm === 'delete' ? 'agent_class_delete' : 'agent_class_archive', { class_id: id }); setConfirm(null); }}>{confirm === 'delete' ? 'Delete' : 'Archive'}</Button></footer></div></ModalDialog>
  </form>;
}

export function AgentClassLibrary({ classes, contract, capabilityCatalog, responses, baseDir, send }: AgentClassLibraryProps) {
  const items = useMemo(() => list(classes), [classes]);
  const capabilities = useMemo(() => {
    const direct = list(capabilityCatalog);
    return direct.length ? direct : list(record(contract).capability_catalog);
  }, [capabilityCatalog, contract]);
  const [selection, setSelection] = useState(items[0] ? text(items[0].id) : '');
  const isNew = selection === '__new__';
  const selected = isNew ? {} : items.find((item) => text(item.id) === selection) ?? items[0] ?? {};
  const editorKey = isNew ? '__new__' : text(selected.id);
  return <section className={styles.classLibrary}>
    <aside><header><div><h2>Agent Classes</h2><p>Trusted project authoring and launch authority.</p></div><Button tone="quiet" onPress={() => setSelection('__new__')}>＋ New</Button></header><div>{items.length ? items.map((item) => <button key={text(item.id)} aria-current={text(item.id) === text(selected.id) && !isNew ? 'page' : undefined} onClick={() => setSelection(text(item.id))}><span><strong>{classLabel(item)}</strong><small>{text(item.id)} · v{text(item.version, '1')}</small></span><span>{text(item.base_kind)}{item.archived === true ? ' · archived' : item.builtin === true ? ' · built-in' : ' · project'}</span></button>) : <StateSurface title="No Agent Classes" description="Create a project class or refresh the server catalog." />}</div></aside>
    <AgentClassEditor key={editorKey} item={selected} isNew={isNew} capabilities={capabilities} baseDir={baseDir} responses={responses} send={send} onSaved={setSelection} />
  </section>;
}
