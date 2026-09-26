import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { Scope } from './model';
export interface Draft { text: string; rationale: string; baseText: string; dirty: boolean; attempt?: { signature: string; key: string }; submitted?: { signature: string; id: string } }
interface BehaviorSession { selections: Record<string, Pick<Scope, 'kind' | 'target'>>; drafts: Record<string, Draft> }
const fresh = (): Draft => ({ text: '', rationale: '', baseText: '', dirty: false });
export const behaviorSessionSlice = createSlice({
  name: 'behaviorSession',
  initialState: (): BehaviorSession => ({ selections: {}, drafts: {} }),
  reducers: {
    select(state, { payload }: PayloadAction<Scope>) { state.selections[payload.group] = { kind: payload.kind, target: payload.target }; },
    seed(state, { payload }: PayloadAction<{ key: string; text: string }>) { const draft = state.drafts[payload.key] ??= fresh(); if (!draft.dirty) draft.text = payload.text; draft.baseText = payload.text; draft.dirty = draft.text !== draft.baseText || !!draft.rationale; },
    edit(state, { payload }: PayloadAction<{ key: string; field: 'text' | 'rationale'; value: string }>) { const draft = state.drafts[payload.key] ??= fresh(); draft[payload.field] = payload.value; draft.dirty = draft.text !== draft.baseText || !!draft.rationale; },
    attempt(state, { payload }: PayloadAction<{ key: string; signature: string; token: string }>) { const draft = state.drafts[payload.key]; if (draft) draft.attempt = { signature: payload.signature, key: payload.token }; },
    submitted(state, { payload }: PayloadAction<{ key: string; signature: string; id: string }>) { const draft = state.drafts[payload.key]; if (draft) draft.submitted = { signature: payload.signature, id: payload.id }; },
  },
});
export const behaviorActions = behaviorSessionSlice.actions;
