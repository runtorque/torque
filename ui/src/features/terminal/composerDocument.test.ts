import { describe, expect, it } from 'vitest';
import { editorLength, editorOffset, editorText, locatedAttachments, messageWithAttachments, moveAttachments, moveUploadAnchor, plainOffset, replaceDocumentRange, sameDocument, type ComposerDocument } from './composerDocument';
const image = (id: string, position: number) => ({ id, position, path: `/images/${id}.png`, filename: `${id}.png`, previewUrl: `blob:${id}` });
const document = (text = 'left right', attachments = [image('one', 5)]): ComposerDocument => ({ text, attachments, selection: [5, 5] });
describe('inline composer documents', () => {
  it('assembles images at logical positions, preserves whitespace and orders multiple images at one boundary', () => {
    expect(messageWithAttachments(document())).toBe('left /images/one.png right');
    expect(messageWithAttachments(document('after', [image('a', 0), image('b', 0)]))).toBe('/images/a.png\n/images/b.png after');
    expect(messageWithAttachments(document('before\n', [image('a', 7)]))).toBe('before\n/images/a.png');
    expect(messageWithAttachments(document('', [image('a', 0)]))).toBe('/images/a.png');
    expect(messageWithAttachments(document('  unchanged\ntext  ', []))).toBe('  unchanged\ntext  ');
    expect(messageWithAttachments({ text: 'Legacy', attachments: [{ path: '/old.png', filename: 'old.png' }] })).toBe('Legacy\n/old.png');
  });
  it('distinguishes both sides and intervening positions of zero-text-width image tokens', () => {
    const value = document('ab', [image('one', 1), image('two', 1)]);
    expect(editorText(value)).toBe('a\ufffc\ufffcb'); expect(editorLength(value)).toBe(4);
    expect([0, 1, 2, 3, 4].map((offset) => plainOffset(value, offset))).toEqual([0, 1, 1, 1, 2]);
    expect(editorOffset(value, 1, false)).toBe(1); expect(editorOffset(value, 1, true)).toBe(3);
    expect(locatedAttachments(value).map((row) => row.offset)).toEqual([1, 2]);
  });
  it('inserts at a selected position and replaces selected text and tokens without deleting neighboring images', () => {
    const value = document('abc def', [image('one', 3), image('two', 4)]);
    const next = replaceDocumentRange(value, [3, 5], '', [image('new', 999)]);
    expect(next).toMatchObject({ text: 'abcdef', attachments: [image('new', 3), image('two', 3)], selection: [4, 4] });
    expect(messageWithAttachments(next)).toBe('abc/images/new.png\n/images/two.png def');
    const middle = replaceDocumentRange(next, [4, 4], 'X');
    expect(middle.text).toBe('abcXdef'); expect(middle.attachments.map((entry) => entry.position)).toEqual([3, 4]);
  });
  it('deletes only the adjacent token and supports inserting before, between and after images', () => {
    const value = document('ab', [image('one', 1), image('two', 1)]);
    expect(replaceDocumentRange(value, [2, 3]).attachments).toEqual([image('one', 1)]);
    expect(replaceDocumentRange(value, [1, 2]).attachments).toEqual([image('two', 1)]);
    for (const [offset, positions] of [[1, [2, 2]], [2, [1, 2]], [3, [1, 1]]] as const) {
      const next = replaceDocumentRange(value, [offset, offset], 'X'); expect(next.text).toBe('aXb'); expect(next.attachments.map((entry) => entry.position)).toEqual(positions);
    }
    expect(value.attachments).toEqual([image('one', 1), image('two', 1)]);
  });
  it('retains UTF-16 selection coordinates, newlines and literal placeholder characters in text', () => {
    const value = document('😀\n\ufffcend', [image('one', 3)]);
    expect(editorOffset(value, 3)).toBe(4);
    expect(replaceDocumentRange(value, [4, 5]).text).toBe('😀\nend');
    expect(messageWithAttachments(value)).toBe('😀\n/images/one.png \ufffcend');
  });
  it('tracks attachment positions through text insertion, deletion and replacements', () => {
    expect(moveAttachments('left right', 'new left right', [image('one', 5)])[0]?.position).toBe(9);
    expect(moveAttachments('left right', 'right', [image('one', 5)])[0]?.position).toBe(0);
    expect(moveAttachments('left right', 'L right', [image('one', 2)])[0]?.position).toBe(0);
    expect(locatedAttachments(document('ab', [image('end', 99), image('start', -8)])).map((row) => row.position)).toEqual([0, 2]);
  });
  it('moves pending uploads with preceding edits and preserves edits over a selected upload range', () => {
    const before = document('left right', []); const anchor = { key: 'upload', selection: [5, 10] as [number, number] };
    expect(moveUploadAnchor(anchor, before, document('new left right', []))?.selection).toEqual([9, 14]);
    expect(moveUploadAnchor(anchor, before, document('left right!', []))?.selection).toEqual([5, 10]);
    const overwritten = document('left replacement', []); const moved = moveUploadAnchor(anchor, before, overwritten)!;
    expect(moved.selection).toEqual([16, 16]);
    expect(replaceDocumentRange(overwritten, moved.selection, '', [image('new', 0)]).text).toBe('left replacement');
    expect(moveUploadAnchor(null, before, overwritten)).toBeNull();
  });
  it('treats selection as restore metadata while token identity, location and preview belong to semantic history', () => {
    const value = document(); expect(sameDocument(value, { ...value, selection: [0, 2] })).toBe(true);
    for (const changed of [{ ...value, text: 'changed' }, { ...value, attachments: [] }, { ...value, attachments: [image('two', 5)] }, { ...value, attachments: [image('one', 0)] }, { ...value, attachments: [{ ...image('one', 5), previewUrl: 'blob:new' }] }]) expect(sameDocument(value, changed)).toBe(false);
  });
});
