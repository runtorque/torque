import { useState } from 'react';
import catalogs from './digestEventCatalogs.json';
import { Button } from '../../design/primitives';
import styles from './ControlCenter.module.css';

function eventLabel(kind: string): string {
  const labels: Record<string, string> = { ask_created: 'Question created', perceived_empty_episode: 'Empty episode', worker_boot_doa: 'Worker boot failed' };
  return labels[kind] || kind.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
}
export function DigestEventSetting({ kind, label, value, onChange }: { kind: 'engineer' | 'architect'; label: string; value: string[]; onChange: (next: string[]) => void }) {
  const catalog = catalogs[kind]; const title = kind === 'engineer' ? 'Engineer' : 'Architect';
  const unknown = value.filter((item) => !catalog.mandatory.includes(item) && !catalog.optional.includes(item));
  const [name, setName] = useState('');
  const candidate = name.trim(); const canAdd = Boolean(candidate) && !catalog.mandatory.includes(candidate) && !value.includes(candidate);
  const add = () => { if (!canAdd) return; onChange([...value.filter((item) => !catalog.mandatory.includes(item)), candidate]); setName(''); };
  const [extra, setExtra] = useState(() => [...new Set(unknown)]);
  if (unknown.some((item) => !extra.includes(item))) setExtra([...new Set([...extra, ...unknown])]);
  const choices = (items: string[]) => <div className={styles.digestEventChoices}>{items.map((item) => <label key={item}><input type="checkbox" checked={value.includes(item)} onChange={(event) => {
    const optional = value.filter((entry) => !catalog.mandatory.includes(entry));
    onChange(event.target.checked ? [...new Set([...optional, item])] : optional.filter((entry) => entry !== item));
  }} /><span>{eventLabel(item)}</span></label>)}</div>;
  return <fieldset aria-label={label} className={styles.digestEvents}><legend>{label}</legend><p>Always included</p><ul aria-label={`Events always included in ${title} digests`} className={styles.mandatoryEvents}>{catalog.mandatory.map((item) => <li key={item}>{eventLabel(item)}</li>)}</ul><p>Optional events</p>{choices(catalog.optional)}{extra.length ? <><p>Additional events</p>{choices(extra)}</> : null}<div className={styles.digestEventCustom}><label className={styles.field}><span>Additional {title} event name</span><input value={name} onChange={(event) => setName(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); add(); } }} placeholder="extension_event" /></label><Button type="button" isDisabled={!canAdd} onPress={add}>Add {title} event</Button></div>{catalog.mandatory.includes(candidate) ? <small>This event is always included.</small> : null}<small>Mandatory events are always included. Clearing all optional events keeps that mandatory set. Changes apply when you save.</small></fieldset>;
}
