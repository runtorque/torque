import { DeployStatus } from './DeployStatus';
import { CommandConfirmation, type ConfirmedCommand } from './CommandConfirmation';
import { SettingsNavigationProvider } from './SettingsNavigationGuard';
import { useSettingsNavigation } from './settingsNavigation';
import type { WorkspaceNavigation } from './workspaceNavigation';
import { releaseWindow, savedWindowBounds } from '../host/windowState';
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Provider } from 'react-redux';
import { Dialog, DialogTrigger, Heading, Popover } from 'react-aria-components';

import { ActionMenu, ActionMenuItem, Button, ModalDialog, StateSurface } from '../design/primitives';
import { RelayStatusIndicator } from '../features/relay/RelayStatus';
import { statusVisibilityEnabled } from '../features/relay/relayStatusModel';
import { extensionRegistry } from '../extensions';
import { BoardPanel, type CommandSender } from '../features/board/BoardPanel';
import { AgentWorkspace } from '../features/agents/AgentWorkspace';
import { createDesktopHost, hasHostCapability, type DesktopHost } from '../host';
import { TorqueProtocolClient, type TorqueCommand } from '../protocol';
import { useAppDispatch, useAppSelector } from './hooks';
import {
  createAppStore,
  selectAgentsState,
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
import { detachedNavigation } from './workspaceNavigation';
import { useWorkspaceNavigation } from './useWorkspaceNavigation';
import { RenderTelemetry } from './RenderTelemetry';
import { sanitizeClientError } from './clientDiagnostics';
import { effectiveBinding, eventMatchesBinding, formatBinding } from './preferences';
import { fixedNavigatorScope, type NavigatorScope } from './shortcutBindings';
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
  category?: 'panel';
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

function CommandPalette({ entries, scope, onClose, onGroup, onLink }: { scope: NavigatorScope; entries: CommandPaletteEntry[]; onClose: () => void; onGroup: (group: string) => void; onLink: (href: string) => void }) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const visible = useMemo(() => entries
    .filter((entry) => scope === 'all' || (scope === 'groups' ? Boolean(entry.group) : entry.category === 'panel'))
    .map((entry, order) => ({ entry, order, score: fuzzyScore(`${entry.label} ${entry.keywords ?? ''}`, query) }))
    .filter((result) => result.score >= 0)
    .sort((left, right) => left.score - right.score || left.order - right.order)
    .map((result) => result.entry), [entries, query, scope]);
  const boundedActiveIndex = Math.min(activeIndex, Math.max(0, visible.length - 1));
  const activate = (entry: CommandPaletteEntry | undefined) => {
    if (!entry) return;
    if (entry.href) onLink(entry.href);
    else if (entry.group) onGroup(entry.group);
    else entry.run?.();
  };
  return <div className={styles.commandPalette} onKeyDown={(event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); setActiveIndex((current) => visible.length ? (current + 1) % visible.length : 0); }
    if (event.key === 'ArrowUp') { event.preventDefault(); setActiveIndex((current) => visible.length ? (current - 1 + visible.length) % visible.length : 0); }
    if (event.key === 'Enter') { event.preventDefault(); activate(visible[boundedActiveIndex]); }
    if (event.key === 'Escape') { event.preventDefault(); onClose(); }
  }}>
    <label className={styles.commandSearch}><span>⌕</span><input autoFocus type="search" role="combobox" aria-expanded="true" aria-controls="command-results" aria-activedescendant={visible[boundedActiveIndex] ? `command-${visible[boundedActiveIndex].id}` : undefined} value={query} onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); }} placeholder={scope === 'groups' ? 'Search groups' : scope === 'panels' ? 'Search panels' : 'Type a command, group, or destination'} aria-label={scope === 'groups' ? 'Search groups' : scope === 'panels' ? 'Search panels' : 'Search commands'} autoComplete="off" /></label>
    <div id="command-results" className={styles.commandResults} role="listbox" aria-label="Command results">
      {visible.length ? visible.map((entry, index) => entry.href
        ? <a id={`command-${entry.id}`} key={entry.id} href={entry.href} onClick={(event) => { if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); onLink(entry.href!); } }} role="option" aria-label={entry.label} aria-selected={index === boundedActiveIndex} onMouseEnter={() => setActiveIndex(index)}><span>{entry.label}</span>{entry.shortcut ? <kbd>{entry.shortcut}</kbd> : null}</a>
        : <button id={`command-${entry.id}`} key={entry.id} type="button" role="option" aria-label={entry.label} aria-selected={index === boundedActiveIndex} onMouseEnter={() => setActiveIndex(index)} onClick={() => activate(entry)}><span>{entry.label}</span>{entry.shortcut ? <kbd>{entry.shortcut}</kbd> : null}</button>)
        : <StateSurface title="No matching commands" description="Try a panel name, create action, sync, or group." />}
    </div>
    <footer><span>↑↓ choose</span><span>Enter run</span><span>Esc close</span><strong>{visible.length} {visible.length === 1 ? 'result' : 'results'}</strong></footer>
  </div>;
}

type NativeMenuCallbacks = {
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

type NativeMenuWindow = Window & NativeMenuCallbacks;
function installNativeMenuCallbacks(callbacks: Required<NativeMenuCallbacks>) {
  const target = window as NativeMenuWindow;
  const entries = Object.entries(callbacks) as [keyof NativeMenuCallbacks, () => void][];
  for (const [key, callback] of entries) target[key] = callback;
  return () => { for (const [key, callback] of entries) if (target[key] === callback) delete target[key]; };
}

export function WorkspaceShell(props: WorkspaceShellProps) {
  return <SettingsNavigationProvider><WorkspaceShellContent {...props} /></SettingsNavigationProvider>;
}

function WorkspaceShellContent({ host, sendCommand }: WorkspaceShellProps) {
  const dispatch = useAppDispatch();
  const connection = useAppSelector(selectConnection);
  const runtime = useAppSelector(selectRuntime);
  const agentsState = useAppSelector(selectAgentsState);
  const tasksState = useAppSelector(selectTasksState);
  const operations = useAppSelector(selectOperationsState);
  const { records: groups } = useAppSelector(selectGroupsState);
  const notices = useAppSelector(selectNoticesState);
  const workspace = useAppSelector(selectWorkspaceState);
  const workspaceUi = useAppSelector(selectWorkspaceUi);
  const { request: requestNavigation, retainedGroup } = useSettingsNavigation();
  const navigatePanel = useCallback((panel: WorkspaceNavigation['activePanel'], tab?: WorkspaceNavigation['controlTab'], after?: () => void) => {
    dispatch(workspaceUiActions.setCommandPaletteOpen(false));
    const apply = () => { dispatch(workspaceUiActions.setActivePanel(panel)); if (tab) dispatch(workspaceUiActions.setControlTab(tab)); after?.(); };
    if (panel === workspaceUi.activePanel && (!tab || tab === workspaceUi.controlTab)) apply();
    else requestNavigation(apply);
  }, [dispatch, requestNavigation, workspaceUi.activePanel, workspaceUi.controlTab]);
  const groupNames = Object.keys(groups);
  const activeGroup = typeof workspace.activeGroup === 'string' && groups[workspace.activeGroup]
    ? workspace.activeGroup
    : groupNames[0] ?? '';
  const controlGroup = retainedGroup ?? activeGroup;
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const query = new URLSearchParams(window.location.search);
  const [welcomeOpen, setWelcomeOpen] = useState(query.get('onboarding') === '1');
  const [addGroupOpen, setAddGroupOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<ConfirmedCommand | null>(null);
  const [newGroupName, setNewGroupName] = useState('');
  const [newGroupDirectory, setNewGroupDirectory] = useState('');
  const [reorderGroup, setReorderGroup] = useState('');
  const [groupBefore, setGroupBefore] = useState('');
  const [renameGroup, setRenameGroup] = useState('');
  const [renameGroupName, setRenameGroupName] = useState('');
  const [inboxView, setInboxView] = useState<'alert' | 'notification'>('alert');
  const [inboxArchived, setInboxArchived] = useState(false);
  const toastSequence = useRef(0);
  const boundsTimer = useRef<number | null>(null);
  const sidebarResize = useRef<{ startX: number; startWidth: number } | null>(null);
  const reportedClientErrors = useRef<string[]>([]);
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
  const navigation = useWorkspaceNavigation(Boolean(detachedPanel));
  const detachedWindowLabel = query.get('window') ?? '';
  const activeDetachedWindow = asRecord(asRecord(workspace.detachedPanels)[workspaceUi.activePanel]);
  const activeDetachedLabel = !detachedPanel && hasHostCapability(host, 'detach-panel') && !(workspaceUi.activePanel === 'control' && retainedGroup) ? textValue(activeDetachedWindow.label) : '';
  const reattachActive = async () => {
    try {
      await host.reattachWindow(activeDetachedLabel);
      const next = releaseWindow(asRecord(workspace.detachedPanels), workspaceUi.activePanel, activeDetachedLabel);
      if (!sendCommand({ cmd: 'ui_set_detached_panels', detached_panels: next })) commandUnavailable();
    } catch { commandUnavailable(); }
  };
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
      if (event.defaultPrevented || event.isComposing || event.repeat) return;
      const fixedScope = fixedNavigatorScope(event);
      if (fixedScope) { event.preventDefault(); dispatch(workspaceUiActions.openNavigator(fixedScope)); return; }
      const target = event.target as HTMLElement | null;
      const isEditing = Boolean(target?.closest?.('input, textarea, select, [contenteditable="true"]'));
      const matches = (action: string, platform = false) => eventMatchesBinding(event, effectiveBinding(globalSettings, action), platform);
      if (matches('navigator.open', true)) {
        event.preventDefault();
        if (workspaceUi.commandPaletteOpen && workspaceUi.commandPaletteScope === 'all') dispatch(workspaceUiActions.setCommandPaletteOpen(false));
        else dispatch(workspaceUiActions.openNavigator('all'));
        return;
      }
      if (isEditing || workspaceUi.commandPaletteOpen || target?.closest?.('[role="dialog"], [role="menu"]')) return;
      const panel = ([['react.panel.board', 'board'], ['panel.toggle', 'board'], ['react.panel.agents', 'agents'], ['react.panel.planning', 'planning'], ['react.panel.control', 'control']] as const)
        .find(([action]) => matches(action));
      if (panel) { event.preventDefault(); navigatePanel(panel[1]); return; }
      if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        window.dispatchEvent(new CustomEvent('torque:focus-board-search'));
      }
      if (matches('task.create')) { event.preventDefault(); navigatePanel('board', undefined, () => dispatch(workspaceUiActions.setCreateTaskDialogOpen(true))); }
      if (matches('composer.focus')) { event.preventDefault(); navigatePanel('agents', undefined, () => { dispatch(workspaceUiActions.setAgentsViewMode('live')); window.setTimeout(() => window.dispatchEvent(new CustomEvent('torque:focus-composer')), 0); }); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [dispatch, navigatePanel, globalSettings, workspaceUi.commandPaletteOpen, workspaceUi.commandPaletteScope]);

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
    const target = window as Window & {
      torqueDetachedWindowClosed?: (panel: string, label: string) => void;
    };
    target.torqueDetachedWindowClosed = (panel, label) => {
      const current = asRecord(workspace.detachedPanels);
      const entry = asRecord(current[panel]);
      if (textValue(entry.label) && textValue(entry.label) !== label) return;
      const next = releaseWindow(current, panel, label);
      sendCommand({ cmd: 'ui_set_detached_panels', detached_panels: next });
    };
    return () => { delete target.torqueDetachedWindowClosed; };
  }, [sendCommand, workspace.detachedPanels]);

  const reconciledWindows = useRef('');
  useEffect(() => {
    if (detachedPanel || host.kind !== 'tauri' || connection.status !== 'connected' || connection.expectedSeq === null || !hasHostCapability(host, 'list-detached-windows')) return;
    const key = `${connection.lastConnectedAt}:${connection.reconnectCount}`;
    if (reconciledWindows.current === key) return;
    let cancelled = false;
    const current = asRecord(workspace.detachedPanels);
    void host.listDetachedWindows().then((windows) => {
      if (cancelled || !Array.isArray(windows)) return;
      const live = new Set(windows.map((item) => item.label));
      let next = current;
      for (const [panel, value] of Object.entries(current)) {
        const label = textValue(asRecord(value).label);
        if (label && !live.has(label)) next = releaseWindow(next, panel, label);
      }
      if (next !== current && !sendCommand({ cmd: 'ui_set_detached_panels', detached_panels: next })) return;
      reconciledWindows.current = key;
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [connection.expectedSeq, connection.lastConnectedAt, connection.reconnectCount, connection.status, detachedPanel, host, sendCommand, workspace.detachedPanels]);

  const runCommand = useCallback((command: TorqueCommand) => {
    dispatch(workspaceUiActions.setCommandPaletteOpen(false));
    if (!sendCommand(command)) pushToast('Torque is not connected. Your change was not sent.', 'error');
  }, [dispatch, pushToast, sendCommand]);

  const selectGroup = (group: string) => {
    closeCommandPalette();
    const apply = () => { dispatch(workspaceUiActions.clearSelectedTasks()); runCommand({ cmd: 'ui_select_group', group }); };
    if (group === activeGroup && group === controlGroup) apply(); else requestNavigation(apply);
  };
  const openGroupSettings = (group: string) => {
    const apply = () => { if (group !== activeGroup) runCommand({ cmd: 'ui_select_group', group }); dispatch(workspaceUiActions.setActivePanel('control')); dispatch(workspaceUiActions.setControlTab('settings')); };
    if (group === controlGroup) navigatePanel('control', 'settings'); else requestNavigation(apply);
  };

  const runNoticeAction = (notice: Record<string, unknown>) => {
    const payload = asRecord(notice.action_payload);
    const kind = textValue(notice.action_kind);
    const taskId = textValue(notice.task_id, textValue(payload.task_id, textValue(payload.task)));
    const agentId = textValue(notice.agent_id, textValue(payload.agent_id, textValue(payload.id)));
    const markRead = () => { if (!Number(notice.read_at ?? 0)) runCommand({ cmd: 'operator_notice_mark_read', id: textValue(notice.id) }); };
    if (kind === 'open_task' && taskId) navigatePanel('board', undefined, () => { dispatch(workspaceUiActions.setDetailTask(taskId)); markRead(); });
    else if (kind === 'open_agent' && agentId) navigatePanel('agents', undefined, () => { dispatch(workspaceUiActions.setSelectedAgent(agentId)); markRead(); });
    else if (kind === 'open_panel') {
      const panel = textValue(payload.panel);
      if (['board', 'agents', 'planning', 'control'].includes(panel)) navigatePanel(panel as WorkspaceNavigation['activePanel'], undefined, markRead);
    } else if (kind === 'open_settings') navigatePanel('control', 'settings', markRead);
    else { if (kind === 'retry_board_sync' && taskId) runCommand({ cmd: 'board_sync_task', task: taskId }); markRead(); }
  };

  const commandUnavailable = useCallback(() => pushToast('Torque is not connected. Your change was not sent.', 'error'), [pushToast]);

  useEffect(() => {
    const openControl = (tab: 'mission' | 'activity' | 'logs' | 'settings' | 'help') => {
      navigatePanel('control', tab);
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
    const removeNativeMenus = installNativeMenuCallbacks({
      openAddGroup: () => setAddGroupOpen(true),
      openAddArchitectModal: () => {
        navigatePanel('agents', undefined, () => dispatch(workspaceUiActions.setCreateAgentKind('architect')));
      },
      openGlobalSettings: () => openControl('settings'),
      openCheatsheet: () => openControl('help'),
      openWelcome: () => setWelcomeOpen(true),
      openLogViewer: () => openControl('logs'),
      detachActivePanel: () => {
        if (!hasHostCapability(host, 'detach-panel') || detachedPanel) return;
        const panel = workspaceUi.activePanel;
        const current = asRecord(workspace.detachedPanels);
        const existing = asRecord(current[panel]);
        if (textValue(existing.label)) {
          void host.focusWindow(textValue(existing.label));
          return;
        }
        const bounds = savedWindowBounds(existing, panel === 'board' ? { width: 1180, height: 760 } : { width: 1080, height: 740 });
        requestNavigation(() => { void host.detachPanel({ panel, bounds, ...(panel === 'control' ? { section: workspaceUi.controlTab } : {}) }).then((detached) => {
          sendCommand({ cmd: 'ui_set_detached_panels', detached_panels: { ...current, [panel]: { label: detached.label, bounds } } });
        }).catch(commandUnavailable); });
      },
      restartTerminalSupervisor: () => {
        setConfirmation({
          title: 'Restart terminal supervisor?',
          message: 'Active terminal sessions will reconnect after the supervisor restarts.',
          confirmLabel: 'Restart',
          command: { cmd: 'supervisor_restart' },
        });
      },
      torqueMainWindowBoundsChanged: persistBounds,
      torqueDetachedWindowBoundsChanged: persistBounds,
    });
    return () => {
      if (boundsTimer.current !== null) window.clearTimeout(boundsTimer.current);
      removeNativeMenus();
    };
  }, [commandUnavailable, detachedPanel, detachedWindowLabel, dispatch, host, navigatePanel, requestNavigation, runCommand, sendCommand, workspace.detachedPanels, workspaceUi.activePanel, workspaceUi.controlTab]);

  const confirmationDialog = confirmation ? <CommandConfirmation key={`${confirmation.command.cmd}:${textValue(confirmation.command.group)}`} request={confirmation} sendCommand={sendCommand} onClose={() => setConfirmation(null)} /> : null;

  const closeCommandPalette = () => dispatch(workspaceUiActions.setCommandPaletteOpen(false));
  const openPanel = (panel: 'board' | 'agents' | 'planning' | 'control') => {
    closeCommandPalette();
    navigatePanel(panel);
  };
  const bindingHint = (action: string) => formatBinding(effectiveBinding(globalSettings, action));
  const commandEntries: CommandPaletteEntry[] = [
    { id: 'board', label: 'Open Board', keywords: 'tasks lanes work', category: 'panel', shortcut: `${bindingHint('react.panel.board')} / ${bindingHint('panel.toggle')}`, run: () => openPanel('board') },
    { id: 'agents', label: 'Open Agents', keywords: 'workers engineers architects terminals', category: 'panel', shortcut: bindingHint('react.panel.agents'), run: () => openPanel('agents') },
    { id: 'planning', label: 'Open Planning', keywords: 'initiatives areas thinking decisions', category: 'panel', shortcut: bindingHint('react.panel.planning'), run: () => openPanel('planning') },
    { id: 'control', label: 'Open Control Center', keywords: 'mission activity history settings help', category: 'panel', shortcut: bindingHint('react.panel.control'), run: () => openPanel('control') },
    ...([['mission', 'Mission Control'], ['activity', 'Activity'], ['logs', 'Logs'], ['chat', 'Chat'], ['pipelines', 'Pipelines'], ['history', 'History'], ['context', 'Context'], ['actions', 'Actions'], ['catalog', 'Catalog'], ['settings', 'Settings'], ['help', 'Help']] as const).map(([tab, label]) => ({ id: `control-${tab}`, label: `Open ${label}`, category: 'panel' as const, run: () => navigatePanel('control', tab) })),
    { id: 'new-task', label: 'New Board task', keywords: 'create add work', shortcut: bindingHint('task.create'), run: () => { navigatePanel('board', undefined, () => dispatch(workspaceUiActions.setCreateTaskDialogOpen(true))); } },
    { id: 'new-architect', label: 'New Architect', keywords: 'create agent principal', run: () => { navigatePanel('agents', undefined, () => dispatch(workspaceUiActions.setCreateAgentKind('architect'))); } },
    { id: 'new-engineer', label: 'New Engineer', keywords: 'create agent lead', run: () => { navigatePanel('agents', undefined, () => dispatch(workspaceUiActions.setCreateAgentKind('engineer'))); } },
    { id: 'new-worker', label: 'New Worker', keywords: 'create agent task', run: () => { navigatePanel('agents', undefined, () => dispatch(workspaceUiActions.setCreateAgentKind('worker'))); } },
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
        {confirmationDialog}
        <div className={styles.toastRegion} role="region" aria-label="Notifications" aria-live="polite">
          {toasts.map((toast) => <div key={toast.id} className={`${styles.toast} ${styles[`toast_${toast.level}`] ?? ''}`}>{toast.message}</div>)}
        </div>
      </main>
    );
  }

  if (detachedPanel === 'board' || detachedPanel === 'planning' || detachedPanel === 'control') {
    return <main className={styles.detachedShell}>
      {detachedPanel === 'board' ? <BoardPanel host={host} group={activeGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /> : null}
      {detachedPanel === 'planning' ? <Suspense fallback={<StateSurface title="Loading Planning" description="Preparing planning resources." />}><PlanningWorkspace key={activeGroup} group={activeGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /></Suspense> : null}
      {retainedGroup && retainedGroup !== activeGroup ? <div role="status">Settings for {retainedGroup} remain open. <Button onPress={() => requestNavigation(() => {})}>Switch to {activeGroup}</Button></div> : null}
      {detachedPanel === 'control' ? <Suspense fallback={<StateSurface title="Loading Control Center" description="Preparing operational resources." />}><ControlCenter host={host} key={controlGroup} group={controlGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /></Suspense> : null}
      {confirmationDialog}

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
          <button className={workspaceUi.activePanel === 'board' && !activeDetachedLabel ? styles.navActive : ''} aria-current={workspaceUi.activePanel === 'board' && !activeDetachedLabel ? 'page' : undefined} onClick={() => navigatePanel('board')}><span>▦</span> Board <kbd>{bindingHint('react.panel.board')}</kbd></button>
          <button className={workspaceUi.activePanel === 'agents' ? styles.navActive : ''} aria-current={workspaceUi.activePanel === 'agents' ? 'page' : undefined} onClick={() => navigatePanel('agents')}><span>⌁</span> Agents <kbd>{bindingHint('react.panel.agents')}</kbd></button>
          <button className={workspaceUi.activePanel === 'planning' && !activeDetachedLabel ? styles.navActive : ''} aria-current={workspaceUi.activePanel === 'planning' && !activeDetachedLabel ? 'page' : undefined} onClick={() => navigatePanel('planning')}><span>◇</span> Planning <kbd>{bindingHint('react.panel.planning')}</kbd></button>
          <button className={workspaceUi.activePanel === 'control' && !activeDetachedLabel ? styles.navActive : ''} aria-current={workspaceUi.activePanel === 'control' && !activeDetachedLabel ? 'page' : undefined} onClick={() => navigatePanel('control')}><span>◎</span> Control <kbd>{bindingHint('react.panel.control')}</kbd></button>
        </nav>
        <section className={styles.groupNav} aria-labelledby="groups-heading">
          <header><h2 id="groups-heading">Groups</h2><span>{groupNames.length}</span><button aria-label="Add group" onClick={() => setAddGroupOpen(true)}>＋</button></header>
          <div>
            {groupNames.map((group, index) => {
              const settings = asRecord(groups[group]);
              const color = typeof settings.color === 'string' ? settings.color : '#6172f3';
              return <div key={group} className={styles.groupRow} draggable onDragStart={(event) => event.dataTransfer.setData('application/x-torque-group', group)} onDragOver={(event) => { if (event.dataTransfer.types.includes('application/x-torque-group')) event.preventDefault(); }} onDrop={(event) => { const moved = event.dataTransfer.getData('application/x-torque-group'); if (moved && moved !== group && groupNames.includes(moved)) { event.preventDefault(); runCommand({ cmd: 'move_group', group: moved, before: group }); } }}><button className={group === activeGroup ? styles.groupActive : ''} onClick={() => selectGroup(group)}><i style={{ background: color }} />{displayName(groups[group], group)}</button><ActionMenu label={`${group} group options`}><ActionMenuItem onAction={() => { setRenameGroup(group); setRenameGroupName(group); }}>Rename</ActionMenuItem><ActionMenuItem onAction={() => openGroupSettings(group)}>Settings</ActionMenuItem><ActionMenuItem onAction={() => { setReorderGroup(group); setGroupBefore(''); }}>Move group…</ActionMenuItem><ActionMenuItem onAction={() => runCommand({ cmd: 'move_group', group, before: index === 0 ? '' : groupNames[0] })} isDisabled={groupNames.length < 2}>{index === 0 ? 'Move to bottom' : 'Move to top'}</ActionMenuItem><ActionMenuItem onAction={() => { setConfirmation({ title: `Remove ${group}?`, message: `Removing this group also removes its agents and child terminals and closes their sessions. The group's settings are deleted.`, confirmLabel: 'Remove', command: { cmd: 'remove_group', group } }); }}>Remove…</ActionMenuItem></ActionMenu></div>;
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
          <button className={styles.commandTrigger} onClick={() => dispatch(workspaceUiActions.setCommandPaletteOpen(true))}><span>⌕</span> Search commands <kbd>{bindingHint('navigator.open')}</kbd></button>
          <span className={styles.chromeSpacer} />
          <a href={legacyUrl} className={styles.legacyLink} onClick={(event) => { if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); requestNavigation(() => window.location.assign(legacyUrl)); } }}>Classic UI</a>
          <ActionMenu label="Workspace actions" trigger={<Button tone="quiet" aria-label="Workspace actions">•••</Button>}>
            <ActionMenuItem onAction={() => (window as NativeMenuWindow).detachActivePanel?.()} isDisabled={!hasHostCapability(host, 'detach-panel')}>Detach current panel</ActionMenuItem>
            <ActionMenuItem onAction={() => navigatePanel('control', 'logs')}>Open logs</ActionMenuItem>
            <ActionMenuItem onAction={() => { if (hasHostCapability(host, 'reveal-log-directory')) void host.revealLogDirectory(); }} isDisabled={!hasHostCapability(host, 'reveal-log-directory')}>Reveal log directory</ActionMenuItem>
            <ActionMenuItem onAction={() => { setConfirmation({ title: 'Restart Torque daemon?', message: 'Live sessions may briefly reconnect.', confirmLabel: 'Restart', command: { cmd: 'restart' } }); }}>Restart daemon…</ActionMenuItem>
            <ActionMenuItem onAction={() => { setConfirmation({ title: 'Stop Torque daemon?', message: 'The UI will disconnect until Torque is launched again.', confirmLabel: 'Stop', command: { cmd: 'stop' } }); }}>Stop daemon…</ActionMenuItem>
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

        {navigation.error ? <div className={styles.connectionBanner} role="alert">Last workspace save is unconfirmed: {navigation.error} <Button tone="quiet" onPress={navigation.retry}>Retry workspace save</Button></div> : null}
        {connection.status === 'disconnected' ? <div className={styles.connectionBanner}>Connection lost. Torque will reconnect automatically.</div> : null}
        {workspaceUi.activePanel === 'board' && !activeDetachedLabel ? <BoardPanel host={host} group={activeGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /> : null}
        {activeDetachedLabel ? <StateSurface title={`${workspaceUi.activePanel[0]?.toUpperCase()}${workspaceUi.activePanel.slice(1)} workspace detached`} description="This workspace is open in its native window." action={<><Button onPress={() => { void host.focusWindow(activeDetachedLabel).catch(commandUnavailable); }}>Focus detached workspace</Button><Button onPress={() => { void reattachActive(); }}>Reattach workspace</Button></>} /> : null}
        {workspaceUi.activePanel === 'agents' ? <div className={styles.agentWorkspaceHost} hidden={Boolean(activeDetachedLabel)}><AgentWorkspace active={!activeDetachedLabel} group={activeGroup} host={host} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /></div> : null}
        {workspaceUi.activePanel === 'planning' && !activeDetachedLabel ? <Suspense fallback={<StateSurface title="Loading Planning" description="Preparing planning resources." />}><PlanningWorkspace key={activeGroup} group={activeGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /></Suspense> : null}
        {retainedGroup && textValue(activeDetachedWindow.label) ? <div role="status">Settings remain in this window while another Control Center window is open.</div> : null}
        {retainedGroup && retainedGroup !== activeGroup ? <div role="status">Settings for {retainedGroup} remain open. <Button onPress={() => requestNavigation(() => {})}>Switch to {activeGroup}</Button></div> : null}
        {workspaceUi.activePanel === 'control' && !activeDetachedLabel ? <Suspense fallback={<StateSurface title="Loading Control Center" description="Preparing operational resources." />}><ControlCenter host={host} key={controlGroup} group={controlGroup} sendCommand={sendCommand} onCommandUnavailable={commandUnavailable} /></Suspense> : null}
        <footer className={styles.statusBar} aria-label="Workspace status">
          {statusVisibilityEnabled(statusVisibility.daemon_status) ? <span data-state={connection.status}>● Daemon {connection.status}</span> : null}
          <RelayStatusIndicator connection={operations.relayConnection} visible={statusVisibility.daemon_status} />
          <DeployStatus group={activeGroup} enabled={statusVisibilityEnabled(statusVisibility.deploy)} ready={connection.status === 'connected' && connection.expectedSeq !== null && !connection.awaitingResync} reconnect={connection.reconnectCount} onOpen={() => navigatePanel('board')} />
          {statusVisibility.health ? <span>Health {healthState}</span> : null}
          {statusVisibility.workload ? <span>Agents {runningAgents} run · {groupAgents.length} total</span> : null}
          {statusVisibility.tasks ? <span>Tasks {groupTasks.filter((task) => task.lane !== 'Done').length} active</span> : null}
          {statusVisibility.attention ? <button onClick={() => navigatePanel('control', 'mission')}>Attention {attentionCount}</button> : null}
          {statusVisibility.claude_usage ? <span>Claude {textValue(asRecord(operations.health.claude_usage).percent, '—')}%</span> : null}
          {statusVisibility.codex_usage ? <span>Codex {textValue(asRecord(operations.health.codex_usage).percent, '—')}%</span> : null}
        </footer>
      </section>

      <ModalDialog title={workspaceUi.commandPaletteScope === 'groups' ? 'Group navigator' : workspaceUi.commandPaletteScope === 'panels' ? 'Panel navigator' : 'Command palette'} description="Jump to a workspace or run a common action." size="medium" isOpen={workspaceUi.commandPaletteOpen} onOpenChange={(open) => dispatch(workspaceUiActions.setCommandPaletteOpen(open))}>
        <CommandPalette key={`${workspaceUi.commandPaletteOpen}:${workspaceUi.commandPaletteScope}`} scope={workspaceUi.commandPaletteScope} entries={commandEntries} onLink={(href) => { closeCommandPalette(); requestNavigation(() => window.location.assign(href)); }} onClose={closeCommandPalette} onGroup={selectGroup} />
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

      <ModalDialog title="Move group" description={reorderGroup} size="small" isOpen={Boolean(reorderGroup)} onOpenChange={(open) => { if (!open) setReorderGroup(''); }}>
        <form onSubmit={(event) => { event.preventDefault(); runCommand({ cmd: 'move_group', group: reorderGroup, before: groupBefore }); setReorderGroup(''); }}><label>Group position<select value={groupBefore} onChange={(event) => setGroupBefore(event.target.value)}><option value="">At end</option>{groupNames.filter((name) => name !== reorderGroup).map((name) => <option key={name} value={name}>Before {name}</option>)}</select></label><Button type="submit" tone="primary">Move group</Button></form>
      </ModalDialog>

      <ModalDialog title="Rename group" description={renameGroup} size="small" isOpen={Boolean(renameGroup)} onOpenChange={(open) => { if (!open) setRenameGroup(''); }}>
        <form className={styles.nativeForm} onSubmit={(event) => { event.preventDefault(); const next = renameGroupName.trim(); if (!next || !renameGroup) return; runCommand({ cmd: 'rename_group', group: renameGroup, new_name: next }); setRenameGroup(''); }}>
          <label>New name<input autoFocus value={renameGroupName} onChange={(event) => setRenameGroupName(event.target.value)} /></label>
          <footer><Button tone="quiet" onPress={() => setRenameGroup('')}>Cancel</Button><Button tone="primary" type="submit" isDisabled={!renameGroupName.trim() || renameGroupName.trim() === renameGroup}>Rename</Button></footer>
        </form>
      </ModalDialog>


      {confirmationDialog}

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
  const resolvedStore = useMemo(() => appStore ?? createAppStore(detachedNavigation(window.location.search)), [appStore]);
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
      <RenderTelemetry><WorkspaceShell host={resolvedHost} sendCommand={(command) => client.sendCommand(command)} /></RenderTelemetry>
    </Provider>
  );
}
