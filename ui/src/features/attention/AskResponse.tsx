import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectAgentsState, selectConnection, selectTasksState } from '../../app/store';
import { Button } from '../../design/primitives';
import type { TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { BehaviorReview } from './BehaviorReview';
import { askTarget, isOpenAsk, labels, proposalId, record, text } from './model';
import styles from './Attention.module.css';

export function AskResponse({ taskId, send }: { taskId: string; send: (command: TorqueCommand) => void }) {
  const dispatch = useAppDispatch();
  const tasks = useAppSelector(selectTasksState).records;
  const agents = useAppSelector(selectAgentsState).records;
  const reconnect = useAppSelector(selectConnection).reconnectCount;
  const task = record(tasks[taskId]);
  const parentId = text(task.parent_task_id);
  const parent = record(tasks[parentId]);
  const [hydrated, setHydrated] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [loadError, setLoadError] = useState('');
  const [answer, setAnswer] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [resolved, setResolved] = useState(false);
  const [review, setReview] = useState(false);
  const busy = useRef(false);
  const loadKey = `${taskId}:${parentId}:${reconnect}:${refresh}`;
  const ready = hydrated === loadKey;
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const frame = await readCommand({ cmd: 'task_detail', id: taskId }, controller.signal);
        if (controller.signal.aborted) return;
        if (frame.id !== taskId || !frame.task) throw new Error('Question detail was not returned.');
        dispatch(projectionActions.taskDetailReceived(frame));
        const parent = text(record(frame.task).parent_task_id);
        if (parent) {
          const detail = await readCommand({ cmd: 'task_detail', id: parent }, controller.signal);
          if (controller.signal.aborted) return;
          if (detail.id !== parent || !detail.task) throw new Error('Parent task detail was not returned.');
          dispatch(projectionActions.taskDetailReceived(detail));
        }
        setHydrated(loadKey); setLoadError('');
      } catch (cause) { if (!controller.signal.aborted) setLoadError(cause instanceof Error ? cause.message : 'Could not load the full question.'); }
    };
    void load();
    return () => controller.abort();
  }, [dispatch, taskId, loadKey]);
  const target = askTarget(task, tasks, agents);
  const approval = labels(task).includes('behavior-overlay-approval');
  const proposal = proposalId(task);
  const submit = async () => {
    if (busy.current || !ready || approval || !target.answerable || !isOpenAsk(task) || resolved || !answer.trim()) return;
    busy.current = true; setPending(true); setError('');
    const requestId = `react-ask-${crypto.randomUUID()}`;
    try {
      const frame = await readCommand({ cmd: 'resolve_ask', id: taskId, answer: answer.trim(), request_id: requestId }, new AbortController().signal);
      if (frame.command !== 'resolve_ask' || frame.request_id !== requestId) throw new Error('Could not confirm delivery. Refresh the question before retrying.');
      setAnswer(''); setResolved(true);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not confirm delivery. Your answer is retained.'); }
    finally { busy.current = false; setPending(false); }
  };
  if (!isOpenAsk(task)) return null;
  return <section className={styles.ask} aria-label={`Response to ${text(task.task)}`} aria-busy={pending}>
    <h3>{text(task.task) || 'Question'}</h3>
    <p>{text(task.description)}</p>
    {parentId ? <details><summary>Parent: {text(parent.task) || parentId}</summary><p>{text(parent.description)}</p></details> : null}
    {loadError ? <p role="alert">{loadError}</p> : !ready ? <p role="status">Loading full question…</p> : null}
    {approval ? <><p>Review the proposed behavior changes before deciding.</p><Button isDisabled={!ready || !proposal} onPress={() => setReview(true)}>Review behavior diff</Button>{ready && !proposal ? <p role="alert">This approval has no proposal reference.</p> : null}{review ? <BehaviorReview key={proposal} proposalId={proposal} onClose={() => setReview(false)} /> : null}</> : <>
      <p>Reply to {text(target.agent.name) || target.id || 'unknown agent'}</p>
      {!target.answerable ? <p>{target.reason} This question remains available when the agent resumes.</p> : null}
      <label>Answer {text(task.task)}<textarea value={answer} disabled={pending || resolved} onChange={(event) => setAnswer(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(); } }} /></label>
      {error ? <p role="alert">{error}</p> : null}{resolved ? <p role="status">Answer delivered.</p> : null}
      <Button tone="primary" isDisabled={!ready || !target.answerable || !answer.trim() || pending || resolved} onPress={() => { void submit(); }}>{pending ? 'Sending answer…' : 'Resolve ask'}</Button>
      {target.id && agents[target.id] ? <Button onPress={() => send({ cmd: 'focus_agent', id: target.id })}>Focus reply target</Button> : null}
    </>}
    <Button isDisabled={pending} onPress={() => setRefresh((value) => value + 1)}>Refresh question</Button>
  </section>;
}
