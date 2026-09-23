import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useCallback, useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import type { UnknownRecord } from '../../protocol';
import { BoardSyncSettings } from './BoardSyncSettings';
const initial = { default_directory: '/draft/project', board_sync_provider: 'github', board_sync_enabled: false, board_sync_github: { github_repo: 'manual/repo', github_project_owner: 'team', github_project_number: 0, github_project_id: '', github_lane_status_map: {} } };
const project = { owner: 'team', number: 7, id: 'P7', name: 'Delivery' };
const reply = (data: UnknownRecord) => new Response(JSON.stringify({ ok: true, data }), { status: 200 });
const frame = (extra: UnknownRecord = {}) => ({ type: 'board_sync_preflight', ok: true, group: 'QA', provider: 'github', repo: 'actual/repository', ...extra });
function Form() {
  const [settings, setSettings] = useState<UnknownRecord>(initial);
  const change = useCallback((github: UnknownRecord) => setSettings((value) => ({ ...value, board_sync_github: github })), []);
  return <><BoardSyncSettings group="QA" settings={settings} onChange={change} /><button onClick={() => change({ ...initial.board_sync_github, github_repo: 'edited/repo', github_lane_status_map: { Review: 'Manual' } })}>Edit during check</button><output aria-label="Settings draft">{JSON.stringify(settings)}</output></>;
}
function savedDraft() { return JSON.parse(screen.getByLabelText('Settings draft').textContent) as typeof initial; }
afterEach(() => vi.unstubAllGlobals());
it('discovers projects from unsaved settings, resolves selection, and fills only the empty lane map', async () => {
  const calls: UnknownRecord[] = [];
  vi.stubGlobal('fetch', vi.fn((_url, options: RequestInit) => {
    const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as UnknownRecord; calls.push(command);
    return Promise.resolve(reply(command.cmd === 'board_sync_list_projects' ? { type: command.cmd, ok: true, group: 'QA', provider: 'github', projects: [project], errors: [{ error: 'Other owner denied' }] } : frame({ project_owner: 'team', project_number: 7, project_id: 'P7', status_options: { Ready: 'S1' }, lane_status_map_suggestion: { Todo: 'Ready' }, lane_status_map_strategy: 'position', lane_status_map_unmatched_lanes: ['Review'] }))); 
  }));
  render(<Form />); expect(calls).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Load GitHub projects' })); await screen.findByRole('option', { name: /Delivery/ });
  expect(calls[0]).toMatchObject({ group: 'QA', provider: 'github', owner: 'team', settings: { board_sync_provider: initial.board_sync_provider, board_sync_enabled: false, board_sync_github: initial.board_sync_github } });
  expect(screen.getByRole('alert')).toHaveTextContent('Other owner denied');
  fireEvent.change(screen.getByRole('combobox'), { target: { value: JSON.stringify(['team', 7, 'P7']) } });
  await screen.findByText(/Filled empty lane mapping/); expect(savedDraft().board_sync_github).toMatchObject({ github_project_number: 7, github_project_id: 'P7', github_lane_status_map: { Todo: 'Ready' } });
  expect(screen.getByText(/Project status options/)).toHaveTextContent('Ready'); expect(screen.getByText(/unmatched lanes/)).toHaveTextContent('Review');
  fireEvent.click(screen.getByRole('button', { name: 'Edit during check' })); fireEvent.click(screen.getByRole('button', { name: 'Test GitHub connection' }));
  await screen.findByText(/GitHub connection OK/); expect(savedDraft().board_sync_github.github_lane_status_map).toEqual({ Review: 'Manual' });
  expect(calls.map((call) => call.cmd)).toEqual(['board_sync_list_projects', 'board_sync_preflight', 'board_sync_preflight']);
});
it('uses current repository by querying without the manual repo, then applies the resolved draft', async () => {
  const fetcher = vi.fn(() => Promise.resolve(reply(frame()))); vi.stubGlobal('fetch', fetcher); render(<Form />);
  fireEvent.click(screen.getByRole('button', { name: 'Use current repository' })); await screen.findByText(/GitHub connection OK/);
  const options = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(JSON.parse(typeof options[1].body === 'string' ? options[1].body : '{}')).toMatchObject({ settings: { board_sync_github: { github_repo: '' } } });
  expect(savedDraft().board_sync_github.github_repo).toBe('actual/repository');
});
it('cancels stale edits and unmounts, and ignores late successful replies', async () => {
  let resolve: (value: Response) => void = () => {}; let signal: AbortSignal | null | undefined;
  vi.stubGlobal('fetch', vi.fn((_url, options: RequestInit) => { signal = options.signal; return new Promise<Response>((done) => { resolve = done; }); }));
  const mounted = render(<Form />); fireEvent.click(screen.getByRole('button', { name: 'Use current repository' }));
  fireEvent.click(screen.getByRole('button', { name: 'Edit during check' })); expect(signal?.aborted).toBe(true);
  await act(async () => { resolve(reply(frame())); await Promise.resolve(); }); expect(savedDraft().board_sync_github.github_repo).toBe('edited/repo');
  fireEvent.click(screen.getByRole('button', { name: 'Test GitHub connection' })); mounted.unmount(); expect(signal?.aborted).toBe(true);
  await act(async () => { resolve(reply(frame())); await Promise.resolve(); });
});
it('shows actionable failures, retries, rejects foreign responses, and handles empty discovery', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(reply(frame({ ok: false, phase: 'project_scope', error: 'Missing project permission' }))).mockResolvedValueOnce(reply(frame({ group: 'Other' }))).mockResolvedValueOnce(reply({ type: 'board_sync_list_projects', group: 'QA', provider: 'github', ok: true, projects: [] }));
  vi.stubGlobal('fetch', fetcher); render(<Form />);
  fireEvent.click(screen.getByRole('button', { name: 'Test GitHub connection' })); expect(await screen.findByRole('alert')).toHaveTextContent('gh auth refresh -s project');
  fireEvent.click(screen.getByRole('button', { name: 'Test GitHub connection' })); await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('GitHub configuration check failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Load GitHub projects' })); await screen.findByText(/No accessible projects/); expect(savedDraft()).toEqual(initial);
});
it('does not replay a completed check when an operator changes and resets the configuration', async () => {
  const fetcher = vi.fn(() => Promise.resolve(reply(frame()))); vi.stubGlobal('fetch', fetcher); const onChange = vi.fn();
  const view = render(<BoardSyncSettings group="QA" settings={initial} onChange={onChange} />);
  fireEvent.click(screen.getByRole('button', { name: 'Test GitHub connection' })); await screen.findByText(/GitHub connection OK/);
  view.rerender(<BoardSyncSettings group="QA" settings={{ ...initial, board_sync_github: { ...initial.board_sync_github, github_repo: 'changed/repo' } }} onChange={onChange} />);
  view.rerender(<BoardSyncSettings group="QA" settings={initial} onChange={onChange} />);
  await act(async () => { await Promise.resolve(); }); expect(fetcher).toHaveBeenCalledTimes(1);
});
it('cancels draft checks when a save starts, so late suggestions cannot become unacknowledged edits', async () => {
  let resolve: (value: Response) => void = () => {}; let signal: AbortSignal | null | undefined;
  vi.stubGlobal('fetch', vi.fn((_url, options: RequestInit) => { signal = options.signal; return new Promise<Response>((done) => { resolve = done; }); }));
  const onChange = vi.fn(); const view = render(<BoardSyncSettings group="QA" settings={initial} onChange={onChange} />);
  fireEvent.click(screen.getByRole('button', { name: 'Use current repository' }));
  view.rerender(<BoardSyncSettings group="QA" settings={initial} onChange={onChange} disabled />); expect(signal?.aborted).toBe(true);
  await act(async () => { resolve(reply(frame({ lane_status_map_suggestion: { Todo: 'Ready' } }))); await Promise.resolve(); }); expect(onChange).not.toHaveBeenCalled();
  view.rerender(<BoardSyncSettings group="QA" settings={initial} onChange={onChange} />);
  expect(screen.getByRole('button', { name: 'Test GitHub connection' })).toBeEnabled(); expect(screen.getByRole('status')).toHaveTextContent('Check canceled');
});
