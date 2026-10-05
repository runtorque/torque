import type { UnknownRecord } from '../../protocol';
export type RelayTone = 'success' | 'warning' | 'danger' | 'muted';
const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
const text = (value: unknown) => typeof value === 'string' ? value : '';
export function statusVisibilityEnabled(value: unknown) { return typeof value === 'string' ? ['1', 'true', 'yes', 'on'].includes(value.trim().toLowerCase()) : Boolean(value); }
export function relayConnectionView(value: unknown) {
  const row = record(value); const status = text(row.status); if (!status) return null;
  const count = Number(row.retry_count); const retryCount = Number.isFinite(count) && count > 0 ? count : 0;
  const escalated = ['connecting', 'disconnected'].includes(status) && retryCount >= 5;
  const tone: RelayTone = status === 'connected' ? 'success' : status === 'error' || escalated ? 'danger' : status === 'disabled' ? 'muted' : 'warning';
  const configured = 'configured' in row ? Boolean(row.configured) : 'enabled' in row ? Boolean(row.enabled) : status !== 'disabled';
  const host = text(row.relay_host); const daemonId = text(row.daemon_id); const since = text(row.since); const connected = text(row.last_connected_at); const error = text(row.last_error);
  const tooltip = [`Relay: ${status}`, host && `Host: ${host}`, connected && `Last connected: ${connected}`, retryCount > 0 && `Retrying, ${retryCount} ${retryCount === 1 ? 'attempt' : 'attempts'}`, since && `Since: ${since}`, error && `Error: ${error}`].filter(Boolean).join('\n');
  return { status, tone, configured, retryCount, escalated, host, daemonId, since, connected, error, tooltip };
}
export function relayProbePresentation(status: string) {
  const known: Record<string, { tone: RelayTone; label: string }> = {
    ok: { tone: 'success', label: 'Connection test passed' },
    reachable_unauthed: { tone: 'warning', label: 'Reachable; authentication not confirmed' },
    disabled: { tone: 'muted', label: 'Relay disabled' },
    ca_missing: { tone: 'danger', label: 'CA certificate unavailable' },
    unreachable: { tone: 'danger', label: 'Relay unreachable' },
    auth_rejected: { tone: 'danger', label: 'Authentication rejected' },
    misconfigured: { tone: 'danger', label: 'Relay misconfigured' },
  };
  return known[status] ?? { tone: 'warning' as const, label: status };
}
