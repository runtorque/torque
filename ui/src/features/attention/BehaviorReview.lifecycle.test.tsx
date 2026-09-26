import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { BehaviorReview } from './BehaviorReview';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
beforeEach(() => { vi.mocked(readCommand).mockReset(); });
afterEach(() => { vi.useRealTimers(); });
const proposal = { id: 'p1', scope_kind: 'role', scope_group: 'A', scope_key: 'worker', status: 'proposed', next_actor_kind: 'user', base_version_id: 'v1', proposed_text_sha256: 'reviewed-hash', rationale: 'Original rationale' };
const diff = (patch: UnknownRecord = {}): AuxiliaryFrame => ({ type: 'behavior_overlay_diff', proposal: { ...proposal, ...patch }, diff: '-old\n+new' });
const ack = (patch: UnknownRecord = {}): AuxiliaryFrame => ({ type: 'behavior_overlay_proposal', proposal_id: 'p1', proposal: { ...proposal, status: 'applied', next_actor_kind: '', ...patch } });
function setup() {
  const calls: { command: TorqueCommand; signal: AbortSignal; resolve: (frame: AuxiliaryFrame) => void; reject: (error: Error) => void }[] = [];
  vi.mocked(readCommand).mockImplementation((command, signal) => new Promise((resolve, reject) => { calls.push({ command, signal, resolve, reject }); }));
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); const dispatch = vi.spyOn(store, 'dispatch'); const close = vi.fn(); let shown = true; let id = 'p1';
  const element = () => <Provider store={store}>{shown ? <BehaviorReview proposalId={id} onClose={close} /> : null}</Provider>;
  const view = render(element()); const rerender = (next: { shown?: boolean; id?: string }) => { shown = next.shown ?? shown; id = next.id ?? id; view.rerender(element()); };
  const respond = async (index = calls.length - 1, frame = diff()) => { await act(async () => { calls[index]!.resolve(frame); await Promise.resolve(); }); };
  return { ...view, store, dispatch, calls, rerender, respond, close };
}
const approve = () => screen.getByRole('button', { name: 'Approve behavior change' });
const reject = () => screen.getByRole('button', { name: 'Reject behavior change' });
const note = () => screen.getByRole('textbox', { name: 'Review note' });
it('bounds the initial read and ignores a late result after explicit retry', async () => {
  vi.useFakeTimers(); const test = setup(); await act(async () => { await vi.advanceTimersByTimeAsync(15_001); }); expect(screen.getByRole('alert')).toHaveTextContent('refresh timed out'); expect(approve()).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: 'Reload diff' })); await test.respond(1, diff({ rationale: 'Fresh rationale' })); await test.respond(0); expect(screen.getByText('Fresh rationale')).toBeVisible(); expect(screen.queryByText('Original rationale')).not.toBeInTheDocument();
});
it('aborts a pending decision on target replacement and never publishes the old acknowledgement', async () => {
  const test = setup(); await test.respond(); fireEvent.change(note(), { target: { value: 'First note' } }); fireEvent.click(approve()); test.rerender({ id: 'p2' }); expect(test.calls[1]!.signal.aborted).toBe(true); await test.respond(2, diff({ id: 'p2' })); expect(note()).toHaveValue(''); await test.respond(1, ack()); expect(screen.queryByText('Behavior change approved.')).not.toBeInTheDocument(); expect(test.dispatch).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'projection/auxiliaryResourceReceived' })); expect(approve()).toBeEnabled();
});
it('refuses a mismatched nested decision acknowledgement and retains the inspected diff and note', async () => {
  const test = setup(); await test.respond(); fireEvent.change(note(), { target: { value: 'Preserve evidence' } }); fireEvent.click(approve()); await test.respond(1, ack({ base_version_id: 'wrong-base' })); expect(screen.getByRole('alert')).toHaveTextContent('Could not confirm'); expect(screen.queryByText('Behavior change approved.')).not.toBeInTheDocument(); expect(screen.getByLabelText('Behavior diff')).toHaveTextContent('+new'); expect(note()).toHaveValue('Preserve evidence'); expect(approve()).toBeDisabled(); expect(screen.getByRole('button', { name: 'Close review' })).toBeEnabled();
});
it('retains diff/note/focus/caret through a stalled reconnect read while disabling decisions', async () => {
  const test = setup(); await test.respond(); fireEvent.change(note(), { target: { value: 'Read this evidence' } }); const input = note() as HTMLTextAreaElement; input.focus(); input.setSelectionRange(2, 7); vi.useFakeTimers();
  act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(approve()).toBeDisabled(); expect(screen.getByLabelText('Behavior diff')).toHaveTextContent('+new'); await act(async () => { await vi.advanceTimersByTimeAsync(15_001); }); expect(screen.getByRole('alert')).toHaveTextContent('refresh timed out'); expect(input).toHaveFocus(); expect([input.selectionStart, input.selectionEnd]).toEqual([2, 7]); expect(input).toHaveValue('Read this evidence'); fireEvent.click(screen.getByRole('button', { name: 'Reload diff' })); await test.respond(); expect(approve()).toBeEnabled(); expect(note()).toBe(input);
});
it.each([true, false])('bounds a stalled decision (%s), preserves the note and requires manual fresh review without replay', async (approving) => {
  const test = setup(); await test.respond(); fireEvent.change(note(), { target: { value: 'Decision evidence' } }); vi.useFakeTimers(); fireEvent.click(approving ? approve() : reject()); await act(async () => { await vi.advanceTimersByTimeAsync(30_001); }); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(note()).toHaveValue('Decision evidence'); expect(note()).toBeEnabled(); expect(approve()).toBeDisabled(); expect(reject()).toBeDisabled(); expect(screen.getByRole('button', { name: 'Close review' })).toBeEnabled();
  expect(test.calls).toHaveLength(2); await test.respond(1, ack({ status: approving ? 'applied' : 'rejected' })); expect(screen.queryByText(approving ? 'Behavior change approved.' : 'Behavior change rejected.')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Reload diff' })); await test.respond(); act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await test.respond(); expect(test.calls.filter((call) => call.command.cmd !== 'behavior_overlay_diff')).toHaveLength(1); fireEvent.click(reject()); expect(test.calls.at(-1)!.command).toMatchObject({ cmd: 'behavior_overlay_user_reject', note: 'Decision evidence', expected_base_version_id: 'v1', expected_proposed_text_sha256: 'reviewed-hash' });
});
it('does not issue a refresh or replay while a decision is pending during reconnect', async () => {
  const test = setup(); await test.respond(); fireEvent.click(approve()); act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(test.calls).toHaveLength(2); await test.respond(1, ack()); expect(screen.getByText('Behavior change approved.')).toBeVisible(); expect(approve()).toBeDisabled();
});
it('aborts observation on unmount and ignores a late rejected-decision result', async () => {
  const test = setup(); await test.respond(); fireEvent.click(reject()); test.rerender({ shown: false }); expect(test.calls[1]!.signal.aborted).toBe(true); await test.respond(1, ack({ status: 'rejected' })); expect(test.dispatch).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'projection/auxiliaryResourceReceived' }));
});
it.each([{ id: 'other' }, { scope_group: 'other' }, { proposed_text_sha256: 'other-hash' }, { status: 'proposed' }])('validates the complete decision identity and terminal result %j', async (patch) => {
  const test = setup(); await test.respond(); fireEvent.click(approve()); await test.respond(1, ack(patch)); expect(screen.getByRole('alert')).toHaveTextContent('Could not confirm'); expect(approve()).toBeDisabled();
});
it('does not adopt a wrong response type even when it contains the requested proposal', async () => {
  const test = setup(); await test.respond(0, { ...diff(), type: 'other' }); expect(screen.getByRole('alert')).toHaveTextContent('did not contain this proposal'); expect(approve()).toBeDisabled();
});
