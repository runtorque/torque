import { useEffect, useRef, useState } from 'react';
import { Button, ModalDialog } from '../../design/primitives';
import { planningRequest } from './planningRequests';
import { text } from './model';
import styles from './PlanningWorkspace.module.css';

export function RejectHireDialog({ hire, architect, onClose, onRejected }: {
  hire: Record<string, unknown>; architect: string; onClose: () => void; onRejected: () => void;
}) {
  const [note, setNote] = useState('');
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { active.current?.abort(); active.current = null; }, []);
  const reject = async () => {
    if (active.current) return;
    const controller = new AbortController(); active.current = controller;
    const reviewed = submitted ?? note.trim(); setSubmitted(reviewed); setPending(true); setError('');
    try {
      const frame = await planningRequest({ cmd: 'pending_hire_reject', id: hire.id, note: reviewed }, controller.signal, true);
      if (active.current !== controller) return;
      if (frame.type === 'error') throw new Error(text(frame.message, 'Could not reject this hire request.'));
      if (frame.type !== 'ok' || frame.ok !== true) throw new Error('Hire rejection returned an invalid acknowledgement; its outcome is unknown.');
      onRejected();
    } catch (cause) {
      if (active.current === controller) setError(cause instanceof Error ? cause.message : 'Could not reject this hire request.');
    } finally {
      if (active.current === controller) { active.current = null; setPending(false); }
    }
  };
  return <ModalDialog title="Reject hire request" description={`Reject ${text(hire.requested_name, 'this Engineer')} requested by ${architect}.`} isOpen onOpenChange={(open) => { if (!open && !active.current) onClose(); }} size="small">
    <form className={styles.createForm} onSubmit={(event) => { event.preventDefault(); void reject(); }}>
      <label>Optional note<textarea autoFocus value={note} readOnly={submitted !== null} onChange={(event) => setNote(event.target.value)} /></label>
      {error ? <p role="alert">{error} The submitted note is retained for an exact retry.</p> : null}
      <footer><Button tone="quiet" isDisabled={pending} onPress={onClose}>Cancel</Button><Button tone="danger" type="submit" isDisabled={pending}>{pending ? 'Rejecting…' : submitted === null ? 'Reject hire' : 'Retry rejection'}</Button></footer>
    </form>
  </ModalDialog>;
}
