import { Button } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { text } from './model';
import styles from './BehaviorOverlayEditor.module.css';

const identity = (kind: unknown, id: unknown) => [text(kind), text(id)].filter(Boolean).join(' · ') || 'Not recorded';
export function VersionProvenance({ version }: { version: UnknownRecord }) {
  const seconds = Number(version.created_at); const date = Number.isFinite(seconds) && seconds > 0 ? new Date(seconds * 1000) : null;
  const validDate = date && Number.isFinite(date.getTime()) ? date : null;
  return <dl className={styles.provenance}>
    <div><dt>Version ID</dt><dd>{text(version.id)}</dd></div>
    <div><dt>Author</dt><dd>{identity(version.author_kind, version.author_agent_id)}</dd></div>
    <div><dt>Approver</dt><dd>{identity(version.approver_kind, version.approver_id)}</dd></div>
    <div><dt>Created</dt><dd>{validDate ? <time dateTime={validDate.toISOString()}>{validDate.toLocaleString()}</time> : 'Not recorded'}</dd></div>
    <div><dt>Text size</dt><dd>{typeof version.text_bytes === 'number' ? `${version.text_bytes} bytes` : 'Not recorded'}</dd></div>
    <div><dt>SHA-256</dt><dd>{text(version.text_sha256) || 'Not recorded'}</dd></div>
    {text(version.parent_version_id) ? <div><dt>Parent version</dt><dd>{text(version.parent_version_id)}</dd></div> : null}
    {text(version.source_proposal_id) ? <div><dt>Source proposal</dt><dd>{text(version.source_proposal_id)}</dd></div> : null}
    <div><dt>Rationale</dt><dd>{text(version.rationale) || 'No rationale provided.'}</dd></div>
  </dl>;
}
export function BehaviorVersionHistory({ versions, activeId, ready, onInspect }: { versions: UnknownRecord[]; activeId: string; ready: boolean; onInspect: (id: string) => void }) {
  return <section aria-label="Behavior version history"><h3>Version history</h3><p>{versions.length} versions loaded (up to 50).</p>
    {versions.length ? versions.map((version) => { const id = text(version.id); const number = typeof version.version_number === 'number' ? String(version.version_number) : text(version.version_number) || id; return <article key={id} aria-label={`Version ${number}`}>
      <strong>Version {number}{id === activeId ? ' · Active' : ''}</strong>
      <details><summary>Version provenance</summary><VersionProvenance version={version} /></details>
      <Button tone="quiet" isDisabled={!ready} onPress={() => onInspect(id)}>{id === activeId ? 'Inspect active version' : 'Review version and rollback'}</Button>
    </article>; }) : <p>No versions returned.</p>}
  </section>;
}
