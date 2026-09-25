import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
interface IndexStartState { id: string; phase: 'idle' | 'pending' | 'ready' | 'error'; jobId: string; error: string }
// The accepted job is daemon-owned; this state owns only the start request.
export const aiIndexStartSlice = createSlice({
  name: 'aiIndexStart', initialState: (): IndexStartState => ({ id: '', phase: 'idle', jobId: '', error: '' }),
  reducers: {
    started(state, action: PayloadAction<string>) { if (state.phase === 'pending') return; state.id = action.payload; state.phase = 'pending'; state.jobId = ''; state.error = ''; },
    accepted(state, action: PayloadAction<{ id: string; jobId: string }>) { if (state.id !== action.payload.id || state.phase !== 'pending') return; state.phase = 'ready'; state.jobId = action.payload.jobId; },
    failed(state, action: PayloadAction<{ id: string; error: string }>) { if (state.id !== action.payload.id || state.phase !== 'pending') return; state.phase = 'error'; state.error = action.payload.error; },
  },
});
export const aiIndexStartActions = aiIndexStartSlice.actions;
