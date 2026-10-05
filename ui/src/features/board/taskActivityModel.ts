export function taskMessageDate(value: unknown): Date | null {
  const numeric = typeof value === 'number' ? value : typeof value === 'string' && /^\d+(\.\d+)?$/.test(value) ? Number(value) : null;
  const date = numeric !== null ? new Date(numeric < 1e12 ? numeric * 1000 : numeric) : typeof value === 'string' ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? date : null;
}
