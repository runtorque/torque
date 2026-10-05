import { useEffect, useId, useRef } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { selectMissionDismissals } from '../../app/store';
import { Button, StateSurface } from '../../design/primitives';
import type { UnknownRecord } from '../../protocol';
import { actionLabel, count, dateValue, matchesCard, ownerLabel, record, referenceLabel, rows, sectionDefinitions, text, visibleSection } from './missionModel';
import { missionSessionActions as actions } from './missionSession';
import { useMissionControl, useMissionDismiss } from './useMissionControl';
import styles from './MissionSummary.module.css';
function Timestamp({ value }: { value: unknown }) { const date = dateValue(value); return date ? <time dateTime={date.toISOString()}>{date.toLocaleString()}</time> : <>{text(value, '—')}</>; }
function Chips({ value, label }: { value: unknown; label: string }) { const items = Array.isArray(value) ? value : []; return items.length ? <div className={styles.chips} aria-label={label}>{items.map((item, index) => <span key={index}>{text(item, text(record(item).label || record(item).title, 'Unknown'))}</span>)}</div> : null; }
function Descriptors({ card }: { card: UnknownRecord }) { const links = rows(card.deep_links); return links.length ? <ul>{links.map((link, index) => <li key={index}>{text(link.surface, 'surface')} / {text(link.kind, 'inspect')} / {text(link.task_id || link.agent_id || link.stream_id || link.group || link.id, '—')}</li>)}</ul> : <p>No deep-link descriptor.</p>; }
function SourceFreshness({ summary }: { summary: UnknownRecord }) { const sources = Object.entries(record(summary.source_freshness)).sort(([a], [b]) => a.localeCompare(b)); return sources.length ? <section aria-label="Mission Control source freshness"><h3>Source freshness</h3><dl className={styles.freshness}>{sources.map(([key, value]) => { const source = record(value); return <div key={key} data-state={text(source.state, 'unknown')}><dt>{key.replaceAll('_', ' ')}</dt><dd><strong>{text(source.state, 'unknown')}</strong>{source.error ? <p>{text(source.error)}</p> : source.count !== undefined ? <span> · count {count(source.count)}</span> : null}</dd></div>; })}</dl></section> : null; }
function Card({ card, selected, onSelect, onDismiss, pending, error, sectionToggle, onOpenTask, onOpenAgent }: {
  card: UnknownRecord; selected: boolean; onSelect: () => void; onDismiss: (beforeRemoval: () => void) => void; pending: boolean; error: string; sectionToggle: string;
  onOpenTask: (id: string) => void; onOpenAgent: (id: string) => void;
}) {
  const element = useRef<HTMLElement>(null); const id = text(card.id); const title = text(card.title, id); const timestamps = record(card.timestamps);
  const keys = ['set_at', 'updated_at', 'last_activity_at', 'lane_entered_at', 'generated_at', 'created_at', 'boot_timestamp'].filter((key) => timestamps[key] !== undefined && timestamps[key] !== null && timestamps[key] !== '').slice(0, 3);
  return <article ref={element} className={styles.card} data-selected={selected} data-severity={text(card.severity, 'medium')} aria-label={title}>
    <div className={styles.cardMeta}><span>{referenceLabel(card)}</span><span>{text(card.kind, 'card')} · {text(card.severity, 'medium')}</span></div>
    <Button tone="quiet" aria-label={`Inspect ${title}`} aria-expanded={selected} onPress={onSelect}>{title}</Button>
    <dl><div><dt>Owner</dt><dd>{ownerLabel(card)}</dd></div><div><dt>Gate</dt><dd>{text(card.gate, 'state')}</dd></div></dl>
    <p><strong>Why: </strong>{text(card.reason, 'No reason supplied.')}</p><p><strong>Next: </strong>{actionLabel(card)}</p>
    <Chips value={card.evidence_chips} label="Evidence" /><Chips value={card.caveat_chips} label="Caveats" />
    {keys.length ? <dl>{keys.map((key) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd><Timestamp value={timestamps[key]} /></dd></div>)}</dl> : null}
    <Descriptors card={card} />
    <footer><Button tone="quiet" isDisabled={pending} aria-label={`Dismiss ${title}`} onPress={() => { const ownedFocus = element.current?.contains(document.activeElement); onDismiss(() => { if (element.current?.contains(document.activeElement) || (ownedFocus && document.activeElement === document.body)) document.getElementById(sectionToggle)?.focus(); }); }}>{pending ? 'Dismissing…' : 'Dismiss'}</Button>{text(card.primary_task_id) ? <Button tone="quiet" onPress={() => onOpenTask(text(card.primary_task_id))}>Open task</Button> : null}{text(record(card.owner).agent_id) ? <Button tone="quiet" onPress={() => onOpenAgent(text(record(card.owner).agent_id))}>Open agent</Button> : null}</footer>
    {error ? <p role="alert">{error}</p> : null}
  </article>;
}
export function MissionSummary({ group, refreshVersion = 0, onOpenTask, onOpenAgent }: { group: string; refreshVersion?: number; onOpenTask: (id: string) => void; onOpenAgent: (id: string) => void }) {
  const dispatch = useAppDispatch(); const session = useAppSelector((state) => state.missionSession); const persistedDismissals = useAppSelector(selectMissionDismissals);
  const read = useMissionControl(group, refreshVersion); const mutation = useMissionDismiss(group); const prefix = useId();
  const dismissed = { ...persistedDismissals, ...session.dismissed };
  const sections = sectionDefinitions.map(([key, title, subtitle, empty]) => ({ key, title, subtitle, empty, ...visibleSection(read.summary ?? {}, key, dismissed) }));
  const selected = sections.flatMap((section) => section.items).find((card) => text(card.id) === session.selectedId);
  const selectedId = session.selectedId;
  useEffect(() => { if (read.summary && selectedId && !selected) dispatch(actions.select('')); }, [dispatch, read.summary, selectedId, selected]);
  return <section className={styles.root} aria-label="Mission Control summary">
    <header className={styles.toolbar}><label>Search Mission Control<input value={session.search} onChange={(event) => dispatch(actions.search(event.target.value))} placeholder="Gate, task, owner, evidence" /></label><span>{read.summary ? `${sections.reduce((total, section) => total + section.count, 0)} cards` : '— cards'}</span><Button tone="quiet" isDisabled={read.pending || read.disconnected || !group} onPress={read.refresh}>Refresh Mission Control summary</Button></header>
    {read.disconnected ? <p role="status">Disconnected. {read.summary ? 'Showing the last accepted summary.' : 'Connect to load Mission Control.'}</p> : null}
    {read.error ? <p role="alert">{read.error} {read.summary ? 'The last accepted summary is retained.' : ''} <Button tone="quiet" isDisabled={read.pending || read.disconnected} onPress={read.refresh}>Retry Mission Control</Button></p> : null}
    {read.pending ? <p role="status">{read.summary ? 'Refreshing Mission Control…' : 'Loading Mission Control…'}</p> : null}
    {!group ? <StateSurface title="Choose a group" description="Select a workspace group to inspect Mission Control." /> : null}
    {read.summary ? <><p className={styles.updated}>Summary for {group} · <Timestamp value={read.summary.generated_at} /></p><div className={styles.layout}><div className={styles.sections}>{sections.map((section) => {
      const collapsed = session.collapsed[section.key] === true; const filtered = section.items.filter((card) => matchesCard(card, session.search)); const toggle = `${prefix}-${section.key}`;
      return <section className={styles.section} key={section.key} aria-label={section.title}>
        <header><h2><Button id={toggle} tone="quiet" aria-label={`Toggle ${section.title}`} aria-expanded={!collapsed} aria-controls={`${toggle}-cards`} onPress={() => dispatch(actions.toggle(section.key))}>{collapsed ? '▸' : '▾'} {section.title} · {section.count}</Button></h2>{section.truncated ? <span className={styles.truncated}>Truncated</span> : null}</header><p>{section.subtitle}</p>
        {!collapsed ? <div id={`${toggle}-cards`}>{filtered.length ? filtered.map((card) => { const id = text(card.id); const outcome = mutation.outcomes[id]; return <Card key={id} card={card} selected={session.selectedId === id} onSelect={() => dispatch(actions.select(session.selectedId === id ? '' : id))} pending={outcome?.pending === true} error={outcome?.error ?? ''} onDismiss={(beforeRemoval) => mutation.dismiss(id, beforeRemoval)} sectionToggle={toggle} onOpenTask={onOpenTask} onOpenAgent={onOpenAgent} />; }) : <p className={styles.empty}>{session.search.trim() ? 'No cards match the current filter. Clear the filter to restore all cards.' : section.empty}</p>}</div> : null}
      </section>;
    })}</div><aside className={styles.detail} aria-label="Mission Control card details">
      {selected ? <><h2>Selected card</h2><h3>{text(selected.title, text(selected.id))}</h3><dl><div><dt>ID</dt><dd>{text(selected.id)}</dd></div><div><dt>Reference</dt><dd>{referenceLabel(selected)}</dd></div><div><dt>Recommended</dt><dd>{actionLabel(selected)}</dd></div></dl><h3>Why it matters</h3><p>{text(selected.reason, 'No reason supplied.')}</p><h3>Deep-link descriptors</h3><Descriptors card={selected} /><h3>Caveats</h3>{Array.isArray(selected.caveat_chips) && selected.caveat_chips.length ? <Chips value={selected.caveat_chips} label="Selected card caveats" /> : <p>None</p>}</> : <><h2>Read-only detail</h2><p>Select a card to inspect its source descriptors.</p></>}
      <SourceFreshness summary={read.summary} />
    </aside></div></> : null}
  </section>;
}
