import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { UnknownRecord } from '../../protocol';
interface MissionSession { group: string; summary: UnknownRecord | null; selectedId: string; search: string; collapsed: Record<string, boolean>; dismissed: Record<string, boolean> }
// Like Classic, keep disclosure/filter/selection through panel close and refresh.
// A group change discards the previous group's summary and selected card.
export const missionSessionSlice = createSlice({
  name: 'missionSession',
  initialState: (): MissionSession => ({ group: '', summary: null, selectedId: '', search: '', collapsed: {}, dismissed: {} }),
  reducers: {
    enter(state, action: PayloadAction<string>) { if (state.group === action.payload) return; state.group = action.payload; state.summary = null; state.selectedId = ''; },
    received(state, action: PayloadAction<{ group: string; summary: UnknownRecord }>) { if (state.group === action.payload.group) state.summary = action.payload.summary; },
    search(state, action: PayloadAction<string>) { state.search = action.payload; },
    toggle(state, action: PayloadAction<string>) { state.collapsed[action.payload] = !state.collapsed[action.payload]; },
    select(state, action: PayloadAction<string>) { state.selectedId = action.payload; },
    dismissed(state, action: PayloadAction<string>) { state.dismissed[action.payload] = true; if (state.selectedId === action.payload) state.selectedId = ''; },
  },
});
export const missionSessionActions = missionSessionSlice.actions;
