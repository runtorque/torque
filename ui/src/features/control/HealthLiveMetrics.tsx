import { useAppSelector } from '../../app/hooks';
import { selectAuxiliaryResponseState, selectOperationsState } from '../../app/store';
import type { UnknownRecord } from '../../protocol';
import styles from './ParityPanels.module.css';

function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
function measure(value: unknown, unit = '', digits = 1) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}` : '—';
}

export function HealthLiveMetrics() {
  const raw = useAppSelector((state) => selectAuxiliaryResponseState(state)['metrics_tick:latest']);
  const connected = useAppSelector((state) => state.connection.status === 'connected');
  const enabled = useAppSelector((state) => selectOperationsState(state).globalSettings.metrics_enabled !== false);
  const tick = record(raw);
  const off = !enabled || tick.enabled === false;
  const perf = off ? {} : record(tick.perf);
  const lag = record(perf.event_loop_lag_ms); const ws = record(perf.ws); const db = record(perf.db);
  const process = record(perf.proc); const live = record(perf.live); const frontend = record(perf.frontend);
  const overhead = off ? {} : record(tick.meter_overhead);
  const cards: { title: string; value: string; fields: [string, string][] }[] = [
    { title: 'Event-loop lag', value: measure(lag.p95, 'ms'), fields: [['p50', measure(lag.p50, 'ms')], ['Maximum', measure(lag.max, 'ms')]] },
    { title: 'WebSocket throughput', value: measure(ws.deltas_per_s, '/s'), fields: [['Bytes', measure(ws.bytes_per_s, 'B/s', 0)], ['Subscribers', measure(ws.subscribers, '', 0)]] },
    { title: 'DB writes', value: measure(db.write_latency_p95_ms, 'ms'), fields: [['Latency', 'p95'], ['Writes', measure(db.writes_per_s, '/s')]] },
    { title: 'Process memory', value: measure(process.rss_mb, 'MB', 0), fields: [['CPU', measure(process.cpu_pct, '%')]] },
    { title: 'Process CPU', value: measure(process.cpu_pct, '%'), fields: [['Memory', measure(process.rss_mb, 'MB', 0)]] },
    { title: 'Live counts', value: measure(live.agents, 'agents', 0), fields: [['PTYs', measure(live.ptys, '', 0)], ['Prompt queue', measure(live.prompt_queue_depth, '', 0)]] },
    { title: 'Supervisor link', value: live.supervisor_connected === true ? 'Connected' : live.supervisor_connected === false ? 'Disconnected' : '—', fields: [['Latency', measure(live.supervisor_latency_ms, 'ms')], ['Stuck sessions', measure(live.stuck_sessions, '', 0)]] },
    { title: 'Frontend renders', value: measure(frontend.render_per_s, '/s'), fields: [['Render duration p95', measure(frontend.render_ms_p95, 'ms')]] },
  ];
  const date = typeof tick.generated_at === 'number' && Number.isFinite(tick.generated_at) ? new Date(tick.generated_at * 1_000) : null;
  const updated = date && Number.isFinite(date.getTime()) ? date.toLocaleTimeString() : '';
  return <section aria-label="Live performance">
    <header className={styles.toolbar}><h2>Live performance</h2><span>{off ? 'Metrics collection is off.' : !connected ? raw ? 'Offline · showing last received metrics.' : 'Offline · waiting for metrics.' : raw ? 'Live metrics' : 'Waiting for live metrics…'}</span>{updated ? <time dateTime={date!.toISOString()}>Updated {updated}</time> : null}</header>
    <p className={styles.healthNote}>Daemon-wide values. Frontend measurements come from recent window reports; frontend history is not retained.</p>
    <div className={styles.charts}>{cards.map((card) => <article key={card.title} aria-label={`${card.title} live`} className={styles.metricChart}><h3>{card.title}</h3><strong>{card.value}</strong><dl className={styles.liveFacts}>{card.fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{card.title === 'Frontend renders' && !off && !Object.keys(frontend).length ? <small>Frontend not reporting</small> : null}</article>)}</div>
    <dl className={styles.facts}><div><dt>Tick interval</dt><dd>{measure(off ? undefined : tick.interval_ms, 'ms', 0)}</dd></div><div><dt>Collector aggregation</dt><dd>{measure(overhead.agg_tick_ms, 'ms', 2)}</dd></div><div><dt>Collection overhead</dt><dd>{measure(overhead.collect_overhead_pct, '%', 2)}</dd></div></dl>
  </section>;
}
