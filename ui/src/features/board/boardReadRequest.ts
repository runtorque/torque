import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';

/** Bound read observation even if the underlying transport ignores abort. */
export function boardReadRequest(command: TorqueCommand, signal: AbortSignal): Promise<AuxiliaryFrame> {
  return new Promise((resolve, reject) => {
    const transport = new AbortController(); let settled = false;
    const finish = (frame?: AuxiliaryFrame, error?: Error) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal.removeEventListener('abort', abort);
      if (error) { transport.abort(); reject(error); } else resolve(frame!);
    };
    const abort = () => finish(undefined, new DOMException('Board read cancelled', 'AbortError'));
    const timer = setTimeout(() => finish(undefined, new Error('Board read timed out. Retry when ready.')), 15_000);
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    void readCommand(command, transport.signal).then((frame) => finish(frame), (cause: unknown) => finish(undefined, cause instanceof Error ? cause : new Error('Board read failed.')));
  });
}
