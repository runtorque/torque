import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { UnknownRecord } from '../../protocol';
import { editorLength, locatedAttachments, moveAttachments, moveUploadAnchor, replaceDocumentRange, sameDocument, type ComposerDocument, type UploadAnchor } from './composerDocument';
export interface ComposerAttachment { path: string; filename: string; mime_type?: string; size_bytes?: number; id?: string; position?: number; previewUrl?: string }
export interface ReplyTarget { id: string; agentId: string; preview: string }
export interface SentMessage { id: string; message: string; at: number }
export type ComposerEditKind = 'typing' | 'delete' | null;
export function composerEditKind(inputType?: string): ComposerEditKind {
  if (inputType?.startsWith('delete')) return 'delete';
  return inputType && ['insertText', 'insertLineBreak', 'insertParagraph'].includes(inputType) ? 'typing' : null;
}
export interface ComposerDraft {
  text: string; attachments: ComposerAttachment[]; reply: ReplyTarget | null;
  selection: [number, number]; scrollTop: number;
  undo: ComposerDocument[]; undoIndex: number; undoGroup: ComposerEditKind; uploadAnchor: UploadAnchor | null;
  composition: ComposerDocument | null; completedUpload: { key: string; attachments: ComposerAttachment[] } | null;
  pending: boolean; uploading: boolean; error: string; notice: string;
  attempt: { fingerprint: string; key: string } | null;
  sent: SentMessage[];
  recall: { document: ComposerDocument; index: number } | null;
}
export interface MessageReading { count: number; pinned: boolean; anchorId: string; offset: number; scrollTop: number; selectedId: string }
export interface LoopCancellation { agentId: string; loop: UnknownRecord; key: string; pending: boolean; error: string; notice: string }
export interface SubmittedTurn { key: string; sessionId: string; pending: boolean; cancelKey: string; error: string; notice: string }
export const emptyComposerDraft: ComposerDraft = { text: '', attachments: [], reply: null, selection: [0, 0], scrollTop: 0, undo: [{ text: '', attachments: [], selection: [0, 0] }], undoIndex: 0, undoGroup: null, uploadAnchor: null, composition: null, completedUpload: null, pending: false, uploading: false, error: '', notice: '', attempt: null, sent: [], recall: null };
export function documentSnapshot(draft: ComposerDocument): ComposerDocument {
  return { text: draft.text, attachments: draft.attachments.map((entry) => ({ ...entry })), selection: [...draft.selection] };
}
function recordDocument(draft: ComposerDraft, previous: ComposerDocument, next: ComposerDocument, kind: ComposerEditKind = null) {
  if (!sameDocument(previous, next)) {
    const past = draft.undo.slice(0, draft.undoIndex + 1); past[past.length - 1] = documentSnapshot(previous);
    const coalesce = kind !== null && draft.undoGroup === kind && draft.undoIndex === draft.undo.length - 1 && past.length > 1;
    draft.undo = (coalesce ? [...past.slice(0, -1), documentSnapshot(next)] : [...past, documentSnapshot(next)]).slice(-100);
    draft.undoGroup = kind;
    let bytes = draft.undo.reduce((total, snapshot) => total + JSON.stringify(snapshot).length, 0);
    while (draft.undo.length > 1 && bytes > 256 * 1024) bytes -= JSON.stringify(draft.undo.shift()).length;
    draft.undoIndex = draft.undo.length - 1;
  }
}
function commitDocument(draft: ComposerDraft, next: ComposerDocument, kind: ComposerEditKind = null) {
  if (!draft.composition) recordDocument(draft, draft, next, kind);
  draft.uploadAnchor = moveUploadAnchor(draft.uploadAnchor, draft, next);
  draft.text = next.text; draft.attachments = next.attachments;
  const length = editorLength(next); draft.selection = next.selection.map((offset) => Math.max(0, Math.min(length, offset))) as [number, number];
}
function finishUpload(draft: ComposerDraft, attachments: ComposerAttachment[]) {
  const anchor = draft.uploadAnchor; if (!anchor) return;
  const next = replaceDocumentRange(draft, anchor.selection, '', attachments);
  if (Math.min(...draft.selection) !== anchor.selection[0] || Math.max(...draft.selection) !== anchor.selection[1]) next.selection = moveUploadAnchor({ key: 'caret', selection: draft.selection }, draft, next)!.selection;
  draft.uploadAnchor = null; draft.completedUpload = null; draft.uploading = false; commitDocument(draft, next);
}
const initialState: { drafts: Record<string, ComposerDraft>; turns: Record<string, SubmittedTurn>; readings: Record<string, MessageReading>; loopCancellations: Record<string, LoopCancellation> } = { drafts: {}, turns: {}, readings: {}, loopCancellations: {} };
export const composerSlice = createSlice({
  name: 'composer', initialState,
  reducers: {
    loopCancellation(state, { payload }: PayloadAction<{ loopId: string; operation: LoopCancellation }>) { state.loopCancellations[payload.loopId] = payload.operation; },
    settleLoopCancellation(state, { payload }: PayloadAction<{ loopId: string; key: string; error?: string; notice?: string }>) {
      const operation = state.loopCancellations[payload.loopId]; if (!operation || operation.key !== payload.key) return;
      operation.pending = false; operation.error = payload.error ?? ''; operation.notice = payload.notice ?? '';
    },
    dismissLoopCancellation(state, { payload }: PayloadAction<string>) { if (!state.loopCancellations[payload]?.pending) delete state.loopCancellations[payload]; },
    reading(state, { payload }: PayloadAction<{ agentId: string; reading: MessageReading }>) { state.readings[payload.agentId] = payload.reading; },
    patch(state, { payload }: PayloadAction<{ cellId: string; changes: Partial<ComposerDraft> }>) {
      const draft = state.drafts[payload.cellId] ?? emptyComposerDraft;
      const selection = payload.changes.selection;
      const moved = selection && (selection[0] !== draft.selection[0] || selection[1] !== draft.selection[1]);
      state.drafts[payload.cellId] = { ...draft, ...payload.changes, ...(moved ? { undoGroup: null } : {}) };
    },
    edit(state, { payload }: PayloadAction<{ cellId: string; text: string; selection?: [number, number]; kind?: ComposerEditKind }>) {
      const draft = state.drafts[payload.cellId] ?? { ...emptyComposerDraft };
      if (draft.pending) return;
      const next = { text: payload.text, attachments: moveAttachments(draft.text, payload.text, draft.attachments), selection: payload.selection ?? [payload.text.length, payload.text.length] as [number, number] };
      commitDocument(draft, next, payload.kind); state.drafts[payload.cellId] = draft;
    },
    document(state, { payload }: PayloadAction<{ cellId: string; document: ComposerDocument; kind?: ComposerEditKind }>) {
      const draft = state.drafts[payload.cellId] ?? { ...emptyComposerDraft }; if (draft.pending) return;
      commitDocument(draft, payload.document, payload.kind); state.drafts[payload.cellId] = draft;
    },
    removeAttachment(state, { payload }: PayloadAction<{ cellId: string; id: string }>) {
      const draft = state.drafts[payload.cellId]; if (!draft || draft.pending) return;
      const token = locatedAttachments(draft).find(({ entry }) => (entry.id ?? entry.path) === payload.id); if (!token) return;
      commitDocument(draft, replaceDocumentRange(draft, [token.offset, token.offset + 1]));
    },
    startUpload(state, { payload }: PayloadAction<{ cellId: string; key: string; selection: [number, number] }>) {
      const draft = state.drafts[payload.cellId] ?? { ...emptyComposerDraft }; if (draft.pending || draft.uploading) return;
      draft.uploading = true; draft.error = ''; draft.uploadAnchor = { key: payload.key, selection: [Math.min(...payload.selection), Math.max(...payload.selection)] }; state.drafts[payload.cellId] = draft;
    },
    finishUpload(state, { payload }: PayloadAction<{ cellId: string; key: string; attachments: ComposerAttachment[] }>) {
      const draft = state.drafts[payload.cellId]; if (!draft || draft.uploadAnchor?.key !== payload.key) return;
      if (draft.composition) { draft.completedUpload = { key: payload.key, attachments: payload.attachments }; return; }
      finishUpload(draft, payload.attachments);
    },
    failUpload(state, { payload }: PayloadAction<{ cellId: string; key: string; error: string }>) {
      const draft = state.drafts[payload.cellId]; if (!draft || draft.uploadAnchor?.key !== payload.key) return;
      draft.uploadAnchor = null; draft.completedUpload = null; draft.uploading = false; draft.error = payload.error;
    },
    startComposition(state, { payload }: PayloadAction<string>) {
      const draft = state.drafts[payload] ?? { ...emptyComposerDraft }; if (draft.pending || draft.composition) return;
      draft.undoGroup = null; draft.composition = documentSnapshot(draft); state.drafts[payload] = draft;
    },
    endComposition(state, { payload }: PayloadAction<string>) {
      const draft = state.drafts[payload]; if (!draft?.composition) return;
      recordDocument(draft, draft.composition, draft); draft.composition = null;
      if (draft.completedUpload?.key === draft.uploadAnchor?.key && draft.completedUpload) finishUpload(draft, draft.completedUpload.attachments);
    },
    undo(state, { payload }: PayloadAction<{ cellId: string; direction: number }>) {
      const draft = state.drafts[payload.cellId]; if (!draft || draft.pending || draft.composition) return;
      draft.undoGroup = null;
      const index = Math.max(0, Math.min(draft.undo.length - 1, draft.undoIndex + payload.direction)); if (index === draft.undoIndex) return;
      draft.undo[draft.undoIndex] = documentSnapshot(draft);
      const next = draft.undo[index]!; draft.uploadAnchor = moveUploadAnchor(draft.uploadAnchor, draft, next);
      draft.undoIndex = index; draft.text = next.text; draft.attachments = next.attachments.map((entry) => ({ ...entry })); draft.selection = [...next.selection];
    },
    submitted(state, { payload }: PayloadAction<{ cellId: string; key: string; message: SentMessage; notice: string }>) {
      const draft = state.drafts[payload.cellId]; if (!draft || draft.attempt?.key !== payload.key) return;
      state.drafts[payload.cellId] = { ...emptyComposerDraft, notice: payload.notice, sent: [payload.message, ...draft.sent].slice(0, 100) };
    },
    turn(state, { payload }: PayloadAction<{ agentId: string; turn: SubmittedTurn }>) { state.turns[payload.agentId] = payload.turn; },
    patchTurn(state, { payload }: PayloadAction<{ agentId: string; key: string; changes: Partial<SubmittedTurn> }>) {
      const turn = state.turns[payload.agentId]; if (turn?.key === payload.key) Object.assign(turn, payload.changes);
    },
  },
});
export const composerActions = composerSlice.actions;
