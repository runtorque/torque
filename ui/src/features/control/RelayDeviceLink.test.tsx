import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RelayDeviceLink } from './RelayDeviceLink';
import { readCommand } from '../../protocol/http';
import { deviceLinkQr } from './deviceLinkModel';
import type * as DeviceLinkModel from './deviceLinkModel';
import type { AuxiliaryFrame } from '../../protocol';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
vi.mock('./deviceLinkModel', async (original) => ({ ...await original<typeof DeviceLinkModel>(), deviceLinkQr: vi.fn() }));
const read = vi.mocked(readCommand); const qr = vi.mocked(deviceLinkQr);
const resolved = { config: { enabled: true, relay_url: 'https://relay.invalid', daemon_id: 'one' } };
const success: AuxiliaryFrame = { type: 'relay_device_link', ok: true, code: 'transient-code', establish_url: 'https://relay.invalid/establish?code=transient-code', expires_at: '2030-01-01T01:00:00Z' };
beforeEach(() => { read.mockReset(); qr.mockReset(); qr.mockResolvedValue({ size: 29, path: 'M4,4h1v1h-1z' }); });
afterEach(() => vi.useRealTimers());
function pending() { let finish!: (value: AuxiliaryFrame) => void; read.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; })); return async (value = success) => { await act(async () => { finish(value); await Promise.resolve(); }); }; }
const request = () => fireEvent.click(screen.getByRole('button', { name: 'Generate one-time device link' }));
const confirm = () => fireEvent.click(screen.getByRole('button', { name: 'Confirm and generate device link' }));
it('does not generate on open, requires effective enabled Relay and URL, and respects Settings saving', () => {
  const { rerender } = render(<RelayDeviceLink resolved={{}} />); expect(screen.getByRole('button')).toBeDisabled(); expect(screen.getByRole('status')).toHaveTextContent('Enable and save');
  rerender(<RelayDeviceLink resolved={{ config: { enabled: true } }} />); expect(screen.getByRole('button')).toBeDisabled(); expect(screen.getByRole('status')).toHaveTextContent('Relay URL');
  rerender(<RelayDeviceLink resolved={resolved} disabled />); expect(screen.getByRole('button')).toBeDisabled(); expect(read).not.toHaveBeenCalled();
});
it('confirms or cancels explicitly, blocks duplicate generation, and displays local QR with selectable details', async () => {
  const finish = pending(); render(<RelayDeviceLink resolved={resolved} />); expect(read).not.toHaveBeenCalled(); request(); expect(read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel device link' })); expect(read).not.toHaveBeenCalled(); request(); confirm();
  expect(read.mock.calls[0]![0]).toEqual({ cmd: 'generate_relay_device_link', confirm: true }); expect(screen.getByRole('button', { name: 'Generating device link…' })).toBeDisabled();
  await finish(); expect(screen.getByLabelText('Device link URL')).toHaveTextContent(String(success.establish_url)); expect(screen.getByLabelText('Device link code')).toHaveAttribute('tabindex', '0'); expect(await screen.findByRole('img', { name: 'Device link QR code' })).toHaveAttribute('viewBox', '0 0 29 29'); expect(qr).toHaveBeenCalledExactlyOnceWith(success.establish_url);
  expect(document.querySelector('time')).toHaveAttribute('datetime', '2030-01-01T01:00:00.000Z'); expect(read).toHaveBeenCalledTimes(1);
});
it('retains accepted secret DOM and focus through routine refresh but removes it on dismissal and reopen', async () => {
  const finish = pending(); const { rerender, unmount } = render(<RelayDeviceLink resolved={resolved} />); request(); confirm(); await finish();
  const url = screen.getByLabelText('Device link URL'); url.focus(); rerender(<RelayDeviceLink resolved={{ config: { ...resolved.config, enabled: false } }} />); expect(screen.getByLabelText('Device link URL')).toBe(url); expect(url).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss device link' })); expect(screen.queryByText('transient-code')).not.toBeInTheDocument(); expect(screen.queryByRole('img')).not.toBeInTheDocument();
  rerender(<RelayDeviceLink resolved={resolved} />); expect(screen.queryByLabelText('Device link URL')).not.toBeInTheDocument(); unmount(); render(<RelayDeviceLink resolved={resolved} />); expect(screen.queryByLabelText('Device link URL')).not.toBeInTheDocument(); expect(read).toHaveBeenCalledTimes(1);
});
it('drops the old display before requesting another link, even if confirmation is cancelled', async () => {
  const finish = pending(); render(<RelayDeviceLink resolved={resolved} />); request(); confirm(); await finish(); request(); expect(screen.queryByLabelText('Device link URL')).not.toBeInTheDocument(); fireEvent.click(screen.getByRole('button', { name: 'Cancel device link' })); expect(screen.queryByLabelText('Device link URL')).not.toBeInTheDocument(); expect(read).toHaveBeenCalledTimes(1);
});
it('invalidates pending confirmation and aborts pending results when the Relay target changes', async () => {
  const finish = pending(); const { rerender } = render(<RelayDeviceLink resolved={resolved} />); request(); rerender(<RelayDeviceLink resolved={{ config: { ...resolved.config, daemon_id: 'two' } }} />); expect(screen.queryByRole('group')).not.toBeInTheDocument(); request(); confirm();
  rerender(<RelayDeviceLink resolved={resolved} />); expect(read.mock.calls[0]![1]?.aborted).toBe(true); await finish(); expect(screen.getByRole('alert')).toHaveTextContent('configuration changed'); expect(screen.queryByLabelText('Device link URL')).not.toBeInTheDocument();
});
it('renders actionable refusal and generates again only after fresh confirmation', async () => {
  const failed = pending(); const ready = pending(); render(<RelayDeviceLink resolved={resolved} />); request(); confirm(); await failed({ type: 'relay_device_link', ok: false, error: 'relay_not_started', message: 'Relay unavailable' });
  expect(screen.getByRole('alert')).toHaveTextContent('Enable and start Relay'); expect(read).toHaveBeenCalledTimes(1); request(); expect(read).toHaveBeenCalledTimes(1); confirm(); await ready(); expect(screen.getByLabelText('Device link code')).toHaveTextContent('transient-code');
});
it.each([{ type: 'ok' }, { ...success, code: '' }, { ...success, establish_url: 'javascript:bad()' }, { ...success, establish_url: 'not a URL' }])('rejects malformed results without displaying secrets %j', async (value) => {
  const finish = pending(); render(<RelayDeviceLink resolved={resolved} />); request(); confirm(); await finish(value); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(screen.queryByLabelText('Device link code')).not.toBeInTheDocument(); expect(qr).not.toHaveBeenCalled();
});
it('times out without replay, ignores late success and aborts on close', async () => {
  vi.useFakeTimers(); const first = pending(); const second = pending(); const { unmount } = render(<RelayDeviceLink resolved={resolved} />); request(); confirm();
  await act(async () => { vi.advanceTimersByTime(30_000); await Promise.resolve(); }); expect(read.mock.calls[0]![1]?.aborted).toBe(true); await first(); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); expect(screen.queryByLabelText('Device link URL')).not.toBeInTheDocument(); expect(read).toHaveBeenCalledTimes(1);
  request(); confirm(); unmount(); expect(read.mock.calls[1]![1]?.aborted).toBe(true); await second(); expect(qr).not.toHaveBeenCalled();
});
it('keeps the selectable link on encoder failure and retries QR without minting another credential', async () => {
  qr.mockRejectedValueOnce(new Error('Unavailable')); const finish = pending(); render(<RelayDeviceLink resolved={resolved} />); request(); confirm(); await finish();
  expect(screen.getByRole('status')).toHaveTextContent('QR code unavailable'); expect(screen.getByLabelText('Device link URL')).toBeVisible(); fireEvent.click(screen.getByRole('button', { name: 'Retry QR code' })); await screen.findByRole('img', { name: 'Device link QR code' }); expect(read).toHaveBeenCalledTimes(1); expect(qr).toHaveBeenCalledTimes(2);
});
