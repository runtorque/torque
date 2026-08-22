import { describe, expect, it } from 'vitest';

import { emptyBoardFilters, normalizeTasks, taskMatchesFilters, visibleHierarchy } from './model';

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
