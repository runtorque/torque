import type { TorqueCommand } from '../../protocol';
export type PlanningTab = 'roadmap' | 'areas' | 'thinking' | 'decisions' | 'team' | 'schedules';
export type PlanningEditorKind = 'initiative' | 'area' | 'note' | 'brief' | 'decision';
export interface PlanningRead { command: TorqueCommand; type: string; field: 'initiatives' | 'areas' | 'scratchpadNotes' | 'ideaBriefs' | 'decisions' | 'pendingHires' | 'journals' }
export function planningReads(tab: PlanningTab, group: string, archived: boolean, archivedDecisions: boolean, editor: PlanningEditorKind | undefined): PlanningRead[] {
  if (!group) return [];
  const available: Record<PlanningRead['field'], PlanningRead> = {
    initiatives: { command: { cmd: 'initiative_list', group, include_archived: false }, type: 'initiative_list', field: 'initiatives' },
    areas: { command: { cmd: 'area_list', group, include_links: true, include_notes: true }, type: 'area_list', field: 'areas' },
    scratchpadNotes: { command: { cmd: 'scratchpad_note_list', group, include_archived: archived }, type: 'scratchpad_note_list', field: 'scratchpadNotes' },
    ideaBriefs: { command: { cmd: 'idea_brief_list', group, include_archived: archived }, type: 'idea_brief_list', field: 'ideaBriefs' },
    decisions: { command: { cmd: 'decisions_snapshot', include_archived: archivedDecisions }, type: 'decisions_snapshot', field: 'decisions' },
    pendingHires: { command: { cmd: 'pending_hires_snapshot', status: 'pending' }, type: 'pending_hires_snapshot', field: 'pendingHires' },
    journals: { command: { cmd: 'engineer_journal_snapshot', group, include_streams: true }, type: 'engineer_journal_snapshot', field: 'journals' },
  };
  const sections: Record<PlanningTab, PlanningRead['field'][]> = { roadmap: ['initiatives'], areas: ['areas'], thinking: ['scratchpadNotes', 'ideaBriefs'], decisions: ['decisions'], team: ['pendingHires', 'journals'], schedules: [] };
  const fields = new Set(sections[tab]);
  // Editors expose cross-resource relationship selectors. Hydrate their choices
  // only while the editor is open, even when their owning section is hidden.
  if (editor === 'initiative' || editor === 'area') fields.add('decisions');
  if (editor === 'area') fields.add('initiatives');
  return [...fields].map((field) => available[field]);
}
