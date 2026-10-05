import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';

/** Bound observation even when a transport ignores cancellation. */
export function planningRequest(command: TorqueCommand, signal: AbortSignal, writing = false): Promise<AuxiliaryFrame> {
  return new Promise((resolve, reject) => {
    const transport = new AbortController(); let settled = false;
    const finish = (frame?: AuxiliaryFrame, error?: Error) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal.removeEventListener('abort', abort);
      if (error) { transport.abort(); reject(error); } else resolve(frame!);
    };
    const abort = () => finish(undefined, new DOMException('Planning request cancelled', 'AbortError'));
    const timer = setTimeout(() => finish(undefined, new Error(writing
      ? 'Planning save timed out; its outcome is unknown. Review current records before retrying.'
      : 'Planning details or options timed out. Retry when ready.')), writing ? 30_000 : 15_000);
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    void readCommand(command, transport.signal).then((frame) => finish(frame), (cause: unknown) => finish(undefined, cause instanceof Error ? cause : new Error('Planning request failed.')));
  });
}

const record = (value: unknown): UnknownRecord | undefined => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : undefined;
const sameId = (actual: unknown, expected: unknown) => (typeof actual === 'string' || typeof actual === 'number') && String(actual) !== '' && String(actual) === String(expected);
const hasId = (value: unknown) => (typeof value === 'string' && value.length > 0) || typeof value === 'number';

/** Match the existing daemon contracts before accepting a write or continuing a sequence. */
export function validatePlanningAcknowledgement(command: TorqueCommand, frame: AuxiliaryFrame) {
  if (frame.type === 'error') throw new Error(typeof frame.message === 'string' ? frame.message : 'The Planning request failed.');
  const invalid = () => { throw new Error('Planning returned an invalid acknowledgement; the outcome is unknown. Review current records before retrying.'); };
  // Creation deliberately returns only its minted ID and creation timestamp;
  // updates and links return the full decision with its Architect ownership.
  if (command.cmd === 'architect_decision_create') {
    if (frame.type !== 'ok' || typeof frame.id !== 'string' || !frame.id || typeof frame.created_at !== 'number' || !Number.isFinite(frame.created_at) || (frame.architect_id !== undefined && !sameId(frame.architect_id, command.architect_id))) invalid();
    return;
  }
  if (/^architect_decision_(update|link)$/.test(command.cmd)) {
    if (!['ok', 'decision'].includes(frame.type) || !hasId(frame.id) || !sameId(frame.architect_id, command.architect_id) || (command.id !== undefined && !sameId(frame.id, command.id))) invalid();
    return;
  }
  const link = /^(area|initiative)_(link|unlink)_(task|decision|initiative|area)$/.exec(command.cmd);
  if (link) {
    const [, owner, action, kind] = link;
    const type = owner === 'area' ? `area_${action === 'link' ? 'linked' : 'unlinked'}` : `initiative_${kind}_${action === 'link' ? 'linked' : 'unlinked'}`;
    if (frame.type !== type) invalid();
    if (action === 'unlink') { if (typeof frame.removed !== 'boolean') invalid(); return; }
    const value = record(frame.link);
    if (!value || !sameId(value[`${owner}_id`], command.id) || value.link_type !== kind || !sameId(value.target_id, command.target_id ?? command[`${kind}_id`]) || (owner === 'area' && kind === 'area' && value.relation !== (command.relation || 'related'))) invalid();
    return;
  }
  const mutation = /^(area_note|area|initiative|scratchpad_note|idea_brief)_(create|update|archive|delete|refine|park|propose)$/.exec(command.cmd);
  if (!mutation) { invalid(); return; }
  const [, owner, action] = mutation;
  const past: Record<string, string> = { create: 'created', update: 'updated', archive: 'archived', delete: 'deleted', refine: 'refined', park: 'parked', propose: 'proposed' };
  const key = owner === 'area_note' || owner === 'scratchpad_note' ? 'note' : owner!;
  const value = record(frame[key]);
  if (frame.type !== `${owner}_${past[action!]}` || !value || !hasId(value.id)) { invalid(); return; }
  if (owner === 'area_note') {
    if (!sameId(value.area_id, command.id) || (command.note_id !== undefined && !sameId(value.id, command.note_id))) invalid();
  } else if (command.id !== undefined && !sameId(value.id, command.id)) invalid();
  if (command.group !== undefined && (value.group_name ?? value.group) !== command.group) invalid();
}
