import type { UnknownRecord } from '../../protocol';
import { relaySettingsDraft } from './settingsModel';

export type RelayDraft = ReturnType<typeof relaySettingsDraft>;
export type RelayKey = keyof RelayDraft;
export const relayFields: { key: RelayKey; wire: string; label: string }[] = [
  { key: 'relay_enabled', wire: 'enabled', label: 'Relay' },
  { key: 'relay_url', wire: 'relay_url', label: 'Relay URL' },
  { key: 'relay_daemon_id', wire: 'daemon_id', label: 'Daemon ID' },
  { key: 'relay_credential_id', wire: 'credential_id', label: 'Credential ID' },
  { key: 'relay_private_key_path', wire: 'private_key_path', label: 'Private key path' },
];
function record(value: unknown): UnknownRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}; }
function text(value: unknown): string { return typeof value === 'string' ? value : ''; }
export function relayConfigurationView(settings: UnknownRecord, resolved: unknown) {
  const source = record(resolved); const config = record(source.config); const sources = record(source.sources);
  const values = relaySettingsDraft(settings); const placeholders: Partial<Record<RelayKey, string>> = {}; const origins: Partial<Record<RelayKey, string>> = {};
  for (const field of relayFields) {
    const origin = record(sources[field.wire]); const effective = field.wire in config ? config[field.wire] : origin.value;
    origins[field.key] = text(origin.source);
    if (field.key === 'relay_enabled') { if (typeof effective === 'boolean') values.relay_enabled = effective; }
    else if (field.wire in sources) {
      values[field.key] = origin.source === 'settings' ? text(origin.value) : '';
      placeholders[field.key] = origin.source === 'settings' ? '' : text(effective);
    }
  }
  return { values, placeholders, origins };
}


export function relayCredentialGate(resolved: unknown) {
  const source = record(resolved); const config = record(source.config); const sources = record(source.sources);
  const effective = (key: string) => text(key in config ? config[key] : record(sources[key]).value).trim();
  const url = effective('relay_url'); const daemonId = effective('daemon_id'); const credentialId = effective('credential_id');
  const reason = !url && !daemonId ? 'Set and save the Relay URL and daemon ID before pairing.' : !url ? 'Set and save the Relay URL before pairing.' : !daemonId ? 'Set and save the daemon ID before pairing.' : '';
  return { url, daemonId, credentialId, reason, key: JSON.stringify([url, daemonId, credentialId]) };
}
