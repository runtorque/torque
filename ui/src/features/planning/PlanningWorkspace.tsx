import { AreaEditor } from './AreaEditor';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useAppSelector } from '../../app/hooks';
import { selectAgentsState, selectConnection, selectPlanningState, selectTasksState } from '../../app/store';
import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { CommandSender } from '../board/BoardPanel';
import { groupInitiatives, groupRecords, records, text } from './model';
import { DecisionEditor, InitiativeEditor, ThinkingEditor } from './PlanningEditors';
import styles from './PlanningWorkspace.module.css';

type PlanningTab = 'roadmap' | 'areas' | 'thinking' | 'decisions' | 'team' | 'schedules';

const tabs: { id: PlanningTab; label: string }[] = [
  { id: 'roadmap', label: 'Initiatives' },
  { id: 'areas', label: 'Areas' },
  { id: 'thinking', label: 'Thinking' },
  { id: 'decisions', label: 'Decisions' },
  { id: 'team', label: 'Hires & journals' },
  { id: 'schedules', label: 'Schedules' },
];

function Card({ item, eyebrow, onOpen }: { item: Record<string, unknown>; eyebrow?: string; onOpen?: () => void }) {
  const title = text(item.title, text(item.task, text(item.name, 'Untitled')));
  const summary = text(item.summary, text(item.body, text(item.reason, 'No summary yet.')));
  return <button type="button" className={styles.card} title={title} onClick={onOpen}>
    <div><span title={eyebrow || text(item.priority, text(item.lifecycle, 'active'))}>{eyebrow || text(item.priority, text(item.lifecycle, 'active'))}</span><small title={text(item.id)}>{text(item.id)}</small></div>
    <strong title={title}>{title}</strong>
    <p title={summary}>{summary}</p>
  </button>;
}

export function PlanningWorkspace({ group, sendCommand, onCommandUnavailable }: {
  group: string;
  sendCommand: CommandSender;
  onCommandUnavailable: () => void;
}) {
  const planning = useAppSelector(selectPlanningState);
  const tasks = useAppSelector(selectTasksState);
  const agents = useAppSelector(selectAgentsState);
  const connection = useAppSelector(selectConnection);
  const [tab, setTab] = useState<PlanningTab>('roadmap');
  const [createKind, setCreateKind] = useState<'initiative' | 'area' | 'note' | 'brief' | 'decision' | null>(null);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [architectId, setArchitectId] = useState('');
  const [selected, setSelected] = useState<{ kind: 'initiative' | 'area' | 'note' | 'brief' | 'decision'; item: Record<string, unknown> } | null>(null);
  const [requested, setRequested] = useState(false);
  const lastRequestKey = useRef('');
  const error = connection.lastAuxiliaryFrame?.type === 'error'
    ? text(connection.lastAuxiliaryFrame.message, 'The request failed.') : '';

  const refresh = useCallback(() => {
    const commands = [
      { cmd: 'initiative_list', group, include_archived: false },
      { cmd: 'area_list', group, include_links: true, include_notes: true },
      { cmd: 'scratchpad_note_list', group },
      { cmd: 'idea_brief_list', group },
      { cmd: 'decisions_snapshot' },
      { cmd: 'pending_hires_snapshot', status: 'pending' },
      { cmd: 'engineer_journal_snapshot', group, include_streams: true },
    ];
    const sent = commands.every((command) => sendCommand(command));
    if (!sent) onCommandUnavailable();
    setRequested(sent);
  }, [group, onCommandUnavailable, sendCommand]);

  useEffect(() => {
    if (!group || connection.status !== 'connected') return;
    const key = `${group}:${connection.reconnectCount}`;
    if (lastRequestKey.current === key) return;
    lastRequestKey.current = key;
    refresh();
  }, [group, connection.status, connection.reconnectCount, refresh]);

  useEffect(() => {
    const type = connection.lastAuxiliaryFrame?.type || '';
    // Link command replies carry the mutation, while compact clients may not
    // receive a relationship delta. Rehydrate without remounting the editor.
    if (/^initiative_(task|decision)_(linked|unlinked)$/.test(type)) sendCommand({ cmd: 'initiative_list', group, include_archived: false });
    if (/^area_(linked|unlinked|note_(created|updated|archived))$/.test(type)) sendCommand({ cmd: 'area_list', group, include_links: true, include_notes: true });
  }, [connection.lastAuxiliaryFrame, group, sendCommand]);

  const initiatives = useMemo(() => groupInitiatives(planning.initiatives, group), [planning.initiatives, group]);
  const areas = useMemo(() => groupRecords(planning.areas, group), [planning.areas, group]);
  const briefs = useMemo(() => groupRecords(planning.ideaBriefs, group), [planning.ideaBriefs, group]);
  const notes = useMemo(() => groupRecords(planning.scratchpadNotes, group), [planning.scratchpadNotes, group]);
  const decisions = useMemo(() => records(planning.decisions), [planning.decisions]);
  const hires = useMemo(() => records(planning.pendingHires), [planning.pendingHires]);
  const journals = useMemo(() => Object.values(planning.journals).flatMap(records), [planning.journals]);
  const schedules = useMemo(() => records(tasks.schedules).filter((item) => !item.group || item.group === group), [tasks.schedules, group]);
  const taskItems = useMemo(() => records(tasks.records).filter((item) => !item.group || item.group === group), [tasks.records, group]);
  const agentItems = useMemo(() => records(agents.records).filter((item) => !item.group || item.group === group), [agents.records, group]);
  const engineers = agentItems.filter((item) => item.kind === 'engineer');
  const architects = agentItems.filter((item) => item.kind === 'architect');
  const totalInitiatives = Object.values(initiatives).reduce((sum, items) => sum + items.length, 0);
  const createKindForTab = tab === 'roadmap' ? 'initiative'
    : tab === 'areas' ? 'area'
      : tab === 'thinking' ? 'note'
        : tab === 'decisions' ? 'decision'
          : null;

  const create = () => {
    const value = title.trim();
    if (!value || !createKind || (createKind === 'decision' && !architectId)) return;
    const command = createKind === 'initiative'
      ? { cmd: 'initiative_create', group, title: value, summary: description, planning_status: 'triage' }
      : createKind === 'area'
        ? { cmd: 'area_create', group, title: value, summary: description, lifecycle: 'planned' }
        : createKind === 'brief'
          ? { cmd: 'idea_brief_create', group, title: value, summary: description, status: 'draft' }
          : createKind === 'decision'
            ? { cmd: 'architect_decision_create', architect_id: architectId, title: value, rationale: description, linked_task_ids: [], linked_engineer_ids: [] }
            : { cmd: 'scratchpad_note_create', group, title: value, body: description };
    if (!sendCommand(command)) onCommandUnavailable();
    setCreateKind(null);
    setTitle('');
    setDescription('');
  };

  const empty = (name: string, description: string) => requested
    ? <StateSurface title={`No ${name}`} description={description} />
    : <StateSurface title={`Loading ${name}`} description="Torque is hydrating this panel on demand." />;

  return <section className={styles.root} aria-label="Planning">
    <header className={styles.header}>
      <div><p>Workspace / {group || 'No group'}</p><h1>Planning</h1></div>
      <span>{totalInitiatives} initiatives · {areas.length} areas</span>
      <Button tone="quiet" onPress={refresh}>Refresh</Button>
      {createKindForTab ? <Button tone="primary" onPress={() => setCreateKind(createKindForTab)}>＋ New</Button> : null}
    </header>
    <nav className={styles.tabs} aria-label="Planning sections">
      {tabs.map((item) => <button key={item.id} aria-current={tab === item.id ? 'page' : undefined} onClick={() => setTab(item.id)}>{item.label}</button>)}
    </nav>
    {error ? <div className={styles.error} role="alert">{error}</div> : null}
    <div className={styles.content}>
      {tab === 'roadmap' ? <div className={styles.roadmap}>
        {(['triage', 'now', 'next', 'later', 'done'] as const).map((status) => <section key={status} className={styles.column}>
          <header><h2>{status}</h2><span>{initiatives[status].length}</span></header>
          <div>{initiatives[status].length ? initiatives[status].map((item) => <Card key={item.id} item={item} onOpen={() => setSelected({ kind: 'initiative', item })} />) : empty('initiatives', `Nothing is scheduled for ${status}; use New to add the next initiative.`)}</div>
        </section>)}
      </div> : null}
      {tab === 'areas' ? <div className={styles.grid}>{areas.length ? areas.map((item) => <Card key={item.id} item={item} eyebrow={text(item.area_type, 'area')} onOpen={() => setSelected({ kind: 'area', item })} />) : empty('areas', 'Use New to organize durable product context around an Area.')}</div> : null}
      {tab === 'thinking' ? <div className={styles.split}>
        <section><header><h2>Idea briefs</h2><span>{briefs.length}</span><Button tone="quiet" onPress={() => setCreateKind('brief')}>＋ Brief</Button></header><div className={styles.list}>{briefs.length ? briefs.map((item) => <Card key={item.id} item={item} eyebrow={text(item.status, 'idea')} onOpen={() => setSelected({ kind: 'brief', item })} />) : empty('idea briefs', 'Use Brief to shape a promising idea before it becomes Board work.')}</div></section>
        <section><header><h2>Scratchpad</h2><span>{notes.length}</span><Button tone="quiet" onPress={() => setCreateKind('note')}>＋ Note</Button></header><div className={styles.list}>{notes.length ? notes.map((item) => <Card key={item.id} item={item} eyebrow="note" onOpen={() => setSelected({ kind: 'note', item })} />) : empty('notes', 'Use Note to capture rough thinking without creating a Board task.')}</div></section>
      </div> : null}
      {tab === 'decisions' ? <div className={styles.grid}>{decisions.length ? decisions.map((item) => <Card key={item.id} item={item} eyebrow={text(item.status, 'decision')} onOpen={() => setSelected({ kind: 'decision', item })} />) : empty('decisions', 'Architect decisions will appear after they are recorded.')}</div> : null}
      {tab === 'team' ? <div className={styles.split}>
        <section><header><h2>Pending hires</h2><span>{hires.length}</span></header><div className={styles.list}>{hires.length ? hires.map((item) => <article className={styles.hireCard} key={item.id}><Card item={item} eyebrow={text(item.status, 'pending')} /><div><Button tone="quiet" onPress={() => sendCommand({ cmd: 'pending_hire_reject', id: item.id, note: 'Rejected by user from Planning' })}>Reject</Button><Button tone="primary" onPress={() => sendCommand({ cmd: 'pending_hire_approve', id: item.id })}>Approve</Button></div></article>) : empty('pending hires', 'Architect hiring requests will appear here for review.')}</div></section>
        <section><header><h2>Engineer journals</h2><span>{journals.length}</span></header><div className={styles.list}>{journals.length ? journals.map((item) => <Card key={item.id} item={item} eyebrow="journal" />) : empty('journal entries', 'Engineer progress journals are empty for this group.')}</div></section>
      </div> : null}
      {tab === 'schedules' ? <div className={styles.grid}>{schedules.length ? schedules.map((item) => <Card key={item.id} item={item} eyebrow={item.enabled === false ? 'paused' : 'enabled'} />) : empty('schedules', 'Create recurring work from the Board schedule editor.')}</div> : null}
    </div>
    <ModalDialog title={`New ${createKind ?? 'planning item'}`} description={`Create in ${group}.`} size="small" isOpen={createKind !== null} onOpenChange={(open) => { if (!open) setCreateKind(null); }}>
      <form className={styles.createForm} onSubmit={(event) => { event.preventDefault(); create(); }}>
        <label>Title<input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>{createKind === 'decision' ? 'Rationale' : createKind === 'note' ? 'Body' : 'Summary'}<textarea value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        {createKind === 'decision' ? <label>Architect<select value={architectId} onChange={(event) => setArchitectId(event.target.value)}><option value="">Choose an Architect…</option>{architects.map((agent) => <option key={agent.id} value={agent.id}>{text(agent.name, agent.id)}</option>)}</select></label> : null}
        <footer><Button tone="quiet" onPress={() => setCreateKind(null)}>Cancel</Button><Button tone="primary" type="submit" isDisabled={!title.trim() || (createKind === 'decision' && !architectId)}>Create</Button></footer>
      </form>
    </ModalDialog>
    {selected?.kind === 'initiative' ? <InitiativeEditor key={text(selected.item.id)} item={records(planning.initiatives).find((item) => item.id === selected.item.id) ?? selected.item} tasks={taskItems} decisions={decisions} send={(command) => { if (!sendCommand(command)) onCommandUnavailable(); }} onClose={() => setSelected(null)} /> : null}
    {selected?.kind === 'area' ? <AreaEditor key={text(selected.item.id)} item={records(planning.areas).find((item) => item.id === selected.item.id) ?? selected.item} targets={{ task: taskItems, decision: decisions, initiative: Object.values(initiatives).flat(), area: areas }} onClose={() => setSelected(null)} /> : null}
    {selected?.kind === 'note' || selected?.kind === 'brief' ? <ThinkingEditor key={text(selected.item.id)} kind={selected.kind} item={selected.item} send={(command) => { if (!sendCommand(command)) onCommandUnavailable(); }} onClose={() => setSelected(null)} /> : null}
    {selected?.kind === 'decision' ? <DecisionEditor key={text(selected.item.id)} item={selected.item} tasks={taskItems} engineers={engineers} send={(command) => { if (!sendCommand(command)) onCommandUnavailable(); }} onClose={() => setSelected(null)} /> : null}
  </section>;
}
