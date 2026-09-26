import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectCatalogState, selectTasksState } from '../../app/store';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { planningRequest, validatePlanningAcknowledgement } from './planningRequests';
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
  const id = text(initiative.id);
  const linkOwner = useRef<AbortController | null>(null);
  useEffect(() => () => { linkOwner.current?.abort(); linkOwner.current = null; }, [open, id]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void Promise.all(['list_actions', 'list_roles'].map(async (cmd) => {
      const frame = await planningRequest({ cmd, group }, controller.signal);
      if (controller.signal.aborted) return;
      if (frame.type === 'error') throw new Error(text(frame.message, 'Could not load task options.'));
      const key = cmd === 'list_actions' ? 'actions' : 'roles';
      if (frame.type !== key || !frame[key] || typeof frame[key] !== 'object' || (frame.group !== undefined && frame.group !== group)) throw new Error('Task options did not match the requested catalog or group.');
      dispatch(projectionActions.auxiliaryResourceReceived(frame));
    })).then(() => { if (!controller.signal.aborted) setCatalogError(''); }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setCatalogError(cause instanceof Error ? cause.message : 'Could not load task options.');
    });
    return () => controller.abort();
  }, [open, group, dispatch, catalogRevision]);
  const link = async (taskId: string) => {
    const controller = new AbortController(); linkOwner.current?.abort(); linkOwner.current = controller;
    try {
      const command = { cmd: 'initiative_link_task', id, task_id: taskId };
      const frame = await planningRequest(command, controller.signal, true);
      if (controller.signal.aborted || linkOwner.current !== controller) throw new DOMException('Task link editor closed', 'AbortError');
      validatePlanningAcknowledgement(command, frame);
      dispatch(projectionActions.auxiliaryResourceReceived(frame));
      setCreatedTaskId(''); onLinked();
    } finally { if (linkOwner.current === controller) linkOwner.current = null; }
  };
  return <>
    {createdTaskId ? <p role="status">Task {createdTaskId} exists, but its link has not been confirmed. Resume to retry the link, or inspect it later from the Board task selector.</p> : null}
    <Button isDisabled={disabled} onPress={() => setPrefill(initiativeTaskPrefill(initiative))}>{createdTaskId ? 'Resume task link' : 'Create Board task'}</Button>
    {prefill ? <TaskCreateDialog group={group} lanes={lanes.filter((lane): lane is string => typeof lane === 'string' && lane !== 'Archived')} actions={catalog.actions} roles={catalog.roles} initialValues={prefill} notice={catalogError ? <p role="alert">{catalogError} <Button onPress={() => setCatalogRevision((value) => value + 1)}>Retry task options</Button></p> : null} createdTaskId={createdTaskId} onCreated={setCreatedTaskId} afterCreate={link} onClose={() => setPrefill(null)} /> : null}
  </>;
}
