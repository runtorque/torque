import { text } from './model';
import styles from './PlanningWorkspace.module.css';

export function PlanningJournalEntry({ entry, author }: { entry: Record<string, unknown>; author: string }) {
  const body = text(entry.entry);
  const title = body.split('\n')[0] || 'Empty journal entry';
  const timestamp = Number(entry.timestamp);
  const date = Number.isFinite(timestamp) && timestamp > 0 ? new Date(timestamp * 1_000) : null;
  const iso = date && Number.isFinite(date.getTime()) ? date.toISOString() : '';
  return <details className={styles.journalEntry}>
    <summary><strong>{title}</strong><span><span>{author}</span> · <span>{text(entry.type, 'note')}</span>{iso ? <> · <time dateTime={iso}>{date!.toLocaleString()}</time></> : null}</span></summary>
    <p>{body || 'No entry text.'}</p>
  </details>;
}
