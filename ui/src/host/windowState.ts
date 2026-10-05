import type { UnknownRecord } from '../protocol';
import type { WindowBounds } from './types';
function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
/** Releasing a window relinquishes ownership while retaining its last geometry. */
export function releaseWindow(windows: UnknownRecord, panel: string, label: string): UnknownRecord {
  const entry = record(windows[panel]);
  if (entry.label && entry.label !== label) return windows;
  const next = { ...windows };
  if (entry.bounds && typeof entry.bounds === 'object') next[panel] = { ...entry, label: '' };
  else delete next[panel];
  return next;
}
export function savedWindowBounds(entry: unknown, fallback: WindowBounds): WindowBounds {
  const bounds = record(record(entry).bounds);
  return typeof bounds.width === 'number' && Number.isFinite(bounds.width) && bounds.width > 0 && typeof bounds.height === 'number' && Number.isFinite(bounds.height) && bounds.height > 0 ? bounds : fallback;
}
