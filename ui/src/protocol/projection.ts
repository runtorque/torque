import type { Draft } from '@reduxjs/toolkit';

import {
  isKnownDeltaOperation,
  type DeltaOperation,
  type StateFrame,
  type UnknownRecord,
} from './types';

export interface ServerProjectionState {
  hydrated: boolean;
  snapshotVersion: number;
  seq: number;
  data: UnknownRecord;
  appliedOperationCount: number;
  lastDeltaByOperation: Record<string, UnknownRecord>;
  unknownOperations: string[];
}

export function emptyServerProjection(): ServerProjectionState {
  return {
    hydrated: false,
    snapshotVersion: 0,
    seq: 0,
    data: {},
    appliedOperationCount: 0,
    lastDeltaByOperation: {},
    unknownOperations: [],
  };
}

function cloneRecord(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? { ...(value as UnknownRecord) }
    : {};
}

function ensureRecord(
  state: Draft<ServerProjectionState>,
  key: string,
): Draft<UnknownRecord> {
  const existing = state.data[key];
  if (!existing || typeof existing !== 'object' || Array.isArray(existing)) {
    state.data[key] = {};
  }
  return state.data[key] as Draft<UnknownRecord>;
}

function operationPayload(operation: DeltaOperation): UnknownRecord {
  const payload: UnknownRecord = { ...operation };
  Reflect.deleteProperty(payload, 'op');
  return payload;
}

function operationId(operation: UnknownRecord, ...keys: string[]): string {
  for (const key of keys) {
    const value = operation[key];
    if (typeof value === 'string' && value) return value;
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return '';
}

function upsert(
  state: Draft<ServerProjectionState>,
  collection: string,
  operation: DeltaOperation,
  idKeys: string[],
  nestedKey?: string,
): void {
  const source = nestedKey ? operation[nestedKey] : operation;
  const payload = cloneRecord(source);
  const id = operationId(payload, ...idKeys) || operationId(operation, ...idKeys);
  if (!id) return;
  const records = ensureRecord(state, collection);
  records[id] = { ...cloneRecord(records[id]), ...payload };
}

function remove(
  state: Draft<ServerProjectionState>,
  collection: string,
  operation: DeltaOperation,
  ...idKeys: string[]
): void {
  const id = operationId(operation, ...idKeys);
  if (!id) return;
  delete ensureRecord(state, collection)[id];
}

function replaceObject(
  state: Draft<ServerProjectionState>,
  key: string,
  operation: DeltaOperation,
): void {
  state.data[key] = operationPayload(operation);
}

function prependToBucket(
  state: Draft<ServerProjectionState>,
  collection: string,
  bucketId: string,
  value: unknown,
  limit: number,
): void {
  if (!bucketId) return;
  const buckets = ensureRecord(state, collection);
  const current = Array.isArray(buckets[bucketId]) ? buckets[bucketId] as unknown[] : [];
  buckets[bucketId] = [value, ...current].slice(0, limit);
}

function setBucket(
  state: Draft<ServerProjectionState>,
  collection: string,
  bucketId: string,
  value: unknown,
): void {
  if (bucketId) ensureRecord(state, collection)[bucketId] = value;
}

function numericLimit(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 1
    ? Math.floor(value)
    : fallback;
}

function applyCommonOperation(
  state: Draft<ServerProjectionState>,
  operation: DeltaOperation,
): void {
  switch (operation.op) {
    case 'agent_upsert':
      upsert(state, 'agents', operation, ['id']);
      break;
    case 'agent_remove':
      remove(state, 'agents', operation, 'id');
      for (const collection of [
        'agent_settings',
        'resolved_agent_settings',
        'agent_digest_settings',
        'digest_buffer_stats',
        'digest_sent_events',
        'agent_message_history',
        'direct_messages_by_agent',
      ]) remove(state, collection, operation, 'id');
      break;
    case 'context_update': {
      const id = operationId(operation, 'id', 'agent_id', 'cell_id');
      if (!id) break;
      const agents = ensureRecord(state, 'agents');
      const agent = cloneRecord(agents[id]);
      if ('context_window' in operation) agent.context_window = operation.context_window;
      if ('provider_usage' in operation) agent.provider_usage = operation.provider_usage;
      agents[id] = agent;
      break;
    }
    case 'group_update':
      if (operationId(operation, 'name')) {
        ensureRecord(state, 'groups')[operationId(operation, 'name')] =
          Array.isArray(operation.agents) ? operation.agents.slice() : [];
      }
      break;
    case 'group_remove':
      remove(state, 'groups', operation, 'name');
      break;
    case 'group_rename': {
      const oldName = operationId(operation, 'old_name');
      const newName = operationId(operation, 'new_name');
      const groups = ensureRecord(state, 'groups');
      if (oldName && newName) {
        groups[newName] = { ...cloneRecord(groups[oldName]), ...operationPayload(operation), name: newName };
        delete groups[oldName];
      }
      break;
    }
    case 'groups_reorder':
      if (Array.isArray(operation.groups)) {
        const current = ensureRecord(state, 'groups');
        const reordered: UnknownRecord = {};
        for (const name of operation.groups) {
          if (typeof name === 'string') reordered[name] = current[name] ?? [];
        }
        state.data.groups = reordered;
      }
      break;
    case 'group_settings_update': {
      const name = operationId(operation, 'name');
      if (name) ensureRecord(state, 'group_settings')[name] = operationPayload(operation);
      break;
    }
    case 'architect_settings_update':
      setBucket(
        state,
        'architect_settings',
        operationId(operation, 'group'),
        operationPayload(operation),
      );
      break;
    case 'engineer_settings_update':
      setBucket(
        state,
        'engineer_settings',
        operationId(operation, 'group'),
        operationPayload(operation),
      );
      break;
    case 'agent_settings_update': {
      const agentId = operationId(operation, 'agent_id');
      if (!agentId) break;
      const payload = operationPayload(operation);
      const resolved = payload.resolved;
      delete payload.resolved;
      delete payload.group;
      ensureRecord(state, 'agent_settings')[agentId] = payload;
      if (resolved !== undefined) ensureRecord(state, 'resolved_agent_settings')[agentId] = resolved;
      break;
    }
    case 'agent_digest_update': {
      const agentId = operationId(operation, 'agent_id');
      if (!agentId) break;
      const payload = operationPayload(operation);
      const resolved = payload.resolved;
      delete payload.resolved;
      ensureRecord(state, 'agent_digest_settings')[agentId] = payload;
      if (resolved !== undefined) ensureRecord(state, 'resolved_agent_settings')[agentId] = resolved;
      break;
    }
    case 'task_upsert':
      upsert(state, 'board_tasks', operation, ['id']);
      break;
    case 'task_remove':
      remove(state, 'board_tasks', operation, 'id');
      break;
    case 'lanes_update':
      state.data.board_lanes = Array.isArray(operation.lanes) ? operation.lanes.slice() : [];
      break;
    case 'schedule_upsert':
      upsert(state, 'schedules', operation, ['id']);
      break;
    case 'schedule_remove':
      remove(state, 'schedules', operation, 'id');
      break;
    case 'runtime':
    case 'relay_config':
    case 'relay_connection':
    case 'global_settings_update':
    case 'ai_settings_update':
    case 'ai_index_status_update':
    case 'ai_summary_status_update':
      replaceObject(
        state,
        operation.op === 'global_settings_update'
          ? 'global_settings'
          : operation.op.replace(/_update$/, ''),
        operation,
      );
      break;
    case 'provider_usage': {
      const provider = operationId(operation, 'provider', 'provider_id', 'adapter', 'name');
      if (!provider && operation.provider_usage && typeof operation.provider_usage === 'object') {
        state.data.provider_usage = cloneRecord(operation.provider_usage);
        break;
      }
      const usage = ensureRecord(state, 'provider_usage');
      if (!provider) {
        Object.assign(usage, operationPayload(operation));
      } else if (operation.delete || operation.remove || operation.value === null) {
        delete usage[provider];
      } else {
        usage[provider] = cloneRecord(
          operation.usage ?? operation.value ?? operation.payload ?? operation.data ?? operation,
        );
      }
      break;
    }
    case 'focus_update':
      Object.assign(state.data, operationPayload(operation));
      break;
    case 'ui_update': {
      const key = operationId(operation, 'key');
      if (key) state.data[key] = operation.value;
      break;
    }
    case 'operator_notice_upsert':
      upsert(state, 'operator_notices', operation, ['id'], 'notice');
      break;
    case 'operator_notice_summary':
      state.data.operator_notice_summary = operation.summary ?? operationPayload(operation);
      break;
    case 'operator_notices_read_all':
      state.data.operator_notices_read_at = operation.read_at ?? operation.timestamp ?? null;
      break;
    case 'event_append': {
      const event = operationPayload(operation);
      const events = Array.isArray(state.data.panel_events)
        ? [...state.data.panel_events as UnknownRecord[]]
        : [];
      const eventId = operationId(event, 'id');
      const index = eventId
        ? events.findIndex((candidate) => operationId(candidate, 'id') === eventId)
        : -1;
      if (index >= 0) events[index] = event;
      else events.push(event);
      state.data.panel_events = events.slice(-Math.max(500, Math.min(5000, events.length)));
      break;
    }
    case 'perceived_empty_episode':
      prependToBucket(
        state,
        'perceived_empty_episodes',
        operationId(operation, 'cell_id') || 'unassigned',
        operation.episode ?? operationPayload(operation),
        100,
      );
      break;
    case 'mcp_call_append': {
      const call = cloneRecord(operation.call);
      if (operationId(call, 'hook_event_name') !== 'PostToolUse') break;
      prependToBucket(state, 'mcp_calls', operationId(call, 'cell_id'), call, 500);
      break;
    }
    case 'agent_message_history_append':
      prependToBucket(
        state,
        'agent_message_history',
        operationId(operation, 'agent_id'),
        operation.entry,
        numericLimit(operation.limit, 100),
      );
      break;
    case 'peer_message_upsert': {
      const message = cloneRecord(operation.message ?? operation.entry);
      const agentId = operationId(operation, 'agent_id');
      const agent = cloneRecord(ensureRecord(state, 'agents')[agentId]);
      if (!agentId || Object.keys(agent).length === 0) break;
      const messages = Array.isArray(agent.mcp_messages)
        ? [...agent.mcp_messages as UnknownRecord[]]
        : [];
      const messageId = operationId(message, 'id') || operationId(operation, 'id');
      const index = messageId
        ? messages.findIndex((candidate) => operationId(candidate, 'id') === messageId)
        : -1;
      if (index >= 0) messages[index] = { ...cloneRecord(messages[index]), ...message };
      else messages.unshift(message);
      agent.mcp_messages = messages.slice(0, 50);
      ensureRecord(state, 'agents')[agentId] = agent;
      break;
    }
    case 'direct_message_upsert':
    case 'direct_message_read': {
      const agentId = operationId(operation, 'agent_id');
      if (!agentId) break;
      const message = cloneRecord(operation.message ?? operation.entry);
      const messageId = operationId(message, 'id', 'message_id')
        || operationId(operation, 'message_id', 'id');
      if (!messageId) break;
      if (!message.id) message.id = messageId;
      const buckets = ensureRecord(state, 'direct_messages_by_agent');
      const messages = Array.isArray(buckets[agentId])
        ? [...buckets[agentId] as UnknownRecord[]]
        : [];
      const index = messages.findIndex(
        (candidate) => operationId(candidate, 'id', 'message_id') === messageId,
      );
      if (operation.op === 'direct_message_read') {
        message.read_at = operation.read_at ?? message.read_at ?? 0;
      }
      if (index >= 0) messages[index] = { ...cloneRecord(messages[index]), ...message };
      else messages.push(message);
      buckets[agentId] = messages.slice(-numericLimit(operation.limit, 100));
      break;
    }
    case 'decision_upsert':
      upsert(state, 'decisions', operation, ['id']);
      break;
    case 'decision_remove':
      remove(state, 'decisions', operation, 'id');
      break;
    case 'pending_hire_upsert':
      upsert(state, 'pending_hires', operation, ['id']);
      break;
    case 'pending_hire_resolve':
      remove(state, 'pending_hires', operation, 'id');
      break;
    case 'journal_append':
      prependToBucket(
        state,
        'engineer_journal',
        operationId(operation, 'author_cell_id'),
        operationPayload(operation),
        200,
      );
      break;
    case 'journal_delete': {
      const authorId = operationId(operation, 'author_cell_id');
      const journals = ensureRecord(state, 'engineer_journal');
      const buckets = authorId ? [authorId] : Object.keys(journals);
      for (const bucket of buckets) {
        if (Array.isArray(journals[bucket])) {
          journals[bucket] = (journals[bucket] as UnknownRecord[]).filter(
            (entry) => operationId(entry, 'id') !== operationId(operation, 'id'),
          );
        }
      }
      break;
    }
    case 'architect_journal_append':
      prependToBucket(
        state,
        'architect_journals',
        operationId(operation, 'architect_id'),
        operationPayload(operation),
        500,
      );
      break;
    case 'architect_dismissed':
    case 'architect_rehired': {
      const architectId = operationId(operation, 'architect_id');
      const agents = ensureRecord(state, 'agents');
      const architect = cloneRecord(agents[architectId]);
      if (architectId && Object.keys(architect).length > 0) {
        architect.dismissed_at = operation.op === 'architect_dismissed'
          ? operation.dismissed_at ?? Date.now() / 1000
          : 0;
        agents[architectId] = architect;
      }
      break;
    }
    case 'engineer_buffer_stats':
      setBucket(state, 'engineer_buffer_stats', operationId(operation, 'group'), operationPayload(operation));
      break;
    case 'engineer_sent_events':
      setBucket(state, 'engineer_sent_events', operationId(operation, 'group'), operation.events ?? []);
      break;
    case 'digest_buffer_stats':
      setBucket(state, 'digest_buffer_stats', operationId(operation, 'agent_id'), operationPayload(operation));
      break;
    case 'digest_sent_push':
      setBucket(state, 'digest_sent_events', operationId(operation, 'agent_id'), operation.events ?? []);
      break;
    case 'engineer_worklog_append':
      prependToBucket(
        state,
        'engineer_worklog',
        operationId(operation, 'group'),
        cloneRecord(operation.entry),
        200,
      );
      break;
    case 'engineer_streams':
    case 'engineer_streams_update':
      setBucket(
        state,
        'engineer_streams',
        operationId(operation, 'group'),
        operation.streams ?? (Array.isArray(operation.items) ? { items: operation.items } : []),
      );
      break;
    case 'initiative_upsert':
      upsert(state, 'initiatives', operation, ['id']);
      break;
    case 'initiative_link_upsert':
    case 'initiative_link_remove': {
      const initiatives = ensureRecord(state, 'initiatives');
      const id = operationId(operation, 'initiative_id');
      const item = cloneRecord(initiatives[id]);
      const links = cloneRecord(item.links);
      const kind = operation.link_type === 'decision' ? 'decisions' : 'tasks';
      const target = operationId(operation, 'target_id');
      const current = Array.isArray(links[kind]) ? links[kind] as unknown[] : [];
      links[kind] = operation.op === 'initiative_link_upsert'
        ? [...new Set([...current, target])] : current.filter((value) => value !== target);
      if (id && initiatives[id]) initiatives[id] = { ...item, links };
      const stored = ensureRecord(state, 'initiative_links');
      if (operation.op === 'initiative_link_upsert') upsert(state, 'initiative_links', operation, ['id']);
      else for (const [key, value] of Object.entries(stored)) {
        const link = cloneRecord(value);
        if (link.initiative_id === id && link.link_type === operation.link_type && link.target_id === target) delete stored[key];
      }
      break;
    }
    case 'area_upsert':
    case 'planning_area_upsert':
      upsert(state, 'planning_areas', operation, ['id']);
      break;
    case 'area_link_upsert':
    case 'planning_area_link_upsert':
    case 'area_link_remove':
    case 'planning_area_link_remove': {
      const stored = ensureRecord(state, 'planning_area_links');
      const link = { ...cloneRecord(stored[operationId(operation, 'id')]), ...operation };
      const removing = operation.op.endsWith('_remove');
      const areaId = operationId(link, 'area_id');
      const kind = operationId(link, 'link_type');
      const target = operationId(link, 'target_id');
      const relation = operationId(link, 'relation') || (kind === 'area' ? 'related' : '');
      if (removing) {
        for (const [key, value] of Object.entries(stored)) {
          const current = cloneRecord(value);
          if (key === operationId(operation, 'id') || (current.area_id === areaId && current.link_type === kind && current.target_id === target && (kind !== 'area' || current.relation === relation))) delete stored[key];
        }
      } else upsert(state, 'planning_area_links', operation, ['id']);
      const areas = ensureRecord(state, 'planning_areas');
      if (areas[areaId] && kind && target) {
        const area = cloneRecord(areas[areaId]); const links = cloneRecord(area.links);
        const current = Array.isArray(links[`${kind}s`]) ? links[`${kind}s`] as unknown[] : [];
        const next = current.filter((value) => kind === 'area' ? !(cloneRecord(value).area_id === target && cloneRecord(value).relation === relation) : value !== target);
        if (!removing) next.push(kind === 'area' ? { area_id: target, relation } : target);
        areas[areaId] = { ...area, links: { ...links, [`${kind}s`]: next } };
      }
      break;
    }
    case 'area_note_upsert':
    case 'planning_area_note_upsert': {
      upsert(state, 'planning_area_notes', operation, ['id']);
      const areas = ensureRecord(state, 'planning_areas'); const areaId = operationId(operation, 'area_id');
      if (areas[areaId]) {
        const area = cloneRecord(areas[areaId]); const notes: unknown[] = Array.isArray(area.notes) ? area.notes : [];
        const id = operationId(operation, 'id');
        const prior = notes.find((value) => operationId(cloneRecord(value), 'id') === id);
        const note = { ...cloneRecord(prior), ...operationPayload(operation) };
        const next = notes.filter((value) => operationId(cloneRecord(value), 'id') !== id);
        if (!note.archived && !note.archived_at) next.unshift(note);
        areas[areaId] = { ...area, notes: next.slice(0, 50) };
      }
      break;
    }
    case 'idea_brief_upsert':
      upsert(state, 'idea_briefs', operation, ['id']);
      break;
    case 'thinking_scratchpad_note_upsert':
      upsert(state, 'thinking_scratchpad_notes', operation, ['id']);
      {
        const thinking = ensureRecord(state, 'thinking');
        if (!thinking.scratchpad_notes || typeof thinking.scratchpad_notes !== 'object') {
          thinking.scratchpad_notes = {};
        }
        const id = operationId(operation, 'id');
        if (id) (thinking.scratchpad_notes as Draft<UnknownRecord>)[id] = operationPayload(operation);
      }
      break;
    case 'agent_message_loop_upsert':
      upsert(state, 'agent_message_loops', operation, ['id'], 'loop');
      break;
    case 'agent_peer_thread_upsert':
      upsert(state, 'agent_peer_threads', operation, ['id', 'thread_id'], 'thread');
      break;
    case 'agent_peer_thread_remove':
      remove(state, 'agent_peer_threads', operation, 'thread_id', 'id');
      break;
    case 'behavior_overlay_active_update':
      setBucket(
        state,
        'behavior_overlay_active',
        operationId(operation, 'agent_id', 'scope_id', 'cell_id'),
        operationPayload(operation),
      );
      break;
    case 'behavior_overlay_version_append':
      prependToBucket(
        state,
        'behavior_overlay_versions',
        operationId(operation, 'agent_id', 'scope_id', 'cell_id'),
        operationPayload(operation),
        500,
      );
      break;
    case 'behavior_overlay_proposal_upsert':
    case 'behavior_overlay_proposal_resolve':
      upsert(state, 'behavior_overlay_proposals', operation, ['proposal_id', 'id']);
      break;
    case 'worktree_merge_progress':
      setBucket(
        state,
        'worktree_merge_progress',
        operationId(operation, 'id', 'task_id', 'cell_id') || 'latest',
        operationPayload(operation),
      );
      break;
    default:
      break;
  }
}

export function hydrateProjection(frame: StateFrame): ServerProjectionState {
  const data: UnknownRecord = {};
  for (const [key, value] of Object.entries(frame)) {
    if (key !== 'type' && key !== 'seq') data[key] = value;
  }
  return {
    hydrated: true,
    snapshotVersion: 0,
    seq: frame.seq,
    data,
    appliedOperationCount: 0,
    lastDeltaByOperation: {},
    unknownOperations: [],
  };
}

export function applyDeltaOperation(
  state: Draft<ServerProjectionState>,
  operation: DeltaOperation,
): boolean {
  if (!isKnownDeltaOperation(operation.op)) {
    if (!state.unknownOperations.includes(operation.op)) {
      state.unknownOperations.push(operation.op);
      if (state.unknownOperations.length > 20) state.unknownOperations.shift();
    }
    return false;
  }

  state.appliedOperationCount += 1;
  state.lastDeltaByOperation[operation.op] = operationPayload(operation);
  applyCommonOperation(state, operation);
  return true;
}
