import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { beforeEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { readCommand } from '../../protocol/http';
import { BehaviorReview } from './BehaviorReview';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
beforeEach(() => { vi.mocked(readCommand).mockReset(); });
function setup(route = 'architect_then_user') {
  const proposal = { id: 'p1', scope_kind: 'agent', scope_group: 'Foundation', scope_key: 'eng', agent_id: 'eng', status: 'proposed', next_actor_kind: 'architect', approval_route: route, base_version_id: 'v1', proposed_text_sha256: 'reviewed-hash' };
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, agents: { arch: { id: 'arch', name: 'Reviewed Architect', kind: 'architect', group: 'Foundation' }, foreign: { id: 'foreign', name: 'Other group', kind: 'architect', group: 'Elsewhere' }, eng: { id: 'eng', kind: 'engineer', group: 'Foundation', hired_by_architect_id: 'arch' } } }));
  vi.mocked(readCommand).mockImplementation((command) => Promise.resolve(command.cmd === 'behavior_overlay_diff' ? { type: 'behavior_overlay_diff', proposal, diff: '-old\n+new' } : { type: 'behavior_overlay_proposal', proposal_id: 'p1', proposal: { ...proposal, status: command.cmd.endsWith('reject') ? 'rejected' : route === 'architect_then_user' ? 'approved' : 'applied', next_actor_kind: command.cmd.endsWith('reject') || route === 'architect' ? '' : 'user', architect_approver_id: 'arch', resolved_by_kind: 'architect', resolved_by_id: 'arch' } }));
  render(<Provider store={store}><BehaviorReview proposalId="p1" onClose={vi.fn()} /></Provider>); return { store, proposal };
}
it.each(['architect', 'architect_then_user'])('reviews the Architect stage and validates its %s route without skipping required operator approval', async (route) => {
  setup(route); const picker = await screen.findByRole('combobox', { name: 'Acting Architect' }); const approve = screen.getByRole('button', { name: 'Approve behavior change' }); expect(approve).toBeDisabled(); expect(screen.queryByRole('option', { name: 'Other group' })).not.toBeInTheDocument();
  fireEvent.change(picker, { target: { value: 'arch' } }); fireEvent.change(screen.getByRole('textbox', { name: 'Review note' }), { target: { value: 'Reviewed route' } }); fireEvent.click(approve);
  await screen.findByText(route === 'architect_then_user' ? 'Architect approval recorded. Operator approval is still required.' : 'Behavior change approved.');
  expect(readCommand).toHaveBeenLastCalledWith({ cmd: 'behavior_overlay_architect_approve', architect_id: 'arch', proposal_id: 'p1', expected_proposed_text_sha256: 'reviewed-hash', expected_base_version_id: 'v1', note: 'Reviewed route' }, expect.any(AbortSignal));
});
it('retains the note and selected Architect after rejection refusal, then retries only after a fresh diff', async () => {
  setup(); const picker = await screen.findByRole('combobox', { name: 'Acting Architect' }); fireEvent.change(picker, { target: { value: 'arch' } }); fireEvent.change(screen.getByRole('textbox', { name: 'Review note' }), { target: { value: 'Needs revision' } });
  vi.mocked(readCommand).mockResolvedValueOnce({ type: 'error', message: 'Review refused' }); fireEvent.click(screen.getByRole('button', { name: 'Reject behavior change' })); await screen.findByText('Review refused'); expect(picker).toHaveValue('arch'); expect(screen.getByRole('textbox', { name: 'Review note' })).toHaveValue('Needs revision'); expect(screen.getByRole('button', { name: 'Reject behavior change' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Reload diff' })); await waitFor(() => expect(screen.getByRole('button', { name: 'Reject behavior change' })).toBeEnabled()); fireEvent.click(screen.getByRole('button', { name: 'Reject behavior change' })); await screen.findByText('Behavior change rejected.'); expect(readCommand).toHaveBeenLastCalledWith(expect.objectContaining({ cmd: 'behavior_overlay_architect_reject', architect_id: 'arch', note: 'Needs revision' }), expect.any(AbortSignal));
});
it('disables the decision if its selected Architect disappears instead of silently choosing another identity', async () => {
  const { store } = setup(); const picker = await screen.findByRole('combobox', { name: 'Acting Architect' }); fireEvent.change(picker, { target: { value: 'arch' } });
  act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'agent_upsert', id: 'arch', deleted_at: 100 }] })); }); expect(screen.getByRole('button', { name: 'Approve behavior change' })).toBeDisabled();
});
it.each(['wrong-actor', 'skipped-operator'])('does not accept an Architect acknowledgement with %s', async (failure) => {
  const { proposal } = setup(); const picker = await screen.findByRole('combobox', { name: 'Acting Architect' }); fireEvent.change(picker, { target: { value: 'arch' } });
  vi.mocked(readCommand).mockResolvedValueOnce({ type: 'behavior_overlay_proposal', proposal_id: 'p1', proposal: { ...proposal, status: failure === 'skipped-operator' ? 'applied' : 'approved', next_actor_kind: failure === 'skipped-operator' ? '' : 'user', architect_approver_id: failure === 'wrong-actor' ? 'foreign' : 'arch' } });
  fireEvent.click(screen.getByRole('button', { name: 'Approve behavior change' })); expect(await screen.findByRole('alert')).toHaveTextContent('Could not confirm the decision'); expect(screen.queryByText('Architect approval recorded. Operator approval is still required.')).not.toBeInTheDocument();
});
