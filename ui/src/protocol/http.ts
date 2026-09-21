import type { TorqueCommand } from './commands';
import type { AuxiliaryFrame } from './types';

/** Correlated, cancellable reads using the daemon's existing command endpoint. */
export async function readCommand(command: TorqueCommand, signal: AbortSignal): Promise<AuxiliaryFrame> {
  const response = await fetch('/api/cmd', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(command), signal });
  const payload = await response.json() as { ok?: boolean; error?: string; data?: AuxiliaryFrame };
  if (!response.ok || !payload.ok || !payload.data) throw new Error(payload.error || `Request failed (${response.status})`);
  return { ...payload.data, type: typeof payload.data.type === 'string' ? payload.data.type : 'ok' };
}
