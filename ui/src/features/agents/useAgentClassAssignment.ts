import { useState } from 'react';
import { useAppDispatch, useAppSelector, useAppStore } from '../../app/hooks';
import { projectionActions } from '../../app/store';
import type { AuxiliaryFrame, UnknownRecord } from '../../protocol';
import { classAssignmentActions, emptyAssignment } from './classAssignmentState';

function record(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
}
class Refused extends Error {}

/** Status reads and assignment replies share arrival order in the projection. */
export function useAgentClassAssignment(agentId: string, initialId: string, initialStatus: unknown) {
  const dispatch = useAppDispatch(); const store = useAppStore();
  const operation = useAppSelector((state) => state.classAssignments[agentId] ?? emptyAssignment);
  const incoming = useAppSelector((state) => record(state.projection.data.agent_class_status_by_agent)[agentId]);
  const [retained, setRetained] = useState<unknown>(initialStatus);
  if (incoming !== undefined && incoming !== retained) setRetained(incoming);
  // Compact snapshots clear auxiliary projections; keep the accepted status
  // until this agent's next read arrives rather than reviving an old reply.
  const status = record(incoming ?? retained);
  const selectedId = operation.draft ?? (typeof status.assigned_class_id === 'string' ? status.assigned_class_id : initialId);

  async function save(baseDir: string) {
    if (store.getState().classAssignments[agentId]?.pending) return;
    // Freeze the reviewed selection; a refusal must not adopt an external value.
    const classId = selectedId; dispatch(classAssignmentActions.started({ agentId, value: classId }));
    let error = ''; let message = '';
    const command = { cmd: classId ? 'agent_class_assign' : 'agent_class_clear', agent_id: agentId, ...(baseDir ? { base_dir: baseDir } : {}), ...(classId ? { class_id: classId } : {}), actor_label: 'trusted-user-react-ui' };
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 30_000);
    try {
      const response = await fetch('/api/cmd', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(command), signal: controller.signal });
      const body = await response.json() as { ok?: boolean; error?: string; data?: AuxiliaryFrame };
      if (body.ok === false || body.data?.type === 'error') throw new Refused(body.error || (typeof body.data?.message === 'string' ? body.data.message : 'Assignment refused.'));
      const acknowledged = record(body.data?.status);
      if (!response.ok || body.ok !== true || body.data?.type !== 'agent_class_assignment' || acknowledged.agent_id !== agentId || acknowledged.assigned_class_id !== classId) throw new Error('The acknowledgement did not match the requested assignment.');
      dispatch(projectionActions.auxiliaryResourceReceived(body.data));
      message = classId ? 'Assignment saved. Applies at the next launch.' : 'Assignment cleared. The default applies at the next launch.';
    } catch (cause) {
      error = cause instanceof Refused ? cause.message : `The save outcome is unknown. ${controller.signal.aborted ? 'The request timed out.' : cause instanceof Error ? cause.message : 'The connection failed.'} Refresh status and review it before saving again.`;
    } finally {
      window.clearTimeout(timer);
      dispatch(classAssignmentActions.finished({ agentId, error, message }));
    }
  }
  return { status, selectedId, ...operation, save, select: (value: string) => dispatch(classAssignmentActions.selected({ agentId, value })) };
}
