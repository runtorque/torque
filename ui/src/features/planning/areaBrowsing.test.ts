import { describe, expect, it } from 'vitest';
import { areaLifecycle, areaLifecycles, areaTypes, filterAreas, sortedAreas } from './areaModel';
import { planningReads } from './planningReads';
const empty = { search: '', lifecycle: '', type: '' };
describe('Area browsing parity', () => {
  it('matches Classic lifecycle/type/title ordering and excludes archived or other-group records', () => {
    const rows = sortedAreas([
      { id: 'last', group: 'G', title: 'Retired', lifecycle: 'retired' },
      { id: 'b', group: 'G', title: 'Beta', lifecycle: 'planned', area_type: 'API' },
      { id: 'a', group: 'G', title: 'Alpha', lifecycle: 'unknown', area_type: 'api' },
      { id: 'other-type', group_name: 'G', title: 'Alpha', lifecycle: 'planned', area_type: 'UI' },
      { id: 'active', group: 'G', title: 'Active', lifecycle: ' Active_Investment ' },
      { id: 'archived', group: 'G', archived: true }, { id: 'archived-at', group: 'G', archived_at: '2026-09-25' },
      { id: 'outside', group: 'Other' }, { id: 'unscoped' },
    ], 'G');
    expect(rows.map((item) => item.id)).toEqual(['a', 'b', 'other-type', 'active', 'last']);
    expect(areaLifecycle('UNKNOWN')).toBe('planned');
    for (const lifecycle of areaLifecycles) expect(areaLifecycle(lifecycle.toUpperCase())).toBe(lifecycle);
  });
  it('searches every Classic field case-insensitively and combines all filters', () => {
    const keys = ['id', 'slug', 'title', 'area_type', 'lifecycle', 'summary', 'user_purpose', 'system_purpose'];
    for (const key of keys) {
      const rows = sortedAreas([{ id: 'match', group: 'G', [key]: 'A Needle B' }, { id: 'different', group: 'G' }], 'G');
      expect(filterAreas(rows, { ...empty, search: '  NEEDLE ' })).toHaveLength(1);
    }
    const rows = sortedAreas([{ id: 'one', group: 'G', title: 'Needle', lifecycle: 'stable', area_type: ' API ' }, { id: 'two', group: 'G', title: 'Needle', lifecycle: 'planned', area_type: 'API' }, { id: 'three', group: 'G', title: 'Needle', lifecycle: 'stable', area_type: 'UI' }], 'G');
    expect(filterAreas(rows, { search: 'needle', lifecycle: 'stable', type: 'api' }).map((row) => row.id)).toEqual(['one']);
    expect(filterAreas(rows, { ...empty, search: 'absent' })).toEqual([]);
    expect(filterAreas(rows, empty)).toEqual(rows);
  });
  it('discovers only active scoped types and requests the Classic 500-Area window', () => {
    expect(areaTypes(sortedAreas([{ id: '1', group: 'G', area_type: ' UI ' }, { id: '2', group: 'G', area_type: 'API' }, { id: '3', group: 'G', area_type: 'API' }, { id: '4', group: 'Other', area_type: 'Outside' }, { id: '5', group: 'G', area_type: 'Archived', archived: true }], 'G'))).toEqual(['API', 'UI']);
    expect(planningReads('areas', 'G', false, false, undefined)[0]?.command).toEqual({ cmd: 'area_list', group: 'G', include_archived: false, limit: 500, include_links: true, include_notes: true });
  });
});
