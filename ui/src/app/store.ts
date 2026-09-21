import {
  configureStore,
  createSelector,
  createSlice,
  type PayloadAction,
} from '@reduxjs/toolkit';

import {
  applyDeltaOperation,
  emptyServerProjection,
  hydrateProjection,
  type ServerProjectionState,
} from '../protocol/projection';
import type {
  AuxiliaryFrame,
  DeltaFrame,
  StateFrame,
} from '../protocol/types';

export type ConnectionStatus = 'idle' | 'connecting' | 'connected' | 'disconnected';

export interface ProtocolDiagnostic {
  at: number;
  kind: 'parse' | 'compatibility' | 'socket';
  message: string;
}

export interface ConnectionState {
  status: ConnectionStatus;
  expectedSeq: number | null;
  awaitingResync: boolean;
  reconnectCount: number;
  resyncCount: number;
  lastInboundAt: number | null;
  lastConnectedAt: number | null;
  lastDisconnectedAt: number | null;
  diagnostics: ProtocolDiagnostic[];
  lastAuxiliaryFrame: AuxiliaryFrame | null;
}

export interface WorkspaceUiState {
  activePanel: 'board' | 'agents' | 'planning' | 'control';
  controlTab: 'mission' | 'activity' | 'history' | 'context' | 'actions' | 'catalog' | 'settings' | 'help';
  commandPaletteOpen: boolean;
  detailTaskId: string | null;
  focusedTaskId: string | null;
  selectedTaskIds: string[];
  collapsedTaskIds: string[];
  createLane: string | null;
  createTaskDialogOpen: boolean;
  selectedAgentId: string | null;
  selectedTerminalId: string | null;
  agentsViewMode: 'live' | 'activity';
  createAgentKind: 'architect' | 'engineer' | 'worker' | 'terminal' | null;
}

function initialWorkspaceUiState(): WorkspaceUiState {
  return {
    activePanel: 'board',
    controlTab: 'mission',
    commandPaletteOpen: false,
    detailTaskId: null,
    focusedTaskId: null,
    selectedTaskIds: [],
    collapsedTaskIds: [],
    createLane: null,
    createTaskDialogOpen: false,
    selectedAgentId: null,
    selectedTerminalId: null,
    agentsViewMode: 'live',
    createAgentKind: null,
  };
}

function initialConnectionState(): ConnectionState {
  return {
    status: 'idle',
    expectedSeq: null,
    awaitingResync: false,
    reconnectCount: 0,
    resyncCount: 0,
    lastInboundAt: null,
    lastConnectedAt: null,
    lastDisconnectedAt: null,
    diagnostics: [],
    lastAuxiliaryFrame: null,
  };
}

function appendDiagnostic(state: ConnectionState, diagnostic: ProtocolDiagnostic): void {
  state.diagnostics.push(diagnostic);
  if (state.diagnostics.length > 20) state.diagnostics.shift();
}

function auxiliaryResponseTarget(frame: AuxiliaryFrame): string {
  for (const key of ['id', 'agent_id', 'cell_id', 'architect_id', 'engineer_id', 'task_id', 'topic_id', 'linked_target_ref', 'scope_ref', 'proposal_id', 'group', 'group_name']) {
    const value = frame[key];
    if (typeof value === 'string' || typeof value === 'number') return String(value);
  }
  for (const containerKey of ['status', 'record', 'agent', 'task', 'entry', 'proposal', 'agent_class']) {
    const value = frame[containerKey];
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
    const record = value as Record<string, unknown>;
    for (const key of ['agent_id', 'cell_id', 'architect_id', 'engineer_id', 'id', 'class_id']) {
      const target = record[key];
      if (typeof target === 'string' || typeof target === 'number') return String(target);
    }
  }
  return '_';
}

const connectionSlice = createSlice({
  name: 'connection',
  initialState: initialConnectionState(),
  reducers: {
    connecting(state) {
      state.status = 'connecting';
    },
    connected(state, action: PayloadAction<{ at: number; reconnect: boolean }>) {
      state.status = 'connected';
      state.lastConnectedAt = action.payload.at;
      if (action.payload.reconnect) state.reconnectCount += 1;
    },
    disconnected(state, action: PayloadAction<{ at: number; reason?: string }>) {
      state.status = 'disconnected';
      state.lastDisconnectedAt = action.payload.at;
      state.awaitingResync = false;
      if (action.payload.reason) {
        appendDiagnostic(state, {
          at: action.payload.at,
          kind: 'socket',
          message: action.payload.reason,
        });
      }
    },
    inboundObserved(state, action: PayloadAction<number>) {
      state.lastInboundAt = action.payload;
    },
    snapshotAccepted(state, action: PayloadAction<StateFrame>) {
      state.expectedSeq = action.payload.seq + 1;
      state.awaitingResync = false;
    },
    deltaAccepted(state, action: PayloadAction<DeltaFrame>) {
      state.expectedSeq = action.payload.seq + 1;
    },
    resyncRequested(state, action: PayloadAction<{ at: number; reason: string }>) {
      if (!state.awaitingResync) state.resyncCount += 1;
      state.awaitingResync = true;
      appendDiagnostic(state, {
        at: action.payload.at,
        kind: 'compatibility',
        message: action.payload.reason,
      });
    },
    protocolError(state, action: PayloadAction<{ at: number; message: string }>) {
      appendDiagnostic(state, {
        at: action.payload.at,
        kind: 'parse',
        message: action.payload.message,
      });
    },
    auxiliaryFrameReceived(state, action: PayloadAction<AuxiliaryFrame>) {
      state.lastAuxiliaryFrame = action.payload;
    },
    reset() {
      return initialConnectionState();
    },
  },
});

const projectionSlice = createSlice({
  name: 'projection',
  initialState: emptyServerProjection(),
  reducers: {
    snapshotReceived(_state, action: PayloadAction<StateFrame>) {
      return hydrateProjection(action.payload);
    },
    deltaReceived(state, action: PayloadAction<DeltaFrame>) {
      for (const operation of action.payload.ops) applyDeltaOperation(state, operation);
      state.seq = action.payload.seq;
    },
    focusReceived(state, action: PayloadAction<AuxiliaryFrame>) {
      const frame = action.payload;
      if ('active_session_id' in frame) state.data.active_session_id = frame.active_session_id;
      if ('current_window_id' in frame) state.data.current_window_id = frame.current_window_id;
    },
    taskDetailReceived(state, action: PayloadAction<AuxiliaryFrame>) {
      const frame = action.payload;
      const id = typeof frame.id === 'string' ? frame.id : '';
      const task = frame.task;
      if (!id || !task || typeof task !== 'object' || Array.isArray(task)) return;
      const tasks = state.data.board_tasks;
      const records = tasks && typeof tasks === 'object' && !Array.isArray(tasks)
        ? tasks as Record<string, unknown>
        : {};
      const existing = records[id];
      records[id] = {
        ...(existing && typeof existing === 'object' && !Array.isArray(existing)
          ? existing
          : {}),
        ...task as Record<string, unknown>,
        _detail_version: (
          existing && typeof existing === 'object' && !Array.isArray(existing)
            ? Number((existing as Record<string, unknown>)._detail_version) || 0
            : 0
        ) + 1,
      };
      state.data.board_tasks = records;
    },
    auxiliaryResourceReceived(state, action: PayloadAction<AuxiliaryFrame>) {
      const frame = action.payload;
      const replace = (key: string, value: unknown) => {
        if (value !== undefined) state.data[key] = value;
      };
      const replaceCollection = (key: string, value: unknown) => {
        if (!Array.isArray(value)) {
          replace(key, value);
          return;
        }
        const collection: Record<string, unknown> = {};
        value.forEach((item, index) => {
          if (!item || typeof item !== 'object' || Array.isArray(item)) return;
          const id = (item as Record<string, unknown>).id;
          collection[typeof id === 'string' && id ? id : String(index)] = item;
        });
        replace(key, collection);
      };
      const upsertResource = (key: string, value: unknown) => {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return;
        const payload = value as Record<string, unknown>;
        const id = typeof payload.id === 'string' ? payload.id : '';
        if (!id) return;
        const current = state.data[key];
        const collection = current && typeof current === 'object' && !Array.isArray(current)
          ? current as Record<string, unknown> : {};
        if (payload.archived || payload.deleted) delete collection[id];
        else {
          const existing = collection[id];
          collection[id] = {
            ...(existing && typeof existing === 'object' && !Array.isArray(existing)
              ? existing : {}),
            ...payload,
          };
        }
        state.data[key] = collection;
      };
      // Keep durable, keyed copies of command responses. Several classic
      // workflows issue related requests together (for example worktree diff
      // + merge preflight); a single `lastAuxiliaryFrame` loses whichever
      // response arrives first and makes a complete React surface impossible.
      // The latest frame remains available for compatibility, while feature
      // panels consume the stable per-type/per-target entries below.
      const target = auxiliaryResponseTarget(frame);
      const responses = state.data.auxiliary_responses
        && typeof state.data.auxiliary_responses === 'object'
        && !Array.isArray(state.data.auxiliary_responses)
        ? state.data.auxiliary_responses as Record<string, unknown>
        : {};
      responses[`${frame.type}:${target}`] = frame;
      responses[`${frame.type}:latest`] = frame;
      state.data.auxiliary_responses = responses;
      switch (frame.type) {
        case 'initiative_list': {
          const records: Record<string, unknown> = {};
          for (const item of Array.isArray(frame.initiatives) ? frame.initiatives : []) {
            if (item && typeof item === 'object' && !Array.isArray(item)) {
              const id = (item as Record<string, unknown>).id;
              if (typeof id === 'string') records[id] = item;
            }
          }
          replace('initiatives', records);
          break;
        }
        case 'initiative_created':
        case 'initiative_updated':
        case 'initiative_archived':
          upsertResource('initiatives', frame.initiative);
          break;
        case 'area_list': {
          const records: Record<string, unknown> = {};
          for (const item of Array.isArray(frame.areas) ? frame.areas : []) {
            if (item && typeof item === 'object' && !Array.isArray(item)) {
              const id = (item as Record<string, unknown>).id;
              if (typeof id === 'string') records[id] = item;
            }
          }
          replace('planning_areas', records);
          break;
        }
        case 'area_created':
        case 'area_updated':
        case 'area_archived':
          upsertResource('planning_areas', frame.area);
          break;
        case 'scratchpad_note_list':
          replaceCollection('thinking_scratchpad_notes', frame.notes ?? frame.scratchpad_notes);
          break;
        case 'scratchpad_note_created':
        case 'scratchpad_note_updated':
        case 'scratchpad_note_archived':
          upsertResource('thinking_scratchpad_notes', frame.note);
          break;
        case 'idea_brief_list':
          replaceCollection('idea_briefs', frame.idea_briefs);
          break;
        case 'idea_brief_created':
        case 'idea_brief_updated':
        case 'idea_brief_refined':
        case 'idea_brief_parked':
        case 'idea_brief_archived':
          upsertResource('idea_briefs', frame.idea_brief);
          break;
        case 'decisions_snapshot':
          replace('decisions', frame.decisions);
          break;
        case 'pending_hires_snapshot':
          replace('pending_hires', frame.pending_hires);
          break;
        case 'archived_tasks': {
          const group = typeof frame.group === 'string' ? frame.group : '_';
          const current = state.data.board_tasks_archived;
          const archived = current && typeof current === 'object' && !Array.isArray(current)
            ? current as Record<string, unknown> : {};
          archived[group] = frame.board_tasks ?? {};
          state.data.board_tasks_archived = archived;
          break;
        }
        case 'schedule_list':
          replaceCollection('schedules', frame.schedules);
          break;
        case 'engineer_journal_snapshot':
          replace('engineer_journal', frame.engineer_journal);
          replace('engineer_worklog', frame.engineer_worklog);
          replace('engineer_streams', frame.engineer_streams);
          break;
        case 'architect_journal_entries': {
          const architectId = typeof frame.architect_id === 'string' ? frame.architect_id : '';
          if (architectId) {
            const current = state.data.architect_journals;
            const journals = current && typeof current === 'object' && !Array.isArray(current)
              ? current as Record<string, unknown> : {};
            journals[architectId] = Array.isArray(frame.entries) ? frame.entries : [];
            state.data.architect_journals = journals;
          }
          break;
        }
        case 'mcp_calls': {
          const cellId = typeof frame.cell_id === 'string' ? frame.cell_id : typeof frame.agent_id === 'string' ? frame.agent_id : '';
          if (cellId) {
            const current = state.data.mcp_calls;
            const calls = current && typeof current === 'object' && !Array.isArray(current)
              ? current as Record<string, unknown> : {};
            calls[cellId] = Array.isArray(frame.calls) ? frame.calls : [];
            state.data.mcp_calls = calls;
          }
          break;
        }
        case 'agent_class_status':
        case 'agent_class_assignment': {
          const status = frame.status;
          if (status && typeof status === 'object' && !Array.isArray(status)) {
            const agentId = (status as Record<string, unknown>).agent_id;
            if (typeof agentId === 'string' && agentId) {
              const current = state.data.agent_class_status_by_agent;
              const statuses = current && typeof current === 'object' && !Array.isArray(current)
                ? current as Record<string, unknown> : {};
              statuses[agentId] = status;
              state.data.agent_class_status_by_agent = statuses;
            }
          }
          break;
        }
        case 'agent_class_audit': {
          const agentId = typeof frame.agent_id === 'string' ? frame.agent_id : '';
          if (agentId) {
            const current = state.data.agent_class_audit_by_agent;
            const audits = current && typeof current === 'object' && !Array.isArray(current)
              ? current as Record<string, unknown> : {};
            audits[agentId] = Array.isArray(frame.events) ? frame.events : [];
            state.data.agent_class_audit_by_agent = audits;
          }
          break;
        }
        case 'actions':
        case 'roles':
        case 'templates':
        case 'specializations':
          replace(frame.type, frame[frame.type]);
          break;
        case 'agent_classes':
          replace('agent_classes', frame.classes);
          replace('agent_class_issues', frame.issues);
          replace('agent_class_authoring_contract', frame.authoring_contract);
          replace('agent_class_capability_catalog', frame.capability_catalog);
          break;
        case 'agent_class_save':
        case 'agent_class_archive':
        case 'agent_class_delete':
          replace('agent_classes', frame.classes);
          replace('agent_class_issues', frame.registry_issues);
          break;
        case 'events_page':
          replace('panel_events', frame.events);
          break;
        case 'operator_notices': {
          const current = state.data.operator_notices;
          const notices = Number(frame.offset ?? 0) > 0 && current && typeof current === 'object' && !Array.isArray(current)
            ? { ...(current as Record<string, unknown>) } : {};
          for (const item of Array.isArray(frame.notices) ? frame.notices : []) {
            if (item && typeof item === 'object' && !Array.isArray(item)) {
              const id = (item as Record<string, unknown>).id;
              if (typeof id === 'string') notices[id] = item;
            }
          }
          state.data.operator_notices = notices;
          replace('operator_notice_summary', frame.summary);
          break;
        }
        case 'operator_notice':
          upsertResource('operator_notices', frame.notice);
          replace('operator_notice_summary', frame.summary);
          break;
        case 'operator_notices_marked_read': {
          const current = state.data.operator_notices;
          if (current && typeof current === 'object' && !Array.isArray(current)) {
            for (const notice of Object.values(current as Record<string, unknown>)) {
              if (notice && typeof notice === 'object' && !Array.isArray(notice)) (notice as Record<string, unknown>).read_at = Date.now() / 1_000;
            }
          }
          replace('operator_notice_summary', frame.summary);
          break;
        }
        case 'global_settings':
          replace('global_settings', frame.settings);
          replace('relay_config', frame.relay_config);
          break;
        case 'group_settings': {
          const group = typeof frame.group === 'string' ? frame.group : '';
          const current = state.data.group_settings;
          const settings = current && typeof current === 'object' && !Array.isArray(current)
            ? { ...(current as Record<string, unknown>) } : {};
          if (group) settings[group] = frame.settings;
          state.data.group_settings = settings;
          if (frame.roles) replace('roles', frame.roles);
          if (frame.templates) replace('templates', frame.templates);
          if (frame.actions) replace('actions', frame.actions);
          break;
        }
        case 'ai_settings':
          replace('ai_settings', frame.settings ?? frame);
          break;
        case 'system_health_metrics':
          replace('system_health_metrics', frame);
          break;
        case 'mission_control_summary':
          replace('mission_control_summary', frame);
          break;
        case 'supervisor_sessions':
          replace('supervisor_sessions', frame);
          break;
        case 'relay_test_result':
          replace('relay_test_result', frame);
          break;
        default:
          break;
      }
      state.data.phase4_last_response_type = frame.type;
    },
    reset() {
      return emptyServerProjection();
    },
  },
});

const workspaceUiSlice = createSlice({
  name: 'workspaceUi',
  initialState: initialWorkspaceUiState(),
  reducers: {
    setActivePanel(state, action: PayloadAction<WorkspaceUiState['activePanel']>) {
      state.activePanel = action.payload;
    },
    setControlTab(state, action: PayloadAction<WorkspaceUiState['controlTab']>) {
      state.controlTab = action.payload;
    },
    setCommandPaletteOpen(state, action: PayloadAction<boolean>) {
      state.commandPaletteOpen = action.payload;
    },
    setDetailTask(state, action: PayloadAction<string | null>) {
      state.detailTaskId = action.payload;
    },
    setFocusedTask(state, action: PayloadAction<string | null>) {
      state.focusedTaskId = action.payload;
    },
    toggleSelectedTask(state, action: PayloadAction<{ id: string; additive: boolean }>) {
      const { id, additive } = action.payload;
      if (!additive) {
        state.selectedTaskIds = state.selectedTaskIds.length === 1
          && state.selectedTaskIds[0] === id ? [] : [id];
        return;
      }
      state.selectedTaskIds = state.selectedTaskIds.includes(id)
        ? state.selectedTaskIds.filter((taskId) => taskId !== id)
        : [...state.selectedTaskIds, id];
    },
    clearSelectedTasks(state) {
      state.selectedTaskIds = [];
    },
    toggleTaskCollapsed(state, action: PayloadAction<string>) {
      const id = action.payload;
      state.collapsedTaskIds = state.collapsedTaskIds.includes(id)
        ? state.collapsedTaskIds.filter((taskId) => taskId !== id)
        : [...state.collapsedTaskIds, id];
    },
    setCreateLane(state, action: PayloadAction<string | null>) {
      state.createLane = action.payload;
    },
    setCreateTaskDialogOpen(state, action: PayloadAction<boolean>) {
      state.createTaskDialogOpen = action.payload;
    },
    setSelectedAgent(state, action: PayloadAction<string | null>) {
      state.selectedAgentId = action.payload;
      state.selectedTerminalId = null;
    },
    setSelectedTerminal(state, action: PayloadAction<string | null>) {
      state.selectedTerminalId = action.payload;
    },
    setAgentsViewMode(state, action: PayloadAction<WorkspaceUiState['agentsViewMode']>) {
      state.agentsViewMode = action.payload;
    },
    setCreateAgentKind(state, action: PayloadAction<WorkspaceUiState['createAgentKind']>) {
      state.createAgentKind = action.payload;
    },
  },
});

export const connectionActions = connectionSlice.actions;
export const projectionActions = projectionSlice.actions;
export const workspaceUiActions = workspaceUiSlice.actions;

export function createAppStore() {
  return configureStore({
    reducer: {
      connection: connectionSlice.reducer,
      projection: projectionSlice.reducer,
      workspaceUi: workspaceUiSlice.reducer,
    },
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware({
        serializableCheck: {
          warnAfter: 64,
        },
      }),
    devTools: import.meta.env.DEV,
  });
}

export type AppStore = ReturnType<typeof createAppStore>;
export type RootState = ReturnType<AppStore['getState']>;
export type AppDispatch = AppStore['dispatch'];

export const selectConnection = (state: RootState) => state.connection;
export const selectProjection = (state: RootState): ServerProjectionState => state.projection;
export const selectWorkspaceUi = (state: RootState): WorkspaceUiState => state.workspaceUi;
const emptyRecord: Record<string, unknown> = {};
const emptyList: unknown[] = [];

function selectRecord(key: string) {
  return (state: RootState): Record<string, unknown> => {
    const value = state.projection.data[key];
    return value && typeof value === 'object' && !Array.isArray(value)
      ? value as Record<string, unknown>
      : emptyRecord;
  };
}

function selectList(key: string) {
  return (state: RootState): unknown[] => {
    const value = state.projection.data[key];
    return Array.isArray(value) ? value : emptyList;
  };
}

function selectValue(key: string) {
  return (state: RootState): unknown => state.projection.data[key];
}

const selectAgentRecords = selectRecord('agents');
const selectAgentSettings = selectRecord('agent_settings');
const selectResolvedAgentSettings = selectRecord('resolved_agent_settings');
const selectAgentDigestSettings = selectRecord('agent_digest_settings');
const selectDigestBufferStats = selectRecord('digest_buffer_stats');
const selectDigestSentEvents = selectRecord('digest_sent_events');
const selectEngineerBufferStats = selectRecord('engineer_buffer_stats');
const selectEngineerSentEvents = selectRecord('engineer_sent_events');
const selectGroupRecords = selectRecord('groups');
const selectChildren = selectRecord('children');
const selectGroupSettings = selectRecord('group_settings');
const selectTaskRecords = selectRecord('board_tasks');
const selectLanes = selectList('board_lanes');
const selectSchedules = selectRecord('schedules');
const selectDirectMessages = selectRecord('direct_messages_by_agent');
const selectPeerThreads = selectRecord('agent_peer_threads');
const selectMessageHistory = selectRecord('agent_message_history');
const selectMessageLoops = selectRecord('agent_message_loops');
const selectNotices = selectRecord('operator_notices');
const selectNoticeSummary = selectRecord('operator_notice_summary');
const selectInitiatives = selectRecord('initiatives');
const selectAreas = selectRecord('planning_areas');
const selectDecisions = selectRecord('decisions');
const selectPendingHires = selectRecord('pending_hires');
const selectIdeaBriefs = selectRecord('idea_briefs');
const selectJournals = selectRecord('engineer_journal');
const selectThinking = selectRecord('thinking');
const selectScratchpadNotes = selectRecord('thinking_scratchpad_notes');
const selectBehaviorOverlays = selectRecord('behavior_overlay_active');
const selectBehaviorOverlayProposals = selectRecord('behavior_overlay_proposals');
const selectActions = selectValue('actions');
const selectRoles = selectValue('roles');
const selectSpecializations = selectValue('specializations');
const selectTemplates = selectValue('templates');
const selectAgentClasses = selectValue('agent_classes');
const selectAgentClassAuthoringContract = selectValue('agent_class_authoring_contract');
const selectAgentClassCapabilityCatalog = selectValue('agent_class_capability_catalog');
const selectActiveGroup = selectValue('active_group');
const selectSelectedAgent = selectValue('selected_agent_id');
const selectSelectedPrincipal = selectValue('selected_principal_id');
const selectPanelLayout = selectValue('standalone_panel_layout');
const selectDetachedPanels = selectRecord('detached_panels');
const selectSidebarWidth = selectValue('workspace_sidebar_width');
const selectContextSplit = selectValue('context_panel_split_ratio');
const selectTerminalDirectMessagesHeight = selectValue('terminal_direct_messages_height');
const selectTerminalComposeHeight = selectValue('terminal_compose_height');
const selectEngineerPanelSplit = selectValue('engineer_panel_split_fraction');
const selectBoardFilters = selectRecord('board_filters_by_group');
const selectBoardSelectedLanes = selectRecord('board_selected_lanes_by_group');
const selectBoardHiddenWideLanes = selectRecord('board_hidden_wide_lanes_by_group');
const selectBoardSavedViews = selectRecord('board_saved_views_by_group');
const selectBoardLaneSorts = selectRecord('board_lane_sorts_by_group');
const selectBoardCardDensity = selectRecord('board_card_density_by_group');
const selectAuxiliaryResponses = selectRecord('auxiliary_responses');

// These memoized domain selectors are the public store boundary for feature
// code. The flat projection remains private protocol-compatibility state.
export const selectRuntime = selectRecord('runtime');
export const selectAgentsState = createSelector(
  [selectAgentRecords, selectAgentSettings, selectResolvedAgentSettings, selectAgentDigestSettings, selectDigestBufferStats, selectDigestSentEvents, selectEngineerBufferStats, selectEngineerSentEvents],
  (records, settings, resolvedSettings, digestSettings, digestBufferStats, digestSentEvents, engineerBufferStats, engineerSentEvents) => ({ records, settings, resolvedSettings, digestSettings, digestBufferStats, digestSentEvents, engineerBufferStats, engineerSentEvents }),
);
export const selectGroupsState = createSelector(
  [selectGroupRecords, selectChildren, selectGroupSettings],
  (records, children, settings) => ({ records, children, settings }),
);
export const selectTasksState = createSelector(
  [selectTaskRecords, selectLanes, selectSchedules, selectRecord('board_tasks_archived')],
  (records, lanes, schedules, archived) => ({ records, lanes, schedules, archived }),
);
export const selectMessagesState = createSelector(
  [selectDirectMessages, selectPeerThreads, selectMessageHistory, selectMessageLoops],
  (direct, peerThreads, history, loops) => ({ direct, peerThreads, history, loops }),
);
export const selectNoticesState = createSelector(
  [selectNotices, selectNoticeSummary],
  (records, summary) => ({ records, summary }),
);
export const selectPlanningState = createSelector(
  [
    selectInitiatives,
    selectAreas,
    selectDecisions,
    selectPendingHires,
    selectIdeaBriefs,
    selectJournals,
    selectThinking,
    selectScratchpadNotes,
  ],
  (initiatives, areas, decisions, pendingHires, ideaBriefs, journals, thinking, scratchpadNotes) => ({
    initiatives,
    areas,
    decisions,
    pendingHires,
    ideaBriefs,
    journals,
    thinking,
    scratchpadNotes,
  }),
);
export const selectCatalogState = createSelector(
  [
    selectActions,
    selectRoles,
    selectSpecializations,
    selectTemplates,
    selectAgentClasses,
    selectAgentClassAuthoringContract,
    selectAgentClassCapabilityCatalog,
    selectBehaviorOverlays,
  ],
  (
    actions: unknown,
    roles: unknown,
    specializations: unknown,
    templates: unknown,
    agentClasses: unknown,
    agentClassAuthoringContract: unknown,
    agentClassCapabilityCatalog: unknown,
    behaviorOverlays,
  ) => ({
    actions,
    roles,
    specializations,
    templates,
    agentClasses,
    agentClassAuthoringContract,
    agentClassCapabilityCatalog,
    behaviorOverlays,
  }),
);
export const selectOperationsState = createSelector(
  [
    selectRecord('system_health_metrics'),
    selectRecord('mission_control_summary'),
    selectRecord('supervisor_sessions'),
    selectRecord('relay_connection'),
    selectRecord('relay_config'),
    selectList('panel_events'),
    selectRecord('global_settings'),
    selectRecord('ai_settings'),
    selectBehaviorOverlayProposals,
  ],
  (health, missionControl, supervisor, relayConnection, relayConfig, events,
    globalSettings, aiSettings, behaviorOverlayProposals) => ({
    health,
    missionControl,
    supervisor,
    relayConnection,
    relayConfig,
    events,
    globalSettings,
    aiSettings,
    behaviorOverlayProposals,
  }),
);
export const selectWorkspaceState = createSelector(
  [
    selectActiveGroup,
    selectSelectedAgent,
    selectSelectedPrincipal,
    selectPanelLayout,
    selectDetachedPanels,
    selectSidebarWidth,
    selectContextSplit,
    selectTerminalDirectMessagesHeight,
    selectTerminalComposeHeight,
    selectEngineerPanelSplit,
  ],
  (
    activeGroup: unknown,
    selectedAgentId: unknown,
    selectedPrincipalId: unknown,
    panelLayout: unknown,
    detachedPanels,
    sidebarWidth: unknown,
    contextSplitRatio: unknown,
    terminalDirectMessagesHeight: unknown,
    terminalComposeHeight: unknown,
    engineerPanelSplitFraction: unknown,
  ) => ({
    activeGroup,
    selectedAgentId,
    selectedPrincipalId,
    panelLayout,
    detachedPanels,
    sidebarWidth,
    contextSplitRatio,
    terminalDirectMessagesHeight,
    terminalComposeHeight,
    engineerPanelSplitFraction,
  }),
);

export const selectAuxiliaryResponseState = createSelector(
  [selectAuxiliaryResponses],
  (responses) => responses,
);

export const selectBoardViewState = createSelector(
  [
    selectBoardFilters,
    selectBoardSelectedLanes,
    selectBoardHiddenWideLanes,
    selectBoardSavedViews,
    selectBoardLaneSorts,
    selectBoardCardDensity,
  ],
  (filters, selectedLanes, hiddenWideLanes, savedViews, laneSorts, cardDensity) => ({
    filters,
    selectedLanes,
    hiddenWideLanes,
    savedViews,
    laneSorts,
    cardDensity,
  }),
);

export const selectEntityCounts = createSelector(
  [selectAgentRecords, selectGroupRecords, selectTaskRecords],
  (agents, groups, tasks) => ({
    agents: Object.keys(agents).length,
    groups: Object.keys(groups).length,
    tasks: Object.keys(tasks).length,
  }),
);
