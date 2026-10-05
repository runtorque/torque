import type { TorqueCommand, UnknownRecord } from '../../protocol';
export const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
export const rows = (value: unknown): UnknownRecord[] => Array.isArray(value) ? value.map(record) : [];
export const string = (value: unknown): string => typeof value === 'string' ? value : '';
export const display = (value: unknown, fallback = 'Unavailable'): string => typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
export const reference = (item: UnknownRecord): string => string(item.path_anchor) || string(item.source_path) || string(item.topic_id);
export function validateHelp(command: TorqueCommand, frame: UnknownRecord): void {
  const expected = { help_list: 'help_topics', help_show: 'help_topic', help_search: 'help_search', help_query: 'help_query' }[command.cmd];
  if (frame.type !== expected) throw new Error(string(frame.message) || 'Unexpected Help response.');
  if (command.cmd === 'help_list' && !Array.isArray(frame.topics)) throw new Error('Invalid topic list.');
  if (command.cmd === 'help_show') {
    if (frame.status === 'not_found') throw new Error(string(frame.message) || 'This topic is no longer available.');
    if (![frame.path_anchor, frame.topic_id, frame.section_id].includes(command.topic) || typeof frame.body_excerpt !== 'string') throw new Error('Help response did not match the selected topic.');
  }
  if (command.cmd === 'help_search' && (frame.query !== command.query || !Array.isArray(frame.results))) throw new Error('Help response did not match this search.');
  if (command.cmd === 'help_query' && (frame.question !== command.question || !Array.isArray(frame.sources) || typeof frame.answer !== 'string')) throw new Error('Help response did not match this question.');
}
/** Only indexed Markdown paths become in-app links. Schemes never become source references. */
export function helpLink(href: string, source: string, paths: string[]): { external?: string; topic?: string } {
  if (!href || (/\s/.test(href) || [...href].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) || href.startsWith('//') || href.includes('\\')) return {};
  if (/^(https?:\/\/|mailto:)/i.test(href)) return { external: href };
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(href)) return {};
  try {
    const url = new URL(href, `https://torque.invalid/${source}`);
    const path = decodeURIComponent(url.pathname.slice(1));
    return paths.includes(path) && !url.search ? { topic: `${path}${url.hash}` } : {};
  } catch { return {}; }
}
