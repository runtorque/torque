import { describe, expect, it } from 'vitest';

import { emptyBoardFilters, normalizeTasks, staleCompletedTasks, taskMatchesFilters, visibleHierarchy } from './model';

const tasks = normalizeTasks({
  root: { id: 'root', task: 'Ship Board', group: 'Torque', lane: 'Ready', position: 1, labels: ['ui'], health_state: 'healthy' },
  child: { id: 'child', task: 'Verify keyboard', group: 'Torque', lane: 'Ready', position: 2, parent_task_id: 'root', pipeline_depth: 1, labels: ['a11y'], health_state: 'blocked' },
});

describe('Board model', () => {
  it('normalizes records and projects pipeline hierarchy', () => {
    expect(tasks).toHaveLength(2);
    expect(visibleHierarchy(tasks, [])).toEqual([
      { task: tasks[0], depth: 0, childCount: 1 },
      { task: tasks[1], depth: 1, childCount: 0 },
    ]);
    expect(visibleHierarchy(tasks, ['root'])).toEqual([
      { task: tasks[0], depth: 0, childCount: 1 },
    ]);
  });

  it('preserves creator and responsible-owner provenance', () => {
    const [owned] = normalizeTasks({
      owned: {
        id: 'owned', task: 'Owned work', group: 'Torque',
        assigned_architect_id: 'architect-owner', assigned_engineer_id: 'engineer-owner',
        created_by_architect_id: 'architect-creator', created_by_engineer_id: 'engineer-creator',
      },
    });

    expect(owned).toMatchObject({
      assignedArchitectId: 'architect-owner',
      assignedEngineerId: 'engineer-owner',
      createdByArchitectId: 'architect-creator',
      createdByEngineerId: 'engineer-creator',
    });
  });

  it('combines text, label, health, and quick-view filters', () => {
    expect(taskMatchesFilters(tasks[1]!, {
      ...emptyBoardFilters,
      search_query: 'keyboard',
      filter_labels: ['a11y'],
      filter_health: ['blocked'],
      quick_view: 'blocked',
    })).toBe(true);
    expect(taskMatchesFilters(tasks[0]!, { ...emptyBoardFilters, quick_view: 'blocked' })).toBe(false);
  });
});

it('selects only scoped active Done tasks at the inclusive seven-day cutoff, oldest first', () => {
  const now = Date.parse('2026-09-22T12:00:00Z');
  const old = '2026-09-01T12:00:00Z';
  const rows = normalizeTasks(Object.fromEntries(Object.entries({
    boundary: { updated_at: '2026-09-15T12:00:00Z' },
    newer: { updated_at: '2026-09-15T12:00:00.001Z' },
    oldest: { updated_at: old },
    fallback: { updated_at: '', created_at: '2026-09-10T12:00:00Z' },
    touched: { updated_at: '2026-09-22T11:00:00Z', created_at: old },
    invalid: { updated_at: 'broken', created_at: old },
    missing: {}, future: { updated_at: '2027-01-01T00:00:00Z' },
    epoch: { updated_at: '1970-01-01T00:00:00Z' },
    wrongLane: { lane: 'Ready', updated_at: old },
    archived: { lane: 'Archived', updated_at: old },
    legacyArchived: { labels: ['torque:archived'], updated_at: old },
    otherGroup: { group: 'Other', updated_at: old },
  }).map(([id, fields]) => [id, { id, task: id, lane: 'Done', group: 'Torque', ...fields }])));
  expect(staleCompletedTasks(rows, 'Torque', now).map((task) => task.id)).toEqual(['oldest', 'fallback', 'boundary']);
});
