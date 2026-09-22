import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectCatalogState, selectTasksState } from '../../app/store';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { TaskCreateDialog } from '../board/TaskCreateDialog';
import { text } from './model';

function initiativeTaskPrefill(initiative: UnknownRecord) {
  const title = text(initiative.title, text(initiative.id)).trim();
  const sections = [`Source initiative: ${text(initiative.id)} — ${title}`];
  for (const [key, label] of Object.entries({ summary: 'Summary', why: 'Why', in_scope: 'In scope', done_definition: 'Done definition' })) {
    const body = text(initiative[key]).trim();
    if (body) sections.push(`${label}\n${body}`);
  }
  return { title, description: sections.join('\n\n') };
}

/** Keep acknowledged creation separate from linking so recovery cannot recreate it. */
export function InitiativeTaskCreator({ initiative, disabled, onLinked }: {
  initiative: UnknownRecord; disabled: boolean; onLinked: () => void;
}) {
  const dispatch = useAppDispatch();
  const catalog = useAppSelector(selectCatalogState);
  const { lanes } = useAppSelector(selectTasksState);
  const [prefill, setPrefill] = useState<ReturnType<typeof initiativeTaskPrefill> | null>(null);
  const [createdTaskId, setCreatedTaskId] = useState('');
  const [catalogError, setCatalogError] = useState('');
  const [catalogRevision, setCatalogRevision] = useState(0);
  const group = text(initiative.group, text(initiative.group_name));
  const open = Boolean(prefill);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void Promise.all(['list_actions', 'list_roles'].map(async (cmd) => {
      const frame = await readCommand({ cmd, group }, controller.signal);
      if (frame.type === 'error') throw new Error(text(frame.message, 'Could not load task options.'));
      dispatch(projectionActions.auxiliaryResourceReceived(frame));
    })).then(() => setCatalogError('')).catch((cause: unknown) => {
      if (!controller.signal.aborted) setCatalogError(cause instanceof Error ? cause.message : 'Could not load task options.');
    });
    return () => controller.abort();
  }, [open, group, dispatch, catalogRevision]);
  const link = async (taskId: string) => {
    const frame = await readCommand({ cmd: 'initiative_link_task', id: text(initiative.id), task_id: taskId }, new AbortController().signal);
    if (frame.type === 'error') throw new Error(text(frame.message, 'Could not link task.'));
    dispatch(projectionActions.auxiliaryResourceReceived(frame));
    setCreatedTaskId(''); onLinked();
  };
  return <>
    {createdTaskId ? <p role="status">Task {createdTaskId} exists but has not been linked. Resume to retry the link, or link it later from the Board task selector.</p> : null}
    <Button isDisabled={disabled} onPress={() => setPrefill(initiativeTaskPrefill(initiative))}>{createdTaskId ? 'Resume task link' : 'Create Board task'}</Button>
    {prefill ? <TaskCreateDialog group={group} lanes={lanes.filter((lane): lane is string => typeof lane === 'string' && lane !== 'Archived')} actions={catalog.actions} roles={catalog.roles} initialValues={prefill} notice={catalogError ? <p role="alert">{catalogError} <Button onPress={() => setCatalogRevision((value) => value + 1)}>Retry task options</Button></p> : null} createdTaskId={createdTaskId} onCreated={setCreatedTaskId} afterCreate={link} onClose={() => setPrefill(null)} /> : null}
  </>;
}
