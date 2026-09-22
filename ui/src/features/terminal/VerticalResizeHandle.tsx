import { useRef, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react';
import styles from './TerminalSurface.module.css';
const RESIZE_STEP = 24;
const clampHeight = (height: number, minimum: number, maximum: number) => Math.max(minimum, Math.min(maximum, Math.round(height)));

interface VerticalResizeHandleProps {
  label: string;
  value: number;
  minimum: number;
  maximum: number;
  className?: string;
  onChange: (height: number) => void;
  onCommit: (height: number) => void;
}

export function VerticalResizeHandle({ label, value, minimum, maximum, className = '', onChange, onCommit }: VerticalResizeHandleProps) {
  const resizeStart = useRef<{ pointerId: number; clientY: number; height: number } | null>(null);
  const resizeFromPointer = (clientY: number): number => {
    const start = resizeStart.current;
    if (!start) return value;
    const height = clampHeight(start.height + start.clientY - clientY, minimum, maximum);
    onChange(height);
    return height;
  };
  const beginResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    resizeStart.current = { pointerId: event.pointerId, clientY: event.clientY, height: value };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const moveResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (resizeStart.current?.pointerId !== event.pointerId) return;
    resizeFromPointer(event.clientY);
  };
  const finishResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (resizeStart.current?.pointerId !== event.pointerId) return;
    const height = resizeFromPointer(event.clientY);
    resizeStart.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    onCommit(height);
  };
  const cancelResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = resizeStart.current;
    if (start?.pointerId !== event.pointerId) return;
    onChange(start.height);
    resizeStart.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };
  const resizeFromKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    let height = value;
    if (event.key === 'ArrowUp') height += RESIZE_STEP;
    else if (event.key === 'ArrowDown') height -= RESIZE_STEP;
    else if (event.key === 'Home') height = minimum;
    else if (event.key === 'End') height = maximum;
    else return;
    event.preventDefault();
    height = clampHeight(height, minimum, maximum);
    onChange(height);
    onCommit(height);
  };

  return <div
    className={`${styles.resizeHandle} ${className}`}
    role="separator"
    aria-label={label}
    aria-orientation="horizontal"
    aria-valuemin={minimum}
    aria-valuemax={maximum}
    aria-valuenow={value}
    tabIndex={0}
    onPointerDown={beginResize}
    onPointerMove={moveResize}
    onPointerUp={finishResize}
    onPointerCancel={cancelResize}
    onKeyDown={resizeFromKeyboard}
  ><span aria-hidden="true" /></div>;
}

