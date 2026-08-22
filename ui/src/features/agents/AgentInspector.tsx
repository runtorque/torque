import { useEffect, useMemo, useState } from 'react';

import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { CommandSender } from '../board/BoardPanel';
import type { AgentViewModel } from './model';
import styles from './AgentWorkspace.module.css';

type InspectorTab = 'events' | 'mcp' | 'history' | 'class' | 'journal' | 'context' | 'chat';

interface CatalogBundle {
  agentClasses: unknown;
}

interface AgentInspectorProps {
  agent: AgentViewModel;
  group: string;
  catalog: CatalogBundle;
  responses: Record<string, unknown>;
  messages: unknown;
  sendCommand: CommandSender;
  onUnavailable: () => void;
  onClose: () => void;
}

interface ClassOption {
  id: string;
  label: string;
  kind: string;
  raw: Record<string, unknown>;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function list(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.map(record).filter((item) => Object.keys(item).length > 0);
  return Object.values(record(value)).map(record).filter((item) => Object.keys(item).length > 0);
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}

function bool(value: unknown): boolean {
  return value === true || value === 1 || value === 'true' || value === 'success';
}

function timestamp(value: unknown): string {
  const numeric = Number(value ?? 0);
  if (!numeric) return '—';
  const date = new Date(numeric < 1_000_000_000_000 ? numeric * 1_000 : numeric);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

function json(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value, null, 2); } catch { return '[unserializable value]'; }
}

function responseFor(responses: Record<string, unknown>, type: string, target: string): Record<string, unknown> {
  return record(responses[`${type}:${target}`]);
}

function classOptions(value: unknown): ClassOption[] {
  const entries = Array.isArray(value)
    ? value.map((item, index) => [String(index), item] as const)
    : Object.entries(record(value));
  return entries.map(([fallback, rawValue]) => {
    const raw = record(rawValue);
    return {
      id: text(raw.id) || text(raw.slug) || fallback,
      label: text(raw.display_name) || text(raw.name) || text(raw.title) || text(raw.id) || fallback,
      kind: text(raw.base_kind) || text(raw.kind),
      raw,
    };
  }).filter((item) => item.id);
}

function Empty({ title, description }: { title: string; description: string }) {
  return <StateSurface title={title} description={description} />;
}

export function AgentInspector({ agent, group, catalog, responses, messages, sendCommand, onUnavailable, onClose }: AgentInspectorProps) {
  const [tab, setTab] = useState<InspectorTab>('events');
  const [toolFilter, setToolFilter] = useState('');
  const [outcome, setOutcome] = useState('all');
  const [range, setRange] = useState('24h');
  const [selectedClassId, setSelectedClassId] = useState(text(agent.raw.agent_class_id));
  const [memorySearch, setMemorySearch] = useState('');
  const [memoryType, setMemoryType] = useState('');
  const [memoryDraft, setMemoryDraft] = useState({ entry_id: '', title: '', content: '', entry_type: 'note', pinned: false });
  const [peerDraft, setPeerDraft] = useState({ peer: '', message: '', context: '', ack: false });
  const [engineerReply, setEngineerReply] = useState('');

  const run = (command: Record<string, unknown>) => {
    if (!sendCommand(command as { cmd: string })) onUnavailable();
  };

  const refreshEvents = () => run({ cmd: 'get_cell_events', cell_id: agent.id, limit: 200 });
  const refreshMcp = () => {
    const now = Date.now() / 1_000;
    const since = range === '1h' ? now - 3_600 : range === '6h' ? now - 21_600 : range === '24h' ? now - 86_400 : undefined;
    run({
      cmd: 'mcp_calls',
      cell_id: agent.id,
      tool_name_pattern: toolFilter.trim() ? `*${toolFilter.trim()}*` : 'mcp__torque__%',
      hook_event_name: 'PostToolUse',
      success_filter: outcome,
      limit: 100,
      ...(since ? { since } : {}),
    });
  };
  const refreshClass = () => {
    run({ cmd: 'agent_class_list' });
    run({ cmd: 'agent_class_status', agent_id: agent.id });
    run({ cmd: 'agent_class_audit', agent_id: agent.id, limit: 50 });
  };
  const refreshMemory = () => run({ cmd: 'memory_list', group_name: group, linked_target_kind: 'agent', linked_target_ref: agent.id, search: memorySearch, entry_type: memoryType, limit: 100 });
  const refreshPeers = () => { if (agent.kind === 'architect') { run({ cmd: 'architect_peer_list', architect_id: agent.id }); run({ cmd: 'architect_peer_inbox', architect_id: agent.id }); } };

  useEffect(() => {
    refreshEvents();
    refreshMcp();
    refreshClass();
    refreshMemory();
    refreshPeers();
    run({ cmd: 'get_agent_history_detail', agent_id: agent.id, message_limit: 100 });
    if (agent.kind === 'architect') {
      run({ cmd: 'architect_journal_read', architect_id: agent.id, limit: 200 });
      run({ cmd: 'decisions_snapshot', include_archived: true });
    } else if (agent.kind === 'engineer') {
      run({ cmd: 'engineer_journal_snapshot', group, engineer_id: agent.id, include_streams: true, limit: 200 });
      run({ cmd: 'engineer_session_map_read', group, engineer_id: agent.id });
      run({ cmd: 'get_group_settings', group });
    }
    // Requests are intentionally scoped to the inspector's lifetime and agent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id, group]);

  const eventsFrame = responseFor(responses, 'cell_events', agent.id);
  const events = list(eventsFrame.events);
  const mcpFrame = responseFor(responses, 'mcp_calls', agent.id);
  const calls = list(mcpFrame.calls ?? mcpFrame.events);
  const historyFrame = responseFor(responses, 'agent_history_detail', agent.id);
  const historyRecord = record(historyFrame.record);
  const historyTasks = list(historyFrame.tasks);
  const historyMessages = list(historyFrame.messages);
  const classFrame = responseFor(responses, 'agent_class_status', agent.id);
  const assignmentFrame = responseFor(responses, 'agent_class_assignment', agent.id);
  const classStatus = record(assignmentFrame.status ?? classFrame.status ?? agent.raw.agent_class_status);
  const classAuditFrame = responseFor(responses, 'agent_class_audit', agent.id);
  const classAudit = list((Object.keys(classAuditFrame).length ? classAuditFrame : record(responses['agent_class_audit:latest'])).events);
  const classes = useMemo(() => classOptions(catalog.agentClasses).filter((item) => !item.kind || item.kind === agent.kind), [catalog.agentClasses, agent.kind]);
  const selectedClass = classes.find((item) => item.id === selectedClassId)?.raw ?? {};
  const architectJournal = list(responseFor(responses, 'architect_journal_entries', agent.id).entries);
  const engineerJournalFrame = responseFor(responses, 'engineer_journal_snapshot', group);
  const engineerJournal = list(record(engineerJournalFrame.engineer_journal)[agent.id]);
  const engineerWorklog = list(record(engineerJournalFrame.engineer_worklog)[group]).filter((item) => !text(item.engineer_id) || text(item.engineer_id) === agent.id);
  const groupSettingsFrame = responseFor(responses, 'group_settings', group);
  const engineerSettings = record(groupSettingsFrame.engineer_settings);
  const sessionMapFrame = responseFor(responses, 'engineer_session_map', group);
  const sessionMap = record(sessionMapFrame.session_map);
  const journals = agent.kind === 'architect' ? architectJournal : engineerJournal;
  const memoryFrame = responseFor(responses, 'memory_entries', agent.id);
  const memories = list(memoryFrame.entries);
  const peerFrame = responseFor(responses, 'architect_peer_list', agent.id);
  const peers = list(peerFrame.architects).filter((peer) => text(peer.id) !== agent.id);
  const peerThreads = list(messages).filter((thread) => [thread.architect_id, thread.sender_architect_id, thread.recipient_architect_id, thread.peer_architect_id].some((value) => text(value) === agent.id));
  const tabs: Array<{ id: InspectorTab; label: string }> = [
    { id: 'events', label: `Events ${events.length || ''}`.trim() },
    { id: 'mcp', label: `MCP ${calls.length || ''}`.trim() },
    { id: 'history', label: 'History' },
    { id: 'class', label: 'Agent Class' },
    { id: 'context', label: `Context ${memories.length || ''}`.trim() },
    ...(agent.kind === 'architect' ? [{ id: 'chat' as const, label: `Peer chat ${peerThreads.length || ''}`.trim() }] : []),
    ...(agent.kind === 'architect' || agent.kind === 'engineer' ? [{ id: 'journal' as const, label: `Journal ${journals.length || ''}`.trim() }] : []),
  ];

  return <ModalDialog
    title={`Inspect ${agent.name}`}
    description={`${agent.kind} · ${agent.id}`}
    size="large"
    isOpen
    onOpenChange={(open) => { if (!open) onClose(); }}
  >
    <div className={styles.agentInspector}>
      <nav role="tablist" aria-label="Agent inspection">
        {tabs.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} onClick={() => setTab(item.id)}>{item.label}</button>)}
        <span />
        <Button tone="quiet" onPress={() => {
          if (tab === 'events') refreshEvents();
          else if (tab === 'mcp') refreshMcp();
          else if (tab === 'history') run({ cmd: 'get_agent_history_detail', agent_id: agent.id, message_limit: 100 });
          else if (tab === 'class') refreshClass();
          else if (tab === 'context') refreshMemory();
          else if (tab === 'chat') refreshPeers();
          else if (agent.kind === 'architect') run({ cmd: 'architect_journal_read', architect_id: agent.id, limit: 200 });
          else run({ cmd: 'engineer_journal_snapshot', group, engineer_id: agent.id, include_streams: true, limit: 200 });
        }}>Refresh</Button>
      </nav>

      {tab === 'events' ? <section className={styles.inspectorFeed} aria-label="Cell events">
        {events.length ? events.map((event, index) => <article key={text(event.id) || `${text(event.timestamp)}-${index}`}>
          <header><strong>{text(event.kind, 'event').replaceAll('_', ' ')}</strong><time>{timestamp(event.timestamp)}</time></header>
          <p>{text(event.message) || text(event.summary) || 'Runtime event'}</p>
          <footer>{text(event.task_id) ? <span>{text(event.task_id)}</span> : null}<span>{text(event.source, 'runtime')}</span></footer>
        </article>) : <Empty title="No cell events" description="No persisted or live runtime events have been recorded for this agent." />}
      </section> : null}

      {tab === 'mcp' ? <section className={styles.inspectorMcp} aria-label="MCP activity">
        <form onSubmit={(event) => { event.preventDefault(); refreshMcp(); }}>
          <label>Tool contains<input value={toolFilter} onChange={(event) => setToolFilter(event.target.value)} placeholder="task_progress" /></label>
          <label>Outcome<select value={outcome} onChange={(event) => setOutcome(event.target.value)}><option value="all">All</option><option value="success">Success</option><option value="error">Error</option></select></label>
          <label>Range<select value={range} onChange={(event) => setRange(event.target.value)}><option value="1h">1 hour</option><option value="6h">6 hours</option><option value="24h">24 hours</option><option value="all">All retained</option></select></label>
          <Button type="submit">Apply</Button>
        </form>
        <div className={styles.inspectorFeed}>{calls.length ? calls.map((call, index) => <details key={text(call.cursor) || text(call.idempotency_key) || String(index)}>
          <summary><span className={bool(call.success) ? styles.callSuccess : styles.callError}>{bool(call.success) ? '✓' : '!'}</span><strong>{text(call.tool_name, 'MCP call')}</strong><time>{timestamp(call.appended_at ?? call.timestamp)}</time></summary>
          <dl><div><dt>Duration</dt><dd>{text(call.duration_ms, '—')} ms</dd></div><div><dt>Hook</dt><dd>{text(call.hook_event_name, '—')}</dd></div><div><dt>Session</dt><dd>{text(call.session_id, '—')}</dd></div></dl>
          <h4>Arguments</h4><pre>{json(call.args ?? call.arguments ?? call.args_summary)}</pre>
          <h4>Result</h4><pre>{json(call.result ?? call.result_summary ?? call.error)}</pre>
        </details>) : <Empty title="No matching MCP calls" description="Adjust the tool, outcome, or time filters and refresh." />}</div>
      </section> : null}

      {tab === 'history' ? <section className={styles.inspectorHistory} aria-label="Agent history">
        {Object.keys(historyRecord).length ? <>
          <dl className={styles.inspectorFacts}>
            <div><dt>Status</dt><dd>{text(historyRecord.status, agent.status)}</dd></div>
            <div><dt>Started</dt><dd>{timestamp(historyRecord.created_at ?? historyRecord.started_at)}</dd></div>
            <div><dt>Finished</dt><dd>{timestamp(historyRecord.removed_at ?? historyRecord.completed_at)}</dd></div>
            <div><dt>Provider</dt><dd>{text(historyRecord.provider, agent.provider || '—')}</dd></div>
            <div><dt>Model</dt><dd>{text(historyRecord.model, '—')}</dd></div>
            <div><dt>Tokens</dt><dd>{text(historyRecord.total_tokens ?? historyRecord.token_count, '—')}</dd></div>
            <div><dt>Branch</dt><dd>{text(historyRecord.worktree_branch ?? historyRecord.branch, agent.worktreeBranch || '—')}</dd></div>
            <div><dt>Outcome</dt><dd>{text(historyRecord.outcome, '—')}</dd></div>
          </dl>
          <div className={styles.historyColumns}>
            <section><h3>Tasks <span>{historyTasks.length}</span></h3>{historyTasks.length ? historyTasks.map((task, index) => <article key={text(task.task_id) || text(task.id) || String(index)}><strong>{text(task.task) || text(task.title) || text(task.task_id) || 'Task'}</strong><span>{text(task.lane) || text(task.status)}</span><p>{text(task.result) || text(task.summary)}</p></article>) : <p>No recorded tasks.</p>}</section>
            <section><h3>Messages <span>{historyMessages.length}</span></h3>{historyMessages.length ? historyMessages.map((message, index) => <article key={text(message.id) || String(index)}><strong>{text(message.action) || text(message.role) || 'message'}</strong><time>{timestamp(message.created_at ?? message.timestamp)}</time><p>{text(message.message) || text(message.content) || text(message.text)}</p></article>) : <p>No recorded messages.</p>}</section>
          </div>
        </> : <Empty title="Loading agent history" description="Torque is retrieving persisted tasks, messages, and lifecycle metadata." />}
      </section> : null}

      {tab === 'class' ? <section className={styles.classInspector} aria-label="Agent Class assignment">
        <div className={styles.classAssignment}>
          <label>Desired class for next launch<select value={selectedClassId} onChange={(event) => setSelectedClassId(event.target.value)}><option value="">Default for {agent.kind}</option>{classes.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
          <Button tone="primary" onPress={() => run({ cmd: selectedClassId ? 'agent_class_assign' : 'agent_class_clear', agent_id: agent.id, ...(selectedClassId ? { class_id: selectedClassId } : {}), actor_label: 'trusted-user-react-ui' })}>Save assignment</Button>
          {bool(classStatus.pending_next_launch) ? <Button onPress={() => run({ cmd: 'relaunch_agent', id: agent.id })}>Relaunch to apply</Button> : null}
        </div>
        <dl className={styles.inspectorFacts}>
          <div><dt>Effective now</dt><dd>{text(classStatus.effective_primary_identity_label) || text(classStatus.effective_class_id) || 'Default'}</dd></div>
          <div><dt>Desired</dt><dd>{text(classStatus.next_launch_primary_identity_label) || text(classStatus.next_launch_class_id) || 'Default'}</dd></div>
          <div><dt>Effective version</dt><dd>{text(classStatus.effective_class_version, '—')}</dd></div>
          <div><dt>Next version</dt><dd>{text(classStatus.next_launch_class_version, '—')}</dd></div>
          <div><dt>Assigned by</dt><dd>{text(classStatus.assigned_by, '—')}</dd></div>
          <div><dt>Apply state</dt><dd>{bool(classStatus.pending_next_launch) ? 'Pending relaunch' : 'Current'}</dd></div>
        </dl>
        {list(classStatus.warnings).length || Array.isArray(classStatus.warnings) ? <ul className={styles.classWarnings}>{(Array.isArray(classStatus.warnings) ? classStatus.warnings : []).map((warning, index) => <li key={index}>{text(warning)}</li>)}</ul> : null}
        {selectedClassId ? <section className={styles.classPreview}><h3>{text(selectedClass.display_name) || text(selectedClass.name) || selectedClassId}</h3><p>{text(selectedClass.description, 'No description provided.')}</p><pre>{json(selectedClass)}</pre></section> : null}
        <section className={styles.classAudit}><h3>Assignment audit <span>{classAudit.length}</span></h3>{classAudit.length ? classAudit.map((event, index) => <article key={text(event.id) || String(index)}><header><strong>{text(event.event, 'class event').replaceAll('_', ' ')}</strong><time>{timestamp(event.created_at)}</time></header><p>{text(event.message)}</p><span>{text(event.actor_label) || text(event.actor_kind)}</span></article>) : <p>No assignment changes recorded.</p>}</section>
      </section> : null}

      {tab === 'context' ? <section className={styles.contextInspector} aria-label="Shared context">
        <form className={styles.contextFilters} onSubmit={(event) => { event.preventDefault(); refreshMemory(); }}><label>Search<input value={memorySearch} onChange={(event) => setMemorySearch(event.target.value)} /></label><label>Type<select value={memoryType} onChange={(event) => setMemoryType(event.target.value)}><option value="">All</option>{['note', 'decision', 'constraint', 'finding', 'summary', 'handoff'].map((value) => <option key={value}>{value}</option>)}</select></label><Button type="submit">Apply</Button></form>
        <div className={styles.contextColumns}><section><h3>Linked memory <span>{memories.length}</span></h3>{memories.length ? memories.map((entry) => <article key={text(entry.id)}><header><strong>{text(entry.title, text(entry.entry_type, 'Memory'))}</strong><span>{text(entry.entry_type)}{bool(entry.pinned) ? ' · pinned' : ''}</span></header><p>{text(entry.content)}</p><footer><small>{text(entry.source_name, text(entry.source_kind))} · {timestamp(entry.updated_at ?? entry.created_at)}</small><Button tone="quiet" onPress={() => run({ cmd: bool(entry.pinned) ? 'memory_unpin' : 'memory_pin', entry_id: text(entry.id) })}>{bool(entry.pinned) ? 'Unpin' : 'Pin'}</Button><Button tone="quiet" onPress={() => setMemoryDraft({ title: text(entry.title), content: text(entry.content), entry_type: text(entry.entry_type, 'note'), pinned: bool(entry.pinned), entry_id: text(entry.id) })}>Edit</Button></footer></article>) : <Empty title="No linked context" description="Publish durable memory linked to this agent, or broaden the search in the workspace Context surface." />}</section><form onSubmit={(event) => { event.preventDefault(); run({ cmd: 'memory_publish', ...(memoryDraft.entry_id ? { entry_id: memoryDraft.entry_id } : {}), title: memoryDraft.title, content: memoryDraft.content, entry_type: memoryDraft.entry_type, scope_kind: 'group', scope_ref: group, pinned: memoryDraft.pinned, source_kind: 'manual', link_targets: [{ target_kind: 'agent', target_ref: agent.id }] }); setMemoryDraft({ entry_id: '', title: '', content: '', entry_type: 'note', pinned: false }); }}><h3>Publish context</h3><label>Title<input value={memoryDraft.title} onChange={(event) => setMemoryDraft({ ...memoryDraft, title: event.target.value })} /></label><label>Type<select value={memoryDraft.entry_type} onChange={(event) => setMemoryDraft({ ...memoryDraft, entry_type: event.target.value })}>{['note', 'decision', 'constraint', 'finding', 'summary', 'handoff'].map((value) => <option key={value}>{value}</option>)}</select></label><label>Content<textarea value={memoryDraft.content} onChange={(event) => setMemoryDraft({ ...memoryDraft, content: event.target.value })} /></label><label className={styles.inspectorCheck}><input type="checkbox" checked={memoryDraft.pinned} onChange={(event) => setMemoryDraft({ ...memoryDraft, pinned: event.target.checked })} />Pin for ranking</label><Button tone="primary" type="submit" isDisabled={!memoryDraft.content.trim()}>Publish</Button></form></div>
      </section> : null}

      {tab === 'chat' ? <section className={styles.peerChat} aria-label="Architect peer chat"><div><h3>Peer threads <span>{peerThreads.length}</span></h3>{peerThreads.length ? peerThreads.map((thread, index) => <article key={text(thread.id, String(index))}><header><strong>{text(thread.sender_name, text(thread.sender_architect_id, 'Architect'))}</strong><time>{timestamp(thread.created_at ?? thread.timestamp)}</time></header><p>{text(thread.message, text(thread.content))}</p>{text(thread.context_summary) ? <small>{text(thread.context_summary)}</small> : null}</article>) : <Empty title="No peer messages" description="Architect-to-Architect coordination appears here with its attached decision, task, and engineer context." />}</div><form onSubmit={(event) => { event.preventDefault(); const contextIds = peerDraft.context.split(/[\n,]/).map((value) => value.trim()).filter(Boolean); run({ cmd: 'architect_peer_message', architect_id: peerDraft.peer, sender_architect_id: agent.id, message: peerDraft.message, ack_required: peerDraft.ack, context_task_ids: contextIds, context_summary: peerDraft.context }); setPeerDraft({ peer: '', message: '', context: '', ack: false }); }}><h3>Message peer</h3><label>Architect<select value={peerDraft.peer} onChange={(event) => setPeerDraft({ ...peerDraft, peer: event.target.value })}><option value="">Choose…</option>{peers.map((peer) => <option key={text(peer.id)} value={text(peer.id)}>{text(peer.name, text(peer.id))}</option>)}</select></label><label>Message<textarea value={peerDraft.message} onChange={(event) => setPeerDraft({ ...peerDraft, message: event.target.value })} /></label><label>Task IDs / context summary<textarea value={peerDraft.context} onChange={(event) => setPeerDraft({ ...peerDraft, context: event.target.value })} /></label><label className={styles.inspectorCheck}><input type="checkbox" checked={peerDraft.ack} onChange={(event) => setPeerDraft({ ...peerDraft, ack: event.target.checked })} />Require acknowledgement</label><Button tone="primary" type="submit" isDisabled={!peerDraft.peer || !peerDraft.message.trim()}>Send</Button></form></section> : null}

      {tab === 'journal' ? <section className={styles.inspectorJournal} aria-label={`${agent.kind} journal`}>
        {agent.kind === 'engineer' ? <header className={styles.journalActions}><div><h3>Engineer delivery</h3><p>{engineerSettings.paused === true ? 'Digest delivery paused' : 'Digest delivery active'}</p></div><Button tone="quiet" onPress={() => run({ cmd: 'engineer_flush_now', agent_id: agent.id })}>Send digest now</Button><Button tone="quiet" onPress={() => run({ cmd: engineerSettings.paused === true ? 'engineer_resume' : 'engineer_pause', group, engineer_id: agent.id })}>{engineerSettings.paused === true ? 'Resume' : 'Pause'}</Button></header> : null}
        {agent.kind === 'engineer' && text(engineerSettings.pending_question) ? <form className={styles.pendingEngineer} onSubmit={(event) => { event.preventDefault(); run({ cmd: 'engineer_reply', group, answer: engineerReply }); setEngineerReply(''); }}><strong>Engineer asks</strong><p>{text(engineerSettings.pending_question)}</p><textarea value={engineerReply} onChange={(event) => setEngineerReply(event.target.value)} placeholder="Answer and resume delivery" /><footer><Button tone="quiet" onPress={() => run({ cmd: 'engineer_resume', group, engineer_id: agent.id })}>Dismiss</Button><Button tone="primary" type="submit" isDisabled={!engineerReply.trim()}>Reply</Button></footer></form> : null}
        {agent.kind === 'engineer' && text(engineerSettings.pending_note) ? <article className={styles.pendingEngineer}><strong>{text(engineerSettings.pending_note_kind, 'Engineer note')}</strong><p>{text(engineerSettings.pending_note)}</p><footer><Button tone="quiet" onPress={() => run({ cmd: 'engineer_dismiss_note', group, engineer_id: agent.id })}>Dismiss note</Button></footer></article> : null}
        {journals.length ? journals.map((entry, index) => <article key={text(entry.id) || String(index)}><header><strong>{text(entry.title) || text(entry.type) || 'Journal entry'}</strong><time>{timestamp(entry.created_at ?? entry.timestamp)}</time></header><p>{text(entry.summary) || text(entry.message) || text(entry.content) || text(entry.note)}</p><footer>{text(entry.task_id) ? <span>{text(entry.task_id)}</span> : <span />}{agent.kind === 'engineer' && text(entry.id) ? <Button tone="quiet" onPress={() => run({ cmd: 'engineer_journal_delete', group, entry_id: text(entry.id), author_cell_id: agent.id })}>Delete</Button> : null}</footer></article>) : <Empty title="No journal entries" description={`No ${agent.kind} journal entries have been recorded yet.`} />}
        {agent.kind === 'engineer' && engineerWorklog.length ? <section className={styles.worklog}><h3>Group worklog <span>{engineerWorklog.length}</span></h3>{engineerWorklog.map((entry, index) => <article key={text(entry.id) || String(index)}><strong>{text(entry.kind) || text(entry.action) || 'worklog'}</strong><time>{timestamp(entry.created_at ?? entry.timestamp)}</time><p>{text(entry.message) || text(entry.summary)}</p></article>)}</section> : null}
        {agent.kind === 'engineer' && Object.keys(sessionMap).length ? <details className={styles.sessionMap}><summary>Session map</summary><pre>{json(sessionMap)}</pre></details> : null}
      </section> : null}

      <footer className={styles.inspectorFooter}><span>Runtime events and effective Agent Class authority are read-only until an explicit action is chosen.</span><Button tone="quiet" onPress={onClose}>Close</Button></footer>
    </div>
  </ModalDialog>;
}
