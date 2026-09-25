import { relayConnectionView, statusVisibilityEnabled } from './relayStatusModel';
import styles from './RelayStatus.module.css';
function Timestamp({ value }: { value: string }) {
  const date = Date.parse(value); return value ? Number.isFinite(date) ? <time dateTime={new Date(date).toISOString()}>{value}</time> : <>{value}</> : <>—</>;
}
export function RelayStatusIndicator({ connection, visible }: { connection: unknown; visible: unknown }) {
  const view = relayConnectionView(connection);
  if (!view || !view.configured || !statusVisibilityEnabled(visible)) return null;
  return <div className={styles.indicator} data-tone={view.tone} data-state={view.status} role="img" aria-label={view.tooltip} title={view.tooltip} tabIndex={0}><span aria-hidden="true">●</span> Relay</div>;
}
export function RelayConnectionDetails({ connection }: { connection: unknown }) {
  const view = relayConnectionView(connection); if (!view) return null;
  return <section className={styles.panel} aria-label="Relay connection"><header><h4>Relay connection</h4><strong data-tone={view.tone}><span aria-hidden="true">● </span>{view.status}</strong></header>
    {view.escalated ? <p data-tone="danger">Repeated retries need attention.</p> : null}
    <dl><dt>Host</dt><dd>{view.host || '—'}</dd><dt>Daemon ID</dt><dd>{view.daemonId || '—'}</dd><dt>Retry attempts</dt><dd>{view.retryCount}</dd><dt>State since</dt><dd><Timestamp value={view.since} /></dd><dt>Last connected</dt><dd><Timestamp value={view.connected} /></dd><dt>Last error</dt><dd>{view.error || '—'}</dd></dl>
  </section>;
}
