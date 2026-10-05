import { expect, it } from 'vitest';
import { metricsStatus, providerStatus, supervisorStatus } from './workspaceStatusModel';
it('keeps unavailable quotas unknown, supports compatible windows and excludes deleted newer reports', () => {
  const source = { id: 'source', provider: 'codex', provider_usage: { limits: { '5h': { available: true, remaining_pct: 25, resetsAt: '2026-09-30T13:00:00Z' }, weekly: { available: false, used_percentage: 80 } } }, last_event_at: 10 };
  const unavailable = { provider: 'codex', provider_usage: { five_hour: { available: false, used_percentage: 40 } }, last_event_at: 30 };
  const deleted = { ...source, deleted_at: 20, last_event_at: 40 };
  const view = providerStatus([source, unavailable, deleted], 'codex', 'Codex', Date.parse('2026-09-30T12:00:00Z'));
  expect(view).toMatchObject({ label: 'Codex 5h 75%', tone: 'warning' }); expect(view.title).toContain('resets 1h'); expect(view.title).toContain('remaining 25%');
  expect(providerStatus([unavailable], 'codex', 'Codex', 0).label).toBe('Codex —');
  expect(providerStatus([{ ...source, provider_usage: { five_hour: { available: true, used_percentage: null } } }], 'codex', 'Codex', 0)).toMatchObject({ label: 'Codex —', tone: 'muted' });
  expect(providerStatus([source], 'claude-code', 'Claude', 0).label).toBe('Claude —');
  expect(providerStatus([source], 'codex', 'Codex', Date.parse('2026-09-30T13:00:00Z')).title).toContain('reset soon');
});
it('reports metrics unknown/off states and supervisor danger or unavailable without inventing healthy values', () => {
  expect(metricsStatus(undefined, true).label).toBe('Metrics —'); expect(metricsStatus({ perf: { proc: { cpu_pct: 99 } } }, false).label).toBe('Metrics off');
  expect(metricsStatus({ perf: { event_loop_lag_ms: { p95: 100 }, proc: { rss_mb: null } } }, true)).toMatchObject({ label: 'Lag 100.0ms · Mem —', tone: 'danger' });
  expect(supervisorStatus({ state: 'down' })).toMatchObject({ label: 'Supervisor down', tone: 'danger' }); expect(supervisorStatus({ state: 'na_profile' }).title).toContain('not enabled'); expect(supervisorStatus({}).label).toBe('Supervisor —');
});
