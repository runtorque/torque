import { useId, useState } from 'react';

import { NumericSettingInput } from './NumericSettingInput';
import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { providerChoices } from './providerChoices';
import { settingFields } from './settingsFields';
import { changedSettings, githubSettingsDefaults, settingsEqual } from './settingsModel';
import styles from './ControlCenter.module.css';

function scalar(value: unknown): string { return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : ''; }

function labelFor(key: string): string { return settingFields[key]?.label || key.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase()); }

function StringListSetting({ value, onChange, label, descriptionId }: { value: unknown[]; onChange: (next: string[]) => void; label: string; descriptionId: string | undefined }) {
  // Keep a trailing newline in the editing buffer while normalizing the saved
  // list. Deriving this buffer from normalized values prevents typing Enter.
  const [buffer, setBuffer] = useState(() => ({ value, draft: value.map(scalar).join('\n') }));
  if (!settingsEqual(buffer.value, value)) setBuffer({ value, draft: value.map(scalar).join('\n') });
  return <textarea aria-label={label} aria-describedby={descriptionId} rows={3} value={buffer.draft} onChange={(event) => { const next = event.target.value.split('\n').filter((entry) => entry.trim()); setBuffer({ value: next, draft: event.target.value }); onChange(next); }} placeholder="One value per line" />;
}

function SettingObject({ value, onChange, label, template }: { value: UnknownRecord; onChange: (next: UnknownRecord) => void; label: string; template?: UnknownRecord | undefined }) {
  const [newKey, setNewKey] = useState('');
  if (template) { const displayed = { ...template, ...value }; return <fieldset><legend>{label}</legend><StructuredSettings value={displayed} prefix={label} onChange={(next) => onChange({ ...value, ...changedSettings(displayed, next) })} /></fieldset>; }
  return <fieldset><legend>{label}</legend><StructuredSettings value={value} onChange={onChange} prefix={label} schema={false} onRemove={(key) => { const next = { ...value }; delete next[key]; onChange(next); }} /><label>New {label.toLocaleLowerCase()} key<input value={newKey} onChange={(event) => setNewKey(event.target.value)} /></label><Button isDisabled={!newKey.trim() || newKey.trim() in value} onPress={() => { onChange({ ...value, [newKey.trim()]: '' }); setNewKey(''); }}>Add entry</Button></fieldset>;
}

export function StructuredSettings({ value, onChange, prefix = '', omit = [], onRemove, providers = [], defaults = {}, schema = true }: { schema?: boolean; defaults?: UnknownRecord; providers?: unknown[]; value: UnknownRecord; onChange: (next: UnknownRecord) => void; prefix?: string; omit?: string[]; onRemove?: (key: string) => void }) {
  const instance = useId();
  return <div className={styles.structuredSettings}>{Object.entries(value).filter(([key]) => !omit.includes(key)).map(([key, item]) => {
    const field = schema ? settingFields[key] : undefined; const label = `${prefix ? `${prefix}: ` : ''}${schema ? labelFor(key) : key}`;
    const descriptionId = field?.description ? `${instance}-${key}-help` : undefined;
    const set = (next: unknown) => onChange({ ...value, [key]: next });
    const reset = key in defaults ? <Button tone="quiet" isDisabled={settingsEqual(item, defaults[key])} onPress={() => set(structuredClone(defaults[key]))}>Reset {label}</Button> : null;
    if (item && typeof item === 'object' && !Array.isArray(item)) return <div key={key}><SettingObject value={item as UnknownRecord} onChange={set} label={label} template={key === 'board_sync_github' ? githubSettingsDefaults : undefined} />{reset}</div>;
    const suggestions = schema ? providerChoices(providers, key, value) : [];
    const choicesId = `${instance}-${key}-choices`;
    const options = suggestions.length ? undefined : field?.options;
    const choice = options?.length ? [...options, ...(!options.some((option) => option.value === scalar(item)) ? [{ value: scalar(item), label: scalar(item) || 'Inherit' }] : [])] : null;
    return <div key={key}><label className={styles.field}><span>{label}</span>{typeof item === 'boolean' ? <select aria-label={label} aria-describedby={descriptionId} value={item ? 'true' : 'false'} onChange={(event) => set(event.target.value === 'true')}><option value="true">Enabled</option><option value="false">Disabled</option></select>
      : Array.isArray(item) ? <StringListSetting value={item as unknown[]} onChange={set} label={label} descriptionId={descriptionId} />
      : choice ? <select aria-label={label} aria-describedby={descriptionId} value={scalar(item)} onChange={(event) => set(typeof item === 'number' ? Number(event.target.value) : event.target.value)}>{choice.map((option) => <option key={option.value} value={option.value}>{option.label || 'Inherit'}</option>)}</select>
      : typeof item === 'number' || field?.kind === 'int' || field?.kind === 'float' ? <NumericSettingInput fieldKey={schema ? key : ''} aria-label={label} aria-describedby={descriptionId} value={typeof item === 'number' ? item : scalar(item)} onChange={set} />
      : /instructions|nudge|prompt/.test(key) ? <textarea aria-label={label} aria-describedby={descriptionId} rows={4} value={scalar(item)} onChange={(event) => set(event.target.value)} />
      : <input list={suggestions.length ? choicesId : undefined} aria-label={label} aria-describedby={descriptionId} value={scalar(item)} placeholder={field?.placeholder || (scalar(item) === '' ? 'Inherit / default' : '')} onChange={(event) => set(event.target.value)} />}
    {suggestions.length ? <datalist id={choicesId}>{suggestions.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}</datalist> : null}</label>{reset}{field?.description ? <small id={descriptionId}>{field.description}</small> : null}{onRemove ? <Button tone="quiet" onPress={() => onRemove(key)}>Remove {label}</Button> : null}</div>;
  })}</div>;
}
