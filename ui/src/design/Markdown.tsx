import { createElement, type ReactNode } from 'react';
import styles from './Markdown.module.css';
import { safeMarkdownLink, type MarkdownLink } from './markdownLinks';
interface Options {
  resolveLink: (href: string) => MarkdownLink | null;
  renderCode?: (code: string, language: string) => ReactNode;
}
// Markdown becomes React nodes: source HTML and code never enter an HTML sink.
function closing(text: string, start: number, open: string, close: string): number {
  let depth = 0;
  for (let i = start; i < text.length; i++) {
    if (text[i] === '\\') { i++; continue; }
    if (text[i] === open) depth++;
    if (text[i] === close && --depth === 0) return i;
  }
  return -1;
}
function inline(text: string, links: Options, depth = 0): ReactNode {
  if (depth > 8) return text;
  const pattern = /(`+)([^\n]*?)\1|(!?\[)|(https?:\/\/[^\s<>]+|mailto:[^\s<>]+)|(\*\*|__)([\s\S]+?)\5|([*_])([^*_]+?)\7/g;
  const nodes: ReactNode[] = []; let offset = 0; let match: RegExpExecArray | null;
  const link = (href: string, label: ReactNode, key: number) => {
    const target = links.resolveLink(href);
    return target ? target.open ? <a key={key} href={target.href} onClick={(event) => { event.preventDefault(); target.open?.(); }}>{label}</a> : <a key={key} href={target.href} target="_blank" rel="noopener noreferrer">{label}</a> : <span key={key} title="Link unavailable">{label}</span>;
  };
  while ((match = pattern.exec(text))) {
    nodes.push(text.slice(offset, match.index)); offset = pattern.lastIndex; const key = match.index;
    if (match[1]) nodes.push(<code key={key}>{match[2]}</code>);
    else if (match[3]) {
      const isImage = match[3].startsWith('!'); const start = key + (isImage ? 1 : 0);
      const labelEnd = closing(text, start, '[', ']'); const end = text[labelEnd + 1] === '(' ? closing(text, labelEnd + 1, '(', ')') : -1;
      if (labelEnd < 0 || end < 0) { nodes.push(match[3]); continue; }
      const label = inline(text.slice(start + 1, labelEnd), links, depth + 1);
      // Images stay descriptive text; rendering messages never fetches arbitrary image URLs.
      nodes.push(isImage ? <span key={key}>{label}</span> : link(text.slice(labelEnd + 2, end), label, key));
      offset = end + 1; pattern.lastIndex = offset;
    } else if (match[4]) {
      let href = match[4];
      while (/[.,;!?]$/.test(href) || (href.endsWith(')') && href.split(')').length > href.split('(').length) || (href.endsWith(']') && href.split(']').length > href.split('[').length)) href = href.slice(0, -1);
      nodes.push(link(href, href, key), match[4].slice(href.length));
    } else nodes.push(match[5] ? <strong key={key}>{inline(match[6] ?? '', links, depth + 1)}</strong> : <em key={key}>{inline(match[8] ?? '', links, depth + 1)}</em>);
  }
  nodes.push(text.slice(offset)); return nodes;
}
const listItem = (line: string) => /^(\s*)([-+*]|\d+[.)])\s+(.*)$/.exec(line);
const fence = (line: string) => /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, '|'));
const tableDivider = (line: string) => line.includes('|') && cells(line).every((cell) => /^:?-{3,}:?$/.test(cell));
function blocks(lines: string[], links: Options, depth = 0): ReactNode[] {
  if (depth > 12) return [<pre key="bounded">{lines.join('\n')}</pre>];
  const nodes: ReactNode[] = []; let i = 0;
  const starts = (at: number) => !!fence(lines[at] ?? '') || /^ {0,3}(#{1,6}\s|>\s?)/.test(lines[at] ?? '') || !!listItem(lines[at] ?? '') || tableDivider(lines[at + 1] ?? '');
  while (i < lines.length) {
    const line = lines[i] ?? ''; const key = i;
    if (!line.trim()) { i++; continue; }
    const code = fence(line);
    if (code) {
      const body: string[] = []; i++;
      while (i < lines.length && !new RegExp(`^ {0,3}${code[1]![0]}{${code[1]!.length},}\\s*$`).test(lines[i]!)) body.push(lines[i++]!);
      if (i < lines.length) i++;
      nodes.push(links.renderCode ? <div key={key}>{links.renderCode(body.join('\n'), (code[2] ?? '').trim())}</div> : <pre key={key}><code>{body.join('\n')}</code></pre>); continue;
    }
    const heading = /^ {0,3}(#{1,6})\s+(.+)$/.exec(line);
    if (heading) { nodes.push(createElement(`h${heading[1]!.length}`, { key }, inline(heading[2]!.replace(/\s+#+\s*$/, ''), links))); i++; continue; }
    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { nodes.push(<hr key={key} />); i++; continue; }
    if (/^ {0,3}>/.test(line)) {
      const quote: string[] = []; while (i < lines.length && /^ {0,3}>/.test(lines[i]!)) quote.push(lines[i++]!.replace(/^ {0,3}> ?/, ''));
      nodes.push(<blockquote key={key}>{blocks(quote, links, depth + 1)}</blockquote>); continue;
    }
    if (tableDivider(lines[i + 1] ?? '')) {
      const header = cells(line); const body: string[][] = []; i += 2;
      while (i < lines.length && lines[i]!.trim() && lines[i]!.includes('|')) body.push(cells(lines[i++]!));
      nodes.push(<div className={styles.table} key={key}><table><thead><tr>{header.map((cell, index) => <th key={index}>{inline(cell, links)}</th>)}</tr></thead><tbody>{body.map((row, index) => <tr key={index}>{header.map((_, col) => <td key={col}>{inline(row[col] ?? '', links)}</td>)}</tr>)}</tbody></table></div>); continue;
    }
    const item = listItem(line);
    if (item) {
      const indent = item[1]!.length; const ordered = /^\d/.test(item[2]!); const items: ReactNode[] = [];
      while (i < lines.length) {
        const current = listItem(lines[i]!);
        if (!current || current[1]!.length !== indent || /^\d/.test(current[2]!) !== ordered) break;
        const content = [current[3]!]; const itemKey = i++;
        while (i < lines.length && (lines[i]!.trim() === '' || /^\s+/.test(lines[i]!)) && (lines[i]!.trim() === '' || lines[i]!.search(/\S/) > indent)) {
          content.push(lines[i++]!.slice(indent + 2));
        }
        items.push(<li key={itemKey}>{blocks(content, links, depth + 1)}</li>);
      }
      nodes.push(ordered ? <ol key={key} start={parseInt(item[2]!, 10)}>{items}</ol> : <ul key={key}>{items}</ul>); continue;
    }
    const paragraph = [line]; i++;
    while (i < lines.length && lines[i]!.trim() && !starts(i)) paragraph.push(lines[i++]!);
    nodes.push(<p key={key}>{inline(paragraph.join('\n'), links)}</p>);
  }
  return nodes;
}
export function Markdown({ children, resolveLink = safeMarkdownLink, renderCode, className = '' }: { children: string; resolveLink?: Options['resolveLink']; renderCode?: Options['renderCode']; className?: string }) {
  const options: Options = { resolveLink, ...(renderCode ? { renderCode } : {}) };
  return <div className={`${styles.markdown} ${className}`}>{blocks(children.replace(/\r\n?/g, '\n').split('\n'), options)}</div>;
}
