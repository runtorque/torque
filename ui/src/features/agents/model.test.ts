import { describe, expect, it } from 'vitest';

import { buildAgentHierarchy, buildAgentTree, visibleAgentTreeRows } from './model';

describe('agent hierarchy model', () => {
  it('projects principals, owned workers, and child terminals without adjacency assumptions', () => {
    const hierarchy = buildAgentHierarchy({
      architect: { name: 'Architect', group: 'A', kind: 'architect' },
      engineer: { name: 'Engineer', group: 'A', kind: 'engineer', hired_by_architect_id: 'architect' },
      worker: { name: 'Worker', group: 'A', kind: 'worker', owner_engineer_id: 'engineer' },
      terminal: { name: 'Shell', group: 'A', cell_type: 'terminal', parent_id: 'worker' },
      other: { name: 'Other', group: 'B', kind: 'worker' },
    }, 'A');

    expect(hierarchy.architects.map((agent) => agent.id)).toEqual(['architect']);
    expect(hierarchy.engineersByArchitect.architect?.[0]?.id).toBe('engineer');
    expect(hierarchy.workersByEngineer.engineer?.[0]?.id).toBe('worker');
    expect(hierarchy.terminalsByParent.worker?.[0]?.id).toBe('terminal');
    expect(hierarchy.all).toHaveLength(4);
  });

  it('flattens Architect ownership in visual order and supports collapsed teams', () => {
    const tree = buildAgentTree(buildAgentHierarchy({
      worker: { name: 'Worker', group: 'A', kind: 'worker', owner_engineer_id: 'engineer' },
      architect: { name: 'Architect', group: 'A', kind: 'architect' },
      terminal: { name: 'Shell', group: 'A', cell_type: 'terminal', parent_id: 'worker' },
      engineer: { name: 'Engineer', group: 'A', kind: 'engineer', hired_by_architect_id: 'architect' },
    }, 'A'));

    expect(visibleAgentTreeRows(tree, new Set()).map((row) => [row.agent.id, row.depth, row.descendantCount])).toEqual([
      ['architect', 0, 3],
      ['engineer', 1, 2],
      ['worker', 2, 1],
      ['terminal', 3, 0],
    ]);
    expect(visibleAgentTreeRows(tree, new Set(['engineer'])).map((row) => row.agent.id)).toEqual([
      'architect', 'engineer',
    ]);
  });

  it('keeps agents visible when their recorded owner is missing', () => {
    const tree = buildAgentTree(buildAgentHierarchy({
      engineer: { name: 'Orphan engineer', group: 'A', kind: 'engineer', hired_by_architect_id: 'missing-architect' },
      worker: { name: 'Orphan worker', group: 'A', kind: 'worker', owner_engineer_id: 'missing-engineer' },
      terminal: { name: 'Orphan terminal', group: 'A', cell_type: 'terminal', parent_id: 'missing-worker' },
    }, 'A'));
    const rows = visibleAgentTreeRows(tree, new Set());

    expect(rows.map((row) => row.agent.id)).toEqual(['engineer', 'worker', 'terminal']);
    expect(rows.every((row) => row.orphaned)).toBe(true);
  });
});

it('uses saved group order for ownership siblings and saved child order for terminals', () => {
  const hierarchy = buildAgentHierarchy({
    e: { name: 'Engineer', group: 'G', kind: 'engineer' },
    a: { name: 'A', group: 'G', kind: 'worker', owner_engineer_id: 'e' },
    z: { name: 'Z', group: 'G', kind: 'worker', owner_engineer_id: 'e' },
    t1: { name: 'A shell', group: 'G', cell_type: 'terminal', parent_id: 'z' },
    t2: { name: 'Z shell', group: 'G', cell_type: 'terminal', parent_id: 'z' },
    root: { name: 'Other root', group: 'G', kind: 'worker' },
  }, 'G');
  const rows = visibleAgentTreeRows(buildAgentTree(hierarchy, ['root', 'e', 'z', 'a'], { z: ['t2', 't1'] }), new Set());
  expect(rows.map((row) => row.agent.id)).toEqual(['root', 'e', 'z', 't2', 't1', 'a']);
});
