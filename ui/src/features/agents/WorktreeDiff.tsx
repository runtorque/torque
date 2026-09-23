import { useState } from 'react';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import styles from './AgentWorkspace.module.css';

const lineChunk = 400;
function records(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.filter((item): item is UnknownRecord => item !== null && typeof item === 'object' && !Array.isArray(item)) : [];
}
function text(value: unknown): string { return typeof value === 'string' || typeof value === 'number' ? String(value) : ''; }
function count(value: unknown): number { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : 0; }
function lineCount(file: UnknownRecord): number { return records(file.hunks).reduce((total, hunk) => total + (Array.isArray(hunk.lines) ? hunk.lines.length : 0), 0); }
function fileKey(file: UnknownRecord, index: number): string { return text(file.path) || `unknown-${index}`; }
interface Disclosure { collapseAll: boolean; expanded: Record<string, boolean>; limits: Record<string, number> }
function initialDisclosure(files: UnknownRecord[]): Disclosure {
  const lengths = files.map(lineCount);
  const collapseAll = files.length > 12 || lengths.reduce((sum, lines) => sum + lines, 0) > 1500 || (files.length === 1 && (lengths[0] ?? 0) > 800);
  const preview = collapseAll ? files.findIndex((_file, index) => (lengths[index] ?? 0) <= lineChunk) : -1;
  return { collapseAll, expanded: preview >= 0 ? { [fileKey(files[preview]!, preview)]: true } : {}, limits: {} };
}

export function WorktreeDiff({ files }: { files: UnknownRecord[] }) {
  const [saved, setDisclosure] = useState<Disclosure | null>(null);
  const disclosure = saved ?? initialDisclosure(files);
  // Initialize when the first actual diff arrives, not while its read is pending.
  if (saved === null && files.length) setDisclosure(disclosure);
  const expanded = (key: string) => disclosure.expanded[key] ?? !disclosure.collapseAll;
  const collapsed = files.filter((file, index) => !expanded(fileKey(file, index))).length;
  if (!files.length) return null;
  return <>
    <div className={styles.diffToolbar}>
      <Button tone="quiet" isDisabled={collapsed === files.length} onPress={() => setDisclosure({ ...disclosure, collapseAll: true, expanded: {} })}>Collapse all</Button>
      <Button tone="quiet" isDisabled={collapsed === 0} onPress={() => setDisclosure({ ...disclosure, collapseAll: false, expanded: {} })}>Expand all</Button>
      <span aria-live="polite">{collapsed} of {files.length} collapsed</span>
    </div>
    <div className={styles.diffFiles} role="region" aria-label="Worktree diff files">
      {files.map((file, index) => {
        const key = fileKey(file, index); const open = expanded(key); const total = lineCount(file);
        const limit = disclosure.limits[key] ?? lineChunk; let remaining = limit;
        const hunks = open ? records(file.hunks).flatMap((hunk) => {
          if (remaining <= 0) return [];
          const lines = records(Array.isArray(hunk.lines) ? hunk.lines.slice(0, remaining) : []); remaining -= lines.length;
          return [{ header: text(hunk.header), lines }];
        }) : [];
        const shown = Math.min(total, limit);
        return <section key={key} data-diff-path={key}>
          <button type="button" className={styles.diffFileHeader} aria-expanded={open} onClick={() => setDisclosure({ ...disclosure, expanded: { ...disclosure.expanded, [key]: !open } })}>
            <span><span aria-hidden="true">{open ? '▾ ' : '▸ '}</span>{text(file.path) || '(unknown file)'}</span>
            <small>{text(file.status)} · <b className={styles.diffAdd}>+{count(file.insertions)}</b> <b className={styles.diffDelete}>−{count(file.deletions)}</b></small>
          </button>
          {open ? file.binary ? <p>Binary file changed.</p> : !total ? <p>No line-by-line diff for this file.</p> : <>
            {hunks.map((hunk, hunkIndex) => <section key={`${hunk.header}-${hunkIndex}`} className={styles.diffHunk}>
              <header>{hunk.header}</header><pre>{hunk.lines.map((line, lineIndex) => {
                const type = text(line.type); const prefix = type === 'add' ? '+' : type === 'del' ? '−' : ' ';
                return <span key={lineIndex} className={type === 'add' ? styles.diffLineAdd : type === 'del' ? styles.diffLineDelete : ''}>{prefix}{text(line.text)}{'\n'}</span>;
              })}</pre>
            </section>)}
            {shown < total ? <div className={styles.diffLoadMore}><Button tone="quiet" onPress={() => setDisclosure({ ...disclosure, limits: { ...disclosure.limits, [key]: limit + lineChunk } })}>Show {Math.min(lineChunk, total - shown)} more lines</Button><span>{total - shown} remaining</span></div> : null}
          </> : null}
        </section>;
      })}
    </div>
  </>;
}
