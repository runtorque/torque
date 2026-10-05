import { useEffect, useId, useRef, useState, type RefObject } from 'react';
import { Button } from '../../design/primitives';
import styles from './SettingsSearch.module.css';

type Control = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement;
interface Match { control: Control; label: string; scope: string; description: string; disabled: boolean }
const compact = (text: string) => text.replace(/\s+/g, ' ').trim();

// Read labels/help from the mounted form, including closed disclosures. Never
// index values: edited secrets and free-form drafts are not search content.
function matches(form: HTMLFormElement, query: string): Match[] {
  const words = compact(query).toLocaleLowerCase().split(' ').filter(Boolean);
  if (!words.length) return [];
  return [...form.querySelectorAll<Control>('input, select, textarea, button[aria-pressed]')].flatMap((control) => {
    if (control.type === 'search' || control.type === 'hidden' || control.closest('[hidden], [aria-hidden="true"]')) return [];
    const labels = [...(control.labels ?? [])].map((label) => {
      const copy = label.cloneNode(true) as HTMLElement;
      copy.querySelectorAll('input, select, textarea, button').forEach((node) => node.remove());
      return compact(copy.textContent || '');
    });
    const label = control.getAttribute('aria-label') || labels.join(' ');
    if (!label) return [];
    const scope: string[] = [];
    for (let parent = control.parentElement; parent && parent !== form; parent = parent.parentElement) {
      const title = parent.matches('details') ? parent.querySelector(':scope > summary') : parent.matches('section') ? parent.querySelector(':scope > h3') : null;
      if (title?.textContent) scope.unshift(compact(title.textContent));
    }
    const description = (control.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean).map((id) => control.ownerDocument.getElementById(id)?.textContent || '').join(' ');
    const haystack = `${label} ${scope.join(' ')} ${description}`.toLocaleLowerCase();
    return words.every((word) => haystack.includes(word)) ? [{ control, label, scope: scope.join(' › '), description, disabled: control.matches(':disabled') }] : [];
  });
}

export function SettingsSearch({ form }: { form: RefObject<HTMLFormElement | null> }) {
  const [query, setQuery] = useState(''); const queryRef = useRef('');
  const [results, setResults] = useState<Match[]>([]); const [limit, setLimit] = useState(20);
  const root = useRef<HTMLDivElement>(null); const input = useRef<HTMLInputElement>(null); const id = useId();
  const search = (value: string) => {
    queryRef.current = value; setQuery(value); setLimit(20);
    setResults(form.current ? matches(form.current, value) : []);
  };
  useEffect(() => {
    const current = form.current; if (!current) return;
    // Reconnect and conditional controls can update the mounted form without
    // changing the query. Ignore this component's own results to avoid loops.
    const observer = new MutationObserver((records) => {
      if (!queryRef.current.trim() || records.every((record) => root.current?.contains(record.target))) return;
      const next = matches(current, queryRef.current);
      setResults((previous) => previous.length === next.length && previous.every((item, index) => {
        const updated = next[index]!;
        return item.control === updated.control && item.label === updated.label && item.scope === updated.scope && item.description === updated.description && item.disabled === updated.disabled;
      }) ? previous : next);
    });
    observer.observe(current, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['aria-label', 'aria-describedby', 'hidden', 'aria-hidden', 'disabled'] });
    return () => observer.disconnect();
  }, [form]);
  const clear = () => { search(''); input.current?.focus(); };
  const reveal = (match: Match) => {
    if (!form.current?.contains(match.control)) { search(query); return; }
    for (let parent = match.control.parentElement; parent && parent !== form.current; parent = parent.parentElement) {
      if (parent instanceof HTMLDetailsElement) parent.open = true;
    }
    if (match.control.matches(':disabled')) return;
    search('');
    match.control.focus({ preventScroll: true });
    requestAnimationFrame(() => { if (form.current?.contains(match.control)) match.control.scrollIntoView?.({ block: 'center', behavior: 'instant' }); });
  };
  return <div ref={root} className={styles.search}>
    <div className={styles.row}><label>Search settings<input ref={input} type="search" value={query} aria-controls={id} placeholder="Search labels, descriptions and scopes" onChange={(event) => search(event.target.value)} onKeyDown={(event) => {
      if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); const first = results[0]; if (first && !first.control.matches(':disabled')) reveal(first); }
      if (event.key === 'Escape' && query) { event.preventDefault(); event.stopPropagation(); clear(); }
      if (event.key === 'ArrowDown' && results.length) { event.preventDefault(); root.current?.querySelector<HTMLButtonElement>('li button')?.focus(); }
    }} /></label>{query ? <Button tone="quiet" onPress={clear}>Clear settings search</Button> : null}</div>
    <div id={id}>{query.trim() ? <><p role="status">{results.length ? `${results.length} matching settings` : `No settings found for “${query}”`}</p><ul className={styles.results} aria-label="Settings search results">{results.slice(0, limit).map((match, index) => <li key={`${match.scope}:${match.label}:${index}`}><Button tone="quiet" isDisabled={match.disabled} aria-label={`${match.label} — ${match.scope}`} onPress={() => reveal(match)}><span>{match.label}</span><small>{match.scope}</small></Button></li>)}</ul>{results.length > limit ? <Button tone="quiet" onPress={() => setLimit((value) => value + 20)}>Show more settings</Button> : null}</> : null}</div>
  </div>;
}
