import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAppStore, connectionActions } from '../../app/store';
import { toAgentViewModel } from '../agents/model';
import { Conversation } from './Conversation';
import { composerActions } from './composerState';
import { editorSelection, readComposerInput, setEditorSelection, type ComposerInput } from './composerDom';
const agent = toAgentViewModel('worker', { kind: 'worker', name: 'Worker', session_id: 'session' });
function setup() {
  const store = createAppStore(); const revoke = vi.fn(); let serial = 0;
  vi.stubGlobal('URL', class extends URL { static createObjectURL = () => `blob:preview-${++serial}`; static revokeObjectURL = revoke; });
  let finish: (value: unknown) => void = () => { throw new Error('No upload'); };
  vi.stubGlobal('fetch', vi.fn(() => new Promise((resolve) => { finish = resolve; })));
  const view = render(<Provider store={store}><Conversation cell={agent} target={agent} messages={[]} messageHistory={[{ id: 'sent', message: 'Earlier message', sent_at: 1 }]} sendCommand={() => true} onUnavailable={vi.fn()} /></Provider>);
  const draft = () => store.getState().composer.drafts[agent.id]!;
  const input = () => screen.getByRole<ComposerInput>('textbox', { name: 'Message Worker' });
  const document = () => readComposerInput(input(), draft().attachments, draft().selection);
  const select = (start: number, end = start) => act(() => { input().focus(); setEditorSelection(input(), draft().attachments, [start, end]); fireEvent.select(input()); });
  const upload = (name = 'one.png') => fireEvent.change(view.container.querySelector('input[type=file]')!, { target: { files: [new File(['image'], name, { type: 'image/png' })] } });
  const uploaded = async (name = 'one.png') => { await act(async () => { finish({ ok: true, json: () => Promise.resolve({ ok: true, data: [{ path: `/images/${name}`, filename: name }] }) }); await Promise.resolve(); }); };
  return { store, revoke, draft, input, document, select, upload, uploaded };
}
afterEach(() => vi.unstubAllGlobals());
describe('inline image composer interactions', () => {
  it('previews/removes images, restores complete documents with undo, and preserves attachment-bearing drafts during history recall', async () => {
    const h = setup(); fireEvent.change(h.input(), { target: { value: 'left right' } }); h.select(5); h.upload(); await h.uploaded();
    expect(h.document().text).toBe('left right'); expect(h.draft().attachments[0]?.position).toBe(5);
    fireEvent.click(screen.getByRole('button', { name: 'Preview attached image one.png' })); const dialog = screen.getByRole('dialog', { name: 'Attached image preview' }); expect(within(dialog).getByRole('img', { name: 'one.png' })).toHaveAttribute('src', 'blob:preview-1');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove image' })); expect(h.input()).toHaveValue('left right'); expect(h.revoke).not.toHaveBeenCalled();
    fireEvent.keyDown(h.input(), { key: 'z', ctrlKey: true }); expect(h.document().attachments[0]?.position).toBe(5);
    fireEvent.click(screen.getByRole('button', { name: 'Message history' })); fireEvent.click(screen.getByRole('button', { name: 'Earlier message' })); expect(h.input()).toHaveValue('Earlier message');
    fireEvent.click(screen.getByRole('button', { name: 'Restore draft' })); expect(h.document().text).toBe('left right'); expect(h.document().attachments[0]?.previewUrl).toBe('blob:preview-1'); expect(h.revoke).not.toHaveBeenCalled();
    h.select(6); fireEvent.keyDown(h.input(), { key: 'Backspace' }); expect(h.input()).toHaveValue('left right'); fireEvent.keyDown(h.input(), { key: 'z', ctrlKey: true }); expect(h.document().attachments).toHaveLength(1);
    h.select(5); fireEvent.keyDown(h.input(), { key: 'Delete' }); expect(h.input()).toHaveValue('left right');
    act(() => { h.input().dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'historyUndo' })); }); expect(h.document().attachments).toHaveLength(1);
    act(() => { h.input().dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'historyRedo' })); }); expect(h.document().attachments).toHaveLength(0);
  });
  it('keeps native edited nodes and selection through reconnect, newline and plain-text paste', async () => {
    const h = setup(); fireEvent.change(h.input(), { target: { value: 'left right' } }); h.select(5); h.upload(); await h.uploaded();
    h.select(6); const node = h.input(); const token = node.querySelector('[data-composer-image]');
    act(() => { h.store.dispatch(connectionActions.connected({ at: 1, reconnect: true })); }); expect(h.input()).toBe(node); expect(node.querySelector('[data-composer-image]')).toBe(token); expect(editorSelection(node, h.draft().attachments)).toEqual([6, 6]);
    fireEvent.keyDown(node, { key: 'Enter', shiftKey: true }); expect(h.document().text).toBe('left \nright');
    fireEvent.paste(h.input(), { clipboardData: { files: [], getData: () => '<b>literal</b>\nnext' } }); expect(h.document().text).toBe('left \n<b>literal</b>\nnextright'); expect(h.input().querySelector('b')).toBeNull();
  });
  it('captures a textarea composition-only final value before deferred image insertion and undo', async () => {
    const h = setup(); fireEvent.change(h.input(), { target: { value: 'before ' } }); h.select(7); h.upload();
    const node = h.input() as HTMLTextAreaElement; fireEvent.compositionStart(node); fireEvent.change(node, { target: { value: 'before あ' } });
    node.value = 'before あい'; node.setSelectionRange(node.value.length, node.value.length); await h.uploaded();
    expect(h.input()).toBe(node); expect(h.draft().uploading).toBe(true); fireEvent.compositionEnd(node);
    expect(h.document().text).toBe('before あい'); expect(h.document().attachments).toHaveLength(1);
    fireEvent.keyDown(h.input(), { key: 'z', ctrlKey: true }); expect(h.input()).toHaveValue('before あい');
    fireEvent.keyDown(h.input(), { key: 'z', ctrlKey: true }); expect(h.input()).toHaveValue('before ');
  });
  it('defers completed uploads during IME and commits composition as one undoable transaction', async () => {
    const h = setup(); fireEvent.change(h.input(), { target: { value: 'left ' } }); h.select(5); h.upload(); await h.uploaded(); h.select(6); h.upload('two.png');
    const node = h.input(); fireEvent.compositionStart(node); node.lastChild!.nodeValue = 'あ'; fireEvent.input(node, { isComposing: true });
    node.lastChild!.nodeValue = 'あい'; fireEvent.input(node, { isComposing: true }); await h.uploaded('two.png');
    expect(h.input()).toBe(node); expect(node.querySelectorAll('[data-composer-image]')).toHaveLength(1); expect(h.draft().uploading).toBe(true);
    fireEvent.compositionEnd(node); expect(h.document().text).toBe('left あい'); expect(h.document().attachments).toHaveLength(2); expect(h.draft().uploading).toBe(false);
    fireEvent.keyDown(h.input(), { key: 'z', ctrlKey: true }); expect(h.document().text).toBe('left あい'); expect(h.document().attachments).toHaveLength(1);
    fireEvent.keyDown(h.input(), { key: 'z', ctrlKey: true }); expect(h.document().text).toBe('left '); expect(h.document().attachments).toHaveLength(1);
    act(() => { h.store.dispatch(composerActions.endComposition(agent.id)); }); expect(h.draft().composition).toBeNull();
  });
});
