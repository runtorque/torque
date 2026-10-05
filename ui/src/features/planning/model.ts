import type { UnknownRecord } from '../../protocol';

export const planningStatuses = ['triage', 'now', 'next', 'later', 'parked', 'shipped'] as const;
export type PlanningStatus = typeof planningStatuses[number];
export const decisionStatuses = ['proposed', 'accepted', 'revised', 'rejected'] as const;

export function records(value: unknown): (UnknownRecord & { id: string })[] {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => item && typeof item === 'object' && !Array.isArray(item)
      ? [{ id: text((item as UnknownRecord).id, String(index)), ...(item as UnknownRecord) }]
      : []);
  }
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value as UnknownRecord).flatMap(([id, item]) =>
    item && typeof item === 'object' && !Array.isArray(item)
      ? [{ id, ...(item as UnknownRecord) }]
      : []);
}

export function groupRecords(value: unknown, group: string): (UnknownRecord & { id: string })[] {
  return records(value).filter((item) => {
    const itemGroup = text(item.group_name, text(item.group));
    return !itemGroup || itemGroup === group;
  });
}

export function initiativeStatus(item: UnknownRecord): PlanningStatus {
  const status = text(item.planning_status, text(item.status, 'triage')).toLowerCase();
  return (planningStatuses as readonly string[]).includes(status)
    ? status as PlanningStatus
    : 'triage';
}

export function groupInitiatives(value: unknown, group: string) {
  const grouped: Record<PlanningStatus, (UnknownRecord & { id: string })[]> = {
    triage: [], now: [], next: [], later: [], parked: [], shipped: [],
  };
  for (const item of groupRecords(value, group).filter((entry) => !entry.archived && !entry.archived_at)) grouped[initiativeStatus(item)].push(item);
  return grouped;
}

export function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}
