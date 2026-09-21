import { useEffect, useState } from 'react';

import { Button, StateSurface } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import styles from './ControlCenter.module.css';

const MEMORY_TYPES = ['note', 'decision', 'constraint', 'finding', 'summary', 'handoff'] as const;
type ContextFocus = 'all' | 'group' | 'agent' | 'task' | 'pipeline' | 'project';

interface ContextPanelProps {
  group: string;
  agents: UnknownRecord[];
  responses: Record<string, unknown>;
  send: (command: TorqueCommand) => void;
}

interface MemoryDraft {
  entryId: string;
  title: string;
  content: string;
  entryType: string;
  scopeKind: string;
  scopeRef: string;
  pinned: boolean;
}

function record(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
}

function list(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}

function bool(value: unknown): boolean {
  return value === true || value === 1 || value === 'true';
}

function timestamp(value: unknown): string {
  const numeric = Number(value ?? 0);
  if (!numeric) return 'No timestamp';
  const date = new Date(numeric < 1_000_000_000_000 ? numeric * 1_000 : numeric);
  return Number.isNaN(date.getTime()) ? 'No timestamp' : date.toLocaleString();
}

function emptyDraft(group: string): MemoryDraft {
  return { entryId: '', title: '', content: '', entryType: 'note', scopeKind: 'group', scopeRef: group, pinned: false };
}

export function ContextPanel({ group, agents, responses, send }: ContextPanelProps) {
  const [search, setSearch] = useState('');
  const [entryType, setEntryType] = useState('');
  const [focus, setFocus] = useState<ContextFocus>('all');
  const [scopeRef, setScopeRef] = useState('');
  const [pinnedOnly, setPinnedOnly] = useState(false);
  const [selectedId, setSelectedId] = useState('');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<MemoryDraft>(() => emptyDraft(group));

  const queryTarget = focus === 'agent' ? scopeRef : focus === 'all' ? group : focus === 'group' ? group : scopeRef;
  const refresh = () => {
    const command: TorqueCommand = { cmd: 'memory_list', group_name: group, search, entry_type: entryType, pinned_only: pinnedOnly, limit: 100 };
    if (focus === 'agent' && scopeRef) Object.assign(command, { linked_target_kind: 'agent', linked_target_ref: scopeRef });
    else if (focus === 'group') Object.assign(command, { scope_kind: 'group', scope_ref: group });
    else if (focus !== 'all' && scopeRef) Object.assign(command, { scope_kind: focus, scope_ref: scopeRef });
    send(command);
  };

  useEffect(() => {
    refresh();
    // Filters are applied explicitly to avoid issuing a request per keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group]);

  const frame = record(responses[`memory_entries:${queryTarget || group}`] ?? responses['memory_entries:latest']);
  const entries = list(frame.entries);
  const selected = entries.find((entry) => text(entry.id) === selectedId) ?? entries[0] ?? null;
  const selectedVisibleId = selected ? text(selected.id) : '';
  const resetEditor = () => { setDraft(emptyDraft(group)); setEditing(false); };
  const edit = (entry: UnknownRecord) => {
    setDraft({
      entryId: text(entry.id), title: text(entry.title), content: text(entry.content),
      entryType: text(entry.entry_type, 'note'), scopeKind: text(entry.scope_kind, 'group'),
      scopeRef: text(entry.scope_ref, group), pinned: bool(entry.pinned),
    });
    setEditing(true);
  };

  return <div className={styles.contextPanel}>
    <header className={styles.contextToolbar}>
      <label>Search<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search shared context" /></label>
      <label>Focus<select value={focus} onChange={(event) => { const next = event.target.value as ContextFocus; setFocus(next); setScopeRef(next === 'group' ? group : ''); }}><option value="all">All group context</option><option value="group">Group</option><option value="agent">Agent</option><option value="task">Task</option><option value="pipeline">Pipeline</option><option value="project">Project</option></select></label>
      {focus === 'agent' ? <label>Agent<select value={scopeRef} onChange={(event) => setScopeRef(event.target.value)}><option value="">Choose an agent…</option>{agents.filter((agent) => text(agent.cell_type, 'agent') !== 'terminal').map((agent) => <option key={text(agent.id)} value={text(agent.id)}>{text(agent.name, text(agent.id))}</option>)}</select></label> : focus !== 'all' && focus !== 'group' ? <label>{focus[0]?.toUpperCase()}{focus.slice(1)} reference<input value={scopeRef} onChange={(event) => setScopeRef(event.target.value)} placeholder={`${focus} ID or reference`} /></label> : null}
      <label>Type<select value={entryType} onChange={(event) => setEntryType(event.target.value)}><option value="">All types</option>{MEMORY_TYPES.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label className={styles.contextCheck}><input type="checkbox" checked={pinnedOnly} onChange={(event) => setPinnedOnly(event.target.checked)} />Pinned only</label>
      <Button onPress={refresh}>Apply</Button>
      <Button tone="primary" onPress={() => { setDraft({ ...emptyDraft(group), scopeKind: focus === 'all' || focus === 'agent' ? 'group' : focus, scopeRef: focus === 'all' || focus === 'agent' || focus === 'group' ? group : scopeRef }); setEditing(true); }}>＋ Add context</Button>
    </header>
    <div className={styles.contextSplit}>
      <aside className={styles.contextList} aria-label="Shared context entries">
        <header><div><h2>Shared context</h2><p>{focus === 'all' ? group : focus}</p></div><span>{entries.length}</span></header>
        {entries.length ? entries.map((entry) => { const id = text(entry.id); return <button key={id} aria-current={!editing && selectedVisibleId === id ? 'page' : undefined} onClick={() => { setSelectedId(id); setEditing(false); }}><span><strong>{text(entry.title, text(entry.entry_type, 'Memory'))}</strong>{bool(entry.pinned) ? <small>pinned</small> : null}</span><p>{text(entry.content, 'No content')}</p><footer><span>{text(entry.entry_type, 'note')} · {text(entry.scope_kind, 'group')}</span><time>{timestamp(entry.updated_at ?? entry.created_at)}</time></footer></button>; }) : <StateSurface title="No shared context" description="Publish a decision, constraint, finding, handoff, summary, or note for future work." />}
      </aside>
      <main className={styles.contextDetail}>
        {editing ? <form className={styles.contextEditor} onSubmit={(event) => {
          event.preventDefault();
          send({ cmd: 'memory_publish', ...(draft.entryId ? { entry_id: draft.entryId } : {}), title: draft.title, content: draft.content, entry_type: draft.entryType, scope_kind: draft.scopeKind, scope_ref: draft.scopeRef, pinned: draft.pinned, source_kind: 'manual', ...(!draft.entryId && focus === 'agent' && scopeRef ? { link_targets: [{ target_kind: 'agent', target_ref: scopeRef }] } : {}) });
          refresh(); resetEditor();
        }}>
          <header><div><span>{draft.entryId ? 'Edit shared context' : 'New shared context'}</span><h2>{draft.entryId ? draft.title || 'Untitled context' : 'Publish durable memory'}</h2></div><Button tone="quiet" type="button" onPress={resetEditor}>Cancel</Button></header>
          <div className={styles.contextEditorGrid}><label>Title<input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder="A short, scannable title" /></label><label>Type<select value={draft.entryType} onChange={(event) => setDraft({ ...draft, entryType: event.target.value })}>{MEMORY_TYPES.map((value) => <option key={value}>{value}</option>)}</select></label><label>Scope<select value={draft.scopeKind} onChange={(event) => setDraft({ ...draft, scopeKind: event.target.value, scopeRef: event.target.value === 'group' ? group : '' })}><option value="group">Group</option><option value="project">Project</option><option value="task">Task</option><option value="pipeline">Pipeline</option></select></label><label>Scope reference<input value={draft.scopeRef} onChange={(event) => setDraft({ ...draft, scopeRef: event.target.value })} required /></label><label className={styles.contextEditorContent}>Content<textarea value={draft.content} onChange={(event) => setDraft({ ...draft, content: event.target.value })} placeholder="What should future work know?" /></label><label className={styles.contextCheck}><input type="checkbox" checked={draft.pinned} onChange={(event) => setDraft({ ...draft, pinned: event.target.checked })} />Pin for ranking</label></div>
          <footer><span>Context remains durable and available to authorized agents.</span><Button tone="primary" type="submit" isDisabled={!draft.content.trim() || !draft.scopeRef.trim()}>{draft.entryId ? 'Save context' : 'Publish context'}</Button></footer>
        </form> : selected ? <article className={styles.contextEntry}>
          <header><div><span>{text(selected.entry_type, 'note')}{bool(selected.pinned) ? ' · pinned' : ''}</span><h2>{text(selected.title, 'Untitled context')}</h2></div><div><Button tone="quiet" onPress={() => { send({ cmd: bool(selected.pinned) ? 'memory_unpin' : 'memory_pin', entry_id: text(selected.id) }); refresh(); }}>{bool(selected.pinned) ? 'Unpin' : 'Pin'}</Button><Button onPress={() => edit(selected)}>Edit</Button></div></header><p>{text(selected.content, 'No content')}</p><dl><div><dt>Source</dt><dd>{text(selected.source_name, text(selected.source_kind, 'manual'))}</dd></div><div><dt>Updated</dt><dd>{timestamp(selected.updated_at ?? selected.created_at)}</dd></div><div><dt>Scope</dt><dd>{text(selected.scope_kind, 'group')} · {text(selected.scope_ref, group)}</dd></div><div><dt>Retention</dt><dd>{text(selected.retention_kind, 'durable')}</dd></div></dl>
        </article> : <StateSurface title="Select or add context" description="Shared context now lives in Control Center, separate from an individual agent’s operational panel." action={<Button tone="primary" onPress={() => setEditing(true)}>Add context</Button>} />}
      </main>
    </div>
  </div>;
}
