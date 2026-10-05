export interface LogLine { ts?: number; level?: string; message?: string; raw?: string }
export interface LogPage { target?: string; lines: LogLine[]; cursor: number; inode?: string; size?: number }

export function matchesLog(line: LogLine, level: string, search: string): boolean {
  if (level && line.level !== level) return false;
  const query = search.trim();
  if (!query) return true;
  const value = line.raw || line.message || '';
  try { return new RegExp(query, 'i').test(value); }
  catch { return value.toLocaleLowerCase().includes(query.toLocaleLowerCase()); }
}
