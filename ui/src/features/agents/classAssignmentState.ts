import { createSlice, type PayloadAction } from '@reduxjs/toolkit';

interface AssignmentState { draft: string | null; pending: boolean; error: string; message: string; revision: number }
export const emptyAssignment: AssignmentState = { draft: null, pending: false, error: '', message: '', revision: 0 };

// Operator draft and in-flight write state belong to the app session, not to
// the mounted Activity tab. Navigation and compact snapshots cannot drop them.
export const classAssignmentSlice = createSlice({
  name: 'classAssignments',
  initialState: (): Record<string, AssignmentState> => ({}),
  reducers: {
    selected(state, action: PayloadAction<{ agentId: string; value: string }>) {
      const item = state[action.payload.agentId] ??= { ...emptyAssignment };
      if (!item.pending) { item.draft = action.payload.value; item.error = ''; item.message = ''; }
    },
    started(state, action: PayloadAction<{ agentId: string; value: string }>) {
      const item = state[action.payload.agentId] ??= { ...emptyAssignment };
      if (!item.pending) { item.draft = action.payload.value; item.pending = true; item.error = ''; item.message = ''; }
    },
    finished(state, action: PayloadAction<{ agentId: string; error: string; message: string }>) {
      const item = state[action.payload.agentId];
      if (!item) return;
      item.pending = false; item.error = action.payload.error; item.message = action.payload.message; item.revision++;
      if (!item.error) item.draft = null;
    },
  },
});
export const classAssignmentActions = classAssignmentSlice.actions;
