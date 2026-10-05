import { useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectAgentsState } from '../../app/store';
import { toAgentViewModel, type AgentViewModel } from './model';
import { useWorktreeMutation } from './useWorktreeMutation';

type Operation = { kind: 'create' | 'checkpoint'; agent: AgentViewModel };
export function useWorktreeToolbar(active: boolean) {
  const [operation, setOperation] = useState<Operation | null>(null);
  const mutation = useWorktreeMutation();
  const synchronized = useAppSelector((state) => state.connection.status === 'connected' && state.connection.expectedSeq !== null && !state.connection.awaitingResync);
  const records = useAppSelector(selectAgentsState).records;
  const current = operation && records[operation.agent.id] ? toAgentViewModel(operation.agent.id, records[operation.agent.id]) : null;
  const live = current && !Number(current.raw.deleted_at) ? current : null;
  const blocked = mutation.pending || mutation.uncertain;
  const ready = active && synchronized;
  const resumePath = mutation.result.resume_available === true && mutation.result.created === true && typeof mutation.result.worktree_path === 'string' ? mutation.result.worktree_path : '';
  function create(agent: AgentViewModel, resume = '') {
    return mutation.run({ cmd: 'worktree_create', id: agent.id, relaunch: resume ? true : Boolean(agent.sessionId), expected_session_id: agent.sessionId, ...(resume ? { resume_worktree_path: resume } : {}) });
  }
  return {
    operation, mutation, live, blocked, ready, resumePath,
    begin: (agent: AgentViewModel, kind: Operation['kind']) => {
      if (!ready || blocked || !mutation.reset()) return;
      setOperation({ agent, kind });
      if (kind === 'checkpoint') void mutation.run({ cmd: 'worktree_checkpoint', id: agent.id });
      else if (!agent.sessionId) void create(agent);
    },
    confirm: () => {
      if (!ready || blocked || !live || !operation) return;
      if (operation.kind === 'create') void create(live, resumePath);
      else void mutation.run({ cmd: 'worktree_checkpoint', id: operation.agent.id });
    },
    retry: () => { if (ready) void mutation.retry(); },
    dismiss: () => { if (!blocked) setOperation(null); },
  };
}
