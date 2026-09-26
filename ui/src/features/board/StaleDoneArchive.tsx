import { useEffect, useRef, useState } from 'react';
import { Button } from '../../design/primitives';
import { boardWriteRequest } from './boardReadRequest';
import type { AuxiliaryFrame } from '../../protocol/types';
import { staleCompletedTasks, type BoardTask } from './model';
import styles from './StaleDoneArchive.module.css';

type ArchiveProps = { tasks: BoardTask[]; group: string; onArchived: (frame: AuxiliaryFrame) => void };
export function StaleDoneArchive(props: ArchiveProps) {
  return <OwnedStaleDoneArchive key={props.group} {...props} />;
}
function OwnedStaleDoneArchive({ tasks, group, onArchived }: ArchiveProps) {
  const [now, setNow] = useState(Date.now);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { active.current?.abort(); active.current = null; }, []);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const candidates = staleCompletedTasks(tasks, group, now);
  if (!candidates.length && !pending && !error) return null;
  const archive = () => {
    if (active.current) return;
    const ids = staleCompletedTasks(tasks, group, Date.now()).map((task) => task.id);
    if (!ids.length) return;
    const controller = new AbortController(); active.current = controller; setPending(true); setError('');
    void (async () => {
      try {
        const frame = await boardWriteRequest({ cmd: 'board_archive_tasks', ids }, controller.signal);
        if (controller.signal.aborted || active.current !== controller) return;
        if (frame.type !== 'toast' || frame.level !== 'success') throw new Error('Could not confirm the archive. Refresh the Board before retrying.');
        onArchived(frame);
      } catch (cause) { if (!controller.signal.aborted && active.current === controller) setError(cause instanceof Error ? cause.message : 'Could not archive completed tasks. Try again.'); }
      finally { if (active.current === controller) { active.current = null; if (!controller.signal.aborted) setPending(false); } }
    })();
  };
  return <section className={styles.suggestion} aria-label="Inactive completed tasks">
    {candidates.length || pending ? <><Button className={styles.archive ?? ''} tone="quiet" onPress={archive} isDisabled={pending}>{pending ? 'Archiving completed tasks…' : `Archive ${candidates.length} completed task${candidates.length === 1 ? '' : 's'} inactive for 7+ days`}</Button><p>Includes filtered tasks in this group.</p></> : null}
    {error ? <div role="alert"><p>{error}</p><Button tone="quiet" onPress={() => setError('')}>Dismiss archive error</Button></div> : null}
  </section>;
}
