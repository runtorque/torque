import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { AskResponse } from './AskResponse';
import { ActivityPanel } from '../control/OperatorPanels';
import { BehaviorReview } from './BehaviorReview';
import { askTarget, isOpenAsk, proposalId } from './model';

const worker = { id: 'worker', name: 'Worker', cell_type: 'agent', session_id: 'session', status: 'running' };
const ask = { id: 'ask', task: 'Choose release', lane: 'Backlog', labels: ['torque:human'], parent_task_id: 'parent', reply_agent_id: 'worker' };
const parent = { id: 'parent', task: 'Release task', agent_id: 'worker', description: 'Full release context' };
const proposal = { id: 'proposal', scope_kind: 'role', scope_group: 'Foundation', scope_key: 'engineer', status: 'approved', next_actor_kind: 'user', base_version_id: 'base', proposed_text_sha256: 'sha-reviewed', proposed_by_kind: 'engineer', proposed_by_agent_id: 'author', rationale: 'Clarify limits', lint_warnings: [{ code: 'advisory', message: 'Review this rule', excerpt: 'rule text' }] };
function setup(task: UnknownRecord = ask, agents: UnknownRecord = { worker }) {
  const store = createAppStore();
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: { ask: task, parent }, agents }));
  return store;
}
function requests(handler: (command: TorqueCommand) => UnknownRecord | Promise<UnknownRecord>) {
  const calls: TorqueCommand[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command);
    const result = await handler(command);
    return { ok: true, json: () => Promise.resolve(result.type === 'error' ? { ok: false, error: result.message } : { ok: true, data: result }) };
  }));
  return calls;
}
function detail(command: TorqueCommand): UnknownRecord {
  return { type: 'task_detail', id: command.id, task: command.id === 'parent' ? parent : { ...ask, description: 'Options: ship today or wait. Recommended: wait.' } };
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { resolve, promise }; }
afterEach(() => vi.unstubAllGlobals());

describe('attention contracts', () => {
  it('scopes attention to the selected group and excludes closed, internal and deleted records', async () => {
    const store = setup();
    const tasks = { local: { ...ask, id: 'local', group: 'Foundation', task: 'Local ask' }, other: { ...ask, id: 'other', group: 'Other', task: 'Other ask' }, closed: { ...ask, id: 'closed', group: 'Foundation', task: 'Closed ask', lane: 'Done' }, internal: { ...ask, id: 'internal', group: 'Foundation', task: 'Internal ask', labels: ['torque:human', 'torque:non-user-ask'] } };
    store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, board_tasks: tasks, agents: { worker: { ...worker, group: 'Foundation', needs_attention: true, error_message: 'Local error' }, other: { ...worker, id: 'other', group: 'Other', needs_attention: true, error_message: 'Other error' }, deleted: { ...worker, id: 'deleted', group: 'Foundation', needs_attention: true, deleted_at: 1, error_message: 'Deleted error' } } }));
    requests((command) => ({ type: 'task_detail', id: command.id, task: command.id === 'local' ? tasks.local : parent }));
    render(<Provider store={store}><ActivityPanel group="Foundation" events={[]} send={vi.fn()} /></Provider>);
    expect(screen.getByText('Local ask')).toBeVisible(); expect(screen.getByText('Local error')).toBeVisible();
    for (const hidden of ['Other ask', 'Closed ask', 'Internal ask', 'Other error', 'Deleted error']) expect(screen.queryByText(hidden)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Loading full question…')).not.toBeInTheDocument());
  });
  it('uses explicit, architect, then parent targets without falling through an unavailable explicit target', () => {
    const tasks = { parent }; const agents = { worker, architect: worker };
    expect(askTarget({ ...ask, reply_agent_id: ' absent ' }, tasks, agents)).toMatchObject({ id: 'absent', answerable: false });
    expect(askTarget({ ...ask, reply_agent_id: '', labels: ['architect-ask'], created_by_architect_id: 'architect' }, tasks, agents).id).toBe('architect');
    expect(askTarget({ ...ask, reply_agent_id: '' }, tasks, agents).id).toBe('worker');
    for (const patch of [{ cell_type: 'terminal' }, { deleted_at: 1 }, { dismissed_at: 1 }, { session_id: ' ' }, { status: 'stopped' }]) expect(askTarget(ask, tasks, { worker: { ...worker, ...patch } }).answerable).toBe(false);
    expect(askTarget(ask, tasks, agents).answerable).toBe(true);
    expect(isOpenAsk({ ...ask, labels: ['torque:human', 'torque:non-user-ask'] })).toBe(false);
    for (const lane of ['Done', 'Archive', 'Archived']) expect(isOpenAsk({ ...ask, lane })).toBe(false);
    expect(proposalId({ labels: ['proposal: p'] })).toBe('p');
    expect(proposalId({ description: 'Proposal: fallback\nReview' })).toBe('fallback');
  });
  it('hydrates question and parent, preserves text/focus under deltas, and confirms only the matching delivery', async () => {
    const store = setup(); const delivery = deferred<UnknownRecord>();
    const calls = requests((command) => command.cmd === 'task_detail' ? detail(command) : delivery.promise);
    render(<Provider store={store}><AskResponse taskId="ask" send={vi.fn()} /></Provider>);
    const answer = screen.getByRole('textbox', { name: 'Answer Choose release' });
    fireEvent.change(answer, { target: { value: 'Wait' } }); answer.focus(); (answer as HTMLTextAreaElement).setSelectionRange(2, 2);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeEnabled());
    expect(screen.getByText(/Options: ship today/)).toBeVisible();
    fireEvent.click(screen.getByText('Parent: Release task')); expect(screen.getByText('Full release context')).toBeVisible();
    answer.focus(); (answer as HTMLTextAreaElement).setSelectionRange(2, 2);
    act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'agent_upsert', id: 'worker', activity_detail: 'Working' }] })); });
    expect(answer).toHaveFocus(); expect((answer as HTMLTextAreaElement).selectionStart).toBe(2);
    expect(answer).toHaveValue('Wait'); expect(screen.getByText('Full release context')).toBeVisible();
    fireEvent.keyDown(answer, { key: 'Enter', shiftKey: true }); expect(calls.filter((c) => c.cmd === 'resolve_ask')).toHaveLength(0);
    fireEvent.keyDown(answer, { key: 'Enter' }); fireEvent.keyDown(answer, { key: 'Enter' });
    const submitted = calls.filter((c) => c.cmd === 'resolve_ask'); expect(submitted).toHaveLength(1);
    expect(answer).toHaveValue('Wait'); expect(answer).toBeDisabled();
    await act(async () => { await Promise.resolve(); delivery.resolve({ type: 'ok', command: 'resolve_ask', request_id: submitted[0]?.request_id }); });
    expect(screen.getByText('Answer delivered.')).toBeVisible(); expect(answer).toHaveValue('');
  });
  it('retains answers on rejection and rehydrates after reconnect without re-sending', async () => {
    const store = setup(); let failures = 0;
    const calls = requests((command) => command.cmd === 'task_detail' ? detail(command) : (++failures === 1 ? { type: 'error', message: 'Target session ended' } : { type: 'ok', command: 'resolve_ask', request_id: 'unrelated' }));
    render(<Provider store={store}><AskResponse taskId="ask" send={vi.fn()} /></Provider>);
    const answer = screen.getByRole('textbox', { name: 'Answer Choose release' }); fireEvent.change(answer, { target: { value: 'Keep my answer' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Resolve ask' })); expect(await screen.findByRole('alert')).toHaveTextContent('Target session ended');
    expect(answer).toHaveValue('Keep my answer');
    act(() => { store.dispatch(connectionActions.connected({ at: 2000, reconnect: true })); });
    await waitFor(() => expect(calls.filter((c) => c.cmd === 'task_detail')).toHaveLength(4));
    expect(calls.filter((c) => c.cmd === 'resolve_ask')).toHaveLength(1); expect(answer).toHaveValue('Keep my answer');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Resolve ask' })); expect(await screen.findByRole('alert')).toHaveTextContent('Could not confirm delivery'); expect(answer).toHaveValue('Keep my answer');
  });
  it('keeps inactive questions visible and enables reply only when the target resumes', async () => {
    const store = setup(ask, { worker: { ...worker, session_id: '' } }); requests(detail);
    render(<Provider store={store}><AskResponse taskId="ask" send={vi.fn()} /></Provider>);
    fireEvent.change(screen.getByRole('textbox', { name: 'Answer Choose release' }), { target: { value: 'Draft while waiting' } });
    expect(screen.getByText(/has no live session/)).toBeVisible(); await screen.findByText(/Options: ship today/);
    expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeDisabled();
    act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'agent_upsert', ...worker }] })); });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeEnabled());
    expect(screen.getByRole('textbox', { name: 'Answer Choose release' })).toHaveValue('Draft while waiting');
  });
  it('routes approval asks to diff review even with no session and never to generic resolution', async () => {
    const task = { ...ask, labels: ['torque:human', 'behavior-overlay-approval', 'proposal:proposal'] }; const store = setup(task, {});
    const calls = requests((command) => command.cmd === 'task_detail' ? { ...detail(command), task: command.id === 'ask' ? task : parent } : { type: 'behavior_overlay_diff', proposal, diff: '-old\n+new' });
    render(<Provider store={store}><AskResponse taskId="ask" send={vi.fn()} /></Provider>);
    expect(screen.queryByRole('button', { name: 'Resolve ask' })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Review behavior diff' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Review behavior diff' }));
    expect(await screen.findByLabelText('Behavior diff')).toHaveTextContent('+new');
    expect(calls.some((c) => c.cmd === 'resolve_ask')).toBe(false);
  });
});

describe('behavior review', () => {
  it('binds approval to the rendered hash/base, shows advisory detail, and prevents duplicate clicks', async () => {
    const store = setup(); const diff = deferred<UnknownRecord>(); const decision = deferred<UnknownRecord>();
    const calls = requests((command) => command.cmd === 'behavior_overlay_diff' ? diff.promise : decision.promise);
    render(<Provider store={store}><BehaviorReview proposalId="proposal" onClose={vi.fn()} /></Provider>);
    const approve = screen.getByRole('button', { name: 'Approve behavior change' }); expect(approve).toBeDisabled();
    await act(async () => { diff.resolve({ type: 'behavior_overlay_diff', proposal, diff: '-old\n+new' }); await diff.promise; });
    expect(approve).toBeEnabled(); expect(screen.getByText('Review this rule', { exact: false })).toBeVisible(); expect(screen.getByText('Clarify limits')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Review note'), { target: { value: 'Reviewed carefully' } });
    fireEvent.click(approve); fireEvent.click(approve);
    expect(calls.filter((c) => c.cmd === 'behavior_overlay_user_approve')).toEqual([{ cmd: 'behavior_overlay_user_approve', proposal_id: 'proposal', expected_proposed_text_sha256: 'sha-reviewed', expected_base_version_id: 'base', note: 'Reviewed carefully' }]);
    expect(screen.getByRole('button', { name: 'Reject behavior change' })).toBeDisabled();
    await act(async () => { decision.resolve({ type: 'behavior_overlay_proposal', proposal_id: 'proposal', proposal: { ...proposal, status: 'applied' } }); await decision.promise; });
    expect(screen.getByText('Behavior change approved.')).toBeVisible(); expect(approve).toBeDisabled();
  });
  it('requires a fresh review after stale rejection and retains the note through reload and reconnect', async () => {
    const store = setup(); let version = 0;
    const calls = requests((command) => command.cmd === 'behavior_overlay_diff' ? { type: 'behavior_overlay_diff', proposal: { ...proposal, proposed_text_sha256: `sha-${++version}` }, diff: `+version ${version}` } : { type: 'error', message: 'proposed text hash does not match' });
    render(<Provider store={store}><BehaviorReview proposalId="proposal" onClose={vi.fn()} /></Provider>);
    const approve = screen.getByRole('button', { name: 'Approve behavior change' }); await waitFor(() => expect(approve).toBeEnabled());
    const note = screen.getByLabelText('Review note'); fireEvent.change(note, { target: { value: 'Retain review note' } });
    fireEvent.click(approve); expect(await screen.findByRole('alert')).toHaveTextContent('hash does not match'); expect(approve).toBeDisabled(); expect(note).toHaveValue('Retain review note');
    fireEvent.click(screen.getByRole('button', { name: 'Reload diff' })); await waitFor(() => expect(approve).toBeEnabled()); expect(screen.getByLabelText('Behavior diff')).toHaveTextContent('version 2');
    act(() => { store.dispatch(connectionActions.connected({ at: 2000, reconnect: true })); }); await waitFor(() => expect(screen.getByLabelText('Behavior diff')).toHaveTextContent('version 3'));
    expect(note).toHaveValue('Retain review note');
    fireEvent.click(screen.getByRole('button', { name: 'Reject behavior change' })); await screen.findByRole('alert');
    expect(calls.at(-1)).toMatchObject({ cmd: 'behavior_overlay_user_reject', expected_proposed_text_sha256: 'sha-3', note: 'Retain review note' });
  });
  it('rejects mismatched diffs and does not impersonate an architect approver', async () => {
    const store = setup(); let load = 0;
    requests(() => ({ type: 'behavior_overlay_diff', proposal: { ...proposal, id: ++load === 1 ? 'wrong' : 'proposal', next_actor_kind: 'architect' }, diff: '+new' }));
    render(<Provider store={store}><BehaviorReview proposalId="proposal" onClose={vi.fn()} /></Provider>);
    expect(await screen.findByRole('alert')).toHaveTextContent('did not contain this proposal');
    expect(screen.getByRole('button', { name: 'Approve behavior change' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Reload diff' })); expect(await screen.findByText('This proposal is not awaiting an operator decision.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Reject behavior change' })).toBeDisabled();
  });
});
