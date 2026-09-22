import { useEffect, useMemo, useRef, useState } from 'react';

import { useAppSelector } from '../../app/hooks';
import { selectConnection, selectTasksState, selectAgentsState } from '../../app/store';
import { readCommand } from '../../protocol/http';

import { Button, StateSurface } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { ContextLinkEditor, ContextLinkList } from './ContextLinks';
import { contextTargets, contextLinkTarget, savedContextLinks, type ContextLink, type ContextTarget } from './contextLinksModel';
import styles from './ControlCenter.module.css';

const MEMORY_TYPES = ['finding', 'decision', 'warning', 'handoff', 'note'] as const;
type ContextFocus = 'all' | 'group' | 'agent' | 'task' | 'pipeline' | 'project';

interface ContextPanelProps {
  group: string;
  agents: UnknownRecord[];
  onOpenTarget?: (target: ContextTarget) => void;
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

function memoryDraft(entry: UnknownRecord, group: string): MemoryDraft {
  return { entryId: text(entry.id), title: text(entry.title), content: text(entry.content), entryType: text(entry.entry_type, 'note'), scopeKind: text(entry.scope_kind, 'group'), scopeRef: text(entry.scope_ref, group), pinned: bool(entry.pinned) };
}

export function ContextPanel({ group, agents, onOpenTarget }: ContextPanelProps) {
  const connection = useAppSelector(selectConnection);
  const taskState = useAppSelector(selectTasksState); const agentState = useAppSelector(selectAgentsState);
  const targets = useMemo(() => contextTargets(Object.values(taskState.records).map(record), [...Object.values(agentState.records).map(record), ...agents.filter((agent) => !agentState.records[text(agent.id)])]), [taskState.records, agentState.records, agents]);
  const [search, setSearch] = useState('');
  const [entryType, setEntryType] = useState('');
  const [focus, setFocus] = useState<ContextFocus>('all');
  const [scopeRef, setScopeRef] = useState('');
  const [pinnedOnly, setPinnedOnly] = useState(false);
  const [selectedId, setSelectedId] = useState('');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<MemoryDraft>(() => emptyDraft(group));
  const [baseline, setBaseline] = useState<MemoryDraft | null>(null);
  const [links, setLinks] = useState<ContextLink[]>([]);
  const invalidLink = !draft.entryId && links.some((link) => !contextLinkTarget(link, targets));
  const [applied, setApplied] = useState({ search: '', entryType: '', focus: 'all' as ContextFocus, scopeRef: '', pinnedOnly: false });
  const [entries, setEntries] = useState<UnknownRecord[]>([]);
  const [previousEntries, setPreviousEntries] = useState(entries);
  const [loaded, setLoaded] = useState(false);
  const [readError, setReadError] = useState('');
  const [writeError, setWriteError] = useState('');
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const mutation = useRef<AbortController | null>(null);
  const activeRead = useRef<AbortController | null>(null);
  useEffect(() => () => mutation.current?.abort(), []);
  if (previousEntries !== entries) {
    setPreviousEntries(entries);
    const latest = baseline && entries.find((entry) => text(entry.id) === baseline.entryId);
    if (editing && latest && baseline) {
      const next = memoryDraft(latest, group);
      setDraft({ ...next, ...Object.fromEntries(Object.entries(draft).filter(([key, value]) => value !== baseline[key as keyof MemoryDraft])) });
      setBaseline(next);
    }
  }
  const query = useMemo(() => {
    const command: TorqueCommand = { cmd: 'memory_list', group_name: group, search: applied.search, entry_type: applied.entryType, pinned_only: applied.pinnedOnly, limit: 100 };
    if (applied.focus === 'agent' && applied.scopeRef) Object.assign(command, { linked_target_kind: 'agent', linked_target_ref: applied.scopeRef });
    else if (applied.focus === 'group') Object.assign(command, { scope_kind: 'group', scope_ref: group });
    else if (applied.focus !== 'all' && applied.scopeRef) Object.assign(command, { scope_kind: applied.focus, scope_ref: applied.scopeRef });
    return command;
  }, [group, applied]);
  const refresh = () => setRefreshVersion((value) => value + 1);
  useEffect(() => {
    if (connection.status !== 'connected' || busy) return;
    const controller = new AbortController(); activeRead.current = controller;
    void readCommand(query, controller.signal).then((frame) => {
      if (controller.signal.aborted || mutation.current) return;
      if (frame.type !== 'memory_entries' || !Array.isArray(frame.entries) || frame.group_name !== group) throw new Error(text(frame.message, 'Could not load matching shared context.'));
      setEntries(list(frame.entries)); setLoaded(true); setReadError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setReadError(cause instanceof Error ? cause.message : 'Context refresh failed.'); });
    return () => controller.abort();
  }, [query, group, connection.status, connection.reconnectCount, refreshVersion, busy]);
  const applyFilters = () => { setApplied({ search, entryType, focus, scopeRef, pinnedOnly }); refresh(); };
  const mutate = async (command: TorqueCommand, editingEntry = false) => {
    if (mutation.current) return;
    activeRead.current?.abort();
    const controller = new AbortController(); mutation.current = controller; setBusy(true); setWriteError('');
    try {
      const frame = await readCommand(command, controller.signal);
      if (controller.signal.aborted) return;
      const entry = record(frame.entry);
      if (frame.type !== 'memory_entry' || !text(entry.id) || (command.entry_id && entry.id !== command.entry_id)) throw new Error(text(frame.message, 'The context update was not acknowledged.'));
      setEntries((current) => [entry, ...current.filter((item) => item.id !== entry.id)]); setSelectedId(text(entry.id));
      if (editingEntry) { setEditing(false); setBaseline(null); setDraft(emptyDraft(group)); }
    } catch (cause: unknown) {
      if (!controller.signal.aborted) setWriteError(cause instanceof Error ? cause.message : 'Context update failed.');
    } finally { mutation.current = null; if (!controller.signal.aborted) { setBusy(false); refresh(); } }
  };
  const publish = () => {
    if (invalidLink) { setWriteError('Remove unavailable links before publishing.'); return; }
    const values = { title: draft.title, content: draft.content, entry_type: draft.entryType, scope_kind: draft.scopeKind, scope_ref: draft.scopeRef, pinned: draft.pinned };
    const before = baseline ? { title: baseline.title, content: baseline.content, entry_type: baseline.entryType, scope_kind: baseline.scopeKind, scope_ref: baseline.scopeRef, pinned: baseline.pinned } : null;
    const changes = before ? Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== before[key as keyof typeof before])) : values;
    void mutate({ cmd: 'memory_publish', ...changes, ...(draft.entryId ? { entry_id: draft.entryId } : { source_kind: 'manual', link_targets: links }) }, true);
  };

  const selected = entries.find((entry) => text(entry.id) === selectedId) ?? entries[0] ?? null;
  const selectedVisibleId = selected ? text(selected.id) : '';
  const resetEditor = () => { setDraft(emptyDraft(group)); setBaseline(null); setEditing(false); setWriteError(''); };
  const edit = (entry: UnknownRecord) => { const value = memoryDraft(entry, group); setDraft(value); setBaseline(value); setEditing(true); setWriteError(''); };
  const create = () => { setDraft({ ...emptyDraft(group), scopeKind: focus === 'all' || focus === 'agent' ? 'group' : focus, scopeRef: focus === 'all' || focus === 'agent' || focus === 'group' ? group : scopeRef }); setBaseline(null); setLinks(scopeRef && (focus === 'agent' || focus === 'task') ? [{ target_kind: focus, target_ref: scopeRef }] : []); setEditing(true); setWriteError(''); };

  return <div className={styles.contextPanel}>
    <div><header className={styles.contextToolbar}>
      <label>Search<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search shared context" /></label>
      <label>Focus<select value={focus} onChange={(event) => { const next = event.target.value as ContextFocus; setFocus(next); setScopeRef(next === 'group' ? group : ''); }}><option value="all">All group context</option><option value="group">Group</option><option value="agent">Agent</option><option value="task">Task</option><option value="pipeline">Pipeline</option><option value="project">Project</option></select></label>
      {focus === 'agent' ? <label>Agent<select value={scopeRef} onChange={(event) => setScopeRef(event.target.value)}><option value="">Choose an agent…</option>{agents.filter((agent) => text(agent.cell_type, 'agent') !== 'terminal').map((agent) => <option key={text(agent.id)} value={text(agent.id)}>{text(agent.name, text(agent.id))}</option>)}</select></label> : focus !== 'all' && focus !== 'group' ? <label>{focus[0]?.toUpperCase()}{focus.slice(1)} reference<input value={scopeRef} onChange={(event) => setScopeRef(event.target.value)} placeholder={`${focus} ID or reference`} /></label> : null}
      <label>Type<select value={entryType} onChange={(event) => setEntryType(event.target.value)}><option value="">All types</option>{MEMORY_TYPES.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label className={styles.contextCheck}><input type="checkbox" checked={pinnedOnly} onChange={(event) => setPinnedOnly(event.target.checked)} />Pinned only</label>
      <Button onPress={applyFilters}>Apply</Button>
      <Button tone="primary" isDisabled={busy} onPress={create}>＋ Add context</Button>
    </header>
    {readError ? <p role="alert">Context refresh failed. Your current view and draft are retained. {readError} <Button onPress={refresh}>Retry context</Button></p> : null}
    {writeError ? <p role="alert">{writeError} Your draft and selection are retained; retry when ready.</p> : null}
    </div><div className={styles.contextSplit}>
      <aside className={styles.contextList} aria-label="Shared context entries">
        <header><div><h2>Shared context</h2><p>{applied.focus === 'all' ? group : applied.focus}</p></div><span>{entries.length}</span></header>
        {entries.length ? entries.map((entry) => { const id = text(entry.id); return <button key={id} aria-current={!editing && selectedVisibleId === id ? 'page' : undefined} disabled={busy} onClick={() => { setSelectedId(id); resetEditor(); }}><span><strong>{text(entry.title, text(entry.entry_type, 'Memory'))}</strong>{bool(entry.pinned) ? <small>pinned</small> : null}</span><p>{text(entry.content, 'No content')}</p><footer><span>{text(entry.entry_type, 'note')} · {text(entry.scope_kind, 'group')}</span><time>{timestamp(entry.updated_at ?? entry.created_at)}</time></footer></button>; }) : <StateSurface title={loaded ? "No shared context" : readError ? "Context unavailable" : "Loading shared context"} description="Publish a finding, decision, warning, handoff, or note for future work." />}
      </aside>
      <main className={styles.contextDetail}>
        {editing ? <form className={styles.contextEditor} onSubmit={(event) => {
          event.preventDefault();
          publish();
        }}>
          <header><div><span>{draft.entryId ? 'Edit shared context' : 'New shared context'}</span><h2>{draft.entryId ? draft.title || 'Untitled context' : 'Publish shared context'}</h2></div><Button tone="quiet" type="button" isDisabled={busy} onPress={resetEditor}>Cancel</Button></header>
          <fieldset disabled={busy} className={styles.contextEditorGrid}><label>Title<input maxLength={200} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder="A short, scannable title" /></label><label>Type<select value={draft.entryType} onChange={(event) => setDraft({ ...draft, entryType: event.target.value })}>{MEMORY_TYPES.map((value) => <option key={value}>{value}</option>)}</select></label><label>Scope<select value={draft.scopeKind} onChange={(event) => setDraft({ ...draft, scopeKind: event.target.value, scopeRef: event.target.value === 'group' ? group : '' })}><option value="group">Group</option><option value="project">Project</option><option value="task">Task</option><option value="pipeline">Pipeline</option></select></label><label>Scope reference<input value={draft.scopeRef} onChange={(event) => setDraft({ ...draft, scopeRef: event.target.value })} required /></label><label className={styles.contextEditorContent}>Content<textarea maxLength={4000} value={draft.content} onChange={(event) => setDraft({ ...draft, content: event.target.value })} placeholder="What should future work know?" /></label><label className={styles.contextCheck}><input type="checkbox" checked={draft.pinned} onChange={(event) => setDraft({ ...draft, pinned: event.target.checked })} />Pin for ranking</label>{!draft.entryId ? <div className={styles.contextEditorContent}><ContextLinkEditor links={links} targets={targets} group={group} onChange={setLinks} /></div> : null}</fieldset>
          <footer><span>Context expires according to group retention. Pinning affects ranking only.</span><Button tone="primary" type="submit" isDisabled={busy || invalidLink || !draft.content.trim() || !draft.scopeRef.trim()}>{busy ? 'Saving…' : draft.entryId ? 'Save context' : 'Publish context'}</Button></footer>
        </form> : selected ? <article className={styles.contextEntry}>
          <header><div><span>{text(selected.entry_type, 'note')}{bool(selected.pinned) ? ' · pinned' : ''}</span><h2>{text(selected.title, 'Untitled context')}</h2></div><div><Button tone="quiet" isDisabled={busy} onPress={() => { void mutate({ cmd: bool(selected.pinned) ? 'memory_unpin' : 'memory_pin', entry_id: text(selected.id) }); }}>{bool(selected.pinned) ? 'Unpin' : 'Pin'}</Button><Button isDisabled={busy} onPress={() => edit(selected)}>Edit</Button></div></header><p>{text(selected.content, 'No content')}</p><dl><div><dt>Source</dt><dd>{text(selected.source_name) || text(selected.source_kind, 'manual')}</dd></div><div><dt>Updated</dt><dd>{timestamp(selected.updated_at ?? selected.created_at)}</dd></div><div><dt>Scope</dt><dd>{text(selected.scope_kind, 'group')} · {text(selected.scope_ref, group)}</dd></div><div><dt>Expires</dt><dd>{selected.expires_at ? timestamp(selected.expires_at) : 'Not recorded'}</dd></div></dl><ContextLinkList links={savedContextLinks(selected.links)} targets={targets} onOpen={onOpenTarget} />
        </article> : <StateSurface title="Select or add context" description="Shared context now lives in Control Center, separate from an individual agent’s operational panel." action={<Button tone="primary" onPress={create}>Add context</Button>} />}
      </main>
    </div>
  </div>;
}
