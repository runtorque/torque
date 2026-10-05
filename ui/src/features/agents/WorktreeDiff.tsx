import { lineChunk, initialDisclosure, records, text, count, lineCount, fileKey, useDiffDisclosure, type DiffDisclosureState } from './worktreeDiffModel';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import styles from './AgentWorkspace.module.css';

export function WorktreeDiff({ files, workspace }: { files: UnknownRecord[]; workspace?: DiffDisclosureState }) {
  const ownWorkspace = useDiffDisclosure(workspace ? [] : files);
  const [saved, setDisclosure] = workspace ?? ownWorkspace;
  const disclosure = saved ?? initialDisclosure(files);

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
