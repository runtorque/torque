import { describe, expect, it } from 'vitest';
import { groupInitiatives, groupRecords, planningStatuses } from './model';

describe('planning model', () => {
  it('normalizes lists and filters group-owned records', () => {
    expect(groupRecords([{ id: 'one', group: 'A' }, { id: 'two', group: 'B' }], 'A'))
      .toHaveLength(1);
  });

  it('keeps all six persisted initiative states separate and hides archived records', () => {
    const items = planningStatuses.map((status) => ({ id: status, group: 'A', planning_status: status }));
    const grouped = groupInitiatives([...items, { id: 'archived', group: 'A', planning_status: 'now', archived: true }], 'A');
    expect(Object.keys(grouped)).toEqual(['triage', 'now', 'next', 'later', 'parked', 'shipped']);
    for (const status of planningStatuses) expect(grouped[status].map((item) => item.id)).toEqual([status]);
  });

  it('places unknown initiative states in triage', () => {
    const grouped = groupInitiatives({ one: { group_name: 'A', planning_status: 'now' }, two: { group: 'A', status: 'mystery' } }, 'A');
    expect(grouped.now[0]?.id).toBe('one');
    expect(grouped.triage[0]?.id).toBe('two');
  });
});
