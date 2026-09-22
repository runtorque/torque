import type { UnknownRecord } from '../../protocol';
import { resolveActionVariables } from './actionVariables';
import styles from './BoardPanel.module.css';

export function ActionVariableFields({ definitions, value, onChange }: { definitions: UnknownRecord[]; value: string; onChange: (value: string) => void }) {
  let values: UnknownRecord = {}; let invalid = false;
  try { values = resolveActionVariables(value, definitions); } catch { invalid = true; }
  const update = (name: string, next: unknown) => {
    const raw = JSON.parse(value) as UnknownRecord;
    onChange(JSON.stringify({ ...raw, [name]: next }, null, 2));
  };
  return <div className={styles.detailSection}>
    {definitions.length ? <p>Action variables · TASK uses the task title.</p> : null}
    {definitions.map((definition) => {
      const name = String(definition.name); const current = values[name] ?? '';
      const structured = current !== null && typeof current === 'object';
      return <label key={name}>{name}{typeof current === 'boolean'
        ? <input type="checkbox" checked={current} disabled={invalid} onChange={(event) => update(name, event.target.checked)} />
        : typeof current === 'number'
          ? <input type="number" value={current} disabled={invalid} onChange={(event) => update(name, event.target.value === '' ? '' : Number(event.target.value))} />
          : <textarea rows={2} value={typeof current === 'string' ? current : JSON.stringify(current)} readOnly={structured} disabled={invalid} onChange={(event) => update(name, event.target.value)} />}{structured ? <small>Edit structured values in Advanced variables.</small> : null}</label>;
    })}
    <details><summary>Advanced variables</summary><label>Action variables (JSON)<textarea value={value} onChange={(event) => onChange(event.target.value)} rows={4} spellCheck={false} /></label></details>
    {invalid ? <p role="alert">Correct the JSON object in Advanced variables to edit named fields.</p> : null}
  </div>;
}
