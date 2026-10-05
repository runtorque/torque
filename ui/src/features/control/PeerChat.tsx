import { useLayoutEffect, useMemo, useRef, useState } from 'react';

import { Button, StateSurface } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import styles from './ParityPanels.module.css';

function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
function text(value: unknown, fallback = ''): string { return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback; }
function list(value: unknown): UnknownRecord[] { return Array.isArray(value) ? value.map(record) : []; }
function timeValue(value: unknown): number { const n = Number(value); return Number.isFinite(n) ? (n < 1e12 ? n * 1000 : n) : Date.parse(text(value)) || 0; }
function time(value: unknown): string { const n = timeValue(value); return n ? new Date(n).toLocaleString() : '—'; }
function body(message: UnknownRecord): string { return text(message.raw_message, text(message.message, text(message.content, text(message.text)))); }

function peerThreads(value: Record<string, unknown>): UnknownRecord[] {
  return Object.entries(value).map(([id, item]) => ({ ...record(item), thread_id: text(record(item).thread_id, id) }))
    .sort((a: UnknownRecord, b: UnknownRecord) => timeValue(b.last_activity_at ?? b.last_message_at ?? record(b.last_message).timestamp) - timeValue(a.last_activity_at ?? a.last_message_at ?? record(a.last_message).timestamp) || text(a.thread_id).localeCompare(text(b.thread_id)));
}

function ThreadMessages({ thread, agents }: { thread: UnknownRecord; agents: Record<string, unknown> }) {
  const [count, setCount] = useState(40);
  const [copyStatus, setCopyStatus] = useState('');
  const messages = list(thread.messages);
  const shown = messages.slice(-count);
  const viewport = useRef<HTMLDivElement>(null);
  const beforePrepend = useRef<{ height: number; top: number } | null>(null);
  const pinned = useRef(true);
  const showOlder = () => {
    if (!viewport.current) return;
    beforePrepend.current = { height: viewport.current.scrollHeight, top: viewport.current.scrollTop };
    setCount((value) => value + 40);
  };
  useLayoutEffect(() => {
    const node = viewport.current;
    if (!node) return;
    if (beforePrepend.current) {
      node.scrollTop = beforePrepend.current.top + node.scrollHeight - beforePrepend.current.height;
      beforePrepend.current = null;
    } else if (pinned.current) node.scrollTop = node.scrollHeight;
  }, [count, thread.messages]);
  const agentName = (id: unknown) => text(record(agents[text(id)]).name, text(id, 'Unknown'));
  return <section className={styles.messageDetail} aria-label="Peer thread messages"><h3>{text(thread.title, 'Peer thread')} · {messages.length} messages</h3>
    {Number(thread.message_count) > messages.length ? <p>Showing the latest {messages.length} retained messages of {text(thread.message_count)}. Earlier messages are not included in this snapshot.</p> : null}
    <div ref={viewport} className={styles.messages} onScroll={(event) => { const node = event.currentTarget; pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 40; }}>
      {messages.length > count ? <Button onPress={showOlder}>Load older messages ({messages.length - count})</Button> : null}
      {!messages.length ? <p>No messages in this thread.</p> : shown.map((message, index) => {
        const context = record(message.context);
        const summary = text(message.context_summary, text(context.summary));
        const contexts = ['task', 'engineer', 'decision'].map((kind) => ({ kind, ids: message[`context_${kind}_ids`] ?? context[`${kind}_ids`] })).filter((entry) => Array.isArray(entry.ids) && entry.ids.length);
        return <article key={text(message.id, `${text(thread.thread_id)}-${messages.length - shown.length + index}`)}>
          <header><strong>From: {text(message.sender_name, agentName(message.sender_id))}</strong><span>To: {text(message.recipient_name, agentName(message.recipient_id))}</span><time>{time(message.timestamp ?? message.created_at ?? message.sent_at)}</time></header>
          <small>{text(message.action, 'message').replaceAll('_', ' ')}{message.ack_required ? ' · Ack required' : ''}{message.delivery_state ? ` · ${text(message.delivery_state)}` : ''}</small>
          <p>{body(message)}</p>
          {summary || contexts.length ? <details><summary>Context details</summary>{summary ? <p>{summary}</p> : null}<dl>{contexts.map(({ kind, ids }) => <div key={kind}><dt>{kind}</dt><dd>{(ids as unknown[]).map((id) => kind === 'engineer' ? agentName(id) : text(id)).join(', ')}</dd></div>)}</dl></details> : null}
          <Button tone="quiet" onPress={() => { void navigator.clipboard.writeText(body(message)).then(() => setCopyStatus('Message copied'), () => setCopyStatus('Copy failed; select the message text to copy.')); }}>Copy message</Button>
        </article>;
      })}
    </div><span role="status">{copyStatus}</span>
  </section>;
}

export function PeerChat({ threads, agents }: { threads: Record<string, unknown>; agents: Record<string, unknown> }) {
  const [selected, setSelected] = useState('');
  const [search, setSearch] = useState('');
  const [count, setCount] = useState(40);
  const items = useMemo(() => peerThreads(threads), [threads]);
  const visible = items.filter((thread) => !search.trim() || JSON.stringify([thread.title, thread.participants, thread.last_message]).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const active = items.find((thread) => text(thread.thread_id) === selected) ?? visible[0];
  return <section className={styles.panel} aria-label="Aggregate peer Chat">
    <header className={styles.toolbar}><h2>Peer Chat</h2><span>All groups · read-only · {items.length} threads</span><label>Search peer threads<input value={search} onChange={(event) => { setSearch(event.target.value); setCount(40); }} /></label></header>
    <div className={styles.split}><aside className={styles.threadList} aria-label="Peer threads">{visible.slice(0, count).map((thread) => <button key={text(thread.thread_id)} aria-pressed={thread === active} onClick={() => setSelected(text(thread.thread_id))}><strong>{text(thread.title, list(thread.participants).map((p) => text(p.name, text(p.id))).join(' ↔ ') || 'Peer thread')}</strong><small>{time(thread.last_activity_at ?? thread.last_message_at)}</small><span>{body(record(thread.last_message)).split('\n')[0]}</span><small>{text(thread.message_count, String(list(thread.messages).length))} messages</small></button>)}{visible.length > count ? <Button onPress={() => setCount((value) => value + 40)}>Load more threads</Button> : null}{!visible.length ? <p>No matching peer threads.</p> : null}</aside>
      {active ? <ThreadMessages key={text(active.thread_id)} thread={active} agents={agents} /> : <StateSurface title="No peer conversations" description="Agent-to-agent threads appear here across the entire workspace." />}
    </div>
  </section>;
}
