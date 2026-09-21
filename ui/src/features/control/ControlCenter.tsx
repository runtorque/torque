import { selectProviders } from '../../app/store';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useAppDispatch, useAppSelector } from '../../app/hooks';
import {
  selectAgentsState,
  selectAuxiliaryResponseState,
  selectCatalogState,
  selectConnection,
  selectGroupsState,
  selectOperationsState,
  selectMessagesState,
  selectWorkspaceUi,
  workspaceUiActions,
} from '../../app/store';
import { Button, StateSurface } from '../../design/primitives';
import type { TorqueCommand, UnknownRecord } from '../../protocol';
import type { CommandSender } from '../board/BoardPanel';
import { records, text } from '../planning/model';
import { AgentClassLibrary } from './AgentClassLibrary';
import { BehaviorOverlayEditor, CatalogEditor } from './CatalogEditors';
import { browserHost, type DesktopHost } from '../../host';
import { readCommand } from '../../protocol/http';
import { projectionActions } from '../../app/store';
import { StructuredSettings } from './StructuredSettings';
import { PeerChat } from './PeerChat';
import { PipelineExplorer } from './PipelineExplorer';
import { LogViewer } from './LogViewer';
import { ContextPanel } from './ContextPanel';
import { ActivityPanel, HelpPanel, MissionPanel } from './OperatorPanels';
import { AppearancePreferencesPanel, ShortcutPreferencesPanel } from './WorkspacePreferences';
import styles from './ControlCenter.module.css';

type ControlTab = 'mission' | 'activity' | 'history' | 'context' | 'logs' | 'chat' | 'pipelines' | 'actions' | 'catalog' | 'settings' | 'help';

const tabs: { id: ControlTab; label: string }[] = [
  { id: 'mission', label: 'Mission Control' },
  { id: 'activity', label: 'Activity' },
  { id: 'logs', label: 'Logs' },
  { id: 'chat', label: 'Chat' },
  { id: 'pipelines', label: 'Pipelines' },
  { id: 'history', label: 'History' },
  { id: 'context', label: 'Context' },
  { id: 'actions', label: 'Actions' },
  { id: 'catalog', label: 'Catalog' },
  { id: 'settings', label: 'Settings' },
  { id: 'help', label: 'Help' },
];

function record(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
}

function list(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (value && typeof value === 'object') return Object.values(value as UnknownRecord);
  return [];
}

function labelFor(value: unknown, fallback = 'Untitled'): string {
  const item = record(value);
  return text(item.display_name, text(item.title, text(item.name, text(item.id, fallback))));
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className={styles.field}><span>{label}</span>{children}</label>;
}

function formatTime(value: unknown): string {
  const numeric = Number(value ?? 0);
  if (!numeric) return '—';
  const date = new Date(numeric < 1_000_000_000_000 ? numeric * 1_000 : numeric);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

function HistoryPanel({ group, responses, send }: {
  group: string;
  responses: Record<string, unknown>;
  send: (command: TorqueCommand) => void;
}) {
  const [status, setStatus] = useState('merged');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState('');
  useEffect(() => { send({ cmd: 'get_agent_history', status, limit: 100 }); }, [send, status]);
  const listFrame = record(responses['agent_history_list:_']);
  const history = list(listFrame.records).map(record).filter((item) => !group || text(item.group) === group).filter((item) => {
    const query = search.trim().toLocaleLowerCase();
    if (!query) return true;
    return [item.name, item.id, item.kind, item.agent_type, item.provider]
      .some((value) => text(value).toLocaleLowerCase().includes(query));
  });
  const detail = selectedId ? record(responses[`agent_history_detail:${selectedId}`]) : {};
  const detailRecord = record(detail.record);
  const detailTasks = list(detail.tasks).map(record);
  const detailMessages = list(detail.messages).map(record);

  return <div className={styles.historyPanel}>
    <header className={styles.historyToolbar}>
      <Field label="Search history"><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Agent name, ID, kind, provider" /></Field>
      <div className={styles.historyFilters} role="group" aria-label="History status">{([['', 'All'], ['active', 'Active'], ['removed', 'Removed'], ['merged', 'Merged']] as const).map(([id, label]) => <button key={id} aria-pressed={status === id} onClick={() => setStatus(id)}>{label}</button>)}</div>
      <Button tone="quiet" onPress={() => send({ cmd: 'get_agent_history', status, limit: 100 })}>Refresh</Button>
    </header>
    <div className={styles.historySplit}>
      <section className={styles.historyList}>
        <header><h2>Agent runs</h2><span>{history.length}</span></header>
        {history.length ? history.map((item, index) => {
          const id = text(item.id, text(item.agent_id));
          return <button key={id || String(index)} aria-current={selectedId === id ? 'true' : undefined} onClick={() => { setSelectedId(id); send({ cmd: 'get_agent_history_detail', agent_id: id, message_limit: 100 }); }}>
            <span><strong>{text(item.name, id || 'Agent run')}</strong><small>{text(item.kind, text(item.agent_type, 'agent'))} · {text(item.provider, 'unknown provider')}</small></span>
            <span><b>{text(item.status, 'unknown')}</b><time>{formatTime(item.removed_at ?? item.completed_at ?? item.updated_at ?? item.created_at)}</time></span>
          </button>;
        }) : <StateSurface title="No historical runs" description="No agent runs match this group, status, and search." />}
      </section>
      <section className={styles.historyDetail}>{selectedId ? Object.keys(detailRecord).length ? <>
        <header><div><h2>{text(detailRecord.name, selectedId)}</h2><p>{text(detailRecord.id, selectedId)} · {text(detailRecord.status, 'unknown')}</p></div>{text(detailRecord.status) === 'active' ? <Button tone="quiet" onPress={() => send({ cmd: 'focus_agent', id: selectedId })}>Focus live agent</Button> : null}</header>
        <dl><div><dt>Started</dt><dd>{formatTime(detailRecord.started_at ?? detailRecord.created_at)}</dd></div><div><dt>Finished</dt><dd>{formatTime(detailRecord.removed_at ?? detailRecord.completed_at)}</dd></div><div><dt>Model</dt><dd>{text(detailRecord.model, '—')}</dd></div><div><dt>Tokens</dt><dd>{text(detailRecord.total_tokens ?? detailRecord.token_count, '—')}</dd></div><div><dt>Branch</dt><dd>{text(detailRecord.worktree_branch ?? detailRecord.branch, '—')}</dd></div><div><dt>Outcome</dt><dd>{text(detailRecord.outcome, '—')}</dd></div></dl>
        <div className={styles.historyDetailColumns}>
          <section><h3>Tasks <span>{detailTasks.length}</span></h3>{detailTasks.length ? detailTasks.map((task, index) => <article key={text(task.task_id, text(task.id, String(index)))}><strong>{text(task.task, text(task.title, text(task.task_id, 'Task')))}</strong><span>{text(task.lane, text(task.status))}</span><p>{text(task.result, text(task.summary))}</p></article>) : <p>No recorded tasks.</p>}</section>
          <section><h3>Messages <span>{detailMessages.length}</span></h3>{detailMessages.length ? detailMessages.map((message, index) => <article key={text(message.id, String(index))}><strong>{text(message.action, text(message.role, 'message'))}</strong><time>{formatTime(message.timestamp ?? message.created_at)}</time><p>{text(message.message, text(message.content, text(message.text)))}</p></article>) : <p>No recorded messages.</p>}</section>
        </div>
      </> : <StateSurface title="Loading run detail" description="Torque is loading persisted tasks, messages, and lifecycle metadata." /> : <StateSurface title="Select an agent run" description="Inspect its lifecycle, tasks, messages, provider, model, tokens, branch, and outcome." />}</section>
    </div>
  </div>;
}

function SettingsPanel({ group, responses, send }: { group: string; responses: Record<string, unknown>; send: (command: TorqueCommand) => void }) {
  const operations = useAppSelector(selectOperationsState);
  const groups = useAppSelector(selectGroupsState);
  const providers = useAppSelector(selectProviders);
  const currentGlobal = operations.globalSettings;
  const currentGroup = record(groups.settings[group]);
  const groupSettingsFrame = record(responses[`group_settings:${group}`] ?? responses['group_settings:latest']);
  const currentEngineer = record(groupSettingsFrame.engineer_settings);
  const currentArchitect = record(groupSettingsFrame.architect_settings);
  const currentAi = operations.aiSettings;
  const aiGeneration = record(currentAi.generation);
  const aiAnthropic = record(aiGeneration.anthropic);
  const aiOpenAi = record(aiGeneration.openai_compatible);
  const aiEmbeddings = record(currentAi.embeddings);
  const aiIndex = record(currentAi.index);
  const aiBoot = record(currentAi.boot_summary);
  const [globalDraft, setGlobalDraft] = useState(() => ({
    xterm_scrollback: Number(currentGlobal.xterm_scrollback ?? 5000),
    max_pipeline_depth: Number(currentGlobal.max_pipeline_depth ?? 10),
    max_event_log: Number(currentGlobal.max_event_log ?? 500),
    metrics_enabled: currentGlobal.metrics_enabled !== false,
    keybindings: record(currentGlobal.keybindings),
    status_bar_visibility: {
      daemon_status: false, claude_usage: false, codex_usage: false, deploy: true,
      health: false, workload: false, tasks: true, attention: true,
      ...record(currentGlobal.status_bar_visibility),
    },
  }));
  const [groupDraft, setGroupDraft] = useState(() => ({
    default_directory: text(currentGroup.default_directory),
    max_agents: Number(currentGroup.max_agents ?? 0),
    git_worktree: currentGroup.git_worktree === true,
    engineer_merge_mode: text(currentGroup.engineer_merge_mode, 'pr'),
  }));
  const [aiDraft, setAiDraft] = useState(() => ({
    ai_enabled: currentAi.enabled === true || currentGlobal.ai_enabled === true,
    ai_generation_provider: text(aiGeneration.provider, text(currentGlobal.ai_generation_provider, 'anthropic')),
    ai_anthropic_model: text(aiAnthropic.model, text(currentGlobal.ai_anthropic_model)),
    ai_openai_compatible_base_url: text(aiOpenAi.base_url, text(currentGlobal.ai_openai_compatible_base_url)),
    ai_openai_compatible_model: text(aiOpenAi.model, text(currentGlobal.ai_openai_compatible_model)),
    ai_embedding_model: text(aiEmbeddings.model_id, text(currentGlobal.ai_embedding_model)),
    ai_embedding_runtime: text(aiEmbeddings.runtime, text(currentGlobal.ai_embedding_runtime, 'sentence_transformers')),
    ai_index_corpus: record(aiIndex.corpus),
    ai_boot_summary_enabled: aiBoot.enabled !== false,
    ai_boot_summary_min_interval_seconds: Number(aiBoot.min_interval_seconds ?? currentGlobal.ai_boot_summary_min_interval_seconds ?? 300),
    ai_boot_summary_max_refreshes_per_hour: Number(aiBoot.max_refreshes_per_hour ?? currentGlobal.ai_boot_summary_max_refreshes_per_hour ?? 6),
  }));
  const [aiSecrets, setAiSecrets] = useState({ anthropic: '', openai_compatible: '' });
  const [clearAiSecrets, setClearAiSecrets] = useState<string[]>([]);
  const [relayDraft, setRelayDraft] = useState(() => ({ relay_enabled: currentGlobal.relay_enabled === true, relay_url: text(currentGlobal.relay_url), relay_daemon_id: text(currentGlobal.relay_daemon_id), relay_credential_id: text(currentGlobal.relay_credential_id), relay_private_key_path: text(currentGlobal.relay_private_key_path) }));
  const [relayTouched, setRelayTouched] = useState<string[]>([]);
  const changeRelay = (patch: Partial<typeof relayDraft>) => { setRelayDraft((previous) => ({ ...previous, ...patch })); setRelayTouched((keys) => [...new Set([...keys, ...Object.keys(patch)])]); setDirty(true); setSaved(false); };
  const [pairingToken, setPairingToken] = useState('');
  const [advancedGlobal, setAdvancedGlobal] = useState(() => JSON.stringify(currentGlobal, null, 2));
  const [advancedGroup, setAdvancedGroup] = useState(() => JSON.stringify(currentGroup, null, 2));
  const [advancedEngineer, setAdvancedEngineer] = useState(() => JSON.stringify(currentEngineer, null, 2));
  const [advancedArchitect, setAdvancedArchitect] = useState(() => JSON.stringify(currentArchitect, null, 2));
  const [jsonError, setJsonError] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const saveController = useRef<AbortController | null>(null);
  const settingsDispatch = useAppDispatch();
  useEffect(() => () => saveController.current?.abort(), []);

  const change = <T extends object>(setter: React.Dispatch<React.SetStateAction<T>>, patch: Partial<T>) => {
    setter((value) => ({ ...value, ...patch })); setDirty(true); setSaved(false);
  };
  const save = async (confirmEmbeddingRebuild = false) => {
    let fullGlobal: UnknownRecord; let fullGroup: UnknownRecord; let fullEngineer: UnknownRecord; let fullArchitect: UnknownRecord;
    try { fullGlobal = record(JSON.parse(advancedGlobal)); fullGroup = record(JSON.parse(advancedGroup)); fullEngineer = record(JSON.parse(advancedEngineer)); fullArchitect = record(JSON.parse(advancedArchitect)); } catch { setJsonError('Advanced settings must be valid JSON.'); return; }
    setSaving(true); setJsonError(''); setSaved(false);
    const controller = new AbortController(); saveController.current = controller;
    const commands: TorqueCommand[] = [
      { cmd: 'update_global_settings', settings: { ...Object.fromEntries(Object.entries(fullGlobal).filter(([key]) => !key.startsWith('relay_'))), ...globalDraft, ...Object.fromEntries(Object.entries(relayDraft).filter(([key]) => relayTouched.includes(key))) } },
      { cmd: 'update_group_settings', group, settings: { ...fullGroup, ...groupDraft } },
      { cmd: 'engineer_update_settings', group, ...fullEngineer },
      { cmd: 'update_architect_settings', group, settings: fullArchitect },
      { cmd: 'update_ai_settings', settings: aiDraft, secrets: Object.fromEntries(Object.entries(aiSecrets).filter(([, value]) => value.trim())), clear_secrets: clearAiSecrets, ...(confirmEmbeddingRebuild ? { confirm_embedding_rebuild: true } : {}) },
    ];
    try {
      for (const command of commands) {
        const response = await readCommand(command, controller.signal);
        if (controller.signal.aborted) return;
        if (response.type !== 'state') settingsDispatch(projectionActions.auxiliaryResourceReceived(response));
        if (response.type === 'ai_settings_requires_confirmation') throw new Error('AI settings require embedding rebuild confirmation. Other settings were saved.');
      }
      setDirty(false); setSaved(true); setAiSecrets({ anthropic: '', openai_compatible: '' }); setClearAiSecrets([]);
    } catch (cause) {
      if (!controller.signal.aborted) setJsonError(`Some settings may already be saved. ${cause instanceof Error ? cause.message : 'Save failed'}. Your draft is retained; retry when ready.`);
    } finally { if (!controller.signal.aborted) setSaving(false); }

  };
  const relayTest = record(responses['relay_test_result:_'] ?? responses['relay_test_result:latest']);
  const deviceLink = record(responses['relay_device_link:_'] ?? responses['relay_device_link:latest']);
  const daemonCredential = record(responses['daemon_credential:_'] ?? responses['daemon_credential:latest']);
  const aiConfirmation = record(responses['ai_settings_requires_confirmation:_'] ?? responses['ai_settings_requires_confirmation:latest']);
  const promptPreview = record(responses[`system_prompt_preview:${group}`] ?? responses['system_prompt_preview:latest']);

  return <form className={styles.settings} onSubmit={(event) => { event.preventDefault(); void save(); }}>
    <header><div><h2>Workspace settings</h2><p>Global, group, and AI changes save as one coordinated operation.</p></div><span>{saving ? 'Saving…' : dirty ? 'Unsaved changes' : saved ? 'Saved' : 'Up to date'}</span><Button tone="primary" type="submit" isDisabled={!dirty || saving}>Save changes</Button></header>
    <fieldset disabled={saving} className={styles.settingsFields}><section><h3>Global runtime</h3><div className={styles.formGrid}>
      <Field label="Terminal scrollback"><input type="number" min="100" max="100000" value={globalDraft.xterm_scrollback} onChange={(event) => change(setGlobalDraft, { xterm_scrollback: Number(event.target.value) })} /></Field>
      <Field label="Pipeline depth"><input type="number" min="0" value={globalDraft.max_pipeline_depth} onChange={(event) => change(setGlobalDraft, { max_pipeline_depth: Number(event.target.value) })} /></Field>
      <Field label="Event retention"><input type="number" value={globalDraft.max_event_log} onChange={(event) => change(setGlobalDraft, { max_event_log: Number(event.target.value) })} /></Field>
      <Field label="Metrics"><select value={globalDraft.metrics_enabled ? 'on' : 'off'} onChange={(event) => change(setGlobalDraft, { metrics_enabled: event.target.value === 'on' })}><option value="on">Enabled</option><option value="off">Disabled</option></select></Field>
    </div></section>
    <AppearancePreferencesPanel />
    <ShortcutPreferencesPanel settings={globalDraft} onChange={(keybindings) => change(setGlobalDraft, { keybindings })} />
    <section><h3>Status bar</h3><div className={styles.statusVisibility}>{Object.entries(globalDraft.status_bar_visibility).map(([key, enabled]) => <label key={key}><input type="checkbox" checked={enabled === true} onChange={(event) => change(setGlobalDraft, { status_bar_visibility: { ...globalDraft.status_bar_visibility, [key]: event.target.checked } })} />{key.replaceAll('_', ' ')}</label>)}</div><p className={styles.note}>Choose which live daemon, usage, deployment, health, workload, task, and attention signals remain visible across workspaces.</p></section>
    <section><h3>{group} defaults</h3><div className={styles.formGrid}>
      <Field label="Default directory"><input value={groupDraft.default_directory} onChange={(event) => change(setGroupDraft, { default_directory: event.target.value })} /></Field>
      <Field label="Maximum agents"><input type="number" value={groupDraft.max_agents} onChange={(event) => change(setGroupDraft, { max_agents: Number(event.target.value) })} /></Field>
      <Field label="Worktrees"><select value={groupDraft.git_worktree ? 'on' : 'off'} onChange={(event) => change(setGroupDraft, { git_worktree: event.target.value === 'on' })}><option value="on">Enabled</option><option value="off">Disabled</option></select></Field>
      <Field label="Merge mode"><select value={groupDraft.engineer_merge_mode} onChange={(event) => change(setGroupDraft, { engineer_merge_mode: event.target.value })}><option value="pr">Pull request</option><option value="direct">Direct</option><option value="engineer-choice">Engineer choice</option></select></Field>
    </div></section>
    <section><h3>AI subsystem</h3><div className={styles.formGrid}>
      <Field label="AI"><select value={aiDraft.ai_enabled ? 'on' : 'off'} onChange={(event) => change(setAiDraft, { ai_enabled: event.target.value === 'on' })}><option value="off">Disabled</option><option value="on">Enabled</option></select></Field>
      <Field label="Generation provider"><select value={aiDraft.ai_generation_provider} onChange={(event) => change(setAiDraft, { ai_generation_provider: event.target.value })}><option value="anthropic">Anthropic</option><option value="openai_compatible">OpenAI compatible</option></select></Field>
      <Field label="Anthropic model"><input value={aiDraft.ai_anthropic_model} onChange={(event) => change(setAiDraft, { ai_anthropic_model: event.target.value })} /></Field>
      <Field label={`Anthropic key${record(aiAnthropic.key).configured === true ? ` · configured …${text(record(aiAnthropic.key).last4)}` : ''}`}><input type="password" value={aiSecrets.anthropic} onChange={(event) => { setAiSecrets({ ...aiSecrets, anthropic: event.target.value }); setDirty(true); }} placeholder="Leave blank to keep current" autoComplete="off" /></Field>
      <Field label="OpenAI-compatible URL"><input value={aiDraft.ai_openai_compatible_base_url} onChange={(event) => change(setAiDraft, { ai_openai_compatible_base_url: event.target.value })} /></Field>
      <Field label="OpenAI-compatible model"><input value={aiDraft.ai_openai_compatible_model} onChange={(event) => change(setAiDraft, { ai_openai_compatible_model: event.target.value })} /></Field>
      <Field label={`OpenAI-compatible key${record(aiOpenAi.key).configured === true ? ` · configured …${text(record(aiOpenAi.key).last4)}` : ''}`}><input type="password" value={aiSecrets.openai_compatible} onChange={(event) => { setAiSecrets({ ...aiSecrets, openai_compatible: event.target.value }); setDirty(true); }} placeholder="Leave blank to keep current" autoComplete="off" /></Field>
      <Field label="Embedding model"><input value={aiDraft.ai_embedding_model} onChange={(event) => change(setAiDraft, { ai_embedding_model: event.target.value })} /></Field>
      <Field label="Embedding runtime"><select value={aiDraft.ai_embedding_runtime} onChange={(event) => change(setAiDraft, { ai_embedding_runtime: event.target.value })}><option value="sentence_transformers">Sentence Transformers</option><option value="fastembed">FastEmbed</option></select></Field>
      <Field label="Boot summaries"><select value={aiDraft.ai_boot_summary_enabled ? 'on' : 'off'} onChange={(event) => change(setAiDraft, { ai_boot_summary_enabled: event.target.value === 'on' })}><option value="on">Enabled</option><option value="off">Disabled</option></select></Field>
      <Field label="Boot summary minimum interval"><input type="number" value={aiDraft.ai_boot_summary_min_interval_seconds} onChange={(event) => change(setAiDraft, { ai_boot_summary_min_interval_seconds: Number(event.target.value) })} /></Field>
      <Field label="Boot summary hourly limit"><input type="number" value={aiDraft.ai_boot_summary_max_refreshes_per_hour} onChange={(event) => change(setAiDraft, { ai_boot_summary_max_refreshes_per_hour: Number(event.target.value) })} /></Field>
    </div><div className={styles.aiCorpus}><strong>Index corpus</strong>{Object.entries(aiDraft.ai_index_corpus).map(([key, enabled]) => <label key={key}><input type="checkbox" checked={enabled === true} onChange={(event) => change(setAiDraft, { ai_index_corpus: { ...aiDraft.ai_index_corpus, [key]: event.target.checked } })} />{key.replaceAll('_', ' ')}</label>)}</div>{Object.keys(aiConfirmation).length ? <div className={styles.secretResult}><strong>Embedding index rebuild required</strong><p>{text(aiConfirmation.message)}</p><Button tone="primary" onPress={() => { void save(true); }}>Confirm settings and rebuild</Button></div> : null}<div className={styles.settingsActions}>{record(aiAnthropic.key).configured === true ? <Button tone="danger" onPress={() => { setClearAiSecrets((value) => value.includes('anthropic') ? value.filter((item) => item !== 'anthropic') : [...value, 'anthropic']); setDirty(true); }}>{clearAiSecrets.includes('anthropic') ? 'Keep Anthropic key' : 'Clear Anthropic key'}</Button> : null}{record(aiOpenAi.key).configured === true ? <Button tone="danger" onPress={() => { setClearAiSecrets((value) => value.includes('openai_compatible') ? value.filter((item) => item !== 'openai_compatible') : [...value, 'openai_compatible']); setDirty(true); }}>{clearAiSecrets.includes('openai_compatible') ? 'Keep OpenAI key' : 'Clear OpenAI key'}</Button> : null}<Button tone="quiet" onPress={() => send({ cmd: 'ai_index_start', mode: Number(record(aiIndex.counts).indexed ?? 0) > 0 ? 'rebuild' : 'incremental', confirm: true })}>Build / rebuild index</Button></div><p className={styles.note}>Raw provider keys are write-only and never returned in snapshots or logs. Index: {text(aiIndex.status, 'disabled')} · {text(record(aiIndex.counts).indexed, '0')} indexed.</p></section>
    <section><h3>Relay connector</h3><div className={styles.formGrid}><Field label="Relay"><select value={relayDraft.relay_enabled ? 'on' : 'off'} onChange={(event) => changeRelay({ relay_enabled: event.target.value === 'on' })}><option value="off">Disabled</option><option value="on">Enabled</option></select></Field><Field label="Relay URL"><input value={relayDraft.relay_url} onChange={(event) => changeRelay({ relay_url: event.target.value })} /></Field><Field label="Daemon ID"><input value={relayDraft.relay_daemon_id} onChange={(event) => changeRelay({ relay_daemon_id: event.target.value })} /></Field><Field label="Credential ID"><input value={relayDraft.relay_credential_id} onChange={(event) => changeRelay({ relay_credential_id: event.target.value })} /></Field><Field label="Private key path"><input value={relayDraft.relay_private_key_path} onChange={(event) => changeRelay({ relay_private_key_path: event.target.value })} /></Field><Field label="One-time pairing token"><input type="password" value={pairingToken} onChange={(event) => setPairingToken(event.target.value)} autoComplete="off" /></Field></div><div className={styles.settingsActions}><Button tone="quiet" onPress={() => send({ cmd: 'test_relay_connection' })}>Test connection</Button><Button tone="quiet" isDisabled={!pairingToken.trim()} onPress={() => { send({ cmd: 'generate_daemon_credential', pairing_token: pairingToken }); setPairingToken(''); }}>Pair daemon credential</Button><Button tone="primary" isDisabled={!relayDraft.relay_enabled} onPress={() => send({ cmd: 'generate_relay_device_link', confirm: true })}>Generate one-time device link</Button></div>{Object.keys(relayTest).length ? <p className={styles.note}>{text(relayTest.status)} · {text(relayTest.message)}</p> : null}{Object.keys(daemonCredential).length ? <p className={styles.note}>{text(daemonCredential.message, text(daemonCredential.error))}</p> : null}{deviceLink.ok === true ? <div className={styles.secretResult}><strong>Display once</strong><p>{text(deviceLink.establish_url, text(deviceLink.url))}</p><code>{text(deviceLink.code)}</code><small>Expires {text(deviceLink.expires_at, 'soon')}</small></div> : null}</section>
    <section><h3>Runtime and behavior settings</h3><p className={styles.note}>Fields retain their daemon defaults and inheritance. Group-wide defaults apply to future launches; per-agent overrides remain in Agents.</p><details><summary>Global defaults</summary><StructuredSettings providers={providers} value={record(JSON.parse(advancedGlobal))} omit={[...Object.keys(globalDraft), ...Object.keys(relayDraft), ...Object.keys(aiDraft), 'default_lanes']} onChange={(next) => { setAdvancedGlobal(JSON.stringify(next)); setDirty(true); setSaved(false); }} /></details><details><summary>{group} execution, worktrees, notifications and sync</summary><StructuredSettings providers={providers} value={record(JSON.parse(advancedGroup))} omit={Object.keys(groupDraft)} onChange={(next) => { setAdvancedGroup(JSON.stringify(next)); setDirty(true); setSaved(false); }} /></details><details><summary>Engineer behavior defaults</summary><StructuredSettings providers={providers} value={record(JSON.parse(advancedEngineer))} onChange={(next) => { setAdvancedEngineer(JSON.stringify(next)); setDirty(true); setSaved(false); }} /></details><details><summary>Architect behavior defaults</summary><StructuredSettings providers={providers} value={record(JSON.parse(advancedArchitect))} onChange={(next) => { setAdvancedArchitect(JSON.stringify(next)); setDirty(true); setSaved(false); }} /></details><div className={styles.settingsActions}><Button tone="quiet" onPress={() => { try { send({ cmd: 'preview_system_prompt', request_id: `react-engineer-${Date.now()}`, group, kind: 'engineer', group_settings: { ...record(JSON.parse(advancedGroup)), ...groupDraft }, settings: record(JSON.parse(advancedEngineer)) }); setJsonError(''); } catch { setJsonError('Advanced settings must be valid JSON.'); } }}>Preview Engineer system prompt</Button><Button tone="quiet" onPress={() => { try { send({ cmd: 'preview_system_prompt', request_id: `react-architect-${Date.now()}`, group, kind: 'architect', group_settings: { ...record(JSON.parse(advancedGroup)), ...groupDraft }, settings: record(JSON.parse(advancedArchitect)) }); setJsonError(''); } catch { setJsonError('Advanced settings must be valid JSON.'); } }}>Preview Architect system prompt</Button></div>{text(promptPreview.prompt) ? <details className={styles.promptPreview}><summary>{text(promptPreview.kind)} system prompt · {text(record(promptPreview.metadata).provider, 'inherited provider')}</summary><pre>{text(promptPreview.prompt)}</pre></details> : null}{jsonError ? <p className={styles.validation}>{jsonError}</p> : null}</section>
  </fieldset></form>;
}

function SettingsWorkspace({ group, responses, send }: { group: string; responses: Record<string, unknown>; send: (command: TorqueCommand) => void }) {
  const dispatch = useAppDispatch();
  const [ready, setReady] = useState(false); const [error, setError] = useState(''); const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([{ cmd: 'get_global_settings' }, { cmd: 'get_group_settings', group }, { cmd: 'get_ai_settings' }].map((command) => readCommand(command, controller.signal))).then((frames) => {
      if (controller.signal.aborted) return;
      frames.forEach((frame) => dispatch(projectionActions.auxiliaryResourceReceived(frame))); setReady(true); setError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Settings unavailable'); });
    return () => controller.abort();
  }, [group, dispatch, retry]);
  return ready ? <SettingsPanel group={group} responses={responses} send={send} /> : error ? <StateSurface title="Settings unavailable" description={error} action={<Button onPress={() => setRetry((value) => value + 1)}>Retry settings</Button>} /> : <StateSurface title="Loading settings" description="Loading global, group and AI defaults before editing." />;
}

export function ControlCenter({ group, sendCommand, onCommandUnavailable, host = browserHost }: {
  group: string;
  host?: DesktopHost;
  sendCommand: CommandSender;
  onCommandUnavailable: () => void;
}) {
  const connection = useAppSelector(selectConnection);
  const dispatch = useAppDispatch();
  const workspaceUi = useAppSelector(selectWorkspaceUi);
  const operations = useAppSelector(selectOperationsState);
  const auxiliaryResponses = useAppSelector(selectAuxiliaryResponseState);
  const groupState = useAppSelector(selectGroupsState);
  const catalog = useAppSelector(selectCatalogState);
  const agents = useAppSelector(selectAgentsState);
  const messages = useAppSelector(selectMessagesState);
  const tab = workspaceUi.controlTab;
  const setTab = (next: ControlTab) => dispatch(workspaceUiActions.setControlTab(next));
  const [selectedAction, setSelectedAction] = useState('');
  const [actionDraft, setActionDraft] = useState({ name: '', description: '', scope: 'project', agent: '', group: '', prompt: '', labels: '', transitions: '', terminals: '', worktree: false, auto_close_on_done: false, disable_role_preamble: false, implementation_depth: false, review_required_above_loc: '' });
  const [actionDirty, setActionDirty] = useState(false);
  const [actionError, setActionError] = useState('');
  const requestKey = useRef('');
  const lastFrame = connection.lastAuxiliaryFrame;
  const preview = record(auxiliaryResponses[`action_rendered:${selectedAction || actionDraft.name}`]);
  const baseDir = text(record(groupState.settings[group]).default_directory);

  const send = useCallback((command: TorqueCommand) => { if (!sendCommand(command)) onCommandUnavailable(); }, [onCommandUnavailable, sendCommand]);
  const refresh = useCallback(() => {
    const requests: Partial<Record<ControlTab, TorqueCommand[]>> = {
      mission: [{ cmd: 'get_mission_control', group }],
      activity: [{ cmd: 'get_events', limit: 100 }],
      actions: [{ cmd: 'list_actions', group }],
      catalog: [{ cmd: 'list_roles', group }, { cmd: 'list_templates', group }, { cmd: 'list_specializations', group }, { cmd: 'agent_class_list', ...(baseDir ? { base_dir: baseDir } : {}) }],
      help: [{ cmd: 'help_list', audience: 'user' }],
    };
    (requests[tab] || []).forEach(send);
  }, [baseDir, group, send, tab]);

  useEffect(() => {
    if (!group || connection.status !== 'connected') return;
    const key = `${group}:${tab}:${connection.reconnectCount}`;
    if (requestKey.current === key) return;
    requestKey.current = key;
    refresh();
  }, [group, tab, connection.status, connection.reconnectCount, refresh]);

  const actionItems = useMemo(() => {
    const seen = new Set<string>();
    return list(catalog.actions).filter((item) => {
      const name = labelFor(item, '');
      if (!name || seen.has(name)) return false;
      seen.add(name);
      return true;
    });
  }, [catalog.actions]);
  const agentItems = useMemo(() => records(agents.records).filter((item) => !group || item.group === group), [agents.records, group]);
  const agentCount = agentItems.filter((item) => item.cell_type !== 'terminal').length;
  const terminalCount = agentItems.filter((item) => item.cell_type === 'terminal').length;
  const eventItems = useMemo(() => [...operations.events].reverse().map(record), [operations.events]);
  const actionFrame = record(auxiliaryResponses[`action_detail:${selectedAction}`]);
  const loadedAction = text(actionFrame.name) === selectedAction ? record(actionFrame.action) : null;
  const editorDraft = !actionDirty && loadedAction ? {
    name: text(actionFrame.name),
    description: text(loadedAction.description), scope: text(loadedAction.scope, 'project'),
    agent: typeof loadedAction.agent === 'string' ? loadedAction.agent : JSON.stringify(loadedAction.agent ?? ''), group: text(loadedAction.group),
    prompt: text(loadedAction.prompt),
    labels: Array.isArray(loadedAction.labels) ? loadedAction.labels.join(', ') : text(loadedAction.labels),
    transitions: JSON.stringify(loadedAction.transitions ?? [], null, 2),
    terminals: JSON.stringify(loadedAction.terminals ?? [], null, 2),
    worktree: loadedAction.worktree === true, auto_close_on_done: loadedAction.auto_close_on_done === true,
    disable_role_preamble: loadedAction.disable_role_preamble === true, implementation_depth: loadedAction.implementation_depth === true,
    review_required_above_loc: text(loadedAction.review_required_above_loc),
  } : actionDraft;

  const chooseAction = (item: unknown) => {
    const name = typeof item === 'string' ? item : labelFor(item, '');
    setSelectedAction(name); setActionDirty(false); setActionError(''); send({ cmd: 'get_action', group, name });
  };
  const saveAction = () => {
    let transitions: unknown; let terminals: unknown; let agent: unknown = actionDraft.agent;
    try {
      transitions = JSON.parse(actionDraft.transitions || '[]');
      terminals = JSON.parse(actionDraft.terminals || '[]');
      if (!Array.isArray(transitions) || !Array.isArray(terminals)) throw new Error('Expected arrays');
      if (actionDraft.agent.trim().startsWith('{')) agent = JSON.parse(actionDraft.agent);
    } catch {
      setActionError('Transitions and companion terminals must be valid JSON arrays; inline agent JSON must be a valid object.');
      return;
    }
    send({ cmd: 'save_action', group, name: actionDraft.name, old_name: selectedAction, scope: actionDraft.scope, action: { description: actionDraft.description, agent, group: actionDraft.group, prompt: actionDraft.prompt, labels: actionDraft.labels.split(',').map((value) => value.trim()).filter(Boolean), transitions, terminals, worktree: actionDraft.worktree, auto_close_on_done: actionDraft.auto_close_on_done, disable_role_preamble: actionDraft.disable_role_preamble, implementation_depth: actionDraft.implementation_depth, review_required_above_loc: actionDraft.review_required_above_loc ? Number(actionDraft.review_required_above_loc) : undefined } });
    setSelectedAction(actionDraft.name); setActionDirty(false); setActionError('');
  };

  return <section className={styles.root} aria-label="Control Center">
    <header className={styles.header}><div><p>Workspace / {group || 'No group'}</p><h1>Control Center</h1></div><span>{agentCount} {agentCount === 1 ? 'agent' : 'agents'}{terminalCount ? ` · ${terminalCount} ${terminalCount === 1 ? 'terminal' : 'terminals'}` : ''} · {eventItems.length} events</span>{['mission', 'activity', 'actions', 'catalog', 'help'].includes(tab) ? <Button tone="quiet" onPress={refresh}>Refresh section</Button> : null}</header>
    <nav className={styles.tabs} aria-label="Control Center sections">{tabs.map((item) => <button key={item.id} aria-current={tab === item.id ? 'page' : undefined} onClick={() => setTab(item.id)}>{item.label}</button>)}</nav>
    {lastFrame?.type === 'error' ? <div className={styles.error} role="alert">{text(lastFrame.message, 'Request failed.')}</div> : null}
    <div className={styles.content}>
      {tab === 'mission' ? <MissionPanel group={group} agentCount={agentCount} mission={operations.missionControl} health={operations.health} supervisor={operations.supervisor} relay={operations.relayConnection} responses={auxiliaryResponses} send={send} onOpenTask={(id) => { dispatch(workspaceUiActions.setActivePanel('board')); dispatch(workspaceUiActions.setDetailTask(id)); }} onOpenAgent={(id) => { dispatch(workspaceUiActions.setActivePanel('agents')); dispatch(workspaceUiActions.setSelectedAgent(id)); }} /> : null}
      {tab === 'chat' ? <PeerChat threads={messages.peerThreads} agents={agents.records} /> : null}
      {tab === 'pipelines' ? <PipelineExplorer group={group} onEdit={(name) => { chooseAction(name); setTab('actions'); }} /> : null}
      {tab === 'logs' ? <LogViewer host={host} /> : null}
      {tab === 'activity' ? <ActivityPanel events={eventItems} send={send} group={group} /> : null}
      {tab === 'history' ? <HistoryPanel group={group} responses={auxiliaryResponses} send={send} /> : null}
      {tab === 'context' ? <ContextPanel group={group} agents={agentItems} responses={auxiliaryResponses} send={send} /> : null}
      {tab === 'actions' ? <div className={styles.editor}>
        <aside><header><h2>Actions</h2><Button tone="quiet" onPress={() => { setSelectedAction(''); setActionDraft({ name: '', description: '', scope: 'project', agent: '', group: '', prompt: '{{ TASK }}', labels: '', transitions: '[]', terminals: '[]', worktree: false, auto_close_on_done: false, disable_role_preamble: false, implementation_depth: false, review_required_above_loc: '' }); setActionDirty(true); setActionError(''); }}>＋</Button></header>{actionItems.length ? actionItems.map((item, index) => <button key={labelFor(item, String(index))} aria-current={selectedAction === labelFor(item, '') ? 'page' : undefined} onClick={() => chooseAction(item)}>{labelFor(item)}</button>) : <StateSurface title="No actions" description="Create the first project action." />}<hr /><Button tone="quiet" onPress={() => setTab('pipelines')}>Discover pipelines</Button></aside>
        <form onSubmit={(event) => { event.preventDefault(); saveAction(); }}><header><div><h2>Pipeline editor</h2><p>Complete action, dispatch, and transition contract.</p></div><span>{actionDirty ? 'Unsaved' : 'Saved'}</span>{selectedAction ? <Button tone="danger" onPress={() => { send({ cmd: 'delete_action', group, name: selectedAction }); setSelectedAction(''); }}>Delete</Button> : null}<Button tone="quiet" onPress={() => send({ cmd: 'render_action', group, name: selectedAction || editorDraft.name, vars: { TASK: 'Preview task' } })}>Preview</Button><Button tone="primary" type="submit" isDisabled={!actionDirty || !editorDraft.name || !editorDraft.prompt.includes('{{ TASK }}')}>Save action</Button></header>
          <Field label="Name"><input value={editorDraft.name} onChange={(event) => { setActionDraft({ ...editorDraft, name: event.target.value }); setActionDirty(true); }} /></Field>
          <div className={styles.formGrid}><Field label="Description"><input value={editorDraft.description} onChange={(event) => { setActionDraft({ ...editorDraft, description: event.target.value }); setActionDirty(true); }} /></Field><Field label="Scope"><select value={editorDraft.scope} onChange={(event) => { setActionDraft({ ...editorDraft, scope: event.target.value }); setActionDirty(true); }}><option value="project">Project</option><option value="user">User</option></select></Field><Field label="Agent role/template or inline JSON"><input value={editorDraft.agent} onChange={(event) => { setActionDraft({ ...editorDraft, agent: event.target.value }); setActionDirty(true); }} /></Field><Field label="Target group"><input value={editorDraft.group} onChange={(event) => { setActionDraft({ ...editorDraft, group: event.target.value }); setActionDirty(true); }} /></Field><Field label="Labels"><input value={editorDraft.labels} onChange={(event) => { setActionDraft({ ...editorDraft, labels: event.target.value }); setActionDirty(true); }} placeholder="bug, review" /></Field><Field label="Review above LOC"><input type="number" min="0" value={editorDraft.review_required_above_loc} onChange={(event) => { setActionDraft({ ...editorDraft, review_required_above_loc: event.target.value }); setActionDirty(true); }} /></Field></div>
          <div className={styles.checkRow}>{([['worktree', 'Create worktree'], ['auto_close_on_done', 'Auto-close on done'], ['disable_role_preamble', 'Disable role preamble'], ['implementation_depth', 'Implementation-depth gate']] as const).map(([key, label]) => <label key={key}><input type="checkbox" checked={editorDraft[key]} onChange={(event) => { setActionDraft({ ...editorDraft, [key]: event.target.checked }); setActionDirty(true); }} />{label}</label>)}</div>
          <Field label="Prompt"><textarea value={editorDraft.prompt} onChange={(event) => { setActionDraft({ ...editorDraft, prompt: event.target.value }); setActionDirty(true); }} /></Field>
          <Field label="Transitions (JSON)"><textarea className={styles.shortArea} value={editorDraft.transitions} onChange={(event) => { setActionDraft({ ...editorDraft, transitions: event.target.value }); setActionDirty(true); }} /></Field>
          <Field label="Companion terminals (JSON)"><textarea className={styles.shortArea} value={editorDraft.terminals} onChange={(event) => { setActionDraft({ ...editorDraft, terminals: event.target.value }); setActionDirty(true); }} /></Field>
          {actionError ? <p className={styles.validation} role="alert">{actionError}</p> : null}
          {preview.type === 'action_rendered' ? <pre className={styles.json}>{text(preview.prompt)}</pre> : null}
          {!editorDraft.prompt.includes('{{ TASK }}') ? <p className={styles.validation}>Prompt must include {'{{ TASK }}'}.</p> : null}
        </form>
      </div> : null}
      {tab === 'catalog' ? <div className={styles.catalog}>
        <AgentClassLibrary classes={catalog.agentClasses} contract={catalog.agentClassAuthoringContract} capabilityCatalog={catalog.agentClassCapabilityCatalog} responses={auxiliaryResponses} baseDir={baseDir} send={send} />
        <CatalogEditor title="Roles" kind="role" items={catalog.roles} group={group} send={send} />
        <CatalogEditor title="Templates" kind="template" items={catalog.templates} group={group} send={send} />
        <CatalogEditor title="Specializations" kind="specialization" items={catalog.specializations} group={group} send={send} />
        <BehaviorOverlayEditor group={group} active={catalog.behaviorOverlays} proposals={operations.behaviorOverlayProposals} responses={auxiliaryResponses} agents={agentItems} send={send} />
      </div> : null}
      {tab === 'settings' ? <SettingsWorkspace key={group} group={group} responses={auxiliaryResponses} send={send} /> : null}
      {tab === 'help' ? <HelpPanel responses={auxiliaryResponses} send={send} /> : null}
    </div>
  </section>;
}
