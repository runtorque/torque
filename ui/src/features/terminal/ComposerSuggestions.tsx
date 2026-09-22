import { useLayoutEffect, useRef, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import type { Completion, CompletionItem } from './completionModel';
import styles from './ComposerSuggestions.module.css';
export function ComposerSuggestions({ id, input, completion, index, onPick, onChoose }: { id: string; input: RefObject<HTMLTextAreaElement | null>; completion: Completion; index: number; onPick: (item: CompletionItem) => void; onChoose: (item: CompletionItem) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const update = () => {
      const anchor = input.current; const node = ref.current; if (!anchor || !node) return;
      const box = anchor.getBoundingClientRect(); const above = box.top - 8; const below = window.innerHeight - box.bottom - 8;
      const placeAbove = above >= Math.min(240, Math.max(100, below)); const room = Math.max(48, Math.min(240, placeAbove ? above : below));
      node.style.width = `${Math.max(0, Math.min(box.width, window.innerWidth - 16))}px`; node.style.maxHeight = `${room}px`;
      node.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - node.offsetWidth - 8))}px`;
      const preferredTop = placeAbove ? box.top - node.offsetHeight - 4 : box.bottom + 4;
      node.style.top = `${Math.max(4, Math.min(preferredTop, window.innerHeight - node.offsetHeight - 4))}px`;
    };
    update(); window.addEventListener('resize', update); window.addEventListener('scroll', update, true);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update); if (input.current) observer?.observe(input.current);
    return () => { window.removeEventListener('resize', update); window.removeEventListener('scroll', update, true); observer?.disconnect(); };
  }, [input, completion]);
  useLayoutEffect(() => { if (index >= 0) ref.current?.querySelector<HTMLElement>(`[data-index="${index}"]`)?.scrollIntoView?.({ block: 'nearest' }); }, [index]);
  return createPortal(<div ref={ref} id={id} role="listbox" aria-label={completion.kind === 'command' ? 'Message commands' : 'Task references'} className={styles.suggestions} onMouseDown={(event) => event.preventDefault()}>
    {completion.items.map((item, position) => <button key={item.id} id={`${id}-${position}`} data-index={position} type="button" role="option" aria-label={[item.label, item.detail].filter(Boolean).join(' ')} aria-selected={position === index} tabIndex={-1} onMouseEnter={() => onChoose(item)} onClick={() => onPick(item)}><strong>{item.label}</strong>{item.detail ? <span>{item.detail}</span> : null}</button>)}
  </div>, document.body);
}
