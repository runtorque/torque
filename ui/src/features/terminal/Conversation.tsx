import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Button, ModalDialog } from '../../design/primitives';
import { useAppDispatch, useAppSelector, useAppStore } from '../../app/hooks';
import type { TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import type { CommandSender } from '../board/BoardPanel';
import type { AgentViewModel } from '../agents/model';
import { composerActions, composerEditKind, type ComposerEditKind, documentSnapshot, emptyComposerDraft, type ComposerAttachment } from './composerState';
import { acknowledgedMessage, cancellationLabels, composerCommand, recallMessages, rows, text } from './composerModel';
import { DirectMessages } from './DirectMessages';
import { AgentMessageLoop } from './AgentMessageLoop';
import { messageLoopPanel } from './messageLoopModel';
import { selectMessagesState } from '../../app/store';
import { useComposerCompletion } from './useComposerCompletion';
import { ComposerSuggestions } from './ComposerSuggestions';
import { RichComposer } from './RichComposer';
import { attachmentId, editorSelection, readComposerInput, setEditorSelection, type ComposerInput } from './composerDom';
import { editorOffset, editorText, moveAttachments, plainOffset, type ComposerDocument } from './composerDocument';
import attachmentStyles from './RichComposer.module.css';
import { VerticalResizeHandle } from './VerticalResizeHandle';
import styles from './TerminalSurface.module.css';

interface ConversationProps {
  cell: AgentViewModel; target: AgentViewModel | null; messages: unknown; messageHistory?: unknown;
  sendCommand: CommandSender; onUnavailable: () => void; composeHeight?: number; active?: boolean;
}
export function Conversation({ cell, target, messages, messageHistory, sendCommand, onUnavailable, composeHeight = 0, active = true }: ConversationProps) {
  const dispatch = useAppDispatch(); const store = useAppStore();
  const draft = useAppSelector((state) => state.composer.drafts[cell.id] ?? emptyComposerDraft);
  const turn = useAppSelector((state) => target ? state.composer.turns[target.id] : undefined);
  const patch = (changes: Partial<typeof draft>) => dispatch(composerActions.patch({ cellId: cell.id, changes }));
  const currentDraft = () => store.getState().composer.drafts[cell.id] ?? emptyComposerDraft;
  const composer = useRef<ComposerInput>(null); const conversation = useRef<HTMLElement>(null); const fileInput = useRef<HTMLInputElement>(null);
  const wasPending = useRef(false);
  const completionCaret = useRef<{ text: string; selection: [number, number] } | null>(null);
  const activeRef = useRef(active);
  useEffect(() => { activeRef.current = active; }, [active]);
  useEffect(() => () => { dispatch(composerActions.endComposition(cell.id)); }, [cell.id, dispatch]);
  const [conversationHeight, setConversationHeight] = useState(0); const [requestedHeight, setRequestedHeight] = useState<number | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false); const [previewId, setPreviewId] = useState('');
  const preview = draft.attachments.find((entry) => attachmentId(entry) === previewId);
  const showRich = draft.attachments.length > 0;
  const messageCount = rows(messages).length;
  const history = recallMessages(messageHistory, messages, draft.sent);
  const pending = draft.pending; const busy = pending || draft.uploading || Boolean(draft.composition);
  const name = target?.name ?? cell.name;
  const hasLoop = useAppSelector((state) => Boolean(messageLoopPanel(selectMessagesState(state).loops, state.composer.loopCancellations, target?.id ?? '')));
  const maximumHeight = Math.max(38, Math.min(240, (conversationHeight || 300) - (hasLoop ? 64 : 0) - 107 - (draft.reply ? 28 : 0) - (draft.error || draft.notice ? 24 : 0)));
  const height = Math.max(38, Math.min(maximumHeight, requestedHeight ?? (composeHeight > 0 ? composeHeight : 54)));
  const focusComposer = () => { const node = composer.current; if (!node) return; node.focus({ preventScroll: true }); const latest = currentDraft(); setEditorSelection(node, latest.attachments, latest.selection); node.scrollTop = latest.scrollTop; };
  useEffect(() => {
    if (wasPending.current && !pending && active) composer.current?.focus({ preventScroll: true });
    wasPending.current = pending;
  }, [pending, active]);
  useLayoutEffect(() => {
    const node = composer.current;
    // Native composition may update the DOM before its final input event.
    // React value reconciliation on an unrelated upload/status render must
    // not replace that browser-owned value before compositionend captures it.
    if (node instanceof HTMLTextAreaElement && !draft.composition && node.value !== draft.text) node.value = draft.text;
  }, [showRich, draft.text, draft.composition]);
  useLayoutEffect(() => { const node = composer.current; const latest = store.getState().composer.drafts[cell.id] ?? emptyComposerDraft; if (node) { setEditorSelection(node, latest.attachments, latest.selection); node.scrollTop = latest.scrollTop; } }, [showRich, cell.id, store]);
  useEffect(() => {
    if (!active) return;
    const feedback = conversation.current?.querySelectorAll<HTMLElement>('[role="alert"], [role="status"]');
    feedback?.[feedback.length - 1]?.scrollIntoView?.({ block: 'nearest' });
  }, [active, draft.error, draft.notice, turn?.error, turn?.notice]);
  useEffect(() => {
    if (!active) return;
    const focus = () => composer.current?.focus(); window.addEventListener('torque:focus-composer', focus);
    return () => window.removeEventListener('torque:focus-composer', focus);
  }, [active]);
  useEffect(() => {
    const node = conversation.current; if (!node) return;
    const measure = () => setConversationHeight(node.getBoundingClientRect().height); measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure); observer.observe(node); return () => observer.disconnect();
  }, []);
  const edit = (value: string, selection?: [number, number], kind: ComposerEditKind = null) => { dispatch(composerActions.edit({ cellId: cell.id, text: value, kind, selection: selection ?? [value.length, value.length] })); patch({ recall: null }); };
  const applyDocument = (value: ComposerDocument, kind: ComposerEditKind = null) => {
    if (!currentDraft().composition) completionCaret.current = { text: value.text, selection: value.selection };
    dispatch(composerActions.document({ cellId: cell.id, document: value, kind })); patch({ recall: null });
  };
  const requestFocus = () => { const latest = currentDraft(); completionCaret.current = { text: latest.text, selection: latest.selection }; };
  const plainSelection = draft.selection.map((offset) => plainOffset(draft, offset)) as [number, number];
  const completion = useComposerCompletion(cell, target, draft.text, plainSelection, active && !busy, (text, selection) => {
    const next = { text, attachments: moveAttachments(draft.text, text, draft.attachments), selection };
    next.selection = selection.map((offset) => editorOffset(next, offset)) as [number, number]; applyDocument(next);
  });
  useLayoutEffect(() => {
    const desired = completionCaret.current; const node = composer.current;
    if (!desired || !node || desired.text !== draft.text) return;
    completionCaret.current = null;
    // Apply selection after React commits the new value; an earlier microtask
    // can clamp it to the old text length and reopen the just-picked suggestion.
    node.focus({ preventScroll: true }); setEditorSelection(node, draft.attachments, desired.selection);
    dispatch(composerActions.patch({ cellId: cell.id, changes: { selection: desired.selection } }));
  }, [cell.id, dispatch, draft.text, draft.attachments, draft.selection]);
  const restoreDraft = () => {
    const recall = currentDraft().recall; if (!recall) return;
    applyDocument(recall.document);
  };
  const recallAt = (index: number) => {
    if (index < 0) { restoreDraft(); return; } const entry = history[index]; if (!entry || busy) return;
    const current = currentDraft(); const original = current.recall ?? { document: documentSnapshot(current), index: -1 };
    applyDocument({ text: entry.message, attachments: [], selection: [entry.message.length, entry.message.length] }); patch({ recall: { ...original, index } }); setHistoryOpen(false);
  };
  const uploadFiles = async (files: File[]) => {
    const current = currentDraft(); if (!files.length || current.uploading || current.pending) return;
    const node = composer.current; const selection = node ? editorSelection(node, current.attachments, current.selection) : current.selection;
    const key = crypto.randomUUID(); dispatch(composerActions.startUpload({ cellId: cell.id, key, selection }));
    try {
      const body = new FormData(); body.append('agent_id', cell.id); files.forEach((file) => body.append('file', file));
      const response = await fetch('/api/attachment/upload', { method: 'POST', body });
      const payload = await response.json() as { ok?: boolean; error?: string; data?: ComposerAttachment[] };
      if (!response.ok || !payload.ok || !Array.isArray(payload.data) || payload.data.length !== files.length || payload.data.some((entry) => !entry.path)) throw new Error(payload.error || 'Upload failed. The draft is retained.');
      if (currentDraft().uploadAnchor?.key !== key) return;
      const attachments = payload.data.map((entry, index) => ({ ...entry, filename: files[index]!.name || entry.filename, id: crypto.randomUUID(), previewUrl: URL.createObjectURL?.(files[index]!) ?? '' }));
      const ownsFocus = composer.current && (document.activeElement === composer.current || document.activeElement === fileInput.current || document.activeElement?.textContent === 'Uploading…');
      dispatch(composerActions.finishUpload({ cellId: cell.id, key, attachments }));
      if (ownsFocus && activeRef.current && !currentDraft().composition) requestFocus();
    } catch (cause) { dispatch(composerActions.failUpload({ cellId: cell.id, key, error: cause instanceof Error ? cause.message : 'Upload failed.' })); }
  };
  const composing = (value: boolean) => { completion.setComposing(value); dispatch(value ? composerActions.startComposition(cell.id) : composerActions.endComposition(cell.id)); if (!value && activeRef.current && document.activeElement === composer.current) requestFocus(); };
  const submit = async () => {
    const current = currentDraft(); if (current.pending || current.uploading || current.composition) return;
    try {
      const payload = composerCommand(cell, target, current); const fingerprint = JSON.stringify(payload);
      const key = current.attempt?.fingerprint === fingerprint ? current.attempt.key : `react-message-${crypto.randomUUID()}`;
      const command: TorqueCommand = { ...payload, idempotency_key: key }; patch({ pending: true, error: '', notice: '', attempt: { fingerprint, key } });
      const result = acknowledgedMessage(await readCommand(command, new AbortController().signal), command);
      dispatch(composerActions.submitted({ cellId: cell.id, key, message: { id: result.id, message: text(command.message ?? command.text), at: Date.now() / 1000 }, notice: result.notice }));
      if (target && result.cancellable && target.sessionId) dispatch(composerActions.turn({ agentId: target.id, turn: { key, sessionId: target.sessionId, pending: false, cancelKey: '', error: '', notice: '' } }));
    } catch (cause) { patch({ error: cause instanceof Error ? cause.message : 'Message could not be sent. The draft is retained.' }); }
    finally { patch({ pending: false }); }
  };
  const cancelTurn = async () => {
    if (!target || busy) return; const submitted = store.getState().composer.turns[target.id];
    if (!submitted || submitted.pending || submitted.sessionId !== target.sessionId || submitted.notice) return;
    const cancelKey = submitted.cancelKey || `react-cancel-${crypto.randomUUID()}`;
    const change = (changes: Partial<typeof submitted>) => dispatch(composerActions.patchTurn({ agentId: target.id, key: submitted.key, changes }));
    change({ pending: true, cancelKey, error: '' });
    try {
      const frame = await readCommand({ cmd: 'user_agent_turn_cancel', agent_id: target.id, session_id: submitted.sessionId, turn_idempotency_key: submitted.key, idempotency_key: cancelKey }, new AbortController().signal);
      if (frame.type === 'error') throw new Error(text(frame.message) || 'Cancellation refused.');
      const label = cancellationLabels[text(frame.outcome)];
      if (frame.type !== 'ok' || !text(frame.message_id) || !label) throw new Error('Could not confirm cancellation.');
      change({ notice: label });
    } catch (cause) { change({ error: cause instanceof Error ? cause.message : 'Cancellation failed.' }); }
    finally { change({ pending: false }); }
  };
  const keyDown = (event: KeyboardEvent<HTMLElement>) => {
          if (event.nativeEvent.isComposing || currentDraft().composition || currentDraft().pending) return;
          if (completion.handleKey(event)) return;
          if (!event.altKey && event.metaKey !== event.ctrlKey && event.key.toLowerCase() === 'z') { event.preventDefault(); dispatch(composerActions.undo({ cellId: cell.id, direction: event.shiftKey ? 1 : -1 })); patch({ recall: null }); requestFocus(); return; }
          if (event.key === 'Escape') {
            event.preventDefault(); event.stopPropagation(); if (busy) return;
            const current = currentDraft();
            if (current.recall) { restoreDraft(); return; }
            if (current.reply) { patch({ reply: null }); return; }
            if (current.text || current.attachments.length) { applyDocument({ text: '', attachments: [], selection: [0, 0] }); return; }
            if (!event.repeat) void cancelTurn();
            return;
          }
          if ((event.key === 'Home' || event.key === 'End') && !event.altKey) {
            event.preventDefault();
            const current = readComposerInput(composer.current!, draft.attachments, draft.selection);
            const value = editorText(current); const [anchor, caret] = current.selection; const end = event.key === 'End';
            const newline = end ? value.indexOf('\n', caret) : caret > 0 ? value.lastIndexOf('\n', caret - 1) : -1;
            const offset = event.metaKey || event.ctrlKey ? end ? value.length : 0 : end ? newline < 0 ? value.length : newline : newline + 1;
            const selection: [number, number] = event.shiftKey ? [anchor, offset] : [offset, offset];
            setEditorSelection(composer.current!, current.attachments, selection); patch({ selection }); return;
          }
          if ((event.key === 'ArrowUp' || event.key === 'ArrowDown') && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
            const current = readComposerInput(composer.current!, draft.attachments, draft.selection); const [start, end] = current.selection.map((offset) => plainOffset(current, offset)); const up = event.key === 'ArrowUp'; const atEdge = start === end && !(up ? current.text.slice(0, start) : current.text.slice(end)).includes('\n');
            if (atEdge && history.length && (up || draft.recall)) { const index = (draft.recall?.index ?? -1) + (up ? 1 : -1); if (index < history.length) { event.preventDefault(); recallAt(index); return; } }
          }
          if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(); }
  };
  useEffect(() => {
    const node = composer.current; if (!node) return;
    const nativeUndo = (event: Event) => {
      if (!(event instanceof InputEvent)) return;
      if (event.isComposing) return;
      if (!['historyUndo', 'historyRedo'].includes(event.inputType)) {
        const current = store.getState().composer.drafts[cell.id] ?? emptyComposerDraft;
        dispatch(composerActions.patch({ cellId: cell.id, changes: { selection: editorSelection(node, current.attachments, current.selection) } })); return;
      }
      const current = store.getState().composer.drafts[cell.id]; if (current?.pending || current?.composition) return;
      event.preventDefault(); dispatch(composerActions.undo({ cellId: cell.id, direction: event.inputType === 'historyRedo' ? 1 : -1 }));
      dispatch(composerActions.patch({ cellId: cell.id, changes: { recall: null } }));
      const next = store.getState().composer.drafts[cell.id] ?? emptyComposerDraft; completionCaret.current = { text: next.text, selection: next.selection };
    };
    node.addEventListener('beforeinput', nativeUndo); return () => node.removeEventListener('beforeinput', nativeUndo);
  }, [showRich, cell.id, dispatch, store]);
  const sessionChanged = Boolean(turn && turn.sessionId !== target?.sessionId);
  return <section ref={conversation} className={styles.conversation} data-has-loop={hasLoop} aria-label={target ? `Conversation with ${name}` : `Buffered input for ${name}`}>
    <header><strong>{target ? 'Direct messages' : 'Terminal input'}</strong><span>{target ? messageCount : 'Multiline input'}</span>{target ? <Button tone="quiet" isDisabled={busy || !turn || turn.pending || sessionChanged || Boolean(turn.notice)} onPress={() => { void cancelTurn(); }}>{turn?.pending ? 'Cancelling…' : 'Cancel turn'}</Button> : null}</header>
    {target ? <AgentMessageLoop key={`loop-${target.id}`} agentId={target.id} disabled={busy} /> : null}
    {target ? <DirectMessages key={target.id} agent={target} messages={messages} pending={pending} active={active} onReply={(id, body) => { patch({ reply: { id, agentId: target.id, preview: body.replace(/\s+/g, ' ').slice(0, 120) } }); focusComposer(); }} /> : <div className={styles.messageList}><p className={styles.noMessages}>{cell.sessionId ? 'Send the composed text to this terminal session.' : 'Relaunch this terminal before sending input.'}</p></div>}
    <form className={styles.composer} onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <VerticalResizeHandle className={styles.composerResize ?? ''} label="Resize message text box" value={height} minimum={38} maximum={maximumHeight} onChange={setRequestedHeight} onCommit={(value) => { if (!sendCommand({ cmd: 'ui_set_terminal_compose_height', height: Math.round(value) })) onUnavailable(); }} />
      {draft.error ? <p className={styles.composerFeedback} role="alert">{draft.error}</p> : draft.notice ? <p className={styles.composerFeedback} role="status">{draft.notice}</p> : null}
      {turn?.error ? <p className={styles.composerFeedback} role="alert">{turn.error}</p> : turn?.notice ? <p className={styles.composerFeedback} role="status">{turn.notice}</p> : sessionChanged ? <p className={styles.composerFeedback}>The submitted turn belongs to a previous session.</p> : null}
      {draft.reply ? <div className={styles.replyContext}><span>Replying to: {draft.reply.preview || draft.reply.id}</span><Button tone="quiet" aria-label="Cancel reply" isDisabled={pending} onPress={() => { patch({ reply: null }); focusComposer(); }}>×</Button></div> : null}
      {showRich ? <RichComposer inputRef={composer} value={draft} disabled={pending} height={height} name={name} onChange={applyDocument} onSelection={(selection, scrollTop) => patch({ selection, scrollTop })} onKeyDown={keyDown} onFiles={(files) => { void uploadFiles(files); }} onPreview={setPreviewId} onFocus={completion.setFocused} onComposition={composing} aria={{ 'aria-controls': completion.completion ? completion.id : undefined, 'aria-expanded': Boolean(completion.completion), 'aria-activedescendant': completion.completion && completion.index >= 0 ? `${completion.id}-${completion.index}` : undefined }} /> : <textarea ref={(node) => { composer.current = node; }} defaultValue={draft.text} disabled={pending}
        aria-autocomplete="list" aria-haspopup="listbox" aria-controls={completion.completion ? completion.id : undefined} aria-expanded={Boolean(completion.completion)} aria-activedescendant={completion.completion && completion.index >= 0 ? `${completion.id}-${completion.index}` : undefined}
        onFocus={() => completion.setFocused(true)} onCompositionStart={() => composing(true)} onCompositionEnd={(event) => { edit(event.currentTarget.value, editorSelection(event.currentTarget, [])); composing(false); }}
        onChange={(event) => edit(event.target.value, editorSelection(event.target, []), composerEditKind((event.nativeEvent as InputEvent).inputType))}
        onSelect={(event) => { const node = event.currentTarget; patch({ selection: editorSelection(node, []) }); }}
        onBlur={(event) => { completion.setFocused(false); const node = event.currentTarget; patch({ selection: editorSelection(node, []), scrollTop: node.scrollTop }); }}
        onScroll={(event) => patch({ scrollTop: event.currentTarget.scrollTop })}
        onPaste={(event) => { const files = [...event.clipboardData.files]; if (files.length) { event.preventDefault(); void uploadFiles(files); } }}
        onDragOver={(event) => { if ([...event.dataTransfer.types].includes('Files')) event.preventDefault(); }}
        onDrop={(event) => { if (event.dataTransfer.files.length) { event.preventDefault(); void uploadFiles([...event.dataTransfer.files]); } }}
        onKeyDown={keyDown} placeholder={`Message ${name}…`} aria-label={`Message ${name}`} rows={3} style={{ height: `${height}px` }} />}
      <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden onChange={(event) => { void uploadFiles([...(event.target.files ?? [])]); event.target.value = ''; }} />
      <footer><Button tone="quiet" type="button" onPress={() => fileInput.current?.click()} isDisabled={busy}>{draft.uploading ? 'Uploading…' : 'Attach'}</Button><Button tone="quiet" type="button" aria-label="Message history" isDisabled={busy} onPress={() => setHistoryOpen(true)}>History</Button>{draft.recall ? <Button tone="quiet" type="button" isDisabled={busy} onPress={restoreDraft}>Restore draft</Button> : null}<span>Enter send · Shift+Enter newline</span><Button tone="primary" type="submit" isDisabled={busy || (!draft.text.trim() && !draft.attachments.length) || (!target && !cell.sessionId)}>{pending ? 'Sending…' : 'Send'}</Button></footer>
    </form>
    {completion.completion ? <ComposerSuggestions id={completion.id} input={composer} completion={completion.completion} index={completion.index} onPick={completion.pick} onChoose={completion.choose} /> : null}
    <ModalDialog title="Attached image preview" description={preview?.filename ?? ''} isOpen={Boolean(preview)} onOpenChange={(open) => { if (!open) setPreviewId(''); }} size="large"><div className={attachmentStyles.preview}>
      {preview?.previewUrl ? <img src={preview.previewUrl} alt={preview.filename} /> : <p>Preview unavailable for this image in the current session.</p>}
      {preview ? <><p>{preview.filename}</p><Button isDisabled={pending} onPress={() => { const id = attachmentId(preview); dispatch(composerActions.removeAttachment({ cellId: cell.id, id })); setPreviewId(''); requestFocus(); }}>Remove image</Button></> : null}
    </div></ModalDialog>
    <ModalDialog title="Recent messages" description={`Recall a message for ${name}. Your unsent draft remains available.`} isOpen={historyOpen} onOpenChange={setHistoryOpen} size="small"><div className={styles.historyList}>{history.length ? history.map((entry, index) => <button key={entry.id || index} onClick={() => recallAt(index)}>{entry.message}</button>) : <p>No sent messages yet.</p>}</div></ModalDialog>
  </section>;
}
