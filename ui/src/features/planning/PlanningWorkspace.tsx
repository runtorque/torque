import { AreaEditor } from './AreaEditor';
import { useEffect, useMemo, useRef, useState } from 'react';

import { useAppSelector } from '../../app/hooks';
import { selectAgentsState, selectConnection, selectTasksState } from '../../app/store';
import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { CommandSender } from '../board/BoardPanel';
import { groupInitiatives, groupRecords, planningStatuses, records, text } from './model';
import { DecisionEditor, InitiativeEditor } from './PlanningEditors';
import { ThinkingEditor } from './ThinkingEditor';
import { usePlanningMutation } from './usePlanningMutation';
import styles from './PlanningWorkspace.module.css';

import { planningReads, type PlanningTab } from './planningReads';
import { usePlanningReads } from './usePlanningReads';

const tabs: { id: PlanningTab; label: string }[] = [
  { id: 'roadmap', label: 'Initiatives' },
  { id: 'areas', label: 'Areas' },
  { id: 'thinking', label: 'Thinking' },
  { id: 'decisions', label: 'Decisions' },
  { id: 'team', label: 'Hires & journals' },
  { id: 'schedules', label: 'Schedules' },
];

function Card({ item, eyebrow, onOpen }: { item: Record<string, unknown>; eyebrow?: string; onOpen?: () => void }) {
  const title = text(item.title, text(item.task, text(item.name, text(item.requested_name, 'Untitled'))));
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
  const tasks = useAppSelector(selectTasksState);
  const agents = useAppSelector(selectAgentsState);
  const connection = useAppSelector(selectConnection);
  const [tab, setTab] = useState<PlanningTab>('roadmap');
  const [createKind, setCreateKind] = useState<'initiative' | 'area' | 'note' | 'brief' | 'decision' | null>(null);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [architectId, setArchitectId] = useState('');
  const [selected, setSelected] = useState<{ kind: 'initiative' | 'area' | 'note' | 'brief' | 'decision'; item: Record<string, unknown> } | null>(null);
  const createMutation = usePlanningMutation();
  const [showArchivedDecisions, setShowArchivedDecisions] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const reads = usePlanningReads(planningReads(tab, group, showArchived, showArchivedDecisions, selected?.kind));
  const { planning, refresh } = reads;

  const lastMutation = useRef(connection.lastAuxiliaryFrame);
  useEffect(() => {
    if (lastMutation.current === connection.lastAuxiliaryFrame) return;
    lastMutation.current = connection.lastAuxiliaryFrame;
    const type = connection.lastAuxiliaryFrame?.type || '';
    // Refresh only a displayed relationship collection after a compact reply.
    if ((tab === 'roadmap' && /^initiative_(task|decision)_(linked|unlinked)$/.test(type))
      || (tab === 'areas' && /^area_(linked|unlinked|note_(created|updated|archived))$/.test(type))) refresh();
  }, [connection.lastAuxiliaryFrame, tab, refresh]);

  const initiatives = useMemo(() => groupInitiatives(planning.initiatives, group), [planning.initiatives, group]);
  const areas = useMemo(() => groupRecords(planning.areas, group), [planning.areas, group]);
  const briefs = useMemo(() => groupRecords(planning.ideaBriefs, group).filter((item) => showArchived || (!item.archived && !item.archived_at && item.status !== 'archived')), [planning.ideaBriefs, group, showArchived]);
  const notes = useMemo(() => groupRecords(planning.scratchpadNotes, group).filter((item) => !item.deleted && (showArchived || !item.archived)), [planning.scratchpadNotes, group, showArchived]);
  const decisions = useMemo(() => records(planning.decisions).filter((item) => {
    const architect = agents.records[text(item.architect_id)] as Record<string, unknown> | undefined;
    return text(item.group, text(architect?.group)) === group;
  }), [planning.decisions, agents.records, group]);
  const visibleDecisions = decisions.filter((item) => showArchivedDecisions || !item.archived);
  const hires = useMemo(() => records(planning.pendingHires).filter((item) => text((agents.records[text(item.architect_id)] as Record<string, unknown> | undefined)?.group) === group), [planning.pendingHires, agents.records, group]);
  const journals = useMemo(() => Object.entries(planning.journals).filter(([author]) => text((agents.records[author] as Record<string, unknown> | undefined)?.group) === group).flatMap(([, entries]) => records(entries)), [planning.journals, agents.records, group]);
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
    if (!value || !createKind || (createKind === 'brief' && !description.trim()) || (createKind === 'decision' && (!architectId || !description.trim()))) return;
    const command = createKind === 'initiative'
      ? { cmd: 'initiative_create', group, title: value, summary: description, planning_status: 'triage' }
      : createKind === 'area'
        ? { cmd: 'area_create', group, title: value, summary: description, lifecycle: 'planned' }
        : createKind === 'brief'
          ? { cmd: 'idea_brief_create', group, title: value, problem_opportunity: description, status: 'draft' }
          : createKind === 'decision'
            ? { cmd: 'architect_decision_create', architect_id: architectId, title: value, rationale: description, linked_task_ids: [], linked_engineer_ids: [] }
            : { cmd: 'scratchpad_note_create', group, title: value, body: description };
    void createMutation.run(async (request) => {
      await request(command);
      setCreateKind(null); setTitle(''); setDescription('');
    });
  };

  const empty = (name: string, description: string) => reads.ready && !reads.pending
    ? <StateSurface title={`No ${name}`} description={description} />
    : <StateSurface title={`Loading ${name}`} description="Torque is hydrating this panel on demand." />;

  const readStatus = <>{!reads.ready ? <div role="status">Waiting for a synchronized connection. Loaded Planning remains available.</div> : reads.pending ? <div role="status">Refreshing Planning…</div> : null}
    {reads.error ? <div className={styles.error} role="alert">{reads.error} <Button onPress={refresh}>Retry Planning</Button></div> : null}</>;

  return <section className={styles.root} aria-label="Planning">
    <header className={styles.header}>
      <div><p>Workspace / {group || 'No group'}</p><h1>Planning</h1></div>
      <span>{tab === 'roadmap' ? `${totalInitiatives} initiatives` : tab === 'areas' ? `${areas.length} areas` : tab === 'thinking' ? `${briefs.length} briefs · ${notes.length} notes` : tab === 'decisions' ? `${visibleDecisions.length} decisions` : tab === 'team' ? `${hires.length} pending hires · ${journals.length} journal entries` : `${schedules.length} schedules`}</span>
      <Button tone="quiet" isDisabled={!reads.ready} onPress={refresh}>Refresh</Button>
      {createKindForTab ? <Button tone="primary" onPress={() => setCreateKind(createKindForTab)}>＋ New</Button> : null}
    </header>
    <nav className={styles.tabs} aria-label="Planning sections">
      {tabs.map((item) => <button key={item.id} aria-current={tab === item.id ? 'page' : undefined} onClick={() => setTab(item.id)}>{item.label}</button>)}
    </nav>
    {tab === 'thinking' ? <label className={styles.archiveFilter}><input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} />Show archived Thinking</label> : null}
    {tab === 'decisions' ? <label className={styles.archiveFilter}><input type="checkbox" checked={showArchivedDecisions} onChange={(event) => setShowArchivedDecisions(event.target.checked)} />Show archived decisions</label> : null}
    {!selected && !createKind ? readStatus : null}
    <div className={styles.content}>
      {tab === 'roadmap' ? <div className={styles.roadmap}>
        {planningStatuses.map((status) => <section key={status} className={styles.column}>
          <header><h2>{status}</h2><span>{initiatives[status].length}</span></header>
          <div>{initiatives[status].length ? initiatives[status].map((item) => <Card key={item.id} item={item} onOpen={() => setSelected({ kind: 'initiative', item })} />) : empty('initiatives', `Nothing is scheduled for ${status}; use New to add the next initiative.`)}</div>
        </section>)}
      </div> : null}
      {tab === 'areas' ? <div className={styles.grid}>{areas.length ? areas.map((item) => <Card key={item.id} item={item} eyebrow={text(item.area_type, 'area')} onOpen={() => setSelected({ kind: 'area', item })} />) : empty('areas', 'Use New to organize durable product context around an Area.')}</div> : null}
      {tab === 'thinking' ? <div className={styles.split}>
        <section><header><h2>Idea briefs</h2><span>{briefs.length}</span><Button tone="quiet" onPress={() => setCreateKind('brief')}>＋ Brief</Button></header><div className={styles.list}>{briefs.length ? briefs.map((item) => <Card key={item.id} item={item} eyebrow={text(item.status, 'idea')} onOpen={() => setSelected({ kind: 'brief', item })} />) : empty('idea briefs', 'Use Brief to shape a promising idea before it becomes Board work.')}</div></section>
        <section><header><h2>Scratchpad</h2><span>{notes.length}</span><Button tone="quiet" onPress={() => setCreateKind('note')}>＋ Note</Button></header><div className={styles.list}>{notes.length ? notes.map((item) => <Card key={item.id} item={item} eyebrow="note" onOpen={() => setSelected({ kind: 'note', item })} />) : empty('notes', 'Use Note to capture rough thinking without creating a Board task.')}</div></section>
      </div> : null}
      {tab === 'decisions' ? <div className={styles.grid}>{visibleDecisions.length ? visibleDecisions.map((item) => <Card key={item.id} item={item} eyebrow={text(item.status, 'decision')} onOpen={() => setSelected({ kind: 'decision', item })} />) : empty('decisions', 'Architect decisions will appear after they are recorded.')}</div> : null}
      {tab === 'team' ? <div className={styles.split}>
        <section><header><h2>Pending hires</h2><span>{hires.length}</span></header><div className={styles.list}>{hires.length ? hires.map((item) => <article className={styles.hireCard} key={item.id}><Card item={item} eyebrow={text(item.status, 'pending')} /><div><Button tone="quiet" onPress={() => { if (!sendCommand({ cmd: 'pending_hire_reject', id: item.id, note: 'Rejected by user from Planning' })) onCommandUnavailable(); }}>Reject</Button><Button tone="primary" onPress={() => { if (!sendCommand({ cmd: 'pending_hire_approve', id: item.id })) onCommandUnavailable(); }}>Approve</Button></div></article>) : empty('pending hires', 'Architect hiring requests will appear here for review.')}</div></section>
        <section><header><h2>Engineer journals</h2><span>{journals.length}</span></header><div className={styles.list}>{journals.length ? journals.map((item) => <Card key={item.id} item={item} eyebrow="journal" />) : empty('journal entries', 'Engineer progress journals are empty for this group.')}</div></section>
      </div> : null}
      {tab === 'schedules' ? <div className={styles.grid}>{schedules.length ? schedules.map((item) => <Card key={item.id} item={item} eyebrow={item.enabled === false ? 'paused' : 'enabled'} />) : empty('schedules', 'Create recurring work from the Board schedule editor.')}</div> : null}
    </div>
    <ModalDialog title={`New ${createKind ?? 'planning item'}`} description={`Create in ${group}.`} size="small" isOpen={createKind !== null} onOpenChange={(open) => { if (!open && !createMutation.busy.current) setCreateKind(null); }}>
      <form className={styles.createForm} onSubmit={(event) => { event.preventDefault(); create(); }}>
        {readStatus}
        {createMutation.error ? <p role="alert">{createMutation.error}</p> : null}
        <fieldset disabled={createMutation.pending} className={styles.editorFields}>
        <label>Title<input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>{createKind === 'decision' ? 'Rationale' : createKind === 'note' ? 'Body' : createKind === 'brief' ? 'Problem or opportunity' : 'Summary'}<textarea value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        {createKind === 'decision' ? <label>Architect<select value={architectId} onChange={(event) => setArchitectId(event.target.value)}><option value="">Choose an Architect…</option>{architects.map((agent) => <option key={agent.id} value={agent.id}>{text(agent.name, agent.id)}</option>)}</select></label> : null}
        <footer><Button tone="quiet" onPress={() => setCreateKind(null)}>Cancel</Button><Button tone="primary" type="submit" isDisabled={createMutation.pending || !title.trim() || (createKind === 'brief' && !description.trim()) || (createKind === 'decision' && (!architectId || !description.trim()))}>Create</Button></footer></fieldset>
      </form>
    </ModalDialog>
    {selected?.kind === 'initiative' ? <InitiativeEditor readStatus={readStatus} key={text(selected.item.id)} item={records(planning.initiatives).find((item) => item.id === selected.item.id) ?? selected.item} tasks={taskItems} decisions={decisions.filter((item) => !item.archived)} onClose={() => setSelected(null)} /> : null}
    {selected?.kind === 'area' ? <AreaEditor readStatus={readStatus} key={text(selected.item.id)} item={records(planning.areas).find((item) => item.id === selected.item.id) ?? selected.item} targets={{ task: taskItems, decision: decisions, initiative: Object.values(initiatives).flat(), area: areas }} onClose={() => setSelected(null)} /> : null}
    {selected?.kind === 'note' || selected?.kind === 'brief' ? <ThinkingEditor readStatus={readStatus} key={text(selected.item.id)} kind={selected.kind} item={records(selected.kind === 'note' ? planning.scratchpadNotes : planning.ideaBriefs).find((item) => item.id === selected.item.id) ?? selected.item} notes={notes} onClose={() => setSelected(null)} /> : null}
    {selected?.kind === 'decision' ? <DecisionEditor readStatus={readStatus} key={text(selected.item.id)} item={decisions.find((item) => item.id === selected.item.id) ?? selected.item} tasks={taskItems} engineers={engineers} decisions={decisions} onClose={() => setSelected(null)} /> : null}
  </section>;
}
