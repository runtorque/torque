import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { browserHost, createTauriHost } from '../host';
import { compactStateFixture } from '../protocol/fixtures';
import type { TorqueCommand } from '../protocol/commands';
import type { StateFrame, UnknownRecord } from '../protocol/types';
import { WorkspaceShell } from './App';
import { sanitizeClientError } from './clientDiagnostics';
import { connectionActions, createAppStore, projectionActions, workspaceUiActions } from './store';

function renderShell(host = browserHost, frame: StateFrame = compactStateFixture) {
  const featureFetch = globalThis.fetch; const detailReads: TorqueCommand[] = [];
  vi.stubGlobal('fetch', (input: RequestInfo | URL, options?: RequestInit) => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand;
    // Task hydration frames are supplied explicitly by the shell integration tests.
    if (command.cmd === 'task_detail') { detailReads.push(command); return new Promise<Response>(() => {}); }
    if (command.cmd === 'ui_set_react_workspace_state') return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'react_workspace_state', state: command.state } }) });
    return featureFetch(input, options);
  });
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
  return { appStore, sendCommand, detailReads };
}

afterEach(() => vi.unstubAllGlobals());

function pendingActivityReads() {
  const commands: TorqueCommand[] = [];
  vi.stubGlobal('fetch', (_input: RequestInfo | URL, options?: RequestInit) => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; commands.push(command);
    if (command.cmd === 'agent_class_list') return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'agent_classes', classes: [{ id: 'default-worker', display_name: 'Default Worker', base_kind: 'worker', version: '1' }] } }) });
    // Tests below provide response frames explicitly, independently of reads.
    return new Promise<never>(() => {});
  });
  return commands;
}

function agentSettingsResponse(frame: StateFrame, id: string) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'agent_settings', agent_id: id, settings: (frame.agent_settings as Record<string, unknown> | undefined)?.[id] ?? {}, resolved: (frame.resolved_agent_settings as Record<string, unknown> | undefined)?.[id] ?? {} } }) });
}


function mockSettingsRequests(failSave = false) {
  const commands: TorqueCommand[] = []; let failedRead = '';
  const snapshots: Record<string, UnknownRecord> = {
    get_global_settings: { type: 'global_settings', defaults: { xterm_scrollback: 5000, event_ingest_max_days: 14, mcp_call_log_args_capture: 'metadata' }, settings: { xterm_scrollback: 5000, event_ingest_max_days: 14, mcp_call_log_args_capture: 'metadata' } },
    get_group_settings: { type: 'group_settings', group: 'Foundation', defaults: { max_agents: 0, shell: '', env_vars: {}, worktree_symlinks: [] }, engineer_defaults: { default_worker_concurrency: 2 }, architect_defaults: { architect_heartbeat_interval: 300 }, settings: { max_agents: 4, shell: '/bin/zsh', env_vars: {}, worktree_symlinks: [], architect_heartbeat_interval: 300, engineer_agent_id: 'owned' }, engineer_settings: { group: 'Foundation', pending_question: 'Keep this question', default_worker_concurrency: 2, digest_verbosity: 'balanced' }, architect_settings: { group: 'Foundation', architect_heartbeat_interval: 300 } },
    get_ai_settings: { type: 'ai_settings', settings: {} },
  };
  const fetcher = vi.fn((_url: string, options?: RequestInit): Promise<{ ok: boolean; json: () => Promise<{ ok: boolean; error?: string; data?: UnknownRecord }> }> => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}' ) as TorqueCommand; commands.push(command);
    if (command.cmd === failedRead || (failSave && command.cmd === 'update_group_settings')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: false, error: command.cmd === failedRead ? 'Refresh refused' : 'Group save refused' }) });
    const update = (scope: string, key: string, patch: unknown) => { const frame = snapshots[scope]!; frame[key] = { ...(frame[key] as UnknownRecord), ...(patch as UnknownRecord) }; };
    if (command.cmd === 'update_global_settings' || command.cmd === 'update_ai_settings') update('get_global_settings', 'settings', command.settings);
    if (command.cmd === 'update_group_settings') update('get_group_settings', 'settings', command.settings);
    if (command.cmd === 'engineer_update_settings') update('get_group_settings', 'engineer_settings', Object.fromEntries(Object.entries(command).filter(([key]) => key !== 'cmd' && key !== 'group')));
    if (command.cmd === 'update_architect_settings') update('get_group_settings', 'architect_settings', command.settings);
    if (command.cmd === 'board_update_task' || command.cmd === 'remove_attachment') { const id = String(command.id ?? command.task_id); return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'state', seq: 10, board_tasks: { [id]: { id } } } }) }); }
    const frame = structuredClone(snapshots[String(command.cmd)] ?? (command.cmd === 'update_ai_settings' ? snapshots.get_ai_settings! : { type: 'ok' }));
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: frame }) });
  });
  vi.stubGlobal('fetch', fetcher); return { commands, fetcher, setFailure: (value: boolean) => { failSave = value; }, failRead: (command: string) => { failedRead = command; }, refresh: (command: string, patch: UnknownRecord) => { snapshots[command] = { ...snapshots[command], ...patch }; } };
}

describe('workspace shell', () => {
  it('keeps generated device-link secrets out of shared state and does not restore cached links on reopen', async () => {
    const { refresh } = mockSettingsRequests();
    refresh('get_global_settings', { relay_config: { config: { enabled: true, relay_url: 'https://relay.invalid' } } });
    const settingsFetch = globalThis.fetch; let minted = 0;
    vi.stubGlobal('fetch', (input: RequestInfo | URL, options?: RequestInit) => {
      const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand;
      if (command.cmd === 'generate_relay_device_link') { minted += 1; return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'relay_device_link', ok: true, code: 'owned-secret-code', establish_url: 'https://relay.invalid/establish?code=owned-secret-code' } }) }); }
      return settingsFetch(input, options);
    });
    const { appStore } = renderShell();
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'relay_device_link', ok: true, code: 'obsolete-cache-code', establish_url: 'https://relay.invalid/obsolete-cache-code' })); });
    fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    const generate = await screen.findByRole('button', { name: 'Generate one-time device link' }); expect(screen.queryByText('obsolete-cache-code')).not.toBeInTheDocument();
    fireEvent.click(generate); expect(minted).toBe(0); fireEvent.click(screen.getByRole('button', { name: 'Confirm and generate device link' }));
    expect(await screen.findByLabelText('Device link code')).toHaveTextContent('owned-secret-code'); expect(JSON.stringify(appStore.getState())).not.toContain('owned-secret-code');
    fireEvent.click(screen.getByRole('button', { name: 'Mission Control' })); fireEvent.click(screen.getByRole('button', { name: 'Settings' })); await screen.findByRole('button', { name: 'Generate one-time device link' });
    expect(screen.queryByLabelText('Device link code')).not.toBeInTheDocument(); expect(screen.queryByText('obsolete-cache-code')).not.toBeInTheDocument(); expect(minted).toBe(1);
  });

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

  it('opens filtered group and panel navigators with either platform modifier', async () => {
    renderShell();
    fireEvent.keyDown(window, { key: 'g', metaKey: true });
    const search = await screen.findByRole('combobox', { name: 'Search groups' });
    expect(screen.getByRole('option', { name: 'Open group: Foundation' })).toBeVisible();
    expect(screen.queryByRole('option', { name: 'New Board task' })).not.toBeInTheDocument();
    fireEvent.keyDown(search, { key: 'p', ctrlKey: true });
    const panels = await screen.findByRole('combobox', { name: 'Search panels' });
    expect(screen.queryByRole('option', { name: 'Open group: Foundation' })).not.toBeInTheDocument();
    fireEvent.change(panels, { target: { value: 'Logs' } }); fireEvent.keyDown(panels, { key: 'Enter' });
    expect(await screen.findByRole('heading', { name: 'Torque logs' })).toBeVisible();
  });
  it('keeps the Classic Board shortcut and its override working in fixed workspaces', async () => {
    const { appStore } = renderShell(browserHost, { ...compactStateFixture, global_settings: { keybindings: { 'panel.toggle': { key: 'j', alt: true } } } });
    act(() => { appStore.dispatch(workspaceUiActions.setActivePanel('agents')); });
    fireEvent.keyDown(window, { key: 'k' }); expect(appStore.getState().workspaceUi.activePanel).toBe('agents');
    fireEvent.keyDown(window, { key: 'j', altKey: true }); expect(appStore.getState().workspaceUi.activePanel).toBe('board');
    fireEvent.keyDown(window, { key: 'g', metaKey: true });
    const groups = await screen.findByRole('combobox', { name: 'Search groups' });
    fireEvent.keyDown(groups, { key: 'k', metaKey: true });
    expect(await screen.findByRole('combobox', { name: 'Search commands' })).toBeVisible();
    expect(screen.getByRole('option', { name: 'Open Board' })).toHaveTextContent('B / ⌥J');
  });

  it('reveals Live and focuses the composer with its configured shortcut', async () => {
    const { appStore } = renderShell(browserHost, { ...compactStateFixture, global_settings: { keybindings: { 'composer.focus': { key: 'j', alt: true } } } });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(screen.getByRole('tab', { name: 'Activity' }));
    fireEvent.click(screen.getByRole('button', { name: /▦ Board/ }));
    fireEvent.keyDown(window, { key: 'c' }); expect(appStore.getState().workspaceUi.activePanel).toBe('board');
    fireEvent.keyDown(window, { key: 'j', altKey: true });
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Message Foundation Worker' })).toHaveFocus());
    expect(screen.getByRole('tab', { name: 'Live' })).toHaveAttribute('aria-selected', 'true');
  });

  it('uses the configured create-task key even when a Board card owns focus', () => {
    const { appStore } = renderShell(browserHost, { ...compactStateFixture, global_settings: { keybindings: { 'task.create': { key: 't', ctrl: false, meta: false, alt: false, shift: false } } } });
    const card = screen.getByText('Build the foundation').closest('article')!;
    fireEvent.keyDown(card, { key: 'n' }); expect(appStore.getState().workspaceUi.createTaskDialogOpen).toBe(false);
    fireEvent.keyDown(card, { key: 't' }); expect(appStore.getState().workspaceUi.createTaskDialogOpen).toBe(true);
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

  it('opens the full task dialog from the global Board command', async () => {
    const commands: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      commands.push(JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'board_task_added', task_id: 'created' } }) });
    }));
    const { appStore } = renderShell();
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
    fireEvent.click(screen.getByText('Advanced variables'));
    fireEvent.change(screen.getByRole('textbox', { name: 'Action variables (JSON)' }), { target: { value: '[]' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));
    await screen.findByText('Action variables must be a JSON object.');
    fireEvent.change(screen.getByRole('textbox', { name: 'Action variables (JSON)' }), { target: { value: '{}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }));

    await waitFor(() => expect(commands).toHaveLength(1));
    expect(commands[0]).toMatchObject({
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

  it('keeps browser workspaces available when the shared profile has native detached windows', () => {
    renderShell(browserHost, { ...compactStateFixture, detached_panels: { agents: { label: 'agents-native' } } });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(screen.getByRole('tree', { name: 'Agent ownership hierarchy' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Reattach workspace' })).not.toBeInTheDocument();
  });

  it('waits for the current connection snapshot before reconciling native windows', async () => {
    const store = createAppStore(); const send = vi.fn(() => true);
    const invoke = vi.fn((command: string) => Promise.resolve(command === 'list_detached' ? [] : null));
    store.dispatch(connectionActions.connected({ at: 2000, reconnect: false }));
    render(<Provider store={store}><WorkspaceShell host={createTauriHost(invoke)} sendCommand={send} /></Provider>);
    expect(invoke).not.toHaveBeenCalledWith('list_detached', undefined);
    const frame = { ...compactStateFixture, detached_panels: { agents: { label: 'stale', bounds: { width: 900, height: 640 } } } };
    act(() => { store.dispatch(projectionActions.snapshotReceived(frame)); store.dispatch(connectionActions.snapshotAccepted(frame)); });
    await waitFor(() => expect(send).toHaveBeenCalledWith({ cmd: 'ui_set_detached_panels', detached_panels: { agents: { label: '', bounds: { width: 900, height: 640 } } } }));
  });

  it('reclaims stale native ownership after restart and retains bounds for the next detach', async () => {
    const bounds = { x: 100, y: 200, width: 1952, height: 1308, physical: true };
    const invoke = vi.fn((command: string) => Promise.resolve(command === 'list_detached' ? [] : command === 'detach' ? 'fresh' : null));
    const { sendCommand, appStore } = renderShell(createTauriHost(invoke), { ...compactStateFixture, detached_panels: { agents: { label: 'stale', bounds } } });
    await waitFor(() => expect(sendCommand).toHaveBeenCalledWith({ cmd: 'ui_set_detached_panels', detached_panels: { agents: { label: '', bounds } } }));
    act(() => { appStore.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'ui_update', key: 'detached_panels', value: { agents: { label: '', bounds } } }] })); });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ })); fireEvent.click(screen.getByRole('button', { name: 'Detach Agents workspace' }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('detach', { panel: 'agents', bounds }));
    expect(invoke.mock.calls.filter(([command]) => command === 'list_detached')).toHaveLength(1);
  });

  it('hands off the currently selected Control section and focuses an existing window without resetting it', async () => {
    const invoke = vi.fn((command: string) => Promise.resolve(command === 'list_detached' ? [{ panel: 'control', label: 'control-existing' }] : command === 'detach' ? 'control-new' : null));
    const { appStore } = renderShell(createTauriHost(invoke));
    for (const section of ['context', 'logs', 'help'] as const) {
      act(() => { appStore.dispatch(workspaceUiActions.setActivePanel('control')); appStore.dispatch(workspaceUiActions.setControlTab(section)); });
      act(() => { (window as Window & { detachActivePanel?: () => void }).detachActivePanel!(); });
      await waitFor(() => expect(invoke).toHaveBeenCalledWith('detach', { panel: 'control', section, bounds: { width: 1080, height: 740 } }));
    }
    act(() => { appStore.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'ui_update', key: 'detached_panels', value: { control: { label: 'control-existing' } } }] })); });
    invoke.mockClear();
    act(() => { (window as Window & { detachActivePanel?: () => void }).detachActivePanel!(); });
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('focus_window', { label: 'control-existing' }));
    expect(invoke.mock.calls.some(([command]) => command === 'detach')).toBe(false);
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

  it('edits the complete task contract and exposes external, verification, artifact, and human workflows', async () => {
    const { commands } = mockSettingsRequests();
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
    const { appStore, sendCommand, detailReads } = renderShell(browserHost, frame);
    fireEvent.doubleClick(screen.getByLabelText('Build the foundation, In Progress'));

    expect(detailReads.at(-1)).toEqual({ cmd: 'task_detail', id: 'task-1' });
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
    expect(screen.getByRole('button', { name: 'Resolve ask' })).toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: 'Evidence' }));
    expect(screen.getByRole('heading', { name: 'Attachments and artifacts' })).toBeVisible();

    fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: 'Complete parity coverage' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Execution' }));
    fireEvent.click(screen.getByText('Advanced variables'));
    fireEvent.change(screen.getByRole('textbox', { name: 'Action variables (JSON)' }), { target: { value: '{"scope":"board"}' } });
    const draftDescription = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Description' });
    draftDescription.focus(); draftDescription.setSelectionRange(3, 8);
    act(() => { appStore.dispatch(projectionActions.taskDetailReceived({ type: 'task_detail', id: 'task-1', task: { description: 'Another server detail response', action_vars: { scope: 'server' } } })); });
    expect(screen.getByRole('textbox', { name: 'Description' })).toBe(draftDescription);
    expect(draftDescription).toHaveValue('Complete parity coverage'); expect(draftDescription).toHaveFocus();
    expect(draftDescription.selectionStart).toBe(3); expect(draftDescription.selectionEnd).toBe(8);
    expect(screen.getByRole('textbox', { name: 'Action variables (JSON)' })).toHaveValue('{"scope":"board"}');
    fireEvent.click(screen.getByRole('button', { name: 'Save task' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(commands).toContainEqual({
      cmd: 'board_update_task', id: 'task-1', description: 'Complete parity coverage',
      action_vars: { scope: 'board' }, enforce_dispatch_edit_gate: true,
    });
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
    expect(screen.getByRole('region', { name: 'Buffered input for Audit Terminal' })).toBeVisible();
    expect(screen.getByRole('textbox', { name: 'Message Audit Terminal' })).toBeVisible();
  });

  it('creates a worker after matched backend acknowledgement and selects its returned ID', async () => {
    const commands: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; commands.push(command);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: command.cmd === 'agent_class_list' ? { type: 'agent_classes', group: 'Foundation', classes: [], issues: [] } : command.cmd === 'render_template' ? { type: 'template_rendered', name: '', group: 'Foundation', config: {} } : { id: 'created-worker', name: 'UI Worker', kind: 'worker' } }) });
    }));
    const { sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Create agent or terminal' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'New Worker…' }));
    expect(screen.getByRole('dialog', { name: 'New worker' })).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'UI Worker' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Provider' }), { target: { value: 'codex' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create worker' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Create worker' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New worker' })).not.toBeInTheDocument());
    expect(commands).toContainEqual(expect.objectContaining({ cmd: 'add_worker', name: 'UI Worker', group: 'Foundation', provider: 'codex', worktree: false, idempotency_key: expect.any(String) as unknown }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'ui_select_agent', id: 'created-worker' });
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

  it.each(['Inspect diff', 'Preflight merge'])('opens %s with correlated diff, preflight and history', async (opener) => {
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
    const reads: TorqueCommand[] = []; const pending: (() => void)[] = [];
    const frames: Record<string, UnknownRecord> = {
      worktree_diff_full: { type: 'worktree_diff_full', id: 'agent-1', branch: 'torque/ui-worker', base_branch: 'main', stats: { insertions: 2, deletions: 1 }, files: [{ path: 'ui.tsx', status: 'modified', insertions: 2, deletions: 1, hunks: [{ header: '@@ -1 +1 @@', lines: [{ type: 'add', text: 'new UI' }] }] }] },
      worktree_check_merge: { type: 'worktree_check_merge', id: 'agent-1', clean: true, default_message: 'Ship UI', conflicts: [] },
      worktree_history: { type: 'worktree_history', id: 'agent-1', commits: [{ sha: 'abcdef123456', short_sha: 'abcdef1', message: 'Checkpoint', date: 'now' }] },
    };
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; reads.push(command);
      return new Promise((resolve) => pending.push(() => resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: frames[command.cmd] }) })));
    }));
    renderShell(browserHost, frame);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(screen.getByRole('button', { name: opener }));
    expect(reads).toEqual(['worktree_diff_full', 'worktree_check_merge', 'worktree_history'].map((cmd) => ({ cmd, id: 'agent-1' })));
    await act(async () => { pending.reverse().forEach((reply) => reply()); await Promise.resolve(); });
    expect(screen.getByText('ui.tsx')).toBeVisible();
    expect(screen.getByText('Clean merge')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Create PR & merge' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /History 1/ }));
    expect(screen.getByText('abcdef1 · now · +0 −0')).toBeVisible();
  });

  it('shows agent events, MCP calls, persisted history, and Agent Class state inside Activity', async () => {
    const activityCommands = pendingActivityReads();
    const { appStore } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(within(screen.getByRole('tablist', { name: 'View for Foundation Worker' })).getByRole('tab', { name: 'Activity' }));

    expect(activityCommands).toEqual([{ cmd: 'get_cell_events', cell_id: 'agent-1', limit: 20 }]);

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
    expect(activityCommands.at(-1)).toMatchObject({ cmd: 'mcp_calls', cell_id: 'agent-1' });
    expect(within(activity).getByText('mcp__torque__task_progress')).toBeVisible();
    fireEvent.click(within(activity).getByRole('tab', { name: 'History' }));
    expect(activityCommands.at(-1)).toEqual({ cmd: 'get_agent_history_detail', agent_id: 'agent-1', message_limit: 20 });
    const retainedMessages = within(activity).getAllByText('History retained');
    expect(retainedMessages[0]).toBeVisible();
    expect(retainedMessages[1]).not.toBeVisible();
    fireEvent.click(within(activity).getByText('progress'));
    expect(retainedMessages[1]).toBeVisible();
    fireEvent.click(within(activity).getByRole('tab', { name: 'Agent Class' }));
    expect(activityCommands.slice(-3).map((command) => command.cmd)).toEqual(['agent_class_list', 'agent_class_status', 'agent_class_audit']);
    await waitFor(() => expect(within(activity).getAllByText('Default Worker')).toHaveLength(2));
    expect(within(activity).getByText('Desired class saved')).not.toBeVisible();
    fireEvent.click(within(activity).getByText('assignment set'));
    expect(within(activity).getByText('Desired class saved')).toBeVisible();
    expect(screen.queryByRole('dialog', { name: /Inspect Foundation Worker/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Inspect activity/ })).not.toBeInTheDocument();
  });

  it('shows server-resolved per-agent setting origins without writing an unchanged form', async () => {
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
    vi.stubGlobal('fetch', vi.fn(() => agentSettingsResponse(principalFrame, 'architect')));
    const { sendCommand } = renderShell(browserHost, principalFrame);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    await waitFor(() => expect(screen.queryByText('Refreshing agent settings…')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled();
    expect(screen.getAllByText('group')).toHaveLength(2);
    expect(screen.getAllByText('default')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(sendCommand).not.toHaveBeenCalledWith(expect.objectContaining({ cmd: 'update_agent_settings' }));
  });

  it('retains per-agent overrides on failed save and explicitly restores inheritance', async () => {
    const frame: StateFrame = { ...compactStateFixture, agents: { architect: { id: 'architect', name: 'Principal', group: 'Foundation', kind: 'architect', status: 'idle' } }, resolved_agent_settings: { architect: { model: { value: 'override', origin: 'per-agent', inherited: { value: 'group-model', origin: 'group' } }, custom_instructions: { value: 'Group instructions', origin: 'group' } } } };
    let fail = true; const commands: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; if (command.cmd === 'get_agent_settings') return agentSettingsResponse(frame, String(command.agent_id)); commands.push(command); return Promise.resolve({ ok: true, json: () => Promise.resolve(fail ? { ok: false, error: 'Save refused' } : { ok: true, data: { type: 'agent_settings', agent_id: 'architect', settings: command.settings, resolved: { model: { value: 'group-model', origin: 'group' }, custom_instructions: { value: '', origin: 'default' } } } }) }); }));
    const { appStore } = renderShell(browserHost, frame);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ })); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await waitFor(() => expect(screen.queryByText('Refreshing agent settings…')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Use inherited' }));
    const model = screen.getByRole('textbox', { name: /^Model/ }); expect(model).toHaveValue('group-model');
    const instructions = screen.getByRole('textbox', { name: /^Custom instructions/ }); fireEvent.change(instructions, { target: { value: '' } });
    act(() => { appStore.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'agent_settings_update', agent_id: 'architect', resolved: { provider: { value: 'new-provider', origin: 'group' } } }] })); });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Save refused'); expect(model).toHaveValue('group-model');
    expect(commands[0]).toEqual({ cmd: 'update_agent_settings', agent_id: 'architect', settings: { model: null, custom_instructions: '' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled());
    fail = false; fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Agent settings' })).not.toBeInTheDocument());
  });

  it('rejects whitespace names before writes and retains refused Engineer renames', async () => {
    const frame: StateFrame = { ...compactStateFixture, agents: { engineer: { id: 'engineer', name: 'Original', group: 'Foundation', kind: 'engineer', status: 'idle' } } };
    const commands: TorqueCommand[] = []; let refuse = true;
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; if (command.cmd === 'get_agent_settings') return agentSettingsResponse(frame, String(command.agent_id)); commands.push(command);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(refuse ? { ok: false, error: "Engineer 'Taken' already exists" } : { ok: true, data: command.cmd === 'rename_engineer' ? { id: 'engineer', kind: 'engineer', name: command.new_name } : command.cmd === 'update_agent_settings' ? { type: 'agent_settings', agent_id: 'engineer', settings: command.settings, resolved: { model: { value: 'new-model', origin: 'per-agent' } } } : { type: 'ok' } }) });
    }));
    renderShell(browserHost, frame); fireEvent.click(screen.getByRole('button', { name: /Agents/ })); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await waitFor(() => expect(screen.queryByText('Refreshing agent settings…')).not.toBeInTheDocument());
    const name = screen.getByRole('textbox', { name: 'Name' });
    fireEvent.change(name, { target: { value: '   ' } }); fireEvent.change(screen.getByLabelText('Icon'), { target: { value: 'gear' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Name is required.'); expect(name).toHaveFocus(); expect(commands).toHaveLength(0);
    fireEvent.change(name, { target: { value: 'Taken' } }); fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("Engineer 'Taken' already exists"); await waitFor(() => expect(screen.getByRole('alert')).toHaveFocus()); expect(name).toHaveValue('Taken'); expect(screen.getByLabelText('Icon')).toHaveValue('gear');
    expect(commands).toEqual([{ cmd: 'rename_engineer', id: 'engineer', new_name: 'Taken' }]);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled());
    refuse = false; fireEvent.change(name, { target: { value: '  Available  ' } }); fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Agent settings' })).not.toBeInTheDocument());
    expect(commands.slice(1)).toEqual([{ cmd: 'rename_engineer', id: 'engineer', new_name: 'Available' }, { cmd: 'update_agent', id: 'engineer', icon: 'gear' }]);
  });

  it('waits for a matching Engineer rename acknowledgement before closing or saving other fields', async () => {
    const frame: StateFrame = { ...compactStateFixture, agents: { engineer: { id: 'engineer', name: 'Original', group: 'Foundation', kind: 'engineer', status: 'idle' } } };
    const commands: TorqueCommand[] = []; let release: (frame: UnknownRecord) => void = () => { throw new Error('Rename not pending'); };
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; if (command.cmd === 'get_agent_settings') return agentSettingsResponse(frame, String(command.agent_id)); commands.push(command);
      return new Promise((resolve) => { release = (data) => resolve({ ok: true, json: () => Promise.resolve({ ok: true, data }) }); });
    }));
    renderShell(browserHost, frame); fireEvent.click(screen.getByRole('button', { name: /Agents/ })); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await waitFor(() => expect(screen.queryByText('Refreshing agent settings…')).not.toBeInTheDocument());
    const name = screen.getByRole('textbox', { name: 'Name' }); fireEvent.change(name, { target: { value: 'Renamed' } }); fireEvent.change(screen.getByLabelText('Icon'), { target: { value: 'gear' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(screen.getByRole('button', { name: 'Saving settings…' })).toBeDisabled(); expect(name).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' })); fireEvent.keyDown(screen.getByRole('dialog', { name: 'Agent settings' }), { key: 'Escape' }); fireEvent.submit(name.closest('form')!);
    expect(screen.getByRole('dialog', { name: 'Agent settings' })).toBeInTheDocument(); expect(commands).toHaveLength(1);
    await act(async () => { release({ id: 'different-engineer', kind: 'engineer', name: 'Renamed' }); await Promise.resolve(); });
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not confirm the Engineer rename'); expect(name).toHaveValue('Renamed'); expect(name).not.toBeDisabled(); expect(commands).toHaveLength(1);
  });

  it('retries unfinished settings without replaying acknowledged identity edits and permits an explicit rename back', async () => {
    const frame: StateFrame = { ...compactStateFixture, agents: { engineer: { id: 'engineer', name: 'Original', group: 'Foundation', kind: 'engineer', status: 'idle' } } };
    const commands: TorqueCommand[] = []; let refuseSettings = true;
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; if (command.cmd === 'get_agent_settings') return agentSettingsResponse(frame, String(command.agent_id)); commands.push(command);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(command.cmd === 'update_agent_settings' && refuseSettings ? { ok: false, error: 'Settings refused' } : { ok: true, data: command.cmd === 'rename_engineer' ? { id: 'engineer', kind: 'engineer', name: command.new_name } : command.cmd === 'update_agent_settings' ? { type: 'agent_settings', agent_id: 'engineer', settings: command.settings, resolved: { model: { value: 'new-model', origin: 'per-agent' } } } : { type: 'ok' } }) });
    }));
    const { appStore } = renderShell(browserHost, frame); fireEvent.click(screen.getByRole('button', { name: /Agents/ })); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await waitFor(() => expect(screen.queryByText('Refreshing agent settings…')).not.toBeInTheDocument());
    const name = screen.getByRole('textbox', { name: 'Name' }); fireEvent.change(name, { target: { value: 'Renamed' } }); fireEvent.change(screen.getByLabelText('Icon'), { target: { value: 'gear' } }); fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'new-model' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })); expect(await screen.findByRole('alert')).toHaveTextContent('Some changes were saved. Settings refused');
    act(() => { appStore.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'agent_upsert', id: 'engineer', name: 'External name', icon: 'external', kind: 'engineer', group: 'Foundation', status: 'idle' }] })); });
    expect(screen.getByRole('heading', { name: 'External name', hidden: true })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' })); await waitFor(() => expect(commands.filter((command) => command.cmd === 'update_agent_settings')).toHaveLength(2));
    expect(await screen.findByRole('alert')).toHaveTextContent('Settings refused'); expect(commands.filter((command) => command.cmd === 'rename_engineer')).toHaveLength(1); expect(commands.filter((command) => command.cmd === 'update_agent')).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save settings' })).toBeEnabled());
    refuseSettings = false; fireEvent.change(name, { target: { value: 'Original' } }); fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Agent settings' })).not.toBeInTheDocument());
    expect(commands.filter((command) => command.cmd === 'rename_engineer')).toEqual([{ cmd: 'rename_engineer', id: 'engineer', new_name: 'Renamed' }, { cmd: 'rename_engineer', id: 'engineer', new_name: 'Original' }]);
    expect(commands.filter((command) => command.cmd === 'update_agent')).toHaveLength(1);
  });

  it('pins an open settings dialog to its original agent when workspace selection changes', async () => {
    const frame: StateFrame = { ...compactStateFixture, agents: { engineer: { id: 'engineer', name: 'Original', group: 'Foundation', kind: 'engineer', status: 'idle' }, other: { id: 'other', name: 'Other', group: 'Foundation', kind: 'engineer', status: 'idle' } } };
    const commands: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; commands.push(command);
      return agentSettingsResponse(frame, 'engineer');
    }));
    const { appStore } = renderShell(browserHost, frame);
    act(() => { appStore.dispatch(workspaceUiActions.setSelectedAgent('engineer')); });
    fireEvent.click(screen.getByRole('button', { name: /Agents/ })); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await waitFor(() => expect(screen.queryByText('Refreshing agent settings…')).not.toBeInTheDocument());
    const model = screen.getByLabelText('Model'); fireEvent.change(model, { target: { value: 'draft' } });
    act(() => { appStore.dispatch(workspaceUiActions.setSelectedAgent('other')); });
    expect(screen.getByLabelText('Name')).toHaveValue('Original'); expect(screen.getByLabelText('Model')).toBe(model); expect(model).toHaveValue('draft');
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Agent settings' })).not.toBeInTheDocument());
    expect(commands.at(-1)).toEqual({ cmd: 'update_agent_settings', agent_id: 'engineer', settings: { model: 'draft' } });
  });

  it('opens Phase 4 Planning, lazy-loads its resources, and creates an initiative', async () => {
    const writes: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
      const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}') as TorqueCommand; writes.push(command);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: command.cmd === 'initiative_list' ? { type: 'initiative_list', group: 'Foundation', initiatives: [] } : { type: 'initiative_created', initiative: { id: 'new-initiative', ...command } } }) });
    }));
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Planning/ }));

    expect(await screen.findByRole('heading', { name: 'Planning' })).toBeVisible();
    await waitFor(() => expect(writes).toEqual([{ cmd: 'initiative_list', group: 'Foundation', include_archived: false }]));

    fireEvent.click(screen.getByRole('button', { name: '＋ New' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), { target: { value: 'Ship Phase 4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(writes).toContainEqual({
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

  it('hydrates tasks opened outside Board cards and refreshes the selected task after reconnect', async () => {
    const { appStore, sendCommand, detailReads } = renderShell();
    act(() => { appStore.dispatch(workspaceUiActions.setDetailTask('task-1')); });
    expect(detailReads.at(-1)).toEqual({ cmd: 'task_detail', id: 'task-1' });
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'list_roles', group: 'Foundation' });
    const reads = () => detailReads.length;
    expect(reads()).toBe(1);
    act(() => { appStore.dispatch(projectionActions.taskDetailReceived({ type: 'task_detail', id: 'task-1', task: { id: 'task-1', task: 'Hydrated linked task', description: 'Full task scope', group: 'Foundation', lane: 'Backlog' } })); });
    const title = await screen.findByRole<HTMLInputElement>('textbox', { name: 'Title' }); expect(title).toHaveValue('Hydrated linked task');
    fireEvent.change(title, { target: { value: 'Retained task draft' } }); title.focus(); title.setSelectionRange(2, 6);
    act(() => { appStore.dispatch(projectionActions.snapshotReceived(compactStateFixture)); appStore.dispatch(connectionActions.connected({ at: 3_000, reconnect: true })); }); expect(reads()).toBe(2);
    act(() => { appStore.dispatch(projectionActions.taskDetailReceived({ type: 'task_detail', id: 'task-1', task: { task: 'Remote title' } })); });
    expect(screen.getByRole('textbox', { name: 'Title' })).toBe(title); expect(title).toHaveValue('Retained task draft'); expect(title).toHaveFocus(); expect([title.selectionStart, title.selectionEnd]).toEqual([2, 6]);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    act(() => { appStore.dispatch(workspaceUiActions.setDetailTask('task-1')); }); expect(reads()).toBe(3);
  });

  it('loads shared memory through the active Control Center Context panel', async () => {
    const fetcher = vi.fn<(url: string, options?: RequestInit) => Promise<{ ok: boolean; json: () => Promise<UnknownRecord> }>>(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'memory_entries', group_name: 'Foundation', entries: [{ id: 'memory-1', title: 'Release constraint', content: 'Never deploy from a worker.', entry_type: 'warning', scope_kind: 'group', scope_ref: 'Foundation', pinned: false }] } }) }));
    vi.stubGlobal('fetch', fetcher); const { appStore } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Context' }));
    expect(await screen.findAllByText('Never deploy from a worker.')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Mission Control' }));
    const memoryReads = () => fetcher.mock.calls.filter(([, options]) => typeof options?.body === 'string' && options.body.includes('memory_list')).length;
    const reads = memoryReads();
    await act(async () => { appStore.dispatch(connectionActions.connected({ at: 3_000, reconnect: true })); await Promise.resolve(); });
    expect(memoryReads()).toBe(reads);
  });

  it('restores the original role-specific Architect activity panel', () => {
    const activityCommands = pendingActivityReads();
    const { appStore } = renderShell(browserHost, {
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
    expect(activityCommands).toEqual([{ cmd: 'decisions_snapshot', include_archived: true }]);
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
    const activityCommands = pendingActivityReads();
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
    expect(activityCommands.at(-1)).toEqual({ cmd: 'architect_peer_inbox', architect_id: 'architect', detail: true, limit: 100 });
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
    const activityCommands = pendingActivityReads();
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
    expect(activityCommands.at(-1)).toEqual({ cmd: 'architect_journal_read', architect_id: 'architect', limit: 40 });
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

  it('authors full scoped role definitions through the typed React catalog', async () => {
    const commands: TorqueCommand[] = []; let definition: UnknownRecord = { name: 'reviewer', description: 'Review changes', model: 'keep-model', system_prompt: 'Keep full prompt' };
    vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
      const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; commands.push(command);
      let data: UnknownRecord = { type: 'ok' };
      if (command.cmd === 'list_roles') data = { type: 'roles', group: 'Foundation', roles: [{ name: 'reviewer', global: false, path: '/project/.torque/roles/reviewer.yaml' }] };
      if (command.cmd === 'get_template') data = { type: 'template_detail', name: 'reviewer', template: definition };
      if (command.cmd === 'save_role') { definition = command.data as UnknownRecord; data = { type: 'roles', group: 'Foundation', saved: 'reviewer', roles: [{ name: 'reviewer', global: false }] }; }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
    }));
    renderShell(); fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Catalog' }));
    const library = within(screen.getByRole('region', { name: 'Roles library' }));
    fireEvent.click(await library.findByRole('button', { name: /reviewer/ }));
    fireEvent.change(await library.findByRole('textbox', { name: 'Description' }), { target: { value: 'Review UI' } });
    fireEvent.change(library.getByRole('textbox', { name: 'Preamble' }), { target: { value: 'Be exact' } });
    fireEvent.change(library.getByRole('textbox', { name: 'Priorities (one per line)' }), { target: { value: 'correctness' } });
    fireEvent.click(library.getByRole('button', { name: 'Save' })); await library.findByRole('status');
    expect(commands.find((command) => command.cmd === 'save_role')).toMatchObject({ group: 'Foundation', name: 'reviewer', old_scope: 'project', data: { description: 'Review UI', preamble: 'Be exact', priorities: ['correctness'], model: 'keep-model', system_prompt: 'Keep full prompt' } });
    expect(commands.find((command) => command.cmd === 'save_role')?.data).not.toHaveProperty('path');
  });

  it('keeps project and user actions distinct and previews the current draft through correlated reads', async () => {
    const calls: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
      const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command);
      const data = command.cmd === 'list_actions' ? { type: 'actions', group: 'Foundation', actions: [{ name: 'test/action', global: false }, { name: 'test/action', global: true, shadowed: true }] }
        : command.cmd === 'list_roles' ? { type: 'roles', group: 'Foundation', roles: [] }
        : command.cmd === 'get_action' ? { type: 'action_detail', group: 'Foundation', scope: command.scope, name: command.name, action: { prompt: '{{ TASK }}' } }
        : { type: command.variables_only ? 'action_variables' : 'action_rendered', workspace_group: 'Foundation', name: command.name, scope: command.scope, vars: [{ name: 'TASK' }], prompt: 'Rendered current draft' };
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
    }));
    const { appStore } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Actions' }));
    await screen.findByRole('button', { name: 'test/actionproject' }); fireEvent.click(screen.getByRole('button', { name: /test\/actionuser/ }));
    const prompt = await screen.findByRole('textbox', { name: 'Prompt' }); fireEvent.change(prompt, { target: { value: 'Unsaved {{ TASK }}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' })); await screen.findByText('Rendered current draft');
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'action_rendered', name: 'other', prompt: 'Unrelated preview' })); });
    expect(screen.getByText('Rendered current draft')).toBeVisible(); expect(screen.queryByText('Unrelated preview')).not.toBeInTheDocument();
    expect(calls).toContainEqual(expect.objectContaining({ cmd: 'get_action', scope: 'user', name: 'test/action' }));
    expect(calls.find((call) => call.cmd === 'render_action' && !call.variables_only)?.action).toMatchObject({ prompt: 'Unsaved {{ TASK }}' });
  });

  it('loads searchable agent-run history through correlated reads', async () => {
    const commands: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
      const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; commands.push(command);
      const data = command.cmd === 'get_agent_history'
        ? { type: 'agent_history_list', records: [{ id: 'old-agent', name: 'Release Worker', group: 'Foundation', status: 'merged' }] }
        : { type: 'agent_history_detail', record: { id: 'old-agent', name: 'Release Worker', status: 'merged' }, tasks: [{ task_id: 'release-task', task_title: 'Cut release' }], messages: [{ id: 'release-message', message: 'Release verified' }] };
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
    }));
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'History' }));
    fireEvent.click(await screen.findByRole('button', { name: /Release Worker/ }));
    await screen.findByText('Release verified');
    expect(commands.filter((command) => command.cmd.startsWith('get_agent_history'))).toEqual([{ cmd: 'get_agent_history', status: 'merged', limit: 100 }, { cmd: 'get_agent_history_detail', agent_id: 'old-agent', message_limit: 100 }]);
    expect(screen.getByText('Cut release')).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search history' }), { target: { value: 'no match' } });
    expect(screen.getByText('No historical runs')).toBeVisible();
  });

  it('authors and validates project Agent Classes with acknowledged catalog requests', async () => {
    const commands: TorqueCommand[] = []; let classes: UnknownRecord[] = [{ id: 'default-worker', display_name: 'Default Worker', base_kind: 'worker', version: '1', builtin: true, source: 'builtin', acl: { mode: 'allow', rules: [] } }];
    const capabilities = [{ id: 'self.read', label: 'Read own context', scopes: ['self'], base_kinds: ['worker'], maximum_scopes: { worker: 'self' } }];
    vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
      const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; commands.push(command);
      let data: UnknownRecord = { type: 'ok' };
      if (command.cmd === 'agent_class_list') data = { type: 'agent_classes', classes, capability_catalog: capabilities };
      if (command.cmd === 'agent_class_validate') data = { type: 'agent_class_validation', valid: true, agent_class: command.agent_class };
      if (command.cmd === 'agent_class_create') { const item = command.agent_class as UnknownRecord; classes = [...classes, item]; data = { type: 'agent_class_save', ok: true, agent_class: item, classes }; }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
    }));
    renderShell(); fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Catalog' }));
    await screen.findByText('Built-in classes cannot be edited. Duplicate this definition into the project to customize it.');
    fireEvent.click(screen.getByRole('button', { name: '＋ New' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'ID' }), { target: { value: 'release-worker' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Display name' }), { target: { value: 'Release Worker' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Class job prompt' }), { target: { value: 'Prepare and verify releases.' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Read own context/ })); fireEvent.click(screen.getByRole('button', { name: 'Validate' })); await screen.findByText('Validation passed');
    const library = screen.getByRole('heading', { name: 'Agent Classes' }).closest('section')!;
    fireEvent.click(within(library).getByRole('button', { name: 'Save' })); await screen.findByRole('heading', { name: 'Edit project Agent Class' });
    expect(commands.find((command) => command.cmd === 'agent_class_create')?.agent_class).toMatchObject({ id: 'release-worker', display_name: 'Release Worker', acl: { mode: 'allow', rules: [{ capability: 'self.read', scope: 'self' }] } });
  });

  it('searches settings descriptions and reveals a closed control without saving or losing drafts', async () => {
    const { commands } = mockSettingsRequests(); renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    const draft = await screen.findByRole('spinbutton', { name: 'Terminal scrollback' });
    fireEvent.change(draft, { target: { value: '9300' } });
    const search = screen.getByRole('searchbox', { name: 'Search settings' });
    fireEvent.change(search, { target: { value: 'age expiry' } });
    fireEvent.click(screen.getByRole('button', { name: /Event ingest max days.*Global defaults/ }));
    const days = screen.getByRole('spinbutton', { name: 'Event ingest max days' });
    expect(days).toHaveFocus(); expect(days.closest('details')).toHaveAttribute('open');
    expect(draft).toHaveValue(9300); expect(search).toHaveValue('');
    fireEvent.change(search, { target: { value: 'no such setting' } });
    expect(screen.getByText('No settings found for “no such setting”')).toHaveAttribute('role', 'status');
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(commands.filter((command) => /^(update_|engineer_update_)/.test(String(command.cmd)))).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Clear settings search' }));
    expect(search).toHaveFocus(); expect(search).toHaveValue(''); expect(draft).toHaveValue(9300);
  });

  it('captures a palette-key conflict without opening the palette or saving settings', async () => {
    const { commands } = mockSettingsRequests(); renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    const input = await screen.findByRole('textbox', { name: 'Create task shortcut' });
    fireEvent.keyDown(input, { key: 'k', ctrlKey: true });
    expect(screen.getByRole('alert')).toHaveTextContent('Open command palette');
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel reassignment' }));
    expect(input).toHaveFocus(); expect(input).toHaveValue('N');
    expect(commands.some((command) => command.cmd === 'update_global_settings')).toBe(false);
  });

  it('displays effective Relay configuration without promoting inherited values on unrelated saves', async () => {
    const { commands, refresh } = mockSettingsRequests();
    refresh('get_global_settings', { relay_config: { config: { enabled: true }, sources: { enabled: { source: 'env', value: true }, relay_url: { source: 'ee_connector.json', value: 'wss://inherited.invalid/ws' }, private_key_path: { source: 'ee_connector.json', value: '' } } } });
    renderShell(); fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    expect(await screen.findByLabelText('Relay')).toHaveValue('on');
    expect(screen.getByLabelText('Relay URL')).toHaveValue(''); expect(screen.getByLabelText('Relay URL')).toHaveAttribute('placeholder', 'wss://inherited.invalid/ws');
    expect(screen.getByLabelText('Private key path')).toHaveValue('');
    fireEvent.change(screen.getByLabelText('Terminal scrollback'), { target: { value: '9000' } }); fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findByText('Saved', { exact: true });
    expect(commands.find((command) => command.cmd === 'update_global_settings')?.settings).toEqual({ xterm_scrollback: 9000 });
  });

  it('refreshes untouched Relay fields while preserving focused and dirty fields through deltas', async () => {
    const { commands, refresh } = mockSettingsRequests();
    const config = { config: { enabled: false }, sources: { relay_url: { source: 'settings', value: 'wss://original.invalid/ws' }, daemon_id: { source: 'settings', value: 'old-daemon' }, credential_id: { source: 'env', value: 'inherited-credential' } } };
    refresh('get_global_settings', { relay_config: config });
    const { appStore } = renderShell(); fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    const daemon = await screen.findByLabelText('Daemon ID'); fireEvent.change(daemon, { target: { value: 'local-draft' } });
    const url = screen.getByLabelText<HTMLInputElement>('Relay URL'); act(() => url.focus()); url.setSelectionRange(6, 14);
    act(() => { appStore.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'relay_config', config: { enabled: true }, sources: { relay_url: { source: 'settings', value: 'wss://updated.invalid/ws' }, daemon_id: { source: 'settings', value: 'remote-daemon' }, credential_id: { source: 'settings', value: 'new-credential' } } }] })); });
    expect(url).toHaveValue('wss://original.invalid/ws'); expect(url).toHaveFocus(); expect([url.selectionStart, url.selectionEnd]).toEqual([6, 14]);
    expect(daemon).toHaveValue('local-draft'); expect(screen.getByLabelText('Credential ID')).toHaveValue('new-credential'); expect(screen.getByLabelText('Relay')).toHaveValue('on');
    fireEvent.blur(url); expect(url).toHaveValue('wss://updated.invalid/ws');
    fireEvent.change(url, { target: { value: '' } }); fireEvent.click(screen.getByRole('button', { name: 'Save changes' })); await screen.findByText('Saved', { exact: true });
    expect(commands.find((command) => command.cmd === 'update_global_settings')?.settings).toEqual({ relay_daemon_id: 'local-draft', relay_url: '' });
  });

  it('saves only edited global fields without promoting inherited relay or unrelated settings', async () => {
    const { commands } = mockSettingsRequests();
    renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    const scrollback = await screen.findByRole('spinbutton', { name: 'Terminal scrollback' });
    fireEvent.change(scrollback, { target: { value: '9000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(commands.some((command) => command.cmd === 'update_global_settings')).toBe(true));
    const global = commands.find((command) => command.cmd === 'update_global_settings')?.settings as Record<string, unknown>;
    expect(global.xterm_scrollback).toBe(9000);
    expect(global).not.toHaveProperty('relay_enabled');
    expect(global).toEqual({ xterm_scrollback: 9000 });
    expect(commands.filter((command) => /^(update_|engineer_update_)/.test(String(command.cmd)))).toHaveLength(1);
    expect(await screen.findByText('Saved', { exact: true })).toBeVisible();
  });

  it('retains structured settings drafts and exposes a partial save failure', async () => {
    mockSettingsRequests(true);
    const { appStore } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    const scrollback = await screen.findByRole('spinbutton', { name: 'Terminal scrollback' });
    fireEvent.change(scrollback, { target: { value: '9100' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Maximum agents' }), { target: { value: '7' } });
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'group_settings', group: 'Foundation', settings: { max_agents: 9 }, engineer_settings: {} })); });
    expect(scrollback).toHaveValue(9100);
    fireEvent.click(screen.getByText('Global defaults'));
    expect(screen.getByLabelText('Event ingest max days')).toHaveValue(14);
    expect(screen.queryByLabelText('Global settings (JSON)')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByText(/Group save refused/)).toBeVisible();
    expect(scrollback).toHaveValue(9100);
    expect(screen.getByText('Unsaved changes')).toBeVisible();
  });

  it('blocks blank and out-of-range numeric settings before sending any mutation and focuses hidden invalid fields', async () => {
    const { commands } = mockSettingsRequests(); renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    const input = await screen.findByRole('spinbutton', { name: 'Maximum agents' });
    for (const value of ['', '-1', '101', '1.5']) {
      fireEvent.change(input, { target: { value } }); fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      expect(screen.getByRole('alert')).toHaveTextContent('Correct the highlighted setting'); expect(input).toHaveFocus();
      expect(commands.some((command) => String(command.cmd).startsWith('update_'))).toBe(false);
    }
    fireEvent.change(input, { target: { value: '0' } });
    fireEvent.click(screen.getByText('Global defaults'));
    const days = screen.getByRole('spinbutton', { name: 'Event ingest max days' }); fireEvent.change(days, { target: { value: '' } }); fireEvent.click(screen.getByText('Global defaults'));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' })); expect(days).toHaveFocus(); expect(days.closest('details')).toHaveAttribute('open');
    fireEvent.change(days, { target: { value: '0' } }); fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findByText('Saved', { exact: true });
    expect(commands).toContainEqual({ cmd: 'update_group_settings', group: 'Foundation', settings: { max_agents: 0 } });
  });

  it('retries only unacknowledged scopes after a partial settings save', async () => {
    const { commands, setFailure } = mockSettingsRequests(true); renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    fireEvent.change(await screen.findByRole('spinbutton', { name: 'Terminal scrollback' }), { target: { value: '6000' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Maximum agents' }), { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' })); await screen.findByText(/Group save refused/);
    setFailure(false); fireEvent.click(screen.getByRole('button', { name: 'Save changes' })); await screen.findByText('Saved', { exact: true });
    expect(commands.filter((command) => command.cmd === 'update_global_settings')).toHaveLength(1);
    expect(commands.filter((command) => command.cmd === 'update_group_settings')).toHaveLength(2);
  });

  it('reconciles active Settings after reconnect while preserving draft identity, focus, caret and secrets', async () => {
    const { refresh } = mockSettingsRequests(); const { appStore } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    fireEvent.change(await screen.findByRole('spinbutton', { name: 'Terminal scrollback' }), { target: { value: '9100' } });
    const secret = screen.getByLabelText('Anthropic key'); fireEvent.change(secret, { target: { value: 'unsaved-test-key' } });
    const directory = screen.getByRole<HTMLInputElement>('textbox', { name: 'Default directory' }); fireEvent.change(directory, { target: { value: '/draft/path' } }); directory.focus(); directory.setSelectionRange(2, 6);
    refresh('get_global_settings', { settings: { xterm_scrollback: 8000, event_ingest_max_days: 30 }, defaults: { event_ingest_max_days: 21 } });
    refresh('get_group_settings', { settings: { max_agents: 9, default_directory: '/server/path' }, engineer_settings: { default_worker_concurrency: 6 }, architect_settings: { architect_heartbeat_interval: 900 } });
    refresh('get_ai_settings', { settings: { enabled: false, generation: { anthropic: { model: 'refreshed-model' } } } });
    act(() => { appStore.dispatch(connectionActions.disconnected({ at: 2_000 })); appStore.dispatch(projectionActions.snapshotReceived(compactStateFixture)); appStore.dispatch(connectionActions.connected({ at: 3_000, reconnect: true })); });
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Maximum agents' })).toHaveValue(9));
    expect(screen.getByRole('textbox', { name: 'Default directory' })).toBe(directory); expect(directory).toHaveValue('/draft/path'); expect(directory).toHaveFocus(); expect(directory.selectionStart).toBe(2); expect(directory.selectionEnd).toBe(6);
    expect(screen.getByRole('spinbutton', { name: 'Terminal scrollback' })).toHaveValue(9100); expect(secret).toHaveValue('unsaved-test-key'); expect(screen.getByRole('textbox', { name: 'Anthropic model' })).toHaveValue('refreshed-model');
    fireEvent.click(screen.getByText('Global defaults')); expect(screen.getByRole('spinbutton', { name: 'Event ingest max days' })).toHaveValue(30);
    fireEvent.click(screen.getByRole('button', { name: 'Reset Event ingest max days' })); expect(screen.getByRole('spinbutton', { name: 'Event ingest max days' })).toHaveValue(21);
  });

  it('retains staged resets through failed reconnect reads, retries in place and reads nothing while hidden', async () => {
    const { commands, failRead, refresh } = mockSettingsRequests(); const { appStore } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' })); await screen.findByRole('spinbutton', { name: 'Maximum agents' });
    fireEvent.click(screen.getByText('Engineer behavior defaults')); fireEvent.click(screen.getByRole('button', { name: 'Reset Engineer defaults' }));
    const concurrency = screen.getByRole('combobox', { name: 'Default worker concurrency' });
    refresh('get_group_settings', { engineer_settings: { default_worker_concurrency: 6 }, engineer_defaults: { default_worker_concurrency: 7 } });
    failRead('get_group_settings'); act(() => { appStore.dispatch(connectionActions.connected({ at: 2_000, reconnect: true })); });
    await screen.findByText(/Settings refresh failed/); expect(concurrency).toHaveValue('2');
    failRead(''); fireEvent.click(screen.getByRole('button', { name: 'Retry settings' }));
    await waitFor(() => expect(screen.queryByText(/Settings refresh failed/)).not.toBeInTheDocument()); expect(screen.getByRole('combobox', { name: 'Default worker concurrency' })).toBe(concurrency); expect(concurrency).toHaveValue('2');
    fireEvent.click(screen.getByRole('button', { name: 'Reset Engineer defaults' })); expect(concurrency).toHaveValue('7');
    fireEvent.click(screen.getByRole('button', { name: 'Mission Control' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard changes' }));
    const reads = commands.filter((command) => ['get_global_settings', 'get_group_settings', 'get_ai_settings'].includes(String(command.cmd))).length;
    await act(async () => { appStore.dispatch(connectionActions.connected({ at: 3_000, reconnect: true })); await Promise.resolve(); });
    expect(commands.filter((command) => ['get_global_settings', 'get_group_settings', 'get_ai_settings'].includes(String(command.cmd)))).toHaveLength(reads);
  });

  it('aborts a reconnect read before saving and ignores its late response after acknowledgement', async () => {
    const { fetcher } = mockSettingsRequests(); const { appStore } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    const input = await screen.findByRole('spinbutton', { name: 'Terminal scrollback' }); fireEvent.change(input, { target: { value: '9200' } });
    const original = fetcher.getMockImplementation()!; let hold = true;
    let release: ((response: Awaited<ReturnType<typeof original>>) => void) | undefined; let signal: AbortSignal | null | undefined;
    fetcher.mockImplementation((url, options) => {
      const cmd = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand;
      if (hold && cmd.cmd === 'get_global_settings') { hold = false; signal = options?.signal; return new Promise((resolve) => { release = resolve; }); }
      return original(url, options);
    });
    act(() => { appStore.dispatch(connectionActions.connected({ at: 2_000, reconnect: true })); });
    await waitFor(() => expect(release).toBeDefined());
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' })); await screen.findByText('Saved', { exact: true });
    expect(signal?.aborted).toBe(true);
    await act(async () => { release!({ ok: true, json: () => Promise.resolve({ ok: true, data: { type: 'global_settings', settings: { xterm_scrollback: 1234 } } }) }); await Promise.resolve(); });
    expect(input).toHaveValue(9200); expect(screen.getByRole('spinbutton', { name: 'Terminal scrollback' })).toBe(input);
  });

  it('stages section resets and preserves newer unrelated settings through a sparse save', async () => {
    const { commands } = mockSettingsRequests(); const { appStore } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
    await screen.findByRole('spinbutton', { name: 'Terminal scrollback' });
    fireEvent.click(screen.getByRole('button', { name: 'Reset group defaults' }));
    expect(screen.getByRole('spinbutton', { name: 'Maximum agents' })).toHaveValue(0);
    expect(commands.every((command) => !/^(update_|engineer_update_)/.test(String(command.cmd)))).toBe(true);
    act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'group_settings', group: 'Foundation', settings: { max_agents: 9, agent_model: 'new-external-model', architect_heartbeat_interval: 900 }, engineer_settings: { pending_question: 'Still waiting' }, architect_settings: { group: 'Foundation', architect_heartbeat_interval: 900 } })); });
    expect(screen.getByRole('spinbutton', { name: 'Maximum agents' })).toHaveValue(0);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByText('Saved', { exact: true })).toBeVisible();
    const mutations = commands.filter((command) => /^(update_|engineer_update_)/.test(String(command.cmd)));
    expect(mutations).toHaveLength(1);
    expect(mutations[0]).toMatchObject({ cmd: 'update_group_settings', group: 'Foundation', settings: { max_agents: 0, shell: '', env_vars: {}, worktree_symlinks: [] } });
    expect(mutations[0]?.settings).not.toHaveProperty('engineer_agent_id');
    expect(mutations[0]?.settings).not.toHaveProperty('architect_heartbeat_interval');
    fireEvent.click(screen.getByText('Engineer behavior defaults'));
    expect(screen.queryByLabelText('Pending question')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Group')).not.toBeInTheDocument();
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
    const requests: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => { const command = JSON.parse(typeof options.body === 'string' ? options.body : '{}' ) as TorqueCommand; requests.push(command); if (command.cmd === 'get_mission_control' || command.cmd === 'mission_control_dismiss') return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data: command.cmd === 'get_mission_control' ? { type: 'mission_control_summary', ...(frame.mission_control_summary as Record<string, unknown>) } : { type: 'ok' } }) }); return new Promise(() => undefined); }));
    const frame: StateFrame = {
      ...compactStateFixture,
      mission_control_summary: { group: 'Foundation', sections: { needs_operator_now: { items: [{ id: 'gate-1', title: 'Approve release', reason: 'Verification gate', primary_task_id: 'task-1' }] } } },
      system_health_metrics: { counts: { agents: 1, needs_attention: 1 } },
      supervisor_sessions: { runtime_state: 'running', sessions: [{ session_id: 'session-1', agent_name: 'UI Worker', pid: 42, status: 'live' }] },
    };
    const { sendCommand } = renderShell(browserHost, frame);
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    expect(await screen.findByText('Approve release')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss Approve release' }));
    expect(requests).toContainEqual(expect.objectContaining({ cmd: 'mission_control_dismiss', id: 'gate-1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Terminate UI Worker' }));
    expect(screen.getByRole('dialog', { name: 'Terminate PTY session?' })).toBeVisible();
    expect(sendCommand).not.toHaveBeenCalledWith({ cmd: 'supervisor_session_terminate', session_id: 'session-1' });
    fireEvent.click(screen.getByRole('button', { name: 'Terminate session' }));
    expect(sendCommand).toHaveBeenCalledWith({ cmd: 'supervisor_session_terminate', session_id: 'session-1' });
    fireEvent.click(screen.getByRole('button', { name: 'Restart supervisor' }));
    expect(screen.getByRole('dialog', { name: 'Restart PTY supervisor?' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(sendCommand).not.toHaveBeenCalledWith({ cmd: 'supervisor_restart' });
    expect(requests).toContainEqual({ cmd: 'get_metrics_history', group: 'Foundation', window: '24h' });
  });

  it('opens maintained Help through correlated HTTP and refreshes the active document', async () => {
    const calls: TorqueCommand[] = [];
    vi.stubGlobal('fetch', vi.fn((_url: string, options?: RequestInit) => {
      const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand; calls.push(command);
      const data = command.cmd === 'help_list' ? { type: 'help_topics', topics: [{ topic_id: 'operate', title: 'Operating Torque', source_path: 'docs/operate/index.md' }] } : { type: 'help_topic', path_anchor: command.topic, title: 'Operating Torque', body_excerpt: 'Use Mission Control to supervise by exception.' };
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, data }) });
    }));
    const { sendCommand } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: /Control/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Help' }));
    expect(await screen.findByText('Use Mission Control to supervise by exception.')).toBeVisible();
    expect(calls).toContainEqual({ cmd: 'help_show', topic: 'docs/operate/index.md', max_chars: 16000 });
    expect(sendCommand).not.toHaveBeenCalledWith(expect.objectContaining({ cmd: 'help_list' }));
    const count = calls.length; fireEvent.click(screen.getByRole('button', { name: 'Refresh section' }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(count));
  });

  it('bridges native menu actions into React-owned panels and dialogs', async () => {
    mockSettingsRequests();
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


it('preserves terminal parent when reordering and sends dedicated child commands', () => {
  const { sendCommand } = renderShell(browserHost, { ...compactStateFixture, groups: { Foundation: ['e'] }, agents: { e: { id: 'e', name: 'Owner', group: 'Foundation', kind: 'engineer' }, t: { id: 't', name: 'Shell', group: 'Foundation', cell_type: 'terminal', parent_id: 'e' } }, children: { e: ['t'] }, selected_agent_id: 't' });
  fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
  fireEvent.click(screen.getByRole('treeitem', { name: /Shell, terminal/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Lifecycle actions for Shell' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Move or reorder…' }));
  expect(screen.getByLabelText('Terminal parent')).toHaveValue('e');
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  expect(sendCommand).toHaveBeenCalledWith({ cmd: 'reorder_child', id: 't', parent_id: 'e', before: '' });
  expect(sendCommand).toHaveBeenCalledWith({ cmd: 'resync' });
  expect(sendCommand.mock.calls.some(([command]) => command.cmd === 'move_agent')).toBe(false);
});

it('offers arbitrary keyboard-accessible group ordering', () => {
  const { sendCommand } = renderShell(browserHost, { ...compactStateFixture, groups: { Foundation: [], Middle: [], Last: [] } });
  fireEvent.click(screen.getByRole('button', { name: 'Last group options' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Move group…' }));
  fireEvent.change(screen.getByLabelText('Group position'), { target: { value: 'Middle' } });
  fireEvent.click(screen.getByRole('button', { name: 'Move group' }));
  expect(sendCommand).toHaveBeenCalledWith({ cmd: 'move_group', group: 'Last', before: 'Middle' });
});

it('restores lane visibility and persists selected lane independently per group', () => {
  const { sendCommand } = renderShell(browserHost, { ...compactStateFixture, board_hidden_wide_lanes_by_group: { Foundation: { Ready: true } } });
  expect(screen.queryByRole('heading', { name: 'Ready' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Visible lane'), { target: { value: 'Ready' } });
  expect(sendCommand).toHaveBeenCalledWith({ cmd: 'board_set_selected_lanes', selected_lanes_by_group: { Foundation: 'Ready' } });
  fireEvent.click(screen.getByRole('button', { name: 'Show lanes' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Show Ready' }));
  expect(sendCommand).toHaveBeenCalledWith({ cmd: 'board_set_hidden_wide_lanes', hidden_wide_lanes_by_group: { Foundation: { Ready: false } } });
});

it('protects settings drafts on section and workspace navigation and retains field identity on cancel', async () => {
  const { commands } = mockSettingsRequests(); renderShell();
  fireEvent.click(screen.getByRole('button', { name: /◎ Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
  const input = await screen.findByRole('textbox', { name: 'Default directory' });
  fireEvent.change(input, { target: { value: '/retained/draft' } }); input.focus(); (input as HTMLInputElement).setSelectionRange(3, 8);
  fireEvent.click(screen.getByRole('button', { name: 'Help' }));
  const dialog = await screen.findByRole('dialog', { name: 'Discard settings changes?' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Keep editing' }));
  await waitFor(() => expect(input).toHaveFocus()); expect(input).toHaveValue('/retained/draft'); expect((input as HTMLInputElement).selectionStart).toBe(3);
  expect(screen.getByRole('textbox', { name: 'Default directory' })).toBe(input);
  fireEvent.keyDown(document.body, { key: 'b' }); await screen.findByRole('dialog', { name: 'Discard settings changes?' });
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  await screen.findByRole('heading', { name: 'Board' });
  expect(commands.some((command) => /^(update_|engineer_update_)/.test(String(command.cmd)))).toBe(false);
});

it('does not send group navigation on cancel and allows it exactly once after discard', async () => {
  mockSettingsRequests(); const { sendCommand } = renderShell(browserHost, { ...compactStateFixture, groups: { ...(compactStateFixture.groups as Record<string, unknown>), Research: { name: 'Research' } } });
  fireEvent.click(screen.getByRole('button', { name: /◎ Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
  fireEvent.change(await screen.findByRole('spinbutton', { name: 'Maximum agents' }), { target: { value: '7' } });
  const group = screen.getByRole('button', { name: 'Research' }); fireEvent.click(group);
  await screen.findByRole('dialog', { name: 'Discard settings changes?' }); fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(sendCommand.mock.calls.filter(([command]) => command.cmd === 'ui_select_group')).toHaveLength(0);
  fireEvent.click(group); fireEvent.click(await screen.findByRole('button', { name: 'Discard changes' }));
  expect(sendCommand.mock.calls.filter(([command]) => command.cmd === 'ui_select_group')).toEqual([[{ cmd: 'ui_select_group', group: 'Research' }]]);
});

it('keeps pending and failed saves mounted until a deliberate exit after the result', async () => {
  const { fetcher, setFailure, commands } = mockSettingsRequests(true); renderShell();
  fireEvent.click(screen.getByRole('button', { name: /◎ Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
  const input = await screen.findByRole('spinbutton', { name: 'Maximum agents' }); fireEvent.change(input, { target: { value: '7' } });
  const original = fetcher.getMockImplementation()!; let release = () => {};
  fetcher.mockImplementation((url, options) => {
    const command = JSON.parse(typeof options?.body === 'string' ? options.body : '{}') as TorqueCommand;
    if (command.cmd === 'update_group_settings') return new Promise((resolve) => { release = () => { void original(url, options).then(resolve); }; });
    return original(url, options);
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' })); fireEvent.click(screen.getByRole('button', { name: /▦ Board/ }));
  await screen.findByRole('dialog', { name: 'Settings save in progress' });
  expect(screen.queryByRole('button', { name: 'Discard changes' })).not.toBeInTheDocument();
  expect(input).toBeDisabled(); await act(async () => { release(); await Promise.resolve(); });
  await screen.findByRole('dialog', { name: 'Discard settings changes?' }); fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(await screen.findByText(/Group save refused/)).toBeVisible(); expect(screen.getByRole('spinbutton', { name: 'Maximum agents' })).toBe(input); expect(input).toHaveValue(7);
  setFailure(false); fireEvent.click(screen.getByRole('button', { name: 'Save changes' })); fireEvent.click(screen.getByRole('button', { name: /▦ Board/ }));
  await screen.findByRole('dialog', { name: 'Settings save in progress' }); await act(async () => { release(); await Promise.resolve(); });
  await screen.findByRole('dialog', { name: 'Leave Settings?' }); expect(screen.queryByRole('heading', { name: 'Board' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Continue navigation' })); await screen.findByRole('heading', { name: 'Board' });
  expect(commands.filter((command) => command.cmd === 'update_group_settings')).toHaveLength(2);
});

it('guards native-menu and command-palette exits, then clears discarded secrets and permits clean exits', async () => {
  mockSettingsRequests(); renderShell();
  fireEvent.click(screen.getByRole('button', { name: /◎ Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
  const key = await screen.findByLabelText('Anthropic key'); fireEvent.change(key, { target: { value: 'draft-secret-not-sent' } });
  act(() => { (window as Window & { openLogViewer?: () => void }).openLogViewer!(); });
  await screen.findByRole('dialog', { name: 'Discard settings changes?' }); fireEvent.click(screen.getByRole('button', { name: 'Keep editing' })); expect(key).toHaveValue('draft-secret-not-sent');
  fireEvent.click(screen.getByRole('button', { name: /Search commands/ })); fireEvent.change(screen.getByRole('combobox', { name: 'Search commands' }), { target: { value: 'Open Board' } });
  fireEvent.click(screen.getByRole('option', { name: 'Open Board' }));
  await screen.findByRole('dialog', { name: 'Discard settings changes?' }); expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' })); await screen.findByRole('heading', { name: 'Board' });
  fireEvent.click(screen.getByRole('button', { name: /◎ Control/ })); expect(await screen.findByLabelText('Anthropic key')).toHaveValue('');
  const directory = screen.getByRole('textbox', { name: 'Default directory' }); fireEvent.change(directory, { target: { value: '/temporary' } }); fireEvent.change(directory, { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Mission Control' })); expect(screen.queryByRole('dialog', { name: 'Discard settings changes?' })).not.toBeInTheDocument();
});

it('retains the edited settings group when the daemon changes the active group', async () => {
  const { refresh } = mockSettingsRequests();
  const frame = { ...compactStateFixture, groups: { ...(compactStateFixture.groups as Record<string, unknown>), Research: { name: 'Research' } } };
  const { appStore } = renderShell(browserHost, frame);
  fireEvent.click(screen.getByRole('button', { name: /◎ Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
  const directory = await screen.findByRole('textbox', { name: 'Default directory' }); fireEvent.change(directory, { target: { value: '/foundation/draft' } });
  act(() => { appStore.dispatch(projectionActions.snapshotReceived({ ...frame, active_group: 'Research' })); });
  expect(await screen.findByText(/Settings for Foundation remain open/)).toBeVisible(); expect(directory).toHaveValue('/foundation/draft');
  fireEvent.click(screen.getByRole('button', { name: 'Switch to Research' })); fireEvent.click(await screen.findByRole('button', { name: 'Keep editing' }));
  expect(screen.getByRole('textbox', { name: 'Default directory' })).toBe(directory);
  refresh('get_group_settings', { group: 'Research', settings: { default_directory: '/research' } });
  fireEvent.click(screen.getByRole('button', { name: 'Switch to Research' })); fireEvent.click(await screen.findByRole('button', { name: 'Discard changes' }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Default directory' })).toHaveValue('/research'));
});

it('does not detach a dirty settings workspace before explicit discard at the host boundary', async () => {
  mockSettingsRequests(); const invoke = vi.fn((command: string) => Promise.resolve(command === 'list_detached' ? [] : command === 'detach' ? 'settings-detached' : null));
  const { sendCommand, appStore } = renderShell(createTauriHost(invoke));
  fireEvent.click(screen.getByRole('button', { name: /◎ Control/ })); fireEvent.click(await screen.findByRole('button', { name: 'Settings' }));
  const input = await screen.findByRole('spinbutton', { name: 'Maximum agents' }); fireEvent.change(input, { target: { value: '7' } });
  act(() => { (window as Window & { detachActivePanel?: () => void }).detachActivePanel!(); });
  fireEvent.click(await screen.findByRole('button', { name: 'Keep editing' })); expect(invoke.mock.calls.some(([command]) => command === 'detach')).toBe(false);
  // External native ownership changes must not destroy the local draft either.
  act(() => { appStore.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 11, ops: [{ op: 'ui_update', key: 'detached_panels', value: { control: { label: 'external-control' } } }] })); });
  expect(screen.getByRole('spinbutton', { name: 'Maximum agents' })).toBe(input); expect(input).toHaveValue(7);
  act(() => { appStore.dispatch(projectionActions.deltaReceived({ type: 'delta', seq: 12, ops: [{ op: 'ui_update', key: 'detached_panels', value: {} }] })); });
  act(() => { (window as Window & { detachActivePanel?: () => void }).detachActivePanel!(); });
  fireEvent.click(await screen.findByRole('button', { name: 'Discard changes' }));
  await waitFor(() => expect(invoke).toHaveBeenCalledWith('detach', { panel: 'control', section: 'settings', bounds: { width: 1080, height: 740 } }));
  expect(sendCommand.mock.calls.filter(([command]) => command.cmd === 'ui_set_detached_panels')).toHaveLength(1);
});


it('activates the selected terminal on double click and Enter while ordinary tree navigation preserves Activity', () => {
  const { sendCommand } = renderShell();
  fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
  const row = screen.getByRole('treeitem', { name: 'Foundation Worker, worker, running' });
  fireEvent.click(screen.getByRole('tab', { name: 'Activity' }));
  fireEvent.click(row); expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true');
  sendCommand.mockClear(); fireEvent.doubleClick(row);
  expect(sendCommand).toHaveBeenCalledWith({ cmd: 'focus_agent', id: 'agent-1' });
  expect(screen.getByRole('tab', { name: 'Live' })).toHaveAttribute('aria-selected', 'true');
  fireEvent.click(screen.getByRole('tab', { name: 'Activity' })); sendCommand.mockClear();
  fireEvent.keyDown(row, { key: 'Enter' });
  expect(sendCommand).toHaveBeenCalledWith({ cmd: 'focus_agent', id: 'agent-1' });
  expect(screen.getByRole('tab', { name: 'Live' })).toHaveAttribute('aria-selected', 'true');
});
it('applies live Focus on click changes without activating a row merely receiving keyboard focus', () => {
  const { appStore, sendCommand } = renderShell(browserHost, { ...compactStateFixture, global_settings: { focus_on_click: false } });
  fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
  const row = screen.getByRole('treeitem', { name: 'Foundation Worker, worker, running' });
  sendCommand.mockClear(); fireEvent.click(row); expect(sendCommand).not.toHaveBeenCalledWith({ cmd: 'focus_agent', id: 'agent-1' });
  act(() => { appStore.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'global_settings', settings: { focus_on_click: true } })); });
  sendCommand.mockClear(); fireEvent.focus(row); expect(sendCommand).not.toHaveBeenCalled();
  fireEvent.click(row); expect(sendCommand).toHaveBeenCalledWith({ cmd: 'focus_agent', id: 'agent-1' });
});
