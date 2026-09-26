import { act, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, expect, it, vi } from 'vitest';
import { connectionActions, createAppStore } from '../../app/store';
import type { AuxiliaryFrame, TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import type * as httpModule from '../../protocol/http';
import { useCreationRoles } from './useCreationRoles';
vi.mock('../../protocol/http', async (original) => ({ ...await original<typeof httpModule>(), readCommand: vi.fn() }));
const frame = (group = 'A', name = 'build'): AuxiliaryFrame => ({ type: 'roles', group, roles: [{ name, display_name: name }] });
function Owner({ group, active }: { group: string; active: boolean }) {
  const catalog = useCreationRoles(group, active);
  return <><input aria-label="Draft" defaultValue="keep this draft" /><output>{catalog.roles.map((role) => String(role.name)).join(',')}</output>{catalog.error ? <p role="alert">{catalog.error}</p> : null}<button onClick={catalog.refresh}>Refresh roles</button><span>{catalog.verified ? 'Verified' : 'Unverified'}</span></>;
}
function setup() {
  const store = createAppStore(); store.dispatch(connectionActions.connected({ at: 1, reconnect: false }));
  const reads: { command: TorqueCommand; signal: AbortSignal; resolve: (frame: AuxiliaryFrame) => void }[] = [];
  vi.mocked(readCommand).mockImplementation((command, signal) => new Promise((resolve) => { reads.push({ command, signal, resolve }); }));
  const content = (group = 'A', active = true) => <Provider store={store}><Owner group={group} active={active} /></Provider>;
  const view = render(content());
  const reply = async (index: number, value: AuxiliaryFrame) => { await act(async () => { reads[index]!.resolve(value); await Promise.resolve(); }); };
  return { store, reads, reply, change: (group = 'A', active = true) => view.rerender(content(group, active)), ...view };
}
afterEach(() => { vi.resetAllMocks(); vi.useRealTimers(); });
it('owns group choices and rejects late and wrong-group responses', async () => {
  const test = setup(); expect(test.reads[0]!.command).toEqual({ cmd: 'list_roles', group: 'A' });
  await test.reply(0, frame()); test.change('B'); expect(screen.getByRole('status')).toHaveTextContent('');
  await test.reply(1, frame('A', 'foreign')); expect(screen.getByRole('alert')).toHaveTextContent('did not match');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh roles' })); await test.reply(2, frame('B', 'review')); expect(screen.getByRole('status')).toHaveTextContent('review');
  test.change('A'); const pending = test.reads.at(-1)!; test.change('B'); expect(pending.signal.aborted).toBe(true);
  await test.reply(test.reads.length - 1, frame('B', 'current')); await test.reply(test.reads.length - 2, frame('A', 'obsolete')); expect(screen.getByRole('status')).toHaveTextContent('current');
});
it('times out and retries while retaining accepted choices and draft focus', async () => {
  vi.useFakeTimers(); const test = setup(); await test.reply(0, frame());
  const input = screen.getByRole<HTMLInputElement>('textbox', { name: 'Draft' }); input.focus(); input.setSelectionRange(2, 6);
  act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); });
  await act(async () => { await vi.advanceTimersByTimeAsync(15_000); }); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(test.reads[1]!.signal.aborted).toBe(true);
  expect(input).toHaveFocus(); expect([input.selectionStart, input.selectionEnd]).toEqual([2, 6]); expect(screen.getByRole('status')).toHaveTextContent('build');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh roles' })); await test.reply(2, frame('A', 'updated')); await test.reply(1, frame('A', 'obsolete')); expect(screen.getByRole('status')).toHaveTextContent('updated');
});
it('cancels hidden reads and does not refresh while hidden', () => {
  const test = setup(); test.change('A', false); expect(test.reads[0]!.signal.aborted).toBe(true);
  act(() => { test.store.dispatch(connectionActions.connected({ at: 2, reconnect: true })); }); expect(test.reads).toHaveLength(1);
  test.change(); expect(test.reads).toHaveLength(2); test.unmount(); expect(test.reads[1]!.signal.aborted).toBe(true);
});
it.each([{}, ['raw'], [{ name: '' }]])('rejects malformed role catalog %j', async (roles) => {
  const test = setup(); await test.reply(0, { type: 'roles', group: 'A', roles }); expect(screen.getByRole('alert')).toBeVisible(); expect(screen.getByText('Unverified')).toBeVisible();
});
