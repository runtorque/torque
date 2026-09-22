import { useEffect, useRef, useState } from 'react';
import { Button } from '../../design/primitives';
import { readCommand } from '../../protocol/http';
import type { AuxiliaryFrame } from '../../protocol/types';
import { staleCompletedTasks, type BoardTask } from './model';
import styles from './StaleDoneArchive.module.css';

export function StaleDoneArchive({ tasks, group, onArchived }: { tasks: BoardTask[]; group: string; onArchived: (frame: AuxiliaryFrame) => void }) {
  const [now, setNow] = useState(Date.now);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const busy = useRef(false);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const candidates = staleCompletedTasks(tasks, group, now);
  if (!candidates.length && !pending && !error) return null;
  const archive = () => {
    if (busy.current) return;
    const ids = staleCompletedTasks(tasks, group, Date.now()).map((task) => task.id);
    if (!ids.length) return;
    busy.current = true; setPending(true); setError('');
    void (async () => {
      try {
        const frame = await readCommand({ cmd: 'board_archive_tasks', ids }, new AbortController().signal);
        if (frame.type !== 'toast' || frame.level !== 'success') throw new Error('Could not confirm the archive. Refresh the Board before retrying.');
        onArchived(frame);
      } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not archive completed tasks. Try again.'); }
      finally { busy.current = false; setPending(false); }
    })();
  };
  return <section className={styles.suggestion} aria-label="Inactive completed tasks">
    {candidates.length || pending ? <><Button className={styles.archive ?? ''} tone="quiet" onPress={archive} isDisabled={pending}>{pending ? 'Archiving completed tasks…' : `Archive ${candidates.length} completed task${candidates.length === 1 ? '' : 's'} inactive for 7+ days`}</Button><p>Includes filtered tasks in this group.</p></> : null}
    {error ? <div role="alert"><p>{error}</p><Button tone="quiet" onPress={() => setError('')}>Dismiss archive error</Button></div> : null}
  </section>;
}
