import type { UnknownRecord } from '../../protocol';
import type { McpFilter } from './activityReads';

export function matchesMcpActivity(call: UnknownRecord, id: string, filter: McpFilter, anchor: number): boolean {
  if (call.cell_id && call.cell_id !== id) return false;
  if (call.hook_event_name !== 'PostToolUse') return false;
  const success = call.success === true || call.success === 1 || call.success === 'true' || call.success === 'success';
  if ((filter.outcome === 'success' && !success) || (filter.outcome === 'error' && success)) return false;
  const tool = (typeof call.tool_name === 'string' ? call.tool_name : '').toLowerCase();
  const contains = filter.tool.trim().toLowerCase().replace(/[*%]/g, '');
  if (contains ? !tool.includes(contains) : !tool.startsWith('mcp__torque__')) return false;
  const seconds = { '1h': 3600, '6h': 21600, '24h': 86400 }[filter.range];
  return !seconds || Number(call.appended_at ?? 0) >= anchor - seconds;
}
