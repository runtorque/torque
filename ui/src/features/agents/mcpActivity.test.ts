import { expect, it } from 'vitest';
import { mergeCellEvents, mergeMcpCalls } from '../../protocol/activityFeeds';
import { matchesMcpActivity } from './mcpActivity';

it('applies submitted tool, outcome and time bounds to live calls with the same scope as historical reads', () => {
  const anchor = 100_000; const filter = { tool: '*PROGRESS%', outcome: 'error', range: '1h' };
  const call = { cursor: 1, cell_id: 'worker', hook_event_name: 'PostToolUse', tool_name: 'mcp__torque__task_progress', success: false, appended_at: anchor - 3600 };
  expect(matchesMcpActivity(call, 'worker', filter, anchor)).toBe(true);
  for (const patch of [{ cell_id: 'another' }, { hook_event_name: 'PreToolUse' }, { tool_name: 'mcp__torque__task_complete' }, { success: true }, { appended_at: anchor - 3601 }]) expect(matchesMcpActivity({ ...call, ...patch }, 'worker', filter, anchor)).toBe(false);
  expect(matchesMcpActivity({ ...call, appended_at: 1 }, 'worker', { ...filter, range: 'all' }, anchor)).toBe(true);
  expect(matchesMcpActivity({ ...call, tool_name: 'mcp__other__task_progress' }, 'worker', { tool: '', outcome: 'all', range: 'all' }, anchor)).toBe(false);
});

it('reconciles delayed pages and repeated MCP deltas by cursor while retaining newest-first bounded history', () => {
  const initial = Array.from({ length: 500 }, (_, cursor) => ({ cursor, appended_at: cursor, tool_name: `call-${cursor}` }));
  const live = mergeMcpCalls(initial, [{ cursor: 501, appended_at: 501, tool_name: 'live' }]);
  const refreshed = mergeMcpCalls(live, [{ cursor: 499, appended_at: 499, tool_name: 'updated' }, { cursor: 501, appended_at: 501, tool_name: 'live' }]);
  expect(refreshed).toHaveLength(500); expect(refreshed[0]?.tool_name).toBe('live'); expect(refreshed.filter((row) => row.cursor === 501)).toHaveLength(1); expect(refreshed[1]?.tool_name).toBe('updated'); expect(refreshed.some((row) => row.cursor === 0)).toBe(false);
});

it('reconciles cell-event pages and live append IDs without losing a newer row or duplicating existing history', () => {
  const live = [{ id: 3, timestamp: 3, message: 'New live event' }, { id: 2, timestamp: 2, message: 'Existing event' }];
  const merged = mergeCellEvents(live, [{ id: 2, timestamp: 2, message: 'Existing event' }, { id: 'live:1', timestamp: 1, message: 'Older runtime event' }]);
  expect(merged.map((row) => row.id)).toEqual([3, 2, 'live:1']);
});
