import { useState, type Dispatch, type SetStateAction } from 'react';
import type { UnknownRecord } from '../../protocol';

export const lineChunk = 400;
export function records(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.filter((item): item is UnknownRecord => item !== null && typeof item === 'object' && !Array.isArray(item)) : [];
}
export function text(value: unknown): string { return typeof value === 'string' || typeof value === 'number' ? String(value) : ''; }
export function count(value: unknown): number { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : 0; }
export function lineCount(file: UnknownRecord): number { return records(file.hunks).reduce((total, hunk) => total + (Array.isArray(hunk.lines) ? hunk.lines.length : 0), 0); }
export function fileKey(file: UnknownRecord, index: number): string { return text(file.path) || `unknown-${index}`; }
export interface DiffDisclosure { collapseAll: boolean; expanded: Record<string, boolean>; limits: Record<string, number> }
export function initialDisclosure(files: UnknownRecord[]): DiffDisclosure {
  const lengths = files.map(lineCount);
  const collapseAll = files.length > 12 || lengths.reduce((sum, lines) => sum + lines, 0) > 1500 || (files.length === 1 && (lengths[0] ?? 0) > 800);
  const preview = collapseAll ? files.findIndex((_file, index) => (lengths[index] ?? 0) <= lineChunk) : -1;
  return { collapseAll, expanded: preview >= 0 ? { [fileKey(files[preview]!, preview)]: true } : {}, limits: {} };
}

export function useDiffDisclosure(files: UnknownRecord[]) {
  const state = useState<DiffDisclosure | null>(null);
  if (state[0] === null && files.length) state[1](initialDisclosure(files));
  return state;
}
export type DiffDisclosureState = [DiffDisclosure | null, Dispatch<SetStateAction<DiffDisclosure | null>>];
