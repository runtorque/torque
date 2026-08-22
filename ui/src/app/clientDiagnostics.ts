function diagnosticText(value: unknown): string {
  if (value instanceof Error) return value.message;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value);
  }
  return 'Unknown client error';
}

function removeUnsafeControlCharacters(value: string): string {
  return Array.from(value).filter((character) => {
    const code = character.charCodeAt(0);
    return code === 9 || code === 10 || code === 13 || (code >= 32 && code !== 127);
  }).join('');
}

export function sanitizeClientError(value: unknown): string {
  return removeUnsafeControlCharacters(diagnosticText(value))
    .replace(/\b(authorization|api[_-]?key|token|password|secret)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi, '$1=[redacted]')
    .trim()
    .slice(0, 512) || 'Unknown client error';
}
