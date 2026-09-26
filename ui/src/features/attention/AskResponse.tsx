import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectAgentsState, selectConnection, selectTasksState } from '../../app/store';
import { Button } from '../../design/primitives';
import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { CommandResponseError } from '../../protocol/http';
import { settingsRequest } from '../control/settingsRequests';
import { BehaviorReview } from './BehaviorReview';
import { askTarget, isOpenAsk, labels, proposalId, record, text } from './model';
import styles from './Attention.module.css';

interface Props { taskId: string; send: (command: TorqueCommand) => void }
export function AskResponse(props: Props) {
  const open = useAppSelector((state) => isOpenAsk(record(selectTasksState(state).records[props.taskId])));
  return open ? <QuestionResponse key={props.taskId} {...props} /> : null;
}
function questionDetail(frame: AuxiliaryFrame, id: string, label: string) {
  const task = record(frame.task);
  if (frame.type !== 'task_detail' || frame.id !== id || (task.id !== undefined && task.id !== id) || typeof task.description !== 'string') throw new Error(`${label} detail was not returned or did not match the requested task.`);
  return task;
}
function QuestionResponse({ taskId, send }: Props) {
  const dispatch = useAppDispatch();
  const tasks = useAppSelector(selectTasksState).records;
  const agents = useAppSelector(selectAgentsState).records;
  const connection = useAppSelector(selectConnection);
  const reconnect = connection.reconnectCount;
  const snapshotVersion = useAppSelector((state) => state.projection.snapshotVersion);
  const task = record(tasks[taskId]);
  const parentId = text(task.parent_task_id);
  const parent = record(tasks[parentId]);
  const [hydrated, setHydrated] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [readResult, setReadResult] = useState({ key: '', error: '', version: 0 });
  const [accepted, setAccepted] = useState<{ question: UnknownRecord; parent: UnknownRecord; parentId: string }>({ question: {}, parent: {}, parentId: '' });
  const [answer, setAnswer] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [resolved, setResolved] = useState(false);
  const [review, setReview] = useState(false);
  const operation = useRef<AbortController | null>(null);
  const readVersion = useRef(0);
  const [uncertain, setUncertain] = useState<{ command: TorqueCommand; targetId: string; afterRead: number } | null>(null);
  useEffect(() => () => { operation.current?.abort(); operation.current = null; }, []);
  const loadKey = `${taskId}:${text(task.updated_at)}:${parentId}:${text(parent.updated_at)}:${reconnect}:${snapshotVersion}:${refresh}`;
  const ready = hydrated === loadKey && connection.status === 'connected';
  const loadError = readResult.key === loadKey ? readResult.error : '';
  useEffect(() => {
    if (connection.status !== 'connected') return;
    const controller = new AbortController(); const version = ++readVersion.current;
    const load = async () => {
      try {
        const frame = await settingsRequest({ cmd: 'task_detail', id: taskId }, controller.signal, false, 'Question');
        if (controller.signal.aborted) return;
        const question = questionDetail(frame, taskId, 'Question');
        if (!Array.isArray(question.labels) || typeof question.lane !== 'string') throw new Error('Question detail did not include its current lifecycle state.');
        setAccepted((previous) => ({ ...previous, question }));
        dispatch(projectionActions.taskDetailReceived(frame));
        if (!isOpenAsk(question)) return;
        const parent = text(question.parent_task_id);
        if (parent) {
          const detail = await settingsRequest({ cmd: 'task_detail', id: parent }, controller.signal, false, 'Parent question');
          if (controller.signal.aborted) return;
          const context = questionDetail(detail, parent, 'Parent task');
          setAccepted((previous) => ({ ...previous, parent: context, parentId: parent }));
          dispatch(projectionActions.taskDetailReceived(detail));
        }
        setHydrated(loadKey); setReadResult({ key: loadKey, error: '', version });
      } catch (cause) { if (!controller.signal.aborted) setReadResult({ key: loadKey, error: cause instanceof Error ? cause.message : 'Could not load the full question.', version }); }
    };
    void load();
    return () => controller.abort();
  }, [dispatch, taskId, loadKey, connection.status]);
  const target = askTarget(task, tasks, agents);
  const approval = labels(task).includes('behavior-overlay-approval');
  const proposal = proposalId(task);
  const deliveryReviewed = !uncertain || (ready && readResult.version > uncertain.afterRead && target.id === uncertain.targetId);
  const submit = async () => {
    if (operation.current || !ready || !deliveryReviewed || approval || !target.answerable || !isOpenAsk(task) || resolved || !answer.trim()) return;
    const controller = new AbortController(); operation.current = controller; setPending(true); setError('');
    const command = uncertain?.command ?? { cmd: 'resolve_ask', id: taskId, answer: answer.trim(), request_id: `react-ask-${crypto.randomUUID()}` };
    try {
      const frame = await settingsRequest(command, controller.signal, true, 'Answer delivery');
      if (controller.signal.aborted || operation.current !== controller) return;
      if (frame.type !== 'ok' || frame.command !== 'resolve_ask' || frame.request_id !== command.request_id || (frame.task_id !== undefined && frame.task_id !== taskId) || (frame.agent_id !== undefined && frame.agent_id !== target.id) || (frame.architect_id !== undefined && frame.architect_id !== target.id) || (frame.delivery_state !== undefined && frame.delivery_state !== 'delivered')) throw new Error('Could not confirm delivery; its outcome is unknown. Refresh the question before retrying.');
      setAnswer(''); setResolved(true); setUncertain(null);
    } catch (cause) {
      if (controller.signal.aborted || operation.current !== controller) return;
      const unknown = !(cause instanceof CommandResponseError) || Boolean(uncertain);
      if (unknown) setUncertain({ command, targetId: uncertain?.targetId ?? target.id, afterRead: readVersion.current });
      const message = cause instanceof Error ? cause.message : 'Could not confirm delivery. Your answer is retained.';
      setError(unknown && !message.includes('outcome is unknown') ? `${message} The delivery outcome is unknown. Refresh the question before retrying.` : message);
    } finally { if (operation.current === controller) { operation.current = null; setPending(false); } }
  };
  if (!isOpenAsk(task)) return null;
  return <section className={styles.ask} aria-label={`Response to ${text(task.task)}`} aria-busy={pending}>
    <h3>{text(task.task) || 'Question'}</h3>
    <p>{text(typeof task.description === 'string' ? task.description : accepted.question.description)}</p>
    {parentId ? <details><summary>Parent: {text(parent.task) || parentId}</summary><p>{text(typeof parent.description === 'string' ? parent.description : accepted.parentId === parentId ? accepted.parent.description : '')}</p></details> : null}
    {loadError ? <p role="alert">{loadError}</p> : !ready ? <p role="status">{connection.status === 'connected' ? 'Loading full question…' : 'Reconnect to refresh this question.'}</p> : null}
    {approval ? <><p>Review the proposed behavior changes before deciding.</p><Button isDisabled={!ready || !proposal} onPress={() => setReview(true)}>Review behavior diff</Button>{ready && !proposal ? <p role="alert">This approval has no proposal reference.</p> : null}{review ? <BehaviorReview key={proposal} proposalId={proposal} onClose={() => setReview(false)} /> : null}</> : <>
      <p>Reply to {text(target.agent.name) || target.id || 'unknown agent'}</p>
      {!target.answerable ? <p>{target.reason} This question remains available when the agent resumes.</p> : null}
      <label>Answer {text(task.task)}<textarea value={answer} readOnly={Boolean(uncertain)} disabled={pending || resolved} onChange={(event) => setAnswer(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(); } }} /></label>
      {error ? <p role="alert">{error}</p> : null}
      {uncertain ? <p>{target.id !== uncertain.targetId ? 'The reply target changed. Inspect the original delivery before answering this question again.' : deliveryReviewed ? 'Question refreshed. Retry sends the same reviewed answer.' : 'Your submitted answer is retained. Refresh the question before retrying the same answer.'}</p> : null}{resolved ? <p role="status">Answer delivered.</p> : null}
      <Button tone="primary" isDisabled={!ready || !deliveryReviewed || !target.answerable || !answer.trim() || pending || resolved} onPress={() => { void submit(); }}>{pending ? 'Sending answer…' : 'Resolve ask'}</Button>
      {target.id && agents[target.id] ? <Button onPress={() => send({ cmd: 'focus_agent', id: target.id })}>Focus reply target</Button> : null}
    </>}
    <Button isDisabled={pending} onPress={() => setRefresh((value) => value + 1)}>Refresh question</Button>
  </section>;
}
