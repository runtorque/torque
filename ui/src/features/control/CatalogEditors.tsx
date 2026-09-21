import { BehaviorReview, BehaviorVersionReview } from '../attention/BehaviorReview';
import { useMemo, useState } from 'react';

import { Button, StateSurface } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import { records, text } from '../planning/model';
import styles from './ControlCenter.module.css';

function record(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
}

function label(value: unknown): string {
  const item = record(value);
  return text(item.name, text(item.slug, text(item.id, typeof value === 'string' ? value : 'Untitled')));
}

export function CatalogEditor({ title, kind, items, group, send }: {
  title: string;
  kind: 'role' | 'template' | 'specialization';
  items: unknown;
  group: string;
  send: (command: TorqueCommand) => void;
}) {
  const rows = useMemo<unknown[]>(() => Array.isArray(items) ? Array.from(items as unknown[]) : Object.values(record(items)), [items]);
  const [selected, setSelected] = useState('');
  const [scope, setScope] = useState('project');
  const [draftName, setDraftName] = useState('');
  const [draftJson, setDraftJson] = useState('{}');
  const [error, setError] = useState('');
  const choose = (item: unknown) => {
    const name = label(item);
    const value = typeof item === 'string' ? { name } : record(item);
    setSelected(name); setDraftName(name); setScope(value.global === true || value.scope === 'user' ? 'user' : 'project');
    setDraftJson(JSON.stringify(value, null, 2)); setError('');
  };
  const create = () => { setSelected(''); setDraftName(''); setScope('project'); setDraftJson(kind === 'specialization' ? '{\n  "description": "",\n  "preamble": "",\n  "priorities": []\n}' : '{\n  "display_name": "",\n  "description": "",\n  "provider": "",\n  "model": "",\n  "preamble": "",\n  "priorities": []\n}'); setError(''); };
  const save = () => {
    try {
      const data = JSON.parse(draftJson) as UnknownRecord;
      const cmd = kind === 'specialization' ? 'save_specialization' : kind === 'template' ? 'save_template' : 'save_role';
      send({ cmd, group, name: draftName, old_name: selected, old_scope: scope, scope, data });
      setSelected(draftName); setError('');
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Invalid JSON'); }
  };
  const remove = () => {
    const cmd = kind === 'specialization' ? 'delete_specialization' : kind === 'template' ? 'delete_template' : 'delete_role';
    send({ cmd, group, name: selected || draftName, scope }); create();
  };
  return <section className={styles.libraryEditor}>
    <aside><header><div><h2>{title}</h2><p>{rows.length} available</p></div><Button tone="quiet" onPress={create}>＋</Button></header><div>{rows.length ? rows.map((item, index) => <button key={`${label(item)}:${index}`} aria-current={selected === label(item) ? 'page' : undefined} onClick={() => choose(item)}><strong>{label(item)}</strong><small>{text(record(item).scope, record(item).global === true ? 'user' : 'project')}</small></button>) : <StateSurface title={`No ${title.toLowerCase()}`} description="Create the first project entry." />}</div></aside>
    <form onSubmit={(event) => { event.preventDefault(); save(); }}><header><div><h2>{selected ? `Edit ${selected}` : `New ${kind}`}</h2><p>JSON exposes the complete durable authoring contract without hiding advanced fields.</p></div><Button tone="primary" type="submit" isDisabled={!draftName.trim()}>Save</Button></header><div className={styles.libraryMeta}><label>Name<input value={draftName} onChange={(event) => setDraftName(event.target.value)} /></label><label>Scope<select value={scope} onChange={(event) => setScope(event.target.value)}><option value="project">Project</option><option value="user">User</option></select></label></div><label className={styles.libraryJson}>Definition<textarea value={draftJson} onChange={(event) => setDraftJson(event.target.value)} spellCheck={false} /></label>{error ? <p className={styles.validation}>{error}</p> : null}<footer><Button tone="danger" isDisabled={!selected} onPress={remove}>Delete</Button><span /><Button tone="quiet" onPress={() => { try { setDraftJson(JSON.stringify(JSON.parse(draftJson), null, 2)); setError(''); } catch { setError('Invalid JSON'); } }}>Format JSON</Button></footer></form>
  </section>;
}

export function BehaviorOverlayEditor({ group, active, proposals, responses, agents, send }: {
  group: string; active: unknown; proposals: unknown; responses: Record<string, unknown>; agents: UnknownRecord[]; send: (command: TorqueCommand) => void;
}) {
  const [scopeKind, setScopeKind] = useState('agent');
  const [scopeKey, setScopeKey] = useState('');
  const [overlayText, setOverlayText] = useState('');
  const [rationale, setRationale] = useState('');
  const [selectedProposal, setSelectedProposal] = useState('');
  const [versionCommand, setVersionCommand] = useState<TorqueCommand | null>(null);
  const rows = records(proposals);
  const activeRows = records(active);
  const detail = record(responses[`behavior_overlay:${scopeKey || group}`] ?? responses['behavior_overlay:latest']);
  const versions = record(responses[`behavior_overlay_versions:${scopeKey || group}`] ?? responses['behavior_overlay_versions:latest']);
  const scopeArgs = scopeKind === 'agent' ? { agent_id: scopeKey } : { scope_kind: 'role', scope_group: group, scope_key: scopeKey, role_kind: scopeKey };
  const load = () => { send({ cmd: 'behavior_overlay_read', group, ...scopeArgs, seed: true }); send({ cmd: 'behavior_overlay_versions', group, ...scopeArgs }); send({ cmd: 'behavior_overlay_proposals', group, ...scopeArgs }); };
  return <section className={styles.behaviorEditor}>
    <header><div><h2>Dynamic Behavior</h2><p>Inspect, propose, approve, reject, diff, and roll back scoped behavior overlays.</p></div><Button tone="quiet" onPress={() => send({ cmd: 'behavior_overlay_proposals', group })}>Refresh proposals</Button></header>
    <div className={styles.behaviorScope}><label>Scope<select value={scopeKind} onChange={(event) => { setScopeKind(event.target.value); setScopeKey(''); }}><option value="agent">Agent</option><option value="role">Role</option></select></label><label>Target{scopeKind === 'agent' ? <select value={scopeKey} onChange={(event) => setScopeKey(event.target.value)}><option value="">Choose…</option>{agents.map((agent) => <option key={text(agent.id)} value={text(agent.id)}>{text(agent.name, text(agent.id))}</option>)}</select> : <input value={scopeKey} onChange={(event) => setScopeKey(event.target.value)} placeholder="worker, engineer…" />}</label><Button tone="quiet" isDisabled={!scopeKey} onPress={load}>Load scope</Button></div>
    <div className={styles.behaviorColumns}><section><h3>Active overlay</h3><textarea value={overlayText || text(detail.text)} onChange={(event) => setOverlayText(event.target.value)} placeholder="Behavior instructions…" /><input value={rationale} onChange={(event) => setRationale(event.target.value)} placeholder="Why should this change?" /><Button tone="primary" isDisabled={!scopeKey || !(overlayText || text(detail.text)).trim()} onPress={() => send({ cmd: 'behavior_overlay_propose', group, ...scopeArgs, proposed_by_kind: 'user', proposed_by_agent_id: 'user', text: overlayText || text(detail.text), rationale })}>Propose change</Button><h3>Versions</h3><div className={styles.compactRows}>{records(versions.versions).map((version) => <button key={version.id} onClick={() => setVersionCommand({ cmd: 'behavior_overlay_diff', group, ...scopeArgs, to_version_id: version.id })}><strong>Version {text(version.version_number, version.id)}</strong><small>{text(version.created_by_kind, 'system')}</small></button>)}</div></section><section><h3>Approval queue</h3><div className={styles.proposalRows}>{rows.length ? rows.map((proposal) => <article key={proposal.id} aria-current={selectedProposal === proposal.id ? 'true' : undefined} ><header><strong>{text(proposal.scope_key, text(proposal.agent_id, proposal.id))}</strong><span>{text(proposal.status)} · next {text(proposal.next_actor_kind, '—')}</span></header><p>{text(proposal.rationale, 'No rationale.')}</p><Button onPress={() => setSelectedProposal(proposal.id)}>Review behavior diff</Button></article>) : <StateSurface title="No proposals" description="No behavior change is awaiting review." />}</div></section></div>
    {versionCommand ? <BehaviorVersionReview command={versionCommand} onClose={() => setVersionCommand(null)} /> : null}
    {selectedProposal ? <BehaviorReview key={selectedProposal} proposalId={selectedProposal} onClose={() => setSelectedProposal('')} /> : null}
    {activeRows.length ? <details><summary>{activeRows.length} active overlays in snapshot</summary><pre className={styles.json}>{JSON.stringify(activeRows, null, 2)}</pre></details> : null}
  </section>;
}
