import { act, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { expect, it, vi } from 'vitest';
import { createAppStore, projectionActions, selectAgentsState, workspaceUiActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import { ControlCenter } from './ControlCenter';
vi.mock('../mission/MissionSummary', () => ({ MissionSummary: () => null }));
vi.mock('./OperationalDetails', () => ({ HealthDetails: () => null, SupervisorDetails: () => null }));

it('counts retained active and stopped records, excludes deleted agents and terminals, and tracks restore deltas', () => {
  const worker = { id: 'worker', name: 'Stopped worker', kind: 'worker', cell_type: 'agent', group: 'Foundation', status: 'stopped', deleted_at: 0 };
  const terminal = { id: 'terminal', name: 'Terminal', kind: 'terminal', cell_type: 'terminal', group: 'Foundation', status: 'running' };
  const removed = { ...worker, id: 'removed', deleted_at: 100 };
  const removedTerminal = { ...terminal, id: 'removed-terminal', deleted_at: 100 };
  const other = { ...worker, id: 'other', group: 'Other' };
  const agents = { worker, terminal, removed, 'removed-terminal': removedTerminal, other };
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived({ ...compactStateFixture, agents })); store.dispatch(workspaceUiActions.setControlTab('mission'));
  const props = { sendCommand: () => true, onCommandUnavailable: () => {} };
  const view = render(<Provider store={store}><ControlCenter group="Foundation" {...props} /></Provider>);
  const header = () => screen.getByRole('region', { name: 'Control Center' }).querySelector('header')!;
  const metric = () => screen.getByText('Agents', { exact: true }).closest('article')!;
  expect(header()).toHaveTextContent('1 agent · 1 terminal'); expect(within(metric()).getByText('1')).toBeVisible();
  act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'agent_upsert', ...worker, deleted_at: 101 }, { op: 'agent_upsert', ...terminal, deleted_at: 101 }] })); });
  expect(header()).toHaveTextContent('0 agents ·'); expect(header()).not.toHaveTextContent('terminal'); expect(within(metric()).getByText('0')).toBeVisible();
  act(() => { store.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 12, ops: [{ op: 'agent_upsert', ...worker }, { op: 'agent_upsert', ...terminal, deleted_at: 0 }] })); });
  expect(header()).toHaveTextContent('1 agent · 1 terminal'); expect(within(metric()).getByText('1')).toBeVisible();
  view.rerender(<Provider store={store}><ControlCenter group="Other" {...props} /></Provider>);
  expect(header()).toHaveTextContent('1 agent ·'); expect(header()).not.toHaveTextContent('terminal');
  view.rerender(<Provider store={store}><ControlCenter group="" {...props} /></Provider>);
  expect(header()).toHaveTextContent('2 agents · 1 terminal'); expect(within(metric()).getByText('2')).toBeVisible();
  // The count projection must not erase recoverable records or historical author identity.
  expect(selectAgentsState(store.getState()).records.removed).toEqual(removed);
});
