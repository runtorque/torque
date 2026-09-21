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
