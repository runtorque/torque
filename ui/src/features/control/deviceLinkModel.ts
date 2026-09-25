import type { UnknownRecord } from '../../protocol';
// Served alongside the existing xterm assets in browser, dev-proxy and desktop modes.
const qrScriptUrl = '/static/js/vendor/qrcode-generator.js';
const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
export function deviceLinkGate(resolved: unknown) {
  const config = record(record(resolved).config); const url = typeof config.relay_url === 'string' ? config.relay_url.trim() : '';
  const reason = config.enabled !== true ? 'Enable and save Relay to generate a device link.' : !url ? 'Set and save the Relay URL to generate a device link.' : '';
  return { reason, url, key: JSON.stringify([config.enabled === true, url, config.daemon_id, config.credential_id]) };
}
export function deviceLinkExpiry(raw: string, now: number) {
  const time = Date.parse(raw); if (!Number.isFinite(time)) return { relative: '', dateTime: '' };
  const seconds = Math.round((time - now) / 1000);
  if (seconds <= 0) return { relative: 'Expired', dateTime: new Date(time).toISOString() };
  const amount = seconds < 60 ? seconds : seconds < 3600 ? Math.round(seconds / 60) : Math.round(seconds / 3600);
  const unit = seconds < 60 ? 'second' : seconds < 3600 ? 'minute' : 'hour';
  return { relative: `Expires in ${amount} ${unit}${amount === 1 ? '' : 's'}`, dateTime: new Date(time).toISOString() };
}
interface Qr { addData: (value: string) => void; make: () => void; getModuleCount: () => number; isDark: (row: number, column: number) => boolean }
type Generator = (version: number, correction: string) => Qr;
let encoder: Promise<Generator> | null = null;
function loadEncoder(): Promise<Generator> {
  const loaded = (window as unknown as { qrcode?: Generator }).qrcode;
  if (typeof loaded === 'function') return Promise.resolve(loaded);
  if (encoder) return encoder;
  encoder = new Promise<Generator>((resolve, reject) => {
    const script = document.createElement('script'); script.src = qrScriptUrl; script.async = true;
    const fail = () => { window.clearTimeout(timer); script.remove(); encoder = null; reject(new Error('QR encoder unavailable')); };
    const timer = window.setTimeout(fail, 15_000);
    script.onerror = fail;
    script.onload = () => {
      const generator = (window as unknown as { qrcode?: Generator }).qrcode;
      if (typeof generator !== 'function') { fail(); return; }
      window.clearTimeout(timer); resolve(generator);
    };
    document.head.append(script);
  });
  return encoder;
}
/** Encodes locally with the pinned Classic vendor; only numeric SVG path data leaves this function. */
export async function deviceLinkQr(value: string) {
  const generate = await loadEncoder(); const qr = generate(0, 'M'); qr.addData(value); qr.make();
  const count = qr.getModuleCount(); let path = '';
  for (let row = 0; row < count; row += 1) for (let col = 0; col < count; col += 1) if (qr.isDark(row, col)) path += `M${col + 4},${row + 4}h1v1h-1z`;
  return { size: count + 8, path };
}
