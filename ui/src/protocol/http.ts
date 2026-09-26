import type { TorqueCommand } from './commands';
import type { AuxiliaryFrame } from './types';

export class CommandResponseError extends Error {
  readonly status: number;
  readonly creationRefused: boolean;
  readonly deliveryRefused: boolean;
  readonly deliveryUncertain: boolean;
  constructor(message: string, status: number, creationRefused = false, delivery: { refused?: boolean; uncertain?: boolean } = {}) { super(message); this.status = status; this.creationRefused = creationRefused; this.deliveryRefused = delivery.refused === true; this.deliveryUncertain = delivery.uncertain === true; }
}

/** Correlated, cancellable reads using the daemon's existing command endpoint. */
export async function readCommand(command: TorqueCommand, signal: AbortSignal): Promise<AuxiliaryFrame> {
  const response = await fetch('/api/cmd', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(command), signal });
  const payload = await response.json() as { ok?: boolean; error?: string; creation_refused?: boolean; delivery_refused?: boolean; delivery_uncertain?: boolean; command?: string; idempotency_key?: string; data?: AuxiliaryFrame };
  if (!response.ok || !payload.ok || !payload.data) {
    const message = payload.error || `Request failed (${response.status})`;
    const matchingDelivery = payload.command === command.cmd && !!command.idempotency_key && payload.idempotency_key === command.idempotency_key;
    if (payload.ok === false) throw new CommandResponseError(message, response.status ?? 200, payload.creation_refused === true, { refused: matchingDelivery && payload.delivery_refused === true && payload.delivery_uncertain !== true, uncertain: matchingDelivery && payload.delivery_uncertain === true });
    throw new Error(message);
  }
  return { ...payload.data, type: typeof payload.data.type === 'string' ? payload.data.type : 'ok' };
}
