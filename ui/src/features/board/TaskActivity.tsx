import { useEffect, useState } from 'react';
import { readCommand } from '../../protocol/http';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { taskText } from './taskCreationModel';
import { taskMessageDate } from './taskActivityModel';
import styles from './BoardPanel.module.css';
export function TaskActivity({ messages: incoming, taskId, active = true }: { messages: UnknownRecord[]; taskId?: string; active?: boolean }) {
  // Compact wire frames contain a count summary, not a truncated activity row.
  const compact = incoming.length === 1 && typeof incoming[0]?.count === 'number';
  const compactKey = compact ? JSON.stringify(incoming) : '';
  const [loaded, setLoaded] = useState(compact ? [] : incoming);
  const [error, setError] = useState(''); const [retry, setRetry] = useState(0);
  const messages = compact ? loaded : incoming;
  const [end, setEnd] = useState(messages.length); const [limit, setLimit] = useState(40);
  useEffect(() => {
    if (!compactKey || !taskId || !active) return;
    const controller = new AbortController();
    void readCommand({ cmd: 'task_detail', id: taskId }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type === 'error') throw new Error(taskText(frame.message, 'Could not refresh activity.'));
      const task = frame.task as UnknownRecord | undefined;
      if (!Array.isArray(task?.messages)) throw new Error('Could not refresh activity.');
      const next = task.messages as UnknownRecord[];
      setLoaded(next); setEnd((previous) => previous || next.length); setError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not refresh activity.'); });
    return () => controller.abort();
  }, [active, compactKey, retry, taskId]);
  const visibleEnd = Math.min(end, messages.length); const start = Math.max(0, visibleEnd - limit);
  return <section aria-label="Task activity" className={styles.messageHistory}>
    <header><h3>Task activity · {messages.length}</h3>{messages.length > visibleEnd ? <Button onPress={() => setEnd(messages.length)}>Show {messages.length - visibleEnd} new messages</Button> : null}</header>
    {error ? <p role="alert">{error} <Button onPress={() => setRetry((value) => value + 1)}>Retry activity</Button></p> : null}
    {!messages.length ? <p>No task activity yet.</p> : null}
    {messages.slice(start, visibleEnd).map((message, index) => ({ message, sequence: start + index + 1 })).reverse().map(({ message, sequence }) => {
      const date = taskMessageDate(message.timestamp);
      return <article key={taskText(message.id, String(sequence))} aria-label={`Activity ${sequence}`}>
        <header><span>#{sequence}</span><strong>{taskText(message.action, 'update')}</strong>{message.agent ? <span>{taskText(message.agent)}</span> : null}{date ? <time dateTime={date.toISOString()} title={date.toLocaleString()}>{date.toLocaleString()}</time> : null}</header>
        <p>{taskText(message.message ?? message.comment ?? message.status)}</p>
      </article>;
    })}
    {start > 0 ? <Button onPress={() => setLimit((count) => count + 40)}>Load older activity · {start} remaining</Button> : null}
  </section>;
}
