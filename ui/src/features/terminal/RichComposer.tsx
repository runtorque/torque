import { useLayoutEffect, useRef, type HTMLAttributes, type KeyboardEvent, type RefObject } from 'react';
import { locatedAttachments, replaceDocumentRange, sameDocument, type ComposerDocument } from './composerDocument';
import { attachmentId, editorSelection, readComposerInput, renderComposerInput, setEditorSelection } from './composerDom';
import type { ComposerInput } from './composerDom';
import { composerEditKind, type ComposerEditKind } from './composerState';
import styles from './RichComposer.module.css';
interface Props {
  inputRef: RefObject<ComposerInput | null>; value: ComposerDocument; disabled: boolean; height: number; name: string;
  onChange: (value: ComposerDocument, kind?: ComposerEditKind) => void; onSelection: (selection: [number, number], scrollTop: number) => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void; onFiles: (files: File[]) => void; onPreview: (id: string) => void;
  onFocus: (focused: boolean) => void; onComposition: (active: boolean) => void;
  aria: Pick<HTMLAttributes<HTMLElement>, 'aria-controls' | 'aria-activedescendant' | 'aria-expanded'>;
}
export function RichComposer({ inputRef, value, disabled, height, name, onChange, onSelection, onKeyDown, onFiles, onPreview, onFocus, onComposition, aria }: Props) {
  const composing = useRef(false); const nodeRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = nodeRef.current; if (!node || composing.current) return;
    const current = readComposerInput(node, value.attachments, value.selection);
    const ids = [...node.querySelectorAll<HTMLElement>('[data-composer-image]')].map((token) => token.dataset.composerImage);
    const expected = locatedAttachments(value).map(({ entry }) => attachmentId(entry));
    if (!sameDocument(current, value) || ids.join('\n') !== expected.join('\n')) renderComposerInput(node, value);
    if (document.activeElement === node) setEditorSelection(node, value.attachments, value.selection);
  }, [value.text, value.attachments, value.selection, value]);
  const capture = () => { const node = nodeRef.current; if (node) onSelection(editorSelection(node, value.attachments, value.selection), node.scrollTop); };
  const edit = (inputType?: string) => { const node = nodeRef.current; if (node && !disabled) onChange(readComposerInput(node, value.attachments, value.selection), composerEditKind(inputType)); };
  const insert = (text: string, kind: ComposerEditKind = null) => { const node = nodeRef.current; if (node) { capture(); onChange(replaceDocumentRange(readComposerInput(node, value.attachments, value.selection), editorSelection(node, value.attachments, value.selection), text), kind); } };
  return <div ref={(node) => { nodeRef.current = node; inputRef.current = node; }} role="textbox" aria-multiline="true" aria-label={`Message ${name}`} aria-disabled={disabled} contentEditable={!disabled} suppressContentEditableWarning className={styles.editor} style={{ height }} tabIndex={0} spellCheck={false}
    aria-autocomplete="list" aria-haspopup="listbox" {...aria} onInput={(event) => edit(event.nativeEvent.inputType)} onSelect={capture} onMouseUp={capture} onKeyUp={capture} onScroll={capture}
    onFocus={() => onFocus(true)} onBlur={() => { capture(); onFocus(false); }}
    onCompositionStart={() => { composing.current = true; onComposition(true); }} onCompositionEnd={() => { edit(); composing.current = false; onComposition(false); }}
    onClick={(event) => { const token = (event.target as HTMLElement).closest<HTMLElement>('[data-composer-image]'); if (token) onPreview(token.dataset.composerImage!); }}
    onPaste={(event) => { event.preventDefault(); if (disabled) return; const files = [...event.clipboardData.files]; if (files.length) onFiles(files); else insert(event.clipboardData.getData('text/plain')); }}
    onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); if (disabled) return; const files = [...event.dataTransfer.files]; if (files.length) onFiles(files); else insert(event.dataTransfer.getData('text/plain')); }}
    onKeyDown={(event) => {
      if (disabled || composing.current || event.nativeEvent.isComposing) return;
      const token = (event.target as HTMLElement).closest<HTMLElement>('[data-composer-image]');
      const image = token ? locatedAttachments(value).find(({ entry }) => attachmentId(entry) === token.dataset.composerImage) : undefined;
      if (image && ['Enter', ' '].includes(event.key)) { event.preventDefault(); event.stopPropagation(); onPreview(attachmentId(image.entry)); return; }
      const node = nodeRef.current!; const current = readComposerInput(node, value.attachments, value.selection); const start = Math.min(...current.selection); const end = Math.max(...current.selection);
      if (!event.ctrlKey && !event.metaKey && !event.altKey && ['Backspace', 'Delete'].includes(event.key)) {
        const range: [number, number] = image ? [image.offset, image.offset + 1] : start === end ? event.key === 'Backspace' ? [Math.max(0, start - 1), start] : [start, end + 1] : [start, end];
        if (locatedAttachments(current).some((item) => item.offset >= range[0] && item.offset < range[1])) { event.preventDefault(); capture(); onChange(replaceDocumentRange(current, range)); return; }
      }
      if (event.key === 'Enter' && event.shiftKey) { event.preventDefault(); insert('\n', 'typing'); return; }
      onKeyDown(event);
    }} />;
}
