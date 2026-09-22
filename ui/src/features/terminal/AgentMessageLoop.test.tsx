import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { toAgentViewModel } from '../agents/model';
import { Conversation } from './Conversation';
import { assertLoopCancelled, loopInterval, loopNextRun, messageLoopPanel } from './messageLoopModel';
const agent = toAgentViewModel('worker', { kind: 'worker', name: 'Worker', group: 'one', session_id: 'session' });
const other = toAgentViewModel('other', { kind: 'worker', name: 'Other', group: 'two', session_id: 'other-session' });
const loop = { id: 'loop-one', agent_id: agent.id, status: 'active', message: 'Review progress\nand blockers', interval_seconds: 600, next_run_at: 1_900_000_000, created_at: 10 };
function setup() {
  const store = createAppStore(); const calls: { command: TorqueCommand; resolve: (frame: UnknownRecord) => void }[] = [];
  const snapshot = (loops: UnknownRecord = { [loop.id]: loop }) => { store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, agent_message_loops: loops })); };
  snapshot(); vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((resolve) => calls.push({ command: JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand, resolve: (frame) => resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: frame }) }) }))));
  const content = (cell = agent, target: typeof agent | null = cell) => <Provider store={store}><Conversation key={cell.id} cell={cell} target={target} messages={[]} sendCommand={() => true} onUnavailable={vi.fn()} /></Provider>;
  const view = render(content()); const show = (cell = agent, target: typeof agent | null = cell) => view.rerender(content(cell, target));
  const finish = async (index: number, frame: UnknownRecord) => { await act(async () => { calls[index]!.resolve(frame); await Promise.resolve(); }); };
  return { store, calls, snapshot, show, finish };
}
afterEach(() => vi.unstubAllGlobals());
describe('agent message loop controls', () => {
  it('projects scoped active/deferred loops and follows external stop without inventing status', () => {
    const { snapshot, show } = setup(); expect(screen.getByRole('region', { name: 'Scheduled message loop' })).toHaveTextContent('Every 10m'); expect(screen.getByText(/Next /)).toBeVisible();
    act(() => snapshot({ [loop.id]: { ...loop, deferred_at: 100, deferred_reason: 'agent_busy' } })); expect(screen.getByText('Deferred until the agent is idle')).toBeVisible();
    show(other); expect(screen.queryByRole('region', { name: 'Scheduled message loop' })).not.toBeInTheDocument();
    const attached = { ...other, cellType: 'terminal' as const, parentId: agent.id }; show(attached, agent); expect(screen.getByRole('button', { name: 'Cancel loop' })).toBeVisible(); show(attached, null); expect(screen.queryByRole('button', { name: 'Cancel loop' })).not.toBeInTheDocument();
    show(); act(() => snapshot({ [loop.id]: { ...loop, status: 'stopped' } })); expect(screen.queryByRole('region', { name: 'Scheduled message loop' })).not.toBeInTheDocument();
  });
  it('retains unrelated drafts and retries the same acknowledged cancellation after a lost response', async () => {
    const { calls, finish, snapshot } = setup(); const input = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message Worker' }); fireEvent.change(input, { target: { value: 'Unrelated draft' } }); input.setSelectionRange(2, 8); fireEvent.select(input);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel loop' })); expect(calls[0]!.command).toMatchObject({ cmd: 'user_agent_message', agent_id: agent.id, message: '/loop cancel', expected_loop_id: loop.id }); expect(screen.getByRole('button', { name: 'Cancelling loop…' })).toBeDisabled();
    act(() => snapshot({ [loop.id]: { ...loop, status: 'cancelled' } })); expect(screen.getByRole('button', { name: 'Cancelling loop…' })).toBeDisabled();
    await finish(0, { type: 'error', message: 'Lost cancellation response' }); expect(screen.getByRole('alert')).toHaveTextContent('Lost cancellation response'); expect(input).toHaveValue('Unrelated draft');
    fireEvent.click(screen.getByRole('button', { name: 'Retry loop cancellation' })); expect(calls[1]!.command).toEqual(calls[0]!.command);
    await finish(1, { type: 'agent_message_loop', loop: { ...loop, status: 'cancelled' }, audit_message_id: 'audit' }); expect(screen.getByRole('status')).toHaveTextContent('Message loop cancelled.'); expect(input).toHaveValue('Unrelated draft'); expect([input.selectionStart, input.selectionEnd]).toEqual([2, 8]);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss loop cancellation status' })); expect(screen.queryByRole('region', { name: 'Scheduled message loop' })).not.toBeInTheDocument();
  });
  it('keeps pending results with their captured agent and loop across selection and replacement', async () => {
    const { calls, finish, snapshot, show } = setup(); fireEvent.click(screen.getByRole('button', { name: 'Cancel loop' })); show(other); expect(screen.queryByRole('region', { name: 'Scheduled message loop' })).not.toBeInTheDocument();
    await finish(0, { type: 'error', message: 'Delayed refusal' }); show(); expect(screen.getByRole('alert')).toHaveTextContent('Delayed refusal');
    const next = { ...loop, id: 'replacement', created_at: 20, message: 'Replacement loop' }; act(() => snapshot({ [loop.id]: { ...loop, status: 'cancelled' }, replacement: next })); expect(screen.getByText('Replacement loop')).toBeVisible(); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel loop' })); expect(calls[1]!.command.expected_loop_id).toBe('replacement'); expect(calls[1]!.command.idempotency_key).not.toBe(calls[0]!.command.idempotency_key);
    await finish(1, { type: 'agent_message_loop', loop: { ...loop, status: 'cancelled' }, audit_message_id: 'old-audit' }); expect(screen.getByRole('alert')).toHaveTextContent('Could not confirm cancellation of this loop');
  });
  it('requires a matching cancelled loop and audit, and formats bounded timing safely', () => {
    expect(loopInterval(3600)).toBe('1h'); expect(loopInterval(90)).toBe('90s'); expect(loopInterval('bad')).toBe('unknown interval'); expect(loopNextRun({})).toBe('Next run not scheduled');
    expect(messageLoopPanel({ [loop.id]: loop }, {}, other.id)).toBeNull();
    const good = { type: 'agent_message_loop', loop: { ...loop, status: 'cancelled' }, audit_message_id: 'audit' }; expect(() => assertLoopCancelled(good, agent.id, loop.id)).not.toThrow();
    for (const changed of [{ ...good, audit_message_id: '' }, { ...good, loop: { ...loop, status: 'active' } }, { ...good, loop: { ...loop, status: 'cancelled', agent_id: 'other' } }, { ...good, type: 'ok' }]) expect(() => assertLoopCancelled(changed, agent.id, loop.id)).toThrow('confirm');
  });
});
