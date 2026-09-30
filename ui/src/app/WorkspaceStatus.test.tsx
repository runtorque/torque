import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { WorkspaceShell } from './App';
import { browserHost } from '../host';
import { compactStateFixture } from '../protocol/fixtures';
import type { StateFrame, UnknownRecord } from '../protocol';
import { connectionActions, createAppStore, projectionActions } from './store';
afterEach(() => vi.unstubAllGlobals());
const agent = (id: string, extra: UnknownRecord = {}) => ({ id, name: id, group: 'Foundation', cell_type: 'agent', kind: 'worker', status: 'idle', ...extra });
const task = (id: string, extra: UnknownRecord = {}) => ({ id, task: id, group: 'Foundation', lane: 'Backlog', ...extra });
function mount(extra: Partial<StateFrame> = {}) {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
  const store = createAppStore(); const snapshot: StateFrame = { ...compactStateFixture, agents: {}, board_tasks: {}, global_settings: { status_bar_visibility: { daemon_status: true, workload: true, tasks: true, attention: true, claude_usage: true, codex_usage: true, health: true, deploy: false } }, ...extra };
  store.dispatch(projectionActions.snapshotReceived(snapshot)); store.dispatch(connectionActions.connected({ at: 1000, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(snapshot));
  render(<Provider store={store}><WorkspaceShell host={browserHost} sendCommand={() => true} /></Provider>); return { store, footer: within(screen.getByLabelText('Workspace status')) };
}
it('counts real active agents as running idle or error, excluding terminals and dismissed cells', () => {
  const { footer } = mount({ agents: { start: agent('start', { status: 'starting' }), idle: agent('idle'), error: agent('error', { status: 'running', error_message: 'Failed' }), terminal: agent('terminal', { cell_type: 'terminal', status: 'running' }), dismissed: agent('dismissed', { dismissed_at: 5 }), other: agent('other', { group: 'Elsewhere' }) } });
  expect(footer.getByText('Agents 1 run / 1 idle / 1 err')).toBeVisible();
});
it('counts only in-flight assigned tasks and provides the Board shortcut', () => {
  const { footer, store } = mount({ board_tasks: { assigned: task('assigned', { agent_id: 'worker' }), backlog: task('backlog'), done: task('done', { agent_id: 'worker', lane: 'Done' }), deleted: task('deleted', { agent_id: 'worker', deleted_at: 5 }), archived: task('archived', { agent_id: 'worker', archived_at: 5 }) } });
  fireEvent.click(footer.getByRole('button', { name: 'Tasks 1 active' })); expect(store.getState().workspaceUi.activePanel).toBe('board');
});
it('counts undismissed asks and agent alerts independently of unread notices and opens Events', () => {
  const { footer, store } = mount({ agents: { alert: agent('alert', { needs_attention: true, last_event_at: 20 }), old: agent('old', { needs_attention: true, last_event_at: 10 }) }, board_tasks: { ask: task('ask', { labels: ['torque:human'], created_at: '2026-09-30T10:00:00Z' }), internal: task('internal', { labels: ['torque:human', 'torque:non-user-ask'] }) }, events_dismissed_attention: { old: 10 }, operator_notice_summary: { unread_total: 99 } });
  fireEvent.click(footer.getByRole('button', { name: 'Attention 2' })); expect(store.getState().workspaceUi.controlTab).toBe('activity');
});
it('uses freshest available account quotas for both providers, with reset and source details', () => {
  const usage = (used: number) => ({ five_hour: { available: true, used_percentage: used, resets_at: Date.now() / 1000 + 3600 }, seven_day: { available: true, used_percentage: 35 } });
  const { footer } = mount({ agents: { old: agent('old', { agent_type: 'codex', provider_usage: usage(5), last_event_at: 10 }), current: agent('current', { group: 'Elsewhere', agent_type: 'codex', provider_usage: usage(91), last_event_at: 20 }), unavailable: agent('unavailable', { agent_type: 'codex', provider_usage: { five_hour: { available: false, used_percentage: 1 } }, last_event_at: 30 }), claude: agent('claude', { agent_type: 'claude-code', provider_usage: usage(70) }) } });
  const codex = footer.getByText('Codex 5h 91% · 7d 35%'); expect(codex).toHaveAttribute('title', expect.stringContaining('current')); expect(codex).toHaveAttribute('title', expect.stringContaining('remaining 9%')); expect(codex).toHaveAttribute('data-tone', 'danger'); expect(footer.getByText('Claude 5h 70% · 7d 35%')).toBeVisible();
});
it('renders live runtime metrics with a Health shortcut and an explicit disabled state', () => {
  const { footer, store } = mount();
  act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'metrics_tick', enabled: true, perf: { event_loop_lag_ms: { p95: 60 }, proc: { rss_mb: 123, cpu_pct: 80 }, frontend: { render_per_s: 4, render_ms_p95: 3 } } })); });
  const chip = footer.getByRole('button', { name: 'Lag 60.0ms · Mem 123MB' }); expect(chip).toHaveAttribute('title', expect.stringContaining('CPU: 80.0%')); fireEvent.click(chip); expect(store.getState().workspaceUi.controlTab).toBe('mission');
  act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'metrics_tick', enabled: false })); }); expect(footer.getByRole('button', { name: 'Metrics off' })).toBeVisible();
});
it('shows supervisor health independently from the daemon connection', () => {
  const { footer } = mount({ runtime: { ...compactStateFixture.runtime as UnknownRecord, supervisor: { state: 'degraded', connected: false, session_count: 3, last_op_latency_ms: 25, reconnect_count: 4, supervisor_pid: 123 } } });
  const chip = footer.getByText('Supervisor degraded'); expect(chip).toHaveAttribute('title', expect.stringContaining('Sessions: 3')); expect(chip).toHaveAttribute('title', expect.stringContaining('Reconnects: 4'));
});
