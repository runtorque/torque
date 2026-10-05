import { useBoardAuthoringCatalog } from '../board/useBoardAuthoringCatalog';
import { BoardCatalogNotice } from '../board/BoardCatalogNotice';
import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectTasksState } from '../../app/store';
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
  const { lanes } = useAppSelector(selectTasksState);
  const [prefill, setPrefill] = useState<ReturnType<typeof initiativeTaskPrefill> | null>(null);
  const [createdTaskId, setCreatedTaskId] = useState('');
  const group = text(initiative.group, text(initiative.group_name));
  const open = Boolean(prefill);
  const catalog = useBoardAuthoringCatalog(group, open);
  const id = text(initiative.id);
  const linkOwner = useRef<AbortController | null>(null);
  useEffect(() => () => { linkOwner.current?.abort(); linkOwner.current = null; }, [open, id]);
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
    {prefill ? <TaskCreateDialog key={`${id}:${group}`} group={group} lanes={lanes.filter((lane): lane is string => typeof lane === 'string' && lane !== 'Archived')} actions={catalog.actions} roles={catalog.roles} initialValues={prefill} notice={<BoardCatalogNotice catalog={catalog} />} createdTaskId={createdTaskId} onCreated={setCreatedTaskId} afterCreate={link} onClose={() => setPrefill(null)} /> : null}
  </>;
}
