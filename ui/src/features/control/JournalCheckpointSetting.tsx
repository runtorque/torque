import { useId, useState } from 'react';
import choices from './journalCheckpointChoices.json';
import styles from './ControlCenter.module.css';

function frequencyLabel(value: string): string {
  if (value === 'manual_only') return 'Manual only';
  const match = /^every_([1-9]\d*)_(actions|minutes)$/.exec(value);
  if (!match) return value;
  const count = match[1]!; const unit = match[2]!.slice(0, -1);
  return `Every ${count} ${unit}${count === '1' ? '' : 's'}`;
}
export function JournalCheckpointSetting({ label, value, onChange }: { label: string; value: string; onChange: (next: string) => void }) {
  const help = useId();
  const [editor, setEditor] = useState(() => ({ value, custom: !choices.includes(value) }));
  // External resets adopt their preset. Typing a preset in the custom input
  // keeps that input mounted, so matching a known value never steals focus.
  if (editor.value !== value) setEditor({ value, custom: !choices.includes(value) });
  const change = (next: string, custom: boolean) => { setEditor({ value: next, custom }); onChange(next); };
  return <>
    <label className={styles.field}><span>{label}</span><select aria-label={label} value={editor.custom ? '__custom' : value} onChange={(event) => change(event.target.value === '__custom' ? '' : event.target.value, event.target.value === '__custom')}>
      {choices.map((choice) => <option key={choice} value={choice}>{frequencyLabel(choice)}</option>)}
      <option value="__custom">{editor.custom && value ? `Custom · ${frequencyLabel(value)}` : 'Custom frequency'}</option>
    </select></label>
    {editor.custom ? <label className={styles.field}><span>Custom journal checkpoint frequency</span><input aria-label="Custom journal checkpoint frequency" value={value} required pattern="manual_only|every_[1-9][0-9]*_(actions|minutes)" aria-describedby={help} placeholder="every_25_actions" onChange={(event) => change(event.target.value, true)} /><small id={help}>Use every_N_actions or every_N_minutes with a positive whole number, or manual_only.</small></label> : null}
    <small>How often Torque reminds the Architect to summarize its Engineers, open work, pending hires, decisions and next moves.</small>
  </>;
}
