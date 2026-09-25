import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createAppStore, connectionActions, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { MissionSummary } from './MissionSummary';
import { missionRequestTimeout } from './useMissionControl';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
function summary(group = 'A', suffix = ''): AuxiliaryFrame {
  return { type: 'mission_control_summary', group, generated_at: '2026-09-25T18:00:00Z', sections: { needs_operator_now: { count: 30, truncated: true, items: [{ id: `${group}-gate`, title: `Release ${group}${suffix}`, kind: 'gate', severity: 'high', gate: 'approval', owner: { agent_name: 'Ada', agent_id: 'a1' }, ref: { kind: 'task', id: 't1' }, primary_task_id: 't1', reason: 'Verify the release', recommended_next_action: 'review_evidence', evidence_chips: ['verified'], caveat_chips: ['provider pending'], timestamps: { updated_at: 1700000000 }, deep_links: [{ surface: 'board', kind: 'task', task_id: 't1' }] }] }, at_risk_watchlist: { count: 0, items: [] }, in_flight: { count: 1, items: [{ id: `${group}-running`, title: `Build ${group}`, kind: 'task' }] }, recently_completed: { count: 0, items: [] } }, source_freshness: { tasks: { state: 'ok', count: 31 }, provider: { state: 'error', error: 'Fixture provider unavailable' } } };
}
beforeEach(() => { read.mockReset(); });
afterEach(() => { vi.useRealTimers(); });
function setup() {
  const calls: { command: TorqueCommand; signal: AbortSignal; resolve: (frame: AuxiliaryFrame) => void; reject: (error: Error) => void }[] = [];
  read.mockImplementation((command, signal) => new Promise((resolve, reject) => calls.push({ command, signal, resolve, reject })));
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const onTask = vi.fn(); const onAgent = vi.fn();
  const content = (group = 'A', visible = true) => <Provider store={store}>{visible ? <MissionSummary key={group} group={group} onOpenTask={onTask} onOpenAgent={onAgent} /> : null}</Provider>;
  const view = render(content());
  const resolve = async (index: number, frame: AuxiliaryFrame) => { await act(async () => { calls[index]!.resolve(frame); await Promise.resolve(); }); };
  return { ...view, calls, store, onTask, onAgent, resolve, show: (group = 'A', visible = true) => view.rerender(content(group, visible)) };
}
const refresh = () => fireEvent.click(screen.getByRole('button', { name: 'Refresh Mission Control summary' }));
it('distinguishes loading from an empty result, validates group and allows explicit retry', async () => {
  const test = setup(); expect(screen.getByRole('status')).toHaveTextContent('Loading Mission Control'); expect(screen.queryByText('No active risks in this group.')).not.toBeInTheDocument();
  expect(test.calls[0]?.command).toEqual({ cmd: 'get_mission_control', group: 'A', limit_per_section: 20, include_recent_completed: true }); await test.resolve(0, summary('B'));
  expect(screen.getByRole('alert')).toHaveTextContent('wrong scope'); expect(screen.queryByText('Release B')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry Mission Control' })); await test.resolve(1, summary()); expect(screen.getByText('Release A')).toBeVisible();
});
it('ignores aborted responses after group changes and shared unowned cached summaries', async () => {
  const test = setup(); test.show('B'); expect(test.calls[0]?.signal.aborted).toBe(true); await test.resolve(1, summary('B')); await test.resolve(0, summary('A'));
  act(() => { test.store.dispatch(projectionActions.auxiliaryResourceReceived(summary('C'))); }); expect(screen.getByText('Release B')).toBeVisible(); expect(screen.queryByText('Release A')).not.toBeInTheDocument(); expect(screen.queryByText('Release C')).not.toBeInTheDocument();
});
it('retains accepted content, selected ID, search DOM/focus and disclosure through failed refresh and reconnect', async () => {
  const test = setup(); await test.resolve(0, summary()); fireEvent.click(screen.getByRole('button', { name: 'Inspect Release A' })); fireEvent.click(screen.getByRole('button', { name: 'Toggle In flight' }));
  const search = screen.getByRole('textbox', { name: 'Search Mission Control' }); fireEvent.change(search, { target: { value: 'Release' } }); search.focus(); (search as HTMLInputElement).setSelectionRange(1, 4);
  act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(screen.getByText('Refreshing Mission Control…')).toBeVisible(); await act(async () => { test.calls[1]!.reject(new Error('Refresh refused')); await Promise.resolve(); });
  expect(screen.getByRole('alert')).toHaveTextContent('last accepted summary is retained'); expect(search).toHaveFocus(); expect(screen.getByRole('textbox', { name: 'Search Mission Control' })).toBe(search); expect((search as HTMLInputElement).selectionStart).toBe(1);
  fireEvent.click(screen.getByRole('button', { name: 'Retry Mission Control' })); await test.resolve(2, summary('A', ' revised'));
  expect(screen.getByRole('button', { name: 'Inspect Release A revised' })).toHaveAttribute('aria-expanded', 'true'); expect(screen.getByRole('button', { name: 'Toggle In flight' })).toHaveAttribute('aria-expanded', 'false'); expect(within(screen.getByRole('complementary', { name: 'Mission Control card details' })).getByText('Release A revised')).toBeVisible();
});
it('retains session view through close/reopen, makes no hidden reads and rejects late hidden results', async () => {
  const test = setup(); await test.resolve(0, summary()); fireEvent.click(screen.getByRole('button', { name: 'Inspect Release A' })); fireEvent.click(screen.getByRole('button', { name: 'Toggle In flight' })); refresh(); test.show('A', false); expect(test.calls[1]?.signal.aborted).toBe(true);
  act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(test.calls).toHaveLength(2); await test.resolve(1, summary('A', ' stale')); test.show();
  expect(screen.getByRole('button', { name: 'Inspect Release A' })).toHaveAttribute('aria-expanded', 'true'); expect(screen.getByRole('button', { name: 'Toggle In flight' })).toHaveAttribute('aria-expanded', 'false'); await test.resolve(2, summary());
});
it('shows reported totals and truncation, filter-empty text and readable context without JSON', async () => {
  const test = setup(); await test.resolve(0, summary()); expect(screen.getByText('31 cards')).toBeVisible(); expect(screen.getByText('Truncated')).toBeVisible();
  const card = screen.getByRole('article', { name: 'Release A' }); for (const value of ['Ada', 'approval', 'task:t1', 'review evidence', 'verified', 'provider pending', 'board / task / t1']) expect(within(card).getByText(value)).toBeVisible(); expect(card.querySelector('time')).toHaveAttribute('datetime', '2023-11-14T22:13:20.000Z'); expect(card.querySelector('pre')).toBeNull();
  expect(screen.getByText('Fixture provider unavailable')).toBeVisible(); fireEvent.click(within(card).getByRole('button', { name: 'Open task' })); expect(test.onTask).toHaveBeenCalledWith('t1'); fireEvent.click(within(card).getByRole('button', { name: 'Open agent' })); expect(test.onAgent).toHaveBeenCalledWith('a1');
  fireEvent.change(screen.getByRole('textbox', { name: 'Search Mission Control' }), { target: { value: 'not found' } }); expect(screen.getAllByText('No cards match the current filter. Clear the filter to restore all cards.')).toHaveLength(4); expect(screen.getByText('31 cards')).toBeVisible();
});
it('guards pending dismissal, retains refusal for retry, then removes the card and clears selection', async () => {
  const test = setup(); await test.resolve(0, summary()); fireEvent.click(screen.getByRole('button', { name: 'Inspect Release A' })); const button = screen.getByRole('button', { name: 'Dismiss Release A' }); button.focus(); fireEvent.click(button); fireEvent.click(button);
  expect(test.calls).toHaveLength(2); expect(button).toBeDisabled(); expect(test.calls[1]?.command).toMatchObject({ cmd: 'mission_control_dismiss', id: 'A-gate' });
  await test.resolve(1, { type: 'error', message: 'Dismiss refused' }); expect(screen.getByRole('alert')).toHaveTextContent('Dismiss refused'); expect(button).toBeEnabled(); fireEvent.click(button); await test.resolve(2, { type: 'ok' });
  expect(screen.queryByRole('article', { name: 'Release A' })).not.toBeInTheDocument(); expect(screen.getByText('30 cards')).toBeVisible(); expect(screen.getByRole('heading', { name: 'Read-only detail' })).toBeVisible(); expect(screen.getByRole('button', { name: 'Toggle Needs operator now' })).toHaveFocus();
  refresh(); await test.resolve(3, summary()); expect(screen.queryByRole('article', { name: 'Release A' })).not.toBeInTheDocument();
});
it('consumes another client dismissal delta and does not double-subtract refreshed server totals', async () => {
  const test = setup(); await test.resolve(0, summary()); fireEvent.click(screen.getByRole('button', { name: 'Inspect Release A' }));
  act(() => { test.store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: test.store.getState().projection.seq + 1, ops: [{ op: 'ui_update', key: 'mission_control_dismissed_cards', value: { 'A-gate': 123 } }] })); });
  expect(screen.queryByRole('article', { name: 'Release A' })).not.toBeInTheDocument(); expect(test.store.getState().missionSession.selectedId).toBe(''); expect(screen.getByText('30 cards')).toBeVisible();
  refresh(); const next = summary(); (next.sections as Record<string, unknown>).needs_operator_now = { count: 29, items: [], truncated: true }; await test.resolve(1, next); expect(screen.getByText('30 cards')).toBeVisible();
});
it('bounds pending reads and ignores a timed-out result after an explicit retry', async () => {
  vi.useFakeTimers(); const test = setup(); await act(async () => { vi.advanceTimersByTime(missionRequestTimeout); await Promise.resolve(); }); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(test.calls[0]?.signal.aborted).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Retry Mission Control' })); await test.resolve(0, summary('A', ' stale')); expect(screen.queryByText('Release A stale')).not.toBeInTheDocument(); await test.resolve(1, summary()); expect(screen.getByText('Release A')).toBeVisible();
});
it('reports uncertain dismissal on timeout, never replays it on reconnect and ignores late acknowledgements', async () => {
  vi.useFakeTimers(); const test = setup(); await test.resolve(0, summary()); fireEvent.click(screen.getByRole('button', { name: 'Dismiss Release A' })); await act(async () => { vi.advanceTimersByTime(missionRequestTimeout); await Promise.resolve(); }); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown');
  act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(test.calls.filter((call) => call.command.cmd === 'mission_control_dismiss')).toHaveLength(1); await test.resolve(1, { type: 'ok' }); await test.resolve(2, summary()); expect(screen.getByRole('article', { name: 'Release A' })).toBeVisible();
});

it.each([{ type: 'ok' }, { type: 'mission_control_summary', group: 'A', sections: [] }, { type: 'mission_control_summary', group: 'A', sections: { in_flight: { items: [{}] } } }])('rejects malformed summaries without rendering a false empty state: %j', async (frame) => {
  const test = setup(); await test.resolve(0, frame); expect(screen.getByRole('alert')).toBeVisible(); expect(screen.queryByText('No healthy in-flight work to show.')).not.toBeInTheDocument();
});
it('aborts hidden dismissal observation and does not accept its late acknowledgement on reopening', async () => {
  const test = setup(); await test.resolve(0, summary()); fireEvent.click(screen.getByRole('button', { name: 'Dismiss Release A' })); test.show('A', false); expect(test.calls[1]?.signal.aborted).toBe(true); await test.resolve(1, { type: 'ok' }); test.show(); await test.resolve(2, summary()); expect(screen.getByRole('article', { name: 'Release A' })).toBeVisible(); expect(test.calls.filter((call) => call.command.cmd === 'mission_control_dismiss')).toHaveLength(1);
});
