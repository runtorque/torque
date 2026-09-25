import { afterEach, expect, it, vi } from 'vitest';
import { deviceLinkExpiry, deviceLinkGate, deviceLinkQr } from './deviceLinkModel';
afterEach(() => { Reflect.deleteProperty(window, 'qrcode'); vi.useRealTimers(); document.head.querySelectorAll('script').forEach((script) => script.remove()); });
it('formats relative and absolute expiry without pretending malformed dates are valid', () => {
  const now = Date.parse('2030-01-01T00:00:00Z');
  expect(deviceLinkExpiry('2030-01-01T00:00:01Z', now).relative).toBe('Expires in 1 second');
  expect(deviceLinkExpiry('2030-01-01T00:02:00Z', now).relative).toBe('Expires in 2 minutes');
  expect(deviceLinkExpiry('2030-01-01T01:00:00Z', now).relative).toBe('Expires in 1 hour');
  expect(deviceLinkExpiry('2030-01-01T00:00:00Z', now).relative).toBe('Expired');
  expect(deviceLinkExpiry('bad date', now)).toEqual({ relative: '', dateTime: '' });
});
it('gates from effective configuration and fingerprints target changes', () => {
  expect(deviceLinkGate({ config: { enabled: false, relay_url: 'https://relay.invalid' } }).reason).toContain('Enable');
  expect(deviceLinkGate({ config: { enabled: true, relay_url: ' ' } }).reason).toContain('URL');
  expect(deviceLinkGate({ config: { enabled: true, relay_url: ' https://relay.invalid ' } })).toMatchObject({ reason: '', url: 'https://relay.invalid' });
});
it('loads only the local pinned asset, encodes numeric paths with a quiet zone and reuses the encoder', async () => {
  const first = deviceLinkQr('credential-bearing URL'); const script = document.head.querySelector('script')!; expect(script.src).toContain('qrcode-generator.js'); expect(script.src).not.toContain('credential-bearing');
  const addData = vi.fn(); Object.assign(window, { qrcode: vi.fn(() => ({ addData, make: vi.fn(), getModuleCount: () => 2, isDark: (row: number, col: number) => row === col })) });
  script.dispatchEvent(new Event('load')); expect(await first).toEqual({ size: 10, path: 'M4,4h1v1h-1zM5,5h1v1h-1z' }); await deviceLinkQr('second'); expect(document.head.querySelectorAll('script')).toHaveLength(1); expect(addData.mock.calls).toEqual([['credential-bearing URL'], ['second']]);
});
