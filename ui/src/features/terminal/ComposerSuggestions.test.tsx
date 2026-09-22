import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAppStore, projectionActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { toAgentViewModel } from '../agents/model';
import { Conversation } from './Conversation';
const agent = toAgentViewModel('worker', { name: 'Worker', kind: 'worker', group: 'one', agent_type: 'generic', session_id: 'session' });
const catalog = [{ id: 'compact', label: '/compact', insert: '/compact', help: 'Compact context' }, { id: 'commands', label: '/commands', insert: '/commands', help: 'List supported commands' }, { id: 'fast', label: '/fast', insert: '/fast', providers: ['codex'] }];
const tasks = { 'T:1': { group: 'one', task: 'Fix renderer' }, 'T:2': { group: 'two', task: 'Other renderer' } };
function setup() {
  const store = createAppStore(); const frame = { ...compactStateFixture, user_dm_commands: catalog, board_tasks: tasks };
  store.dispatch(projectionActions.snapshotReceived(frame)); const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  const content = (cell = agent, target: typeof agent | null = agent, active = true) => <Provider store={store}><Conversation key={cell.id} cell={cell} target={target} messages={[]} sendCommand={() => true} onUnavailable={vi.fn()} active={active} /></Provider>;
  const view = render(content()); const input = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message Worker' }); act(() => input.focus());
  const change = (text: string, caret = text.length) => { fireEvent.change(input, { target: { value: text, selectionStart: caret, selectionEnd: caret } }); };
  return { store, frame, input, fetcher, change, show: (cell = agent, target: typeof agent | null = agent, active = true) => view.rerender(content(cell, target, active)) };
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe('composer suggestion interaction', () => {
  it('supports arrows, Enter, caret placement and undo without sending', async () => {
    const { input, fetcher, change } = setup(); change('/'); expect(screen.getByRole('listbox', { name: 'Message commands' })).toBeVisible(); expect(screen.queryByRole('option', { name: '/fast' })).not.toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'ArrowDown' }); expect(screen.getByRole('option', { name: /\/compact/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(input, { key: 'ArrowDown' }); fireEvent.keyDown(input, { key: 'Enter' }); await act(async () => { await Promise.resolve(); });
    expect(input).toHaveValue('/commands'); expect(input.selectionStart).toBe(9); expect(input).toHaveFocus(); expect(screen.queryByRole('listbox')).not.toBeInTheDocument(); expect(fetcher).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'z', ctrlKey: true }); expect(input).toHaveValue('/');
  });
  it('inserts a scoped task at the caret by pointer and retains surrounding text and undo', async () => {
    const { input, fetcher, change } = setup(); change('Please :rend after', 12); const option = screen.getByRole('option', { name: 'T:1 Fix renderer' }); expect(screen.queryByText('Other renderer')).not.toBeInTheDocument();
    fireEvent.mouseDown(option); fireEvent.click(option); await act(async () => { await Promise.resolve(); }); expect(input).toHaveValue('Please T:1  after'); expect(input.selectionStart).toBe(11); expect(input).toHaveFocus(); expect(fetcher).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'z', ctrlKey: true }); expect(input).toHaveValue('Please :rend after');
  });
  it('dismisses without changing text, suppresses IME/hidden suggestions and accepts Tab', async () => {
    const { input, change, show, fetcher } = setup(); change('/'); fireEvent.keyDown(input, { key: 'Escape' }); expect(screen.queryByRole('listbox')).not.toBeInTheDocument(); expect(input).toHaveValue('/');
    change('/com'); fireEvent.compositionStart(input); expect(screen.queryByRole('listbox')).not.toBeInTheDocument(); fireEvent.keyDown(input, { key: 'Enter', isComposing: true }); expect(fetcher).not.toHaveBeenCalled(); fireEvent.compositionEnd(input);
    expect(screen.getByRole('listbox')).toBeVisible(); show(agent, agent, false); expect(screen.queryByRole('listbox')).not.toBeInTheDocument(); show(); fireEvent.keyDown(input, { key: 'Tab' }); await act(async () => { await Promise.resolve(); }); expect(input).toHaveValue('/compact'); expect(fetcher).not.toHaveBeenCalled();
  });
  it('refreshes catalog/task scope on snapshot without replacing the draft or input', () => {
    const { input, change, store, frame, show } = setup(); change(':rend');
    act(() => { store.dispatch(projectionActions.snapshotReceived({ ...frame, board_tasks: { ...tasks, 'T:1': { ...tasks['T:1'], archived_at: 1 } } })); }); expect(screen.queryByRole('listbox')).not.toBeInTheDocument(); expect(input).toHaveValue(':rend'); expect(input).toHaveFocus();
    change('/'); show({ ...agent, provider: 'codex' }, { ...agent, provider: 'codex' }); expect(screen.getByRole('option', { name: '/fast' })).toBeVisible(); expect(screen.getByRole('textbox', { name: 'Message Worker' })).toBe(input);
    show(agent, null); expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});
