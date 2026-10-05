import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { PipelineExplorer } from './PipelineExplorer';
import { HistoryPanel } from './HistoryPanel';
import { HelpPanel } from './HelpPanel';
import { ContextPanel } from './ContextPanel';
import { SupervisorDetails } from './OperationalDetails';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const kinds = ['pipelines', 'history', 'help', 'context', 'supervisor'] as const;
type Kind = typeof kinds[number];
const commands: Record<Kind, string> = { pipelines: 'discover_pipelines', history: 'get_agent_history', help: 'help_list', context: 'memory_list', supervisor: 'supervisor_sessions_list' };
const retryNames: Record<Kind, string> = { pipelines: 'Discover pipelines', history: 'Retry history', help: 'Retry topics', context: 'Retry context', supervisor: 'Refresh sessions' };
const flush = async () => { await act(async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); }); };
beforeEach(() => { read.mockReset(); vi.useFakeTimers(); });
afterEach(() => vi.useRealTimers());
function setup(kind: Kind, held = '') {
  let hold = held; let revision = 0;
  let entry: UnknownRecord = { id: 'entry', title: 'Retained context', content: 'Original context', scope_kind: 'group', scope_ref: 'Foundation', entry_type: 'note', pinned: false };
  const calls: { command: TorqueCommand; signal: AbortSignal }[] = [];
  const pending: { frame: AuxiliaryFrame; resolve: (frame: AuxiliaryFrame) => void }[] = [];
  const run = { id: 'run', name: 'Recorded worker', group: 'Foundation', kind: 'worker', status: 'merged' };
  const topic = { topic_id: 'README.md', title: 'Maintained guide', source_path: 'README.md' };
  read.mockImplementation((command, signal) => {
    calls.push({ command, signal }); let frame: AuxiliaryFrame;
    if (command.cmd === 'discover_pipelines') frame = { type: 'pipelines', group: 'Foundation', pipelines: [{ name: `Pipeline ${revision}`, actions: ['build', 'review'], edges: [{ from: 'build', to: 'review', when: 'Ready' }], asks: [] }] };
    else if (command.cmd === 'get_agent_history') frame = { type: 'agent_history_list', records: [run] };
    else if (command.cmd === 'get_agent_history_detail') frame = { type: 'agent_history_detail', record: run, tasks: [], messages: [{ id: 'm', message: `Recorded message ${revision}` }] };
    else if (command.cmd === 'help_list') frame = { type: 'help_topics', topics: [topic] };
    else if (command.cmd === 'help_show') frame = { ...topic, type: 'help_topic', path_anchor: command.topic, body_excerpt: `Document ${revision}` };
    else if (command.cmd === 'help_search') frame = { type: 'help_search', query: command.query, results: [topic] };
    else if (command.cmd === 'help_query') frame = { type: 'help_query', question: command.question, answer: `Answer ${revision}`, sources: [topic] };
    else if (command.cmd === 'supervisor_sessions_list') frame = { type: 'supervisor_sessions', sessions: [{ session_id: 'session', owner: { name: 'Retained worker' }, pid: 7 + revision, alive: true }] };
    else if (command.cmd === 'memory_list') frame = { type: 'memory_entries', group_name: 'Foundation', entries: [structuredClone(entry)] };
    else { entry = { ...entry, ...command, id: 'entry', ...(command.cmd === 'memory_pin' ? { pinned: true } : command.cmd === 'memory_unpin' ? { pinned: false } : {}) }; frame = { type: 'memory_entry', entry: structuredClone(entry) }; }
    return command.cmd === hold ? new Promise((resolve) => pending.push({ frame, resolve })) : Promise.resolve(frame);
  });
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1, reconnect: false })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture));
  const view = render(<Provider store={store}>{kind === 'pipelines' ? <PipelineExplorer group="Foundation" onEdit={vi.fn()} /> : kind === 'history' ? <HistoryPanel group="Foundation" send={vi.fn()} /> : kind === 'help' ? <HelpPanel /> : kind === 'context' ? <ContextPanel group="Foundation" agents={[]} /> : <SupervisorDetails supervisor={{}} send={vi.fn()} onTerminate={vi.fn()} />}</Provider>);
  return { ...view, calls, pending, hold: (cmd: string) => { hold = cmd; }, remote: () => { revision++; }, reconnect: async () => { act(() => { store.dispatch(connectionActions.connected({ at: Date.now(), reconnect: true })); store.dispatch(connectionActions.snapshotAccepted(compactStateFixture)); }); await flush(); }, release: async (index = 0) => { pending[index]!.resolve(pending[index]!.frame); await flush(); }, advance: async (ms: number) => { await act(() => vi.advanceTimersByTimeAsync(ms)); } };
}
it.each(kinds)('%s initial read times out, offers retry and ignores the late first result', async (kind) => {
  const h = setup(kind, commands[kind]); await flush(); await h.advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('refresh timed out'); expect(h.calls[0]!.signal.aborted).toBe(true);
  h.hold(''); h.remote(); fireEvent.click(screen.getByRole('button', { name: retryNames[kind] })); await flush(); expect(screen.queryByRole('alert')).not.toBeInTheDocument(); const accepted = h.container.textContent; await h.release(); expect(h.container.textContent).toBe(accepted);
});
it('pipeline refresh timeout preserves the graph, zoom, pan and focused node', async () => {
  const h = setup('pipelines'); await flush(); const graph = screen.getByRole('group', { name: 'Pipeline graph' }); fireEvent.click(screen.getByRole('button', { name: 'Zoom in pipeline' })); fireEvent.keyDown(graph, { key: 'ArrowRight' }); const node = within(graph).getByRole('button', { name: 'Edit action review' }); act(() => node.focus()); const bounds = graph.getAttribute('viewBox');
  h.hold('discover_pipelines'); await h.reconnect(); await h.advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(graph).toHaveAttribute('viewBox', bounds); expect(node).toHaveFocus(); expect(node).toHaveAttribute('aria-pressed', 'true'); await h.release(); expect(screen.getByRole('alert')).toHaveTextContent('timed out');
});
it('history list and detail expire independently without replacing retained reading state', async () => {
  const h = setup('history'); await flush(); fireEvent.click(screen.getByRole('button', { name: /Recorded worker/ })); await flush(); const search = screen.getByRole<HTMLInputElement>('textbox', { name: 'Search history' }); fireEvent.change(search, { target: { value: 'Recorded' } }); search.focus(); search.setSelectionRange(1, 4); const detail = screen.getByRole('region', { name: 'Run detail' }); detail.scrollTop = 90;
  h.hold('get_agent_history_detail'); h.remote(); await h.reconnect(); await h.advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.getByText('Recorded message 0')).toBeVisible(); expect(search).toHaveFocus(); expect([search.selectionStart, search.selectionEnd]).toEqual([1, 4]); expect(detail.scrollTop).toBe(90); await h.release(); expect(screen.queryByText('Recorded message 1')).not.toBeInTheDocument();
  h.hold(''); fireEvent.click(screen.getByRole('button', { name: 'Retry run' })); await flush(); expect(screen.getByText('Recorded message 1')).toBeVisible();
});
it.each([['help_list', 'Retry topics'], ['help_show', 'Retry detail'], ['help_search', 'Retry search'], ['help_query', 'Retry answer']])('Help bounds %s independently while keeping the current article and drafts', async (cmd, retry) => {
  const h = setup('help'); await flush(); const question = screen.getByLabelText<HTMLInputElement>('Question for the docs'); fireEvent.change(question, { target: { value: 'Asked question' } }); fireEvent.click(screen.getByRole('button', { name: 'Answer from docs' })); fireEvent.change(screen.getByLabelText('Search documentation'), { target: { value: 'Applied query' } }); fireEvent.click(screen.getByRole('button', { name: 'Search' })); await flush(); fireEvent.change(question, { target: { value: 'Unsubmitted draft' } }); question.focus(); question.setSelectionRange(2, 5); h.hold(cmd); h.remote(); await h.reconnect(); await h.advance(15_001);
  expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(question).toHaveValue('Unsubmitted draft'); expect(question).toHaveFocus(); expect([question.selectionStart, question.selectionEnd]).toEqual([2, 5]); expect(screen.getByText(cmd === 'help_show' ? 'Document 0' : 'Document 1')).toBeVisible(); await h.release(); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); h.hold(''); fireEvent.click(screen.getByRole('button', { name: retry })); await flush(); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('Context retains an edited entry through stalled refresh, then retries without losing caret', async () => {
  const h = setup('context'); await flush(); fireEvent.click(screen.getByRole('button', { name: 'Edit' })); const body = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Content' }); fireEvent.change(body, { target: { value: 'Retained local draft' } }); body.focus(); body.setSelectionRange(2, 8); h.hold('memory_list'); await h.reconnect(); await h.advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(body).toHaveFocus(); expect(body).toHaveValue('Retained local draft'); expect([body.selectionStart, body.selectionEnd]).toEqual([2, 8]); await h.release(); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); h.hold(''); fireEvent.click(screen.getByRole('button', { name: 'Retry context' })); await flush(); expect(body).toHaveValue('Retained local draft');
});
it.each(['save', 'publish', 'pin', 'unpin'])('Context %s timeout unlocks the owner, retains intent and never replays on reconnect', async (mode) => {
  const h = setup('context'); await flush(); const command = mode === 'pin' || mode === 'unpin' ? `memory_${mode}` : 'memory_publish';
  if (mode === 'unpin') { fireEvent.click(screen.getByRole('button', { name: 'Pin' })); await flush(); }
  if (mode === 'save' || mode === 'publish') { fireEvent.click(screen.getByRole('button', { name: mode === 'save' ? 'Edit' : '＋ Add context' })); fireEvent.change(screen.getByRole('textbox', { name: 'Content' }), { target: { value: 'Retained mutation draft' } }); }
  h.hold(command); fireEvent.click(screen.getByRole('button', { name: mode === 'save' ? 'Save context' : mode === 'publish' ? 'Publish context' : mode === 'pin' ? 'Pin' : 'Unpin' })); await flush(); expect(screen.getByRole('button', { name: '＋ Add context' })).toBeDisabled(); await h.reconnect(); expect(h.calls.filter((call) => call.command.cmd === command)).toHaveLength(1); await h.advance(30_001); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.getByRole('button', { name: '＋ Add context' })).toBeEnabled(); await h.release(); await h.reconnect(); expect(h.calls.filter((call) => call.command.cmd === command)).toHaveLength(1); if (mode === 'save' || mode === 'publish') expect(screen.getByRole('textbox', { name: 'Content' })).toHaveValue('Retained mutation draft');
});
it('Supervisor resumes automatic polling after timeout and retains expanded sessions', async () => {
  const h = setup('supervisor'); await flush(); const row = screen.getByRole('button', { name: /^Retained worker / }); fireEvent.click(row); row.focus(); h.hold('supervisor_sessions_list'); await h.advance(2_000); await h.advance(15_001); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(row).toHaveFocus(); expect(row).toHaveAttribute('aria-expanded', 'true'); h.hold(''); h.remote(); await h.advance(2_000); expect(screen.queryByRole('alert')).not.toBeInTheDocument(); expect(row).toHaveTextContent('8'); await h.release(); expect(row).toHaveTextContent('8');
});
it.each(kinds)('%s cancels pending hidden reads without accepting late results or retrying', async (kind) => {
  const h = setup(kind, commands[kind]); await flush(); h.unmount(); expect(h.calls[0]!.signal.aborted).toBe(true); await h.release(); await h.advance(20_000); expect(h.calls).toHaveLength(1);
});

it.each(['save', 'publish'])('Context aborts a hidden %s without accepting its late acknowledgement', async (mode) => {
  const h = setup('context'); await flush(); fireEvent.click(screen.getByRole('button', { name: mode === 'save' ? 'Edit' : '＋ Add context' })); fireEvent.change(screen.getByRole('textbox', { name: 'Content' }), { target: { value: 'Pending hidden draft' } }); h.hold('memory_publish'); fireEvent.click(screen.getByRole('button', { name: mode === 'save' ? 'Save context' : 'Publish context' })); await flush(); h.unmount(); expect(h.calls.at(-1)?.signal.aborted).toBe(true); await h.release(); await h.advance(30_001); expect(h.calls.filter((call) => call.command.cmd === 'memory_publish')).toHaveLength(1);
});
