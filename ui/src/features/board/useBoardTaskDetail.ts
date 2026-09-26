import { useEffect, useState } from 'react';
import { useAppDispatch } from '../../app/hooks';
import { projectionActions } from '../../app/store';
import type { UnknownRecord } from '../../protocol';
import { boardReadRequest } from './boardReadRequest';

export function useBoardTaskDetail(id: string | null, group: string, connected: boolean, reconnect: number) {
  const dispatch = useAppDispatch(); const [retry, setRetry] = useState(0);
  const [outcome, setOutcome] = useState({ key: '', error: '' });
  const key = JSON.stringify([id, group, connected, reconnect, retry]);
  useEffect(() => {
    if (!id || !connected) return;
    const controller = new AbortController();
    void boardReadRequest({ cmd: 'task_detail', id }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type === 'error') throw new Error(typeof frame.message === 'string' ? frame.message : 'Could not load task details.');
      const task = frame.task as UnknownRecord | undefined;
      if (frame.type !== 'task_detail' || frame.id !== id || !task || Array.isArray(task) || task.id !== id || task.group !== group || typeof task.description !== 'string') throw new Error('Task details did not match the requested task and group.');
      dispatch(projectionActions.taskDetailReceived(frame)); setOutcome({ key, error: '' });
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setOutcome({ key, error: cause instanceof Error ? cause.message : 'Could not load task details.' });
    });
    return () => controller.abort();
  }, [id, group, connected, key, dispatch]);
  return { error: outcome.key === key ? outcome.error : '', retry: () => setRetry((value) => value + 1) };
}
