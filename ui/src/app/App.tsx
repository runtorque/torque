import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Provider } from 'react-redux';
import { Dialog, DialogTrigger, Heading, Popover } from 'react-aria-components';

import { ActionMenu, ActionMenuItem, Button, ModalDialog, StateSurface } from '../design/primitives';
import { extensionRegistry } from '../extensions';
import { BoardPanel, type CommandSender } from '../features/board/BoardPanel';
import { AgentWorkspace } from '../features/agents/AgentWorkspace';
import { createDesktopHost, hasHostCapability, type DesktopHost } from '../host';
import { TorqueProtocolClient, type TorqueCommand } from '../protocol';
import { useAppDispatch, useAppSelector } from './hooks';
import {
  createAppStore,
  selectAgentsState,
  selectAuxiliaryResponseState,
  selectConnection,
  selectGroupsState,
  selectNoticesState,
  selectOperationsState,
  selectRuntime,
  selectTasksState,
  selectWorkspaceState,
  selectWorkspaceUi,
  workspaceUiActions,
  type AppStore,
} from './store';
import { sanitizeClientError } from './clientDiagnostics';
import { effectiveBinding, eventMatchesBinding } from './preferences';
import styles from './App.module.css';

const PlanningWorkspace = lazy(() => import('../features/planning/PlanningWorkspace')
  .then((module) => ({ default: module.PlanningWorkspace })));
const ControlCenter = lazy(() => import('../features/control/ControlCenter')
  .then((module) => ({ default: module.ControlCenter })));

interface ToastMessage {
  id: number;
  level: 'info' | 'success' | 'error';
  message: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function displayName(value: unknown, fallback: string): string {
  const item = asRecord(value);
  return typeof item.name === 'string' && item.name ? item.name : fallback;
}

function textValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}

function noticeTime(value: unknown): string {
  if (typeof value !== 'number' && typeof value !== 'string') return '';
  const date = new Date(typeof value === 'number' && value < 10_000_000_000 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
}

interface WorkspaceShellProps {
  host: DesktopHost;
  sendCommand: CommandSender;
}

interface CommandPaletteEntry {
  id: string;
  label: string;
  keywords?: string;
  shortcut?: string;
  run?: () => void;
  href?: string;
  group?: string;
}

function fuzzyScore(value: string, query: string): number {
  const target = value.toLocaleLowerCase();
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return 0;
  const direct = target.indexOf(needle);
  if (direct >= 0) return direct;
  let targetIndex = 0;
  let gap = 0;
  for (const character of needle) {
    const found = target.indexOf(character, targetIndex);
    if (found < 0) return -1;
    gap += found - targetIndex;
    targetIndex = found + 1;
  }
  return 100 + gap;
}

function CommandPalette({ entries, onClose, onGroup }: { entries: CommandPaletteEntry[]; onClose: () => void; onGroup: (group: string) => void }) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const visible = useMemo(() => entries
    .map((entry, order) => ({ entry, order, score: fuzzyScore(`${entry.label} ${entry.keywords ?? ''}`, query) }))
    .filter((result) => result.score >= 0)
    .sort((left, right) => left.score - right.score || left.order - right.order)
    .map((result) => result.entry), [entries, query]);
  const boundedActiveIndex = Math.min(activeIndex, Math.max(0, visible.length - 1));
  const activate = (entry: CommandPaletteEntry | undefined) => {
    if (!entry) return;
    if (entry.href) window.location.assign(entry.href);
    else if (entry.group) onGroup(entry.group);
    else entry.run?.();
  };
  return <div className={styles.commandPalette} onKeyDown={(event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); setActiveIndex((current) => visible.length ? (current + 1) % visible.length : 0); }
    if (event.key === 'ArrowUp') { event.preventDefault(); setActiveIndex((current) => visible.length ? (current - 1 + visible.length) % visible.length : 0); }
    if (event.key === 'Enter') { event.preventDefault(); activate(visible[boundedActiveIndex]); }
    if (event.key === 'Escape') { event.preventDefault(); onClose(); }
  }}>
    <label className={styles.commandSearch}><span>⌕</span><input autoFocus type="search" role="combobox" aria-expanded="true" aria-controls="command-results" aria-activedescendant={visible[boundedActiveIndex] ? `command-${visible[boundedActiveIndex].id}` : undefined} value={query} onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); }} placeholder="Type a command, group, or destination" aria-label="Search commands" autoComplete="off" /></label>
    <div id="command-results" className={styles.commandResults} role="listbox" aria-label="Command results">
      {visible.length ? visible.map((entry, index) => entry.href
        ? <a id={`command-${entry.id}`} key={entry.id} href={entry.href} role="option" aria-label={entry.label} aria-selected={index === boundedActiveIndex} onMouseEnter={() => setActiveIndex(index)}><span>{entry.label}</span>{entry.shortcut ? <kbd>{entry.shortcut}</kbd> : null}</a>
        : <button id={`command-${entry.id}`} key={entry.id} type="button" role="option" aria-label={entry.label} aria-selected={index === boundedActiveIndex} onMouseEnter={() => setActiveIndex(index)} onClick={() => activate(entry)}><span>{entry.label}</span>{entry.shortcut ? <kbd>{entry.shortcut}</kbd> : null}</button>)
        : <StateSurface title="No matching commands" description="Try a panel name, create action, sync, or group." />}
    </div>
    <footer><span>↑↓ choose</span><span>Enter run</span><span>Esc close</span><strong>{visible.length} {visible.length === 1 ? 'result' : 'results'}</strong></footer>
  </div>;
}

type NativeMenuWindow = Window & {
  openAddGroup?: () => void;
  openAddArchitectModal?: () => void;
  openGlobalSettings?: () => void;
  openCheatsheet?: () => void;
  openWelcome?: () => void;
  openLogViewer?: () => void;
  detachActivePanel?: () => void;
  restartTerminalSupervisor?: () => void;
  torqueMainWindowBoundsChanged?: () => void;
  torqueDetachedWindowBoundsChanged?: () => void;
};

export function WorkspaceShell({ host, sendCommand }: WorkspaceShellProps) {
  const dispatch = useAppDispatch();
  const connection = useAppSelector(selectConnection);
  const runtime = useAppSelector(selectRuntime);
  const agentsState = useAppSelector(selectAgentsState);
  const tasksState = useAppSelector(selectTasksState);
  const operations = useAppSelector(selectOperationsState);
  const auxiliaryResponses = useAppSelector(selectAuxiliaryResponseState);
  const { records: groups } = useAppSelector(selectGroupsState);
  const notices = useAppSelector(selectNoticesState);
  const workspace = useAppSelector(selectWorkspaceState);
  const workspaceUi = useAppSelector(selectWorkspaceUi);
  const groupNames = Object.keys(groups);
  const activeGroup = typeof workspace.activeGroup === 'string' && groups[workspace.activeGroup]
    ? workspace.activeGroup
    : groupNames[0] ?? '';
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const query = new URLSearchParams(window.location.search);
  const [welcomeOpen, setWelcomeOpen] = useState(query.get('onboarding') === '1');
  const [addGroupOpen, setAddGroupOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [newGroupDirectory, setNewGroupDirectory] = useState('');
  const [renameGroup, setRenameGroup] = useState('');
  const [renameGroupName, setRenameGroupName] = useState('');
  const [inboxView, setInboxView] = useState<'alert' | 'notification'>('alert');
  const [inboxArchived, setInboxArchived] = useState(false);
  const toastSequence = useRef(0);
  const boundsTimer = useRef<number | null>(null);
  const sidebarResize = useRef<{ startX: number; startWidth: number } | null>(null);
  const reportedClientErrors = useRef<string[]>([]);
  const renderSamples = useRef<number[]>([]);
  const renderReportAt = useRef(0);
  const lastAuxiliary = connection.lastAuxiliaryFrame;
  const summary = asRecord(notices.summary);
  const unread = Number(summary.unread_total ?? summary.unread ?? 0);
  const noticeItems = Object.entries(notices.records)
    .map<Record<string, unknown> & { id: string }>(([id, value]) => ({ id, ...asRecord(value) }))
    .sort((a, b) => Number(b.created_at ?? 0) - Number(a.created_at ?? 0))
    .filter((notice) => !notice.notice_type || notice.notice_type === inboxView)
    .filter((notice) => inboxArchived || !Number(notice.archived_at ?? 0));
  const legacyUrl = new URL('/legacy/', window.location.origin).toString();
  const detachedPanel = query.get('panel');
  const detachedWindowLabel = query.get('window') ?? '';
  const persistedSidebarWidth = Number(workspace.sidebarWidth ?? 0);
  const [sidebarWidth, setSidebarWidth] = useState(Number.isFinite(persistedSidebarWidth) && persistedSidebarWidth > 0 ? persistedSidebarWidth : 188);
  const globalSettings = operations.globalSettings;
  const statusVisibility = {
    daemon_status: false, claude_usage: false, codex_usage: false, deploy: true,
    health: false, workload: false, tasks: true, attention: true,
    ...asRecord(globalSettings.status_bar_visibility),
  };
  const groupAgents = Object.values(agentsState.records).map(asRecord).filter((agent) => agent.group === activeGroup && !Number(agent.deleted_at ?? 0));
  const groupTasks = Object.values(tasksState.records).map(asRecord).filter((task) => task.group === activeGroup && task.lane !== 'Archived');
  const runningAgents = groupAgents.filter((agent) => ['running', 'working', 'busy'].includes(textValue(agent.status))).length;
  const attentionCount = groupAgents.filter((agent) => agent.needs_attention === true || textValue(agent.health_state) === 'blocked').length + unread;
  const deployState = asRecord(auxiliaryResponses['deploy_state:_'] ?? auxiliaryResponses['deploy_state:latest']);
  const healthState = textValue(operations.health.status, textValue(operations.health.overall_status, 'unknown'));

  const pushToast = useCallback((message: string, level: ToastMessage['level'] = 'info') => {
    const id = ++toastSequence.current;
    setToasts((current) => [...current.slice(-3), { id, message, level }]);
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4_500);
  }, []);

  const reportClientError = useCallback((source: string, value: unknown) => {
    const message = sanitizeClientError(value);
    const key = `${source}:${message}`;
    if (reportedClientErrors.current.includes(key)) return;
    const sent = sendCommand({
      cmd: 'operator_notice_report_client_error',
      title: 'React UI error',
      message,
      category: 'client',
      source: `react:${source}`,
      action_kind: 'open_inbox',
      action_payload: {},
      dedupe_key: key.slice(0, 180),
    });
    if (!sent) return;
    reportedClientErrors.current = [...reportedClientErrors.current.slice(-19), key];
  }, [sendCommand]);

  useEffect(() => {
    const startedAt = performance.now();
    const frame = window.requestAnimationFrame((paintedAt) => {
      renderSamples.current.push(Math.max(0, paintedAt - startedAt));
      if (renderSamples.current.length > 300) renderSamples.current.shift();
    });
    return () => window.cancelAnimationFrame(frame);
  });

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.hidden || connection.status !== 'connected' || !renderSamples.current.length) return;
      const sorted = [...renderSamples.current].sort((left, right) => left - right);
      const now = performance.now();
      const elapsedSeconds = Math.max(1, renderReportAt.current ? (now - renderReportAt.current) / 1_000 : 30);
      sendCommand({ cmd: 'report_frontend_render', render_per_s: renderSamples.current.length / elapsedSeconds, render_ms_p95: sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] ?? 0 });
      renderSamples.current = [];
      renderReportAt.current = now;
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [connection.status, sendCommand]);

  useEffect(() => {
    if (connection.status !== 'connected') return;
    const diagnostic = connection.diagnostics.at(-1);
    if (!diagnostic || diagnostic.kind === 'socket') return;
    reportClientError(`protocol:${diagnostic.kind}`, diagnostic.message);
  }, [connection.diagnostics, connection.status, reportClientError]);

  useEffect(() => {
    const onError = (event: ErrorEvent) => reportClientError('window', event.error ?? event.message);
    const onUnhandledRejection = (event: PromiseRejectionEvent) => reportClientError('promise', event.reason);
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onUnhandledRejection);
    return () => {
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onUnhandledRejection);
    };
  }, [reportClientError]);

  useEffect(() => {
    if (!lastAuxiliary) return;
    const message = typeof lastAuxiliary.message === 'string' ? lastAuxiliary.message : '';
    if (lastAuxiliary.type === 'error' && message) pushToast(message, 'error');
    if (lastAuxiliary.type === 'toast' && message) {
      pushToast(message, lastAuxiliary.level === 'success' ? 'success' : 'info');
    }
  }, [lastAuxiliary, pushToast]);

  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const isEditing = Boolean(target?.matches('input, textarea, select, [contenteditable="true"]'));
      const matches = (action: string, platform = false) => eventMatchesBinding(event, effectiveBinding(globalSettings, action), platform);
      if (matches('navigator.open', true)) {
        event.preventDefault();
        dispatch(workspaceUiActions.setCommandPaletteOpen(!workspaceUi.commandPaletteOpen));
        return;
      }
      if (isEditing || workspaceUi.commandPaletteOpen) return;
      const panel = ([['react.panel.board', 'board'], ['react.panel.agents', 'agents'], ['react.panel.planning', 'planning'], ['react.panel.control', 'control']] as const)
        .find(([action]) => matches(action));
      if (panel) { event.preventDefault(); dispatch(workspaceUiActions.setActivePanel(panel[1])); return; }
      if (event.key === '/') {
        event.preventDefault();
        window.dispatchEvent(new CustomEvent('torque:focus-board-search'));
      }
      if (matches('task.create')) { event.preventDefault(); dispatch(workspaceUiActions.setActivePanel('board')); dispatch(workspaceUiActions.setCreateTaskDialogOpen(true)); }
      if (matches('composer.focus')) { event.preventDefault(); dispatch(workspaceUiActions.setActivePanel('agents')); window.setTimeout(() => window.dispatchEvent(new CustomEvent('torque:focus-composer')), 0); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [dispatch, globalSettings, workspaceUi.commandPaletteOpen]);

  useEffect(() => {
    if (sidebarResize.current || !Number.isFinite(persistedSidebarWidth) || persistedSidebarWidth <= 0) return;
    setSidebarWidth(Math.max(150, Math.min(360, persistedSidebarWidth)));
  }, [persistedSidebarWidth]);

  const beginSidebarResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    sidebarResize.current = { startX: event.clientX, startWidth: sidebarWidth };
    const move = (pointer: PointerEvent) => {
      const active = sidebarResize.current;
      if (!active) return;
      setSidebarWidth(Math.max(150, Math.min(360, active.startWidth + pointer.clientX - active.startX)));
    };
    const end = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', end);
      sidebarResize.current = null;
      setSidebarWidth((current) => {
        const next = Math.round(current);
        sendCommand({ cmd: 'ui_set_workspace_sidebar_width', width: next });
        return next;
      });
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', end, { once: true });
  };

  const resizeSidebarFromKeyboard = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight' && event.key !== 'Home' && event.key !== 'End') return;
    event.preventDefault();
    const next = event.key === 'Home' ? 150 : event.key === 'End' ? 360 : Math.max(150, Math.min(360, sidebarWidth + (event.key === 'ArrowLeft' ? -10 : 10)));
    setSidebarWidth(next);
    sendCommand({ cmd: 'ui_set_workspace_sidebar_width', width: next });
  };

  useEffect(() => {
    if (connection.status === 'connected') sendCommand({ cmd: 'get_deploy_state' });
  }, [connection.reconnectCount, connection.status, sendCommand]);

  useEffect(() => {
    const target = window as Window & {
      torqueDetachedWindowClosed?: (panel: string, label: string) => void;
    };
    target.torqueDetachedWindowClosed = (panel, label) => {
      const current = asRecord(workspace.detachedPanels);
      const entry = asRecord(current[panel]);
      if (textValue(entry.label) && textValue(entry.label) !== label) return;
      const next = { ...current };
      delete next[panel];
      sendCommand({ cmd: 'ui_set_detached_panels', detached_panels: next });
    };
    return () => { delete target.torqueDetachedWindowClosed; };
  }, [sendCommand, workspace.detachedPanels]);

  const runCommand = useCallback((command: TorqueCommand) => {
    dispatch(workspaceUiActions.setCommandPaletteOpen(false));
    if (!sendCommand(command)) pushToast('Torque is not connected. Your change was not sent.', 'error');
  }, [dispatch, pushToast, sendCommand]);

  const selectGroup = (group: string) => {
    dispatch(workspaceUiActions.clearSelectedTasks());
    runCommand({ cmd: 'ui_select_group', group });
  };

  const runNoticeAction = (notice: Record<string, unknown>) => {
    const payload = asRecord(notice.action_payload);
    const kind = textValue(notice.action_kind);
    const taskId = textValue(notice.task_id, textValue(payload.task_id, textValue(payload.task)));
    const agentId = textValue(notice.agent_id, textValue(payload.agent_id, textValue(payload.id)));
    if (kind === 'open_task' && taskId) {
      dispatch(workspaceUiActions.setActivePanel('board'));
      dispatch(workspaceUiActions.setDetailTask(taskId));
    } else if (kind === 'open_agent' && agentId) {
      dispatch(workspaceUiActions.setActivePanel('agents'));
      dispatch(workspaceUiActions.setSelectedAgent(agentId));
    } else if (kind === 'open_panel') {
      const panel = textValue(payload.panel);
      if (['board', 'agents', 'planning', 'control'].includes(panel)) dispatch(workspaceUiActions.setActivePanel(panel as 'board' | 'agents' | 'planning' | 'control'));
    } else if (kind === 'open_settings') {
      dispatch(workspaceUiActions.setActivePanel('control'));
      dispatch(workspaceUiActions.setControlTab('settings'));
    } else if (kind === 'retry_board_sync' && taskId) {
      runCommand({ cmd: 'board_sync_task', task: taskId });
    }
    if (!Number(notice.read_at ?? 0)) runCommand({ cmd: 'operator_notice_mark_read', id: textValue(notice.id) });
  };

  const commandUnavailable = useCallback(() => pushToast('Torque is not connected. Your change was not sent.', 'error'), [pushToast]);

  useEffect(() => {
    const target = window as NativeMenuWindow;
    const openControl = (tab: 'mission' | 'activity' | 'settings' | 'help') => {
      dispatch(workspaceUiActions.setActivePanel('control'));
      dispatch(workspaceUiActions.setControlTab(tab));
    };
    const persistBounds = () => {
      if (!hasHostCapability(host, 'window-bounds')) return;
      if (boundsTimer.current !== null) window.clearTimeout(boundsTimer.current);
      boundsTimer.current = window.setTimeout(() => {
        void host.currentWindowBounds().then((bounds) => {
          if (!bounds) return;
          if (detachedPanel && detachedWindowLabel) {
            sendCommand({ cmd: 'ui_set_detached_panel_bounds', panel: detachedPanel, label: detachedWindowLabel, bounds });
          } else {
            sendCommand({ cmd: 'ui_set_window_bounds', window: 'main', bounds });
          }
        }).catch(() => undefined);
      }, 180);
    };
    target.openAddGroup = () => setAddGroupOpen(true);
    target.openAddArchitectModal = () => {
      dispatch(workspaceUiActions.setActivePanel('agents'));
      dispatch(workspaceUiActions.setCreateAgentKind('architect'));
    };
    target.openGlobalSettings = () => openControl('settings');
    target.openCheatsheet = () => openControl('help');
    target.openWelcome = () => setWelcomeOpen(true);
    target.openLogViewer = () => openControl('activity');
    target.detachActivePanel = () => {
      if (!hasHostCapability(host, 'detach-panel') || detachedPanel) return;
      const panel = workspaceUi.activePanel;
      const current = asRecord(workspace.detachedPanels);
      const existing = asRecord(current[panel]);
      if (textValue(existing.label)) {
        void host.focusWindow(textValue(existing.label));
        return;
      }
      const bounds = panel === 'board' ? { width: 1180, height: 760 } : { width: 1080, height: 740 };
      void host.detachPanel({ panel, bounds }).then((detached) => {
        sendCommand({ cmd: 'ui_set_detached_panels', detached_panels: { ...current, [panel]: { label: detached.label, bounds } } });
      }).catch(commandUnavailable);
    };
    target.restartTerminalSupervisor = () => {
      void host.confirm({
        title: 'Restart terminal supervisor?',
        message: 'Active terminal sessions will reconnect after the supervisor restarts.',
        confirmLabel: 'Restart',
        destructive: true,
      }).then((confirmed) => { if (confirmed) runCommand({ cmd: 'supervisor_restart' }); }).catch(commandUnavailable);
    };
    target.torqueMainWindowBoundsChanged = persistBounds;
    target.torqueDetachedWindowBoundsChanged = persistBounds;
    return () => {
      if (boundsTimer.current !== null) window.clearTimeout(boundsTimer.current);
      delete target.openAddGroup;
      delete target.openAddArchitectModal;
      delete target.openGlobalSettings;
      delete target.openCheatsheet;
      delete target.openWelcome;
      delete target.openLogViewer;
      delete target.detachActivePanel;
      delete target.restartTerminalSupervisor;
      delete target.torqueMainWindowBoundsChanged;
      delete target.torqueDetachedWindowBoundsChanged;
    };
  }, [commandUnavailable, detachedPanel, detachedWindowLabel, dispatch, host, runCommand, sendCommand, workspace.detachedPanels, workspaceUi.activePanel]);

  const closeCommandPalette = () => dispatch(workspaceUiActions.setCommandPaletteOpen(false));
  const openPanel = (panel: 'board' | 'agents' | 'planning' | 'control') => {
    closeCommandPalette();
    dispatch(workspaceUiActions.setActivePanel(panel));
  };
  const commandEntries: CommandPaletteEntry[] = [
    { id: 'board', label: 'Open Board', keywords: 'tasks lanes work', shortcut: 'B', run: () => openPanel('board') },
    { id: 'agents', label: 'Open Agents', keywords: 'workers engineers architects terminals', shortcut: 'A', run: () => openPanel('agents') },
    { id: 'planning', label: 'Open Planning', keywords: 'initiatives areas thinking decisions', shortcut: 'P', run: () => openPanel('planning') },
    { id: 'control', label: 'Open Control Center', keywords: 'mission activity history settings help', shortcut: 'O', run: () => openPanel('control') },
    { id: 'new-task', label: 'New Board task', keywords: 'create add work', shortcut: 'N', run: () => { openPanel('board'); dispatch(workspaceUiActions.setCreateTaskDialogOpen(true)); } },
    { id: 'new-architect', label: 'New Architect', keywords: 'create agent principal', run: () => { openPanel('agents'); dispatch(workspaceUiActions.setCreateAgentKind('architect')); } },
    { id: 'new-engineer', label: 'New Engineer', keywords: 'create agent lead', run: () => { openPanel('agents'); dispatch(workspaceUiActions.setCreateAgentKind('engineer')); } },
    { id: 'new-worker', label: 'New Worker', keywords: 'create agent task', run: () => { openPanel('agents'); dispatch(workspaceUiActions.setCreateAgentKind('worker')); } },
    { id: 'sync-group', label: 'Sync current group', keywords: `board external ${activeGroup}`, run: () => runCommand({ cmd: 'board_sync_group', group: activeGroup, force: true }) },
    ...groupNames.map((group) => ({ id: `group-${group}`, label: `Open group: ${group}`, keywords: 'workspace switch', group })),
    { id: 'classic', label: 'Open classic UI', keywords: 'legacy old', href: legacyUrl },
  ];

  if (detachedPanel === 'agents' || detachedPanel === 'engineer' || detachedPanel === 'terminal') {
    return (
      <main className={styles.detachedShell}>
        <AgentWorkspace
          group={activeGroup}
          host={host}
          sendCommand={sendCommand}
          onCommandUnavailable={commandUnavailable}
          terminalOnly={detachedPanel === 'terminal'}
        />
        <div className={styles.toastRegion} role="region" aria-label="Notifications" aria-live="polite">
          {toasts.map((toast) => <div key={toast.id} className={`${styles.toast} ${styles[`toast_${toast.level}`] ?? ''}`}>{toast.message}</div>)}
        </div>
      </main>
    );
  }

  if (detachedPanel === 'board' || detachedPanel === 'planning' || detachedPanel === 'control') {
    return <main className={styles.detachedShell}>
      {detachedPanel === 'board' ? <BoardPanel group={activeGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /> : null}
      {detachedPanel === 'planning' ? <Suspense fallback={<StateSurface title="Loading Planning" description="Preparing planning resources." />}><PlanningWorkspace group={activeGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /></Suspense> : null}
      {detachedPanel === 'control' ? <Suspense fallback={<StateSurface title="Loading Control Center" description="Preparing operational resources." />}><ControlCenter key={activeGroup} group={activeGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /></Suspense> : null}
      <div className={styles.toastRegion} role="region" aria-label="Notifications" aria-live="polite">{toasts.map((toast) => <div key={toast.id} className={`${styles.toast} ${styles[`toast_${toast.level}`] ?? ''}`}>{toast.message}</div>)}</div>
    </main>;
  }

  return (
    <main className={styles.shell}>
      <aside className={styles.sidebar} aria-label="Torque workspace navigation" style={{ width: sidebarWidth, minWidth: sidebarWidth }}>
        <header className={styles.brand}>
          <div className={styles.mark}>TQ</div>
          <div><strong>Torque</strong><span>{textValue(runtime.profile, 'default')}</span></div>
        </header>
        <nav className={styles.primaryNav} aria-label="Product areas">
          <button className={workspaceUi.activePanel === 'board' ? styles.navActive : ''} aria-current={workspaceUi.activePanel === 'board' ? 'page' : undefined} onClick={() => dispatch(workspaceUiActions.setActivePanel('board'))}><span>▦</span> Board <kbd>B</kbd></button>
          <button className={workspaceUi.activePanel === 'agents' ? styles.navActive : ''} aria-current={workspaceUi.activePanel === 'agents' ? 'page' : undefined} onClick={() => dispatch(workspaceUiActions.setActivePanel('agents'))}><span>⌁</span> Agents <kbd>A</kbd></button>
          <button className={workspaceUi.activePanel === 'planning' ? styles.navActive : ''} aria-current={workspaceUi.activePanel === 'planning' ? 'page' : undefined} onClick={() => dispatch(workspaceUiActions.setActivePanel('planning'))}><span>◇</span> Planning <kbd>P</kbd></button>
          <button className={workspaceUi.activePanel === 'control' ? styles.navActive : ''} aria-current={workspaceUi.activePanel === 'control' ? 'page' : undefined} onClick={() => dispatch(workspaceUiActions.setActivePanel('control'))}><span>◎</span> Control <kbd>O</kbd></button>
        </nav>
        <section className={styles.groupNav} aria-labelledby="groups-heading">
          <header><h2 id="groups-heading">Groups</h2><span>{groupNames.length}</span><button aria-label="Add group" onClick={() => setAddGroupOpen(true)}>＋</button></header>
          <div>
            {groupNames.map((group, index) => {
              const settings = asRecord(groups[group]);
              const color = typeof settings.color === 'string' ? settings.color : '#6172f3';
              return <div key={group} className={styles.groupRow}><button className={group === activeGroup ? styles.groupActive : ''} onClick={() => selectGroup(group)}><i style={{ background: color }} />{displayName(groups[group], group)}</button><ActionMenu label={`${group} group options`}><ActionMenuItem onAction={() => { setRenameGroup(group); setRenameGroupName(group); }}>Rename</ActionMenuItem><ActionMenuItem onAction={() => { dispatch(workspaceUiActions.setActivePanel('control')); dispatch(workspaceUiActions.setControlTab('settings')); }}>Settings</ActionMenuItem><ActionMenuItem onAction={() => runCommand({ cmd: 'move_group', group, before: index === 0 ? '' : groupNames[0] })} isDisabled={groupNames.length < 2}>{index === 0 ? 'Move to bottom' : 'Move to top'}</ActionMenuItem><ActionMenuItem onAction={() => { void host.confirm({ title: `Remove ${group}?`, message: 'The group can be removed only when its agents and protected state allow it.', confirmLabel: 'Remove', destructive: true }).then((confirmed) => { if (confirmed) runCommand({ cmd: 'remove_group', group }); }); }}>Remove…</ActionMenuItem></ActionMenu></div>;
            })}
          </div>
        </section>
        <footer className={styles.sidebarFooter}>
          <span className={`${styles.connectionDot} ${styles[`connection_${connection.status}`] ?? ''}`} />
          <span>{connection.status}</span>
          <small>{host.kind}{extensionRegistry.panels.length ? ` · ${extensionRegistry.panels.length} ext` : ''}</small>
        </footer>
        <div className={styles.sidebarResize} role="separator" aria-orientation="vertical" aria-label="Resize workspace sidebar" aria-valuemin={150} aria-valuemax={360} aria-valuenow={Math.round(sidebarWidth)} tabIndex={0} onKeyDown={resizeSidebarFromKeyboard} onPointerDown={beginSidebarResize} />
      </aside>

      <section className={styles.workspace}>
        <header className={styles.globalChrome}>
          <button className={styles.commandTrigger} onClick={() => dispatch(workspaceUiActions.setCommandPaletteOpen(true))}><span>⌕</span> Search commands <kbd>⌘K</kbd></button>
          <span className={styles.chromeSpacer} />
          <a href={legacyUrl} className={styles.legacyLink}>Classic UI</a>
          <ActionMenu label="Workspace actions" trigger={<Button tone="quiet">•••</Button>}>
            <ActionMenuItem onAction={() => (window as NativeMenuWindow).detachActivePanel?.()} isDisabled={!hasHostCapability(host, 'detach-panel')}>Detach current panel</ActionMenuItem>
            <ActionMenuItem onAction={() => { dispatch(workspaceUiActions.setActivePanel('control')); dispatch(workspaceUiActions.setControlTab('activity')); }}>Open logs</ActionMenuItem>
            <ActionMenuItem onAction={() => { if (hasHostCapability(host, 'reveal-log-directory')) void host.revealLogDirectory(); }} isDisabled={!hasHostCapability(host, 'reveal-log-directory')}>Reveal log directory</ActionMenuItem>
            <ActionMenuItem onAction={() => { void host.confirm({ title: 'Restart Torque daemon?', message: 'Live sessions may briefly reconnect.', confirmLabel: 'Restart', destructive: true }).then((confirmed) => { if (confirmed) runCommand({ cmd: 'restart' }); }); }}>Restart daemon…</ActionMenuItem>
            <ActionMenuItem onAction={() => { void host.confirm({ title: 'Stop Torque daemon?', message: 'The UI will disconnect until Torque is launched again.', confirmLabel: 'Stop', destructive: true }).then((confirmed) => { if (confirmed) runCommand({ cmd: 'stop' }); }); }}>Stop daemon…</ActionMenuItem>
          </ActionMenu>
          <DialogTrigger>
            <Button tone="quiet" aria-label={`Inbox${unread ? `, ${unread} unread` : ''}`} onPress={() => runCommand({ cmd: 'operator_notices_list', notice_type: inboxView, include_archived: inboxArchived, limit: 100, offset: 0 })}>♢{unread ? <span className={styles.unreadBadge}>{unread > 99 ? '99+' : unread}</span> : null}</Button>
            <Popover className={styles.inboxPopover ?? ''} placement="bottom end">
              <Dialog className={styles.inboxDialog ?? ''}>
                <header><Heading slot="title">Inbox</Heading><Button tone="quiet" onPress={() => runCommand({ cmd: 'operator_notices_mark_all_read' })} isDisabled={!unread}>Mark all read</Button></header>
                <div className={styles.inboxToolbar}><div role="tablist" aria-label="Inbox view"><button role="tab" aria-selected={inboxView === 'alert'} onClick={() => { setInboxView('alert'); runCommand({ cmd: 'operator_notices_list', notice_type: 'alert', include_archived: inboxArchived, limit: 100, offset: 0 }); }}>Alerts <span>{Number(summary.open_alerts ?? 0)}</span></button><button role="tab" aria-selected={inboxView === 'notification'} onClick={() => { setInboxView('notification'); runCommand({ cmd: 'operator_notices_list', notice_type: 'notification', include_archived: inboxArchived, limit: 100, offset: 0 }); }}>Notifications <span>{Number(summary.unread_notifications ?? 0)}</span></button></div><label><input type="checkbox" checked={inboxArchived} onChange={(event) => { setInboxArchived(event.target.checked); runCommand({ cmd: 'operator_notices_list', notice_type: inboxView, include_archived: event.target.checked, limit: 100, offset: 0 }); }} />Show archived</label></div>
                <div className={styles.noticeList}>
                  {noticeItems.length ? noticeItems.map((notice) => {
                    const isUnread = !Number(notice.read_at ?? 0);
                    const archived = Boolean(Number(notice.archived_at ?? 0));
                    const resolved = Boolean(Number(notice.resolved_at ?? 0));
                    return <article key={notice.id} className={isUnread ? styles.noticeUnread : ''} data-severity={textValue(notice.severity)}><header><span>{textValue(notice.title, 'Torque notice')}</span><time>{noticeTime(notice.created_at)}</time></header><p>{textValue(notice.message)}</p><small>{textValue(notice.category, textValue(notice.source))}{resolved ? ' · resolved' : ''}</small><footer>{notice.action_kind ? <Button tone="primary" onPress={() => runNoticeAction(notice)}>Open</Button> : null}{isUnread ? <Button tone="quiet" onPress={() => runCommand({ cmd: 'operator_notice_mark_read', id: notice.id })}>Read</Button> : null}{!resolved && inboxView === 'alert' ? <Button tone="quiet" onPress={() => runCommand({ cmd: 'operator_notice_resolve', id: notice.id })}>Resolve</Button> : null}{!archived ? <Button tone="quiet" onPress={() => runCommand({ cmd: 'operator_notice_dismiss', id: notice.id })}>Dismiss</Button> : null}<Button tone="quiet" onPress={() => runCommand({ cmd: archived ? 'operator_notice_restore' : 'operator_notice_archive', id: notice.id })}>{archived ? 'Restore' : 'Archive'}</Button></footer></article>;
                  }) : <StateSurface title="Inbox clear" description="Operator notices and requests will appear here." />}
                </div>
              </Dialog>
            </Popover>
          </DialogTrigger>
        </header>

        {connection.status === 'disconnected' ? <div className={styles.connectionBanner}>Connection lost. Torque will reconnect automatically.</div> : null}
        {workspaceUi.activePanel === 'board' ? <BoardPanel group={activeGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /> : null}
        {workspaceUi.activePanel === 'agents' ? <AgentWorkspace group={activeGroup} host={host} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /> : null}
        {workspaceUi.activePanel === 'planning' ? <Suspense fallback={<StateSurface title="Loading Planning" description="Preparing planning resources." />}><PlanningWorkspace group={activeGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /></Suspense> : null}
        {workspaceUi.activePanel === 'control' ? <Suspense fallback={<StateSurface title="Loading Control Center" description="Preparing operational resources." />}><ControlCenter key={activeGroup} group={activeGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /></Suspense> : null}
        <footer className={styles.statusBar} aria-label="Workspace status">
          {statusVisibility.daemon_status ? <span data-state={connection.status}>● Daemon {connection.status}</span> : null}
          {statusVisibility.deploy ? <span>Deploy {textValue(deployState.status, textValue(deployState.state, '—'))}{Number(deployState.commits_behind ?? deployState.behind ?? 0) ? ` +${Number(deployState.commits_behind ?? deployState.behind)}` : ''}</span> : null}
          {statusVisibility.health ? <span>Health {healthState}</span> : null}
          {statusVisibility.workload ? <span>Agents {runningAgents} run · {groupAgents.length} total</span> : null}
          {statusVisibility.tasks ? <span>Tasks {groupTasks.filter((task) => task.lane !== 'Done').length} active</span> : null}
          {statusVisibility.attention ? <button onClick={() => { dispatch(workspaceUiActions.setActivePanel('control')); dispatch(workspaceUiActions.setControlTab('mission')); }}>Attention {attentionCount}</button> : null}
          {statusVisibility.claude_usage ? <span>Claude {textValue(asRecord(operations.health.claude_usage).percent, '—')}%</span> : null}
          {statusVisibility.codex_usage ? <span>Codex {textValue(asRecord(operations.health.codex_usage).percent, '—')}%</span> : null}
        </footer>
      </section>

      <ModalDialog title="Command palette" description="Jump to a workspace or run a common action." size="medium" isOpen={workspaceUi.commandPaletteOpen} onOpenChange={(open) => dispatch(workspaceUiActions.setCommandPaletteOpen(open))}>
        <CommandPalette key={workspaceUi.commandPaletteOpen ? 'open' : 'closed'} entries={commandEntries} onClose={closeCommandPalette} onGroup={selectGroup} />
      </ModalDialog>

      <ModalDialog title="Welcome to Torque" description="A local workspace for planning, dispatching, and supervising agent work." size="large" isOpen={welcomeOpen} onOpenChange={setWelcomeOpen}>
        <div className={styles.welcome}>
          <ol><li><strong>Choose a group</strong><span>Groups scope tasks, agents, planning, and defaults.</span></li><li><strong>Create or import work</strong><span>Use Planning for durable context and Board for executable tasks.</span></li><li><strong>Supervise by exception</strong><span>Mission Control, Inbox, and agent terminals surface what needs you.</span></li></ol>
          <footer><Button tone="quiet" onPress={() => { void host.openExternal('https://github.com/aleksanderarruda/torque'); }}>Documentation</Button><span /><Button tone="quiet" onPress={() => setAddGroupOpen(true)}>Create a group</Button><Button tone="primary" onPress={() => { if (sendCommand({ cmd: 'first_run_complete' })) setWelcomeOpen(false); else commandUnavailable(); }}>Start using Torque</Button></footer>
        </div>
      </ModalDialog>

      <ModalDialog title="New group" description="Create an isolated workspace for a project or stream of work." size="small" isOpen={addGroupOpen} onOpenChange={setAddGroupOpen}>
        <form className={styles.nativeForm} onSubmit={(event) => { event.preventDefault(); const group = newGroupName.trim(); if (!group) return; runCommand({ cmd: 'add_group', group, default_directory: newGroupDirectory.trim() }); setNewGroupName(''); setNewGroupDirectory(''); setAddGroupOpen(false); }}>
          <label>Name<input autoFocus value={newGroupName} onChange={(event) => setNewGroupName(event.target.value)} /></label>
          <label>Default directory<input value={newGroupDirectory} onChange={(event) => setNewGroupDirectory(event.target.value)} placeholder="Optional" /></label>
          <footer><Button tone="quiet" onPress={() => setAddGroupOpen(false)}>Cancel</Button><Button tone="primary" type="submit" isDisabled={!newGroupName.trim()}>Create group</Button></footer>
        </form>
      </ModalDialog>

      <ModalDialog title="Rename group" description={renameGroup} size="small" isOpen={Boolean(renameGroup)} onOpenChange={(open) => { if (!open) setRenameGroup(''); }}>
        <form className={styles.nativeForm} onSubmit={(event) => { event.preventDefault(); const next = renameGroupName.trim(); if (!next || !renameGroup) return; runCommand({ cmd: 'rename_group', group: renameGroup, new_name: next }); setRenameGroup(''); }}>
          <label>New name<input autoFocus value={renameGroupName} onChange={(event) => setRenameGroupName(event.target.value)} /></label>
          <footer><Button tone="quiet" onPress={() => setRenameGroup('')}>Cancel</Button><Button tone="primary" type="submit" isDisabled={!renameGroupName.trim() || renameGroupName.trim() === renameGroup}>Rename</Button></footer>
        </form>
      </ModalDialog>


      <div className={styles.toastRegion} role="region" aria-label="Notifications" aria-live="polite">
        {toasts.map((toast) => <div key={toast.id} className={`${styles.toast} ${styles[`toast_${toast.level}`] ?? ''}`}>{toast.message}</div>)}
      </div>
    </main>
  );
}

// Kept as a compatibility export for Phase 1 imports while the product shell
// replaces the protocol-only screen.
export const FoundationShell = WorkspaceShell;

interface AppProps {
  appStore?: AppStore;
  host?: DesktopHost;
  clientFactory?: (store: AppStore) => TorqueProtocolClient;
}

export function App({ appStore, host, clientFactory }: AppProps) {
  const resolvedStore = useMemo(() => appStore ?? createAppStore(), [appStore]);
  const resolvedHost = useMemo(() => host ?? createDesktopHost(), [host]);
  const client = useMemo(
    () => clientFactory?.(resolvedStore) ?? new TorqueProtocolClient({ store: resolvedStore }),
    [clientFactory, resolvedStore],
  );

  useEffect(() => {
    client.start();
    return () => client.stop();
  }, [client]);

  return (
    <Provider store={resolvedStore}>
      <WorkspaceShell host={resolvedHost} sendCommand={(command) => client.sendCommand(command)} />
    </Provider>
  );
}
