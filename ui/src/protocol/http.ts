import type { TorqueCommand } from './commands';
import type { AuxiliaryFrame } from './types';

export class CommandResponseError extends Error {
  readonly status: number;
  readonly creationRefused: boolean;
  constructor(message: string, status: number, creationRefused = false) { super(message); this.status = status; this.creationRefused = creationRefused; }
}

/** Correlated, cancellable reads using the daemon's existing command endpoint. */
export async function readCommand(command: TorqueCommand, signal: AbortSignal): Promise<AuxiliaryFrame> {
  const response = await fetch('/api/cmd', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(command), signal });
  const payload = await response.json() as { ok?: boolean; error?: string; creation_refused?: boolean; data?: AuxiliaryFrame };
  if (!response.ok || !payload.ok || !payload.data) {
    const message = payload.error || `Request failed (${response.status})`;
    if (payload.ok === false) throw new CommandResponseError(message, response.status ?? 200, payload.creation_refused === true);
    throw new Error(message);
  }
  return { ...payload.data, type: typeof payload.data.type === 'string' ? payload.data.type : 'ok' };
}
