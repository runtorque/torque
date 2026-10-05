import type { UnknownRecord } from './types';

export interface TorqueCommand extends UnknownRecord {
  cmd: string;
}

export interface CommandResult<T = UnknownRecord> extends UnknownRecord {
  type: string;
  ok?: boolean;
  data?: T;
  error?: string;
}
