export type UnknownRecord = Record<string, unknown>;

export const KNOWN_DELTA_OPERATIONS = [
  'agent_digest_update',
  'agent_message_history_append',
  'agent_message_loop_upsert',
  'agent_peer_thread_remove',
  'agent_peer_thread_upsert',
  'agent_remove',
  'agent_settings_update',
  'agent_upsert',
  'ai_index_status_update',
  'ai_settings_update',
  'ai_summary_status_update',
  'architect_dismissed',
  'architect_journal_append',
  'architect_rehired',
  'architect_settings_update',
  'area_link_remove',
  'area_link_upsert',
  'area_note_upsert',
  'area_upsert',
  'behavior_overlay_active_update',
  'behavior_overlay_proposal_resolve',
  'behavior_overlay_proposal_upsert',
  'behavior_overlay_version_append',
  'context_update',
  'decision_remove',
  'decision_upsert',
  'digest_buffer_stats',
  'digest_sent_push',
  'direct_message_read',
  'direct_message_upsert',
  'engineer_buffer_stats',
  'engineer_sent_events',
  'engineer_settings_update',
  'engineer_streams',
  'engineer_streams_update',
  'engineer_worklog_append',
  'event_append',
  'focus_update',
  'global_settings_update',
  'group_remove',
  'group_rename',
  'group_settings_update',
  'group_update',
  'groups_reorder',
  'idea_brief_upsert',
  'initiative_link_remove',
  'initiative_link_upsert',
  'initiative_upsert',
  'journal_append',
  'journal_delete',
  'lanes_update',
  'mcp_call_append',
  'operator_notice_summary',
  'operator_notice_upsert',
  'operator_notices_read_all',
  'peer_message_upsert',
  'pending_hire_resolve',
  'pending_hire_upsert',
  'perceived_empty_episode',
  'planning_area_link_remove',
  'planning_area_link_upsert',
  'planning_area_note_upsert',
  'planning_area_upsert',
  'provider_usage',
  'relay_config',
  'relay_connection',
  'runtime',
  'schedule_remove',
  'schedule_upsert',
  'task_remove',
  'task_upsert',
  'thinking_scratchpad_note_upsert',
  'ui_update',
  'worktree_merge_progress',
] as const;

export type KnownDeltaOperationName = (typeof KNOWN_DELTA_OPERATIONS)[number];

const knownDeltaOperationSet: ReadonlySet<string> = new Set(KNOWN_DELTA_OPERATIONS);

export function isKnownDeltaOperation(value: string): value is KnownDeltaOperationName {
  return knownDeltaOperationSet.has(value);
}

export interface DeltaOperation extends UnknownRecord {
  op: string;
}

export type KnownDeltaOperation = {
  [Name in KnownDeltaOperationName]: DeltaOperation & { op: Name };
}[KnownDeltaOperationName];

export interface StateFrame extends UnknownRecord {
  type: 'state';
  seq: number;
}

export interface DeltaFrame extends UnknownRecord {
  type: 'delta';
  seq: number;
  ops: DeltaOperation[];
}

export interface FocusUpdateFrame extends UnknownRecord {
  type: 'focus_update';
}

export interface AuxiliaryFrame extends UnknownRecord {
  type: string;
}

export type ServerFrame = StateFrame | DeltaFrame | FocusUpdateFrame | AuxiliaryFrame;

export type FrameParseResult =
  | { ok: true; frame: ServerFrame }
  | { ok: false; error: string };

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isFiniteSequence(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

export function parseServerFrame(value: unknown): FrameParseResult {
  if (!isRecord(value)) return { ok: false, error: 'frame must be an object' };
  if (typeof value.type !== 'string' || !value.type) {
    return { ok: false, error: 'frame type is required' };
  }

  if (value.type === 'state') {
    if (!isFiniteSequence(value.seq)) {
      return { ok: false, error: 'state frame seq must be a non-negative integer' };
    }
    return { ok: true, frame: value as StateFrame };
  }

  if (value.type === 'delta') {
    if (!isFiniteSequence(value.seq)) {
      return { ok: false, error: 'delta frame seq must be a non-negative integer' };
    }
    if (!Array.isArray(value.ops)) {
      return { ok: false, error: 'delta frame ops must be an array' };
    }
    for (const operation of value.ops) {
      if (!isRecord(operation) || typeof operation.op !== 'string' || !operation.op) {
        return { ok: false, error: 'every delta operation requires an op string' };
      }
    }
    return { ok: true, frame: value as unknown as DeltaFrame };
  }

  return { ok: true, frame: value as AuxiliaryFrame };
}

export function parseServerFrameJson(raw: string): FrameParseResult {
  try {
    return parseServerFrame(JSON.parse(raw) as unknown);
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'invalid JSON frame',
    };
  }
}
