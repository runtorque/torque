import { useEffect, useRef, useState } from 'react';
import { useAppDispatch } from '../../app/hooks';
import { projectionActions } from '../../app/store';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { planningRequest, validatePlanningAcknowledgement } from './planningRequests';

/** One owned, acknowledged operation, including its dependent lifecycle writes. */
export function usePlanningMutation() {
  const dispatch = useAppDispatch();
  const busy = useRef(false);
  const active = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => () => { active.current?.abort(); active.current = null; busy.current = false; }, []);
  const run = async (operation: (request: (command: TorqueCommand) => Promise<AuxiliaryFrame>) => Promise<void>) => {
    if (busy.current) return;
    const controller = new AbortController(); active.current = controller;
    busy.current = true; setPending(true); setError('');
    const assertOwned = () => {
      if (controller.signal.aborted || active.current !== controller) throw new DOMException('Planning editor closed', 'AbortError');
    };
    const request = async (command: TorqueCommand) => {
      assertOwned();
      const frame = await planningRequest(command, controller.signal, true);
      assertOwned(); validatePlanningAcknowledgement(command, frame);
      const projection = command.cmd === 'architect_decision_create' ? { ...command, ...frame, type: 'decision' }
        : /^architect_decision_(update|link)$/.test(command.cmd) ? { ...frame, type: 'decision' } : frame;
      dispatch(projectionActions.auxiliaryResourceReceived(projection));
      return frame;
    };
    try { await operation(request); }
    catch (cause) { if (active.current === controller) setError(cause instanceof Error ? cause.message : 'Could not save. Your draft is retained.'); }
    finally { if (active.current === controller) { active.current = null; busy.current = false; setPending(false); } }
  };
  return { run, pending, error, busy };
}
