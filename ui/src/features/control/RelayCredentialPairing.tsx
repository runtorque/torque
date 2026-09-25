import { useEffect, useRef, useState } from 'react';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { readCommand } from '../../protocol/http';
import styles from './ControlCenter.module.css';
import { relayCredentialGate } from './relayConfiguration';

const text = (value: unknown) => typeof value === 'string' ? value.trim() : '';
const record = (value: unknown): UnknownRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
interface Result { kind: 'success' | 'error' | 'recovery'; message: string; detail?: string; credentialId?: string; privateKeyPath?: string; owner?: string; provenance?: string }
export function RelayCredentialPairing({ resolved, disabled = false, onBusyChange, onResolved }: { resolved: unknown; disabled?: boolean; onBusyChange: (busy: boolean) => void; onResolved: (config: UnknownRecord) => void }) {
  const gate = relayCredentialGate(resolved);
  const [token, setToken] = useState(''); const [confirmation, setConfirmation] = useState<string | null>(null);
  const [pending, setPending] = useState(false); const [result, setResult] = useState<Result | null>(null);
  const active = useRef<{ controller: AbortController; timer: number } | null>(null);
  const confirming = confirmation === gate.key;
  if (confirmation !== null && !confirming) setConfirmation(null);
  useEffect(() => () => { active.current?.controller.abort(); window.clearTimeout(active.current?.timer); if (active.current) onBusyChange(false); active.current = null; }, [onBusyChange]);
  const unavailable = disabled || pending || Boolean(gate.reason) || !token.trim();
  async function pair(confirmed = false) {
    if (unavailable || active.current) return;
    if (gate.credentialId && (!confirmed || !confirming)) { setResult(null); setConfirmation(gate.key); return; }
    const controller = new AbortController();
    setConfirmation(null); setResult(null); setPending(true); onBusyChange(true);
    const timer = window.setTimeout(() => {
      if (active.current?.controller !== controller) return;
      controller.abort(); active.current = null; setPending(false); onBusyChange(false);
      setResult({ kind: 'error', message: 'Pairing timed out; its outcome is unknown. Review the refreshed Relay configuration before trying again.' });
    }, 30_000);
    active.current = { controller, timer };
    try {
      const frame = await readCommand({ cmd: 'generate_daemon_credential', pairing_token: token.trim() }, controller.signal);
      if (controller.signal.aborted || active.current?.controller !== controller) return;
      if (frame.type !== 'daemon_credential') throw new Error('The daemon did not acknowledge this pairing request.');
      const credentialId = text(frame.credential_id); const privateKeyPath = text(frame.private_key_path);
      if (frame.ok === true) {
        if (!credentialId || !privateKeyPath || (text(frame.daemon_id) && text(frame.daemon_id) !== gate.daemonId)) throw new Error('The daemon returned an incomplete or mismatched credential acknowledgement.');
        setToken(''); setResult({ kind: 'success', message: 'Daemon credential generated and stored in Settings.', credentialId, privateKeyPath, owner: text(frame.owner_user_id), provenance: record(frame.provenance).private_key_path === 'local_keygen' ? 'Local key generation → Settings' : 'Stored in Settings' });
        const config = record(frame.relay_config); if (Object.keys(config).length) onResolved(config);
      } else if (frame.error === 'settings_write_failed' && credentialId && privateKeyPath) {
        setResult({ kind: 'recovery', message: text(frame.message) || 'Relay accepted the credential, but Torque could not save it to Settings.', detail: text(frame.detail), credentialId, privateKeyPath });
      } else setResult({ kind: 'error', message: text(frame.message) || 'Could not generate a daemon credential.', detail: text(frame.detail) });
    } catch (cause) {
      if (!controller.signal.aborted && active.current?.controller === controller) setResult({ kind: 'error', message: `${cause instanceof Error ? cause.message : 'Pairing request failed'} Its outcome is unknown. Review the refreshed Relay configuration before trying again.` });
    } finally {
      window.clearTimeout(timer);
      if (active.current?.controller === controller) { active.current = null; setPending(false); onBusyChange(false); }
    }
  }
  return <section className={styles.relayPairing} aria-label="Daemon credential pairing">
    <h4>Pair daemon credential</h4>
    <p>Paste a one-time pairing token from your Relay administrator. Pairing uses the saved effective Relay configuration.</p>
    <label className={styles.field}><span>One-time pairing token</span><input type="password" autoComplete="off" spellCheck={false} value={token} disabled={disabled || pending} onChange={(event) => { setToken(event.target.value); setConfirmation(null); }} /></label>
    {gate.reason ? <p role="status">{gate.reason}</p> : <p>Daemon: <code>{gate.daemonId}</code> · Relay: <code>{gate.url}</code></p>}
    {confirming ? <div role="group" aria-label="Confirm credential replacement"><p>This daemon already has credential <code>{gate.credentialId}</code>. Generating a new credential makes it active on Relay and can disrupt the current connection.</p><div className={styles.settingsActions}><Button isDisabled={disabled || pending} onPress={() => setConfirmation(null)}>Cancel replacement</Button><Button isDisabled={unavailable} onPress={() => { void pair(true); }}>Generate replacement credential</Button></div></div> : <div className={styles.settingsActions}><Button isDisabled={unavailable} onPress={() => { void pair(); }}>{pending ? 'Pairing credential…' : 'Pair daemon credential'}</Button></div>}
    {pending ? <p role="status">Waiting for the daemon to acknowledge pairing…</p> : null}
    {result ? <div role={result.kind === 'success' ? 'status' : 'alert'}><strong>{result.kind === 'recovery' ? 'Credential recovery required' : result.kind === 'success' ? result.provenance : 'Pairing failed'}</strong><p>{result.message}</p>{result.detail ? <p>{result.detail}</p> : null}{result.credentialId ? <dl><dt>Credential ID</dt><dd><code>{result.credentialId}</code></dd><dt>Private key path</dt><dd><code>{result.privateKeyPath}</code></dd>{result.owner ? <><dt>Owner user</dt><dd><code>{result.owner}</code></dd></> : null}</dl> : null}{result.kind === 'recovery' ? <p>Set the Relay credential ID and private key path to these values and save, or ask your Relay administrator to revoke the credential before retrying.</p> : null}</div> : null}
  </section>;
}
