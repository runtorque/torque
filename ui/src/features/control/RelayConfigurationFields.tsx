import { useState } from 'react';
import { relayFields, type RelayDraft, type RelayKey, type relayConfigurationView } from './relayConfiguration';
import styles from './ControlCenter.module.css';

export function RelayConfigurationFields({ view, draft, touched, onChange }: { view: ReturnType<typeof relayConfigurationView>; draft: RelayDraft; touched: string[]; onChange: (patch: Partial<RelayDraft>) => void }) {
  const [focused, setFocused] = useState<{ key: RelayKey; value: string | boolean } | null>(null);
  return <><div className={styles.formGrid}>{relayFields.map((field) => {
    const value = touched.includes(field.key) ? draft[field.key] : focused?.key === field.key ? focused.value : view.values[field.key];
    const origin = view.origins[field.key];
    const sourceLabel = origin === 'settings' ? 'Saved override' : origin === 'env' ? 'Inherited from environment' : origin === 'ee_connector.json' ? 'Inherited from ee_connector.json' : '';
    return <div key={field.key}><label className={styles.field}><span>{field.label}</span>{field.key === 'relay_enabled'
      ? <select aria-label={field.label} value={value ? 'on' : 'off'} onFocus={() => setFocused({ key: field.key, value })} onBlur={() => setFocused(null)} onChange={(event) => { const next = event.target.value === 'on'; if (focused?.key === field.key) setFocused({ key: field.key, value: next }); onChange({ relay_enabled: next }); }}><option value="off">Disabled</option><option value="on">Enabled</option></select>
      : <input aria-label={field.label} value={typeof value === 'string' ? value : ''} placeholder={view.placeholders[field.key] || 'Inherit / default'} onFocus={() => setFocused({ key: field.key, value })} onBlur={() => setFocused(null)} onChange={(event) => { const next = event.target.value; if (focused?.key === field.key) setFocused({ key: field.key, value: next }); onChange({ [field.key]: next }); }} />}</label>{sourceLabel ? <small>{sourceLabel}</small> : null}</div>;
  })}</div><p className={styles.note}>Leave text fields empty to inherit file or environment configuration. Only edited fields are saved. Private keys are referenced by path.</p>{view.origins.relay_enabled === 'env' ? <p className={styles.note}>Relay is enabled by the environment. Saving Disabled clears the settings override; turn off the environment flag to disable Relay.</p> : null}</>;
}
