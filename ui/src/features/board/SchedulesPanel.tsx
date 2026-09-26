import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectConnection, selectGroupsState, selectTasksState } from '../../app/store';
import { Button, ModalDialog, StateSurface } from '../../design/primitives';
import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { CommandResponseError } from '../../protocol/http';
import { ActionVariableFields } from './ActionVariableFields';
import { actionVariableDefinitions, resolveActionVariables, useActionVariables } from './actionVariables';
import { boardReadRequest, boardWriteRequest } from './boardReadRequest';
import { localSchedule } from './taskEditModel';
import { catalogItems, useBoardAuthoringCatalog } from './useBoardAuthoringCatalog';
import styles from './BoardPanel.module.css';

const text = (value: unknown) => typeof value === 'string' ? value : '';
const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
const schedulePresets = [['Every 30m', '*/30 * * * *'], ['Hourly', '0 * * * *'], ['Daily 9am', '0 9 * * *'], ['Weekdays', '0 9 * * 1-5'], ['Weekly', '0 9 * * 1'], ['Monthly', '0 9 1 * *']] as const;
function time(value: unknown, timezone: unknown) {
  const raw = text(value); const date = new Date(raw);
  if (!raw || !Number.isFinite(date.getTime())) return raw || 'Not scheduled';
  try { return date.toLocaleString(undefined, { timeZone: text(timezone) || 'UTC', timeZoneName: 'short' }); }
  catch { return raw; }
}
function validate(command: TorqueCommand, frame: AuxiliaryFrame) {
  if (frame.type === 'error') throw new Error(text(frame.message) || 'Schedule request failed.');
  if (command.cmd === 'schedule_create' && frame.type === 'ok' && text(frame.schedule_id)) return;
  if (command.cmd === 'schedule_run' && frame.type === 'ok' && text(frame.task_id)) return;
  if (frame.type === 'state' && typeof frame.seq === 'number' && Number.isFinite(frame.seq) && frame.schedules && typeof frame.schedules === 'object') {
    const row = record(record(frame.schedules)[String(command.id)]);
    if (command.cmd === 'schedule_remove' ? !Object.keys(row).length : row.id === command.id) return;
  }
  throw new Error('Schedule acknowledgement did not match the request; its outcome is unknown. Review the schedule before retrying.');
}

function ScheduleEditor({ initial, group, groups, onSaved, onLockChange }: { initial: UnknownRecord; group: string; groups: string[]; onSaved: () => void; onLockChange: (locked: boolean) => void }) {
  const id = text(initial.id);
  const [name, setName] = useState(text(initial.name)); const [template, setTemplate] = useState(text(initial.task_template));
  const [description, setDescription] = useState(text(initial.description)); const [targetGroup, setTargetGroup] = useState(text(initial.group) || group);
  const [action, setAction] = useState(text(initial.action_name)); const [role, setRole] = useState(text(initial.agent_template));
  const [labels, setLabels] = useState(Array.isArray(initial.labels) ? initial.labels.join(', ') : '');
  const [mode, setMode] = useState(initial.scheduled_at && !initial.cron_expr ? 'once' : 'recurring');
  const [cron, setCron] = useState(text(initial.cron_expr)); const [at, setAt] = useState(localSchedule(text(initial.scheduled_at)));
  const [timezone, setTimezone] = useState(typeof initial.timezone === 'string' ? initial.timezone : Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [variables, setVariables] = useActionVariables(action, record(initial.action_vars));
  const options = useBoardAuthoringCatalog(targetGroup); const definitions = actionVariableDefinitions(options.actions, action);
  const [error, setError] = useState(''); const [pending, setPending] = useState(false); const [unknownCreation, setUnknownCreation] = useState(false);
  useEffect(() => { onLockChange(pending || unknownCreation); return () => onLockChange(false); }, [pending, unknownCreation, onLockChange]);
  const submitted = useRef<TorqueCommand | null>(null); const owner = useRef<AbortController | null>(null);
  useEffect(() => () => { owner.current?.abort(); owner.current = null; }, []);
  const submit = (event: FormEvent) => {
    event.preventDefault(); if (owner.current) return;
    let command: TorqueCommand;
    try {
      const parsed = resolveActionVariables(variables, definitions);
      if (!name.trim() || !targetGroup || (mode === 'recurring' ? !cron.trim() : !at || !Number.isFinite(new Date(at).getTime()))) throw new Error('Enter a name, group and valid schedule trigger.');
      command = submitted.current ?? { cmd: id ? 'schedule_update' : 'schedule_create', ...(id ? { id } : { idempotency_key: `schedule-create:${crypto.randomUUID()}` }), name: name.trim(), task_template: template.trim(), description, group: targetGroup, action_name: action, agent_template: role, action_vars: parsed, labels: labels.split(',').map((value) => value.trim()).filter(Boolean), cron_expr: mode === 'recurring' ? cron.trim() : '', scheduled_at: mode === 'once' ? at === localSchedule(text(initial.scheduled_at)) && initial.scheduled_at ? initial.scheduled_at : new Date(at).toISOString() : '', timezone: timezone.trim() };
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Invalid schedule.'); return; }
    if (!id) { submitted.current = command; setUnknownCreation(true); }
    const controller = new AbortController(); owner.current = controller; setPending(true); setError('');
    void boardWriteRequest(command, controller.signal).then((frame) => {
      if (controller.signal.aborted || owner.current !== controller) return;
      validate(command, frame); submitted.current = null; setUnknownCreation(false); onSaved();
    }).catch((cause: unknown) => {
      if (controller.signal.aborted || owner.current !== controller) return;
      // The schedule handler returns normal errors before schedule_add; exceptions are HTTP 500.
      if (cause instanceof CommandResponseError && cause.status < 500 && cause.status !== 409) { submitted.current = null; setUnknownCreation(false); }
      setError(cause instanceof Error ? cause.message : 'Schedule request failed.');
    }).finally(() => { if (owner.current === controller) { owner.current = null; setPending(false); } });
  };
  const actionNames = [...new Set(options.actions.map((item) => text(item.name)).filter(Boolean))];
  const roleNames = [...new Map(options.roles.map((item) => [text(item.slug) || text(item.name), text(item.name) || text(item.slug)])).entries()].filter(([slug]) => slug);
  return <form className={styles.scheduleForm} onSubmit={submit} aria-label={id ? 'Edit schedule' : 'New schedule'}>
    <h3>{id ? 'Edit schedule' : 'New schedule'}</h3>
    {options.loading ? <p role="status">Loading schedule options…</p> : null}
    {options.error ? <p role="alert">{options.error} <Button onPress={options.retry}>Retry schedule options</Button></p> : null}
    <fieldset className={styles.createFields} disabled={pending || unknownCreation}>
      <label>Name<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
      <label>Task title<input value={template} onChange={(event) => setTemplate(event.target.value)} placeholder="Daily review {date}" /></label>
      <label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></label>
      <label>Group<select value={targetGroup} onChange={(event) => setTargetGroup(event.target.value)}>{[...new Set([...groups, targetGroup])].map((value) => <option key={value}>{value}</option>)}</select></label>
      <div role="group" aria-label="Schedule type" className={styles.taskActionRow}><Button aria-pressed={mode === 'recurring'} onPress={() => setMode('recurring')}>Recurring</Button><Button aria-pressed={mode === 'once'} onPress={() => setMode('once')}>One-time</Button></div>
      {mode === 'recurring' ? <><label>Cron<input value={cron} onChange={(event) => setCron(event.target.value)} placeholder="0 9 * * 1-5" required /></label><div className={styles.taskActionRow} aria-label="Cron presets">{schedulePresets.map(([label, value]) => <Button key={label} onPress={() => setCron(value)}>{label}</Button>)}</div></> : <><label>Date &amp; time<input type="datetime-local" value={at} onChange={(event) => setAt(event.target.value)} required /></label><p>One-time dates use your local timezone: {Intl.DateTimeFormat().resolvedOptions().timeZone}.</p></>}
      <div className={styles.formGrid}>
        <label>Timezone<input value={timezone} onChange={(event) => setTimezone(event.target.value)} placeholder="UTC" /></label>
        <label>Labels<input value={labels} onChange={(event) => setLabels(event.target.value)} /></label>
        <label>Action<select value={action} onChange={(event) => setAction(event.target.value)}><option value="">Group default</option>{action && !actionNames.includes(action) ? <option value={action}>{action} (unavailable)</option> : null}{actionNames.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Worker role<select value={role} onChange={(event) => setRole(event.target.value)}><option value="">Action/default role</option>{role && !roleNames.some(([slug]) => slug === role) ? <option value={role}>{role} (unavailable)</option> : null}{roleNames.map(([slug, label]) => <option key={slug} value={slug}>{label}</option>)}</select></label>
      </div>
      <ActionVariableFields definitions={definitions} value={variables} onChange={setVariables} />
    </fieldset>
    {unknownCreation && !pending ? <p role="status">Creation has not been confirmed. Retry submits the original schedule and cannot change its reviewed fields.</p> : null}
    {error ? <p role="alert" className={styles.formError}>{error}</p> : null}
    <Button tone="primary" type="submit" isDisabled={pending}>{pending ? 'Saving schedule…' : id ? 'Save schedule' : unknownCreation ? 'Retry schedule creation' : 'Create schedule'}</Button>
  </form>;
}

export function SchedulesPanel({ group, onClose, onLockChange }: { group: string; onClose: () => void; onLockChange?: (locked: boolean) => void }) {
  const dispatch = useAppDispatch(); const connection = useAppSelector(selectConnection); const { schedules } = useAppSelector(selectTasksState);
  const { records: groupRecords } = useAppSelector(selectGroupsState); const groups = Object.keys(groupRecords).sort((a, b) => a.localeCompare(b));
  const [editorLocked, setEditorLocked] = useState(false);
  const [filter, setFilter] = useState(''); const [editor, setEditor] = useState<{ key: number; initial: UnknownRecord }>({ key: 0, initial: {} });
  const [revision, setRevision] = useState(0); const [readError, setReadError] = useState('');
  const [target, setTarget] = useState<UnknownRecord | null>(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  useEffect(() => { onLockChange?.(editorLocked || busy); return () => onLockChange?.(false); }, [editorLocked, busy, onLockChange]);
  const owner = useRef<AbortController | null>(null);
  useEffect(() => () => { owner.current?.abort(); owner.current = null; }, []);
  useEffect(() => {
    if (connection.status !== 'connected') return;
    const controller = new AbortController();
    void boardReadRequest({ cmd: 'schedule_list' }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'schedule_list' || !Array.isArray(frame.schedules) || frame.schedules.some((value) => !text(record(value).id))) throw new Error('Schedules returned an invalid list.');
      dispatch(projectionActions.auxiliaryResourceReceived(frame)); setReadError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setReadError(cause instanceof Error ? cause.message : 'Could not load schedules.'); });
    return () => controller.abort();
  }, [connection.status, connection.reconnectCount, revision, dispatch]);
  const refresh = () => setRevision((value) => value + 1);
  const mutate = (command: TorqueCommand) => {
    if (owner.current) return;
    const controller = new AbortController(); owner.current = controller; setBusy(true); setError('');
    void boardWriteRequest(command, controller.signal).then((frame) => {
      if (controller.signal.aborted || owner.current !== controller) return;
      validate(command, frame); if (command.cmd === 'schedule_remove') setTarget(null); refresh();
    }).catch((cause: unknown) => { if (!controller.signal.aborted && owner.current === controller) setError(cause instanceof Error ? cause.message : 'Schedule request failed.'); })
      .finally(() => { if (owner.current === controller) { owner.current = null; setBusy(false); } });
  };
  const rows = catalogItems(schedules).filter((row) => !filter || row.group === filter).sort((a, b) => text(a.name).localeCompare(text(b.name)));
  return <div className={styles.schedulesPanel}>
    <ScheduleEditor key={editor.key} initial={editor.initial} group={group} groups={groups} onLockChange={setEditorLocked} onSaved={() => { refresh(); setEditor((value) => ({ key: value.key + 1, initial: {} })); }} />
    {editor.initial.id ? <Button isDisabled={editorLocked || busy} onPress={() => setEditor((value) => ({ key: value.key + 1, initial: {} }))}>Cancel edit</Button> : null}
    {readError ? <p role="alert">{readError} <Button onPress={refresh}>Retry schedules</Button></p> : null}
    {error && !target ? <p role="alert">{error}</p> : null}
    <label>Show schedules<select value={filter} onChange={(event) => setFilter(event.target.value)}><option value="">All groups</option>{groups.map((value) => <option key={value}>{value}</option>)}</select></label>
    <section className={styles.scheduleList} aria-label="Existing schedules">
      {rows.length ? rows.map((row) => <article key={text(row.id)} aria-label={text(row.name)}>
        <div><strong>{text(row.name) || 'Untitled'}</strong><span>{text(row.slug)} · {text(row.group)} · {row.enabled === false ? 'Disabled' : 'Enabled'}</span>
          <span>{text(row.task_template) || text(row.name)}</span><span>Action: {text(row.action_name) || 'Group default'}{row.agent_template ? ` · Role: ${text(row.agent_template)}` : ''}</span>
          <span>{row.cron_expr ? `Recurring · ${text(row.cron_expr)}` : `One-time · ${time(row.scheduled_at, row.timezone)}`} · Timezone: {text(row.timezone) || 'UTC'}</span>
          <span>Next: {row.enabled === false ? 'Disabled' : time(row.next_run_at, row.timezone)} · {Number(row.run_count) || 0} {Number(row.run_count) === 1 ? 'run' : 'runs'}</span>
        </div>
        <div><Button isDisabled={editorLocked || busy} onPress={() => setEditor((value) => ({ key: value.key + 1, initial: { ...row } }))}>Edit</Button><Button isDisabled={editorLocked || busy} onPress={() => mutate({ cmd: 'schedule_run', id: row.id })}>Run now</Button><Button isDisabled={editorLocked || busy} onPress={() => mutate({ cmd: row.enabled === false ? 'schedule_enable' : 'schedule_disable', id: row.id })}>{row.enabled === false ? 'Enable' : 'Disable'}</Button><Button tone="danger" isDisabled={editorLocked || busy} onPress={() => { setError(''); setTarget({ ...row }); }}>Remove</Button></div>
      </article>) : <StateSurface title="No schedules" description={filter ? 'No schedules in this group.' : 'Create a recurring or one-time dispatch.'} />}
    </section>
    <footer className={styles.detailFooter}><span /><Button isDisabled={editorLocked || busy} onPress={onClose}>Close</Button></footer>
    <ModalDialog title="Remove schedule" isOpen={Boolean(target)} onOpenChange={(open) => { if (!open && !busy) setTarget(null); }}>
      <p>Remove “{text(target?.name)}” from {text(target?.group)}?</p>{error ? <p role="alert">{error}</p> : null}
      <Button isDisabled={busy} onPress={() => setTarget(null)}>Cancel removal</Button><Button tone="danger" isDisabled={busy} onPress={() => { if (target) mutate({ cmd: 'schedule_remove', id: target.id }); }}>Confirm removal</Button>
    </ModalDialog>
  </div>;
}
