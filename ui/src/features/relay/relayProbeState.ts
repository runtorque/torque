import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
export interface ProbeResult { status: string; message: string; detail: string }
interface ProbeState { id: string; phase: 'idle' | 'pending' | 'ready' | 'error'; result: ProbeResult | null; error: string }
// Classic retains its probe result when Settings closes. Keep this read-only
// operation in the app session so navigation/reconnect cannot lose or replay it.
export const relayProbeSlice = createSlice({
  name: 'relayProbe',
  initialState: (): ProbeState => ({ id: '', phase: 'idle', result: null, error: '' }),
  reducers: {
    started(state, action: PayloadAction<string>) { if (state.phase === 'pending') return; state.id = action.payload; state.phase = 'pending'; state.result = null; state.error = ''; },
    succeeded(state, action: PayloadAction<{ id: string; result: ProbeResult }>) { if (state.id !== action.payload.id || state.phase !== 'pending') return; state.phase = 'ready'; state.result = action.payload.result; },
    failed(state, action: PayloadAction<{ id: string; error: string }>) { if (state.id !== action.payload.id || state.phase !== 'pending') return; state.phase = 'error'; state.error = action.payload.error; },
  },
});
export const relayProbeActions = relayProbeSlice.actions;
