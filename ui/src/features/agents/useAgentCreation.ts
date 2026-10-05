import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { TorqueCommand } from '../../protocol';
import { CommandResponseError } from '../../protocol/http';
import { settingsRequest } from '../control/settingsRequests';
import { createdTarget, incompleteCreationTarget, type IncompleteCreationTarget } from './agentCreationModel';

type Attempt = { command: TorqueCommand; kind: string };

/** Keep the reviewed command until the daemon proves refusal or its exact outcome. */
export function useAgentCreation(open: boolean, onCreated: (id: string) => void, onClose: () => void) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [incomplete, setIncomplete] = useState<IncompleteCreationTarget | null>(null);
  const attemptRef = useRef<Attempt | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const callbacks = useRef({ onCreated, onClose });
  useLayoutEffect(() => { callbacks.current = { onCreated, onClose }; }, [onCreated, onClose]);
  useEffect(() => {
    return () => { controllerRef.current?.abort(); controllerRef.current = null; setSaving(false); };
  }, [open]);

  const deliver = (reviewed: Attempt) => {
    if (!open || controllerRef.current) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true); setError('');
    const owns = () => controllerRef.current === controller && !controller.signal.aborted;
    void settingsRequest(reviewed.command, controller.signal, true, 'Agent creation', 'Agent creation timed out; its outcome is unknown. Retry the same creation to recover it.').then((frame) => {
      if (!owns()) return;
      if (frame.type === 'creation_incomplete') {
        const target = incompleteCreationTarget(frame, reviewed.command);
        setIncomplete(target);
        setError(typeof frame.message === 'string' ? frame.message : 'Launch did not finish.');
        return;
      }
      const id = createdTarget(frame, reviewed.command, reviewed.kind);
      attemptRef.current = null; setAttempt(null);
      if (id) callbacks.current.onCreated(id);
      callbacks.current.onClose();
    }).catch((cause: unknown) => {
      if (!owns()) return;
      if (cause instanceof CommandResponseError && cause.creationRefused && cause.status < 500 && cause.status !== 409) {
        attemptRef.current = null; setAttempt(null);
      }
      setError(cause instanceof Error ? cause.message : 'Could not confirm creation.');
    }).finally(() => {
      if (!owns()) return;
      controllerRef.current = null; setSaving(false);
    });
  };
  const create = (payload: TorqueCommand, kind: string) => {
    if (attemptRef.current || controllerRef.current) return;
    const reviewed = { command: { ...payload, idempotency_key: `react-create-${crypto.randomUUID()}` }, kind };
    attemptRef.current = reviewed; setAttempt(reviewed); setIncomplete(null);
    deliver(reviewed);
  };
  const retry = () => { if (attemptRef.current && !incomplete) deliver(attemptRef.current); };
  const requestClose = () => { if (!attemptRef.current && !controllerRef.current) callbacks.current.onClose(); };
  const inspect = () => {
    if (!incomplete || controllerRef.current) return;
    if (incomplete.type === 'agent') callbacks.current.onCreated(incomplete.id);
    callbacks.current.onClose();
  };
  return { saving, error, setError, locked: Boolean(attempt), incomplete, create, retry, requestClose, inspect };
}
