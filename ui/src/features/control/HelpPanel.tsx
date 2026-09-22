import { useCallback, useEffect, useRef, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import { Button } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { HelpMarkdown } from './HelpMarkdown';
import { display, record, reference, rows, string, validateHelp } from './helpModel';
import styles from './HelpPanel.module.css';

function useHelpRead(command: TorqueCommand | null, refresh: string, onAccepted?: (frame: UnknownRecord) => void) {
  const connection = useAppSelector(selectConnection);
  const key = command ? JSON.stringify(command) : '';
  const request = `${key}:${refresh}:${connection.reconnectCount}:${connection.status}`;
  const [accepted, setAccepted] = useState<{ key: string; frame: UnknownRecord }>();
  const [settled, setSettled] = useState<{ request: string; error: string }>();
  useEffect(() => {
    if (!key || connection.status !== 'connected') return;
    const controller = new AbortController(); const current = JSON.parse(key) as TorqueCommand;
    void readCommand(current, controller.signal).then((frame) => {
      if (controller.signal.aborted) return;
      validateHelp(current, frame); setAccepted({ key, frame }); onAccepted?.(frame); setSettled({ request, error: '' });
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setSettled({ request, error: cause instanceof Error ? cause.message : 'Help unavailable.' }); });
    return () => controller.abort();
  }, [key, request, connection.status, onAccepted]);
  return { frame: accepted?.key === key ? accepted.frame : {}, pending: !!key && connection.status === 'connected' && settled?.request !== request, error: settled?.request === request ? settled.error : '' };
}
export function HelpPanel({ refreshVersion = 0 }: { refreshVersion?: number }) {
  const connection = useAppSelector(selectConnection);
  const [audience, setAudience] = useState('');
  const [selected, setSelected] = useState('');
  const [searchDraft, setSearchDraft] = useState(''); const [search, setSearch] = useState<string | null>(null);
  const [questionDraft, setQuestionDraft] = useState(''); const [question, setQuestion] = useState<string | null>(null);
  const [refresh, setRefresh] = useState({ list: 0, detail: 0, search: 0, answer: 0 });
  const retry = (kind: keyof typeof refresh) => setRefresh((value) => ({ ...value, [kind]: value[kind] + 1 }));
  const acceptTopics = useCallback((frame: UnknownRecord) => setSelected((current) => current || reference(rows(frame.topics)[0] ?? {})), []);
  const topicsRead = useHelpRead({ cmd: 'help_list', audience }, `${refreshVersion}:${refresh.list}`, acceptTopics);
  const topics = rows(topicsRead.frame.topics);
  const currentRef = selected || reference(topics[0] ?? {});
  const detailRead = useHelpRead(currentRef ? { cmd: 'help_show', topic: currentRef, max_chars: 16000 } : null, `${refreshVersion}:${refresh.detail}`);
  const searchRead = useHelpRead(search ? { cmd: 'help_search', query: search, limit: 12 } : null, `${refreshVersion}:${refresh.search}`);
  const answerRead = useHelpRead(question ? { cmd: 'help_query', question, limit: 5 } : null, `${refreshVersion}:${refresh.answer}`);
  const article = useRef<HTMLElement>(null); const focusRef = useRef('');
  const detail = detailRead.frame; const sourceModel = record(detail.source_model ?? topicsRead.frame.source_model);
  const paths = Array.isArray(sourceModel.source_paths) ? sourceModel.source_paths.filter((path): path is string => typeof path === 'string') : topics.map((topic) => string(topic.source_path));
  const open = (ref: string) => { if (!ref) return; setSelected(ref); focusRef.current = ref; retry('detail'); };
  useEffect(() => {
    if (detailRead.pending || typeof detail.body_excerpt !== 'string' || focusRef.current !== currentRef) return;
    focusRef.current = ''; article.current?.focus({ preventScroll: true }); if (article.current) article.current.scrollTop = 0;
  }, [detailRead.pending, detail.body_excerpt, currentRef]);
  const clearSearch = () => { setSearch(null); setSearchDraft(''); };
  const clearAnswer = () => { setQuestion(null); setQuestionDraft(''); };
  const items = search !== null ? rows(searchRead.frame.results) : topics;
  const error = (kind: keyof typeof refresh, message: string) => message ? <p role="alert">Help request failed. {message} <Button onPress={() => retry(kind)}>Retry {kind === 'list' ? 'topics' : kind}</Button></p> : null;
  return <section className={styles.root} aria-label="Torque Help">
    <header className={styles.toolbar}>
      <form onSubmit={(event) => { event.preventDefault(); setSearch(searchDraft.trim()); retry('search'); }}>
        <label>Search documentation<input value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); clearSearch(); } }} /></label>
        <Button type="submit">Search</Button><Button tone="quiet" onPress={clearSearch}>All topics</Button>
      </form>
      <form onSubmit={(event) => { event.preventDefault(); setQuestion(questionDraft.trim()); retry('answer'); }}>
        <label>Question for the docs<input value={questionDraft} onChange={(event) => setQuestionDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); clearAnswer(); } }} /></label>
        <Button type="submit">Answer from docs</Button><Button tone="quiet" onPress={clearAnswer}>Clear answer</Button>
      </form>
      <p>Read-only, deterministic lookup of maintained documentation. Workspace data is not searched.</p>
      {connection.status !== 'connected' ? <p role="status">Help is offline. Accepted content remains available; reconnect to refresh.</p> : null}
    </header>
    <div className={styles.split}>
      <aside className={styles.list} aria-label="Help topics">
        <header><h2>{search !== null ? 'Search results' : 'Topics'} · {items.length}</h2><label>Audience for topics<select value={audience} onChange={(event) => { setAudience(event.target.value); setSelected(''); focusRef.current = ''; clearSearch(); }}>
          <option value="">All audiences</option>{['user', 'operator', 'agent', 'worker', 'engineer', 'architect', 'maintainer'].map((value) => <option key={value} value={value}>{value}</option>)}
        </select></label></header>
        {error('list', topicsRead.error)}{search !== null ? error('search', searchRead.error) : null}
        {search !== null ? <p role="status">{search === '' ? 'Enter a search query.' : searchRead.pending ? 'Searching…' : searchRead.error ? 'Search unavailable.' : items.length ? `Results for “${search}”` : `No matching documentation for “${search}”.`}</p> : topicsRead.pending ? <p role="status">Refreshing topics…</p> : !items.length ? <p>No topics for this audience.</p> : null}
        {items.map((item) => <button key={reference(item)} aria-current={currentRef === reference(item) ? 'page' : undefined} onClick={() => open(reference(item))}><strong>{string(item.title) || string(item.topic_title)}</strong><small>{reference(item)}</small><p>{string(item.excerpt) || string(item.summary)}</p></button>)}
      </aside>
      <article className={styles.document} ref={article} tabIndex={-1} aria-label="Help document">
        {question !== null ? <section className={styles.answer} aria-label="Documentation answer"><h2>Documentation answer</h2><p>{question ? `Question: ${question}` : 'Enter a question.'}</p>{error('answer', answerRead.error)}{answerRead.pending ? <p role="status">Looking up documentation…</p> : null}{string(answerRead.frame.answer) ? <><HelpMarkdown source={string(detail.source_path)} paths={paths} onOpen={open}>{string(answerRead.frame.answer)}</HelpMarkdown><nav aria-label="Answer sources">{rows(answerRead.frame.sources).map((source) => <Button key={reference(source)} tone="quiet" onPress={() => open(reference(source))}>{string(source.title)} · {reference(source)}</Button>)}</nav></> : null}</section> : null}
        {error('detail', detailRead.error)}{detailRead.pending ? <p role="status">Refreshing document…</p> : null}
        {typeof detail.body_excerpt === 'string' ? <>
          <header><h2>{string(detail.title)}</h2><p>{reference(detail)}</p><p>{Array.isArray(detail.audience_tags) ? detail.audience_tags.join(' · ') : ''}{detail.restricted_safe === true ? ' · Restricted-safe documentation' : ''}</p><p>{string(detail.summary)}</p>{detail.anchor ? <Button tone="quiet" onPress={() => open(string(detail.source_path))}>Open whole topic</Button> : null}</header>
          {detail.truncated === true ? <p className={styles.notice}>This is a bounded excerpt. Open a section or search the documentation to read beyond it.</p> : null}
          <details key={`sections:${string(detail.source_path)}`}><summary>Sections · {rows(detail.sections).length}</summary><nav aria-label="Document sections">{rows(detail.sections).map((section) => <details key={reference(section)}><summary>{string(section.title)} · lines {display(section.line_start, '?')}–{display(section.line_end, '?')}</summary><p>{reference(section)}</p><Button tone="quiet" onPress={() => open(reference(section))}>Open section: {string(section.title)}</Button></details>)}</nav></details>
          <HelpMarkdown source={string(detail.source_path)} paths={paths} onOpen={open}>{detail.body_excerpt}</HelpMarkdown>
          <details key={`examples:${string(detail.source_path)}`}><summary>Extracted examples</summary>{Array.isArray(detail.examples) && detail.examples.length ? detail.examples.map((example, index) => <pre key={index}><code>{string(example)}</code></pre>) : <p>No extracted examples for this topic.</p>}</details>
          <details key={`freshness:${string(detail.source_path)}`}><summary>Source and freshness</summary><dl>{Object.entries({ Source: reference(detail), Updated: detail.updated_at, 'Source hash': detail.source_hash, 'Index hash': detail.index_hash, Cache: sourceModel.cache, Allowlist: Array.isArray(sourceModel.allowlist) ? sourceModel.allowlist.join(', ') : sourceModel.allowlist, 'Indexed sources': paths.length }).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{display(value)}</dd></div>)}</dl></details>
        </> : !detailRead.pending && !detailRead.error ? <p>Select a maintained topic or search the documentation.</p> : null}
      </article>
    </div>
  </section>;
}
