import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { TorqueCommand } from '../../protocol';
import { TaskCreateDialog } from './TaskCreateDialog';

afterEach(() => vi.unstubAllGlobals());
function setup(actions: unknown = [], defaultAction = '', defaultLane = '') {
  const store = createAppStore();
  store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, group_settings: { Foundation: { board_default_action: defaultAction, board_default_lane: defaultLane } }, board_tasks: { prerequisite: { id: 'prerequisite', task: 'Cross-group prerequisite', group: 'Other', lane: 'Backlog' } } }));
  const calls: TorqueCommand[] = []; const uploads: string[] = []; const cleanups: string[] = []; let fail = '';
  const onClose = vi.fn();
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
    const response = (data: unknown) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
    if (url === '/api/upload') {
      const body = options.body as FormData; const id = body.get('task_id') as string; uploads.push(id);
      const name = (body.get('file') as File).name;
      return response({ ok: true, data: [{ filename: name, path: `/attachments/${id}/${name}`, mime_type: 'image/png' }] });
    }
    if (url === '/api/upload/cleanup') { cleanups.push(typeof options.body === 'string' ? options.body : '{}' ); return response(fail === 'cleanup' ? { ok: false, error: 'Cleanup failed' } : { ok: true }); }
    const cmd = JSON.parse(typeof options.body === 'string' ? options.body : '{}' ) as TorqueCommand; calls.push(cmd);
    if (cmd.cmd === 'preview_prompt' && !fail) return response({ ok: true, data: { type: 'prompt_preview', prompt: 'Rendered unsaved prompt', warning: 'Preview warning' } });
    return response(fail === cmd.cmd ? { ok: false, creation_refused: true, error: 'Write rejected' } : { ok: true, data: { type: cmd.cmd === 'board_add_task' ? 'board_task_added' : 'state', task_id: 'created', title: cmd.task, seq: 10, board_tasks: {} } });
  }));
  const view = (catalog: unknown) => <Provider store={store}><TaskCreateDialog group="Foundation" lanes={['Backlog', 'In Review']} actions={catalog} roles={[]} onClose={onClose} /></Provider>;
  const rendered = render(view(actions));
  return { calls, uploads, cleanups, onClose, store, refresh: (catalog: unknown) => rendered.rerender(view(catalog)), fail: (cmd: string) => { fail = cmd; } };
}
describe('reviewed task creation', () => {
  it('inherits the saved group lane instead of silently submitting the first lane', async () => {
    const { calls, onClose, store } = setup([], '', 'In Review');
    const lane = screen.getByLabelText('Lane');
    expect(lane).toHaveValue('');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Use group default' } });
    lane.focus();
    act(() => { store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, group_settings: { Foundation: { board_default_lane: 'Backlog' } } })); });
    expect(screen.getByLabelText('Lane')).toBe(lane); expect(lane).toHaveFocus(); expect(lane).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(calls.at(-1)).toMatchObject({ cmd: 'board_add_task', lane: '' });
  });
  it('retains an explicit lane across refreshed defaults and refused creation', async () => {
    const { calls, onClose, store, fail } = setup([], '', 'In Review');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Explicit lane' } });
    fireEvent.change(screen.getByLabelText('Lane'), { target: { value: 'Backlog' } });
    act(() => { store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, group_settings: { Foundation: { board_default_lane: 'In Review' } } })); });
    fail('board_add_task'); fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    await screen.findByText('Write rejected'); expect(screen.getByLabelText('Lane')).toHaveValue('Backlog');
    fail(''); fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(calls.filter((call) => call.cmd === 'board_add_task').map((call) => call.lane)).toEqual(['Backlog', 'Backlog']);
  });
  it('uses named defaults, isolates action drafts and retains focus and explicit values across catalog and state refreshes', async () => {
    const actions = [{ name: 'build', vars: [{ name: 'TASK' }, { name: 'SCOPE', default: 'all' }, { name: 'COUNT', default: 0 }, { name: 'ENABLED', default: false }] }, { name: 'review', vars: [{ name: 'SCOPE', default: 'review' }] }];
    const { calls, refresh, store, onClose } = setup(actions, 'build');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Named variables' } });
    expect(screen.queryByLabelText('TASK')).not.toBeInTheDocument();
    expect(screen.getByLabelText('COUNT')).toHaveValue(0);
    expect(screen.getByLabelText('ENABLED')).not.toBeChecked();
    fireEvent.change(screen.getByLabelText('SCOPE'), { target: { value: 'edited scope' } });
    const input = screen.getByLabelText<HTMLTextAreaElement>('SCOPE'); input.focus(); input.setSelectionRange(2, 6);
    act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'actions', actions })); });
    refresh(structuredClone(actions));
    expect(screen.getByLabelText('SCOPE')).toBe(input); expect(input).toHaveFocus(); expect(input.selectionStart).toBe(2); expect(input.selectionEnd).toBe(6);
    fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'review' } });
    expect(screen.getByLabelText('SCOPE')).toHaveValue('review');
    fireEvent.change(screen.getByLabelText('SCOPE'), { target: { value: 'other scope' } });
    fireEvent.change(screen.getByLabelText('Action'), { target: { value: '' } });
    expect(screen.getByLabelText('SCOPE')).toHaveValue('edited scope');
    fireEvent.change(screen.getByLabelText('SCOPE'), { target: { value: '' } });
    refresh(structuredClone(actions)); expect(screen.getByLabelText('SCOPE')).toHaveValue('');
    fireEvent.click(screen.getByText('External ticket'));
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'github' } });
    fireEvent.change(screen.getByLabelText('External ID'), { target: { value: 'owner/repo#7' } });
    fireEvent.change(screen.getByLabelText('External URL'), { target: { value: 'https://github.com/owner/repo/issues/7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(calls.at(-1)).toMatchObject({ action_name: '', action_vars: { SCOPE: '', COUNT: 0, ENABLED: false }, provider: 'github', external_id: 'owner/repo#7', external_url: 'https://github.com/owner/repo/issues/7' });
  });
  it('previews the unsaved default action without creating a task and retains drafts after failure', async () => {
    const { calls, fail, onClose } = setup([{ name: 'build', vars: [{ name: 'SCOPE', default: 'all' }] }], 'build');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Draft title' } });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Draft scope' } });
    fireEvent.change(screen.getByLabelText('SCOPE'), { target: { value: 'named scope' } });
    fail('preview_prompt'); fireEvent.click(screen.getByRole('button', { name: 'Preview prompt' }));
    await screen.findByText('Write rejected'); expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Description')).toHaveValue('Draft scope');
    fail(''); fireEvent.click(screen.getByRole('button', { name: 'Preview prompt' }));
    await screen.findByText('Rendered unsaved prompt'); expect(screen.getByText('Preview warning')).toBeVisible();
    expect(calls).toHaveLength(2);
    expect(calls.at(-1)).toEqual({ cmd: 'preview_prompt', task: 'Draft title', description: 'Draft scope', action_name: 'build', agent_template: '', action_vars: { SCOPE: 'named scope' }, group: 'Foundation', attachments: [], artifacts: [] });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'New scope' } });
    expect(screen.queryByText('Rendered unsaved prompt')).not.toBeInTheDocument();
    expect(screen.getByText('Draft changed. Preview again to see the current prompt.')).toBeVisible();
  });
  it('retains dependencies, all verification fields and structured evidence after rejected creation', async () => {
    const { calls, onClose, fail } = setup();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Reviewed work' } });
    fireEvent.click(screen.getByText('Dependencies · 0'));
    fireEvent.change(screen.getByLabelText('Search dependencies'), { target: { value: 'Cross-group' } });
    fireEvent.change(screen.getByLabelText('Dependency to add'), { target: { value: 'prerequisite' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add dependency' }));
    expect(screen.getByRole('button', { name: 'Remove dependency prerequisite' })).toBeVisible();
    fireEvent.click(screen.getByText('Verification'));
    fireEvent.change(screen.getByLabelText('Mode'), { target: { value: 'restart' } });
    fireEvent.change(screen.getByLabelText('State'), { target: { value: 'attempted' } });
    fireEvent.change(screen.getByLabelText('Tests run'), { target: { value: 'Targeted checks' } });
    fireEvent.change(screen.getByLabelText('Human validation pending'), { target: { value: 'Review UI' } });
    fireEvent.change(screen.getByLabelText('Verification notes'), { target: { value: 'Retained note' } });
    for (const label of ['Manual smoke done', 'Deploy needed', 'Deploy/restart attempted']) fireEvent.click(screen.getByLabelText(label));
    fireEvent.click(screen.getByText('Attachments and artifacts · 0'));
    fireEvent.click(screen.getByRole('button', { name: 'Add structured artifact' }));
    expect(screen.getByRole('button', { name: 'Create task' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Artifact title'), { target: { value: 'Review context' } });
    fireEvent.change(screen.getByLabelText('Artifact content'), { target: { value: 'Useful excerpt' } });
    fireEvent.change(screen.getByLabelText('Prompt mode'), { target: { value: 'summary' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save artifact' }));
    fail('board_add_task'); fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    await screen.findByText('Write rejected'); expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Verification notes')).toHaveValue('Retained note');
    fail(''); fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(calls.at(-1)).toMatchObject({ cmd: 'board_add_task', depends_on: ['prerequisite'], verification_mode: 'restart', verification_state: 'attempted', verification_notes: 'Retained note', verification_summary: { tests_run: 'Targeted checks', human_validation_pending: 'Review UI', manual_smoke_done: true, deploy_needed: true, deploy_attempted: true }, artifacts: [{ type: 'snippet', title: 'Review context', content: 'Useful excerpt', prompt: { mode: 'summary' } }] });
  });
  it('retains failed removal and cleanup, then discards only its own draft uploads', async () => {
    const { calls, uploads, cleanups, onClose, fail } = setup();
    fireEvent.click(screen.getByText('Attachments and artifacts · 0'));
    fireEvent.change(screen.getByLabelText('Upload evidence files'), { target: { files: [new File(['image'], 'evidence.png', { type: 'image/png' })] } });
    await screen.findByRole('button', { name: 'Remove attachment evidence.png' });
    fail('remove_attachment'); fireEvent.click(screen.getByRole('button', { name: 'Remove attachment evidence.png' }));
    await screen.findByText('Write rejected'); expect(screen.getByRole('link', { name: 'evidence.png' })).toBeVisible();
    fail(''); fireEvent.click(screen.getByRole('button', { name: 'Remove attachment evidence.png' }));
    await waitFor(() => expect(screen.queryByRole('link', { name: 'evidence.png' })).not.toBeInTheDocument());
    expect(calls.at(-1)).toEqual({ cmd: 'remove_attachment', task_id: uploads[0], filename: 'evidence.png' });
    fail('cleanup'); fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await screen.findByText('Cleanup failed'); expect(onClose).not.toHaveBeenCalled();
    fail(''); fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(cleanups.map((body) => JSON.parse(body) as unknown)).toEqual([{ task_id: uploads[0] }, { task_id: uploads[0] }]);
    expect(uploads[0]).toMatch(/^draft-/);
  });
  it('blocks closing and repeated creation while the server acknowledgement is pending', async () => {
    const { onClose } = setup(); let complete!: (value: unknown) => void;
    const fetcher = vi.fn(() => new Promise((resolve) => { complete = resolve; })); vi.stubGlobal('fetch', fetcher);
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Once' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    expect(onClose).not.toHaveBeenCalled(); expect(fetcher).toHaveBeenCalledOnce();
    await act(async () => { complete({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'board_task_added', task_id: 'once', title: 'Once' } }) }); await Promise.resolve(); });
    expect(onClose).toHaveBeenCalledOnce();
  });
});
