import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { readCommand } from '../protocol/http';
import type { AuxiliaryFrame } from '../protocol';
import { DeployStatus } from './DeployStatus';
vi.mock('../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const frame = (group = 'One', count = 2): AuxiliaryFrame => ({ type: 'deploy_state', group, pending_deploy: { count, torque_task_ids: ['TASK:1', 'TASK:2'] }, daemon_uptime_seconds: 120 });
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
beforeEach(() => { vi.useFakeTimers(); read.mockReset(); read.mockImplementation((cmd) => Promise.resolve(frame(String(cmd.group)))); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });
it('shows the real pending count and task details, hides zero, and makes errors retryable', async () => {
  const onOpen = vi.fn(); render(<DeployStatus group="One" enabled ready reconnect={0} onOpen={onOpen} />); await flush();
  expect(read).toHaveBeenCalledWith({ cmd: 'get_deploy_state', group: 'One' }, expect.any(AbortSignal));
  const chip = screen.getByRole('button', { name: 'Deploy +2' }); expect(chip).toHaveAttribute('title', expect.stringContaining('TASK:1, TASK:2'));
  read.mockResolvedValueOnce({ ...frame(), error: 'Repository unavailable' }); fireEvent.click(chip); await flush(); expect(onOpen).toHaveBeenCalledOnce();
  expect(screen.getByRole('button', { name: 'Deploy ?' })).toHaveAttribute('title', expect.stringContaining('Repository unavailable'));
  read.mockResolvedValueOnce(frame('Wrong')); fireEvent.click(screen.getByRole('button', { name: 'Deploy ?' })); await flush(); expect(screen.getByRole('button')).toHaveAttribute('title', expect.stringContaining('did not match'));
  read.mockResolvedValueOnce(frame('One', 0)); fireEvent.click(screen.getByRole('button')); await flush(); expect(screen.queryByRole('button')).toBeNull(); expect(onOpen).toHaveBeenCalledOnce();
});
it('polls only visible connected enabled scopes and refreshes on visibility, group and reconnect', async () => {
  let hidden = false; vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
  const props = { group: 'One', enabled: true, ready: true, reconnect: 0, onOpen: vi.fn() }; const { rerender } = render(<DeployStatus {...props} />); await flush(); expect(read).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(90_000); }); expect(read).toHaveBeenCalledTimes(2);
  hidden = true; fireEvent(document, new Event('visibilitychange')); await act(async () => { await vi.advanceTimersByTimeAsync(180_000); }); expect(read).toHaveBeenCalledTimes(2);
  hidden = false; fireEvent(document, new Event('visibilitychange')); await flush(); expect(read).toHaveBeenCalledTimes(3);
  rerender(<DeployStatus {...props} group="Two" />); expect(screen.queryByRole('button')).toBeNull(); await flush(); expect(read.mock.calls.at(-1)?.[0]).toEqual({ cmd: 'get_deploy_state', group: 'Two' });
  rerender(<DeployStatus {...props} group="Two" reconnect={1} />); await flush(); expect(read).toHaveBeenCalledTimes(5);
  rerender(<DeployStatus {...props} ready={false} />); await act(async () => { await vi.advanceTimersByTimeAsync(180_000); }); expect(read).toHaveBeenCalledTimes(5);
  rerender(<DeployStatus {...props} enabled={false} />); await act(async () => { await vi.advanceTimersByTimeAsync(180_000); }); expect(read).toHaveBeenCalledTimes(5); expect(screen.queryByRole('button')).toBeNull();
});
it('bounds an uncooperative transport and rejects responses from old groups and expired reads', async () => {
  let old!: (frame: AuxiliaryFrame) => void; read.mockImplementationOnce(() => new Promise((resolve) => { old = resolve; }));
  const props = { enabled: true, ready: true, reconnect: 0, onOpen: vi.fn() }; const { rerender } = render(<DeployStatus {...props} group="One" />); const oldSignal = read.mock.calls[0]![1];
  rerender(<DeployStatus {...props} group="Two" />); await flush(); expect(oldSignal.aborted).toBe(true); old(frame('One', 99)); await flush(); expect(screen.getByRole('button', { name: 'Deploy +2' })).toBeVisible();
  let late!: (frame: AuxiliaryFrame) => void; read.mockImplementationOnce(() => new Promise((resolve) => { late = resolve; })); fireEvent.click(screen.getByRole('button')); await flush();
  const expiredSignal = read.mock.calls.at(-1)![1]; await act(async () => { await vi.advanceTimersByTimeAsync(15_000); }); expect(expiredSignal.aborted).toBe(true); expect(screen.getByRole('button', { name: 'Deploy ?' })).toHaveAttribute('title', expect.stringContaining('timed out'));
  fireEvent.click(screen.getByRole('button')); await flush(); expect(screen.getByRole('button', { name: 'Deploy +2' })).toBeVisible(); late(frame('Two', 88)); await flush(); expect(screen.queryByText('Deploy +88')).toBeNull();
});
