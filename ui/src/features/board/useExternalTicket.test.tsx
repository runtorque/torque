import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { browserHost } from '../../host/browser';
import type { DesktopHost } from '../../host/types';
import { readCommand } from '../../protocol/http';
import { useExternalTicket } from './useExternalTicket';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
beforeEach(() => { vi.mocked(readCommand).mockReset(); });
function setup(url = '') {
  const openExternal = vi.fn<DesktopHost['openExternal']>().mockResolvedValue();
  function Harness() { const ticket = useExternalTicket({ ...browserHost, kind: 'tauri', openExternal }); return <><button onClick={() => ticket.open({ id: 'task', task: 'Reviewed task', externalUrl: url })}>Launch</button>{ticket.dialog}</>; }
  const view = render(<Harness />); return { ...view, openExternal };
}
it('opens a saved URL through the supplied desktop host during the click without a daemon round trip', () => {
  const test = setup('https://example.invalid/123'); fireEvent.click(screen.getByText('Launch')); expect(test.openExternal).toHaveBeenCalledWith('https://example.invalid/123'); expect(readCommand).not.toHaveBeenCalled();
});
it('requires a fresh click after resolving an ID-only ticket and retains a failed host launch for retry', async () => {
  vi.mocked(readCommand).mockResolvedValue({ type: 'external_open', task_id: 'task', url: 'https://example.invalid/123' });
  const test = setup(); fireEvent.click(screen.getByText('Launch')); await screen.findByText('https://example.invalid/123'); expect(test.openExternal).not.toHaveBeenCalled();
  expect(readCommand).toHaveBeenCalledWith({ cmd: 'external_open_task', id: 'task' }, expect.any(AbortSignal));
  test.openExternal.mockRejectedValueOnce(new Error('Host unavailable')); fireEvent.click(screen.getByRole('button', { name: 'Open ticket' })); await screen.findByText('Host unavailable');
  fireEvent.click(screen.getByRole('button', { name: 'Open ticket' })); await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument()); expect(test.openExternal).toHaveBeenCalledTimes(2);
});
it.each([
  { type: 'external_open', task_id: 'other', url: 'https://example.invalid/123' },
  { type: 'external_open', task_id: 'task', url: 'javascript:alert(1)' },
  { type: 'error', message: 'Ticket missing' },
])('refuses mismatched, unsafe or failed URL resolution: $type $task_id', async (frame) => {
  vi.mocked(readCommand).mockResolvedValue(frame); const test = setup(); fireEvent.click(screen.getByText('Launch')); await screen.findByRole('alert'); expect(test.openExternal).not.toHaveBeenCalled(); expect(screen.queryByRole('button', { name: 'Open ticket' })).not.toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Retry ticket URL' })).toBeEnabled();
});
it('cancels an ID lookup and ignores an eventual response without reopening the dialog or a browser window', async () => {
  let resolve!: (value: { type: string; task_id: string; url: string }) => void;
  vi.mocked(readCommand).mockReturnValue(new Promise((done) => { resolve = done; })); const test = setup(); fireEvent.click(screen.getByText('Launch')); fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(vi.mocked(readCommand).mock.calls[0]![1].aborted).toBe(true); await act(async () => { resolve({ type: 'external_open', task_id: 'task', url: 'https://example.invalid/123' }); await Promise.resolve(); }); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(test.openExternal).not.toHaveBeenCalled();
});
