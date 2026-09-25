import { useEffect, useRef, useState } from 'react';
import { Button } from '../../design/primitives';
import { readCommand } from '../../protocol/http';
import { deviceLinkExpiry, deviceLinkGate, deviceLinkQr } from './deviceLinkModel';
import styles from './ControlCenter.module.css';
interface Secret { id: string; code: string; url: string; expiresAt: string; relayUrl: string }
function LinkDisplay({ secret, dismiss }: { secret: Secret; dismiss: () => void }) {
  const [qr, setQr] = useState<{ size: number; path: string } | null>(null); const [qrError, setQrError] = useState(false); const [retry, setRetry] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  useEffect(() => {
    let cancelled = false;
    void deviceLinkQr(secret.url).then((value) => { if (!cancelled) { setQr(value); setQrError(false); } }).catch(() => { if (!cancelled) setQrError(true); });
    return () => { cancelled = true; };
  }, [secret.url, retry]);
  const expiry = deviceLinkExpiry(secret.expiresAt, now);
  return <div className={styles.deviceLinkDisplay}>
    {qr ? <svg className={styles.deviceLinkQr} role="img" aria-label="Device link QR code" viewBox={`0 0 ${qr.size} ${qr.size}`} shapeRendering="crispEdges"><rect width={qr.size} height={qr.size} fill="white" /><path d={qr.path} fill="black" /></svg> : qrError ? <div role="status">QR code unavailable. You can still select and copy the link.<Button onPress={() => { setQrError(false); setRetry((value) => value + 1); }}>Retry QR code</Button></div> : <p role="status">Preparing QR code…</p>}
    <dl><dt>Relay</dt><dd>{secret.relayUrl}</dd><dt>Link</dt><dd><code tabIndex={0} aria-label="Device link URL">{secret.url}</code></dd><dt>Code</dt><dd><code tabIndex={0} aria-label="Device link code">{secret.code}</code></dd></dl>
    {expiry.relative ? <p>{expiry.relative}</p> : null}{secret.expiresAt ? <p>Expires: {expiry.dateTime ? <time dateTime={expiry.dateTime}>{secret.expiresAt}</time> : secret.expiresAt}</p> : null}
    <p>Shown once — scan or copy now. Dismissing clears it; generate again for a new link.</p><Button onPress={dismiss}>Dismiss device link</Button>
  </div>;
}
export function RelayDeviceLink({ resolved, disabled = false }: { resolved: unknown; disabled?: boolean }) {
  const gate = deviceLinkGate(resolved);
  const [confirmation, setConfirmation] = useState<string | null>(null); const [secret, setSecret] = useState<Secret | null>(null);
  const [pendingKey, setPendingKey] = useState('');
  const pending = Boolean(pendingKey); const [error, setError] = useState('');
  const active = useRef<{ controller: AbortController; timer: number; key: string } | null>(null);
  const confirming = confirmation === gate.key;
  if (confirmation !== null && !confirming) setConfirmation(null);
  if (pendingKey && pendingKey !== gate.key) { setPendingKey(''); setError('Relay configuration changed during generation. The previous request was not replayed. Review the configuration before generating again.'); }
  useEffect(() => () => { active.current?.controller.abort(); window.clearTimeout(active.current?.timer); active.current = null; }, []);
  useEffect(() => {
    if (active.current && active.current.key !== gate.key) {
      active.current.controller.abort(); window.clearTimeout(active.current.timer); active.current = null;
    }
  }, [gate.key]);
  const unavailable = disabled || pending || Boolean(gate.reason);
  const request = () => { if (unavailable || active.current) return; setSecret(null); setError(''); setConfirmation(gate.key); };
  async function generate() {
    if (unavailable || !confirming || active.current) return;
    const controller = new AbortController(); setConfirmation(null); setSecret(null); setPendingKey(gate.key); setError('');
    const timer = window.setTimeout(() => {
      if (active.current?.controller !== controller) return;
      controller.abort(); active.current = null; setPendingKey(''); setError('Device-link generation timed out; its outcome is unknown. Generate again only when ready.');
    }, 30_000);
    active.current = { controller, timer, key: gate.key };
    try {
      const frame = await readCommand({ cmd: 'generate_relay_device_link', confirm: true }, controller.signal);
      if (controller.signal.aborted || active.current?.controller !== controller) return;
      if (frame.type !== 'relay_device_link') throw new Error('The daemon did not acknowledge device-link generation.');
      if (frame.ok !== true) {
        const message = typeof frame.message === 'string' && frame.message ? frame.message : frame.status === 'confirmation_required' ? 'Confirmation is required to generate a device link.' : 'Could not generate a device link.';
        setError(`${message}${frame.error === 'relay_disabled' || frame.error === 'relay_not_started' ? ' Enable and start Relay, then try again.' : ''}`); return;
      }
      if (typeof frame.code !== 'string' || !frame.code || typeof frame.establish_url !== 'string' || !frame.establish_url) throw new Error('The daemon returned an incomplete device link.');
      const url = new URL(frame.establish_url); if (!['http:', 'https:'].includes(url.protocol)) throw new Error('The daemon returned an invalid device link.');
      // Keep the one-shot result only in this mounted view; never dispatch it to shared response caches.
      setSecret({ id: crypto.randomUUID(), code: frame.code, url: frame.establish_url, expiresAt: typeof frame.expires_at === 'string' ? frame.expires_at : '', relayUrl: gate.url });
    } catch {
      if (!controller.signal.aborted && active.current?.controller === controller) setError('Device-link generation could not be acknowledged; its outcome is unknown. Generate again only when ready.');
    } finally { window.clearTimeout(timer); if (active.current?.controller === controller) { active.current = null; setPendingKey(''); } }
  }
  return <section className={styles.relayPairing} aria-label="Relay device link"><h4>One-time device link</h4>
    {gate.reason ? <p role="status">{gate.reason}</p> : null}
    {confirming ? <div role="group" aria-label="Confirm device link"><p>Generate a single-use, short-lived credential for connecting a device to this workspace?</p><div className={styles.settingsActions}><Button isDisabled={disabled} onPress={() => setConfirmation(null)}>Cancel device link</Button><Button isDisabled={unavailable} onPress={() => { void generate(); }}>Confirm and generate device link</Button></div></div> : <Button isDisabled={unavailable} onPress={request}>{pending ? 'Generating device link…' : 'Generate one-time device link'}</Button>}
    {pending ? <p role="status">Waiting for the daemon to generate a device link…</p> : null}{error ? <p role="alert">{error}</p> : null}
    {secret ? <LinkDisplay key={secret.id} secret={secret} dismiss={() => setSecret(null)} /> : null}
  </section>;
}
