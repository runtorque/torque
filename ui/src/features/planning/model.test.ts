import { describe, expect, it } from 'vitest';
import { groupInitiatives, groupRecords } from './model';

describe('planning model', () => {
  it('normalizes lists and filters group-owned records', () => {
    expect(groupRecords([{ id: 'one', group: 'A' }, { id: 'two', group: 'B' }], 'A'))
      .toHaveLength(1);
  });

  it('places unknown initiative states in triage', () => {
    const grouped = groupInitiatives({ one: { group_name: 'A', planning_status: 'now' }, two: { group: 'A', status: 'mystery' } }, 'A');
    expect(grouped.now[0]?.id).toBe('one');
    expect(grouped.triage[0]?.id).toBe('two');
  });
});
