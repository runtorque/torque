import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react';

import { useAppSelector } from '../../app/hooks';
import { Button, StateSurface } from '../../design/primitives';
import type { CommandSender } from '../board/BoardPanel';
import type { AgentViewModel } from '../agents/model';
import {
  acquireTerminalController,
  type TerminalConnectionStatus,
  type TerminalController,
} from './terminalController';
import styles from './TerminalSurface.module.css';
import { terminalScrollback } from './terminalPreferences';
import { Conversation } from './Conversation';
import { messageLoopPanel } from './messageLoopModel';
import { selectAgentSettingsDefaults, selectMessagesState } from '../../app/store';
import { VerticalResizeHandle } from './VerticalResizeHandle';
import { uploadTerminalImages } from './terminalDropUpload';

const DEFAULT_CONVERSATION_HEIGHT = 180;
const MIN_CONVERSATION_HEIGHT = 176;
const MIN_TERMINAL_HEIGHT = 180;
const SPLIT_HANDLE_HEIGHT = 7;
const FALLBACK_WORKSPACE_HEIGHT = 807;

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

function quoteShellPath(path: string): string {
  return "'" + path.replaceAll("'", "'\"'\"'") + "'";
}

interface TerminalMountProps {
  cell: AgentViewModel;
  active: boolean;
  focusRequest: number;
}

function TerminalMount({ cell, active, focusRequest }: TerminalMountProps) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<TerminalController | null>(null);
  const dropOwner = useRef<{ controller: TerminalController; uploads: Set<AbortController> } | null>(null);
  const latestDrop = useRef<AbortController | null>(null);
  const [dropFeedback, setDropFeedback] = useState<{ pending: boolean; text: string } | null>(null);
  const [status, setStatus] = useState<TerminalConnectionStatus>('connecting');
  const scrollback = useAppSelector((state) => terminalScrollback(selectAgentSettingsDefaults(state).global.xterm_scrollback));
  const scrollbackRef = useRef(scrollback);
  useLayoutEffect(() => {
    scrollbackRef.current = scrollback;
    controllerRef.current?.setScrollback(scrollback);
  }, [scrollback]);

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
        scrollback: scrollbackRef.current,
      });
      controllerRef.current = lease.controller;
      dropOwner.current = { controller: lease.controller, uploads: new Set() };
      lease.controller.setScrollback(scrollbackRef.current);
    } catch {
      queueMicrotask(() => setStatus('unavailable'));
    }
    return () => {
      dropOwner.current?.uploads.forEach((upload) => upload.abort());
      dropOwner.current = null;
      latestDrop.current = null;
      setDropFeedback(null);
      controllerRef.current = null;
      lease?.release();
    };
  }, [active, cell.id, cell.sessionId]);

  const consumedFocus = useRef(0);
  useEffect(() => {
    if (!active || !cell.sessionId || !focusRequest || consumedFocus.current === focusRequest) return;
    // Wait for the visible pane's layout, then consume this explicit intent.
    // Routine rerenders, reconnect and revisiting Live cannot steal focus.
    const frame = requestAnimationFrame(() => {
      if (!controllerRef.current) return;
      consumedFocus.current = focusRequest;
      controllerRef.current.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [active, cell.id, cell.sessionId, focusRequest]);

  if (!cell.sessionId) {
    return <StateSurface title="Terminal stopped" description="Relaunch this agent to create a new PTY session." />;
  }

  const uploadDroppedImages = async (files: File[]) => {
    const owner = dropOwner.current;
    if (!owner) return;
    const images = files.filter((file) => file.type.startsWith('image/'));
    if (!images.length) { owner.controller.focus(); return; }
    const upload = new AbortController();
    owner.uploads.add(upload); latestDrop.current = upload;
    setDropFeedback({ pending: true, text: 'Uploading terminal images…' });
    const result = await uploadTerminalImages(`react-terminal-${cell.id}-${cell.sessionId}`, images, upload.signal);
    owner.uploads.delete(upload);
    // Controller instances may be reused on reacquisition; the lease identity is
    // what binds a drop to the original active pane.
    if (dropOwner.current !== owner) return;
    if (result.paths.length) owner.controller.paste(`${result.paths.map(quoteShellPath).join(' ')} `);
    if (latestDrop.current === upload) setDropFeedback(result.failures.length ? {
      pending: false,
      text: `${result.failures.join(' ')} ${result.paths.length ? 'Successful image paths were pasted. ' : ''}Drop the failed images again to retry.`,
    } : null);
    owner.controller.focus();
  };

  return (
    <section className={styles.terminalFrame} aria-label={`${cell.name} terminal`}>
      <header>
        <span className={`${styles.statusDot} ${styles[`status_${status}`] ?? ''}`} />
        <span>{status}</span>
        <span className={styles.session}>{cell.sessionId.slice(0, 10)}</span>
        <Button tone="quiet" onPress={() => controllerRef.current?.scrollToTail()}>Tail</Button>
      </header>
      {dropFeedback ? <p className={styles.dropFeedback} role={dropFeedback.pending ? 'status' : 'alert'}>{dropFeedback.text}</p> : null}
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

interface TerminalWorkspaceProps {
  agent: AgentViewModel;
  terminal: AgentViewModel;
  messages: unknown;
  messageHistory?: unknown;
  messageTarget?: AgentViewModel | null;
  sendCommand: CommandSender;
  onUnavailable: () => void;
  showConversation?: boolean;
  active?: boolean;
  directMessagesHeight?: number;
  composeHeight?: number;
  focusRequest?: number;
}

export function TerminalWorkspace({ agent, terminal, messages, messageHistory, messageTarget, sendCommand, onUnavailable, showConversation = true, active = true, directMessagesHeight = 0, composeHeight = 0, focusRequest = 0 }: TerminalWorkspaceProps) {
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

  const extraComposerHeight = useAppSelector((state) => {
    const draft = state.composer.drafts[agent.id];
    const targetId = agent.cellType === 'agent' ? agent.id : messageTarget?.id;
    const turn = targetId ? state.composer.turns[targetId] : undefined;
    return (draft?.reply ? 28 : 0)
      + (draft?.error || draft?.notice ? 44 : 0) + (turn?.error || turn?.notice ? 44 : 0)
      + (messageLoopPanel(selectMessagesState(state).loops, state.composer.loopCancellations, targetId ?? '') ? 64 : 0);
  });
  const minimumConversationHeight = Math.min(conversationHeightLimit(workspaceHeight), MIN_CONVERSATION_HEIGHT + extraComposerHeight);
  const conversationHeight = clampConversationHeight(
    Math.max(minimumConversationHeight, requestedConversationHeight ?? (directMessagesHeight > 0 ? directMessagesHeight : DEFAULT_CONVERSATION_HEIGHT)),
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
      <TerminalMount key={`${terminal.id}:${terminal.sessionId}`} cell={terminal} active={active} focusRequest={focusRequest} />
      {showConversation ? <>
        <VerticalResizeHandle
          label="Resize terminal and direct messages"
          value={conversationHeight}
          minimum={minimumConversationHeight}
          maximum={maximumConversationHeight}
          onChange={setRequestedConversationHeight}
          onCommit={persistConversationHeight}
        />
        <Conversation key={agent.id} cell={agent} target={agent.cellType === 'agent' ? agent : messageTarget ?? null} messages={messages} messageHistory={messageHistory} sendCommand={sendCommand} onUnavailable={onUnavailable} composeHeight={composeHeight} active={active} />
      </> : null}
    </div>
  );
}
