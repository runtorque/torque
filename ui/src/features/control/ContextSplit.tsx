import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectWorkspaceState } from '../../app/store';
import { readCommand } from '../../protocol/http';
import { Button } from '../../design/primitives';
import styles from './ControlCenter.module.css';

const clamp = (value: unknown) => {
  const number = value == null ? 0.38 : Number(value);
  return Number.isFinite(number) ? Math.max(0.28, Math.min(0.62, number)) : 0.38;
};
export function ContextSplit({ list, detail }: { list: ReactNode; detail: ReactNode }) {
  const persisted = clamp(useAppSelector(selectWorkspaceState).contextSplitRatio);
  const [ratio, setRatio] = useState(persisted);
  const current = useRef(ratio); const dirty = useRef(false); const acknowledged = useRef(persisted);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [compact, setCompact] = useState(false);
  const browser = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; left: number; width: number; ratio: number } | null>(null);
  const alive = useRef(true); const active = useRef<AbortController | null>(null); const queued = useRef<number | null>(null);
  const change = (next: number) => { current.current = next; setRatio(next); };
  useEffect(() => { alive.current = true; return () => { alive.current = false; active.current?.abort(); queued.current = null; }; }, []);
  useEffect(() => { acknowledged.current = persisted; if (!dirty.current && !drag.current) { current.current = persisted; setRatio(persisted); } }, [persisted]);
  useEffect(() => {
    const node = browser.current; if (!node || typeof ResizeObserver === 'undefined') return;
    const measure = () => { const width = node.getBoundingClientRect().width; const narrow = width > 0 && width <= 900; if (narrow && drag.current) { current.current = drag.current.ratio; setRatio(drag.current.ratio); if (!active.current && queued.current === null && drag.current.ratio === acknowledged.current) dirty.current = false; drag.current = null; } setCompact(narrow); };
    const observer = new ResizeObserver(measure); observer.observe(node); measure();
    return () => observer.disconnect();
  }, []);
  // Keep one write in flight and coalesce later commits to the latest width.
  const save = async (next: number) => {
    queued.current = next; dirty.current = true;
    if (active.current) return;
    setSaving(true); setError('');
    while (queued.current !== null && alive.current) {
      const requested = queued.current; queued.current = null;
      const controller = new AbortController(); active.current = controller;
      try {
        const frame = await readCommand({ cmd: 'ui_set_context_panel_split', ratio: requested }, controller.signal);
        if (controller.signal.aborted) return;
        if (frame.type !== 'state' || typeof frame.context_panel_split_ratio !== 'number' || Math.abs(frame.context_panel_split_ratio - requested) > 0.000001) throw new Error(typeof frame.message === 'string' ? frame.message : 'The pane width was not acknowledged.');
        acknowledged.current = requested;
        if (queued.current === null && current.current === requested) dirty.current = false;
        setError('');
      } catch (cause: unknown) {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : 'Could not save the pane width.');
      } finally { if (active.current === controller) active.current = null; }
    }
    if (alive.current) setSaving(false);
  };
  const cancel = () => { const start = drag.current; if (!start) return; drag.current = null; change(start.ratio); if (!active.current && queued.current === null && start.ratio === acknowledged.current) dirty.current = false; };
  return <div className={styles.contextSplitFrame}>
    {error ? <p role="alert">Pane width was not saved. {error} <Button onPress={() => { void save(current.current); }}>Retry pane width</Button></p> : null}
    <div ref={browser} className={styles.contextSplit} data-compact={compact} style={{ '--context-split': `${ratio}fr`, '--context-detail-split': `${1 - ratio}fr` } as CSSProperties}>
      {list}
      <div className={styles.contextResize} hidden={compact} role="separator" aria-label="Resize Context panes" aria-orientation="vertical" aria-valuemin={28} aria-valuemax={62} aria-valuenow={Math.round(ratio * 100)} aria-valuetext={`${Math.round(ratio * 100)}% list width${saving ? ', saving' : ''}`} tabIndex={0}
        onPointerDown={(event) => {
          if (event.button !== 0 || compact) return;
          const rect = browser.current?.getBoundingClientRect(); if (!rect?.width) return;
          event.preventDefault(); drag.current = { id: event.pointerId, left: rect.left, width: rect.width, ratio: current.current };
          event.currentTarget.setPointerCapture?.(event.pointerId);
        }}
        onPointerMove={(event) => { const start = drag.current; if (start?.id === event.pointerId) change(clamp((event.clientX - start.left) / start.width)); }}
        onPointerUp={(event) => {
          const start = drag.current; if (start?.id !== event.pointerId) return;
          const next = clamp((event.clientX - start.left) / start.width); drag.current = null; change(next);
          event.currentTarget.releasePointerCapture?.(event.pointerId); void save(next);
        }}
        onPointerCancel={(event) => { if (drag.current?.id === event.pointerId) cancel(); }} onLostPointerCapture={(event) => { if (drag.current?.id === event.pointerId) cancel(); }}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && drag.current) { event.preventDefault(); cancel(); return; }
          if (compact || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = clamp(event.key === 'Home' ? 0.28 : event.key === 'End' ? 0.62 : current.current + (event.key === 'ArrowLeft' ? -0.02 : 0.02));
          change(next); void save(next);
        }}
      ><span aria-hidden="true" /></div>
      {detail}
    </div>
  </div>;
}
