import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
export interface ComposerAttachment { path: string; filename: string; mime_type?: string; size_bytes?: number }
export interface ReplyTarget { id: string; agentId: string; preview: string }
export interface SentMessage { id: string; message: string; at: number }
export interface ComposerDraft {
  text: string; attachments: ComposerAttachment[]; reply: ReplyTarget | null;
  selection: [number, number]; scrollTop: number;
  undo: string[]; undoIndex: number;
  pending: boolean; uploading: boolean; error: string; notice: string;
  attempt: { fingerprint: string; key: string } | null;
  sent: SentMessage[];
  recall: { original: string; selection: [number, number]; index: number } | null;
}
export interface SubmittedTurn { key: string; sessionId: string; pending: boolean; cancelKey: string; error: string; notice: string }
export const emptyComposerDraft: ComposerDraft = { text: '', attachments: [], reply: null, selection: [0, 0], scrollTop: 0, undo: [''], undoIndex: 0, pending: false, uploading: false, error: '', notice: '', attempt: null, sent: [], recall: null };
const initialState: { drafts: Record<string, ComposerDraft>; turns: Record<string, SubmittedTurn> } = { drafts: {}, turns: {} };
export const composerSlice = createSlice({
  name: 'composer', initialState,
  reducers: {
    patch(state, { payload }: PayloadAction<{ cellId: string; changes: Partial<ComposerDraft> }>) {
      state.drafts[payload.cellId] = { ...(state.drafts[payload.cellId] ?? emptyComposerDraft), ...payload.changes };
    },
    edit(state, { payload }: PayloadAction<{ cellId: string; text: string; selection?: [number, number] }>) {
      const draft = state.drafts[payload.cellId] ?? { ...emptyComposerDraft };
      if (draft.pending) return;
      if (draft.text !== payload.text) {
        draft.undo = [...draft.undo.slice(0, draft.undoIndex + 1), payload.text].slice(-100); draft.undoIndex = draft.undo.length - 1;
      }
      draft.text = payload.text; draft.selection = payload.selection ?? [payload.text.length, payload.text.length];
      state.drafts[payload.cellId] = draft;
    },
    undo(state, { payload }: PayloadAction<{ cellId: string; direction: number }>) {
      const draft = state.drafts[payload.cellId]; if (!draft || draft.pending) return;
      draft.undoIndex = Math.max(0, Math.min(draft.undo.length - 1, draft.undoIndex + payload.direction));
      draft.text = draft.undo[draft.undoIndex] ?? ''; draft.selection = [draft.text.length, draft.text.length];
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
