import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAppStore, connectionActions } from '../../app/store';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { toAgentViewModel } from '../agents/model';
import { Conversation } from './Conversation';
import { composerActions } from './composerState';
import { acknowledgedMessage, cancellationLabels, composerCommand } from './composerModel';
import { editorSelection, readComposerInput, type ComposerInput } from './composerDom';
import { emptyComposerDraft } from './composerState';
const terminal = toAgentViewModel('shell', { name: 'Shell', cell_type: 'terminal', session_id: 'shell-session' });
const agent = toAgentViewModel('worker', { name: 'Worker', kind: 'worker', session_id: 'agent-session' });
const attached = toAgentViewModel('child', { name: 'Child', cell_type: 'terminal', parent_id: agent.id, session_id: 'child-session' });
const other = toAgentViewModel('other', { name: 'Other', kind: 'worker', session_id: 'other-session' });
const response = (data: UnknownRecord) => ({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
const ack = (command: TorqueCommand, extra: UnknownRecord = {}): UnknownRecord => command.cmd === 'send_user_message' ? { type: 'terminal_message_sent', cell_id: command.cell_id, session_id: command.session_id, ...extra } : { type: 'ok', agent_id: command.agent_id, thread_id: command.thread_id, reply_to_id: command.reply_to_id ?? '', message_id: 'msg-1', delivery_state: 'buffered', buffered: true, ...extra };
function harness(initialCell = agent, initialTarget: typeof agent | null = agent, initialMessages: unknown = [], initialHistory: unknown = []) {
  const store = createAppStore(); const calls: { command: TorqueCommand; resolve: (frame: UnknownRecord) => void }[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((resolve) => calls.push({ command: JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand, resolve: (frame) => resolve(response(frame)) }))));
  const content = (cell: typeof agent, target: typeof agent | null, messages: unknown, history: unknown) => <Provider store={store}><Conversation key={cell.id} cell={cell} target={target} messages={messages} messageHistory={history} sendCommand={() => true} onUnavailable={vi.fn()} /></Provider>;
  const view = render(content(initialCell, initialTarget, initialMessages, initialHistory));
  const show = (cell: typeof agent, target: typeof agent | null, messages: unknown = [], history: unknown = []) => view.rerender(content(cell, target, messages, history));
  const reply = async (index: number, extra?: UnknownRecord) => { await act(async () => { const call = calls[index]!; call.resolve(extra ?? ack(call.command)); await Promise.resolve(); }); };
  return { store, calls, show, reply, ...view };
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
describe('buffered terminal and agent composition', () => {
  it('waits for matching standalone delivery, preserves failure and uses the same key for identical retry', async () => {
    const { calls, reply } = harness(terminal, null); const input = screen.getByRole('textbox', { name: 'Message Shell' });
    fireEvent.change(input, { target: { value: '  first\nsecond  ' } }); fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(calls[0]!.command).toMatchObject({ cmd: 'send_user_message', cell_id: 'shell', session_id: 'shell-session', text: '  first\nsecond  ' });
    expect(input).toHaveValue('  first\nsecond  '); expect(input).toBeDisabled(); fireEvent.submit(input.closest('form')!); expect(calls).toHaveLength(1);
    await reply(0, { type: 'error', message: 'PTY refused input' }); expect(await screen.findByRole('alert')).toHaveTextContent('PTY refused input'); expect(input).toHaveValue('  first\nsecond  '); expect(input).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Retry delivery' })); expect(calls[1]!.command).toEqual(calls[0]!.command);
    await reply(1); expect(input).toHaveValue(''); expect(screen.getByRole('status')).toHaveTextContent('Sent to the terminal');
    fireEvent.change(input, { target: { value: '  first\nsecond  ' } }); fireEvent.click(screen.getByRole('button', { name: 'Send' })); expect(calls[2]!.command.idempotency_key).not.toBe(calls[0]!.command.idempotency_key); await reply(2);
  });
  it('routes attached-terminal replies to the parent and retains reply context after mismatched acknowledgement', async () => {
    const messages = [{ id: 'question', sender_kind: 'worker', message: 'Which branch?' }]; const { calls, reply, store } = harness(attached, agent, messages);
    fireEvent.click(screen.getByRole('button', { name: 'Reply' })); expect(screen.getByText('Replying to: Which branch?')).toBeVisible();
    const input = screen.getByRole('textbox', { name: 'Message Worker' }); fireEvent.change(input, { target: { value: 'Use main' } });
    act(() => { store.dispatch(connectionActions.connected({ at: 1, reconnect: true })); }); expect(input).toHaveValue('Use main');
    fireEvent.click(screen.getByRole('button', { name: 'Send' })); expect(calls[0]!.command).toMatchObject({ cmd: 'user_agent_message', agent_id: 'worker', thread_id: 'user-agent:user:worker', reply_to_id: 'question' });
    await reply(0, ack(calls[0]!.command, { reply_to_id: 'other' })); expect(await screen.findByRole('alert')).toHaveTextContent('reply target did not match'); expect(screen.getByText('Replying to: Which branch?')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Retry delivery' })); await reply(1); expect(screen.queryByText('Replying to: Which branch?')).not.toBeInTheDocument(); expect(input).toHaveValue(''); expect(screen.getByRole('status')).toHaveTextContent('waiting for delivery');
  });
  it('keeps drafts, attachments and caret isolated across cells and late completion', async () => {
    const { store, show, calls, reply } = harness();
    const input = screen.getByRole('textbox', { name: 'Message Worker' }); fireEvent.change(input, { target: { value: 'Worker draft' } });
    act(() => { store.dispatch(composerActions.patch({ cellId: agent.id, changes: { attachments: [{ path: '/tmp/image.png', filename: 'image.png' }], selection: [2, 7], reply: { id: 'reply', agentId: agent.id, preview: 'A question' } } })); });
    show(terminal, null); fireEvent.change(screen.getByRole('textbox', { name: 'Message Shell' }), { target: { value: 'Shell draft' } });
    show(agent, agent); expect(readComposerInput(screen.getByRole<ComposerInput>('textbox', { name: 'Message Worker' }), store.getState().composer.drafts[agent.id]!.attachments).text).toBe('Worker draft'); expect(screen.getByRole('button', { name: /image.png/ })).toBeVisible(); expect(screen.getByText('Replying to: A question')).toBeVisible();
    const restored = screen.getByRole<ComposerInput>('textbox', { name: 'Message Worker' }); expect(editorSelection(restored, store.getState().composer.drafts[agent.id]!.attachments)).toEqual([2, 7]);
    fireEvent.click(screen.getByRole('button', { name: 'Send' })); expect(calls[0]!.command.message).toBe('Worker draft\n/tmp/image.png');
    show(terminal, null); await reply(0); expect(screen.getByRole('textbox', { name: 'Message Shell' })).toHaveValue('Shell draft');
    show(agent, agent); expect(screen.getByRole('textbox', { name: 'Message Worker' })).toHaveValue(''); expect(screen.queryByRole('button', { name: /image.png/ })).not.toBeInTheDocument();
  });
  it('captures caret on blur before changing cells even without a select event', () => {
    const { show } = harness(terminal, null);
    const input = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message Shell' });
    fireEvent.change(input, { target: { value: 'Unsent shell draft' } });
    input.setSelectionRange(2, 8); fireEvent.blur(input);
    show(agent, agent); show(terminal, null);
    const restored = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message Shell' });
    expect([restored.selectionStart, restored.selectionEnd]).toEqual([2, 8]);
  });
  it('cancels only an acknowledged turn with its session and stable retry key', async () => {
    const { calls, reply, show } = harness(); const cancel = screen.getByRole('button', { name: 'Cancel turn' }); expect(cancel).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Worker' }), { target: { value: 'Run checks' } }); fireEvent.click(screen.getByRole('button', { name: 'Send' })); await reply(0);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Worker' }), { target: { value: 'Unrelated draft' } });
    fireEvent.click(cancel); expect(calls[1]!.command).toMatchObject({ cmd: 'user_agent_turn_cancel', agent_id: agent.id, session_id: agent.sessionId, turn_idempotency_key: calls[0]!.command.idempotency_key }); expect(screen.getByRole('button', { name: 'Cancelling…' })).toBeDisabled();
    await reply(1, { type: 'error', message: 'Cancel transport refused' }); expect(await screen.findByRole('alert')).toHaveTextContent('Cancel transport refused'); fireEvent.click(screen.getByRole('button', { name: 'Cancel turn' })); expect(calls[2]!.command).toEqual(calls[1]!.command);
    await reply(2, { type: 'ok', message_id: 'cancel-audit', outcome: 'cancelled_queued' }); expect(screen.getByText('Queued message cancelled.')).toBeVisible(); expect(screen.getByRole('textbox', { name: 'Message Worker' })).toHaveValue('Unrelated draft'); expect(cancel).toBeDisabled();
    show({ ...agent, sessionId: 'replacement' }, { ...agent, sessionId: 'replacement' }); expect(cancel).toBeDisabled();
    show(other, other); expect(screen.getByRole('button', { name: 'Cancel turn' })).toBeDisabled();
  });
  it('recalls sent history at line boundaries and restores the unsent draft separately from undo', () => {
    harness(terminal, null, [], [{ id: 'old', message: 'Older message', sent_at: 1 }, { id: 'new', message: 'Newer message', sent_at: 2 }]);
    const input = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message Shell' }); fireEvent.change(input, { target: { value: 'Draft\nsecond line' } }); input.setSelectionRange(input.value.length, input.value.length);
    fireEvent.keyDown(input, { key: 'ArrowUp' }); expect(input).toHaveValue('Draft\nsecond line');
    input.setSelectionRange(0, 0); fireEvent.select(input); fireEvent.keyDown(input, { key: 'ArrowUp' }); expect(input).toHaveValue('Newer message');
    fireEvent.keyDown(input, { key: 'ArrowUp' }); expect(input).toHaveValue('Older message'); fireEvent.keyDown(input, { key: 'ArrowDown' }); expect(input).toHaveValue('Newer message'); fireEvent.keyDown(input, { key: 'ArrowDown' }); expect(input).toHaveValue('Draft\nsecond line');
    fireEvent.click(screen.getByRole('button', { name: 'Message history' })); fireEvent.click(screen.getByRole('button', { name: 'Older message' })); expect(input).toHaveValue('Older message'); fireEvent.click(screen.getByRole('button', { name: 'Restore draft' })); expect(input).toHaveValue('Draft\nsecond line');
    fireEvent.change(input, { target: { value: 'Changed' } }); fireEvent.keyDown(input, { key: 'z', ctrlKey: true }); expect(input).toHaveValue('Draft\nsecond line');
  });
  it('retains uploads for their source cell after selection changes and retains upload failures', async () => {
    const { show, container, store } = harness(terminal, null); let finish: (frame: unknown) => void = () => { throw new Error('not uploading'); }; let body: FormData | undefined;
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { body = options.body as FormData; return new Promise((resolve) => { finish = resolve; }); }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Shell' }), { target: { value: 'With image' } });
    fireEvent.change(container.querySelector('input[type=file]')!, { target: { files: [new File(['image'], 'test.png', { type: 'image/png' })] } }); expect(body?.get('agent_id')).toBe(terminal.id); expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    show(other, other); await act(async () => { finish({ ok: true, json: () => Promise.resolve({ ok: true, data: [{ path: '/tmp/test.png', filename: 'test.png' }] }) }); await Promise.resolve(); }); expect(screen.queryByRole('button', { name: /test.png/ })).not.toBeInTheDocument();
    show(terminal, null); expect(screen.getByRole('button', { name: /test.png/ })).toBeVisible(); expect(readComposerInput(screen.getByRole<ComposerInput>('textbox', { name: 'Message Shell' }), store.getState().composer.drafts[terminal.id]!.attachments).text).toBe('With image');
    fireEvent.change(container.querySelector('input[type=file]')!, { target: { files: [new File(['bad'], 'bad.png')] } });
    await act(async () => { finish({ ok: false, json: () => Promise.resolve({ ok: false, error: 'Image refused' }) }); await Promise.resolve(); }); expect(await screen.findByRole('alert')).toHaveTextContent('Image refused'); expect(screen.getByRole('button', { name: /test.png/ })).toBeVisible();
  });
  it('uses Escape to restore recall, cancel reply, clear undoably, then cancel a submitted turn once', async () => {
    const { calls, reply } = harness(agent, agent, [{ id: 'question', sender_kind: 'worker', message: 'Question' }], [{ id: 'old', message: 'Earlier', sent_at: 1 }]);
    const input = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message Worker' });
    fireEvent.change(input, { target: { value: 'Submitted' } }); fireEvent.keyDown(input, { key: 'Enter' }); await reply(0);
    fireEvent.click(screen.getByRole('button', { name: 'Reply' })); fireEvent.change(input, { target: { value: 'Draft' } });
    input.setSelectionRange(0, 0); fireEvent.select(input); fireEvent.keyDown(input, { key: 'ArrowUp' }); expect(input).not.toHaveValue('Draft');
    fireEvent.keyDown(input, { key: 'Escape' }); expect(input).toHaveValue('Draft'); expect(screen.getByText('Replying to: Question')).toBeVisible();
    fireEvent.keyDown(input, { key: 'Escape' }); expect(input).toHaveValue('Draft'); expect(screen.queryByText('Replying to: Question')).not.toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Escape' }); expect(input).toHaveValue(''); expect(calls).toHaveLength(1);
    fireEvent.keyDown(input, { key: 'z', ctrlKey: true }); expect(input).toHaveValue('Draft');
    fireEvent.keyDown(input, { key: 'Escape' }); fireEvent.keyDown(input, { key: 'Escape', repeat: true }); expect(calls).toHaveLength(1);
    fireEvent.keyDown(input, { key: 'Escape' }); fireEvent.keyDown(input, { key: 'Escape' }); expect(calls).toHaveLength(2);
    expect(calls[1]!.command).toMatchObject({ cmd: 'user_agent_turn_cancel', turn_idempotency_key: calls[0]!.command.idempotency_key });
    await reply(1, { type: 'ok', message_id: 'cancel', outcome: 'cancelled_queued' });
  });
  it('moves Home/End by logical line or whole document, preserves the selection anchor, and ignores Alt and IME', () => {
    harness(terminal, null); const input = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message Shell' });
    fireEvent.change(input, { target: { value: 'first\nsecond\nthird' } }); input.setSelectionRange(9, 9); fireEvent.select(input);
    fireEvent.keyDown(input, { key: 'Home' }); expect(editorSelection(input, [])).toEqual([6, 6]);
    fireEvent.keyDown(input, { key: 'End', shiftKey: true }); expect(editorSelection(input, [])).toEqual([6, 12]);
    fireEvent.keyDown(input, { key: 'Home', ctrlKey: true, shiftKey: true }); expect(editorSelection(input, [])).toEqual([6, 0]);
    fireEvent.keyDown(input, { key: 'End', metaKey: true }); expect(editorSelection(input, [])).toEqual([18, 18]);
    fireEvent.keyDown(input, { key: 'Home', altKey: true }); expect(editorSelection(input, [])).toEqual([18, 18]);
    fireEvent.keyDown(input, { key: 'z', ctrlKey: true, altKey: true }); expect(input).toHaveValue('first\nsecond\nthird');
    expect(fireEvent.keyDown(input, { key: 'z', ctrlKey: true, metaKey: true })).toBe(true); expect(input).toHaveValue('first\nsecond\nthird');
    fireEvent.compositionStart(input); fireEvent.keyDown(input, { key: 'Escape' }); fireEvent.keyDown(input, { key: 'Home' }); fireEvent.keyDown(input, { key: 'z', ctrlKey: true });
    expect(input).toHaveValue('first\nsecond\nthird'); expect(editorSelection(input, [])).toEqual([18, 18]); fireEvent.compositionEnd(input);
  });
  it('does not send on composition Enter or allow an inactive standalone session', () => {
    const { calls, show } = harness(terminal, null); const input = screen.getByRole('textbox', { name: 'Message Shell' }); fireEvent.change(input, { target: { value: 'Draft' } }); fireEvent.keyDown(input, { key: 'Enter', isComposing: true }); expect(calls).toHaveLength(0);
    show({ ...terminal, sessionId: '' }, null); expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled(); expect(input).toHaveValue('Draft');
  });
});
describe('composer acknowledgement contracts', () => {
  it('rejects wrong sessions, wrong targets, failed delivery and changed reply targets', () => {
    const command = composerCommand(terminal, null, { ...emptyComposerDraft, text: 'hello' }); expect(() => acknowledgedMessage(ack(command, { session_id: 'old' }), command)).toThrow('confirm terminal');
    const dm = composerCommand(agent, agent, { ...emptyComposerDraft, text: 'hello' }); expect(() => acknowledgedMessage(ack(dm, { agent_id: 'other' }), dm)).toThrow('target'); expect(() => acknowledgedMessage(ack(dm, { delivery_state: 'failed' }), dm)).toThrow('delivery failed');
    expect(() => composerCommand(attached, other, { ...emptyComposerDraft, text: 'reply', reply: { id: 'q', agentId: agent.id, preview: '' } })).toThrow('reply target changed');
  });
  it('accepts supported local command responses without inventing cancellable turns', () => {
    const command = { cmd: 'user_agent_message', agent_id: agent.id, thread_id: `user-agent:user:${agent.id}`, message: '/status' };
    expect(acknowledgedMessage({ type: 'ok', message_id: 'audit', thread_id: command.thread_id, agent_id: agent.id, delivered: true, buffered: false }, command).cancellable).toBe(false);
    expect(acknowledgedMessage({ type: 'agent_restart', agent_id: agent.id, status: 'succeeded', audit_message_id: 'audit' }, { ...command, message: '/restart' }).notice).toBe('Agent restarted.');
    expect(acknowledgedMessage({ type: 'agent_message_loop', loop: { agent_id: agent.id, status: 'active' }, audit_message_id: 'audit' }, { ...command, message: '/loop 10m check' }).cancellable).toBe(false);
    expect(Object.keys(cancellationLabels)).toEqual(['cancelled_queued', 'interrupted', 'unsupported_provider', 'session_replaced', 'no_active_turn', 'interrupt_failed']);
  });
});

describe('owned composer recovery', () => {
  it('keeps the newer parent turn when an attached composer recovers an older delivery', async () => {
    vi.useFakeTimers(); const { calls, reply, show, store } = harness(attached, agent);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Worker' }), { target: { value: 'Older attached message' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    show(agent, agent);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Worker' }), { target: { value: 'Newer parent message' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' })); await reply(1);
    const newer = calls[1]!.command.idempotency_key;
    show(attached, agent); fireEvent.click(screen.getByRole('button', { name: 'Retry delivery' })); await reply(2);
    expect(store.getState().composer.drafts[attached.id]!.text).toBe('');
    expect(store.getState().composer.turns[agent.id]?.key).toBe(newer);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel turn' }));
    expect(calls[3]!.command.turn_idempotency_key).toBe(newer);
    await reply(3, { type: 'ok', outcome: 'cancelled_queued', message_id: 'newer-cancel' });
  });

  it('bounds submission, freezes its original target and payload, and ignores an expired reply', async () => {
    vi.useFakeTimers(); const { calls, reply, show, store } = harness(attached, agent);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Worker' }), { target: { value: 'Original message' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown');
    const input = screen.getByRole('textbox', { name: 'Message Worker' });
    expect(input).toHaveAttribute('readonly');
    fireEvent.change(input, { target: { value: 'Changed message' } });
    act(() => { store.dispatch(composerActions.patch({ cellId: attached.id, changes: { reply: { id: 'changed', agentId: other.id, preview: 'Changed' } } })); });
    expect(store.getState().composer.drafts[attached.id]!.text).toBe('Original message');
    expect(store.getState().composer.drafts[attached.id]!.reply).toBeNull();
    show(attached, other); act(() => { store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); });
    expect(calls).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Retry delivery' }));
    expect(calls[1]!.command).toEqual(calls[0]!.command);
    await reply(0); expect(screen.getByRole('button', { name: 'Sending…' })).toBeDisabled();
    expect(store.getState().composer.drafts[attached.id]!.text).toBe('Original message');
    await reply(1);
    expect(store.getState().composer.drafts[attached.id]!.text).toBe('');
    expect(store.getState().composer.turns[agent.id]?.key).toBe(calls[0]!.command.idempotency_key);
    expect(store.getState().composer.turns[other.id]).toBeUndefined();
  });
  it('accepts only a correlated verified refusal before releasing the draft', async () => {
    harness(terminal, null);
    const seen: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; seen.push(command);
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: false, error: 'No input was attempted', delivery_refused: true, delivery_uncertain: seen.length === 2, command: command.cmd, idempotency_key: seen.length === 1 ? 'unrelated-key' : command.idempotency_key }) });
    }));
    const input = screen.getByRole('textbox', { name: 'Message Shell' });
    fireEvent.change(input, { target: { value: 'Reviewed message' } }); fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByRole('button', { name: 'Retry delivery' })).toBeEnabled(); expect(input).toHaveAttribute('readonly');
    fireEvent.click(screen.getByRole('button', { name: 'Retry delivery' }));
    expect(await screen.findByRole('button', { name: 'Retry delivery' })).toBeEnabled(); expect(input).toHaveAttribute('readonly');
    fireEvent.click(screen.getByRole('button', { name: 'Retry delivery' }));
    expect(await screen.findByRole('button', { name: 'Send' })).toBeEnabled(); expect(input).not.toHaveAttribute('readonly');
    fireEvent.change(input, { target: { value: 'Corrected message' } }); fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByRole('button', { name: 'Send' });
    expect(seen[1]).toEqual(seen[0]); expect(seen[2]).toEqual(seen[0]); expect(seen[3]!.idempotency_key).not.toBe(seen[0]!.idempotency_key);
  });
  it('releases an uncertain draft only after explicit review and rejects the old response afterward', async () => {
    vi.useFakeTimers(); const { calls, reply, store } = harness(terminal, null);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Shell' }), { target: { value: 'Unknown delivery' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    fireEvent.click(screen.getByRole('button', { name: 'Review delivery' }));
    expect(screen.getByRole('dialog', { name: 'Review uncertain delivery' })).toHaveTextContent('may already have been sent');
    fireEvent.click(screen.getByRole('button', { name: 'Keep recovering' }));
    expect(screen.getByRole('textbox', { name: 'Message Shell' })).toHaveAttribute('readonly');
    fireEvent.click(screen.getByRole('button', { name: 'Review delivery' }));
    fireEvent.click(screen.getByRole('button', { name: 'I checked; keep draft' }));
    const input = screen.getByRole('textbox', { name: 'Message Shell' }); expect(input).not.toHaveAttribute('readonly');
    fireEvent.change(input, { target: { value: 'New reviewed message' } });
    await reply(0); expect(store.getState().composer.drafts[terminal.id]!.text).toBe('New reviewed message');
    fireEvent.click(screen.getByRole('button', { name: 'Send' })); expect(calls[1]!.command.idempotency_key).not.toBe(calls[0]!.command.idempotency_key); await reply(1);
  });
  it('bounds cancellation offscreen and recovers the captured turn after session replacement', async () => {
    vi.useFakeTimers(); const { calls, reply, show, store } = harness();
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Worker' }), { target: { value: 'Run this turn' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' })); await reply(0);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Worker' }), { target: { value: 'Unrelated draft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel turn' })); show(other, other);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(store.getState().composer.turns[agent.id]?.pending).toBe(false);
    show({ ...agent, sessionId: 'new-session' }, { ...agent, sessionId: 'new-session' });
    expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel turn' })); expect(calls[2]!.command).toEqual(calls[1]!.command);
    await reply(1, { type: 'ok', outcome: 'interrupted', message_id: 'expired-audit' });
    expect(screen.getByRole('button', { name: 'Cancelling…' })).toBeDisabled();
    await reply(2, { type: 'ok', outcome: 'cancelled_queued', message_id: 'current-audit' });
    expect(screen.getByText('Queued message cancelled.')).toBeVisible(); expect(screen.getByRole('textbox', { name: 'Message Worker' })).toHaveValue('Unrelated draft');
  });
});
