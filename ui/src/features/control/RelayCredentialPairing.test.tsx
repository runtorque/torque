import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RelayCredentialPairing } from './RelayCredentialPairing';
import { relayCredentialGate } from './relayConfiguration';
import { readCommand } from '../../protocol/http';
import type { AuxiliaryFrame } from '../../protocol';
vi.mock('../../protocol/http', () => ({ readCommand: vi.fn() }));
const read = vi.mocked(readCommand);
const config = { config: { relay_url: 'https://relay.invalid', daemon_id: 'daemon-1', credential_id: '' } };
const resolved = vi.fn(); const busy = vi.fn();
const base = { resolved: config, onResolved: resolved, onBusyChange: busy };
beforeEach(() => { read.mockReset(); resolved.mockReset(); busy.mockReset(); });
afterEach(() => { vi.useRealTimers(); });
const token = () => screen.getByLabelText('One-time pairing token');
const fill = () => fireEvent.change(token(), { target: { value: '  transient-token  ' } });
const pair = () => fireEvent.click(screen.getByRole('button', { name: 'Pair daemon credential' }));
function pending() { let finish!: (value: AuxiliaryFrame) => void; read.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; })); return async (value: AuxiliaryFrame) => { await act(async () => { finish(value); await Promise.resolve(); }); }; }
const success: AuxiliaryFrame = { type: 'daemon_credential', ok: true, credential_id: 'new-credential', daemon_id: 'daemon-1', private_key_path: '/private/tmp/key.pem', owner_user_id: 'owner-1', provenance: { private_key_path: 'local_keygen' }, relay_config: { config: { ...config.config, credential_id: 'new-credential' } } };
it.each([
  [{}, 'Relay URL and daemon ID'],
  [{ config: { relay_url: 'https://relay.invalid' } }, 'daemon ID'],
  [{ config: { daemon_id: 'daemon-1' } }, 'Relay URL'],
])('requires effective saved configuration %j', (input, reason) => {
  render(<RelayCredentialPairing {...base} resolved={input} />); fill(); expect(screen.getByRole('status')).toHaveTextContent(String(reason)); expect(screen.getByRole('button', { name: 'Pair daemon credential' })).toBeDisabled(); expect(read).not.toHaveBeenCalled();
});
it('resolves inherited sources and honors an explicitly empty effective override', () => {
  const sources = { relay_url: { value: ' https://relay.invalid ' }, daemon_id: { value: ' daemon-1 ' }, credential_id: { value: ' existing ' } };
  expect(relayCredentialGate({ sources })).toMatchObject({ reason: '', url: 'https://relay.invalid', daemonId: 'daemon-1', credentialId: 'existing' });
  expect(relayCredentialGate({ sources, config: { relay_url: '' } }).reason).toContain('Relay URL');
});
it('sends one trimmed transient token and clears it only after a valid acknowledgement', async () => {
  const finish = pending(); render(<RelayCredentialPairing {...base} />); expect(screen.getByRole('button', { name: 'Pair daemon credential' })).toBeDisabled(); fill(); pair();
  expect(read.mock.calls[0]![0]).toEqual({ cmd: 'generate_daemon_credential', pairing_token: 'transient-token' }); expect(token()).toBeDisabled(); expect(token()).toHaveValue('  transient-token  ');
  expect(screen.getByRole('button', { name: 'Pairing credential…' })).toBeDisabled(); expect(busy).toHaveBeenLastCalledWith(true);
  await finish(success); expect(token()).toHaveValue(''); expect(screen.getByRole('status')).toHaveTextContent('generated and stored'); expect(screen.getByText('new-credential')).toBeVisible(); expect(screen.getByText('/private/tmp/key.pem')).toBeVisible(); expect(screen.getByText('owner-1')).toBeVisible();
  expect(resolved).toHaveBeenCalledExactlyOnceWith(success.relay_config); expect(busy).toHaveBeenLastCalledWith(false); expect(read).toHaveBeenCalledTimes(1);
});
it('requires a replacement confirmation, supports cancel, and invalidates it after config changes', async () => {
  const finish = pending(); const existing = { config: { ...config.config, credential_id: 'existing' } };
  const { rerender } = render(<RelayCredentialPairing {...base} resolved={existing} />); fill(); pair(); expect(read).not.toHaveBeenCalled(); expect(screen.getByRole('group')).toHaveTextContent('existing');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel replacement' })); expect(read).not.toHaveBeenCalled(); pair();
  rerender(<RelayCredentialPairing {...base} resolved={{ config: { ...existing.config, credential_id: 'other' } }} />); expect(screen.queryByRole('group')).not.toBeInTheDocument(); pair(); expect(screen.getByRole('group')).toHaveTextContent('other');
  fireEvent.click(screen.getByRole('button', { name: 'Generate replacement credential' })); await finish(success); expect(read).toHaveBeenCalledTimes(1);
});
it('retains the token and recovery values when Relay accepts but local Settings persistence fails', async () => {
  const finish = pending(); render(<RelayCredentialPairing {...base} />); fill(); pair();
  await finish({ type: 'daemon_credential', ok: false, error: 'settings_write_failed', credential_id: 'recover-id', private_key_path: '/tmp/recover.pem', detail: 'Disk unavailable' });
  expect(screen.getByRole('alert')).toHaveTextContent('Credential recovery required'); expect(screen.getByRole('alert')).toHaveTextContent('revoke the credential before retrying'); expect(screen.getByText('/tmp/recover.pem')).toBeVisible(); expect(screen.getByText('Disk unavailable')).toBeVisible(); expect(token()).toHaveValue('  transient-token  '); expect(resolved).not.toHaveBeenCalled();
});
it('retains rejected tokens and retries only after another explicit action', async () => {
  const first = pending(); const second = pending(); render(<RelayCredentialPairing {...base} />); fill(); pair(); await first({ type: 'daemon_credential', ok: false, message: 'Token expired', detail: 'Ask the administrator for a new token.' });
  expect(screen.getByRole('alert')).toHaveTextContent('Token expired'); expect(token()).toHaveValue('  transient-token  '); expect(read).toHaveBeenCalledTimes(1); pair(); await second(success); expect(read).toHaveBeenCalledTimes(2);
});
it.each([{ type: 'ok' }, { ...success, daemon_id: 'different' }, { ...success, private_key_path: '' }])('reports an unknown outcome for malformed acknowledgement %j', async (frame) => {
  const finish = pending(); render(<RelayCredentialPairing {...base} />); fill(); pair(); await finish(frame); expect(screen.getByRole('alert')).toHaveTextContent('outcome is unknown'); expect(token()).toHaveValue('  transient-token  '); expect(resolved).not.toHaveBeenCalled();
});
it('bounds pending operations, retains the token and ignores late success', async () => {
  vi.useFakeTimers(); const finish = pending(); render(<RelayCredentialPairing {...base} />); fill(); pair();
  await act(async () => { vi.advanceTimersByTime(30_000); await Promise.resolve(); }); expect(read.mock.calls[0]![1]?.aborted).toBe(true); expect(screen.getByRole('alert')).toHaveTextContent('timed out'); await finish(success); expect(token()).toHaveValue('  transient-token  '); expect(resolved).not.toHaveBeenCalled(); expect(read).toHaveBeenCalledTimes(1); expect(busy).toHaveBeenLastCalledWith(false);
});
it('cancels on unmount without accepting or replaying a late acknowledgement', async () => {
  const finish = pending(); const { unmount } = render(<RelayCredentialPairing {...base} />); fill(); pair(); unmount(); expect(read.mock.calls[0]![1]?.aborted).toBe(true); await finish(success); expect(resolved).not.toHaveBeenCalled(); expect(busy).toHaveBeenLastCalledWith(false);
});
it('disables pairing while Settings is saving and preserves token/focus through routine refresh', () => {
  const { rerender } = render(<RelayCredentialPairing {...base} />); fill(); token().focus(); const original = token();
  rerender(<RelayCredentialPairing {...base} resolved={{ ...config }} />); expect(token()).toBe(original); expect(token()).toHaveFocus(); expect(token()).toHaveValue('  transient-token  ');
  rerender(<RelayCredentialPairing {...base} disabled />); expect(token()).toBeDisabled(); expect(screen.getByRole('button', { name: 'Pair daemon credential' })).toBeDisabled(); expect(read).not.toHaveBeenCalled();
});
