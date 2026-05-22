import type { RelayEnvelope } from "../../src/core/protocol.js";
import type { RelaySocket } from "../../src/core/ports.js";

export class MemorySocket implements RelaySocket {
  readonly sent: RelayEnvelope[] = [];
  readonly closes: { code?: number; reason?: string }[] = [];
  constructor(readonly id: string) {}
  sendEnvelope(envelope: RelayEnvelope): void {
    this.sent.push(envelope);
  }
  close(code?: number, reason?: string): void {
    this.closes.push({ code, reason });
  }
}
