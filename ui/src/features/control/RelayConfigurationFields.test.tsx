import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { RelayConfigurationFields } from './RelayConfigurationFields';
import { relayConfigurationView } from './relayConfiguration';

describe('resolved Relay configuration', () => {
  it('uses source values and config precedence without surfacing unknown or inline-key fields', () => {
    const result = relayConfigurationView({ relay_enabled: false, relay_url: 'stale' }, { config: { enabled: true, relay_url: 'wss://effective.invalid', private_key_pem: 'DO_NOT_DISPLAY' }, sources: { enabled: { source: 'env', value: false }, relay_url: { source: 'env', value: 'wss://source.invalid' }, daemon_id: { source: 'settings', value: 'saved-daemon' }, credential_id: { source: 'env', value: 'credential' }, private_key_path: { source: 'ee_connector.json', value: '' }, private_key_pem: { source: 'ee_connector.json', value: 'DO_NOT_DISPLAY' } } });
    expect(result.values).toEqual({ relay_enabled: true, relay_url: '', relay_daemon_id: 'saved-daemon', relay_credential_id: '', relay_private_key_path: '' });
    expect(result.placeholders.relay_url).toBe('wss://effective.invalid'); expect(result.placeholders.relay_credential_id).toBe('credential');
    expect(JSON.stringify(result)).not.toContain('DO_NOT_DISPLAY');
  });
  it('retains raw settings when the resolved contract is unavailable and handles source-only booleans', () => {
    expect(relayConfigurationView({ relay_enabled: true, relay_url: 'wss://raw.invalid' }, null).values).toMatchObject({ relay_enabled: true, relay_url: 'wss://raw.invalid' });
    expect(relayConfigurationView({}, { sources: { enabled: { source: 'env', value: true } } }).values.relay_enabled).toBe(true);
  });
  it('keeps the latest focused edit after acknowledgement and then adopts the applied value on blur', () => {
    const view = relayConfigurationView({}, { config: { enabled: false }, sources: { relay_url: { source: 'settings', value: 'wss://remote.invalid' } } });
    function Form() {
      const [draft, setDraft] = useState(view.values); const [touched, setTouched] = useState<string[]>([]);
      return <><RelayConfigurationFields view={view} draft={draft} touched={touched} onChange={(patch) => { setDraft((before) => ({ ...before, ...patch })); setTouched(Object.keys(patch)); }} /><button onClick={() => setTouched([])}>Acknowledge</button></>;
    }
    render(<Form />); const url = screen.getByLabelText<HTMLInputElement>('Relay URL'); act(() => url.focus()); fireEvent.change(url, { target: { value: 'wss://edited.invalid' } });
    fireEvent.click(screen.getByRole('button', { name: 'Acknowledge' })); expect(url).toHaveValue('wss://edited.invalid'); expect(url).toHaveFocus();
    fireEvent.blur(url); expect(url).toHaveValue('wss://remote.invalid');
  });
});
