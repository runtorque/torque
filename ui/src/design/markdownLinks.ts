export interface MarkdownLink { href: string; open?: () => void }
export function safeMarkdownLink(href: string): MarkdownLink | null {
  if (!/^(https?:\/\/|mailto:)/i.test(href) || /\s|[\\]/.test(href) || [...href].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) return null;
  try { new URL(href); return { href }; } catch { return null; }
}
