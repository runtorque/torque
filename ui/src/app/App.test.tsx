import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { describe, expect, it, vi } from 'vitest';

import { browserHost, createTauriHost } from '../host';
import { compactStateFixture } from '../protocol/fixtures';
import type { TorqueCommand } from '../protocol/commands';
import type { StateFrame } from '../protocol/types';
import { ThinkingEditor } from '../features/planning/PlanningEditors';
import { WorkspaceShell } from './App';
import { sanitizeClientError } from './clientDiagnostics';
import { connectionActions, createAppStore, projectionActions } from './store';

function renderShell(host = browserHost, frame: StateFrame = compactStateFixture) {
  const appStore = createAppStore();
  appStore.dispatch(projectionActions.snapshotReceived(frame));
  appStore.dispatch(connectionActions.connected({ at: 1_000, reconnect: false }));
  appStore.dispatch(connectionActions.snapshotAccepted(frame));
  const sendCommand = vi.fn<(command: TorqueCommand) => boolean>(() => true);
  render(
    <Provider store={appStore}>
      <WorkspaceShell host={host} sendCommand={sendCommand} />
    </Provider>,
  );
  return { appStore, sendCommand };
}

describe('workspace shell', () => {
  it('renders the live Board, group navigation, and connection state', () => {
    renderShell();

    expect(screen.getByRole('heading', { name: 'Board' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Groups' })).toBeVisible();
    expect(screen.getByText('Build the foundation')).toBeVisible();
    expect(screen.getByText('connected')).toBeVisible();
    expect(screen.getByText('phase-one-test')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Classic UI' })).toHaveAttribute(
      'href',
      'http://localhost:3000/legacy/',
    );
  });

  it('fuzzy-filters commands and runs the active result from the keyboard', () => {
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Search commands/ }));
    const search = screen.getByRole('combobox', { name: 'Search commands' });

    fireEvent.change(search, { target: { value: 'new work' } });
    expect(screen.getByRole('option', { name: 'New Worker' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('option', { name: 'Open Board' })).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: 'open' } });
    expect(screen.getByRole('option', { name: 'Open Board' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    expect(screen.getByRole('option', { name: 'Open Agents' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(search, { key: 'Enter' });

    expect(screen.getByRole('heading', { name: 'Agents' })).toBeVisible();
  });

  it('sizes Board tracks from the viewport and lane count rather than card content', () => {
    renderShell();

    const backlogLane = screen.getByRole('heading', { name: 'Backlog' }).closest('section');
    const laneGrid = backlogLane?.parentElement;
    expect(laneGrid).toHaveStyle({
      gridTemplateColumns: 'repeat(4, minmax(270px, 1fr))',
      minWidth: '1080px',
    });
  });

  it('limits selectable card hit areas to rendered text instead of full-width rows', () => {
    renderShell();
    const title = screen.getByText('Build the foundation');
    expect(title).toHaveAttribute('data-board-text');
    expect(title.closest('h3')).not.toHaveAttribute('data-board-text');
    expect(title.closest('article')).toHaveAttribute('data-task-id', 'task-1');
  });

  it('exposes full Board labels through a tooltip when chips truncate', () => {
    renderShell(browserHost, {
      ...compactStateFixture,
      board_tasks: {
        'task-1': {
          ...(compactStateFixture.board_tasks as Record<string, Record<string, unknown>>)['task-1'],
          labels: ['very-long-label-that-must-remain-readable'],
        },
      },
    });

    expect(screen.getByText('very-long-label-that-must-remain-readable')).toHaveAttribute('title', 'very-long-label-that-must-remain-readable');
  });

  it('distinguishes who created, owns, and actively executes a Board task', () => {
    const { appStore } = renderShell(browserHost, {
      ...compactStateFixture,
      agents: {
        architect: { id: 'architect', name: 'Aria', kind: 'architect', group: 'Foundation' },
        engineer: { id: 'engineer', name: 'Evan', kind: 'engineer', group: 'Foundation' },
        worker: { id: 'worker', name: 'Wren', kind: 'worker', group: 'Foundation', owner_engineer_id: 'engineer' },
      },
      board_tasks: {
        'task-1': {
          ...(compactStateFixture.board_tasks as Record<string, Record<string, unknown>>)['task-1'],
          created_by_architect_id: 'architect',
          assigned_engineer_id: 'engineer',
          agent_id: 'worker',
        },
      },
    });

    expect(screen.getByLabelText('Created by Aria, Architect')).toBeVisible();
    expect(screen.getByLabelText('Executing Evan, Engineer, via Wren, Worker')).toBeVisible();
    expect(screen.queryByText('⠿')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open agent Evan' }));
    expect(appStore.getState().workspaceUi).toMatchObject({ activePanel: 'agents', selectedAgentId: 'engineer', selectedTaskIds: [] });
    expect(screen.getByRole('heading', { name: 'Agents' })).toBeVisible();
  });

  it('progressively mounts large lanes as their tail approaches the viewport', () => {
    let intersectionCallback: IntersectionObserverCallback | undefined;
    let intersectionOptions: IntersectionObserverInit | undefined;
    vi.stubGlobal('IntersectionObserver', class {
      root = null;
      rootMargin = '';
      thresholds = [];
      constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
        intersectionCallback = callback;
        intersectionOptions = options;
      }
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() { return []; }
    });
    const boardTasks = Object.fromEntries(Array.from({ length: 45 }, (_, index) => {
      const id = `done-${index + 1}`;
      return [id, { id, task: `Completed task ${index + 1}`, group: 'Foundation', lane: 'Done', position: index }];
    }));
    renderShell(browserHost, { ...compactStateFixture, board_tasks: boardTasks });

    const doneLane = screen.getByRole('heading', { name: 'Done' }).closest('section');
    expect(doneLane).not.toBeNull();
    const lane = within(doneLane as HTMLElement);
    expect(lane.getAllByRole('article')).toHaveLength(20);
    expect(lane.getByLabelText('25 more tasks in Done')).toBeVisible();
    expect(lane.queryByText('Completed task 21')).not.toBeInTheDocument();
    expect(intersectionOptions?.root).toBe(doneLane?.querySelector('[data-lane-id="lane:Done"]'));

    act(() => intersectionCallback?.(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    ));
    expect(lane.getAllByRole('article')).toHaveLength(40);
    expect(lane.getByText('Completed task 40')).toBeVisible();
    expect(lane.getByLabelText('5 more tasks in Done')).toBeVisible();
    vi.unstubAllGlobals();
  });

  it('keeps active and archived Board counts scoped to the visible surface', () => {
    renderShell(browserHost, {
      ...compactStateFixture,
      board_tasks: {
        ...(compactStateFixture.board_tasks as Record<string, unknown>),
        archived: { id: 'archived', task: 'Archived task', group: 'Foundation', lane: 'Archived' },
      },
    });

    expect(screen.getByText('1 visible · 1 total')).toBeVisible();
    expect(screen.queryByText('Archived task')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Archive' }));
    expect(screen.getByText('Archived task')).toBeVisible();
    expect(screen.getByText('1 visible · 1 total')).toBeVisible();
    expect(screen.queryByRole('button', { name: '＋ Add task' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Actions for Archived task' }));
    expect(screen.getByRole('menuitem', { name: 'Dispatch' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('menuitem', { name: 'Move to Done' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('prefers the current active task record over a stale archived-detail copy', () => {
    renderShell(browserHost, {
      ...compactStateFixture,
      board_tasks: {
        ...(compactStateFixture.board_tasks as Record<string, unknown>),
        restored: { id: 'restored', task: 'Restored task', group: 'Foundation', lane: 'Ready' },
      },
      board_tasks_archived: {
        Foundation: [{ id: 'restored', task: 'Restored task', group: 'Foundation', lane: 'Archived', description: 'stale' }],
      },
    });

    expect(screen.getByLabelText('Restored task, Ready')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Archive' }));
    expect(screen.queryByText('Restored task')).not.toBeInTheDocument();
  });

  it('opens the full task dialog from the global Board command', () => {
    const { appStore, sendCommand } = renderShell();
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({
      type: 'actions',
      actions: [{ name: 'feature/implement' }, { name: 'feature/implement' }, { name: 'oneshot/fix' }],
    })); });
    fireEvent.click(screen.getByRole('button', { name: '＋ New task' }));
    expect(screen.getByRole('dialog', { name: 'Create task' })).toBeVisible();
    expect(within(screen.getByRole('combobox', { name: 'Action' })).getAllByRole('option', { name: 'feature/implement' })).toHaveLength(1);
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), {
      target: { value: 'Ship Phase 2' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Action variables (JSON)' }), { target: { value: '[]' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    expect(screen.getByText('Action variables must be a JSON object.')).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', { name: 'Action variables (JSON)' }), { target: { value: '{}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));

    expect(sendCommand).toHaveBeenCalledWith({
      cmd: 'board_add_task',
      task: 'Ship Phase 2',
      group: 'Foundation',
      lane: 'Backlog',
      description: '',
      labels: [],
      action_name: '',
      agent_template: '',
      action_vars: {},
      scheduled_at: '',
    });
  });

  it('keeps lane-local creation above that lane task list', () => {
    renderShell();
    const firstTask = screen.getByText('Build the foundation').closest('article') as HTMLElement;
    const lane = firstTask.closest('section') as HTMLElement;
    const laneName = within(lane).getByRole('heading', { level: 2 }).textContent ?? '';
    const addTask = within(lane).getByRole('button', { name: '＋ Add task' });
    expect(addTask.compareDocumentPosition(firstTask) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(addTask);
    const inlineTitle = within(lane).getByRole('textbox', { name: `New task in ${laneName}` });
    expect(inlineTitle.compareDocumentPosition(firstTask) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('reports invalid schedule variables instead of silently discarding the submission', () => {
    const { sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: 'Schedules' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Daily audit' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Task title' }), { target: { value: 'Review Board health' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Cron' }), { target: { value: '0 9 * * 1-5' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Action variables (JSON)' }), { target: { value: '[]' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create schedule' }));

    expect(screen.getByText('Action variables must be a valid JSON object.')).toBeVisible();
    expect(sendCommand).not.toHaveBeenCalledWith(expect.objectContaining({ cmd: 'schedule_create' }));

    fireEvent.change(screen.getByRole('textbox', { name: 'Action variables (JSON)' }), { target: { value: '{}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create schedule' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({
      cmd: 'schedule_create', name: 'Daily audit', task_template: 'Review Board health', cron_expr: '0 9 * * 1-5',
    }));
  });

  it('right-aligns status details without duplicating host branding', () => {
    renderShell();
    const status = screen.getByRole('contentinfo', { name: 'Workspace status' });
    expect(status).not.toHaveTextContent('Torque');
    expect(status).not.toHaveTextContent('tauri');
  });

  it('renders through the Tauri host boundary without Tauri imports in features', () => {
    const tauriHost = createTauriHost(vi.fn(() => Promise.resolve(null)));
    renderShell(tauriHost);

    expect(screen.getByText('tauri')).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Board' })).toBeVisible();
  });

  it('uses icon-only workspace detach actions without a terminal detach row', () => {
    const tauriHost = createTauriHost(vi.fn((command: string) => Promise.resolve(command === 'detach' ? 'agents-window' : null)));
    renderShell(tauriHost);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));

    const workspaceDetach = screen.getByRole('button', { name: 'Detach Agents workspace' });
    const selectedDetach = screen.getByRole('button', { name: 'Detach selected agent workspace' });
    expect(workspaceDetach).toHaveTextContent('↗');
    expect(selectedDetach).toHaveTextContent('↗');
    expect(workspaceDetach).not.toHaveTextContent('Detach');
    expect(selectedDetach).not.toHaveTextContent('Detach');
    expect(screen.queryByRole('button', { name: /Detach terminal/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: 'Agent terminals' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Agent' })).not.toBeInTheDocument();
  });

  it('routes card menu actions through the Board command contract', () => {
    const { sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: 'Actions for Build the foundation' }));
    expect(screen.getByRole('menuitem', { name: 'Sync external ticket' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Move to Done' }));

    expect(sendCommand).toHaveBeenCalledWith({
      cmd: 'board_move_task',
      id: 'task-1',
      lane: 'Done',
    });
  });

  it('edits the complete task contract and exposes external, verification, artifact, and human workflows', () => {
    const frame: StateFrame = {
      ...compactStateFixture,
      board_tasks: {
        'task-1': {
          ...(compactStateFixture.board_tasks as Record<string, Record<string, unknown>>)['task-1'],
          labels: ['torque:human'], depends_on: ['task-2'],
          provider: 'github', external_id: 'owner/repo#12', external_url: 'https://github.com/owner/repo/issues/12',
          board_sync: { version: 1, enabled: true, provider: 'github' },
        },
        'task-2': { id: 'task-2', task: 'Dependency', group: 'Foundation', lane: 'To Do', position: 0 },
      },
    };
    const { appStore, sendCommand } = renderShell(browserHost, frame);
    fireEvent.doubleClick(screen.getByLabelText('Build the foundation, In Progress'));

    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'task_detail', id: 'task-1' });
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'list_actions', group: 'Foundation' });
    expect(screen.getByText('Retrieving complete task fields.')).toBeVisible();
    act(() => { appStore.dispatch(projectionActions.taskDetailReceived({
      type: 'task_detail',
      id: 'task-1',
      task: {
        ...(frame.board_tasks as Record<string, Record<string, unknown>>)['task-1'],
        description: 'Hydrated task description',
        action_vars: { scope: 'ui' },
        attachments: [],
      },
    })); });
    expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('Hydrated task description');
    expect(screen.getByRole('dialog').querySelector('[data-dialog-body-layout="fit"]')).not.toBeNull();
    expect(screen.getByRole('region', { name: 'Primary task fields' })).toBeVisible();
    expect(screen.getByRole('complementary', { name: 'Task status and primary actions' })).toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: 'Verification' }));
    expect(screen.getByRole('heading', { name: 'Verification' })).toBeVisible();
    expect(screen.getByRole('textbox', { name: 'Description' })).toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: 'Integrations' }));
    expect(screen.getByRole('heading', { name: 'External ticket and sync' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Human response' })).toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: 'Evidence' }));
    expect(screen.getByRole('heading', { name: 'Artifacts and attachments' })).toBeVisible();

    fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: 'Complete parity coverage' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Execution' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Action variables (JSON)' }), { target: { value: '{"scope":"board"}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({
      cmd: 'board_update_task', id: 'task-1', description: 'Complete parity coverage',
      action_vars: { scope: 'board' }, depends_on: ['task-2'], provider: 'github',
      external_id: 'owner/repo#12',
    }));
  });

  it('supports multi-select batch operations, archived loading, and external import', () => {
    const frame: StateFrame = {
      ...compactStateFixture,
      board_tasks: {
        ...(compactStateFixture.board_tasks as Record<string, unknown>),
        'task-2': { id: 'task-2', task: 'Second task', group: 'Foundation', lane: 'Backlog', position: 2 },
      },
    };
    const { sendCommand } = renderShell(browserHost, frame);
    fireEvent.click(screen.getByLabelText('Build the foundation, In Progress'));
    expect(screen.queryByText('1 selected')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Batch edit' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Second task, Backlog'), { ctrlKey: true });
    expect(screen.getByText('2 selected')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Dispatch' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'dispatch_task', id: 'task-1' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'dispatch_task', id: 'task-2' }));

    fireEvent.click(screen.getByRole('button', { name: 'Archive' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'archived_tasks', group: 'Foundation' });
    fireEvent.click(screen.getByRole('button', { name: 'Import external' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'External reference or URL' }), { target: { value: 'https://github.com/owner/repo/issues/42' } });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'external_import_task', ref: 'https://github.com/owner/repo/issues/42', group: 'Foundation' }));
  });

  it('opens the Phase 3 agent workspace and routes lifecycle actions', () => {
    const { sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));

    expect(screen.getByRole('heading', { name: 'Agents' })).toBeVisible();
    expect(screen.getByLabelText('Foundation Worker, worker, running')).toBeVisible();
    expect(screen.getByText('Terminal stopped')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Lifecycle actions for Foundation Worker' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Restart' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'restart_agent', id: 'agent-1' });
  });

  it('resizes the terminal and direct-message split with an accessible separator', () => {
    const { sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));

    const separator = screen.getByRole('separator', { name: 'Resize terminal and direct messages' });
    const initialHeight = Number(separator.getAttribute('aria-valuenow'));
    expect(initialHeight).toBeGreaterThan(0);

    fireEvent.keyDown(separator, { key: 'ArrowUp' });
    expect(separator).toHaveAttribute('aria-valuenow', String(initialHeight + 24));
    expect(sendCommand).toHaveBeenCalledWith({
      cmd: 'ui_set_terminal_direct_messages_height',
      height: initialHeight + 24,
    });

    fireEvent.pointerDown(separator, { pointerId: 7, clientY: 500 });
    fireEvent.pointerMove(separator, { pointerId: 7, clientY: 450 });
    fireEvent.pointerUp(separator, { pointerId: 7, clientY: 450 });
    expect(separator).toHaveAttribute('aria-valuenow', String(initialHeight + 74));
    expect(sendCommand).toHaveBeenLastCalledWith({
      cmd: 'ui_set_terminal_direct_messages_height',
      height: initialHeight + 74,
    });

    const composerSeparator = screen.getByRole('separator', { name: 'Resize message text box' });
    const initialComposerHeight = Number(composerSeparator.getAttribute('aria-valuenow'));
    fireEvent.keyDown(composerSeparator, { key: 'ArrowUp' });
    expect(composerSeparator).toHaveAttribute('aria-valuenow', String(initialComposerHeight + 24));
    expect(sendCommand).toHaveBeenLastCalledWith({
      cmd: 'ui_set_terminal_compose_height',
      height: initialComposerHeight + 24,
    });
  });

  it('marks sent and received chat messages with distinct directions', () => {
    renderShell(browserHost, {
      ...compactStateFixture,
      direct_messages_by_agent: {
        'agent-1': [
          { id: 'received', sender_kind: 'agent', message: 'Received from the agent', created_at: 1 },
          { id: 'sent', sender_kind: 'user', message: 'Sent by the operator', created_at: 2 },
        ],
      },
    });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));

    expect(screen.getByText('Received from the agent').closest('article')).toHaveAttribute('data-direction', 'inbound');
    expect(screen.getByText('Sent by the operator').closest('article')).toHaveAttribute('data-direction', 'outbound');
  });

  it('preserves the direct-message draft while Activity owns the right pane', () => {
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Foundation Worker' }), { target: { value: 'Draft handoff' } });

    const livePanel = screen.getByRole('region', { name: 'Focused agent Foundation Worker' });
    const viewSwitch = screen.getByRole('tablist', { name: 'View for Foundation Worker' });
    expect(livePanel).not.toContainElement(viewSwitch);
    fireEvent.click(within(viewSwitch).getByRole('tab', { name: 'Activity' }));
    const activityPanel = screen.getByRole('region', { name: 'Activity for Foundation Worker' });
    expect(activityPanel).toBeVisible();
    expect(screen.getByRole('tablist', { name: 'View for Foundation Worker' })).toBe(viewSwitch);
    expect(activityPanel).not.toContainElement(viewSwitch);
    expect(within(activityPanel).getByRole('tab', { name: 'Events' })).toBeVisible();
    expect(within(activityPanel).getByRole('tab', { name: 'Messages' })).toBeVisible();
    expect(within(activityPanel).getByRole('tab', { name: 'Worklog' })).toBeVisible();
    expect(screen.getByRole('tree', { name: 'Agent ownership hierarchy' })).toBeVisible();
    expect(screen.queryByRole('region', { name: 'Conversation with Foundation Worker' })).not.toBeInTheDocument();
    fireEvent.click(within(viewSwitch).getByRole('tab', { name: 'Live' }));

    expect(screen.getByRole('textbox', { name: 'Message Foundation Worker' })).toHaveValue('Draft handoff');
  });

  it('uses one focused onboarding surface when an Agents group is empty', () => {
    renderShell(browserHost, { ...compactStateFixture, agents: {} });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));

    expect(screen.getByText('Start an agent workspace')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Create Worker' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Open Terminal' })).toBeVisible();
    expect(screen.queryByText('Select an agent')).not.toBeInTheDocument();
  });

  it('renders unattached terminals instead of leaving the agent hierarchy blank', () => {
    renderShell(browserHost, {
      ...compactStateFixture,
      agents: {
        ...(compactStateFixture.agents as Record<string, unknown>),
        terminal: {
          id: 'terminal',
          name: 'Audit Terminal',
          group: 'Foundation',
          cell_type: 'terminal',
          status: 'idle',
        },
      },
    });

    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.getByText('1 agent · 1 terminal')).toBeVisible();
    expect(screen.getByLabelText('Audit Terminal, terminal, idle')).toBeVisible();
    fireEvent.click(screen.getByLabelText('Audit Terminal, terminal, idle'));
    expect(screen.getByRole('heading', { name: 'Audit Terminal' })).toBeVisible();
    expect(screen.getByRole('region', { name: 'Focused terminal Audit Terminal' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Inspect' })).not.toBeInTheDocument();
    expect(screen.queryByText('Worktree controls')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Conversation with Audit Terminal' })).not.toBeInTheDocument();
  });

  it('creates every agent kind from the React workspace without returning to Classic', () => {
    const { sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Create agent or terminal' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'New Worker…' }));

    expect(screen.getByRole('dialog', { name: 'New worker' })).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'UI Worker' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Provider' }), { target: { value: 'codex' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create worker' }));

    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({
      cmd: 'add_worker',
      name: 'UI Worker',
      group: 'Foundation',
      provider: 'codex',
      worktree: false,
    }));
  });

  it('restores or explicitly purges agents during the seven-day deletion window', () => {
    const frame: StateFrame = {
      ...compactStateFixture,
      agents: {
        ...(compactStateFixture.agents as Record<string, unknown>),
        deleted: { id: 'deleted', name: 'Recoverable Worker', group: 'Foundation', kind: 'worker', status: 'stopped', deleted_at: 100, worktree_path: '/tmp/recoverable' },
      },
    };
    const { sendCommand } = renderShell(browserHost, frame);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Recently deleted · 1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'restore_agent', id: 'deleted' });

    fireEvent.click(screen.getByRole('button', { name: 'Delete permanently…' }));
    expect(screen.getByText('This permanently removes the agent and any unshared tracked worktree. This cannot be undone.')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Delete permanently' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'purge_agent_now', id: 'deleted' });
  });

  it('retains concurrent worktree responses and renders diff, preflight, and history together', () => {
    const frame: StateFrame = {
      ...compactStateFixture,
      agents: {
        'agent-1': {
          ...(compactStateFixture.agents as Record<string, Record<string, unknown>>)['agent-1'],
          worktree_path: '/tmp/worker',
          worktree_branch: 'torque/ui-worker',
          worktree_base_branch: 'main',
        },
      },
    };
    const { appStore, sendCommand } = renderShell(browserHost, frame);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Inspect diff' }));

    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'worktree_diff_full', id: 'agent-1' });
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'worktree_check_merge', id: 'agent-1' });
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'worktree_history', id: 'agent-1' });

    act(() => {
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'worktree_diff_full', id: 'agent-1', branch: 'torque/ui-worker', base_branch: 'main',
        stats: { insertions: 2, deletions: 1 },
        files: [{ path: 'ui.tsx', status: 'modified', insertions: 2, deletions: 1, hunks: [{ header: '@@ -1 +1 @@', lines: [{ type: 'add', text: 'new UI' }] }] }],
      }));
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'worktree_check_merge', id: 'agent-1', clean: true, default_message: 'Ship UI', conflicts: [],
      }));
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'worktree_history', id: 'agent-1', commits: [{ sha: 'abcdef123456', short_sha: 'abcdef1', message: 'Checkpoint', date: 'now' }],
      }));
    });

    expect(screen.getByText('ui.tsx')).toBeVisible();
    expect(screen.getByText('Clean merge')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /History 1/ }));
    expect(screen.getByText('abcdef1 · now · +0 −0')).toBeVisible();
  });

  it('shows agent events, MCP calls, persisted history, and Agent Class state inside Activity', () => {
    const { appStore, sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(within(screen.getByRole('tablist', { name: 'View for Foundation Worker' })).getByRole('tab', { name: 'Activity' }));

    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'get_cell_events', cell_id: 'agent-1', limit: 20 });
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'mcp_calls', cell_id: 'agent-1' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'agent_class_status', agent_id: 'agent-1' });
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'agent_class_audit', agent_id: 'agent-1', limit: 20 });
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'get_agent_history_detail', agent_id: 'agent-1', message_limit: 20 });

    act(() => {
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'cell_events', cell_id: 'agent-1',
        events: [{ id: 'event-1', kind: 'task_progress', timestamp: 100, message: 'Implemented inspector', source: 'event_log' }],
      }));
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'mcp_calls', cell_id: 'agent-1', agent_id: 'agent-1',
        calls: [{ cursor: 'call-1', tool_name: 'mcp__torque__task_progress', appended_at: 101, success: true, duration_ms: 12 }],
      }));
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'agent_history_detail',
        record: { id: 'agent-1', status: 'active', provider: 'codex', started_at: 99 },
        tasks: [{ task_id: 'task-1', task: 'Build the foundation', lane: 'In Progress' }],
        messages: [{ id: 'message-1', action: 'progress', message: 'History retained', timestamp: 102 }],
      }));
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'agent_classes',
        classes: [{ id: 'default-worker', display_name: 'Default Worker', base_kind: 'worker', version: '1' }],
        issues: [],
      }));
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'agent_class_status',
        status: { agent_id: 'agent-1', effective_class_id: 'default-worker', effective_primary_identity_label: 'Default Worker', next_launch_class_id: 'default-worker', effective_class_version: '1', next_launch_class_version: '1' },
      }));
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'agent_class_audit', agent_id: 'agent-1',
        events: [{ id: 'audit-1', event: 'assignment_set', message: 'Desired class saved', created_at: 103 }],
      }));
    });

    const activity = screen.getByRole('region', { name: 'Activity for Foundation Worker' });
    expect(within(activity).getByText('Implemented inspector')).not.toBeVisible();
    fireEvent.click(within(activity).getByText('task progress'));
    expect(within(activity).getByText('Implemented inspector')).toBeVisible();
    fireEvent.click(within(activity).getByRole('tab', { name: 'MCP' }));
    expect(within(activity).getByText('mcp__torque__task_progress')).toBeVisible();
    fireEvent.click(within(activity).getByRole('tab', { name: 'History' }));
    const retainedMessages = within(activity).getAllByText('History retained');
    expect(retainedMessages[0]).toBeVisible();
    expect(retainedMessages[1]).not.toBeVisible();
    fireEvent.click(within(activity).getByText('progress'));
    expect(retainedMessages[1]).toBeVisible();
    fireEvent.click(within(activity).getByRole('tab', { name: 'Agent Class' }));
    expect(within(activity).getAllByText('Default Worker')).toHaveLength(2);
    expect(within(activity).getByText('Desired class saved')).not.toBeVisible();
    fireEvent.click(within(activity).getByText('assignment set'));
    expect(within(activity).getByText('Desired class saved')).toBeVisible();
    expect(screen.queryByRole('dialog', { name: /Inspect Foundation Worker/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Inspect activity/ })).not.toBeInTheDocument();
  });

  it('shows server-resolved per-agent setting origins without writing an unchanged form', () => {
    const principalFrame: StateFrame = {
      ...compactStateFixture,
      agents: {
        architect: { id: 'architect', name: 'Principal', group: 'Foundation', kind: 'architect', status: 'idle' },
      },
      agent_settings: { architect: { provider: null, model: null } },
      resolved_agent_settings: {
        architect: {
          provider: { value: 'codex', origin: 'group' },
          model: { value: 'gpt-5.6-sol', origin: 'default' },
          reasoning_effort: { value: 'high', origin: 'group' },
          custom_instructions: { value: '', origin: 'default' },
        },
      },
    };
    const { sendCommand } = renderShell(browserHost, principalFrame);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    expect(screen.getAllByText('group')).toHaveLength(2);
    expect(screen.getAllByText('default')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(sendCommand).not.toHaveBeenCalledWith(expect.objectContaining({ cmd: 'update_agent_settings' }));
  });

  it('opens Phase 4 Planning, lazy-loads its resources, and creates an initiative', async () => {
    const { sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Planning/ }));

    expect(await screen.findByRole('heading', { name: 'Planning' })).toBeVisible();
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'initiative_list', group: 'Foundation', include_archived: false });
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'engineer_journal_snapshot', group: 'Foundation', include_streams: true });

    fireEvent.click(screen.getByRole('button', { name: '＋ New' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'Ship Phase 4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(sendCommand).toHaveBeenCalledWith({
      cmd: 'initiative_create',
      group: 'Foundation',
      title: 'Ship Phase 4',
      summary: '',
      planning_status: 'triage',
    });
  });

  it('does not offer unrelated initiative creation from read-only Planning sections', async () => {
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Planning/ }));
    expect(await screen.findByRole('heading', { name: 'Planning' })).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Hires & journals' }));
    expect(screen.queryByRole('button', { name: '＋ New' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Schedules' }));
    expect(screen.queryByRole('button', { name: '＋ New' })).not.toBeInTheDocument();
  });

  it('requires an Architect before creating an Architect decision', async () => {
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Planning/ }));
    expect(await screen.findByRole('heading', { name: 'Planning' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Decisions' }));
    fireEvent.click(screen.getByRole('button', { name: '＋ New' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'Architecture direction' } });
    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled();
  });

  it('edits initiative scope and links durable Board work from Planning', async () => {
    const frame: StateFrame = {
      ...compactStateFixture,
      initiatives: { 'initiative-1': { id: 'initiative-1', group_name: 'Foundation', title: 'Parity roadmap', summary: 'Close migration gaps', planning_status: 'now', links: [] } },
    };
    const { sendCommand } = renderShell(browserHost, frame);
    fireEvent.click(screen.getByRole('button', { name: /Planning/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Parity roadmap/ }));
    const dialog = screen.getByRole('dialog', { name: 'Initiative' });
    expect(dialog).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', { name: 'Why this matters' }), { target: { value: 'Retire the classic UI safely' } });
    fireEvent.change(within(dialog).getByRole('combobox', { name: 'Linked record' }), { target: { value: 'task-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Link' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'initiative_link_task', id: 'initiative-1', task_id: 'task-1' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'initiative_update', id: 'initiative-1', why: 'Retire the classic UI safely' }));
  });

  it('keeps Planning thinking records open when a structured field contains invalid JSON', () => {
    const send = vi.fn();
    const onClose = vi.fn();
    render(<ThinkingEditor kind="note" item={{ id: 'note-1', title: 'Audit note', body: 'Body', context: {}, links: [] }} send={send} onClose={onClose} />);

    fireEvent.change(screen.getByRole('textbox', { name: 'context' }), { target: { value: '{broken' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Context and link fields must contain valid JSON.');
    expect(send).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('publishes and pins shared memory from the Control Center Context panel', async () => {
    const { appStore, sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Context' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'memory_list', group_name: 'Foundation' }));
    act(() => {
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'memory_entries', group_name: 'Foundation', entries: [{ id: 'memory-1', title: 'Release constraint', content: 'Never deploy from a worker.', entry_type: 'constraint', scope_kind: 'group', scope_ref: 'Foundation', pinned: false }],
      }));
    });
    expect(screen.getAllByText('Never deploy from a worker.')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Pin' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'memory_pin', entry_id: 'memory-1' });
    fireEvent.click(screen.getByRole('button', { name: '＋ Add context' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Content' }), { target: { value: 'Keep the migration reversible.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Publish context' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'memory_publish', content: 'Keep the migration reversible.', scope_kind: 'group', scope_ref: 'Foundation' }));
    expect(sendCommand).toHaveBeenLastCalledWith(expect.objectContaining({ cmd: 'memory_list', group_name: 'Foundation' }));
  });

  it('restores the original role-specific Architect activity panel', () => {
    const { appStore, sendCommand } = renderShell(browserHost, {
      ...compactStateFixture,
      agents: { architect: { id: 'architect', name: 'Aria', group: 'Foundation', kind: 'architect', status: 'idle' } },
    });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    const viewTabs = screen.getByRole('tablist', { name: 'View for Aria' });
    fireEvent.click(within(viewTabs).getByRole('tab', { name: 'Activity' }));
    const panel = screen.getByRole('region', { name: 'Activity for Aria' });
    expect(within(panel).getByRole('tab', { name: 'Decisions' })).toBeVisible();
    expect(within(panel).getByRole('tab', { name: 'Journal' })).toBeVisible();
    expect(within(panel).getByRole('tab', { name: 'Messages' })).toBeVisible();
    expect(within(panel).getByRole('tab', { name: 'Events' })).toBeVisible();
    expect(within(panel).getByRole('tab', { name: 'MCP' })).toBeVisible();
    expect(within(panel).getByRole('tab', { name: 'History' })).toBeVisible();
    expect(within(panel).getByRole('tab', { name: 'Agent Class' })).toBeVisible();
    expect(within(panel).getByRole('tab', { name: 'Peer chat' })).toBeVisible();
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'decisions_snapshot', include_archived: true });
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'decisions_snapshot', decisions: { 'decision-1': { id: 'decision-1', architect_id: 'architect', title: 'Keep Tauri', rationale: 'Electron remains an option.', status: 'accepted' } } })); });
    expect(within(panel).getByText('Keep Tauri')).toBeVisible();
    expect(within(panel).getByText('Electron remains an option.')).not.toBeVisible();
    fireEvent.click(within(panel).getByText('Keep Tauri'));
    expect(within(panel).getByText('Electron remains an option.')).toBeVisible();
    fireEvent.click(within(panel).getByRole('tab', { name: 'Journal' }));
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'architect_journal_entries', architect_id: 'architect', entries: [{ id: 'journal-1', type: 'checkpoint', entry: 'Reviewed the migration plan.\nThe second line stays collapsed.', timestamp: 1_000 }] })); });
    expect(within(panel).getByText('Reviewed the migration plan.')).toBeVisible();
    expect(within(panel).getByText(/The second line stays collapsed/)).not.toBeVisible();
    fireEvent.click(within(panel).getByText('checkpoint'));
    expect(within(panel).getByText(/The second line stays collapsed/)).toBeVisible();
  });

  it('keeps peer chat read-only and puts Architect digest delivery in Events', () => {
    const { sendCommand } = renderShell(browserHost, {
      ...compactStateFixture,
      agents: { architect: { id: 'architect', name: 'Aria', group: 'Foundation', kind: 'architect', status: 'idle' } },
      agent_digest_settings: { architect: { paused: false } },
      digest_buffer_stats: { architect: { buffered_events: 1, queued_events: [{ id: 'queued-1', kind: 'task_progress', message: 'Queued digest headline\nQueued detail', timestamp: 1_000 }] } },
      digest_sent_events: { architect: [{ id: 'sent-1', kind: 'task_complete', message: 'Sent digest headline\nSent detail', delivered_at: 900 }] },
      agent_peer_threads: {
        first: { thread_id: 'first', title: 'Migration coordination', participant_ids: ['architect', 'peer-a'], message_count: 1, last_activity_at: 1_000, messages: [{ id: 'peer-message-1', sender_architect_id: 'peer-a', sender_name: 'Ada', message: 'Keep the Tauri boundary clean.', created_at: 1_000 }] },
        second: { thread_id: 'second', title: 'Release planning', participant_ids: ['architect', 'peer-b'], message_count: 1, last_activity_at: 900, messages: [{ id: 'peer-message-2', sender_architect_id: 'peer-b', sender_name: 'Ben', message: 'Sequence the release after verification.', created_at: 900 }] },
      },
    });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(within(screen.getByRole('tablist', { name: 'View for Aria' })).getByRole('tab', { name: 'Activity' }));
    const panel = screen.getByRole('region', { name: 'Activity for Aria' });

    fireEvent.click(within(panel).getByRole('tab', { name: 'Events' }));
    expect(within(panel).getByRole('heading', { name: 'Architect digest' })).toBeVisible();
    expect(within(panel).getByText('Queued digest headline')).toBeVisible();
    expect(within(panel).getByText('Sent digest headline')).toBeVisible();
    fireEvent.click(within(panel).getByRole('button', { name: 'Send digest now' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'engineer_flush_now', agent_id: 'architect' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Pause' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'digest_pause', agent_id: 'architect' });
    fireEvent.click(within(panel).getByRole('tab', { name: 'Journal' }));
    expect(within(panel).queryByRole('heading', { name: 'Architect digest' })).not.toBeInTheDocument();

    fireEvent.click(within(panel).getByRole('tab', { name: 'Peer chat' }));
    expect(within(panel).queryByRole('heading', { name: 'Message peer' })).not.toBeInTheDocument();
    expect(within(panel).queryByRole('textbox')).not.toBeInTheDocument();
    expect(within(panel).getAllByText('Keep the Tauri boundary clean.')).toHaveLength(2);
    fireEvent.click(within(panel).getByRole('button', { name: /Release planning/ }));
    expect(within(panel).getByRole('heading', { name: 'Release planning' })).toBeVisible();
    expect(within(panel).getAllByText('Sequence the release after verification.')).toHaveLength(2);
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'architect_peer_inbox', architect_id: 'architect', detail: true, limit: 100 });
  });

  it('moves the Engineer digest from Journal to Events with group-data fallback', () => {
    renderShell(browserHost, {
      ...compactStateFixture,
      agents: { engineer: { id: 'engineer', name: 'Evan', group: 'Foundation', kind: 'engineer', status: 'idle' } },
      agent_digest_settings: { engineer: { paused: false } },
      engineer_buffer_stats: { Foundation: { buffered_events: 1, queued_events: [{ id: 'queued-1', kind: 'task_progress', message: 'Engineer digest event', timestamp: 1_000 }] } },
      engineer_sent_events: { Foundation: [{ id: 'sent-1', kind: 'task_complete', message: 'Engineer digest delivered', delivered_at: 900 }] },
    });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(within(screen.getByRole('tablist', { name: 'View for Evan' })).getByRole('tab', { name: 'Activity' }));
    const panel = screen.getByRole('region', { name: 'Activity for Evan' });

    expect(within(panel).queryByRole('heading', { name: 'Engineer digest' })).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole('tab', { name: 'Events' }));
    expect(within(panel).getByRole('heading', { name: 'Engineer digest' })).toBeVisible();
    expect(within(panel).getAllByText('Engineer digest event')[0]).toBeVisible();
    expect(within(panel).getAllByText('Engineer digest delivered')[0]).toBeVisible();
  });

  it('loads Activity pages at the scroll tail and manages active and archived decisions', () => {
    let intersectionCallback: IntersectionObserverCallback | undefined;
    vi.stubGlobal('IntersectionObserver', class {
      root = null;
      rootMargin = '';
      thresholds = [];
      constructor(callback: IntersectionObserverCallback) { intersectionCallback = callback; }
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() { return []; }
    });
    const { appStore, sendCommand } = renderShell(browserHost, {
      ...compactStateFixture,
      agents: { architect: { id: 'architect', name: 'Aria', group: 'Foundation', kind: 'architect', status: 'idle' } },
    });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(within(screen.getByRole('tablist', { name: 'View for Aria' })).getByRole('tab', { name: 'Activity' }));
    const panel = screen.getByRole('region', { name: 'Activity for Aria' });
    const decisions: Record<string, Record<string, unknown>> = Object.fromEntries(Array.from({ length: 45 }, (_, index) => {
      const id = `decision-${index + 1}`;
      return [id, { id, architect_id: 'architect', title: `Decision ${index + 1}`, rationale: `Rationale ${index + 1}`, status: 'proposed', updated_at: 100 - index }];
    }));
    decisions.archived = { id: 'archived', architect_id: 'architect', title: 'Archived direction', rationale: 'Historical rationale', status: 'accepted', archived: true, updated_at: 1_000 };
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'decisions_snapshot', decisions })); });

    expect(within(panel).getByText('Decision 1')).toBeVisible();
    expect(within(panel).queryByText('Decision 21')).not.toBeInTheDocument();
    act(() => intersectionCallback?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(within(panel).getByText('Decision 21')).toBeVisible();
    expect(within(panel).getByText('Rationale 1')).not.toBeVisible();
    const decisionOne = within(panel).getByText('Decision 1').closest('details');
    expect(decisionOne).not.toBeNull();
    fireEvent.click(within(panel).getByText('Decision 1'));
    expect(within(panel).getByText('Rationale 1')).toBeVisible();
    fireEvent.click(within(decisionOne as HTMLElement).getByRole('button', { name: 'Accept' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'architect_decision_update', architect_id: 'architect', id: 'decision-1', status: 'accepted' });
    fireEvent.click(within(decisionOne as HTMLElement).getByRole('button', { name: 'Archive' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'architect_decision_update', architect_id: 'architect', id: 'decision-1', archived: true });

    expect(within(panel).queryByText('Archived direction')).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole('button', { name: 'Show archived (1)' }));
    const archivedDecision = within(panel).getByText('Archived direction').closest('details');
    expect(archivedDecision).not.toBeNull();
    fireEvent.click(within(panel).getByText('Archived direction'));
    expect(within(panel).getByText('Historical rationale')).toBeVisible();
    fireEvent.click(within(archivedDecision as HTMLElement).getByRole('button', { name: 'Restore' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'architect_decision_update', architect_id: 'architect', id: 'archived', archived: false });

    fireEvent.click(within(panel).getByRole('tab', { name: 'Journal' }));
    const entries = Array.from({ length: 20 }, (_, index) => ({ id: `journal-${index}`, type: 'observation', entry: `Entry ${index}`, timestamp: 100 - index }));
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'architect_journal_entries', architect_id: 'architect', entries })); });
    act(() => intersectionCallback?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'architect_journal_read', architect_id: 'architect', limit: 40 });
    vi.unstubAllGlobals();
  });

  it('renders and collapses the Architect to Engineer to Worker ownership tree', () => {
    renderShell(browserHost, {
      ...compactStateFixture,
      agents: {
        architect: { id: 'architect', name: 'Aria', group: 'Foundation', kind: 'architect', status: 'idle' },
        engineer: { id: 'engineer', name: 'Evan', group: 'Foundation', kind: 'engineer', status: 'running', hired_by_architect_id: 'architect' },
        worker: { id: 'worker', name: 'Wren', group: 'Foundation', kind: 'worker', status: 'running', owner_engineer_id: 'engineer' },
      },
    });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));

    expect(screen.getByRole('treeitem', { name: 'Aria, architect, idle' })).toHaveAttribute('aria-level', '1');
    expect(screen.getByRole('treeitem', { name: 'Evan, engineer, running' })).toHaveAttribute('aria-level', '2');
    expect(screen.getByRole('treeitem', { name: 'Wren, worker, running' })).toHaveAttribute('aria-level', '3');
    fireEvent.click(screen.getByRole('button', { name: 'Collapse Evan' }));
    expect(screen.queryByRole('treeitem', { name: 'Wren, worker, running' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Expand Evan' }));
    expect(screen.getByRole('treeitem', { name: 'Wren, worker, running' })).toBeVisible();
  });

  it('authors complete role definitions through the React catalog', async () => {
    const frame: StateFrame = { ...compactStateFixture, roles: [{ name: 'reviewer', description: 'Review changes', scope: 'project' }] };
    const { sendCommand } = renderShell(browserHost, frame);
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Catalog' }));
    const roleSection = screen.getByRole('heading', { name: 'Roles' }).closest('section');
    expect(roleSection).not.toBeNull();
    fireEvent.click(within(roleSection as HTMLElement).getByRole('button', { name: /reviewer/ }));
    fireEvent.change(within(roleSection as HTMLElement).getByLabelText('Definition'), { target: { value: '{"description":"Review UI","preamble":"Be exact","priorities":["correctness"]}' } });
    fireEvent.click(within(roleSection as HTMLElement).getByRole('button', { name: 'Save' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'save_role', group: 'Foundation', name: 'reviewer', data: { description: 'Review UI', preamble: 'Be exact', priorities: ['correctness'] } }));
  });

  it('deduplicates overlapping project and built-in actions in Control Center', async () => {
    const { appStore } = renderShell();
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({
      type: 'actions',
      actions: [{ name: 'feature/implement' }, { name: 'feature/implement' }, { name: 'oneshot/fix' }],
    })); });
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Actions' }));
    expect(screen.getAllByRole('button', { name: 'feature/implement' })).toHaveLength(1);
  });

  it('reports invalid action JSON instead of silently ignoring Save', async () => {
    const { sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Actions' }));
    fireEvent.click(screen.getByRole('button', { name: '＋' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'ui/audit' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Transitions (JSON)' }), { target: { value: '{' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save action' }));
    expect(screen.getByRole('alert')).toHaveTextContent('must be valid JSON arrays');
    expect(sendCommand).not.toHaveBeenCalledWith(expect.objectContaining({ cmd: 'save_action' }));
  });

  it('loads searchable agent-run history and preserves detail responses by agent', async () => {
    const { appStore, sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'History' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'get_agent_history', status: 'merged', limit: 100 });

    act(() => {
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'agent_history_list',
        records: [{ id: 'old-agent', name: 'Release Worker', group: 'Foundation', kind: 'worker', provider: 'codex', status: 'merged', completed_at: 100 }],
      }));
    });
    fireEvent.click(screen.getByRole('button', { name: /Release Worker/ }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'get_agent_history_detail', agent_id: 'old-agent', message_limit: 100 });

    act(() => {
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'agent_history_detail',
        record: { id: 'old-agent', name: 'Release Worker', status: 'merged', model: 'gpt-5.6-sol', outcome: 'done' },
        tasks: [{ task_id: 'release-task', task: 'Cut release', lane: 'Done' }],
        messages: [{ id: 'release-message', action: 'done', message: 'Release verified' }],
      }));
    });
    expect(screen.getByText('Cut release')).toBeVisible();
    expect(screen.getByText('Release verified')).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search history' }), { target: { value: 'no match' } });
    expect(screen.getByText('No historical runs')).toBeVisible();
  });

  it('authors and validates project Agent Classes from the React catalog', async () => {
    const { appStore, sendCommand } = renderShell();
    act(() => {
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({
        type: 'agent_classes',
        classes: [{ id: 'default-worker', display_name: 'Default Worker', base_kind: 'worker', version: '1', builtin: true, source: 'builtin', acl: { mode: 'allow', rules: [] } }],
        issues: [],
        authoring_contract: { schema_version: 5, scope_vocabulary: ['self', 'children', 'group', 'global'] },
        capability_catalog: [{ id: 'self.read', label: 'Read own context', description: 'Read caller identity and own context.', risk: 'normal', scoped: true, scopes: ['self'], base_kinds: ['worker', 'engineer', 'architect'], maximum_scopes: { worker: 'self' } }],
      }));
    });
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Catalog' }));
    expect(screen.getByText('Built-in classes cannot be edited. Duplicate this definition into the project to customize it.')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: '＋ New' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'ID' }), { target: { value: 'release-worker' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Display name' }), { target: { value: 'Release Worker' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Class job prompt' }), { target: { value: 'Prepare and verify releases.' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Read own context/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Validate' }));
    expect(sendCommand.mock.calls.some(([command]) => command.cmd === 'agent_class_validate'
      && (command.agent_class as Record<string, unknown>)?.id === 'release-worker'
      && (command.agent_class as Record<string, unknown>)?.base_kind === 'worker')).toBe(true);
    const classLibrary = screen.getByRole('heading', { name: 'Agent Classes' }).closest('section');
    expect(classLibrary).not.toBeNull();
    fireEvent.click(within(classLibrary as HTMLElement).getByRole('button', { name: 'Save' }));
    const createClassCommand = sendCommand.mock.calls.map(([command]) => command).find((command) => command.cmd === 'agent_class_create');
    const createdClass = createClassCommand?.agent_class as Record<string, unknown>;
    expect(createdClass.id).toBe('release-worker');
    expect(createdClass.display_name).toBe('Release Worker');
    expect(createdClass.acl).toEqual({ mode: 'allow', rules: [{ capability: 'self.read', scope: 'self' }] });
  });

  it('coordinates dirty global, group, and AI settings saves', async () => {
    const { sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));

    const scrollback = screen.getByRole('spinbutton', { name: 'Terminal scrollback' });
    fireEvent.change(scrollback, { target: { value: '9000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(sendCommand.mock.calls.some(([command]) => command.cmd === 'update_global_settings'
      && typeof command.settings === 'object'
      && command.settings !== null
      && (command.settings as Record<string, unknown>).xterm_scrollback === 9000)).toBe(true);
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'update_group_settings', group: 'Foundation' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'update_ai_settings' }));
  });

  it('loads the durable Inbox and exposes the complete notice lifecycle', () => {
    const frame: StateFrame = {
      ...compactStateFixture,
      operator_notices: { alert: { id: 'alert', notice_type: 'alert', title: 'Worker blocked', message: 'A human decision is required.', severity: 'warning', created_at: 100, task_id: 'task-1', action_kind: 'open_task' } },
      operator_notice_summary: { unread_total: 1, open_alerts: 1, unread_notifications: 0 },
    };
    const { sendCommand } = renderShell(browserHost, frame);
    fireEvent.click(screen.getByRole('button', { name: /Inbox, 1 unread/ }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'operator_notices_list', notice_type: 'alert', include_archived: false, limit: 100, offset: 0 });
    expect(screen.getByText('A human decision is required.')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Resolve' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'operator_notice_resolve', id: 'alert' });
    fireEvent.click(screen.getByRole('button', { name: 'Archive' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'operator_notice_archive', id: 'alert' });
  });

  it('operates Mission Control cards, health history, and supervisor sessions', async () => {
    const frame: StateFrame = {
      ...compactStateFixture,
      mission_control_summary: { group: 'Foundation', sections: { needs_operator_now: { items: [{ id: 'gate-1', title: 'Approve release', reason: 'Verification gate', primary_task_id: 'task-1' }] } } },
      system_health_metrics: { counts: { agents: 1, needs_attention: 1 } },
      supervisor_sessions: { runtime_state: 'running', sessions: [{ session_id: 'session-1', agent_name: 'UI Worker', pid: 42, status: 'live' }] },
    };
    const { sendCommand } = renderShell(browserHost, frame);
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    expect(await screen.findByText('Approve release')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'mission_control_dismiss', id: 'gate-1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Terminate' }));
    expect(screen.getByRole('dialog', { name: 'Terminate PTY session?' })).toBeVisible();
    expect(sendCommand).not.toHaveBeenCalledWith({ cmd: 'supervisor_session_terminate', session_id: 'session-1' });
    fireEvent.click(screen.getByRole('button', { name: 'Terminate session' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'supervisor_session_terminate', session_id: 'session-1' });
    fireEvent.click(screen.getByRole('button', { name: 'Restart supervisor' }));
    expect(screen.getByRole('dialog', { name: 'Restart PTY supervisor?' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(sendCommand).not.toHaveBeenCalledWith({ cmd: 'supervisor_restart' });
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'get_metrics_history', group: 'Foundation', window: '24h' });
  });

  it('searches maintained Help and opens a source-backed topic', async () => {
    const { appStore, sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Help' }));
    act(() => {
      appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'help_topics', topics: [{ topic_id: 'operate', title: 'Operating Torque', summary: 'Run and supervise Torque.', source_path: 'docs/operate/index.md' }] }));
    });
    fireEvent.click(screen.getByRole('button', { name: /Operating Torque/ }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'help_show', topic: 'operate', max_chars: 16000 });
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'help_topic', topic_id: 'operate', title: 'Operating Torque', path_anchor: 'docs/operate/index.md', body_excerpt: 'Use Mission Control to supervise by exception.' })); });
    expect(screen.getByText('Use Mission Control to supervise by exception.')).toBeVisible();
  });

  it('bridges native menu actions into React-owned panels and dialogs', async () => {
    renderShell();
    const nativeWindow = window as Window & {
      openGlobalSettings?: () => void;
      openWelcome?: () => void;
      openAddGroup?: () => void;
    };

    act(() => nativeWindow.openGlobalSettings?.());
    expect(await screen.findByRole('heading', { name: 'Workspace settings' })).toBeVisible();
    act(() => nativeWindow.openWelcome?.());
    expect(screen.getByRole('dialog', { name: 'Welcome to Torque' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    act(() => nativeWindow.openAddGroup?.());
    expect(screen.getByRole('dialog', { name: 'New group' })).toBeVisible();
  });

  it('completes the React first-run flow through the durable command', () => {
    const { sendCommand } = renderShell();
    const nativeWindow = window as Window & { openWelcome?: () => void };
    act(() => nativeWindow.openWelcome?.());
    fireEvent.click(screen.getByRole('button', { name: 'Start using Torque' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'first_run_complete' });
  });

  it('reports bounded redacted protocol failures into the durable Inbox', () => {
    const { appStore, sendCommand } = renderShell();
    act(() => {
      appStore.dispatch(connectionActions.protocolError({
        at: 2_000,
        message: 'bad frame token=super-secret',
      }));
    });

    expect(sendCommand).toHaveBeenCalledWith(expect.objectContaining({
      cmd: 'operator_notice_report_client_error',
      source: 'react:protocol:parse',
      message: 'bad frame token=[redacted]',
    }));
  });

  it('bounds and redacts client diagnostics before persistence', () => {
    expect(sanitizeClientError('Authorization: bearer-value password=hunter2')).toBe(
      'Authorization=[redacted] password=[redacted]',
    );
    expect(sanitizeClientError('x'.repeat(900))).toHaveLength(512);
  });
});
