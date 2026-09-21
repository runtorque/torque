import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';

import { Button, StateSurface } from '../../design/primitives';
import type { CommandSender } from '../board/BoardPanel';
import type { AgentViewModel } from '../agents/model';
import {
  acquireTerminalController,
  type TerminalConnectionStatus,
  type TerminalController,
} from './terminalController';
import styles from './TerminalSurface.module.css';

interface UploadedAttachment {
  path: string;
  filename: string;
  mime_type?: string;
  size_bytes?: number;
}

const DEFAULT_CONVERSATION_HEIGHT = 180;
const MIN_CONVERSATION_HEIGHT = 176;
const MIN_TERMINAL_HEIGHT = 180;
const SPLIT_HANDLE_HEIGHT = 7;
const RESIZE_STEP = 24;
const FALLBACK_WORKSPACE_HEIGHT = 807;
const DEFAULT_COMPOSER_HEIGHT = 54;
const MIN_COMPOSER_HEIGHT = 38;
const MAX_COMPOSER_HEIGHT = 240;
const FALLBACK_CONVERSATION_HEIGHT = 300;

function conversationHeightLimit(workspaceHeight: number): number {
  const usableHeight = workspaceHeight > 0 ? workspaceHeight : FALLBACK_WORKSPACE_HEIGHT;
  return Math.max(
    MIN_CONVERSATION_HEIGHT,
    Math.min(620, usableHeight - MIN_TERMINAL_HEIGHT - SPLIT_HANDLE_HEIGHT),
  );
}

function clampConversationHeight(height: number, workspaceHeight: number): number {
  return Math.max(
    MIN_CONVERSATION_HEIGHT,
    Math.min(conversationHeightLimit(workspaceHeight), Math.round(height)),
  );
}

function clampHeight(height: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, Math.round(height)));
}

function composerHeightLimit(conversationHeight: number, hasAttachments: boolean): number {
  const usableHeight = conversationHeight > 0 ? conversationHeight : FALLBACK_CONVERSATION_HEIGHT;
  const reservedHeight = hasAttachments ? 132 : 107;
  return Math.max(
    MIN_COMPOSER_HEIGHT,
    Math.min(MAX_COMPOSER_HEIGHT, usableHeight - reservedHeight),
  );
}

interface VerticalResizeHandleProps {
  label: string;
  value: number;
  minimum: number;
  maximum: number;
  className?: string;
  onChange: (height: number) => void;
  onCommit: (height: number) => void;
}

function VerticalResizeHandle({ label, value, minimum, maximum, className = '', onChange, onCommit }: VerticalResizeHandleProps) {
  const resizeStart = useRef<{ pointerId: number; clientY: number; height: number } | null>(null);
  const resizeFromPointer = (clientY: number): number => {
    const start = resizeStart.current;
    if (!start) return value;
    const height = clampHeight(start.height + start.clientY - clientY, minimum, maximum);
    onChange(height);
    return height;
  };
  const beginResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    resizeStart.current = { pointerId: event.pointerId, clientY: event.clientY, height: value };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const moveResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (resizeStart.current?.pointerId !== event.pointerId) return;
    resizeFromPointer(event.clientY);
  };
  const finishResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (resizeStart.current?.pointerId !== event.pointerId) return;
    const height = resizeFromPointer(event.clientY);
    resizeStart.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    onCommit(height);
  };
  const cancelResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = resizeStart.current;
    if (start?.pointerId !== event.pointerId) return;
    onChange(start.height);
    resizeStart.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };
  const resizeFromKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    let height = value;
    if (event.key === 'ArrowUp') height += RESIZE_STEP;
    else if (event.key === 'ArrowDown') height -= RESIZE_STEP;
    else if (event.key === 'Home') height = minimum;
    else if (event.key === 'End') height = maximum;
    else return;
    event.preventDefault();
    height = clampHeight(height, minimum, maximum);
    onChange(height);
    onCommit(height);
  };

  return <div
    className={`${styles.resizeHandle} ${className}`}
    role="separator"
    aria-label={label}
    aria-orientation="horizontal"
    aria-valuemin={minimum}
    aria-valuemax={maximum}
    aria-valuenow={value}
    tabIndex={0}
    onPointerDown={beginResize}
    onPointerMove={moveResize}
    onPointerUp={finishResize}
    onPointerCancel={cancelResize}
    onKeyDown={resizeFromKeyboard}
  ><span aria-hidden="true" /></div>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

function messageTimestamp(value: unknown): number {
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) return numeric > 100_000_000_000 ? numeric / 1_000 : numeric;
  const parsed = Date.parse(text(value));
  return Number.isFinite(parsed) ? parsed / 1_000 : 0;
}

function quoteShellPath(path: string): string {
  return "'" + path.replaceAll("'", "'\"'\"'") + "'";
}

interface TerminalMountProps {
  cell: AgentViewModel;
  active: boolean;
}

function TerminalMount({ cell, active }: TerminalMountProps) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<TerminalController | null>(null);
  const [status, setStatus] = useState<TerminalConnectionStatus>('connecting');

  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface || !cell.sessionId || !active) return;
    const windowId = new URLSearchParams(window.location.search).get('window') || 'main';
    let lease: ReturnType<typeof acquireTerminalController> | null = null;
    try {
      lease = acquireTerminalController(`${windowId}:${cell.id}:${cell.sessionId}`, {
        cellId: cell.id,
        sessionId: cell.sessionId,
        surface,
        isActive: () => active,
        onStatus: setStatus,
      });
      controllerRef.current = lease.controller;
    } catch {
      queueMicrotask(() => setStatus('unavailable'));
    }
    return () => {
      controllerRef.current = null;
      lease?.release();
    };
  }, [active, cell.id, cell.sessionId]);

  if (!cell.sessionId) {
    return <StateSurface title="Terminal stopped" description="Relaunch this agent to create a new PTY session." />;
  }

  const uploadDroppedImages = async (files: File[]) => {
    const images = files.filter((file) => file.type.startsWith('image/'));
    if (!images.length) return;
    const paths: string[] = [];
    for (const file of images) {
      const body = new FormData();
      body.append('task_id', `react-terminal-${cell.id}-${cell.sessionId}`);
      body.append('file', file);
      const response = await fetch('/api/upload', { method: 'POST', body });
      const payload = await response.json() as { ok?: boolean; data?: UploadedAttachment[] };
      if (payload.ok) paths.push(...(payload.data ?? []).map((entry) => entry.path));
    }
    controllerRef.current?.paste(`${paths.map(quoteShellPath).join(' ')} `);
  };

  return (
    <section className={styles.terminalFrame} aria-label={`${cell.name} terminal`}>
      <header>
        <span className={`${styles.statusDot} ${styles[`status_${status}`] ?? ''}`} />
        <span>{status}</span>
        <span className={styles.session}>{cell.sessionId.slice(0, 10)}</span>
        <Button tone="quiet" onPress={() => controllerRef.current?.scrollToTail()}>Tail</Button>
      </header>
      <div
        ref={surfaceRef}
        className={styles.terminalSurface}
        onPointerDown={() => controllerRef.current?.focus()}
        onDragOver={(event) => { if ([...event.dataTransfer.items].some((item) => item.kind === 'file')) event.preventDefault(); }}
        onDrop={(event) => {
          event.preventDefault();
          void uploadDroppedImages([...event.dataTransfer.files]);
        }}
      />
    </section>
  );
}

interface ConversationProps {
  agent: AgentViewModel;
  messages: unknown;
  sendCommand: CommandSender;
  onUnavailable: () => void;
  composeHeight?: number;
}

function Conversation({ agent, messages, sendCommand, onUnavailable, composeHeight = 0 }: ConversationProps) {
  const rows = (Array.isArray(messages) ? messages : [])
    .map(asRecord)
    .sort((a, b) => messageTimestamp(a.created_at ?? a.timestamp) - messageTimestamp(b.created_at ?? b.timestamp));
  const [draft, setDraft] = useState('');
  const [attachments, setAttachments] = useState<UploadedAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const history = useRef<string[]>(['']);
  const historyIndex = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const conversation = useRef<HTMLElement>(null);
  const [conversationHeight, setConversationHeight] = useState(0);
  const [requestedComposeHeight, setRequestedComposeHeight] = useState<number | null>(null);

  const persistHeight = (height: number) => {
    if (!sendCommand({ cmd: 'ui_set_terminal_compose_height', height: Math.round(height) })) onUnavailable();
  };

  useEffect(() => {
    const focus = () => composer.current?.focus();
    window.addEventListener('torque:focus-composer', focus);
    return () => window.removeEventListener('torque:focus-composer', focus);
  }, []);

  useEffect(() => {
    const node = conversation.current;
    if (!node) return;
    const measure = () => setConversationHeight(node.getBoundingClientRect().height);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const maximumComposeHeight = composerHeightLimit(conversationHeight, Boolean(attachments.length));
  const composerHeight = clampHeight(
    requestedComposeHeight ?? (composeHeight > 0 ? composeHeight : DEFAULT_COMPOSER_HEIGHT),
    MIN_COMPOSER_HEIGHT,
    maximumComposeHeight,
  );

  const updateDraft = (value: string, recordHistory = true) => {
    setDraft(value);
    if (!recordHistory || history.current[historyIndex.current] === value) return;
    history.current = [...history.current.slice(0, historyIndex.current + 1), value].slice(-100);
    historyIndex.current = history.current.length - 1;
  };

  const uploadFiles = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    try {
      const body = new FormData();
      body.append('agent_id', agent.id);
      files.forEach((file) => body.append('file', file));
      const response = await fetch('/api/attachment/upload', { method: 'POST', body });
      const payload = await response.json() as { ok?: boolean; data?: UploadedAttachment[] };
      if (!payload.ok) throw new Error('Upload failed');
      setAttachments((current) => [...current, ...(payload.data ?? [])]);
    } catch {
      onUnavailable();
    } finally {
      setUploading(false);
    }
  };

  const submit = () => {
    const message = [draft.trim(), ...attachments.map((entry) => entry.path)].filter(Boolean).join('\n');
    if (!message) return;
    const sent = sendCommand({
      cmd: 'user_agent_message',
      agent_id: agent.id,
      message,
      thread_id: `user-agent:user:${agent.id}`,
      idempotency_key: `react-${agent.id}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    });
    if (!sent) { onUnavailable(); return; }
    updateDraft('');
    setAttachments([]);
  };

  return (
    <section
      ref={conversation}
      className={styles.conversation}
      aria-label={`Conversation with ${agent.name}`}
    >
      <header><strong>Direct messages</strong><span>{rows.length}</span><Button tone="quiet" onPress={() => { if (!sendCommand({ cmd: 'user_agent_turn_cancel', agent_id: agent.id })) onUnavailable(); }}>Cancel turn</Button></header>
      <div className={styles.messageList}>
        {rows.length ? rows.slice(-30).map((row, index) => {
          const sender = text(row.sender_kind) || text(row.direction) || 'agent';
          const body = text(row.message) || text(row.text) || text(row.body);
          const direction = sender === 'user' || sender === 'outbound' ? 'outbound' : 'inbound';
          return <article key={text(row.message_id) || text(row.id) || index} className={direction === 'outbound' ? styles.outbound : styles.inbound} data-direction={direction}><small>{sender}</small><p>{body}</p></article>;
        }) : <p className={styles.noMessages}>No direct messages yet.</p>}
      </div>
      {attachments.length ? <div className={styles.attachments}>{attachments.map((entry, index) => <button key={`${entry.path}-${index}`} onClick={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))} title="Remove attachment">◇ {entry.filename || entry.path.split('/').pop()} ×</button>)}</div> : null}
      <form className={styles.composer} onSubmit={(event) => { event.preventDefault(); submit(); }}>
        <VerticalResizeHandle
          className={styles.composerResize ?? ''}
          label="Resize message text box"
          value={composerHeight}
          minimum={MIN_COMPOSER_HEIGHT}
          maximum={maximumComposeHeight}
          onChange={setRequestedComposeHeight}
          onCommit={persistHeight}
        />
        <textarea
          ref={composer}
          value={draft}
          onChange={(event) => updateDraft(event.target.value)}
          onPaste={(event) => { const files = [...event.clipboardData.files]; if (files.length) void uploadFiles(files); }}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === 'z') {
              event.preventDefault();
              const direction = event.shiftKey ? 1 : -1;
              const next = Math.max(0, Math.min(history.current.length - 1, historyIndex.current + direction));
              historyIndex.current = next;
              updateDraft(history.current[next] ?? '', false);
              return;
            }
            if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit(); }
          }}
          placeholder={`Message ${agent.name}…`}
          aria-label={`Message ${agent.name}`}
          rows={3}
          style={{ height: `${composerHeight}px` }}
        />
        <input ref={fileInput} type="file" multiple hidden onChange={(event) => { void uploadFiles([...(event.target.files ?? [])]); event.target.value = ''; }} />
        <footer>
          <Button tone="quiet" type="button" onPress={() => fileInput.current?.click()} isDisabled={uploading}>{uploading ? 'Uploading…' : 'Attach'}</Button>
          <span>Enter send · Shift+Enter newline · ⌘Z undo</span>
          <Button tone="primary" type="submit" isDisabled={!draft.trim() && !attachments.length}>Send</Button>
        </footer>
      </form>
    </section>
  );
}

interface TerminalWorkspaceProps {
  agent: AgentViewModel;
  terminal: AgentViewModel;
  messages: unknown;
  sendCommand: CommandSender;
  onUnavailable: () => void;
  showConversation?: boolean;
  active?: boolean;
  directMessagesHeight?: number;
  composeHeight?: number;
}

export function TerminalWorkspace({ agent, terminal, messages, sendCommand, onUnavailable, showConversation = true, active = true, directMessagesHeight = 0, composeHeight = 0 }: TerminalWorkspaceProps) {
  const workspace = useRef<HTMLDivElement>(null);
  const [workspaceHeight, setWorkspaceHeight] = useState(0);
  const [requestedConversationHeight, setRequestedConversationHeight] = useState<number | null>(null);

  useEffect(() => {
    const node = workspace.current;
    if (!node) return;
    const measure = () => setWorkspaceHeight(node.getBoundingClientRect().height);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const conversationHeight = clampConversationHeight(
    requestedConversationHeight ?? (directMessagesHeight > 0 ? directMessagesHeight : DEFAULT_CONVERSATION_HEIGHT),
    workspaceHeight,
  );
  const maximumConversationHeight = conversationHeightLimit(workspaceHeight);
  const persistConversationHeight = (height: number) => {
    if (!sendCommand({ cmd: 'ui_set_terminal_direct_messages_height', height })) onUnavailable();
  };
  const workspaceStyle = showConversation
    ? { '--conversation-height': `${conversationHeight}px` } as CSSProperties
    : undefined;

  return (
    <div ref={workspace} className={`${styles.workspace} ${showConversation ? '' : styles.workspace_terminalOnly}`} style={workspaceStyle}>
      <TerminalMount cell={terminal} active={active} />
      {showConversation ? <>
        <VerticalResizeHandle
          label="Resize terminal and direct messages"
          value={conversationHeight}
          minimum={MIN_CONVERSATION_HEIGHT}
          maximum={maximumConversationHeight}
          onChange={setRequestedConversationHeight}
          onCommit={persistConversationHeight}
        />
        <Conversation key={agent.id} agent={agent} messages={messages} sendCommand={sendCommand} onUnavailable={onUnavailable} composeHeight={composeHeight} />
      </> : null}
    </div>
  );
}
