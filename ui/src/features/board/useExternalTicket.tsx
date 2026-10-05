import { useEffect, useRef, useState } from 'react';
import { Button, ModalDialog } from '../../design/primitives';
import type { DesktopHost } from '../../host/types';
import { boardReadRequest } from './boardReadRequest';
import type { BoardTask } from './model';

type Ticket = Pick<BoardTask, 'id' | 'task' | 'externalUrl'>;
type Review = { ticket: Ticket; url: string; loading: boolean; error: string };
function ticketUrl(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error('This ticket has no external URL.');
  const parsed = new URL(value);
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('External tickets require an HTTP or HTTPS URL.');
  return parsed.href;
}

/** Open known links during the click; resolve ID-only tickets before a fresh user gesture. */
export function useExternalTicket(host: DesktopHost) {
  const [review, setReview] = useState<Review | null>(null);
  const owner = useRef<AbortController | null>(null);
  useEffect(() => () => { owner.current?.abort(); }, []);
  const close = () => { owner.current?.abort(); owner.current = null; setReview(null); };
  const launch = (ticket: Ticket, value: string) => {
    owner.current?.abort(); const controller = new AbortController(); owner.current = controller;
    try {
      const url = ticketUrl(value);
      // Calling the host synchronously preserves browser user activation.
      void host.openExternal(url).then(() => { if (!controller.signal.aborted) setReview(null); }).catch((cause: unknown) => {
        if (!controller.signal.aborted) setReview({ ticket, url, loading: false, error: cause instanceof Error ? cause.message : 'Could not open the external ticket.' });
      });
    } catch (cause) { setReview({ ticket, url: '', loading: false, error: cause instanceof Error ? cause.message : 'Could not open the external ticket.' }); }
  };
  const resolve = (ticket: Ticket) => {
    owner.current?.abort(); const controller = new AbortController(); owner.current = controller;
    setReview({ ticket, url: '', loading: true, error: '' });
    void boardReadRequest({ cmd: 'external_open_task', id: ticket.id }, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type === 'error') throw new Error(typeof frame.message === 'string' && frame.message ? frame.message : 'Could not resolve the external ticket.');
      if (frame.type !== 'external_open' || frame.task_id !== ticket.id) throw new Error('The external ticket response did not match this task.');
      setReview({ ticket, url: ticketUrl(frame.url), loading: false, error: '' });
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setReview({ ticket, url: '', loading: false, error: cause instanceof Error ? cause.message : 'Could not resolve the external ticket.' }); });
  };
  return {
    open: (ticket: Ticket) => { if (ticket.externalUrl) launch(ticket, ticket.externalUrl); else resolve(ticket); },
    dialog: <ModalDialog title="Open external ticket" description={review?.ticket.task ?? ''} size="small" isOpen={Boolean(review)} onOpenChange={(open) => { if (!open) close(); }}>
      {review ? <div>
        {review.loading ? <p role="status">Resolving ticket URL…</p> : null}
        {review.error ? <p role="alert">{review.error}</p> : null}
        {review.url ? <p style={{ overflowWrap: 'anywhere' }}>{review.url}</p> : null}
        <Button tone="quiet" onPress={close}>Cancel</Button>
        {review.url ? <Button tone="primary" onPress={() => launch(review.ticket, review.url)}>Open ticket</Button> : <Button isDisabled={review.loading} onPress={() => resolve(review.ticket)}>Retry ticket URL</Button>}
      </div> : null}
    </ModalDialog>,
  };
}
