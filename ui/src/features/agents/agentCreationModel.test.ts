import { describe, expect, it } from 'vitest';
import { groupAgentCapacity } from './agentCreationModel';

describe('agent capacity', () => {
  it('counts retained principals and workers, excluding terminals, deletions and other groups', () => {
    const records = {
      architect: { kind: 'architect', group: 'A', status: 'stopped' },
      engineer: { kind: 'engineer', group: 'A', dismissed_at: 123 },
      worker: { kind: 'worker', group: 'A', owner_engineer_id: 'engineer' },
      terminal: { cell_type: 'terminal', group: 'A', parent_id: 'worker' },
      standalone: { kind: 'terminal', group: 'A' },
      deleted: { kind: 'worker', group: 'A', deleted_at: 123 },
      foreign: { kind: 'worker', group: 'B' },
    };
    expect(groupAgentCapacity(records, 'A', 3)).toEqual({ count: 3, limit: 3, full: true });
    expect(groupAgentCapacity(records, 'A', 2).full).toBe(true);
    expect(groupAgentCapacity(records, 'A', 4).full).toBe(false);
    expect(groupAgentCapacity(records, 'A', 0).full).toBe(false);
  });
});
