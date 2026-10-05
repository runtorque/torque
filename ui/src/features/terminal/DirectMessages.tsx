import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useAppDispatch, useAppStore } from '../../app/hooks';
import { Button } from '../../design/primitives';
import { Markdown } from '../../design/Markdown';
import { composerActions, type MessageReading } from './composerState';
import { MESSAGE_PAGE_SIZE, messageId, messageView, orderedMessages } from './directMessageModel';
import styles from './DirectMessages.module.css';

type Agent = { id: string; name: string };
type ContextTarget = { id: string; body: string; x: number; y: number; invoker: HTMLElement };
function MessageMenu({ target, pending, onCopy, onReply, onClose }: { target: ContextTarget; pending: boolean; onCopy: () => void; onReply: () => void; onClose: (restore: boolean) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const menu = ref.current; if (!menu) return;
    const bounds = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(4, Math.min(target.x, window.innerWidth - bounds.width - 4))}px`;
    menu.style.top = `${Math.max(4, Math.min(target.y, window.innerHeight - bounds.height - 4))}px`;
    menu.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true });
  }, [target]);
  useEffect(() => {
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !ref.current?.contains(event.target)) onClose(false); };
    window.addEventListener('pointerdown', outside); return () => window.removeEventListener('pointerdown', outside);
  }, [onClose]);
  return createPortal(<div ref={ref} role="menu" aria-label="Direct message actions" className={styles.contextMenu} style={{ left: target.x, top: target.y }} onKeyDown={(event) => {
    if (event.key === 'Escape') { event.preventDefault(); onClose(true); }
    if (event.key === 'Tab') onClose(false);
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement); buttons[(current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus();
    }
  }}><button role="menuitem" type="button" onClick={onCopy}>Copy message</button><button role="menuitem" type="button" disabled={pending || !target.id} onClick={onReply}>Reply</button></div>, document.body);
}

export function DirectMessages({ agent, messages, pending, active = true, onReply }: { agent: Agent; messages: unknown; pending: boolean; active?: boolean; onReply: (id: string, body: string) => void }) {
  const dispatch = useAppDispatch(); const store = useAppStore();
  const items = useMemo(() => orderedMessages(messages), [messages]);
  const [initial] = useState<MessageReading>(() => ({ ...(store.getState().composer.readings[agent.id] ?? { count: MESSAGE_PAGE_SIZE, pinned: true, anchorId: '', offset: 0, scrollTop: 0, selectedId: '' }) }));
  const reading = useRef<MessageReading>({ ...initial });
  const [count, setCount] = useState(initial.count); const [pinned, setPinned] = useState(initial.pinned); const [selected, setSelected] = useState(initial.selectedId); const [anchorId, setAnchorId] = useState(initial.anchorId);
  const [copyNotice, setCopyNotice] = useState(''); const [menu, setMenu] = useState<ContextTarget | null>(null);
  const viewport = useRef<HTMLDivElement>(null); const ignoredScroll = useRef<number | null>(null); const loadingOlder = useRef(false);
  const anchorIndex = !pinned ? items.findIndex((row) => messageId(row) === anchorId) : -1;
  const start = Math.max(0, Math.min(items.length - count, anchorIndex < 0 ? items.length : anchorIndex));
  const shown = items.slice(start);
  const persist = useCallback(() => dispatch(composerActions.reading({ agentId: agent.id, reading: { ...reading.current } })), [agent.id, dispatch]);
  const capture = useCallback(() => {
    const node = viewport.current; if (!node || !active || !node.clientHeight) return;
    const top = node.getBoundingClientRect().top;
    const first = [...node.querySelectorAll<HTMLElement>('[data-message-id]')].find((row) => row.getBoundingClientRect().bottom > top);
    reading.current.anchorId = first?.dataset.messageId ?? ''; setAnchorId(reading.current.anchorId); reading.current.offset = first ? first.getBoundingClientRect().top - top : 0; reading.current.scrollTop = node.scrollTop;
    persist();
  }, [active, persist]);
  const restore = useCallback(() => {
    const node = viewport.current; if (!node || !active || !node.clientHeight) return;
    if (reading.current.pinned) node.scrollTop = Math.max(0, node.scrollHeight - node.clientHeight);
    else {
      const row = [...node.querySelectorAll<HTMLElement>('[data-message-id]')].find((entry) => entry.dataset.messageId === reading.current.anchorId);
      node.scrollTop = row ? node.scrollTop + row.getBoundingClientRect().top - node.getBoundingClientRect().top - reading.current.offset : reading.current.scrollTop;
    }
    ignoredScroll.current = node.scrollTop; capture();
  }, [active, capture]);
  useLayoutEffect(() => { reading.current.count = count; restore(); loadingOlder.current = false; }, [items, count, start, restore]);
  useEffect(() => {
    const node = viewport.current; if (!node || !active || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(restore); observer.observe(node); return () => observer.disconnect();
  }, [active, restore]);
  const showOlder = () => {
    if (!start || loadingOlder.current) return; capture(); reading.current.pinned = false; setPinned(false); loadingOlder.current = true;
    setCount(Math.min(items.length, items.length - start + MESSAGE_PAGE_SIZE));
  };
  const latest = () => { reading.current.pinned = true; setPinned(true); restore(); };
  const copy = async (value: string, kind: 'Message' | 'Code') => {
    try { await navigator.clipboard.writeText(value); setCopyNotice(`${kind} copied.`); }
    catch { setCopyNotice(`Could not copy ${kind.toLowerCase()}. Select the text or try again.`); }
  };
  const choose = (id: string) => { reading.current.selectedId = id; setSelected(id); persist(); };
  const closeMenu = (restoreFocus: boolean) => { if (restoreFocus) menu?.invoker.focus({ preventScroll: true }); setMenu(null); };
  return <div className={styles.history}>
    <div className={styles.toolbar}>
      <span title="The daemon supplies a bounded recent-message snapshot, not the complete archive.">{shown.length} of {items.length} retained</span>
      <Button tone="quiet" isDisabled={!start} onPress={showOlder}>Load older messages{start ? ` (${start})` : ''}</Button>
      <Button tone="quiet" aria-pressed={pinned} onPress={latest}>Latest messages</Button>
    </div>
    <div ref={viewport} role="log" aria-label={`Messages with ${agent.name}`} aria-live="polite" aria-relevant="additions text" tabIndex={0} className={styles.messages}
      onWheel={(event) => { if (event.deltaY < 0) { reading.current.pinned = false; setPinned(false); if (event.currentTarget.scrollTop <= 24) showOlder(); } }}
      onKeyDown={(event) => { if (event.target === event.currentTarget && ['ArrowUp', 'PageUp', 'Home'].includes(event.key)) { reading.current.pinned = false; setPinned(false); if (event.currentTarget.scrollTop <= 24) showOlder(); } }}
      onScroll={(event) => {
        const node = event.currentTarget; if (!active || !node.clientHeight) return;
        if (ignoredScroll.current !== null && Math.abs(node.scrollTop - ignoredScroll.current) < 1) { ignoredScroll.current = null; return; }
        ignoredScroll.current = null; const follow = node.scrollHeight - node.scrollTop - node.clientHeight <= 8;
        reading.current.pinned = follow; setPinned(follow); capture(); if (node.scrollTop <= 24 && !follow) showOlder();
      }}>
      {!items.length ? <p className={styles.empty}>No direct messages yet.</p> : shown.map((row, index) => {
        const view = messageView(row, agent); const reply = items.find((item) => messageId(item) === view.replyTo); const replyPreview = reply ? messageView(reply, agent).body.replace(/\s+/g, ' ').slice(0, 120) : view.replyTo;
        return <article key={view.id || `legacy-${start + index}`} tabIndex={0} aria-label={`Message from ${view.sender}`} data-message-id={view.id} data-direction={view.direction} data-selected={selected === view.id && !!view.id} className={styles.message}
          onClick={(event) => { if ((event.target as Element).closest('button, a') || window.getSelection()?.toString()) return; choose(view.id); }}
          onKeyDown={(event) => { if (event.target === event.currentTarget && ['Enter', ' '].includes(event.key)) { event.preventDefault(); choose(view.id); } }}
          onContextMenu={(event) => { event.preventDefault(); const rect = event.currentTarget.getBoundingClientRect(); setMenu({ id: view.id, body: view.body, x: event.clientX || rect.left, y: event.clientY || rect.top, invoker: event.currentTarget }); }}>
          <header><strong>{view.sender}</strong>{view.label ? <span>{view.label}</span> : null}{view.iso ? <time dateTime={view.iso} title={new Date(view.iso).toLocaleString()}>{new Date(view.iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time> : null}</header>
          {view.deliveryLabel ? <div className={styles.delivery} data-delivery={view.delivery}>{view.deliveryLabel}{view.reason ? ` · ${view.reason}` : ''}</div> : null}
          {view.replyTo ? <p className={styles.reply}>In reply to: {replyPreview}</p> : null}
          <Markdown renderCode={(code, language) => <div className={styles.codeBlock}><div><span>{language}</span><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => { void copy(code, 'Code'); }}>Copy code</button></div><pre><code>{code}</code></pre></div>}>{view.body}</Markdown>
          <footer><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => { void copy(view.body, 'Message'); }}>Copy message</button><Button tone="quiet" isDisabled={pending || !view.id} onPress={() => onReply(view.id, view.body)}>Reply</Button></footer>
        </article>;
      })}
    </div>
    {copyNotice ? <div className={styles.copyNotice} role="status">{copyNotice}<button type="button" aria-label="Dismiss copy status" onClick={() => setCopyNotice('')}>×</button></div> : null}
    {menu && active ? <MessageMenu target={menu} pending={pending} onCopy={() => { void copy(menu.body, 'Message'); closeMenu(true); }} onReply={() => { onReply(menu.id, menu.body); closeMenu(false); }} onClose={closeMenu} /> : null}
  </div>;
}
