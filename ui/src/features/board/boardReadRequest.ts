import type { TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';

/** Bound request observation even if the underlying transport ignores abort. */
export const boardReadRequest = (command: TorqueCommand, signal: AbortSignal) => observeBoardRequest((transport) => readCommand(command, transport), signal, false);
export const boardWriteRequest = (command: TorqueCommand, signal: AbortSignal) => observeBoardRequest((transport) => readCommand(command, transport), signal, true);
export function observeBoardRequest<T>(operation: (signal: AbortSignal) => Promise<T>, signal: AbortSignal, writing = true): Promise<T> {
  return new Promise((resolve, reject) => {
    const transport = new AbortController(); let settled = false;
    const finish = (frame?: T, error?: Error) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal.removeEventListener('abort', abort);
      if (error) { transport.abort(); reject(error); } else resolve(frame!);
    };
    const abort = () => finish(undefined, new DOMException('Board request cancelled', 'AbortError'));
    const timer = setTimeout(() => finish(undefined, new Error(writing ? 'Board change timed out; its outcome is unknown. Review the Board before retrying.' : 'Board read timed out. Retry when ready.')), writing ? 30_000 : 15_000);
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    void operation(transport.signal).then((frame) => finish(frame), (cause: unknown) => finish(undefined, cause instanceof Error ? cause : new Error('Board request failed.')));
  });
}
