import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAppStore } from '../../app/store';
import { DirectMessages } from './DirectMessages';
import { messageView, orderedMessages } from './directMessageModel';
import { safeMarkdownLink } from '../../design/markdownLinks';
const agent = { id: 'worker', name: 'Wren' };
const messages = (count: number) => Array.from({ length: count }, (_, index) => ({ id: `m-${String(index).padStart(3, '0')}`, message: `Message ${index}`, timestamp: index + 1, sender_kind: 'worker', sender_id: agent.id, recipient_kind: 'user' }));
function setup(initial: unknown = messages(65)) {
  const store = createAppStore(); const onReply = vi.fn();
  const content = (rows: unknown, id = agent.id, pending = false, active = true) => <Provider store={store}><DirectMessages key={id} agent={{ ...agent, id }} messages={rows} pending={pending} active={active} onReply={onReply} /></Provider>;
  const view = render(content(initial));
  return { ...view, store, onReply, show: (rows: unknown, id = agent.id, pending = false, active = true) => view.rerender(content(rows, id, pending, active)) };
}
function geometry() {
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) { return this.getAttribute('role') === 'log' ? 100 : 0; });
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(function (this: HTMLElement) { return this.querySelectorAll('[data-message-id]').length * 40; });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const index = this.dataset.messageId ? [...this.parentElement!.children].indexOf(this) : 0;
    const top = this.dataset.messageId ? index * 40 - this.parentElement!.scrollTop : 0;
    return { x: 0, y: top, top, bottom: top + 40, left: 0, right: 200, width: 200, height: 40, toJSON: () => ({}) };
  });
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); window.getSelection()?.removeAllRanges(); });
describe('retained direct-message reading', () => {
  it('progressively reveals all retained rows, sorts ties, and labels the bounded snapshot', () => {
    setup([...messages(65)].reverse()); expect(screen.getByText('30 of 65 retained')).toBeVisible(); expect(screen.queryByText('Message 0', { exact: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load older messages (35)' })); expect(screen.getByText('60 of 65 retained')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Load older messages (5)' })); expect(screen.getByText('Message 0', { exact: true })).toBeVisible(); expect(screen.getByRole('button', { name: 'Load older messages' })).toBeDisabled();
    expect(orderedMessages([{ id: 'b', timestamp: 1 }, { id: 'a', timestamp: 1 }]).map((row) => row.id)).toEqual(['a', 'b']);
  });
  it('preserves an anchor through prepend, append, reconnect and switching, then resumes following', () => {
    geometry(); const { store, show } = setup(); const log = screen.getByRole('log'); expect(log.scrollTop).toBe(1100);
    log.scrollTop = 410; fireEvent.scroll(log); const before = store.getState().composer.readings.worker!; expect(before).toMatchObject({ pinned: false, anchorId: 'm-045', offset: -10 });
    fireEvent.click(screen.getByRole('button', { name: 'Load older messages (35)' })); expect(log.scrollTop).toBe(1610);
    show(messages(66)); expect(store.getState().composer.readings.worker).toMatchObject({ anchorId: 'm-045', offset: -10 });
    show(messages(66).map((row) => ({ ...row }))); expect(store.getState().composer.readings.worker).toMatchObject({ anchorId: 'm-045', offset: -10 });
    show(messages(3), 'other'); show(messages(66)); expect(screen.getByRole('log').scrollTop).toBe(1570);
    fireEvent.click(screen.getByRole('button', { name: 'Latest messages' })); expect(store.getState().composer.readings.worker?.pinned).toBe(true);
    show(messages(67)); const latest = screen.getByRole('log'); expect(latest.scrollTop).toBe(latest.scrollHeight - latest.clientHeight);
  });
  it('keeps the reading anchor mounted when appends would push it outside the initial window', () => {
    geometry(); const { store, show } = setup(); const log = screen.getByRole('log'); log.scrollTop = 410; fireEvent.scroll(log);
    const row = log.querySelector('[data-message-id="m-045"]');
    show(messages(105).slice(-100));
    expect(log.querySelector('[data-message-id="m-045"]')).toBe(row);
    expect(store.getState().composer.readings.worker).toMatchObject({ anchorId: 'm-045', offset: -10, pinned: false });
    expect(log.querySelectorAll('[data-message-id]')).toHaveLength(60);
  });
  it('does not overwrite the reading position while hidden and loads older when the user scrolls to the top', () => {
    geometry(); const { store, show } = setup(); const log = screen.getByRole('log'); log.scrollTop = 400; fireEvent.scroll(log);
    const saved = { ...store.getState().composer.readings.worker! }; show(messages(66), 'worker', false, false); log.scrollTop = 0; fireEvent.scroll(log); expect(store.getState().composer.readings.worker).toEqual(saved);
    show(messages(66)); expect(store.getState().composer.readings.worker?.anchorId).toBe(saved.anchorId);
    log.scrollTop = 10; fireEvent.scroll(log); expect(screen.getByText('60 of 66 retained')).toBeVisible();
  });
  it('shows actual sender, valid time, type, reply and delivery state without interpreting raw markup', () => {
    const rich = { id: 'rich', timestamp: 1_790_000_000, sender_kind: 'worker', sender_name: 'Named sender', recipient_kind: 'user', message_type: 'ask', blocking: true, delivery_state: 'buffered', reply_to_id: 'original', message: '# Review\n\n**Bold** and *emphasis* with `code`.\n\n- First\n- Second\n\n> Quote\n\n[Safe](https://example.com) [Unsafe](javascript:alert(1))\n\n<img src=x onerror=alert(1)>' };
    const { container, show } = setup([{ id: 'original', message: 'Original question', timestamp: 1 }, rich]);
    const row = screen.getByRole('article', { name: 'Message from Named sender' }); expect(row).toHaveTextContent('Blocking ask'); expect(row).toHaveTextContent('Waiting for delivery'); expect(row).toHaveTextContent('In reply to: Original question'); expect(row.querySelector('time')).toHaveAttribute('datetime', new Date(1_790_000_000_000).toISOString());
    expect(within(row).getByRole('heading', { name: 'Review' })).toBeVisible(); expect(within(row).getByRole('list')).toBeVisible(); expect(row.querySelector('strong')).toBeTruthy(); expect(container.querySelector('img, script')).toBeNull(); expect(within(row).queryByRole('link', { name: 'Unsafe' })).not.toBeInTheDocument();
    show([{ ...rich, delivery_state: 'failed', delivery_reason: 'Provider unavailable' }]); expect(screen.getByText('Delivery failed · Provider unavailable')).toBeVisible();
    expect(messageView({ timestamp: 'invalid', message_type: 'reminder' }, agent)).toMatchObject({ sender: 'Torque reminder', direction: 'system', iso: '' });
    expect(messageView({ sender_kind: 'user', delivery_state: 'cancelled' }, agent)).toMatchObject({ sender: 'You', direction: 'outbound', deliveryLabel: 'Cancelled' });
  });
  it('copies exact source or code, retains selection on failure and never triggers a reply', async () => {
    const clipboard = vi.fn().mockResolvedValue(undefined); vi.stubGlobal('navigator', { clipboard: { writeText: clipboard } });
    const body = '  **Original**\n\n```js\nconst value = "<tag>";\n  value;\n```\n'; const { onReply } = setup([{ id: 'source', message: body }]);
    const article = screen.getByRole('article'); const node = article.querySelector('strong')!; const range = document.createRange(); range.selectNodeContents(node); window.getSelection()?.addRange(range); const selected = window.getSelection()?.toString();
    fireEvent.click(screen.getByRole('button', { name: 'Copy message' })); await screen.findByText('Message copied.'); expect(clipboard).toHaveBeenLastCalledWith(body);
    fireEvent.click(screen.getByRole('button', { name: 'Copy code' })); await screen.findByText('Code copied.'); expect(clipboard).toHaveBeenLastCalledWith('const value = "<tag>";\n  value;');
    clipboard.mockRejectedValueOnce(new Error('denied')); fireEvent.click(screen.getByRole('button', { name: 'Copy message' })); await screen.findByText('Could not copy message. Select the text or try again.');
    expect(window.getSelection()?.toString()).toBe(selected); expect(onReply).not.toHaveBeenCalled(); expect(screen.getByRole('article')).toBe(article);
  });
  it('supports context actions, keyboard dismissal and pending reply protection', async () => {
    const clipboard = vi.fn().mockResolvedValue(undefined); vi.stubGlobal('navigator', { clipboard: { writeText: clipboard } });
    const { onReply, show } = setup([{ id: 'source', message: 'Reply to me' }]); const article = screen.getByRole('article');
    fireEvent.contextMenu(article, { clientX: 900, clientY: 900 }); const menu = screen.getByRole('menu', { name: 'Direct message actions' }); expect(within(menu).getByRole('menuitem', { name: 'Copy message' })).toHaveFocus();
    fireEvent.keyDown(menu, { key: 'Escape' }); expect(screen.queryByRole('menu')).not.toBeInTheDocument(); expect(article).toHaveFocus();
    fireEvent.contextMenu(article); fireEvent.click(screen.getByRole('menuitem', { name: 'Copy message' })); await waitFor(() => expect(clipboard).toHaveBeenCalledWith('Reply to me')); expect(onReply).not.toHaveBeenCalled();
    fireEvent.contextMenu(article); fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' }); expect(screen.getByRole('menuitem', { name: 'Reply' })).toHaveFocus(); fireEvent.click(screen.getByRole('menuitem', { name: 'Reply' })); expect(onReply).toHaveBeenCalledWith('source', 'Reply to me');
    act(() => show([{ id: 'source', message: 'Reply to me' }], 'worker', true)); expect(screen.getByRole('button', { name: 'Reply' })).toBeDisabled();
  });
  it('rejects executable and relative links while preserving safe links', () => {
    for (const link of ['javascript:alert(1)', 'data:text/html,x', '//example.com', '/relative', 'https://example.com\nattack', 'https://example.com\\bad', 'https://']) expect(safeMarkdownLink(link)).toBeNull();
    expect(safeMarkdownLink('https://example.com/path')).toEqual({ href: 'https://example.com/path' });
    expect(safeMarkdownLink('mailto:help@example.com')).toEqual({ href: 'mailto:help@example.com' });
  });
});
