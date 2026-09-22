import { TaskActivity } from './TaskActivity';
import { TaskEvidenceEditor } from './TaskEvidenceEditor';
import { evidenceFilename, uploadedEvidence } from './taskEvidenceModel';
import { TaskPromptPreview } from './TaskPromptPreview';
import { localSchedule, taskEditChanges } from './taskEditModel';
import { readCommand } from '../../protocol/http';
import { ActionVariableFields } from './ActionVariableFields';
import { actionVariableDefinitions, resolveActionVariables, useActionVariables } from './actionVariables';
import { AskResponse } from '../attention/AskResponse';
import { TaskCreateDialog } from './TaskCreateDialog';
import { VerificationFields, type VerificationDraft } from './VerificationFields';
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
  type PointerSensorOptions,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from 'react';

import { useAppDispatch, useAppSelector } from '../../app/hooks';
import {
  projectionActions,
  selectAgentsState,
  selectAuxiliaryResponseState,
  selectBoardViewState,
  selectCatalogState,
  selectConnection,
  selectGroupsState,
  selectTasksState,
  selectWorkspaceUi,
  workspaceUiActions,
} from '../../app/store';
import type { TorqueCommand } from '../../protocol';
import { ActionMenu, ActionMenuItem, Button, ModalDialog, StateSurface } from '../../design/primitives';
import {
  boardCollisionDetection,
  boardCardTransform,
  dragLaneForTask,
  moveTaskBetweenDragLanes,
  taskDropPosition,
  type DragLaneOrders,
} from './drag';
import {
  displayTime,
  emptyBoardFilters,
  normalizeFilters,
  normalizeTasks,
  orderedLaneTasks,
  taskMatchesFilters,
  visibleHierarchy,
  type BoardFilterState,
  type BoardTask,
} from './model';
import styles from './BoardPanel.module.css';

export type CommandSender = (command: TorqueCommand) => boolean;

const INITIAL_LANE_RENDER_LIMIT = 20;
const LANE_RENDER_BATCH = 20;

interface BoardPanelProps {
  group: string;
  sendCommand: CommandSender;
  onCommandUnavailable: () => void;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function nameOf(value: unknown, fallback: string): string {
  const item = record(value);
  return typeof item.name === 'string' && item.name ? item.name : fallback;
}

function textValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}

function recordItems(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.map(record).filter((item) => Object.keys(item).length > 0);
  return Object.values(record(value)).map(record).filter((item) => Object.keys(item).length > 0);
}

function uniqueRecordItems(value: unknown, keyOf: (item: Record<string, unknown>) => string): Record<string, unknown>[] {
  const unique = new Map<string, Record<string, unknown>>();
  recordItems(value).forEach((item) => {
    const key = keyOf(item);
    if (key && !unique.has(key)) unique.set(key, item);
  });
  return [...unique.values()];
}

function actionItems(value: unknown): Record<string, unknown>[] {
  return uniqueRecordItems(value, (item) => textValue(item.name));
}

function roleItems(value: unknown): Record<string, unknown>[] {
  return uniqueRecordItems(value, (item) => textValue(item.slug, textValue(item.name)));
}

function parseJsonObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value || '{}');
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Expected a JSON object');
  return parsed as Record<string, unknown>;
}

function sendOrNotify(sendCommand: CommandSender, command: TorqueCommand, unavailable: () => void) {
  if (!sendCommand(command)) unavailable();
}

class BoardPointerSensor extends PointerSensor {
  static activators = [{
    eventName: 'onPointerDown' as const,
    handler: ({ nativeEvent: event }: ReactPointerEvent, options: PointerSensorOptions) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!event.isPrimary || event.button !== 0 || target?.closest('button, a, input, textarea, select, [contenteditable="true"], [data-board-text]')) return false;
      options.onActivation?.({ event });
      return true;
    },
  }];
}

function LaneDropZone({ lane, header, children }: { lane: string; header: ReactNode; children: (scrollRootRef: RefObject<HTMLDivElement | null>) => ReactNode }) {
  const { isOver, setNodeRef } = useDroppable({ id: `lane:${lane}` });
  const scrollRootRef = useRef<HTMLDivElement>(null);
  return (
    <section ref={setNodeRef} className={`${styles.lane} ${isOver ? styles.laneOver : ''}`} id={`lane-${lane}`}>
      {header}
      <div ref={scrollRootRef} className={styles.laneBody} data-lane-id={`lane:${lane}`}>{children(scrollRootRef)}</div>
    </section>
  );
}

function LaneRenderSentinel({
  lane,
  remaining,
  scrollRootRef,
  onReveal,
}: {
  lane: string;
  remaining: number;
  scrollRootRef: RefObject<HTMLElement | null>;
  onReveal: () => void;
}) {
  const sentinelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel || remaining <= 0 || typeof IntersectionObserver === 'undefined') return;
    let revealed = false;
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry?.isIntersecting || revealed) return;
      revealed = true;
      onReveal();
    }, { root: scrollRootRef.current, rootMargin: '360px 0px' });
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [onReveal, remaining, scrollRootRef]);

  if (remaining <= 0) return null;
  return (
    <div ref={sentinelRef} className={styles.laneRenderSentinel} aria-label={`${remaining} more tasks in ${lane}`}>
      {remaining} more tasks
    </div>
  );
}

interface SortableTaskCardProps {
  task: BoardTask;
  depth: number;
  childCount: number;
  density: string;
  selected: boolean;
  focused: boolean;
  collapsed: boolean;
  attribution: TaskAttribution;
  onSelect: (additive: boolean) => void;
  onOpen: () => void;
  onActivity: () => void;
  onCollapse: () => void;
  onDispatch: () => void;
  onDone: () => void;
  onSync: () => void;
  onOpenExternal: () => void;
  onDuplicate: () => void;
  onArchive: () => void;
  onVerify: () => void;
  onDetach: () => void;
  onRemove: () => void;
  onFocus: () => void;
  onOpenAgent: (agentId: string) => void;
}

interface TaskIdentity {
  id: string;
  name: string;
  kind: string;
}

interface TaskAttribution {
  creator: TaskIdentity;
  responsible: TaskIdentity;
  activeAgent: TaskIdentity | null;
}

function taskIdentity(agents: Record<string, unknown>, id: string, fallbackKind = ''): TaskIdentity {
  const agent = record(agents[id]);
  const kind = textValue(agent.kind, fallbackKind);
  return {
    id,
    name: nameOf(agent, id),
    kind: kind ? `${kind[0]?.toUpperCase() ?? ''}${kind.slice(1)}` : '',
  };
}

function taskAttribution(task: BoardTask, agents: Record<string, unknown>): TaskAttribution {
  const creator = task.createdByArchitectId
    ? taskIdentity(agents, task.createdByArchitectId, 'architect')
    : task.createdByEngineerId
      ? taskIdentity(agents, task.createdByEngineerId, 'engineer')
      : { id: '', name: 'You', kind: 'User' };
  const responsibleId = task.assignedEngineerId || task.assignedArchitectId || task.agentId;
  const responsibleKind = task.assignedEngineerId ? 'engineer' : task.assignedArchitectId ? 'architect' : '';
  const responsible = responsibleId
    ? taskIdentity(agents, responsibleId, responsibleKind)
    : { id: '', name: 'Unassigned', kind: '' };
  const activeAgent = task.agentId && task.agentId !== responsibleId
    ? taskIdentity(agents, task.agentId)
    : null;
  return { creator, responsible, activeAgent };
}

function identityDescription(identity: TaskIdentity): string {
  return identity.kind ? `${identity.name}, ${identity.kind}` : identity.name;
}

function SortableTaskCard({
  task,
  depth,
  childCount,
  density,
  selected,
  focused,
  collapsed,
  attribution,
  onSelect,
  onOpen,
  onActivity,
  onCollapse,
  onDispatch,
  onDone,
  onSync,
  onOpenExternal,
  onDuplicate,
  onArchive,
  onVerify,
  onDetach,
  onRemove,
  onFocus,
  onOpenAgent,
}: SortableTaskCardProps) {
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: task.id });
  const cardStyle = {
    transform: boardCardTransform(transform),
    transition: isDragging ? 'none' : transition,
    willChange: isDragging ? 'transform' : undefined,
    '--task-depth': depth,
  } as CSSProperties;
  const dispatchLabel = task.dispatchState === 'live' || task.agentId ? 'Dispatched' : 'Dispatch';

  return (
    <article
      ref={setNodeRef}
      style={cardStyle}
      className={`${styles.taskCard} ${styles[`density_${density}`] ?? ''} ${selected ? styles.taskSelected : ''} ${focused ? styles.taskFocused : ''} ${isDragging ? styles.taskDragging : ''}`}
      data-task-id={task.id}
      {...listeners}
      tabIndex={focused ? 0 : -1}
      onFocus={onFocus}
      onClick={(event) => onSelect(event.metaKey || event.ctrlKey || event.shiftKey)}
      onDoubleClick={onOpen}
      aria-label={`${task.task}, ${task.lane}`}
    >
      <div className={styles.cardTopline}>
        {childCount ? (
          <button className={styles.collapseButton} aria-label={collapsed ? 'Expand child tasks' : 'Collapse child tasks'} onClick={(event) => { event.stopPropagation(); onCollapse(); }}>
            {collapsed ? '▸' : '▾'} <span>{childCount}</span>
          </button>
        ) : null}
        <span className={styles.taskId} title={task.id} data-board-text>{task.id}</span>
        <span className={`${styles.health} ${styles[`health_${task.healthState}`] ?? ''}`} title={`Health: ${task.healthState}`} />
        <ActionMenu label={`Actions for ${task.task}`}>
          <ActionMenuItem onAction={onOpen}>Open details</ActionMenuItem>
          <ActionMenuItem onAction={onActivity}>Task activity</ActionMenuItem>
          <ActionMenuItem onAction={onDuplicate}>Duplicate</ActionMenuItem>
          <ActionMenuItem onAction={onDispatch} isDisabled={dispatchLabel === 'Dispatched' || task.lane === 'Archived'}>{dispatchLabel}</ActionMenuItem>
          <ActionMenuItem onAction={onDone} isDisabled={task.lane === 'Done' || task.lane === 'Archived'}>Move to Done</ActionMenuItem>
          <ActionMenuItem onAction={onVerify}>Mark verified</ActionMenuItem>
          <ActionMenuItem onAction={onDetach} isDisabled={!task.parentTaskId}>Detach from pipeline</ActionMenuItem>
          <ActionMenuItem onAction={onSync} isDisabled={!task.externalId && !task.externalUrl}>Sync external ticket</ActionMenuItem>
          <ActionMenuItem onAction={onOpenExternal} isDisabled={!task.externalUrl}>Open external ticket</ActionMenuItem>
          <ActionMenuItem onAction={onArchive}>{task.lane === 'Archived' ? 'Restore from archive' : 'Archive'}</ActionMenuItem>
          <ActionMenuItem onAction={() => { void navigator.clipboard.writeText(task.id); }}>Copy ID</ActionMenuItem>
          <ActionMenuItem onAction={onRemove}>Remove…</ActionMenuItem>
        </ActionMenu>
      </div>
      <h3><span data-board-text>{task.task}</span></h3>
      {density === 'detailed' && task.description ? <p className={styles.description}><span data-board-text>{task.description}</span></p> : null}
      <div className={styles.ownership} aria-label="Task ownership">
        <span aria-label={`Created by ${identityDescription(attribution.creator)}`} title={`Created by ${identityDescription(attribution.creator)}`}>
          <small>Created by</small>
          {attribution.creator.id
            ? <button type="button" aria-label={`Open agent ${attribution.creator.name}`} onClick={(event) => { event.stopPropagation(); onOpenAgent(attribution.creator.id); }}>{attribution.creator.name}</button>
            : <strong>{attribution.creator.name}</strong>}
          {attribution.creator.kind ? <em>{attribution.creator.kind}</em> : null}
        </span>
        <span
          aria-label={`Executing ${identityDescription(attribution.responsible)}${attribution.activeAgent ? `, via ${identityDescription(attribution.activeAgent)}` : ''}`}
          title={`Executing ${identityDescription(attribution.responsible)}${attribution.activeAgent ? ` via ${identityDescription(attribution.activeAgent)}` : ''}`}
        >
          <small>Executing</small>
          {attribution.responsible.id
            ? <button type="button" aria-label={`Open agent ${attribution.responsible.name}`} onClick={(event) => { event.stopPropagation(); onOpenAgent(attribution.responsible.id); }}>{attribution.responsible.name}</button>
            : <strong>{attribution.responsible.name}</strong>}
          {attribution.responsible.kind ? <em>{attribution.responsible.kind}</em> : null}
          {attribution.activeAgent ? <i>via <button type="button" aria-label={`Open agent ${attribution.activeAgent.name}`} onClick={(event) => { event.stopPropagation(); onOpenAgent(attribution.activeAgent?.id ?? ''); }}>{attribution.activeAgent.name}</button></i> : null}
        </span>
      </div>
      <div className={styles.metadata}>
        {task.status ? <span className={styles.statusBadge} data-board-text>{task.status}</span> : null}
        {task.scheduledAt ? <span className={styles.due} data-board-text>◷ {displayTime(task.scheduledAt)}</span> : null}
        {task.dependsOn.length ? <span data-board-text>↳ {task.dependsOn.length} dep</span> : null}
        {task.artifacts.length ? <span data-board-text>◇ {task.artifacts.length}</span> : null}
        {task.messages.length ? <button type="button" aria-label={`View activity for ${task.task}`} onClick={(event) => { event.stopPropagation(); onActivity(); }}>Activity</button> : null}
        {task.externalId ? <span data-board-text>{task.provider || 'external'} · {task.externalId}</span> : null}
      </div>
      {density !== 'compact' && task.labels.length ? (
        <div className={styles.labels}>{task.labels.map((label) => <span key={label} title={label} data-board-text>{label}</span>)}</div>
      ) : null}
    </article>
  );
}

interface InlineCreateProps {
  lane: string;
  group: string;
  onClose: () => void;
  sendCommand: CommandSender;
  onCommandUnavailable: () => void;
}

function InlineCreate({ lane, group, onClose, sendCommand, onCommandUnavailable }: InlineCreateProps) {
  const [title, setTitle] = useState('');
  const [labels, setLabels] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) return;
    sendOrNotify(sendCommand, {
      cmd: 'board_add_task',
      task: trimmed,
      group,
      lane,
      labels: labels.split(',').map((label) => label.trim()).filter(Boolean),
    }, onCommandUnavailable);
    setTitle('');
    setLabels('');
    onClose();
  };

  return (
    <form className={styles.inlineCreate} onSubmit={submit} onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}>
      <input ref={inputRef} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Task title" aria-label={`New task in ${lane}`} />
      <input value={labels} onChange={(event) => setLabels(event.target.value)} placeholder="labels, comma separated" aria-label="Task labels" />
      <div><Button tone="primary" type="submit">Create</Button><Button tone="quiet" type="button" onPress={onClose}>Cancel</Button></div>
    </form>
  );
}

interface TaskDetailProps {
  task: BoardTask;
  tasks: BoardTask[];
  groups: Record<string, unknown>;
  agents: Record<string, unknown>;
  actions: unknown;
  roles: unknown;
  responses: Record<string, unknown>;
  sendCommand: CommandSender;
  onCommandUnavailable: () => void;
  onClose: () => void;
  onRemove: () => void;
  busyRef: { current: boolean };
  closeRef: { current: (() => void) | null };
  initialTab: 'execution' | 'activity';
}

function TaskDetail({ task, tasks, groups, agents, actions, roles, responses, sendCommand, onCommandUnavailable, onClose, onRemove, busyRef, closeRef, initialTab }: TaskDetailProps) {
  const [title, setTitle] = useState(task.task);
  const [description, setDescription] = useState(task.description);
  const [labels, setLabels] = useState(task.labels.join(', '));
  const [scheduledAt, setScheduledAt] = useState(() => localSchedule(task.scheduledAt));
  const [targetGroup, setTargetGroup] = useState(task.group);
  const [actionName, setActionName] = useState(task.actionName);
  const [role, setRole] = useState(task.agentTemplate);
  const [actionVars, setActionVars] = useActionVariables(actionName, record(task.raw.action_vars));
  const definitions = actionVariableDefinitions(actions, actionName);
  const [dependsOn, setDependsOn] = useState(task.dependsOn.join(', '));
  const [agentId, setAgentId] = useState(task.agentId);
  const [provider, setProvider] = useState(task.provider);
  const [externalId, setExternalId] = useState(task.externalId);
  const [externalUrl, setExternalUrl] = useState(task.externalUrl);
  const [syncBaseline] = useState(task.boardSync);
  const [syncEnabled, setSyncEnabled] = useState(task.boardSync.enabled !== false && (Boolean(task.boardSync.enabled) || Boolean(task.boardSync.provider)));
  const [verification, setVerification] = useState<VerificationDraft>({ mode: textValue(task.raw.verification_mode), state: task.verificationState, notes: textValue(task.raw.verification_notes), summary: record(task.raw.verification_summary) });
  const [attachments, setAttachments] = useState(task.attachments);
  const [removedAttachments, setRemovedAttachments] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [artifacts, setArtifacts] = useState(task.artifacts);
  const [artifactEditing, setArtifactEditing] = useState(false);
  const uncommittedUploads = useRef(new Set<string>());
  const [externalStatus, setExternalStatus] = useState(task.status || task.lane);
  const [externalComment, setExternalComment] = useState('');
  const [formError, setFormError] = useState('');
  const [detailTab, setDetailTab] = useState<'execution' | 'verification' | 'integration' | 'evidence' | 'activity'>(initialTab);
  const [activityVisited, setActivityVisited] = useState(initialTab === 'activity');
  const [pullRequested, setPullRequested] = useState(false);
  const [pullBaseline, setPullBaseline] = useState<unknown>(responses[`board_pull_preview:${task.id}`] ?? responses['board_pull_preview:latest']);
  const actionOptions = actionItems(actions);
  const roleOptions = roleItems(roles);
  const liveAgents = Object.entries(agents).map<Record<string, unknown> & { id: string }>(([id, value]) => ({ id, ...record(value) })).filter((item) => item.group === targetGroup && !Number(item.deleted_at ?? 0));
  const pullResponse = responses[`board_pull_preview:${task.id}`] ?? responses['board_pull_preview:latest'];
  const pullPreview = pullRequested && pullResponse !== pullBaseline ? record(pullResponse) : {};
  const pullChanges = record(pullPreview.changes ?? record(pullPreview.preview).changes);
  const attribution = taskAttribution(task, agents);
  const dispatch = useAppDispatch();
  const [saving, setSaving] = useState(false);
  const draftFields = () => ({
    task: title.trim(), group: targetGroup, description,
    labels: labels.split(',').map((label) => label.trim()).filter(Boolean),
    scheduled_at: scheduledAt, action_name: actionName, agent_template: role,
    action_vars: actionVars, depends_on: dependsOn.split(',').map((id) => id.trim()).filter(Boolean),
    agent_id: agentId, attachments, artifacts, provider, external_id: externalId, external_url: externalUrl,
    board_sync: { ...syncBaseline, version: Number(syncBaseline.version ?? 1), enabled: syncEnabled, provider: provider || textValue(syncBaseline.provider, 'github') },
    verification_mode: verification.mode, verification_state: verification.state,
    verification_notes: verification.notes, verification_summary: verification.summary,
  });
  const baseline = useRef(draftFields());
  const savedEvidence = useRef({ attachments: task.attachments, artifacts: task.artifacts });
  useEffect(() => () => { busyRef.current = false; }, [busyRef]);
  const request = async (command: TorqueCommand) => {
    const frame = await readCommand(command, new AbortController().signal);
    if (frame.type === 'error' || frame.type === 'finalization_blocked') throw new Error(textValue(frame.message, frame.type === 'finalization_blocked' ? 'Task changes are blocked by unfinished finalization gates.' : 'The request failed.'));
    // Mutation acknowledgements may contain a full snapshot. Live deltas remain
    // authoritative; a targeted detail refresh avoids replacing newer projections.
    if (frame.type !== 'state') dispatch(projectionActions.auxiliaryResourceReceived(frame));
    return frame;
  };


  const upload = async (files: File[]) => {
    if (!files.length || busyRef.current || artifactEditing) return;
    busyRef.current = true;
    setUploading(true);
    setFormError('');
    try {
      for (const file of files) {
        const body = new FormData();
        body.append('task_id', task.id);
        body.append('file', file);
        const response = await fetch('/api/upload', { method: 'POST', body });
        const payload = await response.json() as { ok?: boolean; error?: string; data?: unknown[] };
        if (!response.ok || !payload.ok) throw new Error(payload.error || 'Upload failed');
        for (const entry of (payload.data ?? []).map(record)) {
          if (entry.filename) uncommittedUploads.current.add(textValue(entry.filename));
          const prepared = await uploadedEvidence(entry, file);
          if (prepared.kind === 'attachment') setAttachments((current) => [...current, prepared.item]);
          else setArtifacts((current) => [...current, prepared.item]);
        }
      }
    } catch (error) { setFormError(error instanceof Error ? error.message : 'Upload failed'); }
    finally { busyRef.current = false; setUploading(false); }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (busyRef.current || artifactEditing || !title.trim()) return;
    busyRef.current = true; setSaving(true); setFormError('');
    void (async () => {
      let saved = false;
      try {
        const draft = draftFields();
        const fields = taskEditChanges(baseline.current, draft, definitions, task.raw);
        if (Object.keys(fields).length) {
          await request({ cmd: 'board_update_task', id: task.id, ...fields, enforce_dispatch_edit_gate: true });
          baseline.current = draft;
          for (const key of ['attachments', 'artifacts'] as const) {
            if (key in fields) savedEvidence.current[key] = fields[key] as typeof artifacts;
          }
          for (const item of [...savedEvidence.current.attachments, ...savedEvidence.current.artifacts]) uncommittedUploads.current.delete(evidenceFilename(item));
          saved = true;
        }
        const kept = new Set([...savedEvidence.current.attachments, ...savedEvidence.current.artifacts].map(evidenceFilename));
        for (const filename of removedAttachments.filter((name) => !kept.has(name))) {
          await request({ cmd: 'remove_attachment', task_id: task.id, filename });
          uncommittedUploads.current.delete(filename);
          setRemovedAttachments((current) => current.filter((name) => name !== filename));
        }
        sendOrNotify(sendCommand, { cmd: 'task_detail', id: task.id }, onCommandUnavailable);
        onClose();
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : 'The request failed.';
        setFormError(`${saved ? 'Task changes saved, but attachment cleanup failed. ' : ''}${message}`);
      } finally { busyRef.current = false; setSaving(false); }
    })();
  };

  const removeEvidence = (kind: 'artifact' | 'attachment', index: number) => {
    const item = (kind === 'artifact' ? artifacts : attachments)[index];
    if (!item) return;
    const filename = evidenceFilename(item);
    if (filename) setRemovedAttachments((current) => [...new Set([...current, filename])]);
    if (kind === 'artifact') setArtifacts((current) => current.filter((_, i) => i !== index));
    else setAttachments((current) => current.filter((_, i) => i !== index));
  };
  const close = () => {
    if (busyRef.current) return;
    busyRef.current = true; setSaving(true); setFormError('');
    void (async () => {
      try {
        const kept = new Set([...savedEvidence.current.attachments, ...savedEvidence.current.artifacts].map(evidenceFilename));
        const discarded = new Set([...uncommittedUploads.current, ...removedAttachments.filter((name) => !kept.has(name))]);
        for (const filename of discarded) {
          await request({ cmd: 'remove_attachment', task_id: task.id, filename });
          uncommittedUploads.current.delete(filename);
          setRemovedAttachments((current) => current.filter((name) => name !== filename));
          setAttachments((current) => current.filter((item) => evidenceFilename(item) !== filename));
          setArtifacts((current) => current.filter((item) => evidenceFilename(item) !== filename));
        }
        onClose();
      } catch (cause) { setFormError(`Could not discard new uploads. ${cause instanceof Error ? cause.message : 'Retry closing.'}`); }
      finally { busyRef.current = false; setSaving(false); }
    })();
  };
  useEffect(() => { closeRef.current = close; return () => { closeRef.current = null; }; });

  return (
    <form className={`${styles.detailForm} ${styles.taskDetailForm} ${detailTab === 'evidence' || detailTab === 'activity' ? styles.detailReading : ''}`} onSubmit={submit} onPaste={(event) => { if (event.clipboardData.files.length) { event.preventDefault(); void upload([...event.clipboardData.files]); } }}>
      <fieldset className={styles.createFields} disabled={saving || uploading}>
      <div className={styles.detailOverview}>
        <section className={styles.detailPrimary} aria-label="Primary task fields">
          <label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label>
          <label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={5} /></label>
          <div className={styles.formGrid}>
            <label>Group<select title={targetGroup} value={targetGroup} onChange={(event) => { setTargetGroup(event.target.value); setAgentId(''); }}>{Object.keys(groups).map((name) => <option key={name}>{name}</option>)}</select></label>
            <label>Assigned agent<select title={liveAgents.find((agent) => agent.id === agentId)?.name as string || 'Unassigned'} value={agentId} onChange={(event) => setAgentId(event.target.value)}><option value="">Unassigned</option>{liveAgents.map((agent) => <option key={agent.id} value={agent.id}>{textValue(agent.name, agent.id)}</option>)}</select></label>
          </div>
          <label>Labels<input title={labels} value={labels} onChange={(event) => setLabels(event.target.value)} placeholder="bug, urgent" /></label>
        </section>
        <aside className={styles.detailSummary} aria-label="Task status and primary actions">
          <dl className={styles.detailFacts}>
            <div><dt>Lane</dt><dd title={task.lane}>{task.lane}</dd></div>
            <div><dt>Dispatch</dt><dd title={task.dispatchState || 'queued'}>{task.dispatchState || 'queued'}</dd></div>
            <div><dt>Health</dt><dd title={task.healthState}>{task.healthState}</dd></div>
            <div><dt>Action</dt><dd title={actionName || 'default'}>{actionName || 'default'}</dd></div>
            <div><dt>Role</dt><dd title={role || 'default'}>{role || 'default'}</dd></div>
            <div><dt>Verification</dt><dd title={verification.state || 'not requested'}>{verification.state || 'not requested'}</dd></div>
            <div><dt>Created by</dt><dd title={identityDescription(attribution.creator)}>{attribution.creator.name} · {attribution.creator.kind}</dd></div>
            <div><dt>Executing</dt><dd title={`Executing ${identityDescription(attribution.responsible)}${attribution.activeAgent ? ` via ${identityDescription(attribution.activeAgent)}` : ''}`}>{attribution.responsible.name}{attribution.responsible.kind ? ` · ${attribution.responsible.kind}` : ''}{attribution.activeAgent ? ` via ${attribution.activeAgent.name}` : ''}</dd></div>
          </dl>
          <div className={styles.primaryTaskActions}>
            <Button tone="primary" type="button" onPress={() => sendOrNotify(sendCommand, { cmd: 'dispatch_task', id: task.id, ...(agentId ? { agent_id: agentId } : { create_agent: true }) }, onCommandUnavailable)} isDisabled={task.dispatchState === 'live' || task.lane === 'Archived'}>{task.dispatchState === 'live' ? 'Dispatched' : 'Dispatch task'}</Button>
            <Button tone="quiet" type="button" onPress={() => sendOrNotify(sendCommand, { cmd: 'board_verify_task', id: task.id, actor_name: 'Operator', verification_state: 'passed', manual_smoke_done: true, human_validation_pending: '', deploy_needed: false }, onCommandUnavailable)}>Mark verified</Button>
            {task.parentTaskId ? <Button tone="quiet" type="button" onPress={() => sendOrNotify(sendCommand, { cmd: 'board_update_task', id: task.id, parent_task_id: '', pipeline_depth: 0, pipeline_root_id: task.id, status: '', labels: task.labels.filter((label) => label !== 'torque:derived') }, onCommandUnavailable)}>Detach pipeline</Button> : null}
          </div>
        </aside>
      </div>
      {task.labels.includes('torque:human') ? <AskResponse key={task.id} taskId={task.id} send={(command) => sendOrNotify(sendCommand, command, onCommandUnavailable)} /> : null}
      <div className={styles.detailSecondary}>
      <nav className={styles.detailTabs} role="tablist" aria-label="Task detail sections">
        {([['execution', 'Execution'], ['verification', 'Verification'], ['integration', 'Integrations'], ['evidence', 'Evidence'], ['activity', 'Activity']] as const).map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={detailTab === id} onClick={() => { setDetailTab(id); if (id === 'activity') setActivityVisited(true); }}>{label}</button>)}
      </nav>
      {detailTab === 'execution' ? <section className={styles.detailSection} role="tabpanel" aria-label="Execution">
        <header><div><h3>Execution</h3><p>Configure scheduling, dispatch behavior, role, and dependencies.</p></div></header>
        <div className={styles.formGrid}>
          <label>Scheduled for<input type="datetime-local" value={scheduledAt} onChange={(event) => setScheduledAt(event.target.value)} /></label>
          <label>Action<select title={actionName || 'Group default'} value={actionName} onChange={(event) => setActionName(event.target.value)}><option value="">Group default</option>{actionOptions.map((action) => <option key={textValue(action.name)} value={textValue(action.name)}>{textValue(action.name)}</option>)}</select></label>
          <label>Worker role<select title={role || 'Action/default role'} value={role} onChange={(event) => setRole(event.target.value)}><option value="">Action/default role</option>{roleOptions.map((item) => <option key={textValue(item.slug, textValue(item.name))} value={textValue(item.slug, textValue(item.name))}>{textValue(item.name, textValue(item.slug))}</option>)}</select></label>
          <label>Dependencies<input title={dependsOn} value={dependsOn} onChange={(event) => setDependsOn(event.target.value)} list={`task-dependencies-${task.id}`} placeholder="task IDs, comma separated" /><datalist id={`task-dependencies-${task.id}`}>{tasks.filter((item) => item.id !== task.id).map((item) => <option key={item.id} value={item.id}>{item.task}</option>)}</datalist></label>
        </div>
        <ActionVariableFields definitions={definitions} value={actionVars} onChange={setActionVars} />
        <TaskPromptPreview disabled={saving || uploading || artifactEditing} inputsKey={JSON.stringify([title, description, targetGroup, actionName, role, agentId, actionVars, definitions, attachments, artifacts, labels, verification])} command={() => ({ cmd: 'preview_prompt', id: task.id, task: title.trim(), description, group: targetGroup, action_name: actionName, agent_template: role, agent_id: agentId, action_vars: resolveActionVariables(actionVars, definitions), attachments, artifacts, labels: labels.split(',').map((label) => label.trim()).filter(Boolean), verification_mode: verification.mode, verification_state: verification.state, verification_notes: verification.notes, verification_summary: verification.summary })} />
      </section> : null}
      {detailTab === 'verification' ? <section className={styles.detailSection} role="tabpanel" aria-label="Verification"><header><div><h3>Verification</h3><p>Record release gates and human checks for this task.</p></div><Button tone="quiet" type="button" onPress={() => sendOrNotify(sendCommand, { cmd: 'board_verify_task', id: task.id, actor_name: 'Operator', verification_state: 'passed', manual_smoke_done: true, human_validation_pending: '', deploy_needed: false }, onCommandUnavailable)}>Mark verified</Button></header><VerificationFields value={verification} onChange={setVerification} />{Object.keys(record(task.raw.completion_evidence)).length ? <details><summary>Completion evidence</summary><pre>{JSON.stringify(task.raw.completion_evidence, null, 2)}</pre></details> : null}</section> : null}
      {detailTab === 'integration' ? <section className={styles.detailSection} role="tabpanel" aria-label="Integrations"><header><div><h3>External ticket and sync</h3><p>Link, synchronize, or communicate with the provider ticket.</p></div>{task.externalUrl ? <Button tone="quiet" type="button" onPress={() => sendOrNotify(sendCommand, { cmd: 'external_open_task', id: task.id }, onCommandUnavailable)}>Open ticket</Button> : null}</header><div className={styles.formGrid}><label>Provider<input value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="github" /></label><label>External ID<input value={externalId} onChange={(event) => setExternalId(event.target.value)} placeholder="owner/repo#123" /></label><label>External URL<input value={externalUrl} onChange={(event) => setExternalUrl(event.target.value)} /></label><label className={styles.checkField}><input type="checkbox" checked={syncEnabled} onChange={(event) => setSyncEnabled(event.target.checked)} />Track with Board sync</label></div><div className={styles.taskActionRow}><Button tone="quiet" type="button" onPress={() => sendOrNotify(sendCommand, { cmd: 'board_sync_task', task: task.id }, onCommandUnavailable)} isDisabled={!externalId && !externalUrl}>Sync now</Button><Button tone="quiet" type="button" onPress={() => { setPullBaseline(pullResponse); setPullRequested(true); sendOrNotify(sendCommand, { cmd: 'board_pull_preview', task: task.id }, onCommandUnavailable); }} isDisabled={!externalId && !externalUrl}>Pull preview</Button><Button tone="quiet" type="button" onPress={() => { setProvider(''); setExternalId(''); setExternalUrl(''); setSyncEnabled(false); sendOrNotify(sendCommand, { cmd: 'external_link_task', id: task.id, ref: '', provider: '', external_id: '', external_url: '', board_sync: { version: 1, enabled: false } }, onCommandUnavailable); }} isDisabled={!externalId && !externalUrl}>Unlink</Button></div>{Object.keys(pullChanges).length ? <div className={styles.pullPreview}><h4>Inbound changes</h4>{Object.entries(pullChanges).map(([field, value]) => <div key={field}><strong>{field}</strong><span>Local: {textValue(record(value).local)}</span><span>Remote: {textValue(record(value).remote)}</span></div>)}<Button tone="primary" type="button" onPress={() => sendOrNotify(sendCommand, { cmd: 'board_pull_apply', task: task.id, fields: Object.keys(pullChanges) }, onCommandUnavailable)}>Apply all changes</Button></div> : null}<div className={styles.externalComposer}><label>Push status<input value={externalStatus} onChange={(event) => setExternalStatus(event.target.value)} /></label><Button tone="quiet" type="button" onPress={() => sendOrNotify(sendCommand, { cmd: 'external_push_task_status', id: task.id, status: externalStatus, note: '' }, onCommandUnavailable)} isDisabled={!externalStatus.trim() || (!externalId && !externalUrl)}>Push</Button><label>Post comment<textarea value={externalComment} onChange={(event) => setExternalComment(event.target.value)} rows={2} /></label><Button tone="quiet" type="button" onPress={() => { sendOrNotify(sendCommand, { cmd: 'external_post_task_comment', id: task.id, comment: externalComment.trim() }, onCommandUnavailable); setExternalComment(''); }} isDisabled={!externalComment.trim() || (!externalId && !externalUrl)}>Post</Button></div></section> : null}
      {activityVisited ? <section role="tabpanel" aria-label="Activity" hidden={detailTab !== 'activity'}><TaskActivity messages={task.messages} taskId={task.id} active={detailTab === 'activity'} /></section> : null}
      <section className={styles.artifacts} role="tabpanel" aria-label="Evidence" hidden={detailTab !== 'evidence'}>
        <TaskEvidenceEditor artifacts={artifacts} attachments={attachments} draftId={task.id} onChange={setArtifacts} onRemove={removeEvidence} onUpload={(files) => { void upload(files); }} onEditingChange={setArtifactEditing} />
      </section>
      </div>
      </fieldset>
      {formError ? <p className={styles.formError} role="alert">{formError}</p> : artifactEditing ? <p className={styles.formHint} role="status">Save or cancel the artifact edit in Evidence before saving the task.</p> : null}
      <footer className={styles.detailFooter}>
        <Button tone="danger" type="button" isDisabled={saving || uploading} onPress={onRemove}>Remove…</Button>
        <span />
        <Button tone="quiet" type="button" isDisabled={saving || uploading} onPress={close}>Cancel</Button>
        <Button tone="primary" type="submit" isDisabled={saving || uploading || artifactEditing}>{saving ? 'Saving task…' : 'Save task'}</Button>
      </footer>
    </form>
  );
}

function BatchEditPanel({ tasks, agents, actions, sendCommand, onCommandUnavailable, onClose }: {
  tasks: BoardTask[];
  agents: Record<string, unknown>;
  actions: unknown;
  sendCommand: CommandSender;
  onCommandUnavailable: () => void;
  onClose: () => void;
}) {
  const [label, setLabel] = useState('');
  const [assignee, setAssignee] = useState('__unchanged__');
  const [dueMode, setDueMode] = useState<'unchanged' | 'set' | 'clear'>('unchanged');
  const [due, setDue] = useState('');
  const [action, setAction] = useState('__unchanged__');
  const [priority, setPriority] = useState('__unchanged__');
  const taskGroup = tasks[0]?.group ?? '';
  const liveAgents = Object.entries(agents).map<Record<string, unknown> & { id: string }>(([id, value]) => ({ id, ...record(value) })).filter((item) => item.group === taskGroup && !Number(item.deleted_at ?? 0));
  const actionOptions = actionItems(actions);
  const apply = (event: FormEvent) => {
    event.preventDefault();
    tasks.forEach((task) => {
      const fields: Record<string, unknown> = {};
      let nextLabels = [...task.labels];
      if (label.trim() && !nextLabels.includes(label.trim())) nextLabels.push(label.trim());
      if (priority !== '__unchanged__') {
        nextLabels = nextLabels.filter((item) => !item.startsWith('priority:'));
        if (priority) nextLabels.push(`priority:${priority}`);
      }
      if (label.trim() || priority !== '__unchanged__') fields.labels = nextLabels;
      if (assignee !== '__unchanged__') fields.agent_id = assignee;
      if (action !== '__unchanged__') { fields.action_name = action; fields.action_vars = {}; }
      if (dueMode === 'clear') fields.scheduled_at = '';
      else if (dueMode === 'set' && due) fields.scheduled_at = new Date(due).toISOString();
      if (Object.keys(fields).length) sendOrNotify(sendCommand, { cmd: 'board_update_task', id: task.id, ...fields }, onCommandUnavailable);
    });
    onClose();
  };
  return <form className={styles.batchEdit} onSubmit={apply}><p>Apply shared metadata to {tasks.length} selected task{tasks.length === 1 ? '' : 's'}.</p><div className={styles.formGrid}><label>Add label<input value={label} onChange={(event) => setLabel(event.target.value)} /></label><label>Priority<select value={priority} onChange={(event) => setPriority(event.target.value)}><option value="__unchanged__">No change</option><option value="">None</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label><label>Assignee<select value={assignee} onChange={(event) => setAssignee(event.target.value)}><option value="__unchanged__">No change</option><option value="">Unassigned</option>{liveAgents.map((agent) => <option key={agent.id} value={agent.id}>{textValue(agent.name, agent.id)}</option>)}</select></label><label>Action<select value={action} onChange={(event) => setAction(event.target.value)}><option value="__unchanged__">No change</option><option value="">None</option>{actionOptions.map((item) => <option key={textValue(item.name)} value={textValue(item.name)}>{textValue(item.name)}</option>)}</select></label><label>Due date<select value={dueMode} onChange={(event) => setDueMode(event.target.value as typeof dueMode)}><option value="unchanged">No change</option><option value="set">Set date</option><option value="clear">Clear date</option></select></label>{dueMode === 'set' ? <label>Date and time<input type="datetime-local" value={due} onChange={(event) => setDue(event.target.value)} required /></label> : null}</div><footer><Button tone="quiet" type="button" onPress={onClose}>Cancel</Button><Button tone="primary" type="submit">Apply changes</Button></footer></form>;
}

function LaneManager({ lanes, sendCommand, onCommandUnavailable, onClose }: { lanes: string[]; sendCommand: CommandSender; onCommandUnavailable: () => void; onClose: () => void }) {
  const reserved = new Set(['Backlog', 'To Do', 'In Progress', 'Done', 'Archived']);
  const [newLane, setNewLane] = useState('');
  const [editing, setEditing] = useState('');
  const [rename, setRename] = useState('');
  const [removing, setRemoving] = useState('');
  const [fallback, setFallback] = useState('Backlog');
  const reorder = (lane: string, direction: number) => {
    const current = lanes.indexOf(lane); const target = current + direction;
    if (current < 0 || target < 0 || target >= lanes.length) return;
    const next = [...lanes]; [next[current], next[target]] = [next[target] as string, next[current] as string];
    sendOrNotify(sendCommand, { cmd: 'board_reorder_lanes', lanes: next }, onCommandUnavailable);
  };
  return <div className={styles.laneManager}><form onSubmit={(event) => { event.preventDefault(); if (!newLane.trim()) return; sendOrNotify(sendCommand, { cmd: 'board_add_lane', name: newLane.trim() }, onCommandUnavailable); setNewLane(''); }}><label>New lane<input value={newLane} onChange={(event) => setNewLane(event.target.value)} /></label><Button tone="primary" type="submit" isDisabled={!newLane.trim()}>Add lane</Button></form><section>{lanes.filter((lane) => lane !== 'Archived').map((lane, index) => <article key={lane}>{editing === lane ? <input value={rename} onChange={(event) => setRename(event.target.value)} aria-label={`Rename ${lane}`} /> : <strong>{lane}</strong>}<Button tone="quiet" aria-label={`Move ${lane} left`} onPress={() => reorder(lane, -1)} isDisabled={index === 0}>←</Button><Button tone="quiet" aria-label={`Move ${lane} right`} onPress={() => reorder(lane, 1)} isDisabled={index === lanes.length - 2}>→</Button>{reserved.has(lane) ? <span>reserved</span> : editing === lane ? <><Button tone="primary" onPress={() => { if (rename.trim()) sendOrNotify(sendCommand, { cmd: 'board_rename_lane', old_name: lane, new_name: rename.trim() }, onCommandUnavailable); setEditing(''); }}>Save</Button><Button tone="quiet" onPress={() => setEditing('')}>Cancel</Button></> : <><Button tone="quiet" onPress={() => { setEditing(lane); setRename(lane); }}>Rename</Button><Button tone="danger" onPress={() => { setRemoving(lane); setFallback(lanes.find((item) => item !== lane && item !== 'Archived') ?? 'Backlog'); }}>Remove…</Button></>}</article>)}</section>{removing ? <div className={styles.laneRemove}><p>Move tasks from <strong>{removing}</strong> before removing it.</p><label>Move tasks to<select value={fallback} onChange={(event) => setFallback(event.target.value)}>{lanes.filter((lane) => lane !== removing && lane !== 'Archived').map((lane) => <option key={lane}>{lane}</option>)}</select></label><Button tone="danger" onPress={() => { sendOrNotify(sendCommand, { cmd: 'board_remove_lane', name: removing, move_tasks_to: fallback }, onCommandUnavailable); setRemoving(''); }}>Remove lane</Button><Button tone="quiet" onPress={() => setRemoving('')}>Cancel</Button></div> : null}<footer><Button tone="quiet" onPress={onClose}>Done</Button></footer></div>;
}

interface SchedulesPanelProps {
  group: string;
  schedules: Record<string, unknown>;
  actions: unknown;
  roles: unknown;
  sendCommand: CommandSender;
  onCommandUnavailable: () => void;
  onClose: () => void;
}

function SchedulesPanel({ group, schedules, actions, roles, sendCommand, onCommandUnavailable, onClose }: SchedulesPanelProps) {
  const [editingId, setEditingId] = useState('');
  const [name, setName] = useState('');
  const [template, setTemplate] = useState('');
  const [description, setDescription] = useState('');
  const [actionName, setActionName] = useState('');
  const [actionVars, setActionVars] = useState('{}');
  const [role, setRole] = useState('');
  const [labels, setLabels] = useState('');
  const [cron, setCron] = useState('');
  const [scheduledAt, setScheduledAt] = useState('');
  const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [formError, setFormError] = useState('');
  const groupSchedules = Object.values(schedules).map(record).filter((schedule) => schedule.group === group);
  const reset = () => { setEditingId(''); setName(''); setTemplate(''); setDescription(''); setActionName(''); setActionVars('{}'); setRole(''); setLabels(''); setCron(''); setScheduledAt(''); setTimezone(Intl.DateTimeFormat().resolvedOptions().timeZone); setFormError(''); };
  const edit = (schedule: Record<string, unknown>) => { setEditingId(textValue(schedule.id)); setName(textValue(schedule.name)); setTemplate(textValue(schedule.task_template)); setDescription(textValue(schedule.description)); setActionName(textValue(schedule.action_name)); setActionVars(JSON.stringify(record(schedule.action_vars), null, 2)); setRole(textValue(schedule.agent_template)); setLabels(Array.isArray(schedule.labels) ? schedule.labels.join(', ') : ''); setCron(textValue(schedule.cron_expr)); setScheduledAt(textValue(schedule.scheduled_at).slice(0, 16)); setTimezone(textValue(schedule.timezone, Intl.DateTimeFormat().resolvedOptions().timeZone)); setFormError(''); };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    let parsedVars: Record<string, unknown>;
    try { parsedVars = parseJsonObject(actionVars); }
    catch { setFormError('Action variables must be a valid JSON object.'); return; }
    setFormError('');
    sendOrNotify(sendCommand, {
      cmd: editingId ? 'schedule_update' : 'schedule_create', ...(editingId ? { id: editingId } : {}), name: name.trim(), task_template: template.trim(), description, group,
      action_name: actionName, action_vars: parsedVars, agent_template: role, labels: labels.split(',').map((item) => item.trim()).filter(Boolean),
      cron_expr: cron.trim(), scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : '',
      timezone,
    }, onCommandUnavailable);
    reset();
  };
  return (
    <div className={styles.schedulesPanel}>
      <form onSubmit={submit} className={styles.scheduleForm}>
        <header><h3>{editingId ? 'Edit schedule' : 'New schedule'}</h3>{editingId ? <Button tone="quiet" type="button" onPress={reset}>Cancel edit</Button> : null}</header>
        <label>Name<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
        <label>Task title<input value={template} onChange={(event) => setTemplate(event.target.value)} required placeholder="Daily review {date}" /></label>
        <label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></label>
        <div className={styles.formGrid}>
          <label>Cron<input value={cron} onChange={(event) => setCron(event.target.value)} placeholder="0 9 * * 1-5" /></label>
          <label>Or one-time<input type="datetime-local" value={scheduledAt} onChange={(event) => setScheduledAt(event.target.value)} /></label>
          <label>Timezone<input value={timezone} onChange={(event) => setTimezone(event.target.value)} /></label>
          <label>Labels<input value={labels} onChange={(event) => setLabels(event.target.value)} placeholder="scheduled, review" /></label>
          <label>Action<select value={actionName} onChange={(event) => setActionName(event.target.value)}><option value="">Group default</option>{actionItems(actions).map((item) => <option key={textValue(item.name)} value={textValue(item.name)}>{textValue(item.name)}</option>)}</select></label>
          <label>Worker role<select value={role} onChange={(event) => setRole(event.target.value)}><option value="">Action/default role</option>{roleItems(roles).map((item) => <option key={textValue(item.slug, textValue(item.name))} value={textValue(item.slug, textValue(item.name))}>{textValue(item.name, textValue(item.slug))}</option>)}</select></label>
        </div>
        <label>Action variables (JSON)<textarea value={actionVars} onChange={(event) => { setActionVars(event.target.value); setFormError(''); }} rows={3} spellCheck={false} /></label>
        {formError ? <p className={styles.formError}>{formError}</p> : null}
        <Button tone="primary" type="submit" isDisabled={!cron && !scheduledAt}>{editingId ? 'Save schedule' : 'Create schedule'}</Button>
      </form>
      <section className={styles.scheduleList} aria-label="Existing schedules">
        {groupSchedules.length ? groupSchedules.map((schedule) => {
          const id = textValue(schedule.id);
          const enabled = schedule.enabled !== false;
          return <article key={id}>
            <div><strong>{textValue(schedule.name, 'Untitled')}</strong><span>{textValue(schedule.cron_expr, textValue(schedule.scheduled_at))}</span></div>
            <div><Button tone="quiet" onPress={() => edit(schedule)}>Edit</Button><Button tone="quiet" onPress={() => sendOrNotify(sendCommand, { cmd: 'schedule_run', id }, onCommandUnavailable)}>Run</Button><Button tone="quiet" onPress={() => sendOrNotify(sendCommand, { cmd: enabled ? 'schedule_disable' : 'schedule_enable', id }, onCommandUnavailable)}>{enabled ? 'Disable' : 'Enable'}</Button><Button tone="danger" onPress={() => sendOrNotify(sendCommand, { cmd: 'schedule_remove', id }, onCommandUnavailable)}>Remove</Button></div>
          </article>;
        }) : <StateSurface title="No schedules" description="Create a recurring or one-time task dispatch for this group." />}
      </section>
      <footer className={styles.detailFooter}><span /><Button tone="quiet" onPress={onClose}>Close</Button></footer>
    </div>
  );
}

export function BoardPanel({ group, sendCommand, onCommandUnavailable }: BoardPanelProps) {
  const [initialTaskTab, setInitialTaskTab] = useState<'execution' | 'activity'>('execution');
  const taskEditBusy = useRef(false);
  const taskEditClose = useRef<(() => void) | null>(null);
  const dispatch = useAppDispatch();
  const { records, lanes: rawLanes, schedules, archived } = useAppSelector(selectTasksState);
  const connection = useAppSelector(selectConnection);
  const { records: agents } = useAppSelector(selectAgentsState);
  const groupsState = useAppSelector(selectGroupsState);
  const catalog = useAppSelector(selectCatalogState);
  const auxiliaryResponses = useAppSelector(selectAuxiliaryResponseState);
  const persistedView = useAppSelector(selectBoardViewState);
  const workspaceUi = useAppSelector(selectWorkspaceUi);
  const [filtersByGroup, setFiltersByGroup] = useState<Record<string, BoardFilterState>>({});
  const [removeTaskId, setRemoveTaskId] = useState<string | null>(null);
  const [schedulesOpen, setSchedulesOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importRef, setImportRef] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [dispatchTargetId, setDispatchTargetId] = useState<string | null>(null);
  const [viewsOpen, setViewsOpen] = useState(false);
  const [lanesOpen, setLanesOpen] = useState(false);
  const [savedViewName, setSavedViewName] = useState('');
  const [dismissedMoveFrame, setDismissedMoveFrame] = useState<unknown>(null);
  const [dragLaneOrders, setDragLaneOrders] = useState<DragLaneOrders | null>(null);
  const [laneRenderWindow, setLaneRenderWindow] = useState<{ context: string; limits: Record<string, number> }>({ context: '', limits: {} });
  const searchRef = useRef<HTMLInputElement>(null);
  const boardRef = useRef<HTMLDivElement>(null);
  const activeLanes = rawLanes.filter((lane): lane is string => typeof lane === 'string' && lane !== 'Archived');
  const selectedLane = textValue(persistedView.selectedLanes[group]);
  const hiddenLanes = record(persistedView.hiddenWideLanes[group]);
  const lanes = showArchived ? ['Archived'] : activeLanes.filter((lane) => selectedLane && activeLanes.includes(selectedLane) ? lane === selectedLane : !hiddenLanes[lane]);
  const chooseLane = (lane: string) => sendOrNotify(sendCommand, { cmd: 'board_set_selected_lanes', selected_lanes_by_group: { ...persistedView.selectedLanes, [group]: lane } }, onCommandUnavailable);
  const toggleLane = (lane: string) => sendOrNotify(sendCommand, { cmd: 'board_set_hidden_wide_lanes', hidden_wide_lanes_by_group: { ...persistedView.hiddenWideLanes, [group]: { ...hiddenLanes, [lane]: !hiddenLanes[lane] } } }, onCommandUnavailable);
  const archivedRecords = useMemo(() => Object.fromEntries(recordItems(archived[group]).map((item, index) => [textValue(item.id, `archived-${index}`), item])), [archived, group]);
  const allTasks = useMemo(() => normalizeTasks({ ...archivedRecords, ...records }), [records, archivedRecords]);
  const groupTasks = useMemo(() => allTasks.filter((task) => task.group === group), [allTasks, group]);
  const surfaceTasks = useMemo(
    () => groupTasks.filter((task) => showArchived ? task.lane === 'Archived' : task.lane !== 'Archived'),
    [groupTasks, showArchived],
  );
  const persistedFilters = normalizeFilters(persistedView.filters[group]);
  const filters = filtersByGroup[group] ?? persistedFilters;
  const filteredTasks = surfaceTasks.filter((task) => taskMatchesFilters(task, filters));
  const savedViews = Array.isArray(persistedView.savedViews[group]) ? persistedView.savedViews[group].map(record) : [];
  const availableLabels = [...new Set(surfaceTasks.flatMap((task) => task.labels))].sort();
  const availableActions = [...new Set(surfaceTasks.map((task) => task.actionName).filter(Boolean))].sort();
  const availableHealth = [...new Set(surfaceTasks.map((task) => task.healthState).filter(Boolean))].sort();
  const density = typeof persistedView.cardDensity[group] === 'string' ? String(persistedView.cardDensity[group]) : 'normal';
  const laneSorts = record(persistedView.laneSorts[group]);
  const laneViews = new Map(lanes.map((lane) => {
    const laneTasks = orderedLaneTasks(filteredTasks, lane, laneSorts[lane]);
    return [lane, { laneTasks, hierarchy: visibleHierarchy(laneTasks, workspaceUi.collapsedTaskIds) }] as const;
  }));
  const laneRenderContext = JSON.stringify({ group, filters, laneSorts, collapsed: workspaceUi.collapsedTaskIds });
  const laneRenderLimits = laneRenderWindow.context === laneRenderContext ? laneRenderWindow.limits : {};
  const renderedHierarchyByLane = new Map(lanes.map((lane) => {
    const hierarchy = laneViews.get(lane)?.hierarchy ?? [];
    return [lane, hierarchy.slice(0, laneRenderLimits[lane] ?? INITIAL_LANE_RENDER_LIMIT)] as const;
  }));
  const hierarchyNodeById = new Map(
    [...laneViews.values()].flatMap(({ hierarchy }) => hierarchy.map((node) => [node.task.id, node] as const)),
  );
  const selected = new Set(workspaceUi.selectedTaskIds);
  const taskById = new Map(allTasks.map((task) => [task.id, task]));
  const detailTask = workspaceUi.detailTaskId ? taskById.get(workspaceUi.detailTaskId) ?? null : null;
  const detailVersion = Number(detailTask?.raw._detail_version) || 0;
  const detailIsHydrated = Boolean(detailTask && (
    detailVersion > 0
    || Object.prototype.hasOwnProperty.call(detailTask.raw, 'description')
    || Object.prototype.hasOwnProperty.call(detailTask.raw, 'action_vars')
    || Object.prototype.hasOwnProperty.call(detailTask.raw, 'attachments')
  ));
  // Compact snapshots intentionally omit full fields. Keep the mounted editor
  // until the selected task's fresh detail arrives instead of discarding drafts.
  const [hydratedDetail, setHydratedDetail] = useState<BoardTask | null>(null);
  if (detailTask && detailIsHydrated && hydratedDetail !== detailTask) setHydratedDetail(detailTask);
  else if (!detailTask && hydratedDetail) setHydratedDetail(null);
  const editorTask = detailTask && (detailIsHydrated ? detailTask : hydratedDetail?.id === detailTask.id ? hydratedDetail : null);
  const taskDetailRequest = useRef('');
  const requestedTaskId = workspaceUi.detailTaskId;
  const requestedTaskGroup = detailTask?.group || group;
  useEffect(() => {
    if (!requestedTaskId) { taskDetailRequest.current = ''; return; }
    if (connection.status !== 'connected') return;
    const key = `${requestedTaskId}:${requestedTaskGroup}:${connection.reconnectCount}`;
    if (taskDetailRequest.current === key) return;
    taskDetailRequest.current = key;
    sendOrNotify(sendCommand, { cmd: 'task_detail', id: requestedTaskId }, onCommandUnavailable);
    sendOrNotify(sendCommand, { cmd: 'list_actions', group: requestedTaskGroup }, onCommandUnavailable);
    sendOrNotify(sendCommand, { cmd: 'list_roles', group: requestedTaskGroup }, onCommandUnavailable);
  }, [requestedTaskId, requestedTaskGroup, connection.status, connection.reconnectCount, sendCommand, onCommandUnavailable]);
  const removeTask = removeTaskId ? taskById.get(removeTaskId) ?? null : null;
  const selectedTasks = workspaceUi.selectedTaskIds.map((id) => taskById.get(id)).filter((task): task is BoardTask => Boolean(task));
  const dispatchTarget = dispatchTargetId ? taskById.get(dispatchTargetId) ?? null : null;
  const auxiliaryFrame = connection.lastAuxiliaryFrame;
  const moveAcknowledgement = auxiliaryFrame
    && auxiliaryFrame !== dismissedMoveFrame
    && auxiliaryFrame.type === 'task_move_acknowledgement_required'
    && typeof auxiliaryFrame.task_id === 'string'
    && typeof auxiliaryFrame.new_lane === 'string'
    ? {
        taskId: auxiliaryFrame.task_id,
        lane: auxiliaryFrame.new_lane,
        message: typeof auxiliaryFrame.message === 'string'
          ? auxiliaryFrame.message
          : 'This task may have unmerged work.',
      }
    : null;
  const sensors = useSensors(
    useSensor(BoardPointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  useEffect(() => {
    const focusSearch = () => searchRef.current?.focus();
    window.addEventListener('torque:focus-board-search', focusSearch);
    return () => window.removeEventListener('torque:focus-board-search', focusSearch);
  }, []);

  useEffect(() => {
    if (!workspaceUi.createTaskDialogOpen) return;
    sendOrNotify(sendCommand, { cmd: 'list_actions', group }, onCommandUnavailable);
    sendOrNotify(sendCommand, { cmd: 'list_roles', group }, onCommandUnavailable);
  }, [group, onCommandUnavailable, sendCommand, workspaceUi.createTaskDialogOpen]);

  const setFilters = (next: BoardFilterState) => {
    const nextByGroup = { ...filtersByGroup, [group]: next };
    setFiltersByGroup(nextByGroup);
    const persisted = { ...persistedView.filters, [group]: next };
    sendOrNotify(sendCommand, { cmd: 'board_set_filters', filters_by_group: persisted }, onCommandUnavailable);
  };
  const toggleFilterValue = (key: 'filter_labels' | 'filter_actions' | 'filter_agents' | 'filter_health', value: string) => {
    const current = filters[key];
    setFilters({ ...filters, [key]: current.includes(value) ? current.filter((item) => item !== value) : [...current, value] });
  };
  const persistSavedViews = (next: Record<string, unknown>[]) => sendOrNotify(sendCommand, { cmd: 'board_set_saved_views', saved_views_by_group: { ...persistedView.savedViews, [group]: next } }, onCommandUnavailable);
  const saveCurrentView = () => {
    const name = savedViewName.trim();
    if (!name) return;
    persistSavedViews([...savedViews.filter((view) => textValue(view.name) !== name), { name, ...filters }]);
    setSavedViewName('');
  };

  const setDensity = (next: string) => {
    sendOrNotify(sendCommand, {
      cmd: 'board_set_card_density',
      card_density_by_group: { ...persistedView.cardDensity, [group]: next },
    }, onCommandUnavailable);
  };

  const setLaneSort = (lane: string, mode: string) => {
    sendOrNotify(sendCommand, {
      cmd: 'board_set_lane_sorts',
      lane_sorts_by_group: { ...persistedView.laneSorts, [group]: { ...laneSorts, [lane]: mode } },
    }, onCommandUnavailable);
  };

  const moveTask = (task: BoardTask, lane: string, position?: number) => {
    const cmd = task.lane === lane ? 'board_reorder_task' : 'board_move_task';
    sendOrNotify(sendCommand, { cmd, id: task.id, ...(cmd === 'board_move_task' ? { lane } : {}), ...(position === undefined ? {} : { position }) }, onCommandUnavailable);
  };

  const duplicateTask = (task: BoardTask) => sendOrNotify(sendCommand, {
    cmd: 'board_add_task', task: task.task, group: task.group, description: task.description,
    action_name: task.actionName, agent_template: task.agentTemplate,
    action_vars: record(task.raw.action_vars), labels: task.labels.filter((label) => !label.startsWith('torque:')),
  }, onCommandUnavailable);

  const archiveTask = (task: BoardTask) => sendOrNotify(sendCommand, task.lane === 'Archived'
    ? { cmd: 'board_unarchive_task', id: task.id }
    : { cmd: 'board_archive_task', id: task.id }, onCommandUnavailable);

  const clearSelection = () => dispatch(workspaceUiActions.clearSelectedTasks());
  const bulkMove = (lane: string) => { selectedTasks.forEach((task) => moveTask(task, lane)); clearSelection(); };
  const bulkDispatch = () => { selectedTasks.forEach((task) => sendOrNotify(sendCommand, { cmd: 'dispatch_task', id: task.id, ...(task.agentId ? { agent_id: task.agentId } : { create_agent: true }) }, onCommandUnavailable)); clearSelection(); };
  const bulkArchive = () => { selectedTasks.forEach(archiveTask); clearSelection(); };
  const bulkSync = () => { selectedTasks.filter((task) => task.externalId || task.externalUrl).forEach((task) => sendOrNotify(sendCommand, { cmd: 'board_sync_task', task: task.id }, onCommandUnavailable)); clearSelection(); };
  const bulkDelete = () => { selectedTasks.forEach((task) => sendOrNotify(sendCommand, { cmd: 'board_remove_task', id: task.id }, onCommandUnavailable)); clearSelection(); };

  const openTask = (taskId: string, tab: 'execution' | 'activity' = 'execution') => {
    setInitialTaskTab(tab);
    dispatch(workspaceUiActions.setDetailTask(taskId));
  };

  const handleDragStart = ({ active }: DragStartEvent) => {
    if (!taskById.has(String(active.id))) return;
    setDragLaneOrders(Object.fromEntries(
      lanes.map((lane) => [lane, (renderedHierarchyByLane.get(lane) ?? []).map(({ task }) => task.id)]),
    ));
  };

  const handleDragOver = ({ active, over }: DragOverEvent) => {
    if (!over) return;
    const activeId = String(active.id);
    const overId = String(over.id);
    setDragLaneOrders((orders) => {
      if (!orders) return orders;
      const sourceLane = dragLaneForTask(orders, activeId);
      const targetLane = overId.startsWith('lane:')
        ? overId.slice(5)
        : dragLaneForTask(orders, overId);
      if (!sourceLane || !targetLane || sourceLane === targetLane) return orders;
      const targetOrder = orders[targetLane];
      if (!targetOrder) return orders;

      const translated = active.rect.current.translated;
      const position = overId.startsWith('lane:')
        ? targetOrder.length
        : taskDropPosition(
            targetOrder,
            activeId,
            overId,
            translated ? {
              activeTop: translated.top,
              activeHeight: translated.height,
              overTop: over.rect.top,
              overHeight: over.rect.height,
            } : undefined,
          ) ?? targetOrder.length;
      return moveTaskBetweenDragLanes(orders, activeId, sourceLane, targetLane, position);
    });
  };

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    const previewOrders = dragLaneOrders;
    setDragLaneOrders(null);
    if (!over) return;
    const task = taskById.get(String(active.id));
    if (!task) return;
    const overId = String(over.id);
    if (overId.startsWith('lane:')) {
      const lane = previewOrders ? dragLaneForTask(previewOrders, task.id) : overId.slice(5);
      const previewPosition = previewOrders?.[lane]?.indexOf(task.id) ?? -1;
      moveTask(task, lane, previewPosition >= 0 ? previewPosition : undefined);
      return;
    }
    const target = taskById.get(overId);
    if (target?.id === task.id) {
      const lane = previewOrders ? dragLaneForTask(previewOrders, task.id) : task.lane;
      const previewPosition = previewOrders?.[lane]?.indexOf(task.id) ?? -1;
      if (lane !== task.lane) moveTask(task, lane, previewPosition >= 0 ? previewPosition : undefined);
      return;
    }
    if (!target) return;
    const targetLaneTasks = orderedLaneTasks(groupTasks, target.lane, laneSorts[target.lane]);
    const targetSort = laneSorts[target.lane];
    if (task.lane === target.lane && targetSort && targetSort !== 'manual') return;
    const translated = active.rect.current.translated;
    const position = targetSort && targetSort !== 'manual'
      ? undefined
      : taskDropPosition(
          targetLaneTasks.map((item) => item.id),
          task.id,
          target.id,
          translated ? {
            activeTop: translated.top,
            activeHeight: translated.height,
            overTop: over.rect.top,
            overHeight: over.rect.height,
          } : undefined,
        ) ?? undefined;
    moveTask(task, target.lane, position);
  };

  const focusRelative = (direction: number) => {
    const visibleIds = lanes.flatMap((lane) => (renderedHierarchyByLane.get(lane) ?? []).map(({ task }) => task.id));
    if (!visibleIds.length) return;
    const current = workspaceUi.focusedTaskId ? visibleIds.indexOf(workspaceUi.focusedTaskId) : -1;
    const next = visibleIds[(current + direction + visibleIds.length) % visibleIds.length] ?? visibleIds[0];
    dispatch(workspaceUiActions.setFocusedTask(next ?? null));
    requestAnimationFrame(() => boardRef.current?.querySelector<HTMLElement>(`[data-task-id="${globalThis.CSS.escape(next ?? '')}"]`)?.focus());
  };

  const revealLaneBatch = (lane: string) => {
    const total = laneViews.get(lane)?.hierarchy.length ?? 0;
    setLaneRenderWindow((current) => {
      const limits = current.context === laneRenderContext ? current.limits : {};
      const currentLimit = limits[lane] ?? INITIAL_LANE_RENDER_LIMIT;
      const nextLimit = Math.min(total, currentLimit + LANE_RENDER_BATCH);
      if (nextLimit <= currentLimit && current.context === laneRenderContext) return current;
      return { context: laneRenderContext, limits: { ...limits, [lane]: nextLimit } };
    });
  };

  const handleBoardKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target.matches('input, textarea, select, [contenteditable="true"]') || target.closest('button, [role="menuitem"]')) return;
    if (event.key === '/') { event.preventDefault(); searchRef.current?.focus(); }
    if (event.key.toLocaleLowerCase() === 'n') { event.preventDefault(); dispatch(workspaceUiActions.setCreateTaskDialogOpen(true)); }
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') { event.preventDefault(); focusRelative(1); }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') { event.preventDefault(); focusRelative(-1); }
    if (event.key === 'Enter' && workspaceUi.focusedTaskId) { event.preventDefault(); openTask(workspaceUi.focusedTaskId); }
    if (event.key === ' ' && workspaceUi.focusedTaskId) { event.preventDefault(); dispatch(workspaceUiActions.toggleSelectedTask({ id: workspaceUi.focusedTaskId, additive: event.metaKey || event.ctrlKey || event.shiftKey })); }
    if (event.key === 'Escape') dispatch(workspaceUiActions.clearSelectedTasks());
  };

  if (!group) return <StateSurface title="Choose a group" description="The Board is scoped to a Torque group. Choose one from the workspace sidebar." />;

  return (
    <section className={styles.boardPanel} aria-labelledby="board-heading">
      <header className={styles.boardHeader}>
        <div><p>Workspace / {group}</p><h1 id="board-heading">Board</h1></div>
        <span className={styles.boardCount}>{filteredTasks.length} visible · {surfaceTasks.length} total</span>
        <div className={styles.headerActions}>
          <Button onPress={() => { setSchedulesOpen(true); [{ cmd: 'schedule_list' }, { cmd: 'list_actions', group }, { cmd: 'list_roles', group }].forEach((command) => sendOrNotify(sendCommand, command, onCommandUnavailable)); }}>Schedules</Button>
          <Button onPress={() => setLanesOpen(true)}>Lanes</Button>
          <Button onPress={() => setImportOpen(true)}>Import external</Button>
          <Button onPress={() => { const next = !showArchived; setShowArchived(next); clearSelection(); if (next) sendOrNotify(sendCommand, { cmd: 'archived_tasks', group }, onCommandUnavailable); }}>{showArchived ? 'Active board' : 'Archive'}</Button>
          <Button onPress={() => sendOrNotify(sendCommand, { cmd: 'board_sync_group', group, force: true }, onCommandUnavailable)}>Sync group</Button>
          {!showArchived ? <Button tone="primary" onPress={() => dispatch(workspaceUiActions.setCreateTaskDialogOpen(true))}>＋ New task</Button> : null}
        </div>
      </header>
      <div className={styles.toolbar}>
        <label className={styles.search}><span>⌕</span><input ref={searchRef} type="search" value={filters.search_query} onChange={(event) => setFilters({ ...filters, search_query: event.target.value })} placeholder="Search board  /" aria-label="Search board" /></label>
        {['blocked', 'scheduled', 'unassigned'].map((view) => (
          <button key={view} aria-pressed={filters.quick_view === view} className={`${styles.filterChip} ${filters.quick_view === view ? styles.filterChipActive : ''}`} onClick={() => setFilters({ ...filters, quick_view: filters.quick_view === view ? '' : view })}>{view}</button>
        ))}
        {!showArchived ? <><label>Visible lane<select aria-label="Visible lane" value={activeLanes.includes(selectedLane) ? selectedLane : ''} onChange={(event) => chooseLane(event.target.value)}><option value="">All visible lanes</option>{activeLanes.map((lane) => <option key={lane}>{lane}</option>)}</select></label><ActionMenu label="Lane visibility" trigger={<Button>Show lanes</Button>}>{activeLanes.map((lane) => <ActionMenuItem key={lane} onAction={() => toggleLane(lane)}>{hiddenLanes[lane] ? 'Show' : 'Hide'} {lane}</ActionMenuItem>)}</ActionMenu></> : null}
        <span className={styles.toolbarSpacer} />
        <ActionMenu label="Saved Board views" trigger={<Button>Views{savedViews.length ? ` · ${savedViews.length}` : ''}</Button>}>
          {savedViews.map((view) => <ActionMenuItem key={textValue(view.name)} onAction={() => setFilters(normalizeFilters(view))}>{textValue(view.name)}</ActionMenuItem>)}
          <ActionMenuItem onAction={() => setViewsOpen(true)}>Filters and saved views…</ActionMenuItem>
        </ActionMenu>
        <ActionMenu label="Card density" trigger={<Button>Density: {density}</Button>}>
          {['compact', 'normal', 'detailed'].map((value) => <ActionMenuItem key={value} onAction={() => setDensity(value)}>{value}</ActionMenuItem>)}
        </ActionMenu>
        <Button tone="quiet" onPress={() => setFilters(emptyBoardFilters)} isDisabled={JSON.stringify(filters) === JSON.stringify(emptyBoardFilters)}>Clear filters</Button>
      </div>
      {selectedTasks.length > 1 ? <div className={styles.selectionBar}><strong>{selectedTasks.length} selected</strong>{!showArchived ? <label>Move to<select defaultValue="" onChange={(event) => { if (event.target.value) bulkMove(event.target.value); }}><option value="" disabled>Choose lane…</option>{rawLanes.filter((lane): lane is string => typeof lane === 'string' && lane !== 'Archived').map((lane) => <option key={lane}>{lane}</option>)}</select></label> : null}<Button tone="quiet" onPress={() => setBatchOpen(true)}>Batch edit</Button><Button tone="quiet" onPress={bulkDispatch} isDisabled={showArchived}>Dispatch</Button><Button tone="quiet" onPress={bulkSync} isDisabled={showArchived || !selectedTasks.some((task) => task.externalId || task.externalUrl)}>Sync linked</Button><Button tone="quiet" onPress={bulkArchive}>{showArchived ? 'Restore' : 'Archive'}</Button><Button tone="danger" onPress={bulkDelete}>Delete</Button><Button tone="quiet" aria-label="Clear task selection" onPress={clearSelection}>×</Button></div> : null}
      {!lanes.length ? <StateSurface title="All lanes hidden" description="Use Show lanes to restore a lane, or select one in Visible lane." /> : null}
      <div className={styles.boardViewport} ref={boardRef} onKeyDown={handleBoardKeyDown}>
        <DndContext
          sensors={sensors}
          collisionDetection={boardCollisionDetection}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
          onDragCancel={() => setDragLaneOrders(null)}
          onDragEnd={handleDragEnd}
        >
          <div
            className={styles.lanes}
            style={{
              gridTemplateColumns: `repeat(${Math.max(1, lanes.length)}, minmax(270px, 1fr))`,
              minWidth: `${Math.max(1, lanes.length) * 270}px`,
            }}
          >
            {lanes.map((lane) => {
              const { laneTasks, hierarchy: fullHierarchy } = laneViews.get(lane) ?? { laneTasks: [], hierarchy: [] };
              const baseHierarchy = renderedHierarchyByLane.get(lane) ?? [];
              const hierarchy = dragLaneOrders
                ? (dragLaneOrders[lane] ?? []).map((id) => hierarchyNodeById.get(id)).filter((node): node is NonNullable<typeof node> => Boolean(node))
                : baseHierarchy;
              const remaining = Math.max(0, fullHierarchy.length - baseHierarchy.length);
              return (
                <LaneDropZone
                  key={lane}
                  lane={lane}
                  header={<header className={styles.laneHeader}>
                    <div><h2>{lane}</h2><span>{laneTasks.length}</span></div>
                    <ActionMenu label={`${lane} lane options`}>
                      {['manual', 'newest', 'oldest', 'due'].map((mode) => <ActionMenuItem key={mode} onAction={() => setLaneSort(lane, mode)}>Sort: {mode}</ActionMenuItem>)}
                      {!showArchived ? <ActionMenuItem onAction={() => dispatch(workspaceUiActions.setCreateLane(lane))}>Add task</ActionMenuItem> : null}
                    </ActionMenu>
                  </header>}
                >
                  {(laneScrollRootRef) => <SortableContext id={`lane:${lane}`} items={hierarchy.map(({ task }) => task.id)} strategy={verticalListSortingStrategy}>
                    {!showArchived ? (workspaceUi.createLane === lane
                      ? <InlineCreate lane={lane} group={group} sendCommand={sendCommand} onCommandUnavailable={onCommandUnavailable} onClose={() => dispatch(workspaceUiActions.setCreateLane(null))} />
                      : <Button tone="quiet" className={styles.addTaskButton ?? ''} onPress={() => dispatch(workspaceUiActions.setCreateLane(lane))}>＋ Add task</Button>) : null}
                    {hierarchy.map(({ task, depth, childCount }) => (
                        <SortableTaskCard
                          key={task.id}
                          task={task}
                          depth={depth}
                          childCount={childCount}
                          density={density}
                          selected={selected.has(task.id)}
                          focused={workspaceUi.focusedTaskId === task.id}
                          collapsed={workspaceUi.collapsedTaskIds.includes(task.id)}
                          attribution={taskAttribution(task, agents)}
                          onSelect={(additive) => { dispatch(workspaceUiActions.setFocusedTask(task.id)); dispatch(workspaceUiActions.toggleSelectedTask({ id: task.id, additive })); }}
                          onOpen={() => openTask(task.id)}
                          onActivity={() => openTask(task.id, 'activity')}
                          onCollapse={() => dispatch(workspaceUiActions.toggleTaskCollapsed(task.id))}
                          onDispatch={() => setDispatchTargetId(task.id)}
                          onDone={() => moveTask(task, 'Done')}
                          onSync={() => sendOrNotify(sendCommand, { cmd: 'board_sync_task', task: task.id }, onCommandUnavailable)}
                          onOpenExternal={() => sendOrNotify(sendCommand, { cmd: 'external_open_task', id: task.id }, onCommandUnavailable)}
                          onDuplicate={() => duplicateTask(task)}
                          onArchive={() => archiveTask(task)}
                          onVerify={() => sendOrNotify(sendCommand, { cmd: 'board_verify_task', id: task.id, actor_name: 'Operator', verification_state: 'passed', manual_smoke_done: true, human_validation_pending: '', deploy_needed: false }, onCommandUnavailable)}
                          onDetach={() => sendOrNotify(sendCommand, { cmd: 'board_update_task', id: task.id, parent_task_id: '', pipeline_depth: 0, pipeline_root_id: task.id, status: '', labels: task.labels.filter((label) => label !== 'torque:derived') }, onCommandUnavailable)}
                          onRemove={() => setRemoveTaskId(task.id)}
                          onFocus={() => dispatch(workspaceUiActions.setFocusedTask(task.id))}
                          onOpenAgent={(agentId) => { dispatch(workspaceUiActions.setSelectedAgent(agentId)); dispatch(workspaceUiActions.setActivePanel('agents')); }}
                        />
                      ))}
                    <LaneRenderSentinel lane={lane} remaining={remaining} scrollRootRef={laneScrollRootRef} onReveal={() => revealLaneBatch(lane)} />
                    {!laneTasks.length && workspaceUi.createLane !== lane ? <div className={styles.emptyLane}>{showArchived ? 'No archived tasks in this lane' : 'Drop a task here or use Add task above'}</div> : null}
                  </SortableContext>}
                </LaneDropZone>
              );
            })}
          </div>
        </DndContext>
      </div>
      <footer className={styles.boardFooter}>
        <span>↑↓ navigate · Space select · Enter open · N create · / search</span>
      </footer>

      <ModalDialog title={detailTask?.task ?? 'Task details'} description={detailTask?.id ?? ''} size="wide" bodyLayout="fit" isOpen={Boolean(detailTask)} onOpenChange={(open) => { if (!open && !taskEditBusy.current) { if (taskEditClose.current) taskEditClose.current(); else dispatch(workspaceUiActions.setDetailTask(null)); } }}>
        {editorTask
          ? <TaskDetail key={editorTask.id} busyRef={taskEditBusy} closeRef={taskEditClose} initialTab={initialTaskTab} task={editorTask} tasks={groupTasks} groups={groupsState.records} agents={agents} actions={catalog.actions} roles={catalog.roles} responses={auxiliaryResponses} sendCommand={sendCommand} onCommandUnavailable={onCommandUnavailable} onClose={() => { setInitialTaskTab('execution'); dispatch(workspaceUiActions.setDetailTask(null)); }} onRemove={() => setRemoveTaskId(editorTask.id)} />
          : detailTask ? <StateSurface title="Loading task" description="Retrieving complete task fields." /> : null}
      </ModalDialog>
      {workspaceUi.createTaskDialogOpen ? <TaskCreateDialog group={group} lanes={lanes} actions={catalog.actions} roles={catalog.roles} onClose={() => dispatch(workspaceUiActions.setCreateTaskDialogOpen(false))} /> : null}
      <ModalDialog title="Schedules" description={`Automated task dispatches for ${group}`} size="large" isOpen={schedulesOpen} onOpenChange={setSchedulesOpen}>
        <SchedulesPanel group={group} schedules={schedules} actions={catalog.actions} roles={catalog.roles} sendCommand={sendCommand} onCommandUnavailable={onCommandUnavailable} onClose={() => setSchedulesOpen(false)} />
      </ModalDialog>
      <ModalDialog title="Manage Board lanes" description="Add or order lanes. Torque's five reserved lifecycle lanes cannot be renamed or removed." size="medium" isOpen={lanesOpen} onOpenChange={setLanesOpen}>
        <LaneManager lanes={rawLanes.filter((lane): lane is string => typeof lane === 'string')} sendCommand={sendCommand} onCommandUnavailable={onCommandUnavailable} onClose={() => setLanesOpen(false)} />
      </ModalDialog>
      <ModalDialog title="Board views and filters" description="Combine task metadata filters and save the result for this group." size="large" isOpen={viewsOpen} onOpenChange={setViewsOpen}>
        <div className={styles.viewsPanel}><div className={styles.filterCollections}><section><h3>Labels</h3>{availableLabels.length ? availableLabels.map((value) => <label key={value} title={value}><input type="checkbox" checked={filters.filter_labels.includes(value)} onChange={() => toggleFilterValue('filter_labels', value)} /><span>{value}</span></label>) : <p>No labels</p>}</section><section><h3>Actions</h3>{availableActions.length ? availableActions.map((value) => <label key={value} title={value}><input type="checkbox" checked={filters.filter_actions.includes(value)} onChange={() => toggleFilterValue('filter_actions', value)} /><span>{value}</span></label>) : <p>No actions</p>}</section><section><h3>Agents</h3>{Object.entries(agents).map<Record<string, unknown> & { id: string }>(([id, value]) => ({ id, ...record(value) })).filter((agent) => agent.group === group).map((agent) => <label key={agent.id} title={textValue(agent.name, agent.id)}><input type="checkbox" checked={filters.filter_agents.includes(agent.id)} onChange={() => toggleFilterValue('filter_agents', agent.id)} /><span>{textValue(agent.name, agent.id)}</span></label>)}</section><section><h3>Health</h3>{availableHealth.map((value) => <label key={value} title={value}><input type="checkbox" checked={filters.filter_health.includes(value)} onChange={() => toggleFilterValue('filter_health', value)} /><span>{value}</span></label>)}</section></div><section className={styles.savedViewEditor}><h3>Saved views</h3>{savedViews.map((view) => <article key={textValue(view.name)}><button type="button" title={textValue(view.name)} onClick={() => setFilters(normalizeFilters(view))}>{textValue(view.name)}</button><button type="button" aria-label={`Delete saved view ${textValue(view.name)}`} onClick={() => persistSavedViews(savedViews.filter((item) => textValue(item.name) !== textValue(view.name)))}>Delete</button></article>)}<div><input value={savedViewName} onChange={(event) => setSavedViewName(event.target.value)} placeholder="View name" aria-label="Saved view name" /><Button tone="primary" onPress={saveCurrentView} isDisabled={!savedViewName.trim()}>Save current filters</Button></div></section><footer><Button tone="quiet" onPress={() => setFilters(emptyBoardFilters)}>Clear filters</Button><span /><Button tone="primary" onPress={() => setViewsOpen(false)}>Done</Button></footer></div>
      </ModalDialog>
      <ModalDialog title="Batch edit tasks" description={`${selectedTasks.length} selected in ${group}`} size="medium" isOpen={batchOpen} onOpenChange={setBatchOpen}>
        {batchOpen ? <BatchEditPanel tasks={selectedTasks} agents={agents} actions={catalog.actions} sendCommand={sendCommand} onCommandUnavailable={onCommandUnavailable} onClose={() => { setBatchOpen(false); clearSelection(); }} /> : null}
      </ModalDialog>
      <ModalDialog title="Import external task" description="Create a Torque task from a provider reference or URL." size="small" isOpen={importOpen} onOpenChange={setImportOpen}>
        <form className={styles.importForm} onSubmit={(event) => { event.preventDefault(); if (!importRef.trim()) return; sendOrNotify(sendCommand, { cmd: 'external_import_task', ref: importRef.trim(), group, lane: showArchived ? '' : lanes[0] ?? '' }, onCommandUnavailable); setImportRef(''); setImportOpen(false); }}><label>External reference or URL<input autoFocus value={importRef} onChange={(event) => setImportRef(event.target.value)} placeholder="https://github.com/owner/repo/issues/123" /></label><footer><Button tone="quiet" type="button" onPress={() => setImportOpen(false)}>Cancel</Button><Button tone="primary" type="submit" isDisabled={!importRef.trim()}>Import</Button></footer></form>
      </ModalDialog>
      <ModalDialog title="Dispatch task" description={dispatchTarget ? `${dispatchTarget.id} · ${dispatchTarget.task}` : ''} size="small" isOpen={Boolean(dispatchTarget)} onOpenChange={(open) => { if (!open) setDispatchTargetId(null); }}>
        <div className={styles.dispatchDialog}><p>Choose an existing live agent in this group, or create a new Worker for the task.</p><Button tone="primary" onPress={() => { if (dispatchTarget) sendOrNotify(sendCommand, { cmd: 'dispatch_task', id: dispatchTarget.id, create_agent: true }, onCommandUnavailable); setDispatchTargetId(null); }}>New Worker</Button>{Object.entries(agents).map<Record<string, unknown> & { id: string }>(([id, value]) => ({ id, ...record(value) })).filter((agent) => agent.group === group && !Number(agent.deleted_at ?? 0)).map((agent) => <Button key={agent.id} tone="quiet" onPress={() => { if (dispatchTarget) sendOrNotify(sendCommand, { cmd: 'dispatch_task', id: dispatchTarget.id, agent_id: agent.id }, onCommandUnavailable); setDispatchTargetId(null); }}>{textValue(agent.name, agent.id)}</Button>)}</div>
      </ModalDialog>
      <ModalDialog title="Remove task?" description={removeTask ? `${removeTask.id} · ${removeTask.task}` : ''} size="small" isOpen={Boolean(removeTask)} onOpenChange={(open) => { if (!open) setRemoveTaskId(null); }}>
        <div className={styles.confirmRemove}><p>This removes the task from the Board. Its external ticket is not deleted.</p><footer><Button tone="quiet" onPress={() => setRemoveTaskId(null)}>Cancel</Button><Button tone="danger" onPress={() => { if (removeTask) sendOrNotify(sendCommand, { cmd: 'board_remove_task', id: removeTask.id }, onCommandUnavailable); setRemoveTaskId(null); dispatch(workspaceUiActions.setDetailTask(null)); }}>Remove task</Button></footer></div>
      </ModalDialog>
      <ModalDialog title="Acknowledge unfinished merge" description={moveAcknowledgement ? `${moveAcknowledgement.taskId} → ${moveAcknowledgement.lane}` : ''} size="small" isOpen={Boolean(moveAcknowledgement)} onOpenChange={(open) => { if (!open) setDismissedMoveFrame(auxiliaryFrame); }}>
        <div className={styles.confirmRemove}><p>{moveAcknowledgement?.message}</p><footer><Button tone="quiet" onPress={() => setDismissedMoveFrame(auxiliaryFrame)}>Cancel</Button><Button tone="primary" onPress={() => { if (moveAcknowledgement) sendOrNotify(sendCommand, { cmd: 'board_move_task', id: moveAcknowledgement.taskId, lane: moveAcknowledgement.lane, acknowledge_unmerged: true }, onCommandUnavailable); setDismissedMoveFrame(auxiliaryFrame); }}>Move anyway</Button></footer></div>
      </ModalDialog>
    </section>
  );
}
