import { AgentSettingsDialog } from './AgentSettingsDialog';
import { savedWindowBounds } from '../../host/windowState';
import { useMemo, useRef, useState, type CSSProperties } from 'react';

import type { TorqueCommand } from '../../protocol';
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
  buildAgentTree,
  toAgentViewModel,
  visibleAgentTreeRows,
  type AgentViewModel,
  type VisibleAgentTreeRow,
} from './model';
import { AgentDetailWorkspace } from './AgentDetailWorkspace';
import { AgentCreateDialog } from './AgentCreateDialog';
import styles from './AgentWorkspace.module.css';
import { WorktreeInspector } from './WorktreeInspector';
import { useWorktreeToolbar } from './useWorktreeToolbar';
import { WorktreeOperationDialog } from './WorktreeOperationDialog';

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

interface AgentTreeRowProps {
  row: VisibleAgentTreeRow;
  selected: boolean;
  focused: boolean;
  taskTitle: string;
  collapsed: boolean;
  onToggle: () => void;
  onSelect: () => void;
  onFocus: () => void;
  onRestart: () => void;
  onRelaunch: () => void;
  onClearContext: () => void;
  onInspectWorktree: () => void;
  onOrganize: () => void;
  onCopyId: () => void;
  onCopyName: () => void;
  onRemove: () => void;
}

function AgentTreeRow({ row, selected, focused, taskTitle, collapsed, onToggle, onSelect, onFocus, onRestart, onRelaunch, onClearContext, onInspectWorktree, onOrganize, onCopyId, onCopyName, onRemove }: AgentTreeRowProps) {
  const { agent } = row;
  return (
    <div
      role="treeitem"
      aria-selected={selected}
      aria-level={row.depth + 1}
      aria-expanded={row.childCount ? !collapsed : undefined}
      className={`${styles.agentTreeRow} ${selected ? styles.agentSelected : ''} ${focused ? styles.agentFocused : ''}`}
      data-agent-id={agent.id}
      tabIndex={focused ? 0 : -1}
      onClick={onSelect}
      onDoubleClick={onFocus}
      onFocus={onFocus}
      aria-label={`${agent.name}, ${agent.kind}, ${agent.status}`}
      style={{ '--tree-depth': row.depth } as CSSProperties}
    >
      <span className={styles.treeGuide} aria-hidden="true" />
      {row.childCount ? <button className={styles.treeToggle} type="button" aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${agent.name}`} onClick={(event) => { event.stopPropagation(); onToggle(); }}>{collapsed ? '›' : '⌄'}</button> : <span className={styles.treeToggleSpacer} />}
      <span className={`${styles.statusDot} ${styles[`tone_${statusTone(agent)}`] ?? ''}`} />
      <div className={styles.treeIdentity}>
        <span><strong>{agent.name}</strong>{row.orphaned ? <small className={styles.orphanBadge}>missing owner</small> : null}</span>
        <small>{agent.kind}{agent.role ? ` · ${agent.role}` : agent.provider ? ` · ${agent.provider}` : ''} · {agentStatusLabel(agent)}</small>
      </div>
      <div className={styles.treeMeta}>
        {agent.currentTaskId ? <span title={taskTitle || agent.currentTaskId}>{taskTitle || agent.currentTaskId}</span> : null}
        {row.descendantCount ? <span>{row.descendantCount} report{row.descendantCount === 1 ? '' : 's'}</span> : null}
        {agent.contextPercent ? <span>{Math.round(agent.contextPercent)}% ctx</span> : null}
        {agent.needsAttention ? <span className={styles.attention}>attention</span> : null}
      </div>
      <ActionMenu label={`Actions for ${agent.name}`}>
        <ActionMenuItem onAction={onRestart} isDisabled={agent.cellType === 'terminal'}>Restart</ActionMenuItem>
        <ActionMenuItem onAction={onRelaunch}>Relaunch</ActionMenuItem>
        <ActionMenuItem onAction={onClearContext} isDisabled={agent.cellType === 'terminal'}>Clear context</ActionMenuItem>
        <ActionMenuItem onAction={onInspectWorktree} isDisabled={!agent.worktreePath}>Inspect worktree…</ActionMenuItem>
        <ActionMenuItem onAction={onOrganize}>Move or reorder…</ActionMenuItem>
        <ActionMenuItem onAction={onCopyId}>Copy ID</ActionMenuItem>
        <ActionMenuItem onAction={onCopyName}>Copy name</ActionMenuItem>
        <ActionMenuItem onAction={onRemove}>Delete…</ActionMenuItem>
      </ActionMenu>
    </div>
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
  const [parentId, setParentId] = useState(agent.parentId);
  const [before, setBefore] = useState('');
  const parentChanged = agent.cellType === 'terminal' && parentId !== agent.parentId;
  const ownerId = agent.kind === 'worker' ? agent.ownerEngineerId : agent.kind === 'engineer' ? agent.hiredByArchitectId : '';
  const candidates = agents.filter((item) => item.id !== agent.id && (parentId
    ? item.parentId === parentId
    : item.group === targetGroup && !item.parentId && (!ownerId || (item.ownerEngineerId || item.hiredByArchitectId) === ownerId)));
  const parents = agents.filter((item) => item.cellType === 'agent');
  const issue = (command: TorqueCommand) => { const ok = sendCommand(command); if (!ok) onUnavailable(); return ok; };

  return <form className={styles.organizationForm} onSubmit={(event) => {
    event.preventDefault();
    if (parentChanged && !issue({ cmd: 'reparent_terminal', id: agent.id, parent_id: parentId })) return;
    if (parentId) {
      if (!issue({ cmd: 'reorder_child', id: agent.id, parent_id: parentId, before })) return;
    } else if (!issue({ cmd: 'move_agent', id: agent.id, target_group: targetGroup, before })) return;
    // Classic child-order commands emit the parent but not the children index.
    // Rehydrate that index after the ordered command sequence completes.
    if (agent.cellType === 'terminal') issue({ cmd: 'resync' });
    onClose();
  }}>
    <p>Order follows the saved group and terminal-child lists. Ownership relationships remain unchanged; a group move may display an agent separately from its owner.</p>
    {agent.cellType === 'terminal' ? <label>Terminal parent<select value={parentId} onChange={(event) => { const next = event.target.value; setParentId(next); setBefore(''); const parent = parents.find((item) => item.id === next); if (parent) setTargetGroup(parent.group); }}><option value="">Standalone terminal</option>{parents.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.group}</option>)}</select></label> : null}
    <label>Group<select value={targetGroup} disabled={Boolean(parentId)} onChange={(event) => { setTargetGroup(event.target.value); setBefore(''); }}>{groupNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
    <label>Position<select value={before} onChange={(event) => setBefore(event.target.value)}><option value="">At end</option>{candidates.map((item) => <option key={item.id} value={item.id}>Before {item.name}</option>)}</select></label>
    {ownerId ? <p>Owned by {agents.find((item) => item.id === ownerId)?.name || ownerId}. Reordering applies among siblings in the ownership tree.</p> : null}
    <footer><Button tone="quiet" type="button" onPress={onClose}>Cancel</Button><Button tone="primary" type="submit" isDisabled={!targetGroup}>Apply</Button></footer>
  </form>;
}

interface FocusPanelProps {
  agent: AgentViewModel;
  terminal: AgentViewModel;
  detachedTerminal: Record<string, unknown> | null;
  messages: unknown;
  messageTarget?: AgentViewModel | null;
  messageHistory?: unknown;
  host: DesktopHost;
  sendCommand: CommandSender;
  onUnavailable: () => void;
  onDetachAgent: () => void;
  onInspectWorktree: () => void;
  onCreateWorktree: () => void;
  onCheckpoint: () => void;
  worktreeDisabled: boolean;
  onOrganize: () => void;
  onRemove: () => void;
  active?: boolean;
  terminalOnly?: boolean;
  directMessagesHeight?: number;
  composeHeight?: number;
}

function FocusPanel({ agent, terminal, detachedTerminal, messages, messageTarget = null, messageHistory, host, sendCommand, onUnavailable, onDetachAgent, onInspectWorktree, onCreateWorktree, onCheckpoint, worktreeDisabled, onOrganize, onRemove, active = true, terminalOnly = false, directMessagesHeight = 0, composeHeight = 0 }: FocusPanelProps) {
  const [settingsTarget, setSettingsTarget] = useState<AgentViewModel | null>(null);
  const run = (command: Record<string, unknown>) => { if (!sendCommand(command as { cmd: string })) onUnavailable(); };
  const focusDetached = () => {
    const label = text(detachedTerminal?.label);
    if (label) void host.focusWindow(label);
  };

  return (
    <section className={`${styles.focusPanel} ${agent.cellType === 'terminal' ? styles.focusTerminal : ''} ${terminalOnly ? styles.terminalOnly : ''}`} aria-label={`Focused ${agent.cellType} ${agent.name}`}>
      {!terminalOnly ? <header className={styles.focusHeader}>
        <div><span className={`${styles.statusDot} ${styles[`tone_${statusTone(agent)}`] ?? ''}`} /><div><h2>{agent.name}</h2><p>{agent.kind} · {agent.status}{agent.provider ? ` · ${agent.provider}` : ''}</p></div></div>
        <div>
          {agent.cellType === 'agent' ? <Button tone="quiet" onPress={() => setSettingsTarget(agent)}>Settings</Button> : null}
          {hasHostCapability(host, 'detach-panel') ? <span className={styles.detachIconWrap} title="Detach selected agent workspace"><Button className={styles.detachIcon ?? ''} tone="quiet" aria-label="Detach selected agent workspace" onPress={onDetachAgent}>↗</Button></span> : null}
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
        <Button tone="quiet" isDisabled={worktreeDisabled} onPress={agent.worktreePath ? onInspectWorktree : onCreateWorktree}>{agent.worktreePath ? 'Inspect diff' : 'Create'}</Button>
        <Button tone="quiet" onPress={onCheckpoint} isDisabled={!agent.worktreePath || worktreeDisabled}>Checkpoint</Button>
        <Button tone="quiet" onPress={onInspectWorktree} isDisabled={!agent.worktreePath || worktreeDisabled}>Preflight merge</Button>
      </div> : null}

      <div className={styles.terminalHost}>
        {detachedTerminal && !terminalOnly
          ? <StateSurface title="Terminal detached" description="The PTY is owned by its native window, preventing competing focus and resize events." action={<Button tone="primary" onPress={focusDetached}>Focus terminal window</Button>} />
          : <TerminalWorkspace agent={agent} terminal={terminal} messages={messages} messageTarget={messageTarget} messageHistory={messageHistory} sendCommand={sendCommand} onUnavailable={onUnavailable} showConversation={!terminalOnly} active={active} directMessagesHeight={directMessagesHeight} composeHeight={composeHeight} />}
      </div>

      {settingsTarget ? <AgentSettingsDialog target={settingsTarget} onClose={() => setSettingsTarget(null)} /> : null}
    </section>
  );
}

export interface AgentWorkspaceProps {
  group: string;
  host: DesktopHost;
  sendCommand: CommandSender;
  onCommandUnavailable: () => void;
  terminalOnly?: boolean;
  active?: boolean;
}

export function AgentWorkspace({ group, host, sendCommand, onCommandUnavailable, terminalOnly = false, active = true }: AgentWorkspaceProps) {
  const dispatch = useAppDispatch();
  const worktreeToolbar = useWorktreeToolbar(active);
  const { records, digestSettings, digestBufferStats, digestSentEvents, engineerBufferStats, engineerSentEvents } = useAppSelector(selectAgentsState);
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
  const messageTarget = selected?.cellType === 'agent' ? selected : selected?.parentId && records[selected.parentId] ? toAgentViewModel(selected.parentId, records[selected.parentId]) : null;
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
  const inspectorAgent = worktreeTarget && records[worktreeTarget.id] ? toAgentViewModel(worktreeTarget.id, records[worktreeTarget.id]) : null;
  const [organizationTarget, setOrganizationTarget] = useState<AgentViewModel | null>(null);
  const [deletedOpen, setDeletedOpen] = useState(false);
  const [purgeTarget, setPurgeTarget] = useState<AgentViewModel | null>(null);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(() => new Set());
  const cardsRef = useRef<HTMLDivElement>(null);
  const tree = useMemo(() => buildAgentTree(hierarchy, groupsState.records[group], groupsState.children), [hierarchy, groupsState.records, groupsState.children, group]);
  const visibleRows = useMemo(() => visibleAgentTreeRows(tree, collapsedIds), [tree, collapsedIds]);
  const orderedAgents = visibleRows.map((row) => row.agent);

  const effectiveFocusedId = focusedId && orderedAgents.some((agent) => agent.id === focusedId)
    ? focusedId
    : orderedAgents.some((agent) => agent.id === selected?.id) ? selected?.id ?? null : orderedAgents[0]?.id ?? null;

  const selectAgent = (agent: AgentViewModel, focusPty = false) => {
    dispatch(workspaceUiActions.setSelectedAgent(agent.id));
    if (agent.cellType === 'terminal' && workspaceUi.agentsViewMode === 'activity') {
      dispatch(workspaceUiActions.setAgentsViewMode('live'));
    }
    setFocusedId(agent.id);
    if (!sendCommand({ cmd: 'ui_select_agent', id: agent.id })) onCommandUnavailable();
    if (focusPty && !sendCommand({ cmd: 'focus_agent', id: agent.id })) onCommandUnavailable();
  };

  const detachAgents = async () => {
    try {
      const current = asRecord(workspace.detachedPanels);
      const panel = 'agents';
      const existing = asRecord(current[panel]);
      if (text(existing.label)) { await host.focusWindow(text(existing.label)); return; }
      const bounds = savedWindowBounds(existing, { width: 1120, height: 760 });
      const detached = await host.detachPanel({ panel, bounds });
      if (!sendCommand({ cmd: 'ui_set_detached_panels', detached_panels: { ...current, [panel]: { label: detached.label, bounds } } })) onCommandUnavailable();
    } catch { onCommandUnavailable(); }
  };

  const agentCount = hierarchy.all.filter((agent) => agent.cellType === 'agent').length;
  const terminalCount = hierarchy.all.filter((agent) => agent.cellType === 'terminal').length;
  const focusStep = (direction: number) => {
    if (!orderedAgents.length) return;
    const current = effectiveFocusedId ? orderedAgents.findIndex((agent) => agent.id === effectiveFocusedId) : -1;
    const next = orderedAgents[(current + direction + orderedAgents.length) % orderedAgents.length];
    if (!next) return;
    setFocusedId(next.id);
    requestAnimationFrame(() => cardsRef.current?.querySelector<HTMLElement>(`[data-agent-id="${CSS.escape(next.id)}"]`)?.focus());
  };

  const taskTitle = (agent: AgentViewModel) => text(asRecord(tasks[agent.currentTaskId]).task);
  const renderTreeRow = (row: VisibleAgentTreeRow) => {
    const { agent } = row;
    return <AgentTreeRow key={agent.id} row={row} collapsed={collapsedIds.has(agent.id)} selected={selected?.id === agent.id} focused={effectiveFocusedId === agent.id} taskTitle={taskTitle(agent)} onToggle={() => setCollapsedIds((current) => { const next = new Set(current); if (next.has(agent.id)) next.delete(agent.id); else next.add(agent.id); return next; })} onSelect={() => selectAgent(agent)} onFocus={() => setFocusedId(agent.id)} onRestart={() => { if (!sendCommand({ cmd: 'restart_agent', id: agent.id })) onCommandUnavailable(); }} onRelaunch={() => { if (!sendCommand({ cmd: 'relaunch_agent', id: agent.id })) onCommandUnavailable(); }} onClearContext={() => { if (!sendCommand({ cmd: 'clear_agent_context', id: agent.id })) onCommandUnavailable(); }} onInspectWorktree={() => setWorktreeTarget(agent)} onOrganize={() => setOrganizationTarget(agent)} onCopyId={() => { void navigator.clipboard.writeText(agent.id); }} onCopyName={() => { void navigator.clipboard.writeText(agent.name); }} onRemove={() => setRemoveTarget(agent)} />;
  };

  const setViewMode = (mode: 'live' | 'activity') => {
    dispatch(workspaceUiActions.setAgentsViewMode(mode));
  };
  const viewControl = (agent: AgentViewModel) => <div className={styles.viewSwitch} role="tablist" aria-label={`View for ${agent.name}`}><button role="tab" aria-selected={workspaceUi.agentsViewMode === 'live'} onClick={() => setViewMode('live')}>Live</button><button role="tab" aria-selected={workspaceUi.agentsViewMode === 'activity'} onClick={() => setViewMode('activity')} disabled={agent.cellType !== 'agent'}>Activity</button></div>;

  const openCreate = (kind: 'architect' | 'engineer' | 'worker' | 'terminal') => {
    [{ cmd: 'get_config', group }, { cmd: 'get_group_settings', group }].forEach((command) => {
      if (!sendCommand(command)) onCommandUnavailable();
    });
    dispatch(workspaceUiActions.setCreateAgentKind(kind));
  };

  if (!group) return <StateSurface title="Choose a group" description="Agents are scoped to the active Torque group." />;
  if (terminalOnly) {
    return selected && terminalChoices.length ? <FocusPanel agent={selected} terminal={terminalChoices.find((cell) => cell.id === selectedTerminalId) ?? terminalChoices[0] ?? selected} detachedTerminal={null} messages={messageTarget ? messagesState.direct[messageTarget.id] : []} messageTarget={messageTarget} messageHistory={messagesState.history[selected.id]} host={host} sendCommand={sendCommand} onUnavailable={onCommandUnavailable} onDetachAgent={() => { void detachAgents(); }} onInspectWorktree={() => setWorktreeTarget(selected)} onCreateWorktree={() => worktreeToolbar.begin(selected, 'create')} onCheckpoint={() => worktreeToolbar.begin(selected, 'checkpoint')} worktreeDisabled={!worktreeToolbar.ready || worktreeToolbar.blocked} onOrganize={() => setOrganizationTarget(selected)} onRemove={() => setRemoveTarget(selected)} directMessagesHeight={Number(workspace.terminalDirectMessagesHeight) || 0} composeHeight={Number(workspace.terminalComposeHeight) || 0} terminalOnly /> : <StateSurface title="No active terminal" description="Select or relaunch an agent in the main Torque window." />;
  }

  return (
    <section className={styles.workspace} aria-labelledby="agents-heading">
      <header className={styles.workspaceHeader}><div><p>Workspace / {group}</p><h1 id="agents-heading">Agents</h1></div><div><span>{agentCount} {agentCount === 1 ? 'agent' : 'agents'}{terminalCount ? ` · ${terminalCount} ${terminalCount === 1 ? 'terminal' : 'terminals'}` : ''}</span>{deletedAgents.length ? <Button tone="quiet" onPress={() => setDeletedOpen(true)}>Recently deleted · {deletedAgents.length}</Button> : null}<ActionMenu label="Create agent or terminal" trigger={<Button tone="primary" aria-label="Create agent or terminal">＋ New</Button>}><ActionMenuItem onAction={() => openCreate('architect')}>New Architect…</ActionMenuItem><ActionMenuItem onAction={() => openCreate('engineer')}>New Engineer…</ActionMenuItem><ActionMenuItem onAction={() => openCreate('worker')}>New Worker…</ActionMenuItem><ActionMenuItem onAction={() => openCreate('terminal')}>New Terminal…</ActionMenuItem></ActionMenu>{hasHostCapability(host, 'detach-panel') ? <span className={styles.detachIconWrap} title="Detach Agents workspace"><Button className={styles.detachIcon ?? ''} tone="quiet" aria-label="Detach Agents workspace" onPress={() => { void detachAgents(); }}>↗</Button></span> : null}</div></header>
      {!hierarchy.all.length ? <div className={styles.emptyAgents}>
        <StateSurface title="Start an agent workspace" description="Create an Architect or Engineer to lead work, a Worker for a focused task, or a standalone terminal for direct shell access." action={<div className={styles.emptyAgentActions}><Button tone="primary" onPress={() => openCreate('worker')}>Create Worker</Button><Button tone="quiet" onPress={() => openCreate('terminal')}>Open Terminal</Button></div>} />
      </div> : <div className={styles.split}>
        <div
          className={styles.hierarchy}
          ref={cardsRef}
          role="tree"
          aria-label="Agent ownership hierarchy"
          onKeyDown={(event) => {
            const target = event.target as HTMLElement;
            if (target.closest('button, input, textarea, [role="menuitem"]')) return;
            const row = visibleRows.find((item) => item.agent.id === effectiveFocusedId);
            if (event.key === 'ArrowDown') { event.preventDefault(); focusStep(1); }
            if (event.key === 'ArrowUp') { event.preventDefault(); focusStep(-1); }
            if (event.key === 'ArrowRight' && row?.childCount) { event.preventDefault(); if (collapsedIds.has(row.agent.id)) setCollapsedIds((current) => { const next = new Set(current); next.delete(row.agent.id); return next; }); else focusStep(1); }
            if (event.key === 'ArrowLeft' && row?.childCount && !collapsedIds.has(row.agent.id)) { event.preventDefault(); setCollapsedIds((current) => new Set(current).add(row.agent.id)); }
            if (event.key === 'Enter') { const item = orderedAgents.find((agent) => agent.id === effectiveFocusedId); if (item) { event.preventDefault(); selectAgent(item, true); } }
            if ((event.key === 'Delete' || event.key === 'Backspace') && effectiveFocusedId) { const item = orderedAgents.find((agent) => agent.id === effectiveFocusedId); if (item) { event.preventDefault(); setRemoveTarget(item); } }
          }}
        >
          <header className={styles.hierarchyHeader}><div><strong>Ownership</strong><span>Architect → Engineer → Worker</span></div><Button tone="quiet" onPress={() => setCollapsedIds(new Set())}>Expand all</Button></header>
          <div className={styles.treeRows}>{visibleRows.map(renderTreeRow)}</div>
        </div>
        <div className={styles.detailHost}>
          {selected ? <header className={styles.detailViewBar}><span>Agent view</span>{viewControl(selected)}</header> : null}
          <div className={`${styles.detailPane} ${workspaceUi.agentsViewMode === 'live' ? '' : styles.workspaceHidden}`} aria-hidden={workspaceUi.agentsViewMode !== 'live'}>
            {selected ? <FocusPanel agent={selected} terminal={selected} detachedTerminal={terminalEntry} messages={messageTarget ? messagesState.direct[messageTarget.id] : []} messageTarget={messageTarget} messageHistory={messagesState.history[selected.id]} host={host} sendCommand={sendCommand} onUnavailable={onCommandUnavailable} onDetachAgent={() => { void detachAgents(); }} onInspectWorktree={() => setWorktreeTarget(selected)} onCreateWorktree={() => worktreeToolbar.begin(selected, 'create')} onCheckpoint={() => worktreeToolbar.begin(selected, 'checkpoint')} worktreeDisabled={!worktreeToolbar.ready || worktreeToolbar.blocked} onOrganize={() => setOrganizationTarget(selected)} onRemove={() => setRemoveTarget(selected)} directMessagesHeight={Number(workspace.terminalDirectMessagesHeight) || 0} composeHeight={Number(workspace.terminalComposeHeight) || 0} active={active && workspaceUi.agentsViewMode === 'live'} /> : <StateSurface title="Select an agent" description="Choose an agent to inspect status, worktree, settings, messages, and terminal." />}
          </div>
          <div className={`${styles.detailPane} ${workspaceUi.agentsViewMode === 'activity' ? '' : styles.workspaceHidden}`} aria-hidden={workspaceUi.agentsViewMode !== 'activity'}>
            {workspaceUi.agentsViewMode === 'activity' ? selected?.cellType === 'agent' ? <AgentDetailWorkspace active={active} key={selected.id} agent={selected} group={group} catalog={catalog} responses={auxiliaryResponses} tasks={tasks} directMessages={messagesState.direct[selected.id]} peerThreads={messagesState.peerThreads} digestSettings={digestSettings[selected.id]} digestBufferStats={digestBufferStats[selected.id] ?? engineerBufferStats[group]} digestSentEvents={digestSentEvents[selected.id] ?? engineerSentEvents[group]} sendCommand={sendCommand} onUnavailable={onCommandUnavailable} /> : <StateSurface title="Select an agent" description="Activity is available for Architects, Engineers, and Workers rather than standalone terminals." /> : null}
          </div>
        </div>
      </div>
      }

      <ModalDialog title="Delete agent?" description={removeTarget ? `${removeTarget.name} · ${removeTarget.kind}` : ''} size="small" isOpen={Boolean(removeTarget)} onOpenChange={(open) => { if (!open) setRemoveTarget(null); }}>
        <div className={styles.removeDialog}><p>This stops the live session and moves supported principals into Torque’s restore window. Worktree safety rules still apply.</p><footer><Button tone="quiet" onPress={() => setRemoveTarget(null)}>Cancel</Button><Button tone="danger" onPress={() => { if (removeTarget && !sendCommand({ cmd: 'remove_agent', id: removeTarget.id })) onCommandUnavailable(); setRemoveTarget(null); }}>Delete agent</Button></footer></div>
      </ModalDialog>
      {workspaceUi.createAgentKind ? <AgentCreateDialog key={workspaceUi.createAgentKind} open initialKind={workspaceUi.createAgentKind} group={group} agents={hierarchy.all} catalog={catalog} sendCommand={sendCommand} onCreated={(id) => { dispatch(workspaceUiActions.setSelectedAgent(id)); dispatch(workspaceUiActions.setAgentsViewMode('live')); if (!sendCommand({ cmd: 'ui_select_agent', id })) onCommandUnavailable(); }} onClose={() => dispatch(workspaceUiActions.setCreateAgentKind(null))} /> : null}
      <WorktreeOperationDialog controller={worktreeToolbar} active={active} />
      {worktreeTarget ? <WorktreeInspector key={worktreeTarget.id} agent={inspectorAgent && !Number(inspectorAgent.raw.deleted_at) ? inspectorAgent : null} active={active} responses={auxiliaryResponses} onClose={() => setWorktreeTarget(null)} /> : null}
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
