import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { promptTokens, type PromptToken } from './promptSyntax';
import styles from './PromptEditor.module.css';
function renderTokens(tokens: PromptToken[]): ReactNode {
  return tokens.map((token, index) => token.kind ? <span key={index} className={styles[token.kind]} data-syntax={token.kind}>{token.children ? renderTokens(token.children) : token.text}</span> : token.text);
}
export function PromptEditor({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const input = useRef<HTMLTextAreaElement>(null); const backdrop = useRef<HTMLPreElement>(null);
  const sync = () => { if (input.current && backdrop.current) { backdrop.current.scrollTop = input.current.scrollTop; backdrop.current.scrollLeft = input.current.scrollLeft; } };
  useLayoutEffect(sync, [value]);
  return <div className={styles.root}>
    <pre className={styles.backdrop} ref={backdrop} aria-hidden="true">{renderTokens(promptTokens(value))}{'\u200b'}</pre>
    <textarea className={styles.input} ref={input} value={value} onChange={(event) => onChange(event.target.value)} onScroll={sync} spellCheck={false} autoCapitalize="off" autoCorrect="off" />
  </div>;
}
