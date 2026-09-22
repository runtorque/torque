import type { UnknownRecord } from '../protocol';
export const workspacePanels = ['board', 'agents', 'planning', 'control'] as const;
export const controlTabs = ['mission', 'activity', 'history', 'context', 'logs', 'chat', 'pipelines', 'actions', 'catalog', 'settings', 'help'] as const;
export interface WorkspaceNavigation { version: 1; activePanel: typeof workspacePanels[number]; controlTab: typeof controlTabs[number] }
export const defaultNavigation: WorkspaceNavigation = { version: 1, activePanel: 'board', controlTab: 'mission' };
export function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
export function validNavigation(raw: unknown): WorkspaceNavigation | null {
  const value = record(raw);
  if (value.version !== 1 || !workspacePanels.some((panel) => panel === value.activePanel) || !controlTabs.some((tab) => tab === value.controlTab)) return null;
  return { version: 1, activePanel: value.activePanel as WorkspaceNavigation['activePanel'], controlTab: value.controlTab as WorkspaceNavigation['controlTab'] };
}
const classicPanels: Record<string, Partial<WorkspaceNavigation>> = {
  board: { activePanel: 'board' }, engineer: { activePanel: 'agents' }, agents: { activePanel: 'agents' }, terminal: { activePanel: 'agents' },
  initiatives: { activePanel: 'planning' }, thinking: { activePanel: 'planning' },
  'mission-control': { activePanel: 'control', controlTab: 'mission' }, events: { activePanel: 'control', controlTab: 'activity' },
  supervisor: { activePanel: 'control', controlTab: 'mission' }, health: { activePanel: 'control', controlTab: 'mission' },
  history: { activePanel: 'control', controlTab: 'history' }, context: { activePanel: 'control', controlTab: 'context' },
  chat: { activePanel: 'control', controlTab: 'chat' }, actions: { activePanel: 'control', controlTab: 'actions' },
  templates: { activePanel: 'control', controlTab: 'catalog' }, help: { activePanel: 'control', controlTab: 'help' },
};
export function restoredNavigation(data: UnknownRecord): WorkspaceNavigation {
  const saved = validNavigation(data.react_workspace_state); if (saved) return saved;
  const layout = record(data.standalone_panel_layout);
  const fromLayout = record(data.runtime).embedded_terminal === true && Object.keys(layout).length > 0;
  const candidates = fromLayout ? [layout.last_active, record(layout.right).active, record(layout.bottom).active] : [data.panel_active];
  const active = candidates.find((value) => typeof value === 'string' && Object.hasOwn(classicPanels, value));
  return { ...defaultNavigation, ...classicPanels[typeof active === 'string' ? active : ''] };
}
