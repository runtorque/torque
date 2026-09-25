import type { TorqueCommand, UnknownRecord } from '../../protocol';
export type ActivityTab = 'decisions' | 'journal' | 'messages' | 'events' | 'queued' | 'worklog' | 'mcp' | 'history' | 'class' | 'chat';
export type RemoteSection = 'events' | 'journal' | 'mcp' | 'history' | 'class';
export interface ActivityRead { command: TorqueCommand; type: string; target?: [string, string] }
export interface McpFilter { tool: string; outcome: string; range: string }
export function activityReads(tab: ActivityTab, id: string, kind: string, group: string, limits: Record<RemoteSection, number>, filter: McpFilter, anchor: number): ActivityRead[] {
  const read = (command: TorqueCommand, type: string, target?: [string, string]): ActivityRead => ({ command, type, ...(target ? { target } : {}) });
  const settings = () => read({ cmd: 'get_group_settings', group }, 'group_settings', ['group', group]);
  if (tab === 'events') return [read({ cmd: 'get_cell_events', cell_id: id, limit: limits.events }, 'cell_events', ['cell_id', id]), ...(kind === 'engineer' ? [settings()] : [])];
  if (tab === 'journal') return kind === 'architect' ? [read({ cmd: 'architect_journal_read', architect_id: id, limit: limits.journal }, 'architect_journal_entries', ['architect_id', id])] : kind === 'engineer' ? [read({ cmd: 'engineer_journal_snapshot', group, engineer_id: id, include_streams: true, limit: limits.journal }, 'engineer_journal_snapshot', ['group', group]), read({ cmd: 'engineer_session_map_read', group, engineer_id: id }, 'engineer_session_map', ['group', group]), settings()] : [];
  if (tab === 'decisions' && kind === 'architect') return [read({ cmd: 'decisions_snapshot', include_archived: true }, 'decisions_snapshot')];
  if ((tab === 'chat' || tab === 'messages') && kind === 'architect') return [read({ cmd: 'architect_peer_inbox', architect_id: id, detail: true, limit: 100 }, 'architect_peer_inbox', ['architect_id', id])];
  if (tab === 'mcp') {
    const seconds = { '1h': 3600, '6h': 21600, '24h': 86400 }[filter.range];
    return [read({ cmd: 'mcp_calls', cell_id: id, tool_name_pattern: filter.tool.trim() ? `*${filter.tool.trim()}*` : 'mcp__torque__%', hook_event_name: 'PostToolUse', success_filter: filter.outcome, limit: limits.mcp, ...(seconds ? { since: anchor - seconds } : {}) }, 'mcp_calls', ['cell_id', id])];
  }
  if (tab === 'history') return [read({ cmd: 'get_agent_history_detail', agent_id: id, message_limit: limits.history }, 'agent_history_detail', ['record.id', id])];
  if (tab === 'class') return [read({ cmd: 'agent_class_list' }, 'agent_classes'), read({ cmd: 'agent_class_status', agent_id: id }, 'agent_class_status', ['status.agent_id', id]), read({ cmd: 'agent_class_audit', agent_id: id, limit: limits.class }, 'agent_class_audit', ['agent_id', id])];
  return [];
}
export function validateActivityRead(frame: UnknownRecord, request: ActivityRead): void {
  if (frame.type === 'error') throw new Error(typeof frame.message === 'string' ? frame.message : 'Activity read failed.');
  const target = request.target?.[0].split('.').reduce<unknown>((value, key) => value && typeof value === 'object' ? (value as UnknownRecord)[key] : undefined, frame);
  // Some older responses omit target metadata; the owned HTTP call still
  // correlates them. Explicit mismatches are never accepted.
  if (frame.type !== request.type || (target !== undefined && target !== request.target?.[1])) throw new Error('Activity response did not match the requested target.');
}
