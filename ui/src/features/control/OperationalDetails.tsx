import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { selectSupervisorUiState, workspaceUiActions, type WorkspaceUiState } from '../../app/store';
import { Button } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import styles from './ParityPanels.module.css';
import { HealthLiveMetrics } from './HealthLiveMetrics';
import { useHealthHistory } from './useHealthHistory';

function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
function text(value: unknown, fallback = '—'): string { return typeof value === 'string' || typeof value === 'number' ? String(value) : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : fallback; }
function list(value: unknown): UnknownRecord[] { return Array.isArray(value) ? value.map(record) : []; }
function stamp(value: unknown): string { const n = Number(value); return Number.isFinite(n) && n > 0 ? new Date(n < 1e12 ? n * 1000 : n).toLocaleString() : '—'; }
type Sender = (command: TorqueCommand) => void;

const sortFields = ['owner', 'state', 'session', 'pid', 'started_at', 'command', 'bytes', 'tty', 'path'] as const;
function sortValue(session: UnknownRecord, key: string): string | number {
  const owner = record(session.owner);
  if (key === 'owner') return text(owner.name, text(session.agent_name, ''));
  if (key === 'state') return session.alive === true ? 'alive' : 'exited';
  if (key === 'session') return text(session.session_id, '');
  if (key === 'command') return text(session.display_command, text(session.command, ''));
  if (key === 'bytes') return Number(session.total_bytes ?? session.bytes_written ?? session.output_bytes ?? session.bytes ?? 0);
  if (key === 'tty') return Number(session.cols ?? 0) * 10000 + Number(session.rows ?? 0);
  if (key === 'path') return text(session.current_path, text(session.cwd, ''));
  return Number(session[key] ?? 0);
}

export function SupervisorDetails({ supervisor, send, onTerminate }: { supervisor: UnknownRecord; send: Sender; onTerminate: (id: string, label: string) => void }) {
  const saved = useAppSelector(selectSupervisorUiState);
  const reconnect = useAppSelector((state) => state.connection.reconnectCount);
  const [view, setView] = useState(() => ({ autoRefresh: saved.autoRefresh !== false, sortKey: text(saved.sortKey, 'owner'), sortDirection: text(saved.sortDirection, 'asc'), selectedSessionId: text(saved.selectedSessionId, ''), expandedSessionId: text(saved.expandedSessionId, ''), scrollPos: Number(saved.scrollPos || 0) }));
  const [sessions, setSessions] = useState(() => list(supervisor.sessions));
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [updated, setUpdated] = useState('');
  const viewport = useRef<HTMLDivElement>(null);
  const scrollTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const update = (patch: Partial<typeof view>) => { const next = { ...view, ...patch }; setView(next); send({ cmd: 'ui_set_supervisor_panel_state', state: next }); };
  useLayoutEffect(() => { if (viewport.current) viewport.current.scrollTop = Number(saved.scrollPos || 0); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const frame = await readCommand({ cmd: 'supervisor_sessions_list' }, controller.signal);
        if (controller.signal.aborted) return;
        if (frame.available === false) throw new Error(text(frame.message, 'Supervisor unavailable'));
        setSessions(list(frame.sessions)); setError(''); setUpdated(new Date().toLocaleTimeString());
      } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Supervisor unavailable'); }
      if (view.autoRefresh && !controller.signal.aborted) timer = setTimeout(() => { void poll(); }, 2000);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [view.autoRefresh, refresh, reconnect]);
  useEffect(() => () => clearTimeout(scrollTimer.current), []);
  const sorted = [...sessions].sort((a, b) => {
    const left = sortValue(a, view.sortKey); const right = sortValue(b, view.sortKey);
    return (typeof left === 'number' && typeof right === 'number' ? left - right : String(left).localeCompare(String(right))) * (view.sortDirection === 'desc' ? -1 : 1);
  });
  return <div className={styles.supervisor}>
    <div className={styles.toolbar}><label>Sort sessions<select value={view.sortKey} onChange={(event) => update({ sortKey: event.target.value })}>{sortFields.map((key) => <option key={key} value={key}>{key.replaceAll('_', ' ')}</option>)}</select></label><Button onPress={() => update({ sortDirection: view.sortDirection === 'asc' ? 'desc' : 'asc' })}>{view.sortDirection === 'asc' ? 'Ascending' : 'Descending'}</Button><label><input type="checkbox" checked={view.autoRefresh} onChange={(event) => update({ autoRefresh: event.target.checked })} />Auto-refresh sessions</label><Button onPress={() => setRefresh((value) => value + 1)}>Refresh sessions</Button><span>{updated ? `Updated ${updated}` : 'Loading sessions…'}</span></div>
    {error ? <p role="alert">{error}</p> : null}
    <div ref={viewport} className={styles.sessionList} onScroll={(event) => { const scrollPos = event.currentTarget.scrollTop; clearTimeout(scrollTimer.current); scrollTimer.current = setTimeout(() => update({ scrollPos }), 250); }}>
      {sorted.map((session) => {
        const id = text(session.session_id, text(session.id)); const owner = record(session.owner); const name = text(owner.name, text(session.agent_name, text(session.name, id)));
        const self = session.row_type === 'supervisor' || session.is_supervisor === true || session.session_id === '__supervisor__' || session.cell_id === '__supervisor__' || session.kind === 'supervisor' || name === '__supervisor__';
        const fields: [string, unknown][] = [['Session', id], ['Cell', session.cell_id], ['Owner', name], ['Group', owner.group], ['State', session.alive ? 'alive' : 'exited'], ['PID', session.pid], ['Started', stamp(session.started_at)], ['Command', session.display_command ?? session.command], ['Path', session.current_path ?? session.cwd], ['PTY argv', Array.isArray(session.shell_argv) ? session.shell_argv.join(' ') : '—'], ['Bootstrap directory', session.bootstrap_dir], ['Dimensions', `${text(session.cols)} × ${text(session.rows)}`], ['Bytes', session.total_bytes ?? session.bytes_written ?? session.output_bytes], ['Subscribers', session.subscriber_count ?? session.subscribers], ['Ownership', session.orphan ? 'Orphan or stale mapping' : 'Mapped']];
        return <article key={id}><button aria-expanded={view.expandedSessionId === id} onClick={() => update({ selectedSessionId: id, expandedSessionId: view.expandedSessionId === id ? '' : id })}><strong>{name}</strong> · {text(session.pid)} · {session.alive ? 'alive' : 'exited'} · {text(session.display_command, text(session.current_path))}</button>{view.expandedSessionId === id ? <dl className={styles.facts}>{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{text(value)}</dd></div>)}</dl> : null}{!self && session.can_terminate !== false ? <Button tone="danger" onPress={() => onTerminate(id, name)}>Terminate {name}</Button> : <span>Managed supervisor process</span>}</article>;
      })}
      {!sorted.length && !error ? <p>No live PTY sessions.</p> : null}
    </div>
  </div>;
}

function MetricChart({ label, values, buckets, unit = '' }: { label: string; values: unknown[]; buckets: UnknownRecord[]; unit?: string }) {
  const points = values.map((value, index) => ({ value: typeof value === 'number' && Number.isFinite(value) ? value : null, index }));
  const finite = points.filter((point): point is { value: number; index: number } => point.value !== null);
  const max = Math.max(1, ...finite.map((point) => point.value));
  const paths: string[] = []; let path = '';
  for (const point of points) {
    if (point.value === null) { if (path) paths.push(path); path = ''; continue; }
    path += `${path ? ' L' : 'M'} ${5 + point.index / Math.max(1, points.length - 1) * 290} ${75 - point.value / max * 70}`;
  }
  if (path) paths.push(path);
  return <article className={styles.metricChart}><h3>{label}</h3>{finite.length ? <><strong>{finite.at(-1)?.value.toFixed(1)} {unit}</strong><svg viewBox="0 0 300 80" role="img" aria-label={`${label} history; ${finite.length} samples; latest ${finite.at(-1)?.value} ${unit}`}><title>{label} over time</title>{paths.map((d, index) => <path key={index} d={d} />)}</svg><small>{text(buckets[0]?.label, 'Oldest')} → {text(buckets.at(-1)?.label, 'Latest')}</small><details><summary>Inspect samples</summary><table><thead><tr><th>Time</th><th>{label} {unit}</th></tr></thead><tbody>{points.map((point) => <tr key={point.index}><td>{text(buckets[point.index]?.label, String(point.index + 1))}</td><td>{point.value === null ? 'No sample' : point.value}</td></tr>)}</tbody></table></details></> : <p>No samples reported.</p>}</article>;
}

export function HealthDetails({ group, runtime }: { group: string; runtime: UnknownRecord }) {
  const dispatch = useAppDispatch();
  const scope = useAppSelector((state) => state.workspaceUi.healthScope);
  const windowSize = useAppSelector((state) => state.workspaceUi.healthWindow);
  const [refresh, setRefresh] = useState(0);
  const { current, error, ready } = useHealthHistory(scope === 'all' ? '' : group, windowSize, refresh);
  const perf = record(current?.history.perf); const workflow = record(current?.health.series); const buckets = list(current?.history.buckets);
  const supervisor = record(runtime.supervisor ?? runtime.pty_supervisor);
  const series = (value: unknown): unknown[] => Array.isArray(value) ? value as unknown[] : [];
  const telemetry = [['Event-loop lag', 'event_loop_lag_p95_ms', 'ms'], ['WebSocket throughput', 'ws_deltas_per_s', '/s'], ['DB write latency', 'db_write_latency_p95_ms', 'ms'], ['Process memory', 'rss_mb', 'MB'], ['Process CPU', 'cpu_pct', '%']] as const;
  return <section aria-label="Health history"><HealthLiveMetrics /><header className={styles.toolbar}><h2>System health history</h2><label>Health scope<select value={scope} onChange={(event) => dispatch(workspaceUiActions.setHealthScope(event.target.value as WorkspaceUiState['healthScope']))}><option value="active">Active group</option><option value="all">All groups</option></select></label><label>Health history window<select value={windowSize} onChange={(event) => dispatch(workspaceUiActions.setHealthWindow(event.target.value as WorkspaceUiState['healthWindow']))}><option value="24h">24 hours</option><option value="7d">7 days</option><option value="30d">30 days</option></select></label><Button onPress={() => setRefresh((value) => value + 1)}>Refresh health</Button></header>
    {!ready ? <p>Health history is offline. Previously loaded data remains available.</p> : <p className={styles.healthNote}>History refreshes every minute while this section is open.</p>}
    {error ? <p role="alert">{error}</p> : null}{ready && !current && !error ? <p>Loading health history…</p> : null}
    {current ? <><p>Workflow scope: {text(current.health.scope)} {text(current.health.group, '')} · Performance: daemon-wide · {text(current.history.bucket_seconds)} second buckets</p><div className={styles.charts}>{telemetry.map(([label, field, unit]) => <MetricChart key={field} label={label} values={series(perf[field])} buckets={buckets} unit={unit} />)}{Object.entries(workflow).filter(([, value]) => Array.isArray(value)).map(([label, values]) => <MetricChart key={label} label={label.replaceAll('_', ' ')} values={series(values)} buckets={list(current.health.buckets)} />)}</div><details><summary>Metric coverage and caveats</summary><ul>{[...series(current.health.notes), ...series(current.history.notes)].map((note, index) => <li key={index}>{text(note)}</li>)}</ul></details></> : null}
    <h3>Supervisor health</h3><dl className={styles.facts}>{Object.keys(supervisor).length ? Object.entries(supervisor).filter(([, value]) => value === null || typeof value !== 'object').map(([field, value]) => <div key={field}><dt>{field.replaceAll('_', ' ')}</dt><dd>{text(value)}</dd></div>) : <div><dt>Status</dt><dd>No runtime supervisor health reported.</dd></div>}</dl>
  </section>;
}
