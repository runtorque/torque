import type { UnknownRecord } from '../../protocol';
import { records, text } from './model';

export const areaLifecycles = ['planned', 'experimental', 'active_investment', 'stable', 'maintenance', 'deprecated', 'retired'];
export const areaNoteTypes = ['caveat', 'tech_debt', 'open_question', 'follow_up', 'invariant'];
export const areaRelations = ['related', 'depends_on', 'supports'];
export type AreaTarget = 'task' | 'decision' | 'initiative' | 'area';
export function areaLinks(value: unknown): UnknownRecord[] {
  if (Array.isArray(value)) return records(value);
  const grouped = value && typeof value === 'object' ? value as UnknownRecord : {};
  return (['task', 'decision', 'initiative', 'area'] as const).flatMap((kind) => {
    const items = grouped[`${kind}s`];
    return (Array.isArray(items) ? items : []).map((item: unknown) => {
      const link = item && typeof item === 'object' ? item as UnknownRecord : {};
      return { link_type: kind, target_id: text(link.area_id, text(link.target_id, text(item))), relation: kind === 'area' ? text(link.relation, 'related') : '' };
    });
  });
}

export interface AreaFilters { search: string; lifecycle: string; type: string }
export function areaLifecycle(value: unknown): string {
  const lifecycle = text(value).trim().toLowerCase();
  return areaLifecycles.includes(lifecycle) ? lifecycle : 'planned';
}
export function sortedAreas(value: unknown, group: string) {
  return records(value).filter((item) => (!group || text(item.group, text(item.group_name)) === group) && !item.archived && !item.archived_at)
    .sort((a, b) => areaLifecycles.indexOf(areaLifecycle(a.lifecycle)) - areaLifecycles.indexOf(areaLifecycle(b.lifecycle))
      || text(a.area_type).toLowerCase().localeCompare(text(b.area_type).toLowerCase())
      || text(a.title, a.id).localeCompare(text(b.title, b.id)));
}
export function filterAreas(items: ReturnType<typeof sortedAreas>, filters: AreaFilters) {
  const search = filters.search.trim().toLowerCase(); const type = filters.type.trim().toLowerCase();
  return items.filter((item) => (!filters.lifecycle || areaLifecycle(item.lifecycle) === filters.lifecycle)
    && (!type || text(item.area_type).trim().toLowerCase() === type)
    && (!search || [item.id, item.slug, item.title, item.area_type, item.lifecycle, item.summary, item.user_purpose, item.system_purpose].map((value) => text(value)).join(' ').toLowerCase().includes(search)));
}
export function areaTypes(items: ReturnType<typeof sortedAreas>): string[] {
  return [...new Set(items.map((item) => text(item.area_type).trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}
