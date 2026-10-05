import type { UnknownRecord } from '../protocol';
export const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
const text = (value: unknown) => typeof value === 'string' ? value : '';
const finite = (value: unknown) => (typeof value === 'number' || typeof value === 'string' && value.trim() !== '') && Number.isFinite(Number(value)) ? Number(value) : NaN;
export const liveAgent = (agent: UnknownRecord) => !agent.deleted_at && !agent.dismissed_at && !agent.tombstoned;
export function workloadStatus(agents: UnknownRecord[], group: string) {
  const counts = { running: 0, idle: 0, error: 0 };
  for (const agent of agents) {
    if (!liveAgent(agent) || agent.cell_type === 'terminal' || group && agent.group !== group) continue;
    const status = text(agent.status).toLowerCase();
    if (status === 'error' || agent.error || agent.error_message) counts.error++;
    else if (['running', 'busy', 'starting'].includes(status)) counts.running++;
    else counts.idle++;
  }
  return { label: counts.running + counts.idle + counts.error ? `Agents ${counts.running} run / ${counts.idle} idle${counts.error ? ` / ${counts.error} err` : ''}` : 'Agents 0', title: `Agent status counts${group ? ` for ${group}` : ''}: ${counts.running} running, ${counts.idle} idle, ${counts.error} error`, tone: counts.error ? 'danger' : counts.running ? 'normal' : 'muted' };
}
export function assignedTaskCount(tasks: UnknownRecord[], group: string) {
  return tasks.filter((task) => (!group || task.group === group) && !task.deleted_at && !task.archived_at && !['done', 'archived'].includes(text(task.lane).toLowerCase()) && text(task.agent_id).trim()).length;
}
const measure = (value: unknown, digits: number, unit: string) => Number.isFinite(finite(value)) && finite(value) >= 0 ? `${finite(value).toFixed(digits)}${unit}` : '—';
export function metricsStatus(value: unknown, enabled: boolean) {
  const tick = record(value);
  if (!enabled || tick.enabled === false) return { label: 'Metrics off', title: 'Runtime metrics collection is disabled.', tone: 'muted' };
  if (!Object.keys(tick).length) return { label: 'Metrics —', title: 'Runtime metrics tick has not arrived yet.', tone: 'muted' };
  const perf = record(tick.perf); const lag = record(perf.event_loop_lag_ms); const proc = record(perf.proc); const frontend = record(perf.frontend);
  const tone = finite(lag.p95) >= 100 || finite(proc.cpu_pct) >= 90 ? 'danger' : finite(lag.p95) >= 50 || finite(proc.cpu_pct) >= 75 ? 'warning' : 'normal';
  return { label: `Lag ${measure(lag.p95, 1, 'ms')} · Mem ${measure(proc.rss_mb, 0, 'MB')}`, title: `Runtime metrics\nEvent-loop lag p95: ${measure(lag.p95, 1, 'ms')}\nRSS: ${measure(proc.rss_mb, 0, 'MB')}\nCPU: ${measure(proc.cpu_pct, 1, '%')}\nFrontend renders: ${measure(frontend.render_per_s, 1, '/s')} · p95 ${measure(frontend.render_ms_p95, 1, 'ms')}`, tone };
}
export function supervisorStatus(value: unknown) {
  const supervisor = record(value); const state = text(supervisor.state);
  const states: Record<string, [string, string]> = { up: ['', 'normal'], degraded: [' degraded', 'warning'], restarting: [' restarting', 'warning'], down: [' down', 'danger'], unavailable: [' unavailable', 'muted'], na_profile: [' n/a', 'muted'] };
  const [suffix, tone] = states[state] ?? [' —', 'muted'];
  const lines = [`Supervisor: ${state === 'na_profile' ? 'not enabled for this profile' : state || 'unknown'}`];
  for (const [label, key] of [['PID', 'supervisor_pid'], ['Sessions', 'session_count'], ['Reconnects', 'reconnect_count']] as const) if (supervisor[key] != null) lines.push(`${label}: ${measure(supervisor[key], 0, '')}`);
  lines.push(`Uptime: ${measure(supervisor.uptime, 0, 's')}`, `Latency: ${measure(supervisor.last_op_latency_ms, 1, 'ms')}`);
  if (typeof supervisor.connected === 'boolean') lines.push(`Connected: ${supervisor.connected ? 'yes' : 'no'}`);
  return { label: `Supervisor${suffix}`, title: lines.join('\n'), tone };
}
function usageWindow(usage: UnknownRecord, keys: string[]) {
  const windows = record(usage.windows ?? usage.limits ?? usage);
  return keys.map((key) => record(windows[key])).find((window) => Object.keys(window).length) ?? {};
}
function quotaWindows(usage: UnknownRecord) { return [['5h', usageWindow(usage, ['five_hour', '5h', 'fiveHour'])], ['7d', usageWindow(usage, ['seven_day', 'weekly', '7d', 'week', 'sevenDay'])]] as const; }
function timestamp(value: unknown) {
  const number = finite(value); if (Number.isFinite(number)) return number > 1e11 ? number : number * 1000;
  return typeof value === 'string' ? Date.parse(value) : NaN;
}
function resetLabel(value: unknown, now: number) {
  const stamp = timestamp(value); if (!Number.isFinite(stamp) || stamp <= 0) return 'reset —';
  if (stamp <= now) return 'reset soon'; const minutes = Math.ceil((stamp - now) / 60_000);
  if (minutes <= 1) return 'resets <1m'; if (minutes < 60) return `resets ${minutes}m`;
  const hours = Math.floor(minutes / 60); if (hours < 24) return `resets ${hours}h${minutes % 60 ? ` ${minutes % 60}m` : ''}`;
  return `resets ${Math.floor(hours / 24)}d${hours % 24 ? ` ${hours % 24}h` : ''}`;
}
export function providerStatus(agents: UnknownRecord[], provider: string, label: string, now: number) {
  let selected: { agent: UnknownRecord; usage: UnknownRecord; freshness: number } | undefined;
  for (const agent of agents) {
    if (!liveAgent(agent)) continue;
    const payload = record(agent.provider_usage); const keyed = record(payload[provider]); const agentProvider = text(agent.agent_type) || text(agent.provider);
    if (!Object.keys(keyed).length && agentProvider && agentProvider !== provider) continue;
    const usage = Object.keys(keyed).length ? keyed : payload;
    if (!quotaWindows(usage).some(([, window]) => window.available === true)) continue;
    const freshness = Math.max(0, ...['last_heartbeat_at', 'last_activity_at', 'last_event_at', 'last_progress_at'].map((key) => finite(agent[key]) || 0));
    if (!selected || freshness >= selected.freshness) selected = { agent, usage, freshness };
  }
  if (!selected) return { label: `${label} —`, title: `${label} 5h and weekly usage limits are unavailable until an agent reports available provider usage.`, tone: 'muted' };
  const parts: string[] = []; const lines = [`${label} account usage limits`, `Source agent: ${text(selected.agent.name) || text(selected.agent.id)}`, 'Account-wide quota shared by all provider agents; one source is selected, not summed.']; let maxUsed = 0;
  for (const [name, window] of quotaWindows(selected.usage)) {
    if (window.available !== true) continue;
    let used = [window.used_percentage, window.usedPercentage, window.used_pct].map(finite).find(Number.isFinite);
    if (used === undefined && Number.isFinite(finite(window.remaining_pct))) used = 100 - finite(window.remaining_pct);
    if (used === undefined) continue; used = Math.max(0, Math.min(100, used)); maxUsed = Math.max(maxUsed, used);
    parts.push(`${name} ${Math.round(used)}%`); lines.push(`${name}: used ${Math.round(used)}% · remaining ${Math.round(100 - used)}% · ${resetLabel(window.resets_at ?? window.reset_at ?? window.resetsAt, now)}`);
  }
  return { label: parts.length ? `${label} ${parts.join(' · ')}` : `${label} —`, title: lines.join('\n'), tone: !parts.length ? 'muted' : maxUsed >= 90 ? 'danger' : maxUsed >= 70 ? 'warning' : 'normal' };
}
