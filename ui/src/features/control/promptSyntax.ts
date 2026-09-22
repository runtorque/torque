export type PromptToken = { text: string; kind?: 'expression' | 'statement' | 'comment' | 'string' | 'filter' | 'paren'; children?: PromptToken[] };
/** Presentation only: concatenating token text always reproduces the authored prompt. */
export function promptTokens(value: string): PromptToken[] {
  const tokens: PromptToken[] = []; let offset = 0; const openings = /\{\{|\{%|\{#/g; let match: RegExpExecArray | null;
  while ((match = openings.exec(value))) {
    const start = match.index; const opener = match[0]; const close = opener === '{{' ? '}}' : opener === '{%' ? '%}' : '#}';
    if (start > offset) tokens.push({ text: value.slice(offset, start) });
    let end = start + 2; let quote = '';
    while (end < value.length) {
      const character = value[end]!;
      if (opener !== '{#') {
        if (quote) { if (character === '\\') { end += 2; continue; } if (character === quote) quote = ''; end++; continue; }
        if (character === '"' || character === "'") { quote = character; end++; continue; }
      }
      if (value.startsWith(close, end)) break;
      end++;
    }
    const bodyEnd = Math.min(end, value.length); end = Math.min(end + close.length, value.length);
    const text = value.slice(start, end); const kind = opener === '{{' ? 'expression' : opener === '{%' ? 'statement' : 'comment';
    tokens.push({ text, kind, ...(kind === 'comment' ? {} : { children: [{ text: opener }, ...innerTokens(value.slice(start + 2, bodyEnd)), { text: value.slice(bodyEnd, end) }] }) });
    offset = end; openings.lastIndex = end;
  }
  if (offset < value.length) tokens.push({ text: value.slice(offset) });
  return tokens;
}
function innerTokens(value: string): PromptToken[] {
  const result: PromptToken[] = []; const syntax = /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\|\s*[A-Za-z_]\w*|[()]/g; let offset = 0;
  for (const match of value.matchAll(syntax)) {
    if (match.index > offset) result.push({ text: value.slice(offset, match.index) });
    const text = match[0]; result.push({ text, kind: text.startsWith('|') ? 'filter' : text === '(' || text === ')' ? 'paren' : 'string' }); offset = match.index + text.length;
  }
  if (offset < value.length) result.push({ text: value.slice(offset) }); return result;
}
