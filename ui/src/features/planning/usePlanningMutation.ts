import { useRef, useState } from 'react';
import { useAppDispatch } from '../../app/hooks';
import { projectionActions } from '../../app/store';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';

/** One acknowledged operation at a time, including dependent lifecycle writes. */
export function usePlanningMutation() {
  const dispatch = useAppDispatch();
  const busy = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const request = async (command: TorqueCommand) => {
    const frame = await readCommand(command, new AbortController().signal);
    if (frame.type === 'error') throw new Error(typeof frame.message === 'string' ? frame.message : 'The request failed.');
    if (/^architect_decision_(create|update|link)$/.test(command.cmd) && typeof frame.id === 'string') {
      dispatch(projectionActions.auxiliaryResourceReceived({ ...command, ...frame, type: 'decision' }));
    } else dispatch(projectionActions.auxiliaryResourceReceived(frame));
    return frame;
  };
  const run = async (operation: (request: (command: TorqueCommand) => Promise<AuxiliaryFrame>) => Promise<void>) => {
    if (busy.current) return;
    busy.current = true; setPending(true); setError('');
    try { await operation(request); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not save. Your draft is retained.'); }
    finally { busy.current = false; setPending(false); }
  };
  return { run, pending, error, busy };
}
