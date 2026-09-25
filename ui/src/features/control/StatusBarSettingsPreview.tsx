import styles from './ControlCenter.module.css';

// Sample values, ordered like the React workspace footer. These deliberately
// do not subscribe to live resources or apply draft visibility to that footer.
const indicators = [
  ['daemon_status', '● Daemon connected'],
  ['daemon_status', '● Relay connected'],
  ['deploy', 'Deploy +2'],
  ['health', 'Health good'],
  ['workload', 'Agents 3 run'],
  ['tasks', 'Tasks 4 active'],
  ['attention', 'Attention 1'],
  ['claude_usage', 'Claude 42%'],
  ['codex_usage', 'Codex 20%'],
] as const;

export function StatusBarSettingsPreview({ visibility }: { visibility: Record<string, unknown> }) {
  const selected = indicators.filter(([key]) => visibility[key] === true);
  return <div role="group" aria-label="Status bar preview" className={styles.statusPreview}>
    <small>Preview · Sample values</small>
    {selected.length ? <ul aria-label="Sample status indicators">{selected.map(([, label]) => <li key={label}>{label}</li>)}</ul> : <p>No optional items selected</p>}
  </div>;
}
