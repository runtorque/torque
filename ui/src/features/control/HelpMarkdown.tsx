import { Markdown } from '../../design/Markdown';
import { helpLink } from './helpModel';
import styles from './HelpPanel.module.css';
export function HelpMarkdown({ children, source, paths, onOpen }: { children: string; source: string; paths: string[]; onOpen: (reference: string) => void }) {
  return <Markdown className={styles.markdown ?? ''} resolveLink={(href) => {
    const target = helpLink(href, source, paths);
    return target.external ? { href: target.external } : target.topic ? { href: `#${target.topic}`, open: () => onOpen(target.topic!) } : null;
  }}>{children}</Markdown>;
}
