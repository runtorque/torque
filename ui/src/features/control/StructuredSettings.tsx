import { useId, useState } from 'react';

import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { settingFields } from './settingsFields';
import styles from './ControlCenter.module.css';

function scalar(value: unknown): string { return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : ''; }

function labelFor(key: string): string { return settingFields[key]?.label || key.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase()); }

function StringListSetting({ value, onChange, label, descriptionId }: { value: unknown[]; onChange: (next: string[]) => void; label: string; descriptionId: string | undefined }) {
  // Keep a trailing newline in the editing buffer while normalizing the saved
  // list. Deriving this buffer from normalized values prevents typing Enter.
  const [draft, setDraft] = useState(() => value.map(scalar).join('\n'));
  return <textarea aria-label={label} aria-describedby={descriptionId} rows={3} value={draft} onChange={(event) => { setDraft(event.target.value); onChange(event.target.value.split('\n').filter((entry) => entry.trim())); }} placeholder="One value per line" />;
}

function SettingObject({ value, onChange, label }: { value: UnknownRecord; onChange: (next: UnknownRecord) => void; label: string }) {
  const [newKey, setNewKey] = useState('');
  return <fieldset><legend>{label}</legend><StructuredSettings value={value} onChange={onChange} prefix={label} onRemove={(key) => { const next = { ...value }; delete next[key]; onChange(next); }} /><label>New {label.toLocaleLowerCase()} key<input value={newKey} onChange={(event) => setNewKey(event.target.value)} /></label><Button isDisabled={!newKey.trim() || newKey.trim() in value} onPress={() => { onChange({ ...value, [newKey.trim()]: '' }); setNewKey(''); }}>Add entry</Button></fieldset>;
}

export function StructuredSettings({ value, onChange, prefix = '', omit = [], onRemove }: { value: UnknownRecord; onChange: (next: UnknownRecord) => void; prefix?: string; omit?: string[]; onRemove?: (key: string) => void }) {
  const instance = useId();
  return <div className={styles.structuredSettings}>{Object.entries(value).filter(([key]) => !omit.includes(key)).map(([key, item]) => {
    const field = settingFields[key]; const label = `${prefix ? `${prefix}: ` : ''}${labelFor(key)}`;
    const descriptionId = field?.description ? `${instance}-${key}-help` : undefined;
    const set = (next: unknown) => onChange({ ...value, [key]: next });
    if (item && typeof item === 'object' && !Array.isArray(item)) return <SettingObject key={key} value={item as UnknownRecord} onChange={set} label={label} />;
    const options = field?.options;
    const choice = options?.length ? [...options, ...(!options.some((option) => option.value === scalar(item)) ? [{ value: scalar(item), label: scalar(item) || 'Inherit' }] : [])] : null;
    return <div key={key}><label className={styles.field}><span>{label}</span>{typeof item === 'boolean' ? <select aria-label={label} aria-describedby={descriptionId} value={item ? 'true' : 'false'} onChange={(event) => set(event.target.value === 'true')}><option value="true">Enabled</option><option value="false">Disabled</option></select>
      : Array.isArray(item) ? <StringListSetting value={item as unknown[]} onChange={set} label={label} descriptionId={descriptionId} />
      : choice ? <select aria-label={label} aria-describedby={descriptionId} value={scalar(item)} onChange={(event) => set(event.target.value)}>{choice.map((option) => <option key={option.value} value={option.value}>{option.label || 'Inherit'}</option>)}</select>
      : typeof item === 'number' ? <input aria-label={label} aria-describedby={descriptionId} type="number" value={item} min={field?.min} max={field?.max} step={field?.step || 'any'} onChange={(event) => { if (Number.isFinite(event.target.valueAsNumber)) set(event.target.valueAsNumber); }} />
      : /instructions|nudge|prompt/.test(key) ? <textarea aria-label={label} aria-describedby={descriptionId} rows={4} value={scalar(item)} onChange={(event) => set(event.target.value)} />
      : <input aria-label={label} aria-describedby={descriptionId} value={scalar(item)} placeholder={field?.placeholder || (scalar(item) === '' ? 'Inherit / default' : '')} onChange={(event) => set(event.target.value)} />}
    </label>{field?.description ? <small id={descriptionId}>{field.description}</small> : null}{onRemove ? <Button tone="quiet" onPress={() => onRemove(key)}>Remove {label}</Button> : null}</div>;
  })}</div>;
}
