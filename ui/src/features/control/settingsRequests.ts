import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
export const settingsReadTimeout = 15_000;
export const settingsSaveTimeout = 30_000;

// Bound observation independently of fetch cancellation: an obsolete adapter or
// response may still settle after abort, but cannot settle the owned operation.
export function settingsRequest(command: TorqueCommand, signal: AbortSignal, writing = false, label = 'Settings', timeoutMessage?: string): Promise<AuxiliaryFrame> {
  return new Promise((resolve, reject) => {
    const request = new AbortController(); let settled = false;
    const finish = (frame?: AuxiliaryFrame, error?: Error) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal.removeEventListener('abort', abort);
      if (error) { request.abort(); reject(error); } else resolve(frame!);
    };
    const abort = () => finish(undefined, new DOMException(`${label} request cancelled`, 'AbortError'));
    const timer = setTimeout(() => finish(undefined, new Error(timeoutMessage ?? (writing
      ? `${label} save timed out; its outcome is unknown. Review the refreshed values before retrying`
      : `${label} refresh timed out. Retry when ready.`))), writing ? settingsSaveTimeout : settingsReadTimeout);
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    void readCommand(command, request.signal).then((frame) => finish(frame), (cause: unknown) => finish(undefined, cause instanceof Error ? cause : new Error('Settings request failed')));
  });
}
const isRecord = (value: unknown): value is UnknownRecord => !!value && typeof value === 'object' && !Array.isArray(value);
export function validateSettingsRead(frame: AuxiliaryFrame | undefined, type: string, group?: string): asserts frame is AuxiliaryFrame {
  if (!frame || frame.type !== type || !isRecord(frame.settings) || (group !== undefined && frame.group !== group)) throw new Error('Could not load matching settings. Retry the refresh.');
}
export function validateSettingsAcknowledgement(frame: AuxiliaryFrame, scope: string, group: string) {
  // The current daemon returns state for global/group writes and ok for
  // Engineer/Architect writes. Typed settings responses are also supported.
  if (scope === 'ai') {
    if (frame.type === 'ai_settings' && isRecord(frame.settings)) return;
  } else {
    if (frame.type === 'ok' || frame.type === 'state') return;
    if (frame.type === `${scope}_settings` && isRecord(frame.settings) && (scope === 'global' || frame.group === group)) return;
  }
  throw new Error(`The daemon returned an invalid ${scope === 'ai' ? 'AI' : scope} settings acknowledgement; the outcome is unknown`);
}
