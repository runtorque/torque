import type { ComposerAttachment } from './composerState';

export interface ComposerDocument {
  text: string;
  attachments: ComposerAttachment[];
  // Editor offsets count each atomic image as one unit; text excludes the tokens.
  selection: [number, number];
}
export interface UploadAnchor { key: string; selection: [number, number] }
export interface LocatedAttachment { entry: ComposerAttachment; position: number; offset: number }
const clamp = (value: number, maximum: number) => Math.max(0, Math.min(maximum, Number.isFinite(value) ? Math.floor(value) : maximum));
export function locatedAttachments(document: Pick<ComposerDocument, 'text' | 'attachments'>): LocatedAttachment[] {
  return document.attachments.map((entry) => ({ entry, position: clamp(entry.position ?? document.text.length, document.text.length) }))
    .sort((a, b) => a.position - b.position).map((item, index) => ({ ...item, offset: item.position + index }));
}
export function editorLength(document: Pick<ComposerDocument, 'text' | 'attachments'>): number { return document.text.length + document.attachments.length; }
export function plainOffset(document: Pick<ComposerDocument, 'text' | 'attachments'>, offset: number): number {
  const bounded = clamp(offset, editorLength(document));
  return bounded - locatedAttachments(document).filter((item) => item.offset < bounded).length;
}
export function editorOffset(document: Pick<ComposerDocument, 'text' | 'attachments'>, position: number, afterImages = true): number {
  const bounded = clamp(position, document.text.length);
  return bounded + locatedAttachments(document).filter((item) => afterImages ? item.position <= bounded : item.position < bounded).length;
}
export function editorText(document: Pick<ComposerDocument, 'text' | 'attachments'>): string {
  let value = ''; let cursor = 0;
  for (const { position } of locatedAttachments(document)) { value += document.text.slice(cursor, position) + '\ufffc'; cursor = position; }
  return value + document.text.slice(cursor);
}
export function messageWithAttachments(document: Pick<ComposerDocument, 'text' | 'attachments'>): string {
  let value = ''; let cursor = 0;
  const entries = locatedAttachments(document);
  entries.forEach(({ entry, position }, index) => {
    value += document.text.slice(cursor, position);
    if (index && entries[index - 1]?.position === position) value += '\n';
    else if (entry.position === undefined && value && !value.endsWith('\n')) value += '\n';
    value += entry.path; cursor = position;
    const following = document.text.slice(cursor, entries[index + 1]?.position ?? document.text.length);
    if (following && !/^\s/.test(following)) value += ' ';
  });
  return value + document.text.slice(cursor);
}
export function textChange(previous: string, next: string) {
  let start = 0; while (start < Math.min(previous.length, next.length) && previous[start] === next[start]) start++;
  let oldEnd = previous.length; let newEnd = next.length;
  while (oldEnd > start && newEnd > start && previous[oldEnd - 1] === next[newEnd - 1]) { oldEnd--; newEnd--; }
  return { start, oldEnd, newEnd, delta: next.length - previous.length };
}
export function moveAttachments(previous: string, next: string, attachments: ComposerAttachment[]): ComposerAttachment[] {
  if (previous === next) return attachments;
  const { start, oldEnd, delta } = textChange(previous, next);
  return attachments.map((entry) => {
    const position = clamp(entry.position ?? previous.length, previous.length);
    return { ...entry, position: clamp(position >= oldEnd ? position + delta : position >= start ? start : position, next.length) };
  });
}
export function moveUploadAnchor(anchor: UploadAnchor | null, previous: ComposerDocument, next: ComposerDocument): UploadAnchor | null {
  if (!anchor) return null;
  const before = editorText(previous); const after = editorText(next); if (before === after) return anchor;
  const { start, oldEnd, newEnd, delta } = textChange(before, after); const left = Math.min(...anchor.selection); const right = Math.max(...anchor.selection);
  if (oldEnd <= left) return { ...anchor, selection: anchor.selection.map((offset) => clamp(offset + delta, after.length)) as [number, number] };
  if (start >= right) return anchor;
  // Editing over a pending insertion must not delete the user's new text when
  // the upload completes. Collapse the replacement to the end of that edit.
  const caret = clamp(Math.max(newEnd, right + delta), after.length);
  return { ...anchor, selection: [caret, caret] };
}
export function replaceDocumentRange(document: ComposerDocument, range: [number, number], insertedText = '', images: ComposerAttachment[] = []): ComposerDocument {
  const start = clamp(Math.min(...range), editorLength(document)); const end = clamp(Math.max(...range), editorLength(document));
  const left = plainOffset(document, start); const right = plainOffset(document, end);
  const delta = insertedText.length - (right - left);
  const located = locatedAttachments(document);
  const before = located.filter((item) => item.offset < start).map(({ entry, position }) => ({ ...entry, position }));
  const after = located.filter((item) => item.offset >= end).map(({ entry, position }) => ({ ...entry, position: position + delta }));
  const attachments = [...before, ...images.map((entry) => ({ ...entry, position: left + insertedText.length })), ...after];
  const caret = start + insertedText.length + images.length;
  return { text: document.text.slice(0, left) + insertedText + document.text.slice(right), attachments, selection: [caret, caret] };
}
export function sameDocument(left: ComposerDocument, right: ComposerDocument): boolean {
  const before = locatedAttachments(left); const after = locatedAttachments(right);
  return left.text === right.text && before.length === after.length && before.every(({ entry, position }, index) => {
    const other = after[index]; return other && entry.id === other.entry.id && entry.path === other.entry.path && position === other.position && entry.previewUrl === other.entry.previewUrl;
  });
}
