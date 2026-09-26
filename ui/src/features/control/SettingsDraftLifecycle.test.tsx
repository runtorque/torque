import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { applyAppearance, appearanceDefaults, readAppearance } from '../../app/preferences';
import { SettingsNavigationProvider } from '../../app/SettingsNavigationGuard';
import { createAppStore, connectionActions, projectionActions, workspaceUiActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { ControlCenter } from './ControlCenter';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const appearanceKey = 'torque.appearance.v1';
beforeEach(() => {
  read.mockReset(); const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } });
  applyAppearance(appearanceDefaults);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function setup() {
  let group: UnknownRecord = { default_directory: '/saved', max_agents: 3 }; const global = { xterm_scrollback: 5000 };
  const commands: TorqueCommand[] = [];
  let handle: (command: TorqueCommand) => Promise<AuxiliaryFrame> = (command) => { group = { ...group, ...command.settings as UnknownRecord }; return Promise.resolve({ type: 'group_settings', group: 'Foundation', settings: group }); };
  read.mockImplementation((command) => {
    if (command.cmd === 'get_global_settings') return Promise.resolve({ type: 'global_settings', settings: global, defaults: global });
    if (command.cmd === 'get_group_settings') return Promise.resolve({ type: 'group_settings', group: 'Foundation', settings: group, defaults: group, engineer_settings: {}, architect_settings: {} });
    if (command.cmd === 'get_ai_settings') return Promise.resolve({ type: 'ai_settings', settings: {} });
    if (!/^(update_|engineer_update_)/.test(String(command.cmd))) return Promise.resolve({ type: 'ok' });
    commands.push(command); return handle(command);
  });
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1000, reconnect: false })); store.dispatch(workspaceUiActions.setControlTab('settings'));
  const view = render(<Provider store={store}><SettingsNavigationProvider><ControlCenter group="Foundation" sendCommand={() => true} onCommandUnavailable={() => {}} /></SettingsNavigationProvider></Provider>);
  await screen.findByLabelText('Default directory');
  return { ...view, store, commands, setHandler: (next: typeof handle) => { handle = next; } };
}
const save = () => screen.getByRole('button', { name: 'Save changes' });
const edit = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const accent = (name: string) => fireEvent.click(screen.getByRole('button', { name: `${name} accent` }));
const appearance = () => screen.getByRole('heading', { name: 'Appearance' }).closest('section')!;
it('returns caption, Save and navigation to clean after ordinary fields are reverted', async () => {
  const { commands } = await setup(); expect(save()).toBeDisabled();
  edit('Default directory', '/draft'); edit('Terminal scrollback', '6000'); expect(save()).toBeEnabled();
  edit('Default directory', '/saved'); expect(save()).toBeEnabled(); edit('Terminal scrollback', '5000');
  expect(save()).toBeDisabled(); expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument();
  fireEvent.submit(save().closest('form')!); expect(commands).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Help' })); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('keeps staged resets and explicit clear/override intents dirty even when ordinary values match', async () => {
  await setup(); fireEvent.click(screen.getByRole('button', { name: 'Reset global defaults' })); expect(save()).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Help' })); fireEvent.click(await screen.findByRole('button', { name: 'Discard changes' }));
  fireEvent.click(screen.getByRole('button', { name: 'Settings' })); await screen.findByLabelText('Default directory'); expect(save()).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Clear Anthropic key' })); expect(save()).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Keep Anthropic key' })); expect(save()).toBeDisabled();
  edit('Relay URL', 'wss://draft.invalid'); edit('Relay URL', ''); expect(save()).toBeEnabled();
});
it('previews appearance without submitting, retains it on cancel and restores it on discard', async () => {
  const { commands } = await setup(); accent('teal'); edit('Contrast', 'high');
  expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#2dd4bf'); expect(document.documentElement.dataset.torqueContrast).toBe('high');
  expect(localStorage.getItem(appearanceKey)).toBeNull(); expect(commands).toHaveLength(0); expect(save()).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Help' })); fireEvent.click(await screen.findByRole('button', { name: 'Keep editing' }));
  expect(screen.getByLabelText('Contrast')).toHaveValue('high'); expect(document.documentElement.dataset.torqueContrast).toBe('high');
  fireEvent.click(screen.getByRole('button', { name: 'Help' })); fireEvent.click(await screen.findByRole('button', { name: 'Discard changes' }));
  expect(document.documentElement.dataset.torqueContrast).toBe('balanced'); expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#8da2fb'); expect(localStorage.getItem(appearanceKey)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Settings' })); expect(await screen.findByLabelText('Contrast')).toHaveValue('balanced'); expect(save()).toBeDisabled();
});
it('reverts appearance cleanly and stages Reset until an explicit local-only save', async () => {
  localStorage.setItem(appearanceKey, JSON.stringify({ ...appearanceDefaults, accent: 'amber' })); const { commands, unmount } = await setup();
  accent('teal'); accent('amber'); expect(save()).toBeDisabled();
  fireEvent.click(within(appearance()).getByRole('button', { name: 'Reset' })); expect(save()).toBeEnabled(); expect(readAppearance().accent).toBe('amber');
  fireEvent.click(save()); await screen.findByText('Saved', { exact: true }); expect(readAppearance()).toEqual(appearanceDefaults); expect(commands).toHaveLength(0); expect(save()).toBeDisabled();
  accent('violet'); unmount(); expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#8da2fb'); expect(readAppearance().accent).toBe('blue');
});
it('retains the preview through reconnect and failed daemon save, committing only after success', async () => {
  const { commands, store, setHandler } = await setup(); edit('Density', 'comfortable'); edit('Default directory', '/next');
  const field = screen.getByLabelText('Density');
  act(() => { store.dispatch(connectionActions.connected({ at: 2000, reconnect: true })); });
  await waitFor(() => expect(read.mock.calls.filter(([command]) => command.cmd === 'get_global_settings')).toHaveLength(2));
  expect(screen.getByLabelText('Density')).toBe(field); expect(field).toHaveValue('comfortable'); expect(readAppearance().density).toBe('compact');
  setHandler(() => Promise.resolve({ type: 'error', message: 'Draft fixture refused' })); fireEvent.click(save()); await screen.findByText(/Draft fixture refused/);
  expect(save()).toBeEnabled(); expect(readAppearance().density).toBe('compact'); expect(document.documentElement.dataset.torqueDensity).toBe('comfortable');
  setHandler(() => Promise.resolve({ type: 'group_settings', group: 'Foundation', settings: { default_directory: '/next' } })); fireEvent.click(save()); await screen.findByText('Saved', { exact: true });
  expect(readAppearance().density).toBe('comfortable'); expect(commands).toHaveLength(2);
});
it('retains a storage failure and retries locally without replaying acknowledged daemon changes', async () => {
  const { commands } = await setup(); edit('Default directory', '/acknowledged'); accent('amber');
  const original = localStorage.setItem.bind(localStorage); let fail = true;
  vi.spyOn(localStorage, 'setItem').mockImplementation(function (key, value) { if (fail && key === appearanceKey) throw new Error('Quota exceeded'); original(key, value); });
  fireEvent.click(save()); expect(await screen.findByRole('alert')).toHaveTextContent('Appearance could not be saved on this device');
  expect(commands).toHaveLength(1); expect(readAppearance().accent).toBe('blue'); expect(save()).toBeEnabled(); expect(screen.getByLabelText('Default directory')).toHaveValue('/acknowledged');
  fail = false; fireEvent.click(save()); await screen.findByText('Saved', { exact: true }); expect(readAppearance().accent).toBe('amber'); expect(commands).toHaveLength(1); expect(save()).toBeDisabled();
});
it('protects appearance and prevents navigation while a coordinated save is pending', async () => {
  const { setHandler, commands } = await setup(); let finish!: (frame: AuxiliaryFrame) => void;
  setHandler(() => new Promise((resolve) => { finish = resolve; })); edit('Default directory', '/pending'); accent('violet'); fireEvent.click(save());
  fireEvent.click(screen.getByRole('button', { name: 'Help' })); expect(await screen.findByRole('dialog', { name: 'Settings save in progress' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Discard changes' })).not.toBeInTheDocument(); expect(screen.getByLabelText('Contrast')).toBeDisabled(); expect(localStorage.getItem(appearanceKey)).toBeNull();
  await act(async () => { finish({ type: 'group_settings', group: 'Foundation', settings: {} }); await Promise.resolve(); });
  await screen.findByRole('dialog', { name: 'Leave Settings?' }); fireEvent.click(screen.getByRole('button', { name: 'Continue navigation' })); expect(readAppearance().accent).toBe('violet'); expect(commands).toHaveLength(1);
});

it('rejects an unrelated save response without claiming success or clearing the draft', async () => {
  const { setHandler, commands } = await setup();
  setHandler(() => Promise.resolve({ type: 'mission_control_summary', group: 'Foundation', sections: {} }));
  edit('Default directory', '/must-remain'); fireEvent.click(save());
  expect(await screen.findByRole('alert')).toHaveTextContent('invalid group settings acknowledgement');
  expect(screen.queryByText('Saved', { exact: true })).not.toBeInTheDocument();
  expect(screen.getByLabelText('Default directory')).toHaveValue('/must-remain'); expect(save()).toBeEnabled(); expect(commands).toHaveLength(1);
});
it('releases a stalled save without accepting its late response or automatically retrying', async () => {
  const { setHandler, commands, store } = await setup(); let finish!: (frame: AuxiliaryFrame) => void;
  setHandler(() => new Promise((resolve) => { finish = resolve; })); edit('Default directory', '/pending');
  vi.useFakeTimers(); fireEvent.click(save());
  await act(async () => { await vi.advanceTimersByTimeAsync(30_001); });
  expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(save()).toBeEnabled();
  act(() => { store.dispatch(connectionActions.connected({ at: 2000, reconnect: true })); });
  await act(async () => { finish({ type: 'group_settings', group: 'Foundation', settings: { default_directory: '/pending' } }); await Promise.resolve(); });
  expect(commands).toHaveLength(1); expect(screen.queryByText('Saved', { exact: true })).not.toBeInTheDocument();
  expect(screen.getByLabelText('Default directory')).toHaveValue('/pending');
});
it('keeps draft, DOM focus and caret through a timed-out refresh, then retries without accepting the old read', async () => {
  const { store } = await setup(); const input = screen.getByLabelText<HTMLInputElement>('Default directory');
  edit('Default directory', '/kept'); input.focus(); input.setSelectionRange(1, 3);
  const original = read.getMockImplementation()!; let finish!: (frame: AuxiliaryFrame) => void; let signal: AbortSignal | undefined;
  read.mockImplementation((command, currentSignal) => command.cmd === 'get_global_settings' ? (signal = currentSignal, new Promise((resolve) => { finish = resolve; })) : original(command, currentSignal));
  vi.useFakeTimers(); act(() => { store.dispatch(connectionActions.connected({ at: 2000, reconnect: true })); });
  await act(async () => { await vi.advanceTimersByTimeAsync(15_001); });
  expect(screen.getByRole('alert')).toHaveTextContent('Settings refresh timed out'); expect(signal?.aborted).toBe(true);
  expect(screen.getByLabelText('Default directory')).toBe(input); expect(input).toHaveFocus(); expect([input.selectionStart, input.selectionEnd]).toEqual([1, 3]);
  await act(async () => { finish({ type: 'global_settings', settings: { xterm_scrollback: 9999 } }); await Promise.resolve(); });
  expect(screen.getByLabelText('Terminal scrollback')).toHaveValue(5000);
  read.mockImplementation(original); await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry settings' })); await Promise.resolve(); });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument(); expect(input).toHaveValue('/kept'); expect(save()).toBeEnabled();
});
it('keeps earlier acknowledgements after a later scope times out and retries only the unfinished scope', async () => {
  const { commands, setHandler } = await setup(); let finish!: (frame: AuxiliaryFrame) => void;
  setHandler((command) => command.cmd === 'update_global_settings' ? Promise.resolve({ type: 'state' }) : new Promise((resolve) => { finish = resolve; }));
  edit('Terminal scrollback', '6000'); edit('Default directory', '/unfinished'); vi.useFakeTimers();
  await act(async () => { fireEvent.click(save()); await Promise.resolve(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(30_001); }); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown');
  setHandler(() => Promise.resolve({ type: 'ok' })); await act(async () => { fireEvent.click(save()); await Promise.resolve(); });
  expect(commands.filter((command) => command.cmd === 'update_global_settings')).toHaveLength(1); expect(commands.filter((command) => command.cmd === 'update_group_settings')).toHaveLength(2);
  expect(screen.getByText('Saved', { exact: true })).toBeVisible();
  await act(async () => { finish({ type: 'error', message: 'Late obsolete refusal' }); await Promise.resolve(); }); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
