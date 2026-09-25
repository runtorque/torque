import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Provider } from 'react-redux';
import { beforeEach, expect, it, vi } from 'vitest';
import { createAppStore, connectionActions, projectionActions, workspaceUiActions } from '../../app/store';
import { compactStateFixture } from '../../protocol/fixtures';
import type { AuxiliaryFrame, TorqueCommand, UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { ControlCenter } from './ControlCenter';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
beforeEach(() => { read.mockReset(); });
async function setup(entries = 0) {
  let ai: UnknownRecord = { enabled: false, embeddings: { model_id: 'old', runtime: 'sentence_transformers' }, index: { counts: { chunks: entries }, corpus: { tasks: true } }, generation: { anthropic: { key: { configured: true, last4: '1234' } }, openai_compatible: { key: { configured: true } } } };
  const commands: TorqueCommand[] = [];
  let handle: (command: TorqueCommand) => Promise<AuxiliaryFrame> = () => Promise.resolve({ type: 'ai_settings', settings: ai });
  read.mockImplementation((command) => {
    if (command.cmd === 'get_global_settings') return Promise.resolve({ type: 'global_settings', settings: {}, defaults: {} });
    if (command.cmd === 'get_group_settings') return Promise.resolve({ type: 'group_settings', group: 'Foundation', settings: {}, engineer_settings: {}, architect_settings: {} });
    if (command.cmd === 'get_ai_settings') return Promise.resolve({ type: 'ai_settings', settings: ai });
    commands.push(command);
    if (command.cmd === 'update_ai_settings') return handle(command);
    return Promise.resolve({ type: 'ok' });
  });
  const store = createAppStore(); store.dispatch(projectionActions.snapshotReceived(compactStateFixture)); store.dispatch(connectionActions.connected({ at: 1000, reconnect: false })); store.dispatch(workspaceUiActions.setControlTab('settings'));
  const view = render(<Provider store={store}><ControlCenter group="Foundation" sendCommand={() => true} onCommandUnavailable={() => {}} /></Provider>);
  await screen.findByLabelText('Anthropic key');
  return { ...view, store, commands, setHandler: (next: typeof handle) => { handle = next; }, setAi: (next: UnknownRecord) => { ai = next; } };
}
const save = () => fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
const edit = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const confirmation = () => screen.getByRole('group', { name: 'Confirm embedding rebuild' });
it.each(['Anthropic', 'OpenAI-compatible'])('clearing %s removes its new key, and replacement cancels a pending clear', async (label) => {
  const { commands, store } = await setup(); const provider = label === 'Anthropic' ? 'anthropic' : 'openai_compatible';
  edit(`${label} key`, 'transient-key'); expect(JSON.stringify(store.getState())).not.toContain('transient-key');
  fireEvent.click(screen.getByRole('button', { name: `Clear ${label} key` })); expect(screen.getByLabelText(`${label} key`)).toHaveValue(''); expect(screen.getByText('Will clear on save.')).toBeVisible();
  save(); await screen.findByText('Saved', { exact: true }); expect(commands.at(-1)).toMatchObject({ secrets: {}, clear_secrets: [provider] });
  fireEvent.click(screen.getByRole('button', { name: `Clear ${label} key` })); edit(`${label} key`, 'replacement'); save(); await screen.findByText('Saved', { exact: true });
  expect(commands.at(-1)).toMatchObject({ secrets: { [provider]: 'replacement' }, clear_secrets: [] }); expect(screen.getByLabelText(`${label} key`)).toHaveValue('');
});
it('keeping a saved key cancels clear intent without resurrecting a discarded password draft', async () => {
  const { commands } = await setup(); edit('Anthropic key', 'discard-me'); fireEvent.click(screen.getByRole('button', { name: 'Clear Anthropic key' })); fireEvent.click(screen.getByRole('button', { name: 'Keep Anthropic key' }));
  expect(screen.getByLabelText('Anthropic key')).toHaveValue(''); edit('Default directory', '/tmp/unrelated'); save(); await screen.findByText('Saved', { exact: true }); expect(commands.some((command) => command.cmd === 'update_ai_settings')).toBe(false);
});
it('retains keys on refusal, redacts echoed credentials and retries only explicitly', async () => {
  const { commands, setHandler, store } = await setup(); setHandler(() => Promise.reject(new Error('Rejected secret.key[] token=other-secret'))); edit('Anthropic key', ' secret.key[] '); save();
  expect(await screen.findByRole('alert')).toHaveTextContent('Rejected [redacted] token: [redacted]'); expect(screen.getByLabelText('Anthropic key')).toHaveValue(' secret.key[] '); expect(JSON.stringify(store.getState())).not.toContain('secret.key'); expect(commands).toHaveLength(1);
  setHandler(() => Promise.resolve({ type: 'ai_settings', settings: {} })); save(); await screen.findByText('Saved', { exact: true }); expect(commands).toHaveLength(2); expect(screen.getByLabelText('Anthropic key')).toHaveValue('');
});
it.each([{ type: 'ok' }, { type: 'ai_settings', settings: [] }])('does not clear keys for invalid acknowledgement %j', async (response) => {
  const { setHandler } = await setup(); setHandler(() => Promise.resolve(response)); edit('Anthropic key', 'keep-me'); save(); expect(await screen.findByRole('alert')).toHaveTextContent('invalid AI settings acknowledgement'); expect(screen.getByLabelText('Anthropic key')).toHaveValue('keep-me');
});
it('confirms model changes before any coordinated writes, cancels, and invalidates changed drafts', async () => {
  const { commands } = await setup(12); edit('Default directory', '/tmp/draft'); edit('Embedding model', 'new'); save(); expect(confirmation()).toHaveTextContent('12 entries'); expect(commands).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel rebuild' })); expect(screen.queryByRole('group', { name: 'Confirm embedding rebuild' })).not.toBeInTheDocument(); expect(screen.getByLabelText('Default directory')).toHaveValue('/tmp/draft');
  save(); edit('Embedding model', 'newer'); expect(screen.queryByRole('group', { name: 'Confirm embedding rebuild' })).not.toBeInTheDocument(); save(); edit('Anthropic key', 'changed-secret'); expect(screen.queryByRole('group', { name: 'Confirm embedding rebuild' })).not.toBeInTheDocument(); save();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm settings and rebuild' })); await screen.findByText('Saved', { exact: true });
  expect(commands).toHaveLength(2); expect(commands[1]).toMatchObject({ cmd: 'update_ai_settings', confirm_embedding_rebuild: true, settings: { ai_embedding_model: 'newer' }, secrets: { anthropic: 'changed-secret' } }); expect(screen.queryByRole('group', { name: 'Confirm embedding rebuild' })).not.toBeInTheDocument();
});
it('confirms corpus changes with an existing index and makes cancel write nothing', async () => {
  const { commands } = await setup(5); fireEvent.click(within(screen.getByRole('heading', { name: 'AI subsystem' }).closest('section')!).getByRole('checkbox', { name: 'tasks' })); save(); expect(confirmation()).toHaveTextContent('5 entries'); fireEvent.click(screen.getByRole('button', { name: 'Cancel rebuild' })); expect(commands).toHaveLength(0); expect(within(screen.getByRole('heading', { name: 'AI subsystem' }).closest('section')!).getByRole('checkbox', { name: 'tasks' })).not.toBeChecked();
});
it('owns backend confirmation locally, retains partial acknowledgements and ignores cached prompts', async () => {
  const { commands, setHandler, store } = await setup(); act(() => { store.dispatch(projectionActions.auxiliaryResourceReceived({ type: 'ai_settings_requires_confirmation', message: 'Obsolete cache' })); }); expect(screen.queryByText('Obsolete cache')).not.toBeInTheDocument();
  setHandler(() => Promise.resolve({ type: 'ai_settings_requires_confirmation', message: 'Rebuild 9 entries; key=private-draft' })); edit('Default directory', '/tmp/accepted'); edit('Embedding model', 'new'); edit('Anthropic key', 'private-draft'); save();
  expect(await screen.findByRole('group', { name: 'Confirm embedding rebuild' })).toHaveTextContent('Rebuild 9 entries; key=[redacted]'); expect(commands).toHaveLength(2); expect(JSON.stringify(store.getState())).not.toContain('private-draft');
  setHandler(() => Promise.resolve({ type: 'ai_settings', settings: {} })); fireEvent.click(screen.getByRole('button', { name: 'Confirm settings and rebuild' })); await screen.findByText('Saved', { exact: true });
  expect(commands.filter((command) => command.cmd === 'update_group_settings')).toHaveLength(1); expect(commands.at(-1)).toMatchObject({ confirm_embedding_rebuild: true }); expect(screen.queryByRole('group', { name: 'Confirm embedding rebuild' })).not.toBeInTheDocument();
});
it('does not duplicate a pending confirmed save or clear a key before acknowledgement', async () => {
  const { commands, setHandler } = await setup(4); let finish!: (frame: AuxiliaryFrame) => void;
  setHandler(() => new Promise((resolve) => { finish = resolve; })); edit('Embedding model', 'new'); edit('Anthropic key', 'waiting-key'); save(); fireEvent.click(screen.getByRole('button', { name: 'Confirm settings and rebuild' }));
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled(); expect(screen.getByLabelText('Anthropic key')).toBeDisabled(); save(); expect(commands).toHaveLength(1); expect(screen.getByLabelText('Anthropic key')).toHaveValue('waiting-key');
  await act(async () => { finish({ type: 'ai_settings', settings: {} }); await Promise.resolve(); }); await waitFor(() => expect(screen.getByLabelText('Anthropic key')).toHaveValue(''));
});
