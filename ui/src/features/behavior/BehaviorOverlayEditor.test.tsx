import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createAppStore, connectionActions, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { BehaviorOverlayEditor } from './BehaviorOverlayEditor';
import { draftDiff } from './model';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
beforeEach(() => { vi.mocked(readCommand).mockReset(); });
afterEach(() => { vi.useRealTimers(); });
const scoped = (group = 'A', target = 'worker', kind = 'role') => ({ scope_kind: kind, scope_group: group, scope_key: target, agent_id: kind === 'agent' ? target : '' });
function setup() {
  const calls: { command: TorqueCommand; signal: AbortSignal; resolve: (frame: AuxiliaryFrame) => void; reject: (error: Error) => void }[] = [];
  vi.mocked(readCommand).mockImplementation((command, signal) => new Promise((resolve, reject) => { calls.push({ command, signal, resolve, reject }); }));
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  let group = 'A'; let active: UnknownRecord = {}; let proposals: UnknownRecord = {}; let shown = true;
  const element = () => <Provider store={store}>{shown ? <BehaviorOverlayEditor group={group} active={active} proposals={proposals} agents={[{ id: 'a', name: 'Agent A', kind: 'engineer' }, { id: 'b', name: 'Agent B', kind: 'architect' }]} /> : null}</Provider>;
  const view = render(element());
  const rerender = (next: { group?: string; active?: UnknownRecord; proposals?: UnknownRecord; shown?: boolean }) => { group = next.group ?? group; active = next.active ?? active; proposals = next.proposals ?? proposals; shown = next.shown ?? shown; view.rerender(element()); };
  const respond = async (index: number, frame: AuxiliaryFrame) => { await act(async () => { calls[index]!.resolve(frame); await Promise.resolve(); }); };
  const batch = async (start = calls.length - 3, body = 'Original behavior', version = 'v1') => {
    await act(async () => { for (const call of calls.slice(start, start + 3)) { const args = scoped(String(call.command.group), String(call.command.scope_key), String(call.command.scope_kind)); call.resolve(call.command.cmd === 'behavior_overlay_read' ? { type: 'behavior_overlay', ...args, text: body, version: { id: version }, active: { active_version_id: version } } : call.command.cmd === 'behavior_overlay_versions' ? { type: 'behavior_overlay_versions', ...args, versions: [{ ...args, id: version, version_number: 1, author_kind: 'user' }] } : { type: 'behavior_overlay_proposals', proposals: [] }); } await Promise.resolve(); });
  };
  const choose = (kind = 'role', target = 'worker') => { fireEvent.change(screen.getByLabelText('Behavior scope'), { target: { value: kind } }); fireEvent.change(screen.getByLabelText('Behavior target'), { target: { value: target } }); };
  return { ...view, calls, store, rerender, respond, batch, choose };
}
const editor = () => screen.getByRole('textbox', { name: 'Behavior instructions' });
const edit = (value: string) => fireEvent.change(editor(), { target: { value } });
const propose = () => screen.getByRole('button', { name: 'Propose change' });
it('does not display cached cross-scope data and rejects wrong current reads with retry', async () => {
  const test = setup(); act(() => { test.store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'behavior_overlay', ...scoped('other'), text: 'Unowned cache' })); });
  expect(test.calls).toHaveLength(0); test.choose(); expect(screen.getByText('Loading behavior scope…')).toBeVisible(); expect(screen.queryByText('No open proposals for this scope.')).not.toBeInTheDocument();
  await test.respond(0, { type: 'behavior_overlay', ...scoped('other'), text: 'Wrong group', version: { id: 'v1' } }); await test.respond(1, { type: 'behavior_overlay_versions', ...scoped(), versions: [] }); await test.respond(2, { type: 'behavior_overlay_proposals', proposals: [] });
  expect(screen.getByRole('alert')).toHaveTextContent('different scope'); expect(screen.queryByDisplayValue('Wrong group')).not.toBeInTheDocument(); fireEvent.click(screen.getByRole('button', { name: 'Retry behavior scope' })); await test.batch(); expect(editor()).toHaveValue('Original behavior');
});
it('keeps independent scope drafts, explicit empty text and rationale through switching and close/reopen', async () => {
  const test = setup(); test.choose(); await test.batch(); edit(''); fireEvent.change(screen.getByLabelText('Proposal rationale'), { target: { value: 'Remove obsolete instructions' } });
  fireEvent.change(screen.getByLabelText('Behavior target'), { target: { value: 'engineer' } }); await test.batch(undefined, 'Engineer text'); expect(editor()).toHaveValue('Engineer text'); edit('Engineer draft');
  fireEvent.change(screen.getByLabelText('Behavior target'), { target: { value: 'worker' } }); await test.batch(); expect(editor()).toHaveValue(''); expect(screen.getByLabelText('Proposal rationale')).toHaveValue('Remove obsolete instructions');
  test.rerender({ group: 'B' }); expect(screen.queryByRole('textbox', { name: 'Behavior instructions' })).not.toBeInTheDocument(); test.choose(); await test.batch(undefined, 'Other group'); expect(editor()).toHaveValue('Other group');
  test.rerender({ group: 'A' }); await test.batch(); expect(editor()).toHaveValue(''); test.rerender({ shown: false }); const length = test.calls.length; act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(test.calls).toHaveLength(length); test.rerender({ shown: true }); await test.batch(); expect(editor()).toHaveValue('');
});
it('aborts obsolete scope reads and ignores their late completion', async () => {
  const test = setup(); test.choose('agent', 'a'); fireEvent.change(screen.getByLabelText('Behavior target'), { target: { value: 'b' } }); expect(test.calls[0]!.signal.aborted).toBe(true); await test.batch(3, 'B current'); await test.batch(0, 'A obsolete'); expect(editor()).toHaveValue('B current');
});
it('retains the focused draft/caret through reconnect and requires a refreshed active base', async () => {
  const test = setup(); test.choose(); await test.batch(); edit('Local draft'); const input = editor() as HTMLTextAreaElement; input.focus(); input.setSelectionRange(1, 4);
  act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(propose()).toBeDisabled(); await test.batch(undefined, 'Server revision'); expect(editor()).toBe(input); expect(input).toHaveFocus(); expect([input.selectionStart, input.selectionEnd]).toEqual([1, 4]); expect(input).toHaveValue('Local draft');
  test.rerender({ active: { target: { ...scoped(), active_version_id: 'v2' } } }); expect(propose()).toBeDisabled(); await test.batch(undefined, 'Old base', 'v1'); expect(screen.getByRole('alert')).toHaveTextContent('active behavior version changed'); expect(input).toHaveValue('Local draft');
  fireEvent.click(screen.getByRole('button', { name: 'Retry behavior scope' })); await test.batch(undefined, 'New base', 'v2'); expect(propose()).toBeEnabled(); expect(input).toHaveValue('Local draft');
});
it('renders a local diff and no-change state without issuing mutations', async () => {
  const test = setup(); test.choose(); await test.batch(undefined, 'Keep\nOld'); fireEvent.click(screen.getByRole('button', { name: 'Preview draft diff' })); expect(screen.getByLabelText('Behavior draft diff')).toHaveTextContent('No text changes.'); edit('Keep\nNew'); expect(screen.getByLabelText('Behavior draft diff')).toHaveTextContent('-Old'); expect(screen.getByLabelText('Behavior draft diff')).toHaveTextContent('+New'); expect(test.calls).toHaveLength(3);
  expect(draftDiff('same', 'same')).toBe(''); expect(draftDiff('removed', '')).toContain('-removed'); expect(draftDiff('a\r\nb', 'a\nb')).not.toContain('-b');
});
it('submits the exact empty draft and base once, retaining refused intent and a stable retry token', async () => {
  const test = setup(); test.choose(); await test.batch(); edit(''); fireEvent.change(screen.getByLabelText('Proposal rationale'), { target: { value: 'Clear it' } }); fireEvent.click(propose()); fireEvent.click(screen.getByRole('button', { name: 'Submitting proposal…' })); expect(test.calls).toHaveLength(4);
  const command = test.calls[3]!.command; expect(command).toMatchObject({ cmd: 'behavior_overlay_propose', scope_kind: 'role', scope_group: 'A', scope_key: 'worker', text: '', rationale: 'Clear it', expected_base_version_id: 'v1', proposed_by_kind: 'user', proposed_by_agent_id: 'user' });
  await act(async () => { test.calls[3]!.reject(new Error('Proposal refused')); await Promise.resolve(); }); await test.batch(); expect(screen.getByRole('alert')).toHaveTextContent('Proposal refused'); expect(editor()).toHaveValue('');
  fireEvent.click(propose()); const next = test.calls.length - 1; expect(test.calls[next]!.command.idempotency_key).toBe(command.idempotency_key);
  await test.respond(next, { type: 'behavior_overlay_proposal', proposal_id: 'p1', proposal: { ...scoped(), id: 'p1', base_version_id: 'v1', status: 'proposed', next_actor_kind: 'user' } }); await test.batch(); expect(propose()).toBeDisabled(); expect(screen.getByText(/Proposal submitted: p1/)).toBeVisible();
});
it('filters projected proposals to the current scope and adopts their resolved status', async () => {
  const test = setup(); test.choose(); await test.batch(); test.rerender({ proposals: { a: { ...scoped(), id: 'ours', status: 'proposed' }, b: { ...scoped('B'), id: 'other', status: 'proposed' }, c: { ...scoped('A', 'engineer'), id: 'other-target', status: 'proposed' } } }); expect(screen.getByText('ours')).toBeVisible(); expect(screen.queryByText('other')).not.toBeInTheDocument(); expect(screen.queryByText('other-target')).not.toBeInTheDocument(); test.rerender({ proposals: { a: { ...scoped(), id: 'ours', status: 'applied' } } }); expect(screen.getByText('No open proposals for this scope.')).toBeVisible();
});
it('bounds a read and ignores its late reply after retry', async () => {
  const test = setup(); vi.useFakeTimers(); test.choose(); await act(async () => { await vi.advanceTimersByTimeAsync(15_001); }); expect(screen.getByRole('alert')).toHaveTextContent('Behavior overlay refresh timed out'); fireEvent.click(screen.getByRole('button', { name: 'Retry behavior scope' })); await test.batch(3, 'Current'); await test.batch(0, 'Old'); expect(editor()).toHaveValue('Current');
});
it('does not replay a timed-out proposal on reconnect or accept a late acknowledgement', async () => {
  const test = setup(); test.choose(); await test.batch(); edit('Proposed'); vi.useFakeTimers(); fireEvent.click(propose()); const original = test.calls[3]!.command;
  await act(async () => { await vi.advanceTimersByTimeAsync(30_001); }); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); await test.batch(); act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); await test.batch(); expect(test.calls.filter((call) => call.command.cmd === 'behavior_overlay_propose')).toHaveLength(1);
  await test.respond(3, { type: 'behavior_overlay_proposal', proposal_id: 'late', proposal: { ...scoped(), id: 'late', base_version_id: 'v1' } }); expect(screen.queryByText(/Proposal submitted:/)).not.toBeInTheDocument(); fireEvent.click(propose()); expect(test.calls.at(-1)!.command.idempotency_key).toBe(original.idempotency_key);
});
it.each([{ type: 'ok' }, { type: 'behavior_overlay_proposal', proposal_id: 'wrong', proposal: { ...scoped('B'), id: 'wrong', base_version_id: 'v1' } }, { type: 'behavior_overlay_proposal', proposal_id: 'wrong-base', proposal: { ...scoped(), id: 'wrong-base', base_version_id: 'other' } }])('rejects an invalid proposal acknowledgement %j', async (frame) => {
  const test = setup(); test.choose(); await test.batch(); edit('Retained'); fireEvent.click(propose()); await test.respond(3, frame); expect(screen.getByRole('alert')).toHaveTextContent('did not match'); expect(editor()).toHaveValue('Retained'); expect(screen.queryByText(/Proposal submitted:/)).not.toBeInTheDocument();
});
it('ends proposal observation on scope change and retains the old target draft', async () => {
  const test = setup(); test.choose(); await test.batch(); edit('Worker draft'); fireEvent.click(propose()); fireEvent.change(screen.getByLabelText('Behavior target'), { target: { value: 'engineer' } }); expect(test.calls[3]!.signal.aborted).toBe(true); await test.batch(); await test.respond(3, { type: 'behavior_overlay_proposal', proposal_id: 'obsolete', proposal: { ...scoped(), id: 'obsolete', base_version_id: 'v1' } }); expect(screen.queryByText(/Proposal submitted:/)).not.toBeInTheDocument(); expect(editor()).toHaveValue('Original behavior');
});
it('preserves explicit group-wide review discovery without leaking other groups or issuing hidden reads', async () => {
  const test = setup(); fireEvent.click(screen.getByRole('button', { name: 'Refresh proposals' })); expect(test.calls[0]!.command).toMatchObject({ cmd: 'behavior_overlay_proposals', group: 'A' });
  await test.respond(0, { type: 'behavior_overlay_proposals', proposals: [{ ...scoped(), id: 'group-proposal', status: 'proposed', rationale: 'Our pending review' }, { ...scoped('B'), id: 'other', status: 'proposed', rationale: 'Other pending review' }] });
  expect(screen.getByText('Our pending review')).toBeVisible(); expect(screen.queryByText('Other pending review')).not.toBeInTheDocument();
  act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(test.calls).toHaveLength(2); test.rerender({ shown: false }); expect(test.calls[1]!.signal.aborted).toBe(true); act(() => { test.store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); }); expect(test.calls).toHaveLength(2);
});
it('keeps group proposal discovery errors actionable and never accepts an old group reply', async () => {
  const test = setup(); fireEvent.click(screen.getByRole('button', { name: 'Refresh proposals' })); test.rerender({ group: 'B' }); expect(test.calls[0]!.signal.aborted).toBe(true); await test.respond(0, { type: 'behavior_overlay_proposals', proposals: [{ ...scoped(), id: 'old', status: 'proposed', rationale: 'Old group result' }] }); expect(screen.queryByText('Old group result')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh proposals' })); await test.respond(1, { type: 'ok' }); expect(screen.getByRole('alert')).toHaveTextContent('group proposal list is invalid'); expect(screen.getByRole('button', { name: 'Refresh proposals' })).toBeEnabled();
});

it('explains role and agent application scope and exposes proposal review metadata', async () => {
  const test = setup(); test.choose(); await test.batch(); expect(screen.getByText(/next worker dispatch/)).toHaveTextContent('applies group-wide and requires user diff approval');
  test.choose('role', 'architect'); await test.batch(); expect(screen.getByText(/next launch or relaunch/)).toHaveTextContent('architect role overlay applies group-wide');
  test.choose('agent', 'a'); await test.batch(); expect(screen.getByText(/only to the selected agent/)).toHaveTextContent('next launch or relaunch');
  test.rerender({ proposals: { a: { ...scoped('A', 'a', 'agent'), id: 'governed', status: 'proposed', proposal_type: 'rollback', approval_route: 'architect_then_user', proposed_text_bytes: 0, proposed_text_sha256: '<full-review-hash>', lint_warning_count: 2 } } });
  for (const label of ['Rollback', 'architect_then_user', '0 bytes', '<full-review-hash>', '2 warnings']) expect(screen.getByText(label)).toBeVisible();
});
