import { describe, expect, it } from 'vitest';

import { buildAgentHierarchy } from './model';

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
});
