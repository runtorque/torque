import { useState } from 'react';
import { Button } from '../../design/primitives';
import { contextLinkKinds, contextLinkTarget, type ContextLinkKind, type ContextLink, type ContextTarget } from './contextLinksModel';
import styles from './ControlCenter.module.css';

export function ContextLinkEditor({ links, targets, group, onChange }: { links: ContextLink[]; targets: ContextTarget[]; group: string; onChange: (links: ContextLink[]) => void }) {
  const [kind, setKind] = useState<ContextLinkKind>('task'); const [targetId, setTargetId] = useState(''); const [search, setSearch] = useState(group);
  const available = targets.filter((target) => target.kind === kind && !links.some((link) => link.target_kind === kind && link.target_ref === target.id));
  const selected = available.find((target) => target.id === targetId);
  const visible = available.filter((target) => target.id === targetId || `${target.group} ${target.name} ${target.id}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <section className={styles.contextLinks} aria-label="Context links to publish"><h3>Links</h3><p>Optional links connect this entry to tasks, pipeline roots and agents. They do not change its scope. Search by name, ID or group.</p>
    <div className={styles.contextLinkPicker}><label>Link kind<select value={kind} onChange={(event) => { setKind(event.target.value as ContextLinkKind); setTargetId(''); }}>{contextLinkKinds.map((value) => <option key={value}>{value}</option>)}</select></label><label>Search link targets<input value={search} onChange={(event) => setSearch(event.target.value)} /></label><label>Link target<select value={selected ? targetId : ''} onChange={(event) => setTargetId(event.target.value)}><option value="">Choose a {kind}…</option>{visible.map((target) => <option key={target.id} value={target.id}>{target.name} · {target.group || 'No group'} · {target.id}</option>)}</select></label><Button isDisabled={!selected} onPress={() => { if (selected) { onChange([...links, { target_kind: kind, target_ref: selected.id }]); setTargetId(''); } }}>Add link</Button></div>
    <ul>{links.map((link) => { const target = contextLinkTarget(link, targets); const label = `${link.target_kind}: ${target?.name ?? link.target_ref}`; return <li key={`${link.target_kind}:${link.target_ref}`}><span>{label}<small>{link.target_ref}{!target ? ' · Unavailable — remove this link before publishing' : ''}</small></span><Button tone="quiet" aria-label={`Remove link ${label}`} onPress={() => onChange(links.filter((item) => item !== link))}>Remove</Button></li>; })}</ul>
    {!links.length ? <p>No optional links.</p> : null}
  </section>;
}
export function ContextLinkList({ links, targets, onOpen }: { links: ContextLink[]; targets: ContextTarget[]; onOpen?: ((target: ContextTarget) => void) | undefined }) {
  return <section className={styles.contextLinks} aria-label="Saved context links"><h3>Linked context</h3>{links.length ? <ul>{links.map((link) => { const target = contextLinkTarget(link, targets); return <li key={`${link.target_kind}:${link.target_ref}`}><span>{link.target_kind}: {target?.name ?? link.target_ref}<small>{link.target_ref}{!target ? ' · Unavailable' : ''}</small></span><Button tone="quiet" isDisabled={!target || !onOpen} aria-label={`Open linked ${link.target_kind} ${target?.name ?? link.target_ref}`} onPress={() => { if (target) onOpen?.(target); }}>Open</Button></li>; })}</ul> : <p>No linked tasks, pipelines or agents.</p>}</section>;
}
