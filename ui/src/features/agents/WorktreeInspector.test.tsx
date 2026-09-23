import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { UnknownRecord } from '../../protocol';
import { toAgentViewModel } from './model';
import { WorktreeInspector } from './WorktreeInspector';
const agent = toAgentViewModel('qa', { id: 'qa', name: 'QA', kind: 'worker', worktree_path: '/tmp/qa', worktree_branch: 'qa' });
const frames: Record<string, UnknownRecord> = {
  worktree_diff_full: { type: 'worktree_diff_full', id: 'qa', files: [{ path: 'keep.txt', hunks: [{ header: '@@ first @@', lines: [{ type: 'add', text: 'retained line' }] }] }] },
  worktree_check_merge: { type: 'worktree_check_merge', id: 'qa', clean: true, default_message: 'Suggested message' },
  worktree_history: { type: 'worktree_history', id: 'qa', commits: [{ sha: 'abc', message: 'Retained checkpoint' }] },
};
function setup() {
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
  const props = { agent, sendCommand: vi.fn(() => true), onUnavailable: vi.fn(), onClose: vi.fn() };
  const element = (responses: Record<string, unknown>, active = true, target = agent) => <Provider store={store}><WorktreeInspector {...props} agent={target} responses={responses} active={active} /></Provider>;
  const cached = Object.fromEntries(Object.entries(frames).map(([key, value]) => [`${key}:qa`, value]));
  const view = render(element(cached)); return { store, ...props, ...view, update: (responses: Record<string, unknown>, active = true, target = agent) => view.rerender(element(responses, active, target)) };
}
function api() {
  let failure = ''; let hold = false; const values = { ...frames }; const pending: (() => void)[] = [];
  const calls: { data: UnknownRecord; signal: AbortSignal }[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
    const data = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as UnknownRecord; calls.push({ data, signal: options.signal as AbortSignal });
    const payload = failure ? { ok: false, error: failure } : { ok: true, data: values[String(data.cmd)] };
    const response = { ok: true, json: () => Promise.resolve(payload) };
    return hold ? new Promise((resolve) => pending.push(() => resolve(response))) : Promise.resolve(response);
  }));
  return { calls, fail: (value: string) => { failure = value; }, frame: (command: string, value: UnknownRecord) => { values[command] = value; }, hold: (value: boolean) => { hold = value; }, release: () => { pending.splice(0).forEach((reply) => reply()); } };
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
it('keeps accepted diff nodes and merge drafts when compact resync clears the auxiliary cache', async () => {
  const requests = api(); const view = setup(); const region = await screen.findByRole('region', { name: 'Worktree diff files' });
  const message = screen.getByRole('textbox', { name: 'Merge message' }); fireEvent.change(message, { target: { value: 'My draft' } }); message.focus(); (message as HTMLTextAreaElement).setSelectionRange(1, 4);
  view.update({});
  expect(screen.getByRole('region', { name: 'Worktree diff files' })).toBe(region); expect(message).toHaveValue('My draft'); expect(message).toHaveFocus();
  await waitFor(() => expect(requests.calls).toHaveLength(3));
  requests.fail('Read refused'); act(() => { view.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); view.store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); });
  await waitFor(() => expect(requests.calls).toHaveLength(6)); await screen.findAllByText(/Read refused/, { exact: false });
  expect(screen.getByRole('region', { name: 'Worktree diff files' })).toBe(region); expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeDisabled();
  requests.fail(''); fireEvent.click(screen.getByRole('button', { name: 'Retry changes' })); await waitFor(() => expect(screen.queryAllByRole('alert')).toHaveLength(0)); expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeEnabled(); expect(requests.calls).toHaveLength(9);
});

it('keeps bounded diff disclosure across tabs, refresh and temporary hiding', async () => {
  const requests = api(); const diff = (count: number) => ({ ...frames.worktree_diff_full, files: [{ path: 'large.txt', hunks: [{ header: '@@ large @@', lines: Array.from({ length: count }, (_, index) => ({ type: 'add', text: `line ${index}` })) }] }] });
  requests.frame('worktree_diff_full', diff(650)); const view = setup(); const region = await screen.findByRole('region');
  fireEvent.click(screen.getByRole('button', { name: 'Show 250 more lines' })); const nav = screen.getByRole('navigation', { name: 'Worktree views' });
  fireEvent.click(within(nav).getByRole('button', { name: /History/ })); expect(region).not.toBeVisible(); fireEvent.click(within(nav).getByRole('button', { name: 'Changes' })); expect(screen.getByRole('region')).toBe(region);
  requests.frame('worktree_diff_full', diff(655)); fireEvent.click(within(nav).getByRole('button', { name: 'Refresh' })); await waitFor(() => expect(region.querySelectorAll('pre span')).toHaveLength(655));
  view.update({}, false); expect(requests.calls.every((call) => call.signal.aborted)).toBe(true); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(requests.calls).toHaveLength(6);
  view.update({}, true); await waitFor(() => expect(requests.calls).toHaveLength(9)); expect((await screen.findByRole('region')).querySelectorAll('pre span')).toHaveLength(655);
});
it('rejects mismatched and malformed reads independently while retaining previous data', async () => {
  const requests = api(); setup(); const region = await screen.findByRole('region');
  requests.frame('worktree_diff_full', { ...frames.worktree_diff_full, id: 'other' }); requests.frame('worktree_history', { ...frames.worktree_history, commits: null }); requests.frame('worktree_check_merge', { ...frames.worktree_check_merge, error: 'Changed boundary' });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); await screen.findByText(/Changed boundary/); await screen.findByText(/response did not match/); await screen.findByText(/response was incomplete/);
  expect(screen.getByRole('region')).toBe(region); expect(screen.getByRole('button', { name: 'Create PR' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /History 1/ })); expect(screen.getByText('Retained checkpoint')).toBeVisible();
});
it('cancels obsolete target reads and ignores replies after replacement or unmount', async () => {
  const requests = api(); requests.hold(true); const view = setup(); expect(requests.calls).toHaveLength(3);
  const next = { ...agent, id: 'second', name: 'Second', worktreePath: '/tmp/second' };
  for (const command of Object.keys(frames)) requests.frame(command, { ...frames[command], id: 'second' });
  requests.hold(false); view.update({}, true, next); await screen.findByRole('region'); expect(requests.calls.slice(0, 3).every((call) => call.signal.aborted)).toBe(true);
  await act(async () => { requests.release(); await Promise.resolve(); }); expect(screen.queryByRole('alert')).not.toBeInTheDocument(); expect(screen.getByRole('dialog', { name: 'Second worktree' })).toBeVisible();
  requests.hold(true); fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); view.unmount(); expect(requests.calls.every((call) => call.signal.aborted)).toBe(true); await act(async () => { requests.release(); await Promise.resolve(); });
});
it('bounds stalled reads and retains accepted content until retry succeeds', async () => {
  const requests = api(); setup(); const region = await screen.findByRole('region'); requests.hold(true); vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' })); await act(async () => { await vi.advanceTimersByTimeAsync(30_001); });
  expect(screen.getAllByRole('alert')).toHaveLength(3); expect(screen.getByRole('region')).toBe(region); expect(requests.calls.slice(-3).every((call) => call.signal.aborted)).toBe(true); vi.useRealTimers();
  requests.hold(false); fireEvent.click(screen.getByRole('button', { name: 'Retry preflight' })); await waitFor(() => expect(screen.queryAllByRole('alert')).toHaveLength(0));
  await act(async () => { requests.release(); await Promise.resolve(); }); expect(screen.queryAllByRole('alert')).toHaveLength(0);
});

it('shows retryable unavailable states after an initial read refusal', async () => {
  const requests = api(); requests.fail('Initial refusal'); setup(); await screen.findByText('Changes unavailable'); expect(screen.getByText('Merge preflight unavailable')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /History 0/ })); expect(screen.getByText('History unavailable')).toBeVisible(); expect(screen.queryByText('Loading history')).not.toBeInTheDocument();
  requests.fail(''); fireEvent.click(screen.getByRole('button', { name: 'Retry history' })); await screen.findByText('Retained checkpoint'); expect(screen.queryAllByRole('alert')).toHaveLength(0);
});
