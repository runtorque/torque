import { useEffect, useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectCatalogState, selectTasksState } from '../../app/store';
import { Button, ModalDialog } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { CreateTaskDialog } from '../board/BoardPanel';
import { text } from './model';
import { usePlanningMutation } from './usePlanningMutation';

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
  const createdId = useRef('');
  const [catalogError, setCatalogError] = useState('');
  const [catalogRevision, setCatalogRevision] = useState(0);
  const mutation = usePlanningMutation();
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
  const create = (command: TorqueCommand) => { void mutation.run(async (request) => {
    if (!createdId.current) {
      const result = await request(command);
      const id = text(result.task_id);
      if (!id) throw new Error('Task creation returned no ID. Check the Board before retrying.');
      createdId.current = id; setCreatedTaskId(id);
    }
    await request({ cmd: 'initiative_link_task', id: text(initiative.id), task_id: createdId.current });
    createdId.current = ''; setCreatedTaskId(''); setPrefill(null); onLinked();
  }); };
  return <>
    {createdTaskId ? <p role="status">Task {createdTaskId} exists but has not been linked. Resume to retry the link, or link it later from the Board task selector.</p> : null}
    <Button isDisabled={disabled} onPress={() => setPrefill(initiativeTaskPrefill(initiative))}>{createdTaskId ? 'Resume task link' : 'Create Board task'}</Button>
    <ModalDialog title="Create Board task" description={`From Initiative ${text(initiative.id)} · ${group}`} size="large" isOpen={open} onOpenChange={(next) => { if (!next && !mutation.busy.current) setPrefill(null); }}>
      {catalogError ? <p role="alert">{catalogError} <Button onPress={() => setCatalogRevision((value) => value + 1)}>Retry task options</Button></p> : null}
      {prefill ? <CreateTaskDialog group={group} lanes={lanes.filter((lane): lane is string => typeof lane === 'string' && lane !== 'Archived')} actions={catalog.actions} roles={catalog.roles} initialValues={prefill} onCreate={create} pending={mutation.pending} createdTaskId={createdTaskId} requestError={mutation.error} onClose={() => setPrefill(null)} /> : null}
    </ModalDialog>
  </>;
}
