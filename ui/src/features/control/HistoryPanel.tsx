import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { selectConnection, selectTasksState, workspaceUiActions } from '../../app/store';
import { Button, StateSurface } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { settingsRequest } from './settingsRequests';
import { text } from '../planning/model';
import styles from './ControlCenter.module.css';

const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
const list = (value: unknown): UnknownRecord[] => Array.isArray(value) ? value.map(record) : [];
function HistoryMessage({ message }: { message: UnknownRecord }) {
  const body = text(message.message, text(message.content, text(message.text)));
  return body.length > 160 ? <details><summary>{body.slice(0, 160)}…</summary><p>{body}</p></details> : <p>{body}</p>;
}
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className={styles.field}><span>{label}</span>{children}</label>; }

function formatTime(value: unknown): string {
  const numeric = Number(value ?? 0);
  if (!numeric) return '—';
  const date = new Date(numeric < 1_000_000_000_000 ? numeric * 1_000 : numeric);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

export function HistoryPanel({ group, send }: {
  group: string;
  send: (command: TorqueCommand) => void;
}) {
  const connection = useAppSelector(selectConnection);
  const tasks = useAppSelector(selectTasksState);
  const dispatch = useAppDispatch();
  const [status, setStatus] = useState('merged');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [listFrame, setListFrame] = useState<UnknownRecord>({});
  const [detail, setDetail] = useState<UnknownRecord>({});
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [detailRefresh, setDetailRefresh] = useState(0);
  useEffect(() => {
    if (connection.status !== 'connected') return;
    const controller = new AbortController();
    void settingsRequest({ cmd: 'get_agent_history', status, limit: 100 }, controller.signal, false, 'History').then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'agent_history_list' || !Array.isArray(frame.records)) throw new Error(text(frame.message, 'Invalid history response.'));
      setListFrame(frame); setListError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setListError(cause instanceof Error ? cause.message : 'History unavailable.'); });
    return () => controller.abort();
  }, [connection.status, connection.reconnectCount, status, refresh]);
  useEffect(() => {
    if (!selectedId || connection.status !== 'connected') return;
    const controller = new AbortController();
    void settingsRequest({ cmd: 'get_agent_history_detail', agent_id: selectedId, message_limit: 100 }, controller.signal, false, 'History').then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'agent_history_detail' || record(frame.record).id !== selectedId || !Array.isArray(frame.tasks) || !Array.isArray(frame.messages)) throw new Error(text(frame.message, 'Could not load the selected run.'));
      setDetail(frame); setDetailError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setDetailError(cause instanceof Error ? cause.message : 'Run detail unavailable.'); });
    return () => controller.abort();
  }, [connection.status, connection.reconnectCount, selectedId, refresh, detailRefresh]);
  const openTask = (id: string) => {
    if (!tasks.records[id]) return; const target = record(tasks.records[id]);
    if (target.group && target.group !== group) send({ cmd: 'ui_select_group', group: text(target.group) });
    dispatch(workspaceUiActions.setActivePanel('board')); dispatch(workspaceUiActions.setDetailTask(id));
  };
  const history = list(listFrame.records).map(record).filter((item) => !group || text(item.group) === group).filter((item) => {
    const query = search.trim().toLocaleLowerCase();
    if (!query) return true;
    return [item.name, item.id, item.group, item.kind, item.agent_type, item.provider]
      .some((value) => text(value).toLocaleLowerCase().includes(query));
  });
  const detailRecord = record(detail.record).id === selectedId ? record(detail.record) : {};
  const detailTasks = list(detail.tasks).map(record);
  const detailMessages = list(detail.messages).map(record);

  return <div className={styles.historyPanel}>
    <div><header className={styles.historyToolbar}>
      <Field label="Search history"><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Agent name, ID, kind, provider" /></Field>
      <div className={styles.historyFilters} role="group" aria-label="History status">{([['', 'All'], ['active', 'Active'], ['removed', 'Removed'], ['merged', 'Merged']] as const).map(([id, label]) => <button key={id} aria-pressed={status === id} onClick={() => setStatus(id)}>{label}</button>)}</div>
      <Button tone="quiet" onPress={() => setRefresh((value) => value + 1)}>Refresh</Button>
    </header>
    {listError ? <p role="alert">History refresh failed. Previous results are retained. {listError} <Button onPress={() => setRefresh((value) => value + 1)}>Retry history</Button></p> : null}
    </div><div className={styles.historySplit}>
      <section className={styles.historyList} aria-label="Agent runs">
        <header><h2>Agent runs</h2><span>{history.length}</span></header>
        {history.length ? history.map((item, index) => {
          const id = text(item.id, text(item.agent_id));
          return <button key={id || String(index)} aria-current={selectedId === id ? 'true' : undefined} onClick={() => { setSelectedId(id); setDetailError(''); setDetailRefresh((value) => value + 1); }}>
            <span><strong>{text(item.name, id || 'Agent run')}</strong><small>{text(item.kind, text(item.agent_type, 'agent'))} · {text(item.provider, text(item.agent_type, 'unknown provider'))}</small><small>{text(item.total_tasks, '0')} tasks · {text(item.total_tokens_in, '0')} in / {text(item.total_tokens_out, '0')} out</small></span>
            <span><b>{text(item.status, 'unknown')}</b><time>{formatTime(item.removed_at ?? item.completed_at ?? item.updated_at ?? item.created_at)}</time></span>
          </button>;
        }) : <StateSurface title={Array.isArray(listFrame.records) ? "No historical runs" : listError ? "History unavailable" : "Loading history"} description="No agent runs match this group, status, and search." />}
      </section>
      <section className={styles.historyDetail} aria-label="Run detail">{detailError ? <p role="alert">Run refresh failed. {detailError} <Button onPress={() => setDetailRefresh((value) => value + 1)}>Retry run</Button></p> : null}{selectedId ? Object.keys(detailRecord).length ? <>
        <header><div><h2>{text(detailRecord.name, selectedId)}</h2><p>{text(detailRecord.id, selectedId)} · {text(detailRecord.status, 'unknown')}</p></div>{text(detailRecord.status) === 'active' ? <Button tone="quiet" onPress={() => { send({ cmd: 'focus_agent', id: selectedId }); dispatch(workspaceUiActions.setSelectedAgent(selectedId)); dispatch(workspaceUiActions.setActivePanel('agents')); }}>Focus live agent</Button> : null}</header>
        <dl><div><dt>Role</dt><dd>{text(detailRecord.template, '—')}</dd></div><div><dt>Started</dt><dd>{formatTime(detailRecord.started_at ?? detailRecord.created_at)}</dd></div><div><dt>Finished</dt><dd>{formatTime(detailRecord.removed_at ?? detailRecord.completed_at)}</dd></div><div><dt>Model</dt><dd>{text(detailRecord.model, '—')}</dd></div><div><dt>Tokens</dt><dd>{text(detailRecord.total_tokens_in, '0')} in / {text(detailRecord.total_tokens_out, '0')} out</dd></div><div><dt>Branch</dt><dd>{text(detailRecord.worktree_branch ?? detailRecord.branch, '—')}</dd></div><div><dt>Outcome</dt><dd>{text(detailRecord.outcome, '—')}</dd></div></dl>
        <div className={styles.historyDetailColumns}>
          <section><h3>Tasks <span>{detailTasks.length}</span></h3>{detailTasks.length ? detailTasks.map((task, index) => <article key={text(task.id, String(index))}><strong>{text(task.task_title, text(task.task, text(task.title, text(task.task_id, 'Task'))))}</strong><span>{text(task.outcome, text(task.lane, text(task.status)))}</span><time>{formatTime(task.started_at)}</time><Button tone="quiet" isDisabled={!tasks.records[text(task.task_id)]} onPress={() => openTask(text(task.task_id))}>Open task</Button><p>{text(task.result, text(task.summary))}</p></article>) : <p>No recorded tasks.</p>}</section>
          <section><h3>Messages <span>{detailMessages.length}</span></h3>{detailMessages.length ? detailMessages.map((message, index) => <article key={text(message.id, String(index))}><strong>{text(message.action, text(message.role, 'message'))}</strong><time>{formatTime(message.timestamp ?? message.created_at)}</time><HistoryMessage message={message} />{text(message.task_id) ? <Button tone="quiet" isDisabled={!tasks.records[text(message.task_id)]} onPress={() => openTask(text(message.task_id))}>Open message task</Button> : null}</article>) : <p>No recorded messages.</p>}</section>
        </div>
      </> : <StateSurface title={detailError ? "Run detail unavailable" : "Loading run detail"} description="Torque is loading persisted tasks, messages, and lifecycle metadata." /> : <StateSurface title="Select an agent run" description="Inspect its lifecycle, tasks, messages, provider, model, tokens, branch, and outcome." />}</section>
    </div>
  </div>;
}

