import { afterEach, describe, expect, it } from 'vitest';
import { editorSelection, readComposerInput, renderComposerInput, setEditorSelection } from './composerDom';
import type { ComposerDocument } from './composerDocument';
const value: ComposerDocument = { text: 'left right\nsecond', attachments: [{ id: 'one', position: 5, path: '/one.png', filename: '<one>.png' }, { id: 'two', position: 5, path: '/two.png', filename: 'two.png' }], selection: [0, 0] };
afterEach(() => { document.body.replaceChildren(); window.getSelection()?.removeAllRanges(); });
describe('rich composer DOM mapping', () => {
  it('renders atomic text-safe tokens and round-trips every text/token caret boundary', () => {
    const node = document.createElement('div'); node.contentEditable = 'true'; document.body.append(node); renderComposerInput(node, value);
    expect(node.querySelectorAll('[data-composer-image]')).toHaveLength(2); expect(node.querySelector('one')).toBeNull();
    expect(readComposerInput(node, value.attachments).text).toBe(value.text);
    for (let offset = 0; offset <= value.text.length + value.attachments.length; offset++) {
      setEditorSelection(node, value.attachments, [offset, offset]); expect(editorSelection(node, value.attachments)).toEqual([offset, offset]);
    }
    setEditorSelection(node, value.attachments, [4, 10]); expect(editorSelection(node, value.attachments)).toEqual([4, 10]);
    setEditorSelection(node, value.attachments, [10, 4]); expect(editorSelection(node, value.attachments)).toEqual([10, 4]);
  });
  it('tracks native text changes and actual token removal without reading decorative image labels', () => {
    const node = document.createElement('div'); document.body.append(node); renderComposerInput(node, value);
    node.firstChild!.nodeValue = 'new left '; node.querySelector('[data-composer-image="one"]')!.remove();
    const next = readComposerInput(node, value.attachments); expect(next.text).toBe('new left right\nsecond'); expect(next.attachments).toEqual([{ ...value.attachments[1], position: 9 }]);
  });
  it('reads native line breaks and preserves selection across nested editable blocks', () => {
    const node = document.createElement('div'); document.body.append(node);
    const first = document.createElement('div'); first.textContent = 'one'; const second = document.createElement('div'); second.append(document.createTextNode('two'), document.createElement('br'), document.createTextNode('three')); node.append(first, second);
    expect(readComposerInput(node, []).text).toBe('one\ntwo\nthree');
    setEditorSelection(node, [], [4, 7]); expect(editorSelection(node, [])).toEqual([4, 7]);
  });
});
