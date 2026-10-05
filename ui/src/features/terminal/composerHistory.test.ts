import { describe, expect, it } from 'vitest';
import { createAppStore } from '../../app/store';
import { composerActions, type ComposerEditKind } from './composerState';
function setup() {
  const store = createAppStore(); const cellId = 'one';
  const edit = (text: string, kind: ComposerEditKind = 'typing', selection: [number, number] = [text.length, text.length]) => store.dispatch(composerActions.edit({ cellId, text, kind, selection }));
  const select = (selection: [number, number]) => store.dispatch(composerActions.patch({ cellId, changes: { selection } }));
  const undo = (direction = -1) => store.dispatch(composerActions.undo({ cellId, direction }));
  const draft = () => store.getState().composer.drafts.one!;
  return { store, cellId, edit, select, undo, draft };
}
describe('composer undo transactions', () => {
  it('groups typing, including newlines, and deletion independently and resets grouping after undo/redo', () => {
    const h = setup(); h.edit('a'); h.edit('ab'); h.edit('ab\n'); h.edit('ab\nc');
    h.edit('ab\n', 'delete'); h.edit('ab', 'delete');
    h.undo(); expect(h.draft().text).toBe('ab\nc'); h.undo(); expect(h.draft().text).toBe('');
    h.undo(1); expect(h.draft().text).toBe('ab\nc'); h.undo(1); expect(h.draft().text).toBe('ab');
    h.edit('abc'); h.undo(); expect(h.draft().text).toBe('ab');
  });
  it('separates caret moves, backward selections and paste, and drops redo only on a semantic edit', () => {
    const h = setup(); h.edit('a'); h.edit('ab'); h.select([0, 0]); h.select([2, 2]); h.edit('abc');
    h.undo(); expect(h.draft().text).toBe('ab'); h.select([2, 0]); h.undo(1); expect(h.draft().text).toBe('abc');
    h.select([3, 1]); h.edit('aX', 'typing'); h.undo(); expect(h.draft().text).toBe('abc'); expect(h.draft().selection).toEqual([3, 1]);
    h.edit('abc paste', null); h.edit('abc pasted'); h.undo(); expect(h.draft().text).toBe('abc paste'); h.undo(); expect(h.draft().text).toBe('abc');
    h.edit('branch'); h.undo(1); expect(h.draft().text).toBe('branch');
  });
  it('keeps image insertion, composition and explicit clear separate from typing, with cell-local history', () => {
    const h = setup(); h.edit('a'); h.edit('ab');
    h.store.dispatch(composerActions.startUpload({ cellId: h.cellId, key: 'image', selection: [2, 2] }));
    h.store.dispatch(composerActions.finishUpload({ cellId: h.cellId, key: 'image', attachments: [{ id: 'image', path: '/image.png', filename: 'image.png' }] }));
    h.edit('abc', 'typing', [4, 4]); h.edit('abcd', 'typing', [5, 5]); h.undo(); expect(h.draft().text).toBe('ab'); expect(h.draft().attachments).toHaveLength(1);
    h.store.dispatch(composerActions.startComposition(h.cellId)); h.edit('abあ'); h.edit('abあい'); h.store.dispatch(composerActions.endComposition(h.cellId));
    h.store.dispatch(composerActions.document({ cellId: h.cellId, document: { text: '', attachments: [], selection: [0, 0] } }));
    h.store.dispatch(composerActions.edit({ cellId: 'two', text: 'other draft', kind: 'typing' }));
    h.undo(); expect(h.draft().text).toBe('abあい'); expect(h.draft().attachments).toHaveLength(1); h.undo(); expect(h.draft().text).toBe('ab');
    h.undo(); expect(h.draft().attachments).toHaveLength(0); h.undo(); expect(h.draft().text).toBe(''); expect(h.store.getState().composer.drafts.two?.text).toBe('other draft');
  });
});
