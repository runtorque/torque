import { useId } from 'react';
import { Button } from '../../design/primitives';
import styles from './ControlCenter.module.css';

export function AiSecretSetting({ label, configured, last4, value, clearing, onChange, onClear, onKeep }: {
  label: string; configured: boolean; last4: string; value: string; clearing: boolean;
  onChange: (value: string) => void; onClear: () => void; onKeep: () => void;
}) {
  const statusId = useId();
  const status = clearing ? 'Will clear on save.' : value.trim() ? 'New key will be saved once.' : configured ? `Configured${last4 ? ` (••••${last4})` : ''}. Blank keeps the saved key.` : 'Not configured.';
  return <div><label className={styles.field}><span>{label} key</span><input type="password" value={value} onChange={(event) => onChange(event.target.value)} aria-describedby={statusId} placeholder={clearing ? 'Will clear on save' : 'Leave blank to keep current'} autoComplete="new-password" spellCheck={false} /></label><small id={statusId} role="status">{status}</small><Button tone={clearing ? 'quiet' : 'danger'} onPress={clearing ? onKeep : onClear}>{clearing ? `Keep ${label} key` : `Clear ${label} key`}</Button></div>;
}
