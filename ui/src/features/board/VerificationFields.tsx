import { taskText } from './taskCreationModel';
import type { UnknownRecord } from '../../protocol';
import styles from './BoardPanel.module.css';

export interface VerificationDraft {
  mode: string; state: string; notes: string; summary: UnknownRecord;
}
export function VerificationFields({ value, onChange }: { value: VerificationDraft; onChange: (value: VerificationDraft) => void }) {
  const summary = (patch: UnknownRecord) => onChange({ ...value, summary: { ...value.summary, ...patch } });
  return <>
    <div className={styles.formGrid}>
      <label>Mode<select value={value.mode} onChange={(event) => onChange({ ...value, mode: event.target.value })}><option value="">None</option><option value="deploy">Deploy</option><option value="restart">Restart</option></select></label>
      <label>State<select value={value.state} onChange={(event) => onChange({ ...value, state: event.target.value })}><option value="">No state</option>{['pending', 'attempted', 'passed', 'failed'].map((state) => <option key={state} value={state}>{state}</option>)}</select></label>
      <label>Tests run<input value={taskText(value.summary.tests_run ?? '')} onChange={(event) => summary({ tests_run: event.target.value })} /></label>
      <label>Human validation pending<textarea rows={2} value={taskText(value.summary.human_validation_pending ?? '')} onChange={(event) => summary({ human_validation_pending: event.target.value })} /></label>
    </div>
    <div className={styles.checkRow}>{Object.entries({ manual_smoke_done: 'Manual smoke done', deploy_needed: 'Deploy needed', deploy_attempted: 'Deploy/restart attempted' }).map(([key, label]) => <label key={key}><input type="checkbox" checked={Boolean(value.summary[key])} onChange={(event) => summary({ [key]: event.target.checked })} />{label}</label>)}</div>
    <label>Verification notes<textarea rows={3} value={value.notes} onChange={(event) => onChange({ ...value, notes: event.target.value })} /></label>
  </>;
}
