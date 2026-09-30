import { EngineerNotificationPreset } from '../control/EngineerNotificationPreset';
import { SpecializationPicker } from '../control/SpecializationPicker';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { Button, ModalDialog } from '../../design/primitives';
import { settingsRequest } from '../control/settingsRequests';
import type { TorqueCommand } from '../../protocol';
import { useAppSelector } from '../../app/hooks';
import { useSettingsProtection } from '../../app/settingsNavigation';
import { selectAgentSettingsDefaults, selectAgentsState, selectConnection } from '../../app/store';
import { initialLaunchDraft, resolvedLaunchDraft, suggestedTerminalName, validateTemplateResponse, type LaunchDraft } from './agentCreationModel';
import type { AgentViewModel } from './model';
import { creationClassDisabledReason, creationClassLabel, useCreationClasses } from './useCreationClasses';
import { useCreationRoles } from './useCreationRoles';
import { useAgentCreation } from './useAgentCreation';
import styles from './AgentWorkspace.module.css';

type CreateKind = 'architect' | 'engineer' | 'worker' | 'terminal';

interface AgentCreateDialogProps {
  open: boolean;
  initialKind?: CreateKind;
  group: string;
  agents: AgentViewModel[];
  onCreated: (id: string) => void;
  onClose: () => void;
}

interface NamedOption {
  id: string;
  label: string;
  kind?: string;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

function optionList(value: unknown): NamedOption[] {
  const entries = Array.isArray(value)
    ? value.map((item, index) => [String(index), item] as const)
    : Object.entries(record(value));
  return entries.map(([key, raw]) => {
    if (typeof raw === 'string') return { id: raw, label: raw };
    const item = record(raw);
    return {
      id: text(item.id) || text(item.slug) || text(item.name) || key,
      label: text(item.display_name) || text(item.name) || text(item.title) || text(item.slug) || text(item.id) || key,
      kind: text(item.base_kind) || text(item.kind),
    };
  }).filter((item) => item.id);
}

function parseEnvironment(value: string): Record<string, string> {
  const result: Record<string, string> = {};
  value.split(/\r?\n/).forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return;
    const separator = line.indexOf('=');
    if (separator < 0) return;
    const key = line.slice(0, separator).trim();
    if (!key) return;
    // Values are literal data, including empty values and trailing whitespace.
    result[key] = line.slice(separator + 1);
  });
  return result;
}

function csv(value: string): string[] {
  return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
}

export function AgentCreateDialog({
  open,
  initialKind = 'worker',
  group: initialGroup,
  agents,
  onCreated,
  onClose,
}: AgentCreateDialogProps) {
  const [group] = useState(initialGroup);
  const connection = useAppSelector(selectConnection);
  const [kind, setKind] = useState<CreateKind>(initialKind);
  const [nameDraft, setName] = useState<string | null>(null);
  const [agentClassId, setAgentClassId] = useState('');
  const [template, setTemplate] = useState('');
  const [hiringArchitectId, setHiringArchitectId] = useState('');
  const [parentId, setParentId] = useState('');
  const [customInstructions, setCustomInstructions] = useState('');
  const [autonomyMode, setAutonomyMode] = useState('');
  const defaults = useAppSelector(selectAgentSettingsDefaults);
  const existingAgents = useAppSelector(selectAgentsState).records;
  const name = nameDraft ?? (kind === 'terminal'
    ? suggestedTerminalName(text(record(defaults.groups[group]).terminal_name_prefix), existingAgents)
    : '');
  const inheritedSpecializations = record(defaults.groups[group]).default_engineer_specializations;
  const [specializationDraft, setSpecializations] = useState<string | null>(null);
  const specializations = specializationDraft ?? (Array.isArray(inheritedSpecializations) ? inheritedSpecializations.filter((item): item is string => typeof item === 'string').join(', ') : '');
  const [digestVerbosity, setDigestVerbosity] = useState('');
  const [pushInterval, setPushInterval] = useState('');
  const [maxInterval, setMaxInterval] = useState('');
  const [heartbeatInterval, setHeartbeatInterval] = useState('');
  const [enabledEventsDraft, setEnabledEvents] = useState<string | null>(null);
  const enabledEvents = enabledEventsDraft ?? '';
  const inheritedDigest = record(defaults.engineers[group]);


  const [launch, setLaunch] = useState(initialLaunchDraft);
  const editedLaunch = useRef(new Set<keyof LaunchDraft>());
  const updateLaunch = <K extends keyof LaunchDraft>(key: K, value: LaunchDraft[K]) => {
    editedLaunch.current.add(key); setLaunch((current) => ({ ...current, [key]: value }));
  };
  const { provider, bootCommand, model, reasoningEffort, fastMode, directory, shell, environment, commandArgs, initScript, icon, worktree, worktreeBaseDir, worktreeBaseBranch, worktreeName, autoCheckpoint, checkpointOnProgress, mergeSquash } = launch;
  const setProvider = (value: LaunchDraft['provider']) => updateLaunch('provider', value);
  const setBootCommand = (value: LaunchDraft['bootCommand']) => updateLaunch('bootCommand', value);
  const setModel = (value: LaunchDraft['model']) => updateLaunch('model', value);
  const setReasoningEffort = (value: LaunchDraft['reasoningEffort']) => updateLaunch('reasoningEffort', value);
  const setFastMode = (value: LaunchDraft['fastMode']) => updateLaunch('fastMode', value);
  const setDirectory = (value: LaunchDraft['directory']) => updateLaunch('directory', value);
  const setShell = (value: LaunchDraft['shell']) => updateLaunch('shell', value);
  const setEnvironment = (value: LaunchDraft['environment']) => updateLaunch('environment', value);
  const setCommandArgs = (value: LaunchDraft['commandArgs']) => updateLaunch('commandArgs', value);
  const setInitScript = (value: LaunchDraft['initScript']) => updateLaunch('initScript', value);
  const setIcon = (value: LaunchDraft['icon']) => updateLaunch('icon', value);
  const setWorktree = (value: LaunchDraft['worktree']) => updateLaunch('worktree', value);
  const setWorktreeBaseDir = (value: LaunchDraft['worktreeBaseDir']) => updateLaunch('worktreeBaseDir', value);
  const setWorktreeBaseBranch = (value: LaunchDraft['worktreeBaseBranch']) => updateLaunch('worktreeBaseBranch', value);
  const setWorktreeName = (value: LaunchDraft['worktreeName']) => updateLaunch('worktreeName', value);
  const setAutoCheckpoint = (value: LaunchDraft['autoCheckpoint']) => updateLaunch('autoCheckpoint', value);
  const setCheckpointOnProgress = (value: LaunchDraft['checkpointOnProgress']) => updateLaunch('checkpointOnProgress', value);
  const setMergeSquash = (value: LaunchDraft['mergeSquash']) => updateLaunch('mergeSquash', value);
  const creation = useAgentCreation(open, onCreated, onClose);
  const { saving, error, setError, locked, requestClose } = creation;
  const lockedRef = useRef(locked);
  useLayoutEffect(() => { lockedRef.current = locked; }, [locked]);
  const errorElement = useRef<HTMLParagraphElement>(null);
  const restoreCreationFocus = useCallback(() => { errorElement.current?.focus(); }, []);
  const keepCreation = useCallback(() => {}, []);
  useSettingsProtection({ purpose: 'creation', group, dirty: false, saving: locked, discard: keepCreation, restoreFocus: restoreCreationFocus });
  const [readRetry, setReadRetry] = useState(0);
  const [resolvedRead, setResolvedRead] = useState({ key: '', error: '' });
  const resolutionKey = JSON.stringify([group, template, kind, connection.reconnectCount, readRetry]);
  const resolving = kind === 'worker' && resolvedRead.key !== resolutionKey;
  const resolutionError = resolvedRead.key === resolutionKey ? resolvedRead.error : '';
  useEffect(() => {
    if (!open || connection.status !== 'connected' || kind !== 'worker' || locked || resolvedRead.key === resolutionKey) return;
    const controller = new AbortController();
    void settingsRequest({ cmd: 'render_template', group, name: template, kind: 'worker' }, controller.signal, false, 'Launch settings').then((frame) => {
      if (controller.signal.aborted || lockedRef.current) return;
      const next = resolvedLaunchDraft(validateTemplateResponse(frame, group, template));
      setLaunch((current) => Object.fromEntries(Object.entries(next).map(([key, value]) => [key, editedLaunch.current.has(key as keyof LaunchDraft) ? current[key as keyof LaunchDraft] : value])) as LaunchDraft);
      setResolvedRead({ key: resolutionKey, error: '' });
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setResolvedRead({ key: resolutionKey, error: cause instanceof Error ? cause.message : 'Could not resolve launch settings.' }); });
    return () => controller.abort();
  }, [open, kind, group, template, resolutionKey, resolvedRead.key, locked, connection.status]);
  useEffect(() => { if (error && !saving) errorElement.current?.focus(); }, [error, saving]);

  const classPickerActive = kind !== 'terminal' && !(kind === 'engineer' && hiringArchitectId);
  const classCatalog = useCreationClasses(open && classPickerActive, group, locked);
  const classes = classCatalog.classes.filter((item) => item.base_kind === kind);
  const selectedClass = classes.find((item) => item.id === agentClassId);
  const registryError = classCatalog.issues.some((issue) => issue.severity === 'error') ? 'Fix the project Agent Class catalog errors before launching a class.' : '';
  const classError = classPickerActive && agentClassId ? classCatalog.unavailable || registryError || (selectedClass ? creationClassDisabledReason(selectedClass, kind) : 'The selected Agent Class is no longer available. Choose another class or the default.') : '';
  const roleCatalog = useCreationRoles(group, open && kind === 'worker' && !locked);
  const roleOptions = useMemo(() => {
    const byId = new Map<string, NamedOption>();
    optionList(roleCatalog.roles).forEach((item) => { if (!byId.has(item.id)) byId.set(item.id, item); });
    return [...byId.values()];
  }, [roleCatalog.roles]);
  const roleError = kind === 'worker' && template && !locked ? !roleCatalog.verified ? (roleCatalog.error || 'Refresh creation roles to verify the selected role.') : !roleOptions.some((item) => item.id === template) ? 'The selected role is no longer available. Choose another role or the group default.' : '' : '';
  const architects = agents.filter((agent) => agent.kind === 'architect');
  const parents = agents.filter((agent) => agent.cellType === 'agent');

  const create = (payload: TorqueCommand) => { setName(text(payload.name)); setSpecializations(specializations); creation.create(payload, kind); };

  const submit = () => {
    const identity = name.trim();
    if (locked || resolving || resolutionError || classError || roleError || !identity || !group) return;
    const agentSettings: Record<string, unknown> = {};
    const settingValues: Record<string, unknown> = {
      provider: provider.trim(),
      boot_command: bootCommand.trim(),
      model: model.trim(),
      reasoning_effort: reasoningEffort.trim(),
      fast_mode: fastMode === 'inherit' ? '' : fastMode,
      custom_instructions: customInstructions.trim(),
      autonomy_mode: autonomyMode,
    };
    Object.entries(settingValues).forEach(([key, value]) => { if (value !== '') agentSettings[key] = value; });
    const digestSettings: Record<string, unknown> = {};
    const digestValues: Record<string, unknown> = {
      digest_verbosity: digestVerbosity,
      push_interval: pushInterval ? Number(pushInterval) : '',
      max_interval: maxInterval ? Number(maxInterval) : '',
      heartbeat_interval: heartbeatInterval ? Number(heartbeatInterval) : '',
      enabled_events: enabledEventsDraft === null ? '' : csv(enabledEvents),
    };
    for (const [key, value] of Object.entries(digestValues)) {
      if (typeof value === 'number' && (!Number.isSafeInteger(value) || value < 0)) { setError(`${key.replaceAll('_', ' ')} must be a non-negative whole number.`); return; }
    }
    Object.entries(digestValues).forEach(([key, value]) => { if (value !== '') digestSettings[key] = value; });

    if (kind === 'engineer' && hiringArchitectId) {
      create({
        cmd: 'architect_engineer_hire',
        architect_id: hiringArchitectId,
        name: identity,
        specializations: csv(specializations),
      });
      return;
    }

    if (kind === 'terminal') {
      const payload: TorqueCommand = { cmd: 'add_terminal', name: identity, group };
      if (parentId) payload.parent_id = parentId;
      if (directory.trim()) payload.directory = directory.trim();
      if (shell) payload.shell = shell;
      if (bootCommand.trim()) payload.command = bootCommand.trim();
      if (commandArgs.trim()) payload.command_args = commandArgs.trim();
      if (initScript.trim()) payload.init_script = initScript.trim();
      const env = parseEnvironment(environment);
      if (Object.keys(env).length) payload.env_vars = env;
      create(payload);
      return;
    }

    const payload: TorqueCommand = agentClassId
      ? { cmd: 'create_agent_from_class', class_id: agentClassId, kind, name: identity, group }
      : { cmd: kind === 'architect' ? 'add_architect' : kind === 'engineer' ? 'add_engineer' : 'add_worker', name: identity, group };
    if (Object.keys(agentSettings).length) payload.agent_settings = agentSettings;
    if (Object.keys(digestSettings).length) payload.agent_digest_settings = digestSettings;
    if (template && kind === 'worker') payload.template = template;
    if (provider.trim()) payload.provider = provider.trim();
    if (bootCommand.trim()) payload.command = bootCommand.trim();
    if (model.trim()) payload.model = model.trim();
    if (reasoningEffort.trim()) payload.reasoning_effort = reasoningEffort.trim();
    if (fastMode !== 'inherit') payload.fast_mode = fastMode;
    if (directory.trim()) payload.directory = directory.trim();
    if (shell) payload.shell = shell;
    if (icon.trim()) payload.icon = icon.trim();
    if (commandArgs.trim()) payload.command_args = commandArgs.trim();
    if (initScript.trim()) payload.init_script = initScript.trim();
    const env = parseEnvironment(environment);
    if (Object.keys(env).length) payload.env_vars = env;
    if (kind === 'engineer') payload.specializations = csv(specializations);
    payload.worktree = worktree;
    if (worktree) {
      if (worktreeBaseDir.trim()) payload.worktree_base_dir = worktreeBaseDir.trim();
      if (worktreeBaseBranch.trim()) payload.worktree_base_branch = worktreeBaseBranch.trim();
      if (worktreeName.trim()) payload.worktree_name = worktreeName.trim();
      payload.worktree_auto_checkpoint = autoCheckpoint;
      payload.checkpoint_on_progress = checkpointOnProgress;
      payload.worktree_merge_squash = mergeSquash;
    }
    create(payload);
  };

  const principal = kind === 'architect' || kind === 'engineer';
  return <ModalDialog
    title={`New ${kind}`}
    description={`Create in ${group}. Launch and worktree values override the group defaults.`}
    size="large"
    isOpen={open}
    onOpenChange={(value) => { if (!value) requestClose(); }}
  >
    {error ? <p role="alert" tabIndex={-1} ref={errorElement}>{error}</p> : null}
    {locked && !saving ? creation.incomplete ? <section aria-label="Incomplete launch">
      <p>{creation.incomplete.type === 'agent' ? 'The target exists, but launch did not finish.' : 'The hire request was saved, but delivery did not finish. Review it in Planning.'} {creation.incomplete.name} · {creation.incomplete.id}</p>
      <Button tone="primary" onPress={creation.inspect}>{creation.incomplete.type === 'agent' ? 'Inspect created target' : 'Keep saved hire request'}</Button>
    </section> : <section aria-label="Creation recovery">
      <p>Creation is not confirmed. Your reviewed request is locked to prevent a duplicate. Retry the same request to recover its outcome.</p>
      <Button tone="primary" onPress={creation.retry}>Retry same creation</Button>
    </section> : null}
    {kind === 'worker' ? <div className={styles.settingsStatus}><span aria-live="polite">{resolving ? 'Resolving launch settings…' : resolutionError ? 'Launch settings unavailable' : 'Resolved role and group settings; edited fields are retained.'}</span><Button tone="quiet" isDisabled={locked || resolving} onPress={() => setReadRetry((value) => value + 1)}>Refresh launch settings</Button></div> : null}
    {resolutionError ? <p role="alert">{resolutionError} <Button tone="quiet" onPress={() => setReadRetry((value) => value + 1)}>Retry launch settings</Button></p> : null}
    <form className={styles.creationForm} onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <fieldset disabled={locked} style={{ border: 0, padding: 0, margin: 0 }}>
      <section>
        <h3>Identity</h3>
        <div className={styles.formGrid}>
          <label>Kind<select aria-label="Agent kind" value={kind} onChange={(event) => { setKind(event.target.value as CreateKind); setAgentClassId(''); }}><option value="architect">Architect</option><option value="engineer">Engineer</option><option value="worker">Worker</option><option value="terminal">Terminal</option></select></label>
          <label>Name<input autoFocus value={name} onChange={(event) => setName(event.target.value)} required /></label>
          {classPickerActive ? <label>Agent Class<select value={agentClassId} aria-describedby="creation-class-status" onChange={(event) => setAgentClassId(event.target.value)}><option value="">Default (no explicit Agent Class)</option>{agentClassId && !selectedClass ? <option value={agentClassId} disabled>Previously selected: {agentClassId}</option> : null}{classes.map((item) => <option key={text(item.id)} value={text(item.id)} disabled={Boolean(classCatalog.unavailable || registryError || creationClassDisabledReason(item, kind))}>{creationClassLabel(item)} · {text(item.version) || '1'} · {item.builtin ? 'built-in' : 'project'}{creationClassDisabledReason(item, kind) ? ' (unavailable)' : ''}</option>)}</select></label> : null}
          {kind === 'worker' ? <label>Role / template<select value={template} onChange={(event) => setTemplate(event.target.value)}><option value="">Group default</option>{template && !roleOptions.some((item) => item.id === template) ? <option value={template} disabled>Unavailable: {template}</option> : null}{roleOptions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label> : null}
          {kind === 'engineer' && architects.length ? <label>Hiring Architect<select value={hiringArchitectId} onChange={(event) => setHiringArchitectId(event.target.value)}><option value="">User-owned Engineer</option>{architects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}
          {kind === 'terminal' ? <label>Parent agent<select value={parentId} onChange={(event) => setParentId(event.target.value)}><option value="">Unattached</option>{parents.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}
          <label>Icon<input value={icon} onChange={(event) => setIcon(event.target.value)} placeholder="optional icon" /></label>
        </div>
      </section>

      {kind === 'worker' ? <section aria-label="Creation role discovery">
        <div className={styles.settingsStatus}><span aria-live="polite">{roleCatalog.loading ? 'Loading creation roles…' : 'Roles for this creation group.'}</span><Button type="button" tone="quiet" isDisabled={locked || roleCatalog.loading} onPress={roleCatalog.refresh}>Refresh creation roles</Button></div>
        {roleCatalog.error ? <p role="alert">{roleCatalog.error} <Button type="button" tone="quiet" onPress={roleCatalog.refresh}>Retry creation roles</Button></p> : roleError ? <p role="alert">{roleError}</p> : null}
      </section> : null}
      {classPickerActive ? <section aria-label="Agent Class discovery">
        <div className={styles.settingsStatus}><span id="creation-class-status" aria-live="polite">{classCatalog.loading ? 'Loading project Agent Classes…' : agentClassId ? 'The selected class is frozen at launch.' : 'The default launch freezes the default class for this agent kind.'}</span><Button type="button" tone="quiet" isDisabled={locked || classCatalog.loading} onPress={classCatalog.refresh}>Refresh Agent Classes</Button></div>
        {classCatalog.error ? <p role="alert">Class discovery failed. {classCatalog.error} <Button type="button" tone="quiet" onPress={classCatalog.refresh}>Retry Agent Classes</Button></p> : null}
        {classError && !classCatalog.error ? <p role="alert">{classError}</p> : null}
        {classCatalog.issues.length ? <details><summary>Agent Class catalog issues ({classCatalog.issues.length})</summary>{classCatalog.issues.map((issue, index) => <p key={index}>{text(issue.message)} <small>{text(issue.path)}</small></p>)}</details> : null}
      </section> : null}
      {kind === 'engineer' && hiringArchitectId ? <section><h3>Hire request</h3><p>The Engineer is created after approval in Planning.</p><div className={styles.creationSpecializations}><h4>Specializations</h4><SpecializationPicker group={group} value={csv(specializations)} onChange={(next) => setSpecializations(next.join(", "))} label="Specializations" /></div></section> : null}
      {!(kind === 'engineer' && hiringArchitectId) ? <section>
        <h3>Launch</h3>
        <div className={styles.formGrid}>
          {kind !== 'terminal' ? <label>Provider<input value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="inherit" /></label> : null}
          <label>Boot command<input value={bootCommand} onChange={(event) => setBootCommand(event.target.value)} placeholder="inherit" /></label>
          {kind !== 'terminal' ? <label>Model<input value={model} onChange={(event) => setModel(event.target.value)} placeholder="inherit" /></label> : null}
          {kind !== 'terminal' ? <label>Reasoning effort<input value={reasoningEffort} onChange={(event) => setReasoningEffort(event.target.value)} placeholder="inherit" /></label> : null}
          {kind !== 'terminal' ? <label>Fast mode<select value={fastMode} onChange={(event) => setFastMode(event.target.value)}><option value="inherit">Inherited</option><option value="on">On</option><option value="off">Off</option></select></label> : null}
          <label>Directory<input value={directory} onChange={(event) => setDirectory(event.target.value)} placeholder="group default" /></label>
          <label>Shell<select value={shell} onChange={(event) => setShell(event.target.value)}><option value="">System default</option><option value="zsh">zsh</option><option value="bash">bash</option><option value="fish">fish</option></select></label>
          <label>Command arguments<input value={commandArgs} onChange={(event) => setCommandArgs(event.target.value)} /></label>
        </div>
        <label>Environment variables<textarea value={environment} onChange={(event) => setEnvironment(event.target.value)} rows={3} placeholder={'KEY=value\nOTHER=value'} /></label>
        <label>Initialization script<textarea value={initScript} onChange={(event) => setInitScript(event.target.value)} rows={3} /></label>
      </section> : null}

      {principal && !(kind === 'engineer' && hiringArchitectId) ? <section>
        <h3>Behavior and delivery</h3>
        {kind === 'engineer' ? <EngineerNotificationPreset value={{ digest_verbosity: digestVerbosity || inheritedDigest.digest_verbosity, push_interval: pushInterval || inheritedDigest.push_interval, max_interval: maxInterval || inheritedDigest.max_interval, heartbeat_interval: heartbeatInterval || inheritedDigest.heartbeat_interval, enabled_events: enabledEventsDraft === null ? inheritedDigest.enabled_events : csv(enabledEvents) }} disabled={locked} onApply={(preset) => { setDigestVerbosity(preset.digest_verbosity); setPushInterval(String(preset.push_interval)); setMaxInterval(String(preset.max_interval)); setHeartbeatInterval(String(preset.heartbeat_interval)); setEnabledEvents(preset.enabled_events.join(', ')); }} /> : null}
        <div className={styles.formGrid}>
          <label>Autonomy mode<input value={autonomyMode} onChange={(event) => setAutonomyMode(event.target.value)} placeholder="inherit" /></label>
          <label>Digest verbosity<input value={digestVerbosity} onChange={(event) => setDigestVerbosity(event.target.value)} placeholder="inherit" /></label>
          <label>Push interval<input type="number" value={pushInterval} onChange={(event) => setPushInterval(event.target.value)} placeholder="inherit" /></label>
          <label>Maximum interval<input type="number" value={maxInterval} onChange={(event) => setMaxInterval(event.target.value)} placeholder="inherit" /></label>
          <label>Heartbeat interval<input type="number" value={heartbeatInterval} onChange={(event) => setHeartbeatInterval(event.target.value)} placeholder="inherit" /></label>
          {kind === 'engineer' ? <div className={styles.creationSpecializations}><h4>Specializations</h4><SpecializationPicker group={group} value={csv(specializations)} onChange={(next) => setSpecializations(next.join(", "))} label="Specializations" /></div> : null}
        </div>
        <label>Enabled digest events<textarea value={enabledEvents} onChange={(event) => setEnabledEvents(event.target.value)} rows={2} placeholder="event names, comma separated" /></label>
        <label>Custom instructions<textarea value={customInstructions} onChange={(event) => setCustomInstructions(event.target.value)} rows={5} /></label>
      </section> : null}

      {kind !== 'terminal' && !(kind === 'engineer' && hiringArchitectId) ? <section>
        <h3>Worktree</h3>
        <label className={styles.inlineCheck}><input type="checkbox" checked={worktree} onChange={(event) => setWorktree(event.target.checked)} />Create an isolated worktree</label>
        {worktree ? <>
          <div className={styles.formGrid}>
            <label>Base directory<input value={worktreeBaseDir} onChange={(event) => setWorktreeBaseDir(event.target.value)} placeholder="group repository" /></label>
            <label>Base branch<input value={worktreeBaseBranch} onChange={(event) => setWorktreeBaseBranch(event.target.value)} placeholder="current default" /></label>
            <label>Worktree name<input value={worktreeName} onChange={(event) => setWorktreeName(event.target.value)} /></label>
          </div>
          <div className={styles.checkGrid}>
            <label className={styles.inlineCheck}><input type="checkbox" checked={autoCheckpoint} onChange={(event) => setAutoCheckpoint(event.target.checked)} />Checkpoint on stop</label>
            <label className={styles.inlineCheck}><input type="checkbox" checked={checkpointOnProgress} onChange={(event) => setCheckpointOnProgress(event.target.checked)} />Checkpoint on progress</label>
            <label className={styles.inlineCheck}><input type="checkbox" checked={mergeSquash} onChange={(event) => setMergeSquash(event.target.checked)} />Squash merge</label>
          </div>
        </> : null}
      </section> : null}

      <footer><Button tone="quiet" type="button" isDisabled={locked} onPress={requestClose}>Cancel</Button><Button tone="primary" type="submit" isDisabled={locked || resolving || Boolean(resolutionError) || Boolean(classError) || Boolean(roleError) || !name.trim()}>{saving ? 'Creating…' : kind === 'engineer' && hiringArchitectId ? 'Request hire' : `Create ${kind}`}</Button></footer>
      </fieldset>
    </form>
  </ModalDialog>;
}
