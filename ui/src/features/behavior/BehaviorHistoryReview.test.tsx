import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createAppStore, connectionActions } from '../../app/store';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { BehaviorHistoryReview } from './BehaviorHistoryReview';
import { BehaviorVersionHistory } from './BehaviorVersionHistory';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
beforeEach(() => { vi.mocked(readCommand).mockReset(); });
afterEach(() => { vi.useRealTimers(); });
const scope = { group: 'A', kind: 'role' as const, target: 'worker' }; const scoped = { scope_kind: 'role', scope_group: 'A', scope_key: 'worker' };
const version = (id: string) => ({ ...scoped, id, version_number: id === 'v1' ? 1 : 2, author_kind: 'architect', author_agent_id: 'architect-12', approver_kind: 'user', approver_id: 'operator-34', created_at: 1_700_000_000, text_bytes: 12, text_sha256: 'abc123'.repeat(10), rationale: 'Retain focused evidence', parent_version_id: 'seed-version', source_proposal_id: 'source-proposal' });
const comparison = (base = 'v2', target = 'v1'): AuxiliaryFrame => ({ type: 'behavior_overlay_diff', from_version: version(base), to_version: version(target), diff: '-Current policy\n+Historical policy' });
const acknowledgement = (): AuxiliaryFrame => ({ type: 'behavior_overlay_proposal', proposal_id: 'rollback1', proposal: { ...scoped, id: 'rollback1', base_version_id: 'v2', target_version_id: 'v1', proposal_type: 'rollback', status: 'proposed', next_actor_kind: 'user' } });
function setup() {
  const calls: { command: TorqueCommand; signal: AbortSignal; resolve: (frame: AuxiliaryFrame) => void; reject: (error: Error) => void }[] = [];
  vi.mocked(readCommand).mockImplementation((command, signal) => new Promise((resolve, reject) => { calls.push({ command, signal, resolve, reject }); }));
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); const refresh = vi.fn(); const close = vi.fn();
  let base = 'v2'; let target = 'v1'; let ready = true; let shown = true;
  const element = () => <Provider store={store}>{shown ? <BehaviorHistoryReview key={target} scope={scope} base={base} target={target} scopeReady={ready} refreshScope={refresh} onClose={close} /> : null}</Provider>;
  const view = render(element());
  const rerender = (next: { base?: string; target?: string; ready?: boolean; shown?: boolean }) => { base = next.base ?? base; target = next.target ?? target; ready = next.ready ?? ready; shown = next.shown ?? shown; view.rerender(element()); };
  const respond = async (index = calls.length - 1, frame = comparison(base, target)) => { await act(async () => { calls[index]!.resolve(frame); await Promise.resolve(); }); };
  const refuse = async (index = calls.length - 1) => { await act(async () => { calls[index]!.reject(new Error('Stale base refused')); await Promise.resolve(); }); };
  return { ...view, store, calls, rerender, respond, refuse, refresh, close };
}
const confirm = () => screen.getByRole('button', { name: 'Confirm rollback request' });
const rationale = () => screen.getByRole('textbox', { name: 'Rollback rationale' });
it('renders readable provenance, active identity and stable expanded history across refresh', () => {
  const inspect = vi.fn(); const first = version('v1'); const second = version('v2'); const view = render(<BehaviorVersionHistory versions={[second, first]} activeId="v2" ready onInspect={inspect} />);
  const history = screen.getByRole('article', { name: 'Version 1' }); fireEvent.click(within(history).getByText('Version provenance')); expect(history.querySelector('details')).toHaveAttribute('open');
  for (const value of ['architect · architect-12', 'user · operator-34', '12 bytes', first.text_sha256, 'seed-version', 'source-proposal', 'Retain focused evidence']) expect(within(history).getByText(value)).toBeVisible();
  expect(history.querySelector('time')).toHaveAttribute('datetime', new Date(first.created_at * 1000).toISOString()); expect(screen.getByText('Version 2 · Active')).toBeVisible();
  view.rerender(<BehaviorVersionHistory versions={[{ ...second }, { ...first }]} activeId="v2" ready onInspect={inspect} />); expect(screen.getByRole('article', { name: 'Version 1' })).toBe(history); expect(history.querySelector('details')).toHaveAttribute('open'); fireEvent.click(within(history).getByRole('button')); expect(inspect).toHaveBeenCalledWith('v1');
});
it('requires the rendered version pair before explicit rollback and never applies it', async () => {
  const test = setup(); expect(confirm()).toBeDisabled(); expect(test.calls[0]!.command).toMatchObject({ cmd: 'behavior_overlay_diff', ...scoped, from_version_id: 'v2', to_version_id: 'v1' }); await test.respond(); expect(screen.getByLabelText('Historical behavior diff')).toHaveTextContent('+Historical policy');
  fireEvent.change(rationale(), { target: { value: 'Restore trusted policy' } }); fireEvent.click(confirm()); fireEvent.click(screen.getByRole('button', { name: 'Requesting rollback…' })); expect(test.calls).toHaveLength(2); expect(test.calls[1]!.command).toMatchObject({ cmd: 'behavior_overlay_propose', ...scoped, proposed_by_kind: 'user', proposed_by_agent_id: 'user', proposal_type: 'rollback', target_version_id: 'v1', expected_base_version_id: 'v2', rationale: 'Restore trusted policy' }); expect(test.calls[1]!.command.auto_apply_architect_direct).toBeUndefined();
  await test.respond(1, acknowledgement()); expect(screen.getByText(/Rollback proposal submitted: rollback1. Next reviewer: user/)).toBeVisible(); expect(confirm()).toBeDisabled(); expect(test.refresh).toHaveBeenCalledOnce();
  test.rerender({ shown: false }); test.rerender({ shown: true }); await test.respond(); expect(confirm()).toBeDisabled(); expect(rationale()).toHaveValue('Restore trusted policy'); expect(test.calls.filter((call) => call.command.cmd === 'behavior_overlay_propose')).toHaveLength(1);
});
it.each([
  { type: 'ok' },
  { ...comparison(), from_version: version('wrong') },
  { ...comparison(), to_version: { ...version('v1'), scope_group: 'B' } },
])('rejects mismatched history replies before mutation: %j', async (frame) => {
  const test = setup(); await test.respond(0, frame); expect(confirm()).toBeDisabled(); expect(screen.getByRole('alert')).toHaveTextContent('did not match'); fireEvent.click(screen.getByRole('button', { name: 'Reload version comparison' })); await test.respond(); expect(confirm()).toBeEnabled();
});
it('retains comparison and rationale/caret through reconnect and requires the refreshed base', async () => {
  const test = setup(); await test.respond(); fireEvent.change(rationale(), { target: { value: 'Keep this rationale' } }); const input = rationale() as HTMLTextAreaElement; input.focus(); input.setSelectionRange(1, 6);
  act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(confirm()).toBeDisabled(); expect(screen.getByLabelText('Historical behavior diff')).toHaveTextContent('Historical policy'); await test.respond(); expect(rationale()).toBe(input); expect(input).toHaveFocus(); expect([input.selectionStart, input.selectionEnd]).toEqual([1, 6]);
  test.rerender({ ready: false }); expect(confirm()).toBeDisabled(); const count = test.calls.length; test.rerender({ base: 'v3', ready: true }); expect(test.calls).toHaveLength(count + 1); await test.respond(); fireEvent.click(confirm()); expect(test.calls.at(-1)!.command.expected_base_version_id).toBe('v3'); expect(test.calls.at(-1)!.command.rationale).toBe('Keep this rationale');
});
it('bounds stalled comparisons, cancels old pairs, and ignores late replies after retry', async () => {
  vi.useFakeTimers(); const test = setup(); await act(async () => { await vi.advanceTimersByTimeAsync(15_001); }); expect(screen.getByRole('alert')).toHaveTextContent('refresh timed out'); fireEvent.click(screen.getByRole('button', { name: 'Reload version comparison' })); await test.respond(1); await test.respond(0, { ...comparison(), diff: 'Late stale result' }); expect(screen.getByLabelText('Historical behavior diff')).not.toHaveTextContent('Late stale result');
  test.rerender({ base: 'v3' }); const old = test.calls.length - 1; test.rerender({ target: 'v4' }); expect(test.calls[old]!.signal.aborted).toBe(true); await test.respond(); await test.respond(old, comparison('v3', 'v1')); expect(screen.getByText('Selected version: v4')).toBeVisible();
});
it('retains refusal rationale and the explicit retry token across closing and reopening', async () => {
  const test = setup(); await test.respond(); fireEvent.change(rationale(), { target: { value: 'Retry evidence' } }); fireEvent.click(confirm()); const original = test.calls[1]!.command; await test.refuse(1); expect(screen.getByRole('alert')).toHaveTextContent('Stale base refused'); expect(confirm()).toBeDisabled(); await test.respond(); test.rerender({ shown: false }); test.rerender({ shown: true }); await test.respond(); expect(rationale()).toHaveValue('Retry evidence'); fireEvent.click(confirm()); expect(test.calls.at(-1)!.command.idempotency_key).toBe(original.idempotency_key);
});
it('does not replay a timed-out rollback on reconnect and ignores a late acknowledgement', async () => {
  const test = setup(); await test.respond(); vi.useFakeTimers(); fireEvent.click(confirm()); const original = test.calls[1]!.command; await act(async () => { await vi.advanceTimersByTimeAsync(30_001); }); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); await test.respond(); act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await test.respond(); await test.respond(1, acknowledgement()); expect(screen.queryByText(/Rollback proposal submitted/)).not.toBeInTheDocument(); expect(test.calls.filter((call) => call.command.cmd === 'behavior_overlay_propose')).toHaveLength(1); fireEvent.click(confirm()); expect(test.calls.at(-1)!.command.idempotency_key).toBe(original.idempotency_key);
});
it('ends mutation observation on leaving and preserves retry identity without accepting a late result', async () => {
  const test = setup(); await test.respond(); fireEvent.click(confirm()); const original = test.calls[1]!.command; test.rerender({ shown: false }); expect(test.calls[1]!.signal.aborted).toBe(true); await test.respond(1, acknowledgement()); expect(test.refresh).not.toHaveBeenCalled(); test.rerender({ shown: true }); await test.respond(); fireEvent.click(confirm()); expect(test.calls.at(-1)!.command.idempotency_key).toBe(original.idempotency_key);
});
it('rejects an acknowledgement for another target and requires a fresh comparison', async () => {
  const test = setup(); await test.respond(); fireEvent.click(confirm()); const frame = acknowledgement(); await test.respond(1, { ...frame, proposal: { ...(frame.proposal as object), target_version_id: 'wrong' } }); expect(screen.getByRole('alert')).toHaveTextContent('acknowledgement did not match'); expect(confirm()).toBeDisabled(); await test.respond(); expect(confirm()).toBeEnabled();
});
it('allows active-version inspection without offering a rollback to the same version', async () => {
  const test = setup(); test.rerender({ target: 'v2' }); await test.respond(); expect(screen.queryByRole('button', { name: 'Confirm rollback request' })).not.toBeInTheDocument(); expect(screen.getByText(/This is the active version/)).toBeVisible(); expect(test.calls[0]!.signal.aborted).toBe(true);
});
