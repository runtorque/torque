import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAppStore } from '../../app/store';
import { composerActions } from './composerState';
import { messageWithAttachments } from './composerDocument';
const image = (id: string) => ({ id, path: `/images/${id}.png`, filename: `${id}.png`, previewUrl: `blob:${id}` });
afterEach(() => vi.unstubAllGlobals());
describe('attachment draft history and upload ownership', () => {
  it('undoes insertion, removal and text as complete documents, retains previews and resets after acknowledged send', () => {
    const revoke = vi.fn(); vi.stubGlobal('URL', { revokeObjectURL: revoke });
    const store = createAppStore(); const draft = () => store.getState().composer.drafts.one!;
    store.dispatch(composerActions.edit({ cellId: 'one', text: 'left right', selection: [5, 5] }));
    store.dispatch(composerActions.startUpload({ cellId: 'one', key: 'upload', selection: [5, 5] }));
    store.dispatch(composerActions.finishUpload({ cellId: 'one', key: 'upload', attachments: [image('one')] }));
    expect(draft().selection).toEqual([6, 6]); expect(messageWithAttachments(draft())).toBe('left /images/one.png right');
    store.dispatch(composerActions.removeAttachment({ cellId: 'one', id: 'one' })); expect(draft().attachments).toEqual([]); expect(draft().text).toBe('left right'); expect(revoke).not.toHaveBeenCalled();
    store.dispatch(composerActions.undo({ cellId: 'one', direction: -1 })); expect(draft().attachments[0]).toMatchObject({ id: 'one', position: 5 }); expect(draft().selection).toEqual([6, 6]);
    store.dispatch(composerActions.undo({ cellId: 'one', direction: -1 })); expect(draft().attachments).toEqual([]); expect(draft().selection).toEqual([5, 5]);
    store.dispatch(composerActions.undo({ cellId: 'one', direction: 1 })); expect(draft().attachments[0]?.previewUrl).toBe('blob:one'); expect(revoke).not.toHaveBeenCalled();
    store.dispatch(composerActions.patch({ cellId: 'one', changes: { attempt: { key: 'send', fingerprint: '' }, pending: true } }));
    store.dispatch(composerActions.submitted({ cellId: 'one', key: 'send', message: { id: 'sent', message: 'sent', at: 1 }, notice: 'Sent' }));
    expect(revoke).toHaveBeenCalledExactlyOnceWith('blob:one'); store.dispatch(composerActions.undo({ cellId: 'one', direction: -1 })); expect(draft().text).toBe(''); expect(draft().attachments).toEqual([]);
  });
  it('releases an abandoned redo preview but keeps another cell and untouched draft history', () => {
    const revoke = vi.fn(); vi.stubGlobal('URL', { revokeObjectURL: revoke }); const store = createAppStore();
    for (const cellId of ['one', 'two']) {
      store.dispatch(composerActions.startUpload({ cellId, key: cellId, selection: [0, 0] }));
      store.dispatch(composerActions.finishUpload({ cellId, key: cellId, attachments: [image(cellId)] }));
    }
    store.dispatch(composerActions.undo({ cellId: 'one', direction: -1 })); expect(revoke).not.toHaveBeenCalled();
    store.dispatch(composerActions.edit({ cellId: 'one', text: 'New branch of edits' })); expect(revoke).toHaveBeenCalledExactlyOnceWith('blob:one');
    expect(store.getState().composer.drafts.two?.attachments[0]?.previewUrl).toBe('blob:two');
  });
  it('tracks an asynchronous upload through edits, isolates its cell and ignores stale completions', () => {
    const store = createAppStore(); store.dispatch(composerActions.edit({ cellId: 'one', text: 'left right', selection: [5, 10] }));
    store.dispatch(composerActions.startUpload({ cellId: 'one', key: 'upload', selection: [5, 10] }));
    store.dispatch(composerActions.edit({ cellId: 'one', text: 'new left right' }));
    store.dispatch(composerActions.edit({ cellId: 'two', text: 'Other draft' }));
    store.dispatch(composerActions.finishUpload({ cellId: 'one', key: 'stale', attachments: [image('stale')] })); expect(store.getState().composer.drafts.one?.uploading).toBe(true);
    store.dispatch(composerActions.finishUpload({ cellId: 'one', key: 'upload', attachments: [image('one')] }));
    expect(messageWithAttachments(store.getState().composer.drafts.one!)).toBe('new left /images/one.png'); expect(store.getState().composer.drafts.two?.text).toBe('Other draft');
    store.dispatch(composerActions.undo({ cellId: 'one', direction: -1 })); expect(store.getState().composer.drafts.one?.text).toBe('new left right');
  });
  it('keeps edits over pending selected text and retains the complete draft after upload refusal', () => {
    const store = createAppStore(); const draft = () => store.getState().composer.drafts.one!;
    store.dispatch(composerActions.edit({ cellId: 'one', text: 'left right' })); store.dispatch(composerActions.startUpload({ cellId: 'one', key: 'upload', selection: [5, 10] }));
    store.dispatch(composerActions.edit({ cellId: 'one', text: 'left replacement' })); store.dispatch(composerActions.finishUpload({ cellId: 'one', key: 'upload', attachments: [image('one')] }));
    expect(draft().text).toBe('left replacement'); expect(draft().attachments[0]?.position).toBe(16);
    store.dispatch(composerActions.startUpload({ cellId: 'one', key: 'next', selection: [0, 16] })); store.dispatch(composerActions.failUpload({ cellId: 'one', key: 'next', error: 'Upload refused' }));
    expect(draft().text).toBe('left replacement'); expect(draft().attachments[0]?.id).toBe('one'); expect(draft().uploading).toBe(false); expect(draft().error).toBe('Upload refused');
  });
});
