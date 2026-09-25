import type { UnknownRecord } from '../../protocol';
import { classRecord as record, classText as text, classIdentity, classKindLabel, classStatusLabel, classWarnings, classPermissions, classTime, selectedClassPreview } from './agentClassPresentation';
import styles from './AgentWorkspace.module.css';

function Warnings({ values, label }: { values: unknown; label: string }) {
  const warnings = classWarnings(values);
  return warnings.length ? <section className={styles.classNotices} aria-label={label}><h4>{label}</h4><ul className={styles.classWarnings}>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></section> : null;
}
function TimeFact({ label, value }: { label: string; value: unknown }) {
  const time = classTime(value);
  return time ? <div><dt>{label}</dt><dd><time dateTime={time.iso}>{time.label}</time></dd></div> : null;
}
export function AgentClassDetails({ status, agent, kind, classes, selectedId }: { status: UnknownRecord; agent: UnknownRecord; kind: string; classes: UnknownRecord[]; selectedId: string }) {
  const effective = record(status.effective_class ?? agent.effective_agent_class_snapshot);
  const preview = selectedClassPreview(classes, selectedId, kind, status);
  const permissions = classPermissions(preview); const apply = record(preview.apply_state);
  const relaunchRequired = apply.relaunch_required_after_assignment !== false;
  const appliesAt = text(apply.applies_at);
  const selectedIdentity = classIdentity(preview);
  return <>
    <section aria-label="Current Agent Class status">
      <dl className={`${styles.inspectorFacts} ${styles.classFacts}`}>
        <div><dt>Effective now</dt><dd>{text(status.effective_primary_identity_label) || classIdentity(effective) || text(status.effective_class_id) || 'Default'}</dd></div>
        <div><dt>Desired</dt><dd>{text(status.next_launch_primary_identity_label) || text(status.next_launch_class_id) || 'Default'}</dd></div>
        <div><dt>Effective version</dt><dd>{text(status.effective_class_version) || '—'}</dd></div>
        <div><dt>Next version</dt><dd>{text(status.next_launch_class_version) || '—'}</dd></div>
        <div><dt>Base kind metadata</dt><dd>{classKindLabel(Object.keys(effective).length ? effective : status, kind)}</dd></div>
        <div><dt>Lifecycle/status</dt><dd>{classStatusLabel(Object.keys(effective).length ? effective : status)}</dd></div>
        <div><dt>Assigned by</dt><dd>{text(status.assigned_by ?? agent.agent_class_assigned_by) || '—'}</dd></div>
        <div><dt>Apply state</dt><dd>{status.pending_next_launch ? 'Pending relaunch' : 'Current'}</dd></div>
        <TimeFact label="Assigned at" value={status.assigned_at ?? agent.agent_class_assigned_at} />
        <TimeFact label="Effective frozen" value={status.effective_applied_at ?? agent.effective_agent_class_applied_at} />
      </dl>
      <Warnings label="Current class warnings" values={Array.isArray(effective.warnings) ? effective.warnings : status.warnings} />
    </section>
    <section className={styles.classPreview} aria-label="Selected Agent Class preview">
      <h3>{selectedIdentity ? `${selectedIdentity}${text(preview.version) ? `@${text(preview.version)}` : ''}` : selectedId || `Default ${classKindLabel({}, kind)}`}</h3>
      <p>{selectedId ? 'This selection applies on the next launch or relaunch.' : `No explicit class selected. Default launch behavior is preserved; Torque freezes default-${kind} at launch.`}</p>
      {preview.id ? <>
        <p>{text(preview.purpose) || text(preview.description) || 'No description provided.'}</p>
        <dl className={styles.classPreviewFacts}>
          <div><dt>Base kind</dt><dd>{classKindLabel(preview, kind)}</dd></div>
          <div><dt>Status</dt><dd>{classStatusLabel(preview)}</dd></div>
          <div><dt>Lifecycle</dt><dd>{text(preview.lifecycle) || 'stable'}</dd></div>
          {preview.scratch_only || record(preview.draft).scratch_only ? <div><dt>Usage</dt><dd>Scratch only</dd></div> : null}
          <div><dt>ACL mode</dt><dd>{permissions.mode || 'Not reported'}</dd></div>
        </dl>
        <section aria-label="Selected class access" className={styles.classAccess}>
          <h4>Class access</h4>
          {permissions.available ? permissions.grants.length ? <details><summary>Allowed actions ({permissions.grants.length})</summary><ul>{permissions.grants.map((grant) => <li key={`${grant.capability}:${grant.scope}`}><span>{grant.capability}</span>{grant.scope ? <span>Scope: {grant.scope}</span> : null}</li>)}</ul></details> : <p>No actions granted.</p> : <p>Allowed actions are unavailable. Refresh the class catalog.</p>}
          <h4>Denied</h4>
          {permissions.mode === 'allow' ? <p>Everything not listed is denied by default.</p> : permissions.denialsAvailable ? permissions.denials.length ? <ul>{permissions.denials.map((denial) => <li key={`${denial.capability}:${denial.scope}`}><span>{denial.capability}</span>{denial.scope ? <span>Scope: {denial.scope}</span> : null}</li>)}</ul> : <p>No explicit denials.</p> : <p>Authored denials are unavailable in this snapshot.</p>}
        </section>
        <dl className={styles.classPreviewFacts}>
          <div><dt>Running sessions</dt><dd>{apply.mutates_running_sessions === true ? 'May affect running sessions immediately.' : 'Does not change running sessions.'}</dd></div>
          <div><dt>Relaunch behavior</dt><dd>{appliesAt === 'next_launch_or_relaunch' || relaunchRequired ? 'Access freezes on the next launch or relaunch.' : appliesAt ? appliesAt.replaceAll('_', ' ') : 'Next launch/relaunch.'}</dd></div>
        </dl>
        <Warnings label="Selected class warnings" values={preview.warnings} />
      </> : <p>Class details are unavailable. Refresh the catalog to inspect this selection.</p>}
    </section>
  </>;
}
