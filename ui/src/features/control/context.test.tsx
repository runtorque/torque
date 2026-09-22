import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { ContextPanel } from './ContextPanel';
import { contextTargets, savedContextLinks } from './contextLinksModel';

afterEach(() => vi.unstubAllGlobals());
const taskRecords = { root: { id: 'root', task: 'Pipeline root', group: 'Foundation' }, child: { id: 'child', task: 'Implementation task', group: 'Foundation', parent_task_id: 'root' }, foreign: { id: 'foreign', task: 'Other group task', group: 'Other' } };
const contextFixture = { ...compactStateFixture, board_tasks: taskRecords, agents: { 'agent-1': { id: 'agent-1', name: 'Agent One', group: 'Foundation', kind: 'worker' } } };
function setup() {
  const commands: TorqueCommand[] = []; const signals: AbortSignal[] = [];
  let entries: UnknownRecord[] = [{ id: 'memory-1', title: 'Original title', content: 'Original body', entry_type: 'note', scope_kind: 'group', scope_ref: 'Foundation', pinned: false, source_kind: 'agent', source_name: '', source_id: 'author' }];
  let failure = ''; let mismatch = false;
  const fetcher = vi.fn((_url: string, options?: RequestInit) => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; commands.push(command); signals.push(options?.signal as AbortSignal);
    let data: UnknownRecord;
    if (command.cmd === failure) data = { type: 'error', message: 'Injected refusal' };
    else if (command.cmd === 'memory_list') data = { type: 'memory_entries', group_name: 'Foundation', entries: structuredClone(entries) };
    else {
      const entry: UnknownRecord = { ...(command.entry_id ? entries.find((item) => item.id === command.entry_id) : {}), ...command, id: mismatch ? 'wrong-entry' : command.entry_id ?? 'created-entry' };
      if (command.link_targets) entry.links = command.link_targets;
      if (command.cmd === 'memory_pin' || command.cmd === 'memory_unpin') entry.pinned = command.cmd === 'memory_pin';
      if (!mismatch) entries = [entry, ...entries.filter((item) => item.id !== entry.id)];
      data = { type: 'memory_entry', entry };
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
  });
  vi.stubGlobal('fetch', fetcher); const store = createAppStore();
  store.dispatch(projectionActions.snapshotReceived(contextFixture));
  store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const onOpenTarget = vi.fn();
  const view = render(<Provider store={store}><ContextPanel group="Foundation" agents={[{ id: 'agent-1', name: 'Agent One', group: 'Foundation' }]} onOpenTarget={onOpenTarget} /></Provider>);
  const reconnect = async () => { await act(async () => { store.dispatch(projectionActions.snapshotReceived(contextFixture)); store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); await Promise.resolve(); }); };
  return { ...view, store, onOpenTarget, commands, signals, fetcher, reconnect, fail: (cmd: string) => { failure = cmd; }, mismatch: (value: boolean) => { mismatch = value; }, remote: (patch: UnknownRecord) => { entries = [{ ...entries[0], ...patch }]; } };
}

it('refreshes applied filters on reconnect while retaining edited content, caret and unapplied search', async () => {
  const { commands, remote, reconnect } = setup();
  await screen.findByRole('heading', { name: 'Original title' }); expect(screen.getByText('agent', { selector: 'dd' })).toBeVisible(); fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  const content = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Content' }); fireEvent.change(content, { target: { value: 'Local draft body' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Search' }), { target: { value: 'unapplied search' } });
  content.focus(); content.setSelectionRange(2, 7); remote({ title: 'Remote title', content: 'Remote body', pinned: true }); await reconnect();
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Remote title'));
  expect(screen.getByRole('textbox', { name: 'Content' })).toBe(content); expect(content).toHaveValue('Local draft body'); expect(content).toHaveFocus(); expect([content.selectionStart, content.selectionEnd]).toEqual([2, 7]);
  expect(screen.getByLabelText('Pin for ranking')).toBeChecked(); expect(commands.at(-1)).toMatchObject({ cmd: 'memory_list', search: '' });
  fireEvent.click(screen.getByRole('button', { name: 'Apply' })); await waitFor(() => expect(commands.at(-1)).toMatchObject({ search: 'unapplied search' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save context' })); await screen.findByRole('button', { name: 'Edit' });
  expect(commands.find((item) => item.cmd === 'memory_publish')).toEqual({ cmd: 'memory_publish', entry_id: 'memory-1', content: 'Local draft body' });
});

it('retains edits on read/write errors, rejects mismatched acknowledgements and retries without changing targets', async () => {
  const { commands, fail, mismatch, reconnect } = setup();
  await screen.findByRole('heading', { name: 'Original title' }); fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  const title = screen.getByRole('textbox', { name: 'Title' }); fireEvent.change(title, { target: { value: 'Local title' } });
  fail('memory_list'); await reconnect(); await screen.findByText(/Context refresh failed/); expect(title).toHaveValue('Local title');
  fail(''); fireEvent.click(screen.getByRole('button', { name: 'Retry context' })); await waitFor(() => expect(screen.queryByText(/Context refresh failed/)).not.toBeInTheDocument());
  fail('memory_publish'); fireEvent.click(screen.getByRole('button', { name: 'Save context' })); await screen.findByText(/Injected refusal/); expect(title).toHaveValue('Local title');
  fail(''); mismatch(true); fireEvent.click(screen.getByRole('button', { name: 'Save context' })); await screen.findByText(/not acknowledged/); expect(screen.getByRole('textbox', { name: 'Title' })).toBe(title);
  mismatch(false); fireEvent.click(screen.getByRole('button', { name: 'Save context' })); await screen.findByRole('heading', { name: 'Local title' });
  expect(commands.filter((item) => item.cmd === 'memory_publish')).toHaveLength(3);
  fail('memory_pin'); fireEvent.click(screen.getByRole('button', { name: 'Pin' })); await screen.findByText(/Injected refusal/); expect(screen.getByRole('button', { name: 'Pin' })).toBeEnabled();
  fail(''); fireEvent.click(screen.getByRole('button', { name: 'Pin' })); await screen.findByRole('button', { name: 'Unpin' });
});

it('captures a new entry agent link before filter changes and aborts reads when hidden', async () => {
  const { commands, signals, unmount, store } = setup(); await screen.findByRole('heading', { name: 'Original title' });
  fireEvent.change(screen.getByLabelText('Focus'), { target: { value: 'agent' } }); fireEvent.change(screen.getByLabelText('Agent'), { target: { value: 'agent-1' } });
  fireEvent.click(screen.getByRole('button', { name: '＋ Add context' })); fireEvent.change(screen.getByRole('textbox', { name: 'Content' }), { target: { value: 'New context' } });
  fireEvent.change(screen.getByLabelText('Focus'), { target: { value: 'group' } }); fireEvent.click(screen.getByRole('button', { name: 'Publish context' })); await screen.findByRole('button', { name: 'Edit' });
  expect(commands.find((item) => item.cmd === 'memory_publish')).toMatchObject({ scope_kind: 'group', scope_ref: 'Foundation', content: 'New context', link_targets: [{ target_kind: 'agent', target_ref: 'agent-1' }] });
  await waitFor(() => expect(commands.filter((command) => command.cmd === 'memory_list')).toHaveLength(2));
  unmount(); const count = commands.length; expect(signals.at(-1)?.aborted).toBe(true);
  await act(async () => { store.dispatch(connectionActions.connected({ at: 3, reconnect: true })); await Promise.resolve(); }); expect(commands).toHaveLength(count);
});

it.each(['group', 'agent', 'task', 'pipeline', 'project'])('applies the %s target explicitly and retains it on reconnect', async (focus) => {
  const { commands, reconnect } = setup(); await screen.findByRole('heading', { name: 'Original title' });
  fireEvent.change(screen.getByLabelText('Focus'), { target: { value: focus } });
  if (focus === 'agent') fireEvent.change(screen.getByLabelText('Agent'), { target: { value: 'agent-1' } });
  else if (focus !== 'group') fireEvent.change(screen.getByLabelText(`${focus[0]!.toUpperCase()}${focus.slice(1)} reference`), { target: { value: 'target-1' } });
  fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'warning' } }); fireEvent.click(screen.getByLabelText('Pinned only'));
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(commands).toHaveLength(2));
  const target = focus === 'agent' ? { linked_target_kind: 'agent', linked_target_ref: 'agent-1' } : { scope_kind: focus, scope_ref: focus === 'group' ? 'Foundation' : 'target-1' };
  expect(commands.at(-1)).toMatchObject({ ...target, entry_type: 'warning', pinned_only: true });
  await reconnect(); expect(commands.at(-1)).toEqual(commands.at(-2));
});


it('builds distinct pipeline-root choices and excludes terminals from agent targets', () => {
  const targets = contextTargets(Object.values(taskRecords), [{ id: 'terminal', kind: 'terminal' }, { id: 'agent', name: 'Worker', group: 'Foundation' }]);
  expect(targets.filter((target) => target.kind === 'pipeline').map((target) => target.id)).toEqual(['root', 'foreign']);
  expect(contextTargets([{ id: 'child', group: 'Other', parent_task_id: 'root' }, { id: 'root', group: 'Foundation' }], []).find((target) => target.kind === 'pipeline')).toMatchObject({ id: 'root', group: 'Foundation' });
  expect(targets.find((target) => target.kind === 'pipeline' && target.id === 'root')?.name).toBe('Pipeline root');
  expect(targets.filter((target) => target.kind === 'agent').map((target) => target.id)).toEqual(['agent']);
  expect(savedContextLinks([{ target_kind: 'invalid', target_ref: 'x' }, { target_kind: 'task', target_ref: 'child' }])).toEqual([{ target_kind: 'task', target_ref: 'child' }]);
});

it('stages all link kinds without duplicates, retains them on reconnect/refusal and opens saved targets', async () => {
  const { store, commands, fail, reconnect, onOpenTarget } = setup(); await screen.findByRole('button', { name: 'Edit' });
  fireEvent.click(screen.getByRole('button', { name: '＋ Add context' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Content' }), { target: { value: 'Linked context' } });
  for (const [kind, id] of [['task', 'child'], ['pipeline', 'root'], ['agent', 'agent-1']]) {
    fireEvent.change(screen.getByRole('combobox', { name: 'Link kind' }), { target: { value: kind } });
    expect(screen.queryByRole('option', { name: /Other group task/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Link target' }), { target: { value: id } }); fireEvent.click(screen.getByRole('button', { name: 'Add link' }));
    expect(screen.getByRole('button', { name: 'Add link' })).toBeDisabled();
  }
  const body = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Content' }); body.focus(); body.setSelectionRange(1, 5); await reconnect();
  expect(body).toHaveFocus(); expect([body.selectionStart, body.selectionEnd]).toEqual([1, 5]); expect(screen.getAllByRole('button', { name: /Remove link/ })).toHaveLength(3);
  fail('memory_publish'); fireEvent.click(screen.getByRole('button', { name: 'Publish context' })); await screen.findByText(/Injected refusal/); expect(screen.getAllByRole('button', { name: /Remove link/ })).toHaveLength(3);
  fail(''); fireEvent.click(screen.getByRole('button', { name: 'Publish context' })); await screen.findByRole('button', { name: 'Edit' });
  expect(commands.filter((command) => command.cmd === 'memory_publish').at(-1)).toMatchObject({ scope_kind: 'group', scope_ref: 'Foundation', link_targets: [{ target_kind: 'task', target_ref: 'child' }, { target_kind: 'pipeline', target_ref: 'root' }, { target_kind: 'agent', target_ref: 'agent-1' }] });
  fireEvent.click(screen.getByRole('button', { name: 'Open linked pipeline Pipeline root' })); expect(onOpenTarget).toHaveBeenCalledWith(expect.objectContaining({ kind: 'pipeline', id: 'root' }));
  fireEvent.click(screen.getByRole('button', { name: 'Open linked task Implementation task' })); expect(onOpenTarget).toHaveBeenCalledWith(expect.objectContaining({ kind: 'task', id: 'child' }));
  fireEvent.click(screen.getByRole('button', { name: 'Open linked agent Agent One' })); expect(onOpenTarget).toHaveBeenCalledWith(expect.objectContaining({ kind: 'agent', id: 'agent-1' }));
  fireEvent.click(screen.getByRole('button', { name: 'Edit' })); fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'Changed title' } }); fireEvent.click(screen.getByRole('button', { name: 'Save context' })); await screen.findByRole('button', { name: 'Edit' });
  expect(commands.filter((command) => command.cmd === 'memory_publish').at(-1)).toEqual({ cmd: 'memory_publish', entry_id: 'created-entry', title: 'Changed title' });
  expect(screen.getAllByRole('button', { name: /Open linked/ })).toHaveLength(3);
  act(() => { store.dispatch(projectionActions.snapshotReceived({ ...contextFixture, board_tasks: { root: taskRecords.root } })); });
  expect(screen.getByRole('button', { name: 'Open linked task child' })).toBeDisabled(); expect(screen.getByText('child · Unavailable')).toBeVisible();
});

it('blocks a disappeared draft target without discarding content and allows removing that link', async () => {
  const { store, commands } = setup(); await screen.findByRole('button', { name: 'Edit' }); fireEvent.click(screen.getByRole('button', { name: '＋ Add context' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Content' }), { target: { value: 'Keep draft' } }); fireEvent.change(screen.getByRole('combobox', { name: 'Link target' }), { target: { value: 'child' } }); fireEvent.click(screen.getByRole('button', { name: 'Add link' }));
  act(() => { store.dispatch(projectionActions.snapshotReceived({ ...contextFixture, board_tasks: { root: taskRecords.root } })); });
  expect(screen.getByText(/Unavailable — remove this link/)).toBeVisible(); expect(screen.getByRole('button', { name: 'Publish context' })).toBeDisabled(); expect(screen.getByRole('textbox', { name: 'Content' })).toHaveValue('Keep draft');
  expect(commands.some((command) => command.cmd === 'memory_publish')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Remove link task: child' })); expect(screen.getByRole('button', { name: 'Publish context' })).toBeEnabled();
});


it('lets the operator search other groups and navigate a saved cross-group target', async () => {
  const { commands, onOpenTarget } = setup(); await screen.findByRole('button', { name: 'Edit' }); fireEvent.click(screen.getByRole('button', { name: '＋ Add context' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Content' }), { target: { value: 'Cross-group context' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Search link targets' }), { target: { value: 'Other' } });
  expect(screen.getByRole('option', { name: 'Other group task · Other · foreign' })).toBeVisible();
  fireEvent.change(screen.getByRole('combobox', { name: 'Link target' }), { target: { value: 'foreign' } }); fireEvent.click(screen.getByRole('button', { name: 'Add link' }));
  fireEvent.click(screen.getByRole('button', { name: 'Publish context' })); await screen.findByRole('button', { name: 'Edit' });
  expect(commands.find((command) => command.cmd === 'memory_publish')).toMatchObject({ scope_ref: 'Foundation', link_targets: [{ target_kind: 'task', target_ref: 'foreign' }] });
  fireEvent.click(screen.getByRole('button', { name: 'Open linked task Other group task' })); expect(onOpenTarget).toHaveBeenCalledWith({ kind: 'task', id: 'foreign', name: 'Other group task', group: 'Other' });
});
