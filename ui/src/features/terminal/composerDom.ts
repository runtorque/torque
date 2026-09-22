import type { ComposerAttachment } from './composerState';
import { locatedAttachments, type ComposerDocument } from './composerDocument';
export type ComposerInput = HTMLTextAreaElement | HTMLDivElement;
interface Point { node: Node; offset: number }
interface RichIndex { text: string; attachments: ComposerAttachment[]; boundaries: Map<Node, number[]>; points: Map<number, Point> }
export function attachmentId(entry: ComposerAttachment): string { return entry.id ?? entry.path; }
export function indexRichInput(root: HTMLElement, entries: ComposerAttachment[]): RichIndex {
  let text = ''; let offset = 0;
  const attachments: ComposerAttachment[] = []; const boundaries = new Map<Node, number[]>(); const points = new Map<number, Point>();
  const known = new Map(entries.map((entry) => [attachmentId(entry), entry])); const seen = new Set<string>();
  const visit = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const value = node.nodeValue ?? ''; boundaries.set(node, Array.from({ length: value.length + 1 }, (_, index) => offset + index));
      for (let index = 0; index <= value.length; index++) points.set(offset + index, { node, offset: index });
      text += value; offset += value.length; return;
    }
    if (!(node instanceof HTMLElement)) return;
    const id = node.dataset.composerImage;
    if (id) {
      const entry = known.get(id); if (entry && !seen.has(id)) { seen.add(id); attachments.push({ ...entry, position: text.length }); boundaries.set(node, [offset, offset + 1]); offset++; } return;
    }
    if (node.tagName === 'BR') { boundaries.set(node, [offset]); text += '\n'; offset++; return; }
    const block = node !== root && ['DIV', 'P'].includes(node.tagName);
    if (block && text && !text.endsWith('\n')) { text += '\n'; offset++; }
    const positions: number[] = [offset]; points.set(offset, { node, offset: 0 });
    [...node.childNodes].forEach((child, index) => { visit(child); positions.push(offset); if (!points.has(offset)) points.set(offset, { node, offset: index + 1 }); });
    boundaries.set(node, positions);
  };
  visit(root); return { text, attachments, boundaries, points };
}
function selectionOffset(root: HTMLElement, index: RichIndex, node: Node | null, offset: number): number | null {
  if (!node || !(node === root || root.contains(node))) return null;
  const direct = index.boundaries.get(node); if (direct) return direct[Math.min(offset, direct.length - 1)] ?? 0;
  const token = (node instanceof HTMLElement ? node : node.parentElement)?.closest<HTMLElement>('[data-composer-image]');
  return token ? index.boundaries.get(token)?.[offset ? 1 : 0] ?? null : null;
}
export function editorSelection(node: ComposerInput, attachments: ComposerAttachment[], fallback: [number, number] = [0, 0]): [number, number] {
  if (node instanceof HTMLTextAreaElement) return node.selectionDirection === 'backward' ? [node.selectionEnd, node.selectionStart] : [node.selectionStart, node.selectionEnd];
  const selected = window.getSelection(); if (!selected) return fallback;
  const index = indexRichInput(node, attachments);
  const start = selectionOffset(node, index, selected.anchorNode, selected.anchorOffset); const end = selectionOffset(node, index, selected.focusNode, selected.focusOffset);
  return start === null || end === null ? fallback : [start, end];
}
export function readComposerInput(node: ComposerInput, attachments: ComposerAttachment[], fallback: [number, number] = [0, 0]): ComposerDocument {
  if (node instanceof HTMLTextAreaElement) return { text: node.value, attachments: [], selection: editorSelection(node, []) };
  const value = indexRichInput(node, attachments); return { text: value.text, attachments: value.attachments, selection: editorSelection(node, attachments, fallback) };
}
export function setEditorSelection(node: ComposerInput, attachments: ComposerAttachment[], range: [number, number]): void {
  if (node instanceof HTMLTextAreaElement) { node.setSelectionRange(Math.min(...range), Math.max(...range), range[0] > range[1] ? 'backward' : 'forward'); return; }
  const existing = window.getSelection();
  if (existing?.anchorNode && node.contains(existing.anchorNode)) {
    const current = editorSelection(node, attachments); if (current[0] === range[0] && current[1] === range[1]) return;
  }
  const index = indexRichInput(node, attachments); const length = index.text.length + index.attachments.length;
  const point = (offset: number) => index.points.get(Math.max(0, Math.min(length, offset))) ?? { node, offset: node.childNodes.length };
  const start = point(range[0]); const end = point(range[1]); const selection = window.getSelection(); if (!selection) return;
  selection.setBaseAndExtent(start.node, start.offset, end.node, end.offset);
}
export function renderComposerInput(node: HTMLDivElement, value: ComposerDocument): void {
  const fragment = document.createDocumentFragment(); let cursor = 0;
  for (const { entry, position } of locatedAttachments(value)) {
    fragment.append(document.createTextNode(value.text.slice(cursor, position))); cursor = position;
    const chip = document.createElement('span'); chip.dataset.composerImage = attachmentId(entry); chip.contentEditable = 'false'; chip.tabIndex = 0; chip.setAttribute('role', 'button');
    chip.setAttribute('aria-label', `Preview attached image ${entry.filename}`); chip.title = entry.filename || entry.path; chip.textContent = `▧ ${entry.filename || 'Image'}`;
    fragment.append(chip);
  }
  fragment.append(document.createTextNode(value.text.slice(cursor))); node.replaceChildren(fragment);
}
