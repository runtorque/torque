import { useMemo, useRef, useState, type ChangeEvent, type ReactNode } from 'react';

import { useAppDispatch, useAppSelector } from '../../app/hooks';
import {
  selectAuxiliaryResponseState,
  selectAgentsState,
  selectCatalogState,
  selectGroupsState,
  selectMessagesState,
  selectTasksState,
  selectWorkspaceState,
  selectWorkspaceUi,
  workspaceUiActions,
} from '../../app/store';
import {
  ActionMenu,
  ActionMenuItem,
  Button,
  ModalDialog,
  StateSurface,
} from '../../design/primitives';
import { hasHostCapability, type DesktopHost } from '../../host';
import type { CommandSender } from '../board/BoardPanel';
import { TerminalWorkspace } from '../terminal/TerminalSurface';
import {
  agentStatusLabel,
  buildAgentHierarchy,
  toAgentViewModel,
  type AgentViewModel,
} from './model';
import { AgentCreateDialog } from './AgentCreateDialog';
import { AgentInspector } from './AgentInspector';
import styles from './AgentWorkspace.module.css';
import { WorktreeInspector } from './WorktreeInspector';

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}

function statusTone(agent: AgentViewModel): string {
  if (agent.needsAttention || agent.status === 'error') return 'danger';
  if (agent.status === 'running') return 'success';
  if (agent.status === 'idle') return 'warning';
  return 'muted';
}

function orderedAgentExists(agents: AgentViewModel[], id: string): boolean {
  return agents.some((agent) => agent.cellType === 'agent' && agent.id === id);
}

interface AgentCardProps {
  agent: AgentViewModel;
  selected: boolean;
  focused: boolean;
  taskTitle: string;
  onSelect: () => void;
  onFocus: () => void;
  onRestart: () => void;
  onRelaunch: () => void;
  onClearContext: () => void;
  onInspectWorktree: () => void;
  onInspect: () => void;
  onOrganize: () => void;
  onCopyId: () => void;
  onCopyName: () => void;
  onRemove: () => void;
}

function AgentCard({ agent, selected, focused, taskTitle, onSelect, onFocus, onRestart, onRelaunch, onClearContext, onInspectWorktree, onInspect, onOrganize, onCopyId, onCopyName, onRemove }: AgentCardProps) {
  return (
    <article
      className={`${styles.agentCard} ${selected ? styles.agentSelected : ''} ${focused ? styles.agentFocused : ''}`}
      data-agent-id={agent.id}
      tabIndex={focused ? 0 : -1}
      onClick={onSelect}
      onDoubleClick={onFocus}
      onFocus={onFocus}
      aria-label={`${agent.name}, ${agent.kind}, ${agent.status}`}
    >
      <header>
        <span className={`${styles.statusDot} ${styles[`tone_${statusTone(agent)}`] ?? ''}`} />
        <strong>{agent.name}</strong>
        {agent.needsAttention ? <span className={styles.attention}>attention</span> : null}
        <ActionMenu label={`Actions for ${agent.name}`}>
          <ActionMenuItem onAction={onRestart} isDisabled={agent.cellType === 'terminal'}>Restart</ActionMenuItem>
          <ActionMenuItem onAction={onRelaunch}>Relaunch</ActionMenuItem>
          <ActionMenuItem onAction={onClearContext} isDisabled={agent.cellType === 'terminal'}>Clear context</ActionMenuItem>
          <ActionMenuItem onAction={onInspect} isDisabled={agent.cellType === 'terminal'}>Inspect activity…</ActionMenuItem>
          <ActionMenuItem onAction={onInspectWorktree} isDisabled={!agent.worktreePath}>Inspect worktree…</ActionMenuItem>
          <ActionMenuItem onAction={onOrganize}>Move or reorder…</ActionMenuItem>
          <ActionMenuItem onAction={onCopyId}>Copy ID</ActionMenuItem>
          <ActionMenuItem onAction={onCopyName}>Copy name</ActionMenuItem>
          <ActionMenuItem onAction={onRemove}>Delete…</ActionMenuItem>
        </ActionMenu>
      </header>
      <div className={styles.agentIdentity}>
        <span>{agent.kind}</span>
        {agent.provider ? <span>{agent.provider}</span> : null}
        {agent.role ? <span>{agent.role}</span> : null}
      </div>
      <p className={styles.activity}>{agentStatusLabel(agent)}</p>
      <dl>
        <div><dt>Task</dt><dd title={taskTitle || agent.currentTaskId || 'No task'}>{taskTitle || agent.currentTaskId || '—'}</dd></div>
        <div><dt>Branch</dt><dd title={agent.worktreeBranch || agent.currentBranch || 'No branch'}>{agent.worktreeBranch || agent.currentBranch || '—'}</dd></div>
        <div><dt>Diff</dt><dd>{agent.worktreeDirty ? 'dirty' : 'clean'}{agent.worktreeAhead ? ` · ↑${agent.worktreeAhead}` : ''}</dd></div>
        <div><dt>Context</dt><dd>{agent.contextPercent ? `${Math.round(agent.contextPercent)}%` : '—'}</dd></div>
      </dl>
    </article>
  );
}

interface OrganizationFormProps {
  agent: AgentViewModel;
  agents: AgentViewModel[];
  groups: Record<string, unknown>;
  sendCommand: CommandSender;
  onUnavailable: () => void;
  onClose: () => void;
}

function OrganizationForm({ agent, agents, groups, sendCommand, onUnavailable, onClose }: OrganizationFormProps) {
  const groupNames = Object.keys(groups);
  const [targetGroup, setTargetGroup] = useState(agent.group || groupNames[0] || '');
  const [before, setBefore] = useState('');
  const candidates = agents.filter((item) => item.id !== agent.id && item.group === targetGroup && item.cellType === agent.cellType);

  return <form className={styles.organizationForm} onSubmit={(event) => {
    event.preventDefault();
    if (!sendCommand({ cmd: 'move_agent', id: agent.id, target_group: targetGroup, before })) onUnavailable();
    else onClose();
  }}>
    <p>Move this {agent.kind} to another group or change its position inside the current group. Ownership is preserved.</p>
    <label>Group<select value={targetGroup} onChange={(event) => { setTargetGroup(event.target.value); setBefore(''); }}>{groupNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
    <label>Position<select value={before} onChange={(event) => setBefore(event.target.value)}><option value="">At end</option>{candidates.map((item) => <option key={item.id} value={item.id}>Before {item.name}</option>)}</select></label>
    <footer><Button tone="quiet" type="button" onPress={onClose}>Cancel</Button><Button tone="primary" type="submit" isDisabled={!targetGroup}>Apply</Button></footer>
  </form>;
}

interface SettingsFormProps {
  agent: AgentViewModel;
  rawSettings: unknown;
  rawDigestSettings: unknown;
  resolvedSettings: unknown;
  sendCommand: CommandSender;
  onUnavailable: () => void;
  onClose: () => void;
}

const principalSettingFields = [
  ['provider', 'Provider', 'text'],
  ['boot_command', 'Boot command', 'text'],
  ['model', 'Model', 'text'],
  ['reasoning_effort', 'Reasoning effort', 'text'],
  ['fast_mode', 'Fast mode', 'fast'],
  ['autonomy_mode', 'Autonomy mode', 'text'],
  ['custom_instructions', 'Custom instructions', 'textarea'],
] as const;

const engineerSettingFields = [
  ['default_worker_concurrency', 'Default worker concurrency', 'number'],
  ['wave_size_preference', 'Wave size preference', 'text'],
  ['same_agent_follow_up_preference', 'Same-agent follow-up', 'text'],
  ['escalation_style', 'Escalation style', 'text'],
  ['engineer_can_override_worker_provider', 'Worker provider override', 'boolean'],
  ['restrict_to_created_agents', 'Restrict to created agents', 'boolean'],
] as const;

const digestSettingFields = [
  ['paused', 'Digest delivery', 'paused'],
  ['push_interval', 'Push interval (seconds)', 'number'],
  ['max_interval', 'Maximum interval (seconds)', 'number'],
  ['heartbeat_interval', 'Heartbeat interval (seconds)', 'number'],
  ['digest_verbosity', 'Digest verbosity', 'text'],
  ['enabled_events', 'Enabled events', 'list'],
] as const;

function settingText(value: unknown): string {
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return text(value);
}

function SettingsForm({ agent, rawSettings, rawDigestSettings, resolvedSettings, sendCommand, onUnavailable, onClose }: SettingsFormProps) {
  const settings = asRecord(rawSettings);
  const digestSettings = asRecord(rawDigestSettings);
  const resolved = asRecord(resolvedSettings);
  const entry = (key: string) => asRecord(resolved[key]);
  const effective = (key: string, digest = false) => settingText(entry(key).value ?? (digest ? digestSettings[key] : settings[key]));
  const [name, setName] = useState(agent.name);
  const [icon, setIcon] = useState(text(agent.raw.icon));
  const [tabColor, setTabColor] = useState(text(agent.raw.tab_color));
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(
    [...principalSettingFields, ...engineerSettingFields].map(([key]) => [key, effective(key)]),
  ));
  const [digestValues, setDigestValues] = useState<Record<string, string>>(() => Object.fromEntries(
    digestSettingFields.map(([key]) => [key, effective(key, true)]),
  ));
  const [specializations, setSpecializations] = useState(settingText(agent.raw.engineer_specializations));
  const [relaunch, setRelaunch] = useState(false);
  const [inheritFields, setInheritFields] = useState<string[]>([]);
  const resetToInherited = (key: string, digest = false) => {
    const inherited = asRecord(entry(key).inherited);
    const value = settingText(inherited.value);
    if (digest) setDigestValues((current) => ({ ...current, [key]: value }));
    else setValues((current) => ({ ...current, [key]: value }));
    setInheritFields((current) => current.includes(key) ? current : [...current, key]);
  };
  const origin = (key: string) => Object.keys(entry(key)).length ? text(entry(key).origin, 'default') : 'inherit';
  const originControl = (key: string, digest = false) => <span className={styles.settingOrigin}><span>{inheritFields.includes(key) ? 'inherited' : origin(key)}</span>{origin(key) === 'per-agent' && !inheritFields.includes(key) ? <button type="button" onClick={() => resetToInherited(key, digest)}>Use inherited</button> : null}</span>;
  const updateValue = (key: string, value: string, digest = false) => {
    if (digest) setDigestValues((current) => ({ ...current, [key]: value }));
    else setValues((current) => ({ ...current, [key]: value }));
    setInheritFields((current) => current.filter((item) => item !== key));
  };
  const control = (key: string, label: string, type: string, digest = false) => {
    const value = (digest ? digestValues : values)[key] ?? '';
    const common = { value, onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => updateValue(key, event.target.value, digest) };
    let input: ReactNode;
    if (type === 'textarea' || type === 'list') input = <textarea {...common} rows={type === 'list' ? 3 : 7} />;
    else if (type === 'number') input = <input {...common} type="number" placeholder="inherit" />;
    else if (type === 'boolean') input = <select {...common}><option value="">Inherited</option><option value="true">Allowed</option><option value="false">Not allowed</option></select>;
    else if (type === 'paused') input = <select {...common}><option value="">Inherited</option><option value="false">Enabled</option><option value="true">Paused</option></select>;
    else if (type === 'fast') input = <select {...common}><option value="">Inherited</option><option value="on">Fast on</option><option value="off">Fast off</option></select>;
    else input = <input {...common} placeholder="inherit" />;
    return <label key={key}>{label}{originControl(key, digest)}{input}</label>;
  };

  return (
    <form className={styles.settingsForm} onSubmit={(event) => {
      event.preventDefault();
      const identityChanges: Record<string, unknown> = {};
      if (name.trim() !== agent.name) identityChanges.name = name.trim();
      if (icon.trim() !== text(agent.raw.icon)) identityChanges.icon = icon.trim();
      if (tabColor.trim() !== text(agent.raw.tab_color)) identityChanges.tab_color = tabColor.trim();
      const identitySent = !Object.keys(identityChanges).length || sendCommand({ cmd: 'update_agent', id: agent.id, ...identityChanges });
      const changes: Record<string, string | number | boolean | null> = {};
      for (const [key, value] of Object.entries(values)) {
        if (inheritFields.includes(key)) changes[key] = null;
        else if (value !== effective(key)) changes[key] = value || null;
      }
      ['default_worker_concurrency'].forEach((key) => { if (typeof changes[key] === 'string' && changes[key]) changes[key] = Number(changes[key]); });
      ['engineer_can_override_worker_provider', 'restrict_to_created_agents'].forEach((key) => { if (changes[key] === 'true') changes[key] = true; else if (changes[key] === 'false') changes[key] = false; });
      const digestChanges: Record<string, string | number | boolean | string[] | null> = {};
      for (const [key, value] of Object.entries(digestValues)) {
        if (inheritFields.includes(key)) digestChanges[key] = null;
        else if (value !== effective(key, true)) digestChanges[key] = value || null;
      }
      ['push_interval', 'max_interval', 'heartbeat_interval'].forEach((key) => { if (typeof digestChanges[key] === 'string' && digestChanges[key]) digestChanges[key] = Number(digestChanges[key]); });
      if (digestChanges.paused === 'true') digestChanges.paused = true;
      else if (digestChanges.paused === 'false') digestChanges.paused = false;
      if (typeof digestChanges.enabled_events === 'string') digestChanges.enabled_events = digestChanges.enabled_events.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
      const principalSent = !['architect', 'engineer'].includes(agent.kind)
        || !Object.keys(changes).length
        || sendCommand({ cmd: 'update_agent_settings', agent_id: agent.id, settings: changes });
      const digestSent = !['architect', 'engineer'].includes(agent.kind)
        || !Object.keys(digestChanges).length
        || sendCommand({ cmd: 'update_agent_digest_settings', agent_id: agent.id, settings: digestChanges });
      const nextSpecs = specializations.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
      const currentSpecs = Array.isArray(agent.raw.engineer_specializations) ? agent.raw.engineer_specializations.map(String) : [];
      const specializationsSent = agent.kind !== 'engineer'
        || JSON.stringify(nextSpecs) === JSON.stringify(currentSpecs)
        || sendCommand({ cmd: 'set_engineer_specializations', engineer_id: agent.id, specializations: nextSpecs });
      const relaunchSent = !relaunch || sendCommand({ cmd: 'relaunch_agent', id: agent.id });
      if (!identitySent || !principalSent || !digestSent || !specializationsSent || !relaunchSent) onUnavailable();
      else onClose();
    }}>
      <section className={styles.settingsSection}><h3>Identity</h3><div className={styles.formGrid}><label>Name<input value={name} onChange={(event) => setName(event.target.value)} required /></label><label>Icon<input value={icon} onChange={(event) => setIcon(event.target.value)} placeholder="optional icon" /></label><label>Tab color<input value={tabColor} onChange={(event) => setTabColor(event.target.value)} placeholder="#6172f3" /></label></div></section>
      {['architect', 'engineer'].includes(agent.kind) ? <>
        <section className={styles.settingsSection}><h3>Launch and behavior</h3><div className={styles.formGrid}>{principalSettingFields.map(([key, label, type]) => control(key, label, type))}{agent.kind === 'engineer' ? engineerSettingFields.map(([key, label, type]) => control(key, label, type)) : null}</div></section>
        {agent.kind === 'engineer' ? <section className={styles.settingsSection}><h3>Specializations</h3><label>Ordered specialization slugs<textarea value={specializations} onChange={(event) => setSpecializations(event.target.value)} rows={3} placeholder="ui-ux, frontend" /></label></section> : null}
        <section className={styles.settingsSection}><h3>Digest delivery</h3><div className={styles.formGrid}>{digestSettingFields.map(([key, label, type]) => control(key, label, type, true))}</div></section>
        <label className={styles.inlineCheck}><input type="checkbox" checked={relaunch} onChange={(event) => setRelaunch(event.target.checked)} />Relaunch after saving launch-bound changes</label>
      </> : <p className={styles.formHint}>Worker provider and launch settings are inherited from its role, Agent Class, and group defaults.</p>}
      <footer><Button tone="quiet" type="button" onPress={onClose}>Cancel</Button><Button tone="primary" type="submit">Save settings</Button></footer>
    </form>
  );
}

interface FocusPanelProps {
  agent: AgentViewModel;
  terminals: AgentViewModel[];
  selectedTerminalId: string;
  detachedTerminal: Record<string, unknown> | null;
  messages: unknown;
  rawSettings: unknown;
  rawDigestSettings: unknown;
  resolvedSettings: unknown;
  host: DesktopHost;
  sendCommand: CommandSender;
  onUnavailable: () => void;
  onSelectTerminal: (id: string) => void;
  onDetach: (panel: 'agents' | 'terminal') => void;
  onInspectWorktree: () => void;
  onInspect: () => void;
  onOrganize: () => void;
  onRemove: () => void;
  terminalOnly?: boolean;
  directMessagesHeight?: number;
  composeHeight?: number;
}

function FocusPanel({ agent, terminals, selectedTerminalId, detachedTerminal, messages, rawSettings, rawDigestSettings, resolvedSettings, host, sendCommand, onUnavailable, onSelectTerminal, onDetach, onInspectWorktree, onInspect, onOrganize, onRemove, terminalOnly = false, directMessagesHeight = 0, composeHeight = 0 }: FocusPanelProps) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const terminal = terminals.find((item) => item.id === selectedTerminalId) ?? terminals[0] ?? agent;
  const run = (command: Record<string, unknown>) => { if (!sendCommand(command as { cmd: string })) onUnavailable(); };
  const focusDetached = () => {
    const label = text(detachedTerminal?.label);
    if (label) void host.focusWindow(label);
  };

  return (
    <section className={`${styles.focusPanel} ${terminalOnly ? styles.terminalOnly : ''}`} aria-label={`Focused ${agent.cellType} ${agent.name}`}>
      {!terminalOnly ? <header className={styles.focusHeader}>
        <div><span className={`${styles.statusDot} ${styles[`tone_${statusTone(agent)}`] ?? ''}`} /><div><h2>{agent.name}</h2><p>{agent.kind} · {agent.status}{agent.provider ? ` · ${agent.provider}` : ''}</p></div></div>
        <div>
          {agent.cellType === 'agent' ? <Button tone="quiet" onPress={onInspect}>Inspect</Button> : null}
          {agent.cellType === 'agent' ? <Button tone="quiet" onPress={() => setSettingsOpen(true)}>Settings</Button> : null}
          {hasHostCapability(host, 'detach-panel') ? <Button tone="quiet" onPress={() => onDetach('agents')}>Detach agent</Button> : null}
          <ActionMenu label={`Lifecycle actions for ${agent.name}`}>
            <ActionMenuItem onAction={() => run({ cmd: 'clear_agent_context', id: agent.id })} isDisabled={agent.cellType === 'terminal'}>Clear context</ActionMenuItem>
            <ActionMenuItem onAction={() => run({ cmd: 'restart_agent', id: agent.id })} isDisabled={agent.cellType === 'terminal'}>Restart</ActionMenuItem>
            <ActionMenuItem onAction={() => run({ cmd: 'relaunch_agent', id: agent.id })}>Relaunch</ActionMenuItem>
            <ActionMenuItem onAction={onOrganize}>Move or reorder…</ActionMenuItem>
            {agent.kind === 'architect' ? <ActionMenuItem onAction={() => run({ cmd: Number(agent.raw.dismissed_at ?? 0) ? 'architect_rehire' : 'architect_dismiss', architect_id: agent.id, reason: 'Dismissed from React UI' })}>{Number(agent.raw.dismissed_at ?? 0) ? 'Rehire' : 'Dismiss'}</ActionMenuItem> : null}
            {agent.kind === 'engineer' ? <ActionMenuItem onAction={() => run({ cmd: Number(agent.raw.dismissed_at ?? 0) ? 'engineer_rehire' : 'engineer_dismiss', engineer_id: agent.id, reason: 'Dismissed from React UI' })}>{Number(agent.raw.dismissed_at ?? 0) ? 'Rehire' : 'Dismiss'}</ActionMenuItem> : null}
            <ActionMenuItem onAction={() => { void navigator.clipboard.writeText(agent.id); }}>Copy ID</ActionMenuItem>
            <ActionMenuItem onAction={() => { void navigator.clipboard.writeText(agent.name); }}>Copy name</ActionMenuItem>
            <ActionMenuItem onAction={onRemove}>Delete…</ActionMenuItem>
          </ActionMenu>
        </div>
      </header> : null}

      {!terminalOnly ? <div className={styles.focusFacts}>
        <div><span>Activity</span><strong>{agentStatusLabel(agent)}</strong></div>
        <div><span>Path</span><strong title={agent.currentPath}>{agent.currentPath || '—'}</strong></div>
        <div><span>Branch</span><strong>{agent.worktreeBranch || agent.currentBranch || '—'}</strong></div>
        <div><span>Worktree</span><strong>{agent.worktreePath ? `${agent.worktreeDirty ? 'dirty' : 'clean'} · ↑${agent.worktreeAhead} ↓${agent.worktreeBehind}` : 'none'}</strong></div>
      </div> : null}

      {!terminalOnly && agent.cellType === 'agent' ? <div className={styles.worktreeBar}>
        <span>Worktree controls</span>
        <Button tone="quiet" onPress={() => agent.worktreePath ? onInspectWorktree() : run({ cmd: 'worktree_create', id: agent.id, relaunch: Boolean(agent.sessionId) })}>{agent.worktreePath ? 'Inspect diff' : 'Create'}</Button>
        <Button tone="quiet" onPress={() => run({ cmd: 'worktree_checkpoint', id: agent.id })} isDisabled={!agent.worktreePath}>Checkpoint</Button>
        <Button tone="quiet" onPress={() => run({ cmd: 'worktree_check_merge', id: agent.id })} isDisabled={!agent.worktreePath}>Preflight merge</Button>
      </div> : null}

      <div className={styles.terminalTabs} role="tablist" aria-label="Agent terminals">
        {terminals.map((item) => <button key={item.id} role="tab" aria-selected={item.id === terminal.id} onClick={() => onSelectTerminal(item.id)}><span className={`${styles.statusDot} ${styles[`tone_${statusTone(item)}`] ?? ''}`} />{item.id === agent.id ? (agent.cellType === 'terminal' ? 'Terminal' : 'Agent') : item.name}</button>)}
        {hasHostCapability(host, 'detach-panel') && !terminalOnly ? <Button tone="quiet" onPress={() => detachedTerminal ? focusDetached() : onDetach('terminal')}>{detachedTerminal ? 'Open detached terminal' : 'Detach terminal'}</Button> : null}
      </div>

      <div className={styles.terminalHost}>
        {detachedTerminal && !terminalOnly
          ? <StateSurface title="Terminal detached" description="The PTY is owned by its native window, preventing competing focus and resize events." action={<Button tone="primary" onPress={focusDetached}>Focus terminal window</Button>} />
          : <TerminalWorkspace agent={agent} terminal={terminal} messages={messages} sendCommand={sendCommand} onUnavailable={onUnavailable} showConversation={!terminalOnly && agent.cellType === 'agent'} directMessagesHeight={directMessagesHeight} composeHeight={composeHeight} />}
      </div>

      <ModalDialog title="Agent settings" description={`${agent.kind} · ${agent.id}`} size="large" isOpen={settingsOpen} onOpenChange={setSettingsOpen}>
        <SettingsForm agent={agent} rawSettings={rawSettings} rawDigestSettings={rawDigestSettings} resolvedSettings={resolvedSettings} sendCommand={sendCommand} onUnavailable={onUnavailable} onClose={() => setSettingsOpen(false)} />
      </ModalDialog>
    </section>
  );
}

export interface AgentWorkspaceProps {
  group: string;
  host: DesktopHost;
  sendCommand: CommandSender;
  onCommandUnavailable: () => void;
  terminalOnly?: boolean;
}

export function AgentWorkspace({ group, host, sendCommand, onCommandUnavailable, terminalOnly = false }: AgentWorkspaceProps) {
  const dispatch = useAppDispatch();
  const { records, settings, resolvedSettings, digestSettings } = useAppSelector(selectAgentsState);
  const groupsState = useAppSelector(selectGroupsState);
  const catalog = useAppSelector(selectCatalogState);
  const auxiliaryResponses = useAppSelector(selectAuxiliaryResponseState);
  const messagesState = useAppSelector(selectMessagesState);
  const tasks = useAppSelector(selectTasksState).records;
  const workspace = useAppSelector(selectWorkspaceState);
  const workspaceUi = useAppSelector(selectWorkspaceUi);
  const hierarchy = useMemo(() => buildAgentHierarchy(records, group), [records, group]);
  const deletedAgents = useMemo(() => Object.entries(records)
    .map(([id, value]) => toAgentViewModel(id, value))
    .filter((agent) => (!group || agent.group === group) && Number(agent.raw.deleted_at ?? 0) > 0)
    .sort((left, right) => Number(right.raw.deleted_at ?? 0) - Number(left.raw.deleted_at ?? 0)), [records, group]);
  const requestedId = workspaceUi.selectedAgentId || text(workspace.selectedAgentId);
  const selected = hierarchy.all.find((agent) => agent.id === requestedId)
    ?? hierarchy.all.find((agent) => agent.cellType === 'agent')
    ?? hierarchy.looseTerminals[0]
    ?? null;
  const terminalChoices = selected
    ? [selected, ...(hierarchy.terminalsByParent[selected.id] ?? [])].filter((cell) => Boolean(cell.sessionId))
    : [];
  const selectedTerminalId = workspaceUi.selectedTerminalId && terminalChoices.some((cell) => cell.id === workspaceUi.selectedTerminalId)
    ? workspaceUi.selectedTerminalId
    : terminalChoices[0]?.id ?? '';
  const detachedTerminal = asRecord(workspace.detachedPanels).terminal;
  const terminalEntry = detachedTerminal && typeof detachedTerminal === 'object' && !Array.isArray(detachedTerminal)
    ? detachedTerminal as Record<string, unknown>
    : null;
  const [focusedId, setFocusedId] = useState(selected?.id ?? null);
  const [removeTarget, setRemoveTarget] = useState<AgentViewModel | null>(null);
  const [worktreeTarget, setWorktreeTarget] = useState<AgentViewModel | null>(null);
  const [inspectTarget, setInspectTarget] = useState<AgentViewModel | null>(null);
  const [organizationTarget, setOrganizationTarget] = useState<AgentViewModel | null>(null);
  const [deletedOpen, setDeletedOpen] = useState(false);
  const [purgeTarget, setPurgeTarget] = useState<AgentViewModel | null>(null);
  const cardsRef = useRef<HTMLDivElement>(null);

  const effectiveFocusedId = focusedId && orderedAgentExists(hierarchy.all, focusedId)
    ? focusedId
    : selected?.id ?? null;

  const selectAgent = (agent: AgentViewModel, focusPty = false) => {
    dispatch(workspaceUiActions.setSelectedAgent(agent.id));
    setFocusedId(agent.id);
    if (!sendCommand({ cmd: 'ui_select_agent', id: agent.id })) onCommandUnavailable();
    if (focusPty && !sendCommand({ cmd: 'focus_agent', id: agent.id })) onCommandUnavailable();
  };

  const detach = async (panel: 'agents' | 'terminal') => {
    try {
      const current = asRecord(workspace.detachedPanels);
      const existing = asRecord(current[panel]);
      if (text(existing.label)) { await host.focusWindow(text(existing.label)); return; }
      const bounds = panel === 'agents' ? { width: 1120, height: 760 } : { width: 980, height: 680 };
      const detached = await host.detachPanel({ panel, bounds });
      if (!sendCommand({ cmd: 'ui_set_detached_panels', detached_panels: { ...current, [panel]: { label: detached.label, bounds } } })) onCommandUnavailable();
    } catch { onCommandUnavailable(); }
  };

  const orderedCards = hierarchy.all.filter((agent) => agent.cellType === 'agent' || !agent.parentId);
  const agentCount = hierarchy.all.filter((agent) => agent.cellType === 'agent').length;
  const terminalCount = hierarchy.all.filter((agent) => agent.cellType === 'terminal').length;
  const focusStep = (direction: number) => {
    if (!orderedCards.length) return;
    const current = effectiveFocusedId ? orderedCards.findIndex((agent) => agent.id === effectiveFocusedId) : -1;
    const next = orderedCards[(current + direction + orderedCards.length) % orderedCards.length];
    if (!next) return;
    setFocusedId(next.id);
    requestAnimationFrame(() => cardsRef.current?.querySelector<HTMLElement>(`[data-agent-id="${CSS.escape(next.id)}"]`)?.focus());
  };

  const taskTitle = (agent: AgentViewModel) => text(asRecord(tasks[agent.currentTaskId]).task);
  const renderCard = (agent: AgentViewModel) => <AgentCard key={agent.id} agent={agent} selected={selected?.id === agent.id} focused={effectiveFocusedId === agent.id} taskTitle={taskTitle(agent)} onSelect={() => selectAgent(agent)} onFocus={() => setFocusedId(agent.id)} onRestart={() => { if (!sendCommand({ cmd: 'restart_agent', id: agent.id })) onCommandUnavailable(); }} onRelaunch={() => { if (!sendCommand({ cmd: 'relaunch_agent', id: agent.id })) onCommandUnavailable(); }} onClearContext={() => { if (!sendCommand({ cmd: 'clear_agent_context', id: agent.id })) onCommandUnavailable(); }} onInspectWorktree={() => setWorktreeTarget(agent)} onInspect={() => setInspectTarget(agent)} onOrganize={() => setOrganizationTarget(agent)} onCopyId={() => { void navigator.clipboard.writeText(agent.id); }} onCopyName={() => { void navigator.clipboard.writeText(agent.name); }} onRemove={() => setRemoveTarget(agent)} />;

  const openCreate = (kind: 'architect' | 'engineer' | 'worker' | 'terminal') => {
    [{ cmd: 'get_config', group }, { cmd: 'get_group_settings', group }, { cmd: 'list_roles', group }, { cmd: 'list_templates', group }, { cmd: 'list_specializations', group }, { cmd: 'agent_class_list' }].forEach((command) => {
      if (!sendCommand(command)) onCommandUnavailable();
    });
    dispatch(workspaceUiActions.setCreateAgentKind(kind));
  };

  if (!group) return <StateSurface title="Choose a group" description="Agents are scoped to the active Torque group." />;
  if (terminalOnly) {
    return selected && terminalChoices.length ? <FocusPanel agent={selected} terminals={terminalChoices} selectedTerminalId={selectedTerminalId} detachedTerminal={null} messages={messagesState.direct[selected.id]} rawSettings={settings[selected.id]} rawDigestSettings={digestSettings[selected.id]} resolvedSettings={resolvedSettings[selected.id]} host={host} sendCommand={sendCommand} onUnavailable={onCommandUnavailable} onSelectTerminal={(id) => dispatch(workspaceUiActions.setSelectedTerminal(id))} onDetach={(panel) => { void detach(panel); }} onInspectWorktree={() => setWorktreeTarget(selected)} onInspect={() => setInspectTarget(selected)} onOrganize={() => setOrganizationTarget(selected)} onRemove={() => setRemoveTarget(selected)} directMessagesHeight={Number(workspace.terminalDirectMessagesHeight) || 0} composeHeight={Number(workspace.terminalComposeHeight) || 0} terminalOnly /> : <StateSurface title="No active terminal" description="Select or relaunch an agent in the main Torque window." />;
  }

  return (
    <section className={styles.workspace} aria-labelledby="agents-heading">
      <header className={styles.workspaceHeader}><div><p>Workspace / {group}</p><h1 id="agents-heading">Agents</h1></div><div><span>{agentCount} {agentCount === 1 ? 'agent' : 'agents'}{terminalCount ? ` · ${terminalCount} ${terminalCount === 1 ? 'terminal' : 'terminals'}` : ''}</span>{deletedAgents.length ? <Button tone="quiet" onPress={() => setDeletedOpen(true)}>Recently deleted · {deletedAgents.length}</Button> : null}<ActionMenu label="Create agent or terminal" trigger={<Button tone="primary" aria-label="Create agent or terminal">＋ New</Button>}><ActionMenuItem onAction={() => openCreate('architect')}>New Architect…</ActionMenuItem><ActionMenuItem onAction={() => openCreate('engineer')}>New Engineer…</ActionMenuItem><ActionMenuItem onAction={() => openCreate('worker')}>New Worker…</ActionMenuItem><ActionMenuItem onAction={() => openCreate('terminal')}>New Terminal…</ActionMenuItem></ActionMenu>{hasHostCapability(host, 'detach-panel') ? <Button tone="quiet" onPress={() => { void detach('agents'); }}>Detach workspace</Button> : null}</div></header>
      {!hierarchy.all.length ? <div className={styles.emptyAgents}>
        <StateSurface title="Start an agent workspace" description="Create an Architect or Engineer to lead work, a Worker for a focused task, or a standalone terminal for direct shell access." action={<div className={styles.emptyAgentActions}><Button tone="primary" onPress={() => openCreate('worker')}>Create Worker</Button><Button tone="quiet" onPress={() => openCreate('terminal')}>Open Terminal</Button></div>} />
      </div> : <div className={styles.split}>
        <div
          className={styles.hierarchy}
          ref={cardsRef}
          onKeyDown={(event) => {
            const target = event.target as HTMLElement;
            if (target.closest('button, input, textarea, [role="menuitem"]')) return;
            if (event.key === 'ArrowDown' || event.key === 'ArrowRight') { event.preventDefault(); focusStep(1); }
            if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') { event.preventDefault(); focusStep(-1); }
            if (event.key === 'Enter') { const item = orderedCards.find((agent) => agent.id === effectiveFocusedId); if (item) { event.preventDefault(); selectAgent(item, true); } }
            if ((event.key === 'Delete' || event.key === 'Backspace') && effectiveFocusedId) { const item = orderedCards.find((agent) => agent.id === effectiveFocusedId); if (item) { event.preventDefault(); setRemoveTarget(item); } }
          }}
        >
          {hierarchy.architects.map((architect) => <section key={architect.id} className={styles.principalSection}><h2>Architect</h2>{renderCard(architect)}{(hierarchy.engineersByArchitect[architect.id] ?? []).map((engineer) => <section key={engineer.id} className={styles.teamBand}><h3>{engineer.name} team</h3>{renderCard(engineer)}<div className={styles.workerGrid}>{(hierarchy.workersByEngineer[engineer.id] ?? []).length ? (hierarchy.workersByEngineer[engineer.id] ?? []).map(renderCard) : <p>No workers</p>}</div></section>)}</section>)}
          {hierarchy.userEngineers.length || hierarchy.userWorkers.length ? <section className={styles.principalSection}><h2>User-owned</h2>{hierarchy.userEngineers.map((engineer) => <section key={engineer.id} className={styles.teamBand}><h3>{engineer.name} team</h3>{renderCard(engineer)}<div className={styles.workerGrid}>{(hierarchy.workersByEngineer[engineer.id] ?? []).length ? (hierarchy.workersByEngineer[engineer.id] ?? []).map(renderCard) : <p>No workers</p>}</div></section>)}<div className={styles.workerGrid}>{hierarchy.userWorkers.map(renderCard)}</div></section> : null}
          {hierarchy.looseTerminals.length ? <section className={styles.principalSection}><h2>Terminals</h2><div className={styles.workerGrid}>{hierarchy.looseTerminals.map(renderCard)}</div></section> : null}
        </div>
        {selected ? <FocusPanel agent={selected} terminals={terminalChoices.length ? terminalChoices : [selected]} selectedTerminalId={selectedTerminalId || selected.id} detachedTerminal={terminalEntry} messages={messagesState.direct[selected.id]} rawSettings={settings[selected.id]} rawDigestSettings={digestSettings[selected.id]} resolvedSettings={resolvedSettings[selected.id]} host={host} sendCommand={sendCommand} onUnavailable={onCommandUnavailable} onSelectTerminal={(id) => dispatch(workspaceUiActions.setSelectedTerminal(id))} onDetach={(panel) => { void detach(panel); }} onInspectWorktree={() => setWorktreeTarget(selected)} onInspect={() => setInspectTarget(selected)} onOrganize={() => setOrganizationTarget(selected)} onRemove={() => setRemoveTarget(selected)} directMessagesHeight={Number(workspace.terminalDirectMessagesHeight) || 0} composeHeight={Number(workspace.terminalComposeHeight) || 0} /> : <StateSurface title="Select an agent" description="Choose an agent to inspect status, worktree, settings, messages, and terminal." />}
      </div>}

      <ModalDialog title="Delete agent?" description={removeTarget ? `${removeTarget.name} · ${removeTarget.kind}` : ''} size="small" isOpen={Boolean(removeTarget)} onOpenChange={(open) => { if (!open) setRemoveTarget(null); }}>
        <div className={styles.removeDialog}><p>This stops the live session and moves supported principals into Torque’s restore window. Worktree safety rules still apply.</p><footer><Button tone="quiet" onPress={() => setRemoveTarget(null)}>Cancel</Button><Button tone="danger" onPress={() => { if (removeTarget && !sendCommand({ cmd: 'remove_agent', id: removeTarget.id })) onCommandUnavailable(); setRemoveTarget(null); }}>Delete agent</Button></footer></div>
      </ModalDialog>
      {workspaceUi.createAgentKind ? <AgentCreateDialog key={workspaceUi.createAgentKind} open initialKind={workspaceUi.createAgentKind} group={group} agents={hierarchy.all} catalog={catalog} sendCommand={sendCommand} onUnavailable={onCommandUnavailable} onClose={() => dispatch(workspaceUiActions.setCreateAgentKind(null))} /> : null}
      {worktreeTarget ? <WorktreeInspector key={worktreeTarget.id} agent={worktreeTarget} responses={auxiliaryResponses} sendCommand={sendCommand} onUnavailable={onCommandUnavailable} onClose={() => setWorktreeTarget(null)} /> : null}
      {inspectTarget ? <AgentInspector key={inspectTarget.id} agent={inspectTarget} group={group} catalog={catalog} responses={auxiliaryResponses} messages={messagesState.peerThreads} sendCommand={sendCommand} onUnavailable={onCommandUnavailable} onClose={() => setInspectTarget(null)} /> : null}
      <ModalDialog title="Move or reorder agent" description={organizationTarget ? `${organizationTarget.name} · ${organizationTarget.kind}` : ''} size="small" isOpen={Boolean(organizationTarget)} onOpenChange={(open) => { if (!open) setOrganizationTarget(null); }}>
        {organizationTarget ? <OrganizationForm key={organizationTarget.id} agent={organizationTarget} agents={Object.entries(records).map(([id, value]) => toAgentViewModel(id, value)).filter((item) => Number(item.raw.deleted_at ?? 0) <= 0)} groups={groupsState.records} sendCommand={sendCommand} onUnavailable={onCommandUnavailable} onClose={() => setOrganizationTarget(null)} /> : null}
      </ModalDialog>
      <ModalDialog title="Recently deleted" description="Agents remain restorable for seven days; preserved worktrees are removed only after permanent deletion." size="large" isOpen={deletedOpen} onOpenChange={setDeletedOpen}>
        <div className={styles.deletedAgents}>{deletedAgents.length ? deletedAgents.map((agent) => <article key={agent.id}><div><strong>{agent.name}</strong><span>{agent.kind} · deleted {new Date(Number(agent.raw.deleted_at) * 1_000).toLocaleString()}</span><small>{agent.worktreePath ? `Worktree preserved: ${agent.worktreePath}` : 'No worktree'}</small></div><Button tone="primary" onPress={() => { if (!sendCommand({ cmd: 'restore_agent', id: agent.id })) onCommandUnavailable(); }}>Restore</Button><Button tone="danger" onPress={() => { setDeletedOpen(false); setPurgeTarget(agent); }}>Delete permanently…</Button></article>) : <StateSurface title="No recently deleted agents" description="Deleted agents will remain available here during their restore window." />}</div>
      </ModalDialog>
      <ModalDialog title="Delete permanently?" description={purgeTarget ? `${purgeTarget.name} · ${purgeTarget.id}` : ''} size="small" isOpen={Boolean(purgeTarget)} onOpenChange={(open) => { if (!open) setPurgeTarget(null); }}>
        <div className={styles.removeDialog}><p>This permanently removes the agent and any unshared tracked worktree. This cannot be undone.</p><footer><Button tone="quiet" onPress={() => setPurgeTarget(null)}>Cancel</Button><Button tone="danger" onPress={() => { if (purgeTarget && !sendCommand({ cmd: 'purge_agent_now', id: purgeTarget.id })) onCommandUnavailable(); setPurgeTarget(null); }}>Delete permanently</Button></footer></div>
      </ModalDialog>
    </section>
  );
}
