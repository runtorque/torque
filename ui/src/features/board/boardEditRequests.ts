import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { observeBoardRequest } from './boardReadRequest';
const record = (value: unknown): UnknownRecord | undefined => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : undefined;

export function validateBoardEditAcknowledgement(command: TorqueCommand, frame: AuxiliaryFrame) {
  if (frame.type === 'error' || frame.type === 'finalization_blocked') throw new Error(typeof frame.message === 'string' ? frame.message : 'Task changes are blocked or could not be saved.');
  // These existing commands acknowledge with the full state, not an echoed command.
  const id = command.cmd === 'remove_attachment' ? command.task_id : command.id;
  const task = record(record(frame.board_tasks)?.[String(id)]);
  if (frame.type !== 'state' || typeof frame.seq !== 'number' || !Number.isFinite(frame.seq) || !task || task.id !== id) throw new Error('Board returned an invalid acknowledgement; the outcome is unknown. Review the task before retrying.');
}

export function uploadBoardFile(taskId: string, file: File, signal: AbortSignal) {
  return observeBoardRequest(async (transport) => {
    const body = new FormData(); body.append('task_id', taskId); body.append('file', file);
    const response = await fetch('/api/upload', { method: 'POST', body, signal: transport });
    const payload = await response.json() as { ok?: boolean; error?: string; data?: unknown[] };
    if (!response.ok || !payload.ok) throw new Error(payload.error || 'Upload failed.');
    if (!Array.isArray(payload.data) || !payload.data.length) throw new Error('Board returned an invalid upload acknowledgement; the outcome is unknown.');
    return payload.data.map((value) => {
      const entry = record(value);
      if (!entry || typeof entry.filename !== 'string' || !entry.filename || /[/\\]/.test(entry.filename) || typeof entry.path !== 'string' || !entry.path.replaceAll('\\', '/').endsWith(`/${taskId}/${entry.filename}`)) throw new Error('Board returned an invalid upload acknowledgement; the outcome is unknown.');
      return entry;
    });
  }, signal);
}
