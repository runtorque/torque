export class RelayError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly retryable = false,
    readonly cause?: unknown,
  ) {
    super(message);
    this.name = "RelayError";
  }
}

export class RelayStoreError extends RelayError {
  constructor(message: string, code = "relay_store_error", retryable = false, cause?: unknown) {
    super(message, code, retryable, cause);
    this.name = "RelayStoreError";
  }
}

export class RelayConflictError extends RelayStoreError {
  constructor(message: string, cause?: unknown) {
    super(message, "relay_conflict", false, cause);
    this.name = "RelayConflictError";
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
