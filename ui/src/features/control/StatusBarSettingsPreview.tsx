import styles from './ControlCenter.module.css';

// Sample values, ordered like the React workspace footer. These deliberately
// do not subscribe to live resources or apply draft visibility to that footer.
const indicators = [
  ['daemon_status', '● Daemon connected'],
  ['daemon_status', 'Supervisor'],
  ['daemon_status', '● Relay connected'],
  ['deploy', 'Deploy +2'],
  ['health', 'Lag 2.0ms · Mem 128MB'],
  ['workload', 'Agents 3 run / 1 idle'],
  ['tasks', 'Tasks 4 active'],
  ['attention', 'Attention 1'],
  ['claude_usage', 'Claude 5h 42% · 7d 20%'],
  ['codex_usage', 'Codex 5h 20% · 7d 10%'],
] as const;

export function StatusBarSettingsPreview({ visibility }: { visibility: Record<string, unknown> }) {
  const selected = indicators.filter(([key]) => visibility[key] === true);
  return <div role="group" aria-label="Status bar preview" className={styles.statusPreview}>
    <small>Preview · Sample values</small>
    {selected.length ? <ul aria-label="Sample status indicators">{selected.map(([, label]) => <li key={label}>{label}</li>)}</ul> : <p>No optional items selected</p>}
  </div>;
}
