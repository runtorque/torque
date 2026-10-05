import { useEffect, useRef, useState } from 'react';
import { useAppSelector } from '../../app/hooks';
import { selectConnection } from '../../app/store';
import { Button } from '../../design/primitives';
import { settingsRequest } from './settingsRequests';
import { record, text } from './agentClassesModel';
import { settingsEqual } from './settingsModel';
import styles from './ControlCenter.module.css';

const normalize = (values: string[]) => [...new Set(values.map((value) => value.trim()).filter(Boolean))];
export function SpecializationPicker({ group, value, onChange, label = 'Ordered specialization slugs', disabled = false }: { group: string; value: string[]; onChange: (value: string[]) => void; label?: string; disabled?: boolean }) {
  const connection = useAppSelector(selectConnection);
  const [requested, setRequested] = useState(false); const [retry, setRetry] = useState(0);
  const [catalog, setCatalog] = useState<{ group: string; names: string[] } | null>(null);
  const [read, setRead] = useState({ key: '', error: '' });
  const [choice, setChoice] = useState('');
  const [buffer, setBuffer] = useState({ value, text: value.join('\n') });
  if (!settingsEqual(buffer.value, value)) setBuffer({ value, text: value.join('\n') });
  const selected = normalize(value); const available = catalog?.group === group ? catalog.names : [];
  const options = available.filter((name) => !selected.includes(name));
  const select = useRef<HTMLSelectElement>(null); const list = useRef<HTMLOListElement>(null);
  const key = JSON.stringify([group, connection.reconnectCount, retry]);
  const pending = requested && !disabled && connection.status === 'connected' && read.key !== key;
  useEffect(() => {
    if (!requested || disabled || connection.status !== 'connected') return;
    const controller = new AbortController();
    void settingsRequest({ cmd: 'list_specializations', group }, controller.signal, false, 'Specialization catalog').then((frame) => {
      if (controller.signal.aborted) return;
      if (frame.type !== 'specializations' || frame.group !== group || !Array.isArray(frame.specializations)) throw new Error(text(frame.message, 'Specialization list did not match this group.'));
      const names = normalize(frame.specializations.map(record).filter((item) => item.shadowed !== true).map((item) => text(item.name)));
      setCatalog({ group, names }); setRead({ key, error: '' });
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setRead({ key, error: cause instanceof Error ? cause.message : 'Could not load specializations.' }); });
    return () => controller.abort();
  }, [group, requested, disabled, connection.status, key]);
  const focusRow = (name: string) => { const row = Array.from(list.current?.children ?? []).find((item) => (item as HTMLElement).dataset.specialization === name); if (row instanceof HTMLElement) row.focus(); else select.current?.focus(); };
  const move = (index: number, direction: number) => { const next = [...selected]; const name = next[index]!; next.splice(index, 1); next.splice(index + direction, 0, name); focusRow(name); onChange(next); };
  const remove = (index: number) => { focusRow(selected[index + 1] ?? selected[index - 1] ?? ''); onChange(selected.filter((_, position) => position !== index)); };
  return <div className={styles.specializationPicker}>
    <p>First selected is primary. Changes are applied when you save.</p>
    <ol ref={list} aria-label="Selected specializations">{selected.map((name, index) => <li key={name} data-specialization={name} tabIndex={-1}><span>{name}{index === 0 ? <strong> · Primary</strong> : null}{catalog?.group === group && !available.includes(name) ? <small> · unavailable in this project</small> : null}</span><div><Button aria-label={`Move ${name} up`} isDisabled={disabled || index === 0} onPress={() => move(index, -1)}>↑</Button><Button aria-label={`Move ${name} down`} isDisabled={disabled || index === selected.length - 1} onPress={() => move(index, 1)}>↓</Button><Button aria-label={`Remove ${name}`} isDisabled={disabled} onPress={() => remove(index)}>Remove</Button></div></li>)}</ol>
    {!selected.length ? <p>No specializations selected.</p> : null}
    <div className={styles.specializationControls}><label>Available specializations<select ref={select} disabled={disabled} value={options.includes(choice) ? choice : ''} onFocus={() => setRequested(true)} onChange={(event) => setChoice(event.target.value)}><option value="">Choose a specialization</option>{options.map((name) => <option key={name} value={name}>{name}</option>)}</select></label><Button isDisabled={disabled || !options.includes(choice)} onPress={() => { onChange([...selected, choice]); setChoice(''); select.current?.focus(); }}>Add specialization</Button><Button isDisabled={disabled || pending} onPress={() => { setRequested(true); setRetry((value) => value + 1); }}>Refresh specializations</Button></div>
    {pending ? <p role="status">Loading project specializations…</p> : null}{read.key === key && read.error ? <p role="alert">{read.error} Use Refresh specializations to retry.</p> : null}{catalog?.group === group && !available.length ? <p>No specialization definitions are available in this project.</p> : null}
    <details><summary>Edit specialization slugs</summary><label>{label}<textarea disabled={disabled} value={buffer.text} rows={3} onChange={(event) => { const next = normalize(event.target.value.split(/[\n,]/)); setBuffer({ value: next, text: event.target.value }); onChange(next); }} /></label></details>
  </div>;
}
