import type { UnknownRecord } from '../../protocol';
export interface EngineerNotificationValues {
  digest_verbosity: string;
  push_interval: number;
  max_interval: number;
  heartbeat_interval: number;
  enabled_events: string[];
}
export const engineerNotificationPresets = {
  quiet: { label: 'Quiet', description: 'Major milestones, slower digests, and no idle heartbeat.', digest_verbosity: 'compact', push_interval: 120, max_interval: 600, heartbeat_interval: 0, enabled_events: ['task_derived', 'task_health_alert'] },
  normal: { label: 'Normal', description: 'Key lifecycle updates with balanced digests and heartbeats.', digest_verbosity: 'balanced', push_interval: 60, max_interval: 300, heartbeat_interval: 300, enabled_events: ['agent_started', 'task_dispatched', 'task_derived', 'task_health_alert'] },
  noisy: { label: 'Noisy', description: 'Faster, detailed digests including ongoing progress.', digest_verbosity: 'detailed', push_interval: 30, max_interval: 120, heartbeat_interval: 60, enabled_events: ['agent_started', 'task_dispatched', 'task_derived', 'agent_progress', 'task_health_alert'] },
};
export type EngineerNotificationPresetName = keyof typeof engineerNotificationPresets;
export function notificationPresetValues(name: EngineerNotificationPresetName): EngineerNotificationValues {
  const { digest_verbosity, push_interval, max_interval, heartbeat_interval, enabled_events } = engineerNotificationPresets[name];
  return { digest_verbosity, push_interval, max_interval, heartbeat_interval, enabled_events: [...enabled_events] };
}
function events(value: unknown): string[] | null {
  if (!(typeof value === 'string' || Array.isArray(value))) return null;
  const list = typeof value === 'string' ? value.split(/[\n,]/) : value;
  if (list.some((entry) => typeof entry !== 'string')) return null;
  return [...new Set((list as string[]).map((entry) => entry.trim()).filter(Boolean))].sort();
}
export function matchNotificationPreset(value: UnknownRecord): EngineerNotificationPresetName | 'custom' {
  const currentEvents = events(value.enabled_events);
  for (const name of Object.keys(engineerNotificationPresets) as EngineerNotificationPresetName[]) {
    const preset = engineerNotificationPresets[name];
    if (value.digest_verbosity !== preset.digest_verbosity || currentEvents === null || JSON.stringify(currentEvents) !== JSON.stringify(events(preset.enabled_events))) continue;
    if (['push_interval', 'max_interval', 'heartbeat_interval'].every((key) => {
      const item = value[key]; return (typeof item === 'number' || (typeof item === 'string' && item.trim() !== '')) && Number(item) === preset[key as 'push_interval' | 'max_interval' | 'heartbeat_interval'];
    })) return name;
  }
  return 'custom';
}
