import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { HelpPanel } from './HelpPanel';
import { HelpMarkdown } from './HelpMarkdown';
import { helpLink, string } from './helpModel';
afterEach(() => vi.unstubAllGlobals());
const path = 'docs/operate/help.md'; const sectionRef = `${path}#start`;
const sourceModel = { cache: 'Loaded at query time', allowlist: 'Maintained docs', source_paths: [path, 'README.md'] };
const topic = { topic_id: 'help', title: 'Help guide', summary: 'Guide summary', source_path: path, audience_tags: ['user'], restricted_safe: true, source_hash: 'source-123', updated_at: '2026-09-22T12:00:00Z', sections: [{ id: 'start', title: 'Start', path_anchor: sectionRef, line_start: 3, line_end: 9 }], examples: ['torque doctor'] };
function setup() {
  let refusal = ''; let held = ''; let mismatch = ''; let body = 'Original document body'; let notFound = false; let noExamples = false; let reversed = false;
  const calls: TorqueCommand[] = []; const signals: AbortSignal[] = []; const waiting: (() => void)[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command); signals.push(options?.signal as AbortSignal);
    let data: UnknownRecord = {};
    if (command.cmd === 'help_list') data = { type: 'help_topics', topics: command.audience === 'maintainer' ? [] : [topic, { ...topic, topic_id: 'readme', title: 'Read me', source_path: 'README.md' }], source_model: sourceModel };
    if (command.cmd === 'help_list' && reversed) (data.topics as UnknownRecord[]).reverse();
    if (command.cmd === 'help_show') data = notFound ? { type: 'help_topic', status: 'not_found', message: 'Topic removed' } : { ...topic, type: 'help_topic', status: 'ok', title: command.topic === sectionRef ? 'Start' : 'Help guide', source_path: string(command.topic).split('#')[0], path_anchor: command.topic, anchor: command.topic === sectionRef ? 'start' : '', body_excerpt: body, truncated: command.topic !== sectionRef, index_hash: 'index-456', source_model: sourceModel, ...(noExamples ? { examples: [] } : {}) };
    if (command.cmd === 'help_search') data = { type: 'help_search', query: command.query, results: command.query === 'nothing' ? [] : [{ ...topic, title: 'Search match', path_anchor: sectionRef }] };
    if (command.cmd === 'help_query') data = { type: 'help_query', question: command.question, answer: `Answer for ${string(command.question)}`, sources: [{ title: 'Start source', path_anchor: sectionRef }] };
    if (command.cmd === mismatch) data = { ...data, path_anchor: 'wrong.md', query: 'wrong', question: 'wrong' };
    const response = { ok: true, json: () => Promise.resolve(command.cmd === refusal ? { ok: false, error: 'Injected Help refusal' } : { ok: true, data }) };
    return command.cmd === held ? new Promise((resolve) => waiting.push(() => resolve(response))) : Promise.resolve(response);
  }));
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const view = render(<Provider store={store}><HelpPanel /></Provider>);
  return { ...view, calls, signals, store, refuse: (cmd: string) => { refusal = cmd; }, hold: (cmd: string) => { held = cmd; }, mismatch: (cmd: string) => { mismatch = cmd; }, body: (value: string) => { body = value; }, notFound: () => { notFound = true; }, noExamples: () => { noExamples = true; }, reverse: () => { reversed = true; }, release: async () => { await act(async () => { waiting.shift()!(); await Promise.resolve(); }); }, reconnect: async () => { await act(async () => { store.dispatch(connectionActions.connected({ at: Date.now(), reconnect: true })); await Promise.resolve(); }); } };
}
function search(value: string) { fireEvent.change(screen.getByLabelText('Search documentation'), { target: { value } }); fireEvent.click(screen.getByRole('button', { name: 'Search' })); }
function ask(value: string) { fireEvent.change(screen.getByLabelText('Question for the docs'), { target: { value } }); fireEvent.click(screen.getByRole('button', { name: 'Answer from docs' })); }
it('keeps explicit search mode for empty results and clears it with All topics or Escape', async () => {
  const test = setup(); await screen.findByText('Original document body'); search('nothing'); await screen.findByText('No matching documentation for “nothing”.');
  expect(within(screen.getByRole('complementary', { name: 'Help topics' })).queryByRole('button', { name: /Help guide/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'All topics' })); expect(screen.getByLabelText('Search documentation')).toHaveValue(''); expect(screen.getByRole('button', { name: /Help guide/ })).toBeVisible();
  search('start'); await screen.findByText('Search match'); fireEvent.keyDown(screen.getByLabelText('Search documentation'), { key: 'Escape' }); expect(screen.queryByText('Search match')).not.toBeInTheDocument();
  search('  '); expect(screen.getByText('Enter a search query.')).toBeVisible(); expect(test.calls.filter((call) => call.cmd === 'help_search')).toHaveLength(2);
});
it('offers all audiences, reads the chosen one, and distinguishes empty topics', async () => {
  const test = setup(); await screen.findByText('Original document body'); const audience = screen.getByLabelText('Audience for topics');
  expect(within(audience).getAllByRole('option').map((item) => (item as HTMLOptionElement).value)).toEqual(['', 'user', 'operator', 'agent', 'worker', 'engineer', 'architect', 'maintainer']);
  for (const value of ['user', 'operator', 'agent', 'worker', 'engineer', 'architect', 'maintainer', '']) { fireEvent.change(audience, { target: { value } }); await waitFor(() => expect(test.calls.filter((call) => call.cmd === 'help_list').at(-1)).toMatchObject({ audience: value })); if (value === 'maintainer') await screen.findByText('No topics for this audience.'); }
});
it('navigates section metadata and answer sources and exposes provenance, bounds and examples', async () => {
  setup(); await screen.findByText('Original document body'); expect(screen.getByText(/This is a bounded excerpt/)).toBeVisible();
  fireEvent.click(screen.getByText('Source and freshness')); expect(screen.getByText('source-123')).toBeVisible(); expect(screen.getByText('index-456')).toBeVisible(); expect(screen.getByText('Loaded at query time')).toBeVisible();
  fireEvent.click(screen.getByText('Extracted examples')); expect(screen.getByText('torque doctor')).toBeVisible();
  fireEvent.click(screen.getByText('Sections · 1')); fireEvent.click(screen.getByText('Start · lines 3–9')); fireEvent.click(screen.getByRole('button', { name: 'Open section: Start' })); await screen.findByRole('button', { name: 'Open whole topic' }); expect(screen.getByRole('article', { name: 'Help document' })).toHaveFocus(); expect(screen.queryByText(/This is a bounded excerpt/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Open whole topic' })); await waitFor(() => expect(screen.queryByRole('button', { name: 'Open whole topic' })).not.toBeInTheDocument());
  ask('How to start?'); await screen.findByText('Answer for How to start?'); fireEvent.click(screen.getByRole('button', { name: `Start source · ${sectionRef}` })); await screen.findByRole('button', { name: 'Open whole topic' });
});
it('refreshes applied requests without replacing drafts, focus, caret, scroll or disclosures', async () => {
  const test = setup(); await screen.findByText('Original document body'); search('start'); ask('first'); await screen.findByText('Answer for first');
  const input = screen.getByLabelText<HTMLInputElement>('Question for the docs'); fireEvent.change(input, { target: { value: 'Unsubmitted question' } }); input.focus(); input.setSelectionRange(2, 7);
  fireEvent.change(screen.getByLabelText('Search documentation'), { target: { value: 'Unsubmitted search' } });
  const disclosure = screen.getByText('Source and freshness').parentElement as HTMLDetailsElement; disclosure.open = true;
  const article = screen.getByRole('article', { name: 'Help document' }); article.scrollTop = 150;
  test.body('Refreshed document body'); await test.reconnect(); await screen.findByText('Refreshed document body');
  expect(input).toHaveFocus(); expect(input).toHaveValue('Unsubmitted question'); expect([input.selectionStart, input.selectionEnd]).toEqual([2, 7]); expect(article.scrollTop).toBe(150); expect(disclosure.open).toBe(true);
  expect(test.calls.filter((call) => call.cmd === 'help_query').at(-1)).toMatchObject({ question: 'first' }); expect(test.calls.filter((call) => call.cmd === 'help_search').at(-1)).toMatchObject({ query: 'start' });
});
it('retains accepted detail after refresh refusal and retries without unrelated reads', async () => {
  const test = setup(); await screen.findByText('Original document body'); test.refuse('help_show'); await test.reconnect(); await screen.findByRole('alert'); expect(screen.getByText('Original document body')).toBeVisible();
  const count = test.calls.filter((call) => call.cmd === 'help_list').length; test.refuse(''); test.body('Retry succeeded'); fireEvent.click(screen.getByRole('button', { name: 'Retry detail' })); await screen.findByText('Retry succeeded'); expect(test.calls.filter((call) => call.cmd === 'help_list')).toHaveLength(count);
});
it('rejects wrong topic, search and answer responses and surfaces not-found detail', async () => {
  const test = setup(); await screen.findByText('Original document body'); test.mismatch('help_show'); fireEvent.click(screen.getByRole('button', { name: /Read me/ })); await screen.findByText(/did not match the selected topic/); expect(screen.queryByText('Original document body')).not.toBeInTheDocument();
  test.mismatch('help_search'); search('start'); await screen.findByText(/did not match this search/);
  test.mismatch('help_query'); ask('first'); await screen.findByText(/did not match this question/); expect(screen.queryByText('Answer for first')).not.toBeInTheDocument();
  test.mismatch(''); test.notFound(); fireEvent.click(screen.getByRole('button', { name: 'Retry detail' })); await screen.findByText(/Topic removed/);
});
it('ignores late document responses after selection and cancels hidden reads', async () => {
  const test = setup(); await screen.findByText('Original document body'); test.hold('help_show'); test.body('Late body'); await test.reconnect(); const index = test.calls.map((call) => call.cmd).lastIndexOf('help_show');
  test.hold(''); test.body('Current body'); fireEvent.click(screen.getByRole('button', { name: /Read me/ })); await screen.findByText('Current body'); expect(test.signals[index]?.aborted).toBe(true); await test.release(); expect(screen.queryByText('Late body')).not.toBeInTheDocument();
  test.hold('help_show'); await test.reconnect(); const last = test.calls.map((call) => call.cmd).lastIndexOf('help_show'); test.unmount(); expect(test.signals[last]?.aborted).toBe(true); await test.release(); const count = test.calls.length; await test.reconnect(); expect(test.calls).toHaveLength(count);
});
it('clears stale answers on a new question and ignores late search or answer responses', async () => {
  const test = setup(); await screen.findByText('Original document body'); ask('first'); await screen.findByText('Answer for first');
  test.hold('help_query'); ask('second'); expect(screen.queryByText('Answer for first')).not.toBeInTheDocument(); const index = test.calls.length - 1; fireEvent.keyDown(screen.getByLabelText('Question for the docs'), { key: 'Escape' }); expect(test.signals[index]?.aborted).toBe(true); await test.release(); expect(screen.queryByRole('region', { name: 'Documentation answer' })).not.toBeInTheDocument();
  test.hold('help_search'); search('start'); const searchIndex = test.calls.length - 1; fireEvent.click(screen.getByRole('button', { name: 'All topics' })); expect(test.signals[searchIndex]?.aborted).toBe(true); await test.release(); expect(screen.queryByText('Search match')).not.toBeInTheDocument();
});
it('shows empty examples and supports independent list/search/answer retry', async () => {
  const test = setup(); await screen.findByText('Original document body'); test.noExamples(); await test.reconnect(); fireEvent.click(screen.getByText('Extracted examples')); await screen.findByText('No extracted examples for this topic.');
  for (const [cmd, button] of [['help_list', 'Retry topics'], ['help_search', 'Retry search'], ['help_query', 'Retry answer']]) {
    test.refuse(cmd!); if (cmd === 'help_list') await test.reconnect(); else if (cmd === 'help_search') search('start'); else ask('first');
    const retry = await screen.findByRole('button', { name: button! }); test.refuse(''); fireEvent.click(retry); await waitFor(() => expect(screen.queryByRole('button', { name: button! })).not.toBeInTheDocument());
  }
});
it('renders structured Markdown as safe React nodes with indexed source navigation', () => {
  const open = vi.fn(); const body = '# Heading\n\n**Strong** and *emphasis* with `code`.\n\n- First\n  - Nested\n- Second\n\n3. Third\n4. Fourth\n\n> Quoted\n\n| Name | Value |\n| --- | --- |\n| alpha | `one` |\n\n```html\n<script>alert(1)</script>\n```\n\n[Safe](https://example.com/path_(one)) [Section](#start) [Relative](../../README.md) [Bad](javascript:alert(1))\n\n<img src=x onerror=alert(1)>\n<script>alert(2)</script>';
  const { container } = render(<HelpMarkdown source={path} paths={[path, 'README.md']} onOpen={open}>{body}</HelpMarkdown>);
  expect(screen.getByRole('heading', { name: 'Heading' })).toBeVisible(); expect(screen.getByRole('table')).toHaveTextContent('alpha'); expect(screen.getAllByRole('list')).toHaveLength(3); expect(container.querySelector('ol')).toHaveAttribute('start', '3'); expect(container.querySelector('strong')).toHaveTextContent('Strong'); expect(container.querySelector('em')).toHaveTextContent('emphasis'); expect(container.querySelector('blockquote')).toHaveTextContent('Quoted'); expect(container.querySelector('pre code')).toHaveTextContent('<script>alert(1)</script>'); expect(container.querySelector('script, img')).toBeNull();
  expect(screen.getByRole('link', { name: 'Safe' })).toHaveAttribute('rel', 'noopener noreferrer'); expect(screen.queryByRole('link', { name: 'Bad' })).not.toBeInTheDocument(); fireEvent.click(screen.getByRole('link', { name: 'Section' })); expect(open).toHaveBeenCalledWith(sectionRef); fireEvent.click(screen.getByRole('link', { name: 'Relative' })); expect(open).toHaveBeenCalledWith('README.md');
});
it('rejects executable, malformed, external-relative and unindexed source links', () => {
  for (const href of ['javascript:alert(1)', 'data:text/html,test', 'file:///etc/passwd', '//example.com', '\\example.com', 'https://example.com\nattack', '../secret.md', '%ZZ']) expect(helpLink(href, path, [path])).toEqual({});
  expect(helpLink('mailto:help@example.com', path, [])).toEqual({ external: 'mailto:help@example.com' });
});

it('retains the initial selection when refreshed topics are reordered and focuses empty sections', async () => {
  const test = setup(); await screen.findByText('Original document body'); test.reverse(); await test.reconnect();
  expect(test.calls.filter((call) => call.cmd === 'help_show').at(-1)).toMatchObject({ topic: path });
  test.body(''); fireEvent.click(screen.getByRole('button', { name: /Read me/ })); await waitFor(() => expect(screen.getByRole('article', { name: 'Help document' })).toHaveFocus());
});
it('renders multiline emphasis, linked badges and automatic safe URLs without interpreting HTML', () => {
  render(<HelpMarkdown source={path} paths={[path]} onOpen={() => {}}>{'# C#\n\n**Claude\nCode** and [![Docs](https://example.com/badge.svg)](https://example.com/docs).\n\nVisit https://example.com/path_(one). or mailto:help@example.com'}</HelpMarkdown>);
  expect(screen.getByRole('heading', { name: 'C#' })).toBeVisible(); expect(screen.getByText('Claude Code').tagName).toBe('STRONG');
  expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute('href', 'https://example.com/docs'); expect(screen.getByRole('link', { name: 'https://example.com/path_(one)' })).toHaveAttribute('href', 'https://example.com/path_(one)'); expect(screen.getByRole('link', { name: 'mailto:help@example.com' })).toBeVisible();
});
