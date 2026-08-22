import { useMemo, useState } from 'react';

import { Button, ModalDialog } from '../../design/primitives';
import type { CommandSender } from '../board/BoardPanel';
import type { AgentViewModel } from './model';
import styles from './AgentWorkspace.module.css';

type CreateKind = 'architect' | 'engineer' | 'worker' | 'terminal';

interface CatalogBundle {
  agentClasses: unknown;
  roles: unknown;
  specializations: unknown;
  templates: unknown;
}

interface AgentCreateDialogProps {
  open: boolean;
  initialKind?: CreateKind;
  group: string;
  agents: AgentViewModel[];
  catalog: CatalogBundle;
  sendCommand: CommandSender;
  onUnavailable: () => void;
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
    const separator = trimmed.indexOf('=');
    if (separator <= 0) return;
    result[trimmed.slice(0, separator).trim()] = trimmed.slice(separator + 1);
  });
  return result;
}

function csv(value: string): string[] {
  return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
}

export function AgentCreateDialog({
  open,
  initialKind = 'worker',
  group,
  agents,
  catalog,
  sendCommand,
  onUnavailable,
  onClose,
}: AgentCreateDialogProps) {
  const [kind, setKind] = useState<CreateKind>(initialKind);
  const [name, setName] = useState('');
  const [agentClassId, setAgentClassId] = useState('');
  const [template, setTemplate] = useState('');
  const [hiringArchitectId, setHiringArchitectId] = useState('');
  const [parentId, setParentId] = useState('');
  const [provider, setProvider] = useState('');
  const [bootCommand, setBootCommand] = useState('');
  const [model, setModel] = useState('');
  const [reasoningEffort, setReasoningEffort] = useState('');
  const [fastMode, setFastMode] = useState('inherit');
  const [customInstructions, setCustomInstructions] = useState('');
  const [autonomyMode, setAutonomyMode] = useState('');
  const [specializations, setSpecializations] = useState('');
  const [digestVerbosity, setDigestVerbosity] = useState('');
  const [pushInterval, setPushInterval] = useState('');
  const [maxInterval, setMaxInterval] = useState('');
  const [heartbeatInterval, setHeartbeatInterval] = useState('');
  const [enabledEvents, setEnabledEvents] = useState('');
  const [directory, setDirectory] = useState('');
  const [shell, setShell] = useState('');
  const [environment, setEnvironment] = useState('');
  const [commandArgs, setCommandArgs] = useState('');
  const [initScript, setInitScript] = useState('');
  const [icon, setIcon] = useState('');
  const [worktree, setWorktree] = useState(false);
  const [worktreeBaseDir, setWorktreeBaseDir] = useState('');
  const [worktreeBaseBranch, setWorktreeBaseBranch] = useState('');
  const [worktreeName, setWorktreeName] = useState('');
  const [autoCheckpoint, setAutoCheckpoint] = useState(true);
  const [checkpointOnProgress, setCheckpointOnProgress] = useState(false);
  const [mergeSquash, setMergeSquash] = useState(true);

  const classes = useMemo(() => optionList(catalog.agentClasses)
    .filter((item) => !item.kind || item.kind === kind), [catalog.agentClasses, kind]);
  const roleOptions = useMemo(() => {
    const byId = new Map<string, NamedOption>();
    [...optionList(catalog.roles), ...optionList(catalog.templates)].forEach((item) => byId.set(item.id, item));
    return [...byId.values()];
  }, [catalog.roles, catalog.templates]);
  const specializationOptions = useMemo(() => optionList(catalog.specializations), [catalog.specializations]);
  const architects = agents.filter((agent) => agent.kind === 'architect');
  const parents = agents.filter((agent) => agent.cellType === 'agent');

  const send = (command: Record<string, unknown>) => {
    if (!sendCommand(command as { cmd: string })) {
      onUnavailable();
      return false;
    }
    return true;
  };

  const submit = () => {
    const identity = name.trim();
    if (!identity || !group) return;
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
      enabled_events: enabledEvents.trim() ? csv(enabledEvents) : '',
    };
    Object.entries(digestValues).forEach(([key, value]) => { if (value !== '') digestSettings[key] = value; });

    if (kind === 'engineer' && hiringArchitectId) {
      if (send({
        cmd: 'architect_engineer_hire',
        architect_id: hiringArchitectId,
        name: identity,
        specializations: csv(specializations),
      })) onClose();
      return;
    }

    if (kind === 'terminal') {
      const payload: Record<string, unknown> = { cmd: 'add_terminal', name: identity, group };
      if (parentId) payload.parent_id = parentId;
      if (directory.trim()) payload.directory = directory.trim();
      if (shell) payload.shell = shell;
      if (bootCommand.trim()) payload.command = bootCommand.trim();
      if (commandArgs.trim()) payload.command_args = commandArgs.trim();
      if (initScript.trim()) payload.init_script = initScript.trim();
      const env = parseEnvironment(environment);
      if (Object.keys(env).length) payload.env_vars = env;
      if (send(payload)) onClose();
      return;
    }

    const payload: Record<string, unknown> = agentClassId
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
    if (send(payload)) onClose();
  };

  const principal = kind === 'architect' || kind === 'engineer';
  return <ModalDialog
    title={`New ${kind}`}
    description={`Create in ${group}. Launch and worktree values override the group defaults.`}
    size="large"
    isOpen={open}
    onOpenChange={(value) => { if (!value) onClose(); }}
  >
    <form className={styles.creationForm} onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <section>
        <h3>Identity</h3>
        <div className={styles.formGrid}>
          <label>Kind<select aria-label="Agent kind" value={kind} onChange={(event) => { setKind(event.target.value as CreateKind); setAgentClassId(''); }}><option value="architect">Architect</option><option value="engineer">Engineer</option><option value="worker">Worker</option><option value="terminal">Terminal</option></select></label>
          <label>Name<input autoFocus value={name} onChange={(event) => setName(event.target.value)} required /></label>
          {kind !== 'terminal' ? <label>Agent Class<select value={agentClassId} onChange={(event) => setAgentClassId(event.target.value)}><option value="">Group default</option>{classes.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label> : null}
          {kind === 'worker' ? <label>Role / template<select value={template} onChange={(event) => setTemplate(event.target.value)}><option value="">Group default</option>{roleOptions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label> : null}
          {kind === 'engineer' && architects.length ? <label>Hiring Architect<select value={hiringArchitectId} onChange={(event) => setHiringArchitectId(event.target.value)}><option value="">User-owned Engineer</option>{architects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}
          {kind === 'terminal' ? <label>Parent agent<select value={parentId} onChange={(event) => setParentId(event.target.value)}><option value="">Unattached</option>{parents.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}
          <label>Icon<input value={icon} onChange={(event) => setIcon(event.target.value)} placeholder="optional icon" /></label>
        </div>
      </section>

      {!hiringArchitectId ? <section>
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

      {principal && !hiringArchitectId ? <section>
        <h3>Behavior and delivery</h3>
        <div className={styles.formGrid}>
          <label>Autonomy mode<input value={autonomyMode} onChange={(event) => setAutonomyMode(event.target.value)} placeholder="inherit" /></label>
          <label>Digest verbosity<input value={digestVerbosity} onChange={(event) => setDigestVerbosity(event.target.value)} placeholder="inherit" /></label>
          <label>Push interval<input type="number" value={pushInterval} onChange={(event) => setPushInterval(event.target.value)} placeholder="inherit" /></label>
          <label>Maximum interval<input type="number" value={maxInterval} onChange={(event) => setMaxInterval(event.target.value)} placeholder="inherit" /></label>
          <label>Heartbeat interval<input type="number" value={heartbeatInterval} onChange={(event) => setHeartbeatInterval(event.target.value)} placeholder="inherit" /></label>
          {kind === 'engineer' ? <label>Specializations<input value={specializations} onChange={(event) => setSpecializations(event.target.value)} list="agent-specializations" placeholder="ordered, comma separated" /><datalist id="agent-specializations">{specializationOptions.map((item) => <option key={item.id} value={item.id} />)}</datalist></label> : null}
        </div>
        <label>Enabled digest events<textarea value={enabledEvents} onChange={(event) => setEnabledEvents(event.target.value)} rows={2} placeholder="event names, comma separated" /></label>
        <label>Custom instructions<textarea value={customInstructions} onChange={(event) => setCustomInstructions(event.target.value)} rows={5} /></label>
      </section> : null}

      {kind !== 'terminal' && !hiringArchitectId ? <section>
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

      <footer><Button tone="quiet" type="button" onPress={onClose}>Cancel</Button><Button tone="primary" type="submit" isDisabled={!name.trim()}>Create {kind}</Button></footer>
    </form>
  </ModalDialog>;
}
