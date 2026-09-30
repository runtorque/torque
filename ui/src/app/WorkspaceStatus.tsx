import { useEffect, useState } from 'react';
import { useAppSelector } from './hooks';
import { selectAgentsState, selectAuxiliaryResponseState, selectConnection, selectEventDismissals, selectOperationsState, selectRuntime, selectTasksState } from './store';
import { attentionDismissed, attentionTimestamp, isOpenAsk, text } from '../features/attention/model';
import { RelayStatusIndicator } from '../features/relay/RelayStatus';
import { statusVisibilityEnabled } from '../features/relay/relayStatusModel';
import { DeployStatus } from './DeployStatus';
import { assignedTaskCount, liveAgent, metricsStatus, providerStatus, record, supervisorStatus, workloadStatus } from './workspaceStatusModel';
import type { UnknownRecord } from '../protocol';
import styles from './App.module.css';
function ProviderStatus({ agents, provider, label }: { agents: UnknownRecord[]; provider: string; label: string }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const refresh = () => { if (!document.hidden) setNow(Date.now()); }; const timer = setInterval(refresh, 60_000);
    document.addEventListener('visibilitychange', refresh);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', refresh); };
  }, []);
  const view = providerStatus(agents, provider, label, now);
  return <span title={view.title} data-tone={view.tone} tabIndex={0}>{view.label}</span>;
}
function MetricsStatus({ enabled, onOpen }: { enabled: boolean; onOpen: () => void }) {
  const tick = useAppSelector((state) => selectAuxiliaryResponseState(state)['metrics_tick:latest']); const view = metricsStatus(tick, enabled);
  return <button title={view.title} data-tone={view.tone} onClick={onOpen}>{view.label}</button>;
}
export function WorkspaceStatus({ group, onBoard, onEvents, onHealth }: { group: string; onBoard: () => void; onEvents: () => void; onHealth: () => void }) {
  const connection = useAppSelector(selectConnection); const runtime = useAppSelector(selectRuntime); const operations = useAppSelector(selectOperationsState);
  const agents = Object.values(useAppSelector(selectAgentsState).records).map(record); const tasks = Object.values(useAppSelector(selectTasksState).records).map(record); const dismissed = useAppSelector(selectEventDismissals);
  const preferences = { daemon_status: false, claude_usage: false, codex_usage: false, deploy: true, health: false, workload: false, tasks: true, attention: true, ...record(operations.globalSettings.status_bar_visibility) };
  const visible = (key: keyof typeof preferences) => statusVisibilityEnabled(preferences[key]);
  const workload = visible('workload') ? workloadStatus(agents, group) : null; const supervisor = supervisorStatus(runtime.supervisor);
  const taskCount = visible('tasks') ? assignedTaskCount(tasks, group) : 0;
  const attention = visible('attention') ? tasks.filter((task) => isOpenAsk(task) && (!group || task.group === group) && !attentionDismissed(dismissed, text(task.id), attentionTimestamp(task, true))).length + agents.filter((agent) => liveAgent(agent) && agent.cell_type === 'agent' && agent.needs_attention && (!group || agent.group === group) && !attentionDismissed(dismissed, text(agent.id), attentionTimestamp(agent, false))).length : 0;
  return <footer className={styles.statusBar} aria-label="Workspace status">
    {visible('daemon_status') ? <><span data-state={connection.status}>● Daemon {connection.status}</span><span title={supervisor.title} data-tone={supervisor.tone} tabIndex={0}>{supervisor.label}</span></> : null}
    <RelayStatusIndicator connection={operations.relayConnection} visible={preferences.daemon_status} />
    <DeployStatus group={group} enabled={visible('deploy')} ready={connection.status === 'connected' && connection.expectedSeq !== null && !connection.awaitingResync} reconnect={connection.reconnectCount} onOpen={onBoard} />
    {visible('health') ? <MetricsStatus enabled={operations.globalSettings.metrics_enabled !== false} onOpen={onHealth} /> : null}
    {workload ? <span title={workload.title} data-tone={workload.tone} tabIndex={0}>{workload.label}</span> : null}
    {visible('tasks') ? <button title={`In-flight tasks assigned to an agent in ${group}: ${taskCount}`} onClick={onBoard}>Tasks {taskCount} active</button> : null}
    {visible('attention') ? <button title={`Pending asks and undismissed attention items in ${group}: ${attention}`} onClick={onEvents}>Attention {attention}</button> : null}
    {visible('claude_usage') ? <ProviderStatus agents={agents} provider="claude-code" label="Claude" /> : null}
    {visible('codex_usage') ? <ProviderStatus agents={agents} provider="codex" label="Codex" /> : null}
  </footer>;
}
