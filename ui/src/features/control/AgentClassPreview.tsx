import type { UnknownRecord } from '../../protocol';
import { record, text, classLabel, strings } from './agentClassesModel';
import styles from './ControlCenter.module.css';

export function AgentClassPreview({ item, validated, dirty, capabilities }: { item: UnknownRecord; validated: boolean; dirty: boolean; capabilities: UnknownRecord[] }) {
  const authority = record(item.effective_authority); const grants = Object.entries(record(authority.capabilities));
  const apply = record(item.apply_state); const prompt = record(item.prompt_summary);
  const warnings = Array.isArray(item.warnings) ? item.warnings.map((warning: unknown) => typeof warning === 'string' ? warning : text(record(warning).message)).filter(Boolean) : [];
  const restrictions = strings(item.restrictions).filter((value) => !/mcp|compiler|internal policy/i.test(value));
  return <section className={styles.classPreview} aria-label="Agent Class authority preview">
    <header><div><h3>{validated ? 'Validated draft authority' : 'Saved class authority'}</h3><p>{item.id ? `${classLabel(item)} · ${text(item.base_kind)} · ${text(item.lifecycle)} · ${text(item.source, 'project')}` : 'Validate this draft to inspect its launch authority.'}</p></div></header>
    {!validated && dirty ? <p>Edits are not reflected in this preview. Validate the current draft to refresh it.</p> : null}
    {item.id ? <>
      <dl><div><dt>Launch availability</dt><dd>{item.archived === true || item.launchable === false ? 'Unavailable' : item.launchable === true ? 'Available' : 'Not reported'}{record(item.draft).scratch_only === true ? ' · scratch only' : ''}</dd></div><div><dt>Class instructions</dt><dd>{prompt.has_prompt === true ? `${text(prompt.char_count)} characters` : 'No prompt summary returned'}</dd></div></dl>
      {apply.mutates_running_sessions === false ? <p>Saves and assignments do not change running sessions. Access applies at the next launch or relaunch.</p> : null}
      <h4>Effective permissions <span>{grants.length}</span></h4>
      {Object.keys(authority).length ? grants.length ? <ul>{grants.map(([id, scope]) => { const capability = capabilities.find((entry) => entry.id === id); return <li key={id}><span><strong>{text(capability?.label, id)}</strong><small>{id}{capability?.risk === 'high' || capability?.risk === 'critical' ? ` · ${text(capability.risk)} risk` : ''}</small></span><b>{text(scope, 'Not scoped')}</b></li>; })}</ul> : <p>No capabilities granted.</p> : <p>Authority preview unavailable. Validate or refresh the class.</p>}
      {prompt.preview ? <p className={styles.classPromptPreview}>{text(prompt.preview)}</p> : null}
      {warnings.length ? <div><h4>Class warnings</h4><ul>{warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></div> : null}
      {restrictions.length ? <div><h4>Restrictions</h4><ul>{restrictions.map((restriction, index) => <li key={index}>{restriction}</li>)}</ul></div> : null}
      <details><summary>Authority diagnostics</summary>{item.source_path ? <p>Source: {text(item.source_path)}</p> : null}<pre>{JSON.stringify({ effective_authority: authority, restrictions: item.restrictions }, null, 2)}</pre></details>
    </> : null}
  </section>;
}
