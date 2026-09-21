import { AskResponse } from '../attention/AskResponse';
import { isOpenAsk } from '../attention/model';
import { HealthDetails, SupervisorDetails } from './OperationalDetails';
import { useAppSelector } from '../../app/hooks';
import { selectRuntime, selectAgentsState, selectTasksState } from '../../app/store';
import { useMemo, useState } from 'react';

import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { text } from '../planning/model';
import styles from './ControlCenter.module.css';

function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
function list(value: unknown): UnknownRecord[] { return Array.isArray(value) ? value.map(record) : Object.values(record(value)).map(record); }
function chips(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function time(value: unknown): string { const n = Number(value ?? 0); if (!n) return '—'; const d = new Date(n < 1e12 ? n * 1000 : n); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString(); }
function label(value: unknown, fallback = 'Untitled'): string { const item = record(value); return text(item.title, text(item.name, text(item.message, text(item.id, fallback)))); }

export function MissionPanel({ group, agentCount, mission, health, supervisor, relay, send, onOpenTask, onOpenAgent }: {
  agentCount: number;
  group: string; mission: UnknownRecord; health: UnknownRecord; supervisor: UnknownRecord; relay: UnknownRecord; responses: Record<string, unknown>;
  send: (command: TorqueCommand) => void; onOpenTask: (id: string) => void; onOpenAgent: (id: string) => void;
}) {
  const [search, setSearch] = useState('');
  const runtime = useAppSelector(selectRuntime);
  const [selected, setSelected] = useState<UnknownRecord | null>(null);
  const [supervisorAction, setSupervisorAction] = useState<null | { kind: 'restart' } | { kind: 'terminate'; sessionId: string; label: string }>(null);
  const sections = record(mission.sections);
  const counts = record(health.counts);
  const sessions = list(supervisor.sessions);
  const query = search.trim().toLocaleLowerCase();
  const sectionDefs = [
    ['needs_operator_now', 'Needs operator now', 'Human gates and decisions waiting for action.'],
    ['at_risk_watchlist', 'At-risk watchlist', 'Risks to inspect before they block work.'],
    ['in_flight', 'In flight', 'Healthy active work.'],
    ['recently_completed', 'Recently completed', 'Recent outcomes for operator confidence.'],
  ] as const;
  const sectionItems = (key: string) => {
    const section = record(sections[key]);
    const rows = list(section.items ?? sections[key]);
    return query ? rows.filter((item) => JSON.stringify(item).toLocaleLowerCase().includes(query)) : rows;
  };
  return <div className={styles.operatorMission}>
    <div className={styles.operatorToolbar}><label>Search Mission Control<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Gate, task, owner, evidence" /></label><Button tone="quiet" onPress={() => send({ cmd: 'get_mission_control', group, limit_per_section: 20, include_recent_completed: true })}>Refresh</Button></div>
    <div className={styles.metrics}><article className={styles.metric}><span>Agents</span><strong>{agentCount}</strong></article><article className={styles.metric} data-tone="warning"><span>Needs attention</span><strong>{text(counts.needs_attention, text(counts.blocked, text(record(sections.needs_operator_now).count, '—')))}</strong></article><article className={styles.metric}><span>Supervisor sessions</span><strong>{text(record(runtime.supervisor).session_count, supervisor.available === true ? String(sessions.length) : '—')}</strong></article><article className={styles.metric}><span>Relay</span><strong>{text(relay.status, '—')}</strong></article></div>
    <div className={styles.sectionGrid}>{sectionDefs.map(([key, title, subtitle]) => { const items = sectionItems(key); return <section key={key}><header><div><h2>{title}</h2><p>{subtitle}</p></div><span>{items.length}</span></header><div>{items.length ? items.map((item, index) => { const itemLabel = label(item); const itemDetail = text(item.reason, text(item.summary, text(item.recommended_next_action, 'No additional detail.'))); return <article key={text(item.id, String(index))} title={itemLabel} className={selected === item ? styles.operatorSelected : ''} onClick={() => setSelected(selected === item ? null : item)}><strong title={itemLabel}>{itemLabel}</strong><p title={itemDetail}>{itemDetail}</p><div className={styles.chips}>{[...chips(item.evidence_chips), ...chips(item.caveat_chips)].map((chip, chipIndex) => { const chipLabel = typeof chip === 'string' ? chip : label(chip); return <span key={chipIndex} title={chipLabel}>{chipLabel}</span>; })}</div><footer><Button tone="quiet" onPress={() => send({ cmd: 'mission_control_dismiss', id: text(item.id), timestamp: Date.now() / 1000 })}>Dismiss</Button>{text(item.primary_task_id) ? <Button tone="primary" onPress={() => onOpenTask(text(item.primary_task_id))}>Open task</Button> : null}{text(record(item.owner).agent_id) ? <Button tone="quiet" onPress={() => onOpenAgent(text(record(item.owner).agent_id))}>Open agent</Button> : null}</footer>{selected === item ? <pre className={styles.json}>{JSON.stringify(item, null, 2)}</pre> : null}</article>; }) : <StateSurface title="Clear" description="No visible items in this section; Torque will surface work here when it needs attention." />}</div></section>; })}</div>
    <HealthDetails group={group} runtime={runtime} />
    <section className={styles.supervisorPanel}><header><h2>PTY supervisor</h2><Button tone="danger" onPress={() => setSupervisorAction({ kind: 'restart' })}>Restart supervisor</Button></header><SupervisorDetails supervisor={supervisor} send={send} onTerminate={(sessionId, label) => setSupervisorAction({ kind: 'terminate', sessionId, label })} /></section>
    <ModalDialog title={supervisorAction?.kind === 'terminate' ? 'Terminate PTY session?' : 'Restart PTY supervisor?'} description={supervisorAction?.kind === 'terminate' ? supervisorAction.label : 'All managed PTY sessions'} size="small" isOpen={supervisorAction !== null} onOpenChange={(open) => { if (!open) setSupervisorAction(null); }}>
      <div className={styles.classConfirm}><p>{supervisorAction?.kind === 'terminate' ? 'This stops the selected live terminal session.' : 'This restarts the supervisor and may interrupt every managed live terminal session.'}</p><footer><Button tone="quiet" onPress={() => setSupervisorAction(null)}>Cancel</Button><Button tone="danger" onPress={() => { if (supervisorAction?.kind === 'terminate') send({ cmd: 'supervisor_session_terminate', session_id: supervisorAction.sessionId }); else if (supervisorAction?.kind === 'restart') send({ cmd: 'supervisor_restart' }); setSupervisorAction(null); }}>{supervisorAction?.kind === 'terminate' ? 'Terminate session' : 'Restart supervisor'}</Button></footer></div>
    </ModalDialog>
  </div>;
}

export function ActivityPanel({ events, send, group = '' }: { events: UnknownRecord[]; group?: string; send: (command: TorqueCommand) => void }) {
  const [search, setSearch] = useState('');
  const [copyStatus, setCopyStatus] = useState('');
  const tasks = useAppSelector(selectTasksState).records;
  const agents = useAppSelector(selectAgentsState).records;
  const asks = Object.values(tasks).map(record).filter((task) => isOpenAsk(task) && (!group || task.group === group));
  const attention = Object.values(agents).map(record).filter((agent) => agent.needs_attention && agent.cell_type === 'agent' && !Number(agent.deleted_at) && !Number(agent.dismissed_at) && (!group || agent.group === group));
  const [kind, setKind] = useState('');
  const [selected, setSelected] = useState('');
  const kinds = useMemo(() => [...new Set(events.map((event) => text(event.kind, text(event.event_type))).filter(Boolean))].sort(), [events]);
  const rows = events.filter((event) => !kind || text(event.kind, text(event.event_type)) === kind).filter((event) => !search.trim() || JSON.stringify(event).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const beforeId = Math.min(...events.map((event) => Number(event.id ?? 0)).filter((value) => value > 0));
  return <div className={styles.activityPanel}><header className={styles.operatorToolbar}><label>Search events<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Message, task, agent" /></label><label>Kind<select value={kind} onChange={(event) => setKind(event.target.value)}><option value="">All</option>{kinds.map((value) => <option key={value}>{value}</option>)}</select></label><Button tone="quiet" onPress={() => send({ cmd: 'get_events', limit: 200 })}>Refresh</Button><Button tone="quiet" isDisabled={!Number.isFinite(beforeId)} onPress={() => send({ cmd: 'get_events', before_id: beforeId, limit: 200 })}>Load older</Button></header><section aria-label="Attention requests">{asks.map((task) => <AskResponse key={text(task.id)} taskId={text(task.id)} send={send} />)}{attention.map((agent) => <article key={text(agent.id)}><h3>{text(agent.name, text(agent.id))} · {agent.error_message ? 'Error' : 'Blocked'}</h3><p>{text(agent.error_message, text(agent.activity_detail, 'Needs attention'))}</p><Button onPress={() => send({ cmd: 'focus_agent', id: text(agent.id) })}>Focus {text(agent.name, text(agent.id))}</Button></article>)}</section><span role="status">{copyStatus}</span><div className={styles.eventTable}><div><b>Kind</b><b>Event</b><b>Agent / task</b><b>Time</b></div>{rows.length ? rows.map((event, index) => { const id = text(event.id, String(index)); return <article key={id} aria-current={selected === id ? 'true' : undefined} onClick={() => setSelected(selected === id ? '' : id)}><div><span>{text(event.kind, text(event.event_type, 'event'))}</span><strong>{label(event)}</strong><small>{text(event.cell_id, text(event.agent_id, text(event.task_id)))}</small><time>{time(event.created_at ?? event.timestamp)}</time></div>{selected === id ? <><pre className={styles.json}>{JSON.stringify(event, null, 2)}</pre><footer><Button tone="quiet" onPress={() => { void navigator.clipboard.writeText(text(event.message, label(event))).then(() => setCopyStatus('Event copied'), () => setCopyStatus('Copy failed; select the text to copy.')); }}>Copy event</Button><Button tone="quiet" onPress={() => send({ cmd: 'events_dismiss', id, timestamp: Date.now() / 1000 })}>Dismiss attention</Button>{text(event.cell_id, text(event.agent_id)) ? <Button tone="quiet" onPress={() => send({ cmd: 'focus_agent', id: text(event.cell_id, text(event.agent_id)) })}>Focus agent</Button> : null}</footer></> : null}</article>; }) : <StateSurface title="No matching events" description="Adjust filters or refresh the durable event log." />}</div></div>;
}

export function HelpPanel({ responses, send }: { responses: Record<string, unknown>; send: (command: TorqueCommand) => void }) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState('');
  const topicsFrame = record(responses['help_topics:_'] ?? responses['help_topics:latest']);
  const searchFrame = record(responses['help_search:_'] ?? responses['help_search:latest']);
  const queryFrame = record(responses['help_query:_'] ?? responses['help_query:latest']);
  const topicFrame = record(responses[`help_topic:${selected}`] ?? responses['help_topic:latest']);
  const topics = list(topicsFrame.topics);
  const results = list(searchFrame.results);
  const open = (topic: UnknownRecord) => { const id = text(topic.path_anchor, text(topic.topic_id, text(topic.source_path))); setSelected(id); send({ cmd: 'help_show', topic: id, max_chars: 16000 }); };
  return <div className={styles.helpBrowser}><header className={styles.operatorToolbar}><label>Search maintained Torque documentation<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="How do worktrees merge?" /></label><Button tone="quiet" onPress={() => send({ cmd: 'help_search', query, limit: 12 })} isDisabled={!query.trim()}>Search</Button><Button tone="primary" onPress={() => send({ cmd: 'help_query', question: query, limit: 5 })} isDisabled={!query.trim()}>Answer from docs</Button><Button tone="quiet" onPress={() => send({ cmd: 'help_list', audience: 'user' })}>All topics</Button></header><div className={styles.helpSplit}><aside><h2>{results.length ? 'Search results' : 'Topics'} <span>{results.length || topics.length}</span></h2><div>{(results.length ? results : topics).map((topic, index) => <button key={text(topic.path_anchor, text(topic.topic_id, String(index)))} aria-current={selected === text(topic.path_anchor, text(topic.topic_id)) ? 'page' : undefined} onClick={() => open(topic)}><strong>{text(topic.title, text(topic.topic_title, 'Topic'))}</strong><small>{text(topic.path_anchor, text(topic.source_path))}</small><p>{text(topic.excerpt, text(topic.summary))}</p></button>)}</div></aside><article className={styles.helpDocument}>{text(queryFrame.answer) ? <section><h2>Documentation answer</h2><p>{text(queryFrame.answer)}</p></section> : null}{text(topicFrame.body_excerpt) ? <><header><h2>{text(topicFrame.title)}</h2><small>{text(topicFrame.path_anchor, text(topicFrame.source_path))}</small></header><pre>{text(topicFrame.body_excerpt)}</pre></> : <div className={styles.helpQuickStart}><h2>Torque help</h2><ol><li><strong>Start with a group</strong><span>Groups scope Board work, agents, planning context, settings, and worktrees.</span></li><li><strong>Plan, then dispatch</strong><span>Use Planning for durable product intent and Board for executable work.</span></li><li><strong>Supervise by exception</strong><span>Mission Control, Inbox, and focused agent terminals surface gates and risks.</span></li></ol><p>Help is read-only and sourced from Torque’s maintained Markdown allow-list.</p></div>}</article></div></div>;
}
