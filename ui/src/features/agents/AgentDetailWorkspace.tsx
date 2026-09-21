import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { Button, StateSurface } from '../../design/primitives';
import type { CommandSender } from '../board/BoardPanel';
import type { AgentViewModel } from './model';
import styles from './AgentWorkspace.module.css';

type AgentTab = 'decisions' | 'journal' | 'messages' | 'events' | 'queued' | 'worklog' | 'mcp' | 'history' | 'class' | 'chat';
type RemoteSection = 'events' | 'journal' | 'mcp' | 'history' | 'class';

const PAGE_SIZE = 20;
const INITIAL_LIMITS: Record<RemoteSection, number> = { events: PAGE_SIZE, journal: PAGE_SIZE, mcp: PAGE_SIZE, history: PAGE_SIZE, class: PAGE_SIZE };
const REMOTE_MAX: Record<RemoteSection, number> = { events: 200, journal: 200, mcp: 500, history: 1_000, class: 500 };

interface CatalogBundle { agentClasses: unknown }
interface Props {
  agent: AgentViewModel;
  group: string;
  catalog: CatalogBundle;
  responses: Record<string, unknown>;
  tasks: Record<string, unknown>;
  directMessages: unknown;
  peerThreads: unknown;
  digestSettings: unknown;
  digestBufferStats: unknown;
  digestSentEvents: unknown;
  sendCommand: CommandSender;
  onUnavailable: () => void;
}
interface ClassOption { id: string; label: string; kind: string; raw: Record<string, unknown> }

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function list(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.map(record).filter((item) => Object.keys(item).length);
  return Object.values(record(value)).map(record).filter((item) => Object.keys(item).length);
}
function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}
function bool(value: unknown): boolean { return value === true || value === 1 || value === 'true' || value === 'success'; }
function json(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value, null, 2); } catch { return '[unserializable value]'; }
}
function responseFor(responses: Record<string, unknown>, type: string, target: string): Record<string, unknown> {
  return record(responses[`${type}:${target}`]);
}
function timestamp(value: unknown): string {
  const numeric = Number(value ?? 0);
  if (!numeric) return '—';
  const date = new Date(numeric < 1_000_000_000_000 ? numeric * 1_000 : numeric);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}
function itemTimestamp(item: Record<string, unknown>): number {
  return Number(item.timestamp ?? item.updated_at ?? item.created_at ?? item.lane_entered_at ?? item.started_at ?? 0) || 0;
}
function firstLine(value: unknown): string {
  return text(value).trim().split(/\r\n|\r|\n/, 1)[0] ?? '';
}
function classOptions(value: unknown): ClassOption[] {
  const entries = Array.isArray(value) ? value.map((item, index) => [String(index), item] as const) : Object.entries(record(value));
  return entries.map(([fallback, rawValue]) => {
    const raw = record(rawValue);
    return {
      id: text(raw.id, text(raw.slug, fallback)),
      label: text(raw.display_name, text(raw.name, text(raw.title, text(raw.id, fallback)))),
      kind: text(raw.base_kind, text(raw.kind)),
      raw,
    };
  }).filter((item) => item.id);
}
function tabsFor(kind: string): Array<{ id: AgentTab; label: string }> {
  const inspection: Array<{ id: AgentTab; label: string }> = [
    { id: 'mcp', label: 'MCP' }, { id: 'history', label: 'History' }, { id: 'class', label: 'Agent Class' },
  ];
  if (kind === 'architect') return [
    { id: 'decisions', label: 'Decisions' }, { id: 'journal', label: 'Journal' },
    { id: 'messages', label: 'Messages' }, { id: 'events', label: 'Events' }, ...inspection,
    { id: 'chat', label: 'Peer chat' },
  ];
  if (kind === 'engineer') return [
    { id: 'journal', label: 'Journal' }, { id: 'events', label: 'Events' },
    { id: 'queued', label: 'Queued' }, { id: 'worklog', label: 'Completed' }, ...inspection,
  ];
  return [{ id: 'events', label: 'Events' }, { id: 'messages', label: 'Messages' }, { id: 'worklog', label: 'Worklog' }, ...inspection];
}

function Empty({ title, description }: { title: string; description: string }) {
  return <StateSurface title={title} description={description} />;
}

interface ProgressiveProps<T> {
  items: T[];
  sectionKey: string;
  renderItem: (item: T, index: number) => ReactNode;
  requestLimit?: number;
  requestMax?: number;
  onRequestMore?: (limit: number) => void;
}
function ProgressiveItems<T>({ items, sectionKey, renderItem, requestLimit, requestMax, onRequestMore }: ProgressiveProps<T>) {
  const [progress, setProgress] = useState({ sectionKey, visible: PAGE_SIZE });
  const tailRef = useRef<HTMLButtonElement>(null);
  const visible = progress.sectionKey === sectionKey ? progress.visible : PAGE_SIZE;

  const hasLocal = visible < items.length;
  const hasRemote = Boolean(onRequestMore && requestLimit && requestLimit < (requestMax ?? requestLimit) && items.length >= requestLimit);
  const hasMore = hasLocal || hasRemote;
  const loadMore = () => {
    if (hasLocal) {
      setProgress({ sectionKey, visible: Math.min(visible + PAGE_SIZE, items.length) });
    } else if (hasRemote) {
      const next = Math.min((requestLimit ?? PAGE_SIZE) + PAGE_SIZE, requestMax ?? Number.MAX_SAFE_INTEGER);
      setProgress({ sectionKey, visible: next });
      onRequestMore?.(next);
    }
  };
  useEffect(() => {
    const tail = tailRef.current;
    if (!tail || !hasMore || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMore();
    }, { root: tail.closest('[data-activity-scroll]'), rootMargin: '0px 0px 240px 0px' });
    observer.observe(tail);
    return () => observer.disconnect();
  });
  return <>
    {items.slice(0, visible).map(renderItem)}
    {hasMore ? <button ref={tailRef} type="button" className={styles.activityLoadMore} onClick={loadMore} aria-label={`Load more ${sectionKey}`}>Load more</button> : null}
  </>;
}

function FeedItem({ title, time, badge, preview, children, footer }: { title: string; time?: unknown; badge?: string; preview?: string; children: ReactNode; footer?: ReactNode }) {
  return <details className={styles.collapsibleItem}>
    <summary><strong>{title}</strong>{badge ? <span>{badge}</span> : <time>{timestamp(time)}</time>}{preview ? <p className={styles.collapsedPreview}>{firstLine(preview)}</p> : null}</summary>
    <div className={styles.collapsibleBody}>{children}{footer ? <footer>{footer}</footer> : null}</div>
  </details>;
}

function TaskRows({ rows, empty, note, sectionKey }: { rows: Record<string, unknown>[]; empty: string; note: string; sectionKey: string }) {
  return <section className={styles.agentTaskPanel}>
    <header><div><h3>{note}</h3><p>{rows.length} {rows.length === 1 ? 'task' : 'tasks'}</p></div></header>
    {rows.length ? <div><ProgressiveItems items={rows} sectionKey={sectionKey} renderItem={(task, index) => <FeedItem key={text(task.id, String(index))} title={text(task.task, text(task.title, text(task.id, 'Task')))} badge={text(task.lane, text(task.status, 'Not on board'))}><small>{text(task.id)}</small><p>{text(task.result, text(task.summary, text(task.status)))}</p><time>{timestamp(itemTimestamp(task))}</time></FeedItem>} /></div> : <Empty title={empty} description="Assignments and completed work appear here as Torque records them." />}
  </section>;
}

function DigestPanel({ agent, queued, sent, paused, bufferedCount, onSend, onTogglePause }: { agent: AgentViewModel; queued: Record<string, unknown>[]; sent: Record<string, unknown>[]; paused: boolean; bufferedCount: number; onSend: () => void; onTogglePause: () => void }) {
  const renderDigestItem = (mode: 'queued' | 'sent') => (event: Record<string, unknown>, index: number) => {
    const body = text(event.message, text(event.summary, text(event.content, 'Digest event')));
    return <FeedItem key={text(event.id, `${mode}-${index}`)} title={text(event.kind, text(event.event, text(event.type, 'Digest event'))).replaceAll('_', ' ')} time={event.delivered_at ?? event.timestamp ?? event.created_at} preview={body} footer={<><span>{text(event.source_agent_name, text(event.source, text(event.task_id)))}</span><span>{text(event.delivery_state, mode)}</span></>}><p>{body}</p></FeedItem>;
  };
  return <section className={styles.digestPanel} aria-label={`Digest delivery for ${agent.name}`}>
    <header><div><h3>{agent.kind === 'architect' ? 'Architect digest' : 'Engineer digest'}</h3><p>{paused ? 'Delivery paused' : `${bufferedCount || queued.length} queued for delivery`}</p></div><Button tone="quiet" onPress={onSend} isDisabled={paused || (!bufferedCount && !queued.length)}>Send digest now</Button><Button tone="quiet" onPress={onTogglePause}>{paused ? 'Resume' : 'Pause'}</Button></header>
    <div className={styles.digestColumns}>
      <section><h4>Queued <span>{bufferedCount || queued.length}</span></h4>{queued.length ? <ProgressiveItems items={queued} sectionKey={`${agent.id}-digest-queued`} renderItem={renderDigestItem('queued')} /> : <p>No queued digest events.</p>}</section>
      <section><h4>Sent <span>{sent.length}</span></h4>{sent.length ? <ProgressiveItems items={sent} sectionKey={`${agent.id}-digest-sent`} renderItem={renderDigestItem('sent')} /> : <p>No digests sent yet.</p>}</section>
    </div>
  </section>;
}

export function AgentDetailWorkspace({ agent, group, catalog, responses, tasks, directMessages, peerThreads, digestSettings: rawDigestSettings, digestBufferStats: rawDigestBufferStats, digestSentEvents: rawDigestSentEvents, sendCommand, onUnavailable }: Props) {
  const tabs = useMemo(() => tabsFor(agent.kind), [agent.kind]);
  const [tab, setTab] = useState<AgentTab>(tabs[0]?.id ?? 'events');
  const [toolFilter, setToolFilter] = useState('');
  const [outcome, setOutcome] = useState('all');
  const [range, setRange] = useState('24h');
  const [selectedClassId, setSelectedClassId] = useState(text(agent.raw.agent_class_id));
  const [peerSelection, setPeerSelection] = useState({ agentId: agent.id, threadId: '' });
  const [engineerReply, setEngineerReply] = useState('');
  const [archiveView, setArchiveView] = useState({ agentId: agent.id, visible: false });
  const [limitState, setLimitState] = useState({ agentId: agent.id, values: INITIAL_LIMITS });
  const [mcpAnchor] = useState(() => Date.now() / 1_000);
  const showArchived = archiveView.agentId === agent.id && archiveView.visible;
  const limits = limitState.agentId === agent.id ? limitState.values : INITIAL_LIMITS;

  const run = (command: Record<string, unknown>) => {
    if (!sendCommand(command as { cmd: string })) onUnavailable();
  };
  const mcpCommand = (limit: number) => {
    const since = range === '1h' ? mcpAnchor - 3_600 : range === '6h' ? mcpAnchor - 21_600 : range === '24h' ? mcpAnchor - 86_400 : undefined;
    return { cmd: 'mcp_calls', cell_id: agent.id, tool_name_pattern: toolFilter.trim() ? `*${toolFilter.trim()}*` : 'mcp__torque__%', hook_event_name: 'PostToolUse', success_filter: outcome, limit, ...(since ? { since } : {}) };
  };
  const requestSection = (section: RemoteSection, limit: number) => {
    setLimitState({ agentId: agent.id, values: { ...limits, [section]: limit } });
    if (section === 'events') run({ cmd: 'get_cell_events', cell_id: agent.id, limit });
    if (section === 'mcp') run(mcpCommand(limit));
    if (section === 'history') run({ cmd: 'get_agent_history_detail', agent_id: agent.id, message_limit: limit });
    if (section === 'class') run({ cmd: 'agent_class_audit', agent_id: agent.id, limit });
    if (section === 'journal' && agent.kind === 'architect') run({ cmd: 'architect_journal_read', architect_id: agent.id, limit });
    if (section === 'journal' && agent.kind === 'engineer') run({ cmd: 'engineer_journal_snapshot', group, engineer_id: agent.id, include_streams: true, limit });
  };
  const refresh = (requested = limits) => {
    run({ cmd: 'get_cell_events', cell_id: agent.id, limit: requested.events });
    run(mcpCommand(requested.mcp));
    run({ cmd: 'agent_class_list' });
    run({ cmd: 'agent_class_status', agent_id: agent.id });
    run({ cmd: 'agent_class_audit', agent_id: agent.id, limit: requested.class });
    run({ cmd: 'get_agent_history_detail', agent_id: agent.id, message_limit: requested.history });
    if (agent.kind === 'architect') {
      run({ cmd: 'architect_journal_read', architect_id: agent.id, limit: requested.journal });
      run({ cmd: 'decisions_snapshot', include_archived: true });
      run({ cmd: 'architect_peer_inbox', architect_id: agent.id, detail: true, limit: 100 });
    } else if (agent.kind === 'engineer') {
      run({ cmd: 'engineer_journal_snapshot', group, engineer_id: agent.id, include_streams: true, limit: requested.journal });
      run({ cmd: 'engineer_session_map_read', group, engineer_id: agent.id });
      run({ cmd: 'get_group_settings', group });
    }
  };
  const updateDecision = (id: string, updates: Record<string, unknown>) => {
    run({ cmd: 'architect_decision_update', architect_id: agent.id, id, ...updates });
    run({ cmd: 'decisions_snapshot', include_archived: true });
  };
  useEffect(() => {
    refresh(INITIAL_LIMITS);
    // Requests are selected-agent scoped and reset when that selection changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id, group]);

  const events = list(record(responses[`cell_events:${agent.id}`]).events);
  const calls = list(responseFor(responses, 'mcp_calls', agent.id).calls ?? responseFor(responses, 'mcp_calls', agent.id).events);
  const historyFrame = responseFor(responses, 'agent_history_detail', agent.id);
  const historyRecord = record(historyFrame.record);
  const historyTasks = list(historyFrame.tasks);
  const historyMessages = list(historyFrame.messages);
  const classFrame = responseFor(responses, 'agent_class_status', agent.id);
  const assignmentFrame = responseFor(responses, 'agent_class_assignment', agent.id);
  const classStatus = record(assignmentFrame.status ?? classFrame.status ?? agent.raw.agent_class_status);
  const classAuditFrame = responseFor(responses, 'agent_class_audit', agent.id);
  const classAudit = list((Object.keys(classAuditFrame).length ? classAuditFrame : record(responses['agent_class_audit:latest'])).events);
  const classes = useMemo(() => classOptions(catalog.agentClasses).filter((item) => !item.kind || item.kind === agent.kind), [agent.kind, catalog.agentClasses]);
  const selectedClass = classes.find((item) => item.id === selectedClassId)?.raw ?? {};
  const architectJournal = list(record(responses[`architect_journal_entries:${agent.id}`]).entries);
  const engineerFrame = record(responses[`engineer_journal_snapshot:${group}`]);
  const engineerJournal = list(record(engineerFrame.engineer_journal)[agent.id]);
  const engineerWorklog = list(record(engineerFrame.engineer_worklog)[group]).filter((entry) => !text(entry.engineer_id) || text(entry.engineer_id) === agent.id);
  const engineerSettings = record(responseFor(responses, 'group_settings', group).engineer_settings);
  const sessionMap = record(responseFor(responses, 'engineer_session_map', group).session_map);
  const journal = agent.kind === 'architect' ? architectJournal : engineerJournal;
  const decisions = list(record(responses['decisions_snapshot:_'] ?? responses['decisions_snapshot:latest']).decisions)
    .filter((decision) => text(decision.architect_id) === agent.id).sort((a, b) => itemTimestamp(b) - itemTimestamp(a));
  const archivedCount = decisions.filter((decision) => decision.archived === true).length;
  const visibleDecisions = decisions.filter((decision) => showArchived || decision.archived !== true);
  const allTasks: Record<string, unknown>[] = Object.entries(tasks).map(([id, value]) => ({ id, ...record(value) }));
  const ownedTasks = allTasks.filter((task) => agent.kind === 'worker' ? text(task.agent_id) === agent.id : text(task.assigned_engineer_id) === agent.id).sort((a, b) => itemTimestamp(b) - itemTimestamp(a));
  const queuedTasks = ownedTasks.filter((task) => ['Backlog', 'To Do', 'In Progress'].includes(text(task.lane)));
  const completedTasks = ownedTasks.filter((task) => text(task.lane) === 'Done' || ['complete', 'completed', 'merged'].includes(text(task.status).toLowerCase()));
  const workerMessages = ownedTasks.flatMap((task) => list(task.messages_thread).filter((message) => !text(message.recipient_agent_id) || text(message.recipient_agent_id) === agent.id).map((message) => ({ ...message, task_id: task.id })));
  const snapshotPeerThreads = list(peerThreads).filter((thread) => {
    const participantIds = Array.isArray(thread.participant_ids) ? thread.participant_ids.map(String) : [];
    return participantIds.includes(agent.id) || list(thread.messages).some((message) => [message.sender_id, message.recipient_id].some((value) => text(value) === agent.id));
  });
  const peerInboxFrame = record(responses[`architect_peer_inbox:${agent.id}`] ?? responses['architect_peer_inbox:_'] ?? responses['architect_peer_inbox:latest']);
  const inboxPeerThreads = list(peerInboxFrame.threads);
  const architectPeerThreads = (inboxPeerThreads.length ? inboxPeerThreads : snapshotPeerThreads)
    .sort((a, b) => Number(b.last_message_at ?? b.last_activity_at ?? 0) - Number(a.last_message_at ?? a.last_activity_at ?? 0));
  const selectedPeerThreadId = peerSelection.agentId === agent.id && architectPeerThreads.some((thread) => text(thread.thread_id, text(thread.id)) === peerSelection.threadId)
    ? peerSelection.threadId
    : text(architectPeerThreads[0]?.thread_id, text(architectPeerThreads[0]?.id));
  const selectedPeerThread = architectPeerThreads.find((thread) => text(thread.thread_id, text(thread.id)) === selectedPeerThreadId) ?? {};
  const selectedPeerMessages = list(selectedPeerThread.messages);
  const architectMessages = [...list(agent.raw.mcp_messages), ...architectPeerThreads.flatMap((thread) => list(thread.messages))];
  const messages = (agent.kind === 'worker' ? workerMessages : agent.kind === 'architect' ? architectMessages : list(directMessages)).sort((a, b) => itemTimestamp(b) - itemTimestamp(a));
  const digestSettings = record(rawDigestSettings);
  const digestBufferStats = record(rawDigestBufferStats);
  const digestQueuedEvents = list(digestBufferStats.queued_events ?? digestBufferStats.events ?? digestBufferStats.buffered_event_list);
  const digestSentEvents = list(rawDigestSentEvents).sort((a, b) => Number(b.delivered_at ?? b.timestamp ?? 0) - Number(a.delivered_at ?? a.timestamp ?? 0));
  const digestPaused = bool(digestSettings.paused) || (agent.kind === 'engineer' && engineerSettings.paused === true);
  const progressive = (section: RemoteSection) => ({ requestLimit: limits[section], requestMax: REMOTE_MAX[section], onRequestMore: (limit: number) => requestSection(section, limit) });

  return <section className={styles.agentDetailWorkspace} aria-label={`Activity for ${agent.name}`}>
    <header className={styles.agentDetailHeader}><div><span>Selected agent</span><h2>{agent.name}</h2><p>{agent.kind}{agent.role ? ` · ${agent.role}` : ''} · {agent.status}</p></div><div><Button tone="quiet" onPress={() => refresh()}>Refresh</Button></div></header>
    <nav className={styles.agentDetailTabs} role="tablist" aria-label={`${agent.kind} details`}>{tabs.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} onClick={() => setTab(item.id)}>{item.label}</button>)}</nav>
    <div className={styles.agentDetailContent} data-activity-scroll>
      {tab === 'events' ? <section className={styles.agentFeed} aria-label="Agent events">{['architect', 'engineer'].includes(agent.kind) ? <DigestPanel agent={agent} queued={digestQueuedEvents} sent={digestSentEvents} paused={digestPaused} bufferedCount={Number(digestBufferStats.buffered_events ?? digestBufferStats.buffered_count ?? 0) || 0} onSend={() => run({ cmd: 'engineer_flush_now', agent_id: agent.id })} onTogglePause={() => run({ cmd: digestPaused ? 'digest_resume' : 'digest_pause', agent_id: agent.id })} /> : null}<header className={styles.feedSectionHeader}><h3>Lifecycle events</h3><span>{events.length}</span></header>{events.length ? <ProgressiveItems items={events} sectionKey={`${agent.id}-events`} {...progressive('events')} renderItem={(event, index) => <FeedItem key={text(event.id, `${text(event.timestamp)}-${index}`)} title={text(event.kind, 'event').replaceAll('_', ' ')} time={event.timestamp} footer={<><span>{text(event.task_id)}</span><span>{text(event.source, 'runtime')}</span></>}><p>{text(event.message, text(event.summary, 'Runtime event'))}</p></FeedItem>} /> : <Empty title="No events yet" description="Lifecycle, dispatch, and runtime events for this agent appear here." />}</section> : null}

      {tab === 'journal' ? <section className={styles.agentFeed} aria-label={`${agent.kind} journal`}>
        {agent.kind === 'engineer' && text(engineerSettings.pending_question) ? <form className={styles.pendingEngineer} onSubmit={(event) => { event.preventDefault(); run({ cmd: 'engineer_reply', group, answer: engineerReply }); setEngineerReply(''); }}><strong>Engineer asks</strong><p>{text(engineerSettings.pending_question)}</p><textarea value={engineerReply} onChange={(event) => setEngineerReply(event.target.value)} placeholder="Answer and resume delivery" /><footer><Button tone="quiet" onPress={() => run({ cmd: 'engineer_resume', group, engineer_id: agent.id })}>Dismiss</Button><Button tone="primary" type="submit" isDisabled={!engineerReply.trim()}>Reply</Button></footer></form> : null}
        {agent.kind === 'engineer' && text(engineerSettings.pending_note) ? <article className={styles.pendingEngineer}><strong>{text(engineerSettings.pending_note_kind, 'Engineer note')}</strong><p>{text(engineerSettings.pending_note)}</p><footer><Button tone="quiet" onPress={() => run({ cmd: 'engineer_dismiss_note', group, engineer_id: agent.id })}>Dismiss note</Button></footer></article> : null}
        {journal.length ? <ProgressiveItems items={journal} sectionKey={`${agent.id}-journal`} {...progressive('journal')} renderItem={(entry, index) => {
          const body = text(entry.summary, text(entry.entry, text(entry.message, text(entry.content, text(entry.note)))));
          return <FeedItem key={text(entry.id, String(index))} title={text(entry.title, text(entry.type, 'Journal entry'))} time={entry.created_at ?? entry.timestamp} preview={body} footer={<><span>{text(entry.task_id)}</span>{agent.kind === 'engineer' && text(entry.id) ? <Button tone="quiet" onPress={() => run({ cmd: 'engineer_journal_delete', group, entry_id: text(entry.id), author_cell_id: agent.id })}>Delete</Button> : <span>{text(entry.status)}</span>}</>}><p>{body}</p></FeedItem>;
        }} /> : <Empty title="No journal entries" description={`No ${agent.kind} journal entries have been recorded yet.`} />}
        {agent.kind === 'engineer' && engineerWorklog.length ? <div className={styles.agentWorklogSummary}><strong>Delivery worklog · {engineerWorklog.length}</strong><p>Open Completed to inspect recorded delivery outcomes.</p></div> : null}
        {agent.kind === 'engineer' && Object.keys(sessionMap).length ? <details className={styles.sessionMap}><summary>Session map</summary><pre>{json(sessionMap)}</pre></details> : null}
      </section> : null}

      {tab === 'decisions' ? <section className={styles.agentDecisionPanel}><header><div><h3>Decision log</h3><p>{decisions.length - archivedCount} active · {archivedCount} archived</p></div>{archivedCount ? <button type="button" className={styles.archiveToggle} aria-pressed={showArchived} onClick={() => setArchiveView({ agentId: agent.id, visible: !showArchived })}>{showArchived ? 'Hide archived' : `Show archived (${archivedCount})`}</button> : null}</header>{visibleDecisions.length ? <ProgressiveItems items={visibleDecisions} sectionKey={`${agent.id}-decisions-${showArchived}`} renderItem={(decision, index) => {
        const id = text(decision.id, String(index));
        const status = text(decision.status, 'proposed');
        const archived = decision.archived === true;
        return <details key={id} className={`${styles.collapsibleItem} ${styles.decisionItem}`} data-archived={archived}><summary><strong>{text(decision.title, 'Decision')}</strong><span>{archived ? 'archived' : status}</span></summary><div className={styles.collapsibleBody}><p>{text(decision.rationale, text(decision.summary, text(decision.decision)))}</p><footer><span>{id}</span><time>{timestamp(decision.updated_at ?? decision.created_at ?? decision.timestamp)}</time></footer><div className={styles.decisionActions}>{!archived && status !== 'accepted' ? <Button tone="primary" onPress={() => updateDecision(id, { status: 'accepted' })}>Accept</Button> : null}{!archived && status !== 'revised' ? <Button tone="quiet" onPress={() => updateDecision(id, { status: 'revised' })}>Mark revised</Button> : null}{!archived && status !== 'rejected' ? <Button tone="quiet" onPress={() => updateDecision(id, { status: 'rejected' })}>Reject</Button> : null}<Button tone="quiet" onPress={() => updateDecision(id, { archived: !archived })}>{archived ? 'Restore' : 'Archive'}</Button></div></div></details>;
      }} /> : <Empty title={showArchived ? 'No decisions yet' : 'No active decisions'} description={showArchived ? 'Decisions filed by this Architect appear here with their current status and rationale.' : 'Archived decisions remain available from the archived filter.'} />}</section> : null}

      {tab === 'messages' ? <section className={styles.agentFeed} aria-label="Agent messages">{messages.length ? <ProgressiveItems items={messages} sectionKey={`${agent.id}-messages`} renderItem={(message, index) => {
        const body = text(message.message, text(message.content, text(message.text)));
        return <FeedItem key={text(message.id, String(index))} title={text(message.action, text(message.sender_kind, text(message.role, 'message'))).replaceAll('_', ' ')} time={message.timestamp ?? message.created_at} preview={body} footer={<><span>{text(message.task_id)}</span><span>{text(message.direction)}</span></>}><p>{body}</p></FeedItem>;
      }} /> : <Empty title="No messages yet" description={agent.kind === 'worker' ? 'Inline Engineer messages attached to this Worker’s tasks appear here.' : 'Agent coordination messages appear here.'} />}</section> : null}
      {tab === 'queued' ? <TaskRows rows={queuedTasks} empty="No queued tasks" note="Queued tasks" sectionKey={`${agent.id}-queued`} /> : null}
      {tab === 'worklog' ? <TaskRows rows={agent.kind === 'engineer' ? completedTasks : ownedTasks} empty={agent.kind === 'engineer' ? 'No completed tasks' : 'No task history'} note={agent.kind === 'engineer' ? 'Completed work' : 'Task history'} sectionKey={`${agent.id}-worklog`} /> : null}

      {tab === 'mcp' ? <section className={styles.inspectorMcp} aria-label="MCP activity"><form onSubmit={(event) => { event.preventDefault(); requestSection('mcp', PAGE_SIZE); }}><label>Tool contains<input value={toolFilter} onChange={(event) => setToolFilter(event.target.value)} placeholder="task_progress" /></label><label>Outcome<select value={outcome} onChange={(event) => setOutcome(event.target.value)}><option value="all">All</option><option value="success">Success</option><option value="error">Error</option></select></label><label>Range<select value={range} onChange={(event) => setRange(event.target.value)}><option value="1h">1 hour</option><option value="6h">6 hours</option><option value="24h">24 hours</option><option value="all">All retained</option></select></label><Button type="submit">Apply</Button></form><div className={styles.inspectorFeed}>{calls.length ? <ProgressiveItems items={calls} sectionKey={`${agent.id}-mcp-${toolFilter}-${outcome}-${range}`} {...progressive('mcp')} renderItem={(call, index) => <details key={text(call.cursor, text(call.idempotency_key, String(index)))}><summary><span className={bool(call.success) ? styles.callSuccess : styles.callError}>{bool(call.success) ? '✓' : '!'}</span><strong>{text(call.tool_name, 'MCP call')}</strong><time>{timestamp(call.appended_at ?? call.timestamp)}</time></summary><dl><div><dt>Duration</dt><dd>{text(call.duration_ms, '—')} ms</dd></div><div><dt>Hook</dt><dd>{text(call.hook_event_name, '—')}</dd></div><div><dt>Session</dt><dd>{text(call.session_id, '—')}</dd></div></dl><h4>Arguments</h4><pre>{json(call.args ?? call.arguments ?? call.args_summary)}</pre><h4>Result</h4><pre>{json(call.result ?? call.result_summary ?? call.error)}</pre></details>} /> : <Empty title="No matching MCP calls" description="Adjust the tool, outcome, or time filters and apply again." />}</div></section> : null}

      {tab === 'history' ? <section className={styles.inspectorHistory} aria-label="Agent history">{Object.keys(historyRecord).length ? <><dl className={styles.inspectorFacts}><div><dt>Status</dt><dd>{text(historyRecord.status, agent.status)}</dd></div><div><dt>Started</dt><dd>{timestamp(historyRecord.created_at ?? historyRecord.started_at)}</dd></div><div><dt>Finished</dt><dd>{timestamp(historyRecord.removed_at ?? historyRecord.completed_at)}</dd></div><div><dt>Provider</dt><dd>{text(historyRecord.provider, agent.provider || '—')}</dd></div><div><dt>Model</dt><dd>{text(historyRecord.model, '—')}</dd></div><div><dt>Tokens</dt><dd>{text(historyRecord.total_tokens ?? historyRecord.token_count, '—')}</dd></div><div><dt>Branch</dt><dd>{text(historyRecord.worktree_branch ?? historyRecord.branch, agent.worktreeBranch || '—')}</dd></div><div><dt>Outcome</dt><dd>{text(historyRecord.outcome, '—')}</dd></div></dl><div className={styles.historyColumns}><section><h3>Tasks <span>{historyTasks.length}</span></h3>{historyTasks.length ? <ProgressiveItems items={historyTasks} sectionKey={`${agent.id}-history-tasks`} renderItem={(task, index) => <FeedItem key={text(task.task_id, text(task.id, String(index)))} title={text(task.task, text(task.title, text(task.task_id, 'Task')))} badge={text(task.lane, text(task.status))}><p>{text(task.result, text(task.summary))}</p></FeedItem>} /> : <p>No recorded tasks.</p>}</section><section><h3>Messages <span>{historyMessages.length}</span></h3>{historyMessages.length ? <ProgressiveItems items={historyMessages} sectionKey={`${agent.id}-history-messages`} {...progressive('history')} renderItem={(message, index) => {
        const body = text(message.message, text(message.content, text(message.text)));
        return <FeedItem key={text(message.id, String(index))} title={text(message.action, text(message.role, 'message'))} time={message.created_at ?? message.timestamp} preview={body}><p>{body}</p></FeedItem>;
      }} /> : <p>No recorded messages.</p>}</section></div></> : <Empty title="Loading agent history" description="Torque is retrieving persisted tasks, messages, and lifecycle metadata." />}</section> : null}

      {tab === 'class' ? <section className={styles.classInspector} aria-label="Agent Class assignment"><div className={styles.classAssignment}><label>Desired class for next launch<select value={selectedClassId} onChange={(event) => setSelectedClassId(event.target.value)}><option value="">Default for {agent.kind}</option>{classes.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label><Button tone="primary" onPress={() => run({ cmd: selectedClassId ? 'agent_class_assign' : 'agent_class_clear', agent_id: agent.id, ...(selectedClassId ? { class_id: selectedClassId } : {}), actor_label: 'trusted-user-react-ui' })}>Save assignment</Button>{bool(classStatus.pending_next_launch) ? <Button onPress={() => run({ cmd: 'relaunch_agent', id: agent.id })}>Relaunch to apply</Button> : null}</div><dl className={styles.inspectorFacts}><div><dt>Effective now</dt><dd>{text(classStatus.effective_primary_identity_label, text(classStatus.effective_class_id, 'Default'))}</dd></div><div><dt>Desired</dt><dd>{text(classStatus.next_launch_primary_identity_label, text(classStatus.next_launch_class_id, 'Default'))}</dd></div><div><dt>Effective version</dt><dd>{text(classStatus.effective_class_version, '—')}</dd></div><div><dt>Next version</dt><dd>{text(classStatus.next_launch_class_version, '—')}</dd></div><div><dt>Assigned by</dt><dd>{text(classStatus.assigned_by, '—')}</dd></div><div><dt>Apply state</dt><dd>{bool(classStatus.pending_next_launch) ? 'Pending relaunch' : 'Current'}</dd></div></dl>{selectedClassId ? <section className={styles.classPreview}><h3>{text(selectedClass.display_name, text(selectedClass.name, selectedClassId))}</h3><p>{text(selectedClass.description, 'No description provided.')}</p><pre>{json(selectedClass)}</pre></section> : null}<section className={styles.classAudit}><h3>Assignment audit <span>{classAudit.length}</span></h3>{classAudit.length ? <ProgressiveItems items={classAudit} sectionKey={`${agent.id}-class-audit`} {...progressive('class')} renderItem={(event, index) => <FeedItem key={text(event.id, String(index))} title={text(event.event, 'class event').replaceAll('_', ' ')} time={event.created_at}><p>{text(event.message)}</p><span>{text(event.actor_label, text(event.actor_kind))}</span></FeedItem>} /> : <p>No assignment changes recorded.</p>}</section></section> : null}

      {tab === 'chat' ? <section className={styles.peerChat} aria-label="Architect peer chat">
        <aside className={styles.peerThreadList}><header><h3>Peer threads</h3><span>{architectPeerThreads.length}</span></header>{architectPeerThreads.length ? <ProgressiveItems items={architectPeerThreads} sectionKey={`${agent.id}-peer-threads`} renderItem={(thread, index) => {
          const id = text(thread.thread_id, text(thread.id, String(index)));
          const threadMessages = list(thread.messages);
          const lastMessage = record(thread.last_message);
          const preview = text(lastMessage.message, text(lastMessage.content, text(threadMessages.at(-1)?.message, text(threadMessages.at(-1)?.content))));
          return <button key={id} type="button" className={id === selectedPeerThreadId ? styles.peerThreadSelected : ''} aria-pressed={id === selectedPeerThreadId} onClick={() => setPeerSelection({ agentId: agent.id, threadId: id })}><span><strong>{text(thread.title, text(thread.peer_name, 'Peer thread'))}</strong><time>{timestamp(thread.last_message_at ?? thread.last_activity_at)}</time></span>{preview ? <p>{firstLine(preview)}</p> : null}<small>{Number(thread.message_count ?? threadMessages.length)} messages</small></button>;
        }} /> : <Empty title="No peer threads" description="Architect-to-Architect coordination threads appear here when agents exchange messages." />}</aside>
        <section className={styles.peerThreadDetail}>{selectedPeerThreadId ? <><header><div><h3>{text(selectedPeerThread.title, text(selectedPeerThread.peer_name, 'Peer thread'))}</h3><p>{Number(selectedPeerThread.message_count ?? selectedPeerMessages.length)} messages</p></div><time>{timestamp(selectedPeerThread.last_message_at ?? selectedPeerThread.last_activity_at)}</time></header><div className={styles.peerThreadMessages}>{selectedPeerMessages.length ? <ProgressiveItems items={selectedPeerMessages} sectionKey={`${agent.id}-${selectedPeerThreadId}-messages`} renderItem={(message, index) => {
          const senderId = text(message.sender_architect_id, text(message.sender_id));
          return <article key={text(message.id, `${selectedPeerThreadId}-${index}`)} data-direction={senderId === agent.id ? 'outgoing' : 'incoming'}><header><strong>{text(message.sender_name, senderId === agent.id ? agent.name : 'Architect')}</strong><time>{timestamp(message.created_at ?? message.timestamp)}</time></header><p>{text(message.message, text(message.content))}</p>{text(message.context_summary) ? <small>{text(message.context_summary)}</small> : null}</article>;
        }} /> : <Empty title="No messages in this thread" description="This thread has no readable messages yet." />}</div></> : <Empty title="Select a peer thread" description="Choose a thread to read its complete message history." />}</section>
      </section> : null}
    </div>
  </section>;
}
