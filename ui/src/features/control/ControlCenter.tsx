import { useSettingsNavigation, useSettingsProtection } from '../../app/settingsNavigation';
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
import { NumericSettingInput } from './NumericSettingInput';
import { EngineerNotificationPreset } from './EngineerNotificationPreset';
import { StructuredSettings } from './StructuredSettings';
import { SettingsSearch } from './SettingsSearch';
import { RelayConfigurationFields } from './RelayConfigurationFields';
import { relayConfigurationView } from './relayConfiguration';
import { BoardSyncSettings } from './BoardSyncSettings';
import { aiSettingsDraft, primaryGlobalSettings, primaryGroupSettings, reconcileSettings, changedSettings, editableSettings, resetSettings, type SettingsSnapshot } from './settingsModel';
import { PeerChat } from './PeerChat';
import { PipelineExplorer } from './PipelineExplorer';
import { ActionsWorkspace } from './ActionsWorkspace';
import { LogViewer } from './LogViewer';
import { HistoryPanel } from './HistoryPanel';
import { ContextPanel } from './ContextPanel';
import { ActivityPanel, MissionPanel } from './OperatorPanels';
import { HelpPanel } from './HelpPanel';
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className={styles.field}><span>{label}</span>{children}</label>;
}

function SettingsPanel({ group, responses, send, snapshot, onSavingChange, onDiscard }: { onDiscard: () => void; group: string; responses: Record<string, unknown>; send: (command: TorqueCommand) => void; snapshot: SettingsSnapshot; onSavingChange: (busy: boolean) => void }) {
  const operations = useAppSelector(selectOperationsState);
  const discoveredProviders = useAppSelector(selectProviders);
  const providers = Array.isArray(snapshot.group.providers) ? snapshot.group.providers : discoveredProviders;
  const currentGlobal = record(snapshot.global.settings);
  const globalDefaults = record(snapshot.global.defaults);
  const currentGroup = record(snapshot.group.settings);
  const currentEngineer = record(snapshot.group.engineer_settings);
  const currentArchitect = record(snapshot.group.architect_settings);
  const groupDefaults = record(snapshot.group.defaults);
  const engineerDefaults = record(snapshot.group.engineer_defaults);
  const architectDefaults = record(snapshot.group.architect_defaults);
  const currentAi = operations.aiSettings;
  const aiGeneration = record(currentAi.generation);
  const aiAnthropic = record(aiGeneration.anthropic);
  const aiOpenAi = record(aiGeneration.openai_compatible);
  const aiIndex = record(currentAi.index);
  const [globalDraft, setGlobalDraft] = useState(() => primaryGlobalSettings(currentGlobal));
  const [groupDraft, setGroupDraft] = useState(() => primaryGroupSettings(currentGroup));
  const [aiDraft, setAiDraft] = useState(() => aiSettingsDraft(record(snapshot.ai.settings), currentGlobal));
  const [aiSecrets, setAiSecrets] = useState({ anthropic: '', openai_compatible: '' });
  const [clearAiSecrets, setClearAiSecrets] = useState<string[]>([]);
  const resolvedRelay = operations.relayConfig.config || operations.relayConfig.sources ? operations.relayConfig : snapshot.global.relay_config;
  const relayView = relayConfigurationView(currentGlobal, resolvedRelay);
  const [relayDraft, setRelayDraft] = useState(() => relayView.values);
  const [relayTouched, setRelayTouched] = useState<string[]>([]);
  const [pairingToken, setPairingToken] = useState('');
  const [advancedGlobal, setAdvancedGlobal] = useState(() => JSON.stringify(editableSettings(currentGlobal), null, 2));
  const [advancedGroup, setAdvancedGroup] = useState(() => JSON.stringify(editableSettings(currentGroup, Object.keys(currentArchitect)), null, 2));
  const [advancedEngineer, setAdvancedEngineer] = useState(() => JSON.stringify(editableSettings(currentEngineer), null, 2));
  const [advancedArchitect, setAdvancedArchitect] = useState(() => JSON.stringify(editableSettings(currentArchitect), null, 2));
  const [jsonError, setJsonError] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const changeRelay = (patch: Partial<typeof relayDraft>) => { setRelayDraft((previous) => ({ ...previous, ...patch })); setRelayTouched((keys) => [...new Set([...keys, ...Object.keys(patch)])]); setDirty(true); setSaved(false); };
  const updateGithubDraft = useCallback((github: UnknownRecord) => { setAdvancedGroup((value) => JSON.stringify({ ...record(JSON.parse(value)), board_sync_github: github })); setDirty(true); setSaved(false); }, []);
  const [saving, setSaving] = useState(false);
  const [baseline, setBaseline] = useState(() => ({ global: { ...record(JSON.parse(advancedGlobal)), ...globalDraft }, group: { ...record(JSON.parse(advancedGroup)), ...groupDraft }, engineer: record(JSON.parse(advancedEngineer)), architect: record(JSON.parse(advancedArchitect)), ai: { ...aiDraft } }));
  const form = useRef<HTMLFormElement>(null);
  const saveController = useRef<AbortController | null>(null);
  const [resetScopes, setResetScopes] = useState(() => new Set<string>());
  const lastEditor = useRef<HTMLElement | null>(null);
  const restoreFocus = useCallback(() => { lastEditor.current?.focus(); }, []);
  const currentDrafts = { global: { ...record(JSON.parse(advancedGlobal)), ...globalDraft }, group: { ...record(JSON.parse(advancedGroup)), ...groupDraft }, engineer: record(JSON.parse(advancedEngineer)), architect: record(JSON.parse(advancedArchitect)), ai: aiDraft };
  const launchContext = { group: currentDrafts.group, runtime: record(snapshot.group.runtime) };
  const hasEdits = resetScopes.size > 0 || relayTouched.length > 0 || clearAiSecrets.length > 0 || Object.values(aiSecrets).some(Boolean) || (Object.keys(currentDrafts) as (keyof typeof currentDrafts)[]).some((scope) => Object.keys(changedSettings(baseline[scope], currentDrafts[scope])).length > 0);
  useSettingsProtection({ dirty: dirty && hasEdits, saving, group, discard: onDiscard, restoreFocus });
  const [appliedSnapshot, setAppliedSnapshot] = useState(snapshot);
  if (appliedSnapshot !== snapshot) {
    setAppliedSnapshot(snapshot);
    // A read that began before an in-flight write cannot replace its draft or
    // acknowledgement baseline. The workspace rereads after the write settles.
    if (!saving) {
      const global = { ...editableSettings(currentGlobal), ...primaryGlobalSettings(currentGlobal) };
      const groupValues = { ...editableSettings(currentGroup, Object.keys(currentArchitect)), ...primaryGroupSettings(currentGroup) };
      const engineer = editableSettings(currentEngineer); const architect = editableSettings(currentArchitect);
      const ai = aiSettingsDraft(record(snapshot.ai.settings), currentGlobal);
      if (!resetScopes.has('global')) {
        setGlobalDraft(reconcileSettings(baseline.global, globalDraft, primaryGlobalSettings(currentGlobal)));
        setAdvancedGlobal(JSON.stringify(reconcileSettings(baseline.global, record(JSON.parse(advancedGlobal)), global)));
      }
      if (!resetScopes.has('group')) {
        setGroupDraft(reconcileSettings(baseline.group, groupDraft, primaryGroupSettings(currentGroup)));
        setAdvancedGroup(JSON.stringify(reconcileSettings(baseline.group, record(JSON.parse(advancedGroup)), groupValues)));
      }
      if (!resetScopes.has('engineer')) setAdvancedEngineer(JSON.stringify(reconcileSettings(baseline.engineer, record(JSON.parse(advancedEngineer)), engineer)));
      if (!resetScopes.has('architect')) setAdvancedArchitect(JSON.stringify(reconcileSettings(baseline.architect, record(JSON.parse(advancedArchitect)), architect)));
      setAiDraft(reconcileSettings(baseline.ai, aiDraft, ai));
      setBaseline({ global, group: groupValues, engineer, architect, ai });
    }
  }

  const resetSection = (scope: 'global' | 'group' | 'engineer' | 'architect') => {
    setResetScopes((current) => new Set([...current, scope]));
    if (scope === 'global') {
      const defaults = editableSettings(globalDefaults, [...Object.keys(relayDraft), ...Object.keys(aiDraft)]);
      setAdvancedGlobal(JSON.stringify(resetSettings(record(JSON.parse(advancedGlobal)), defaults)));
      setGlobalDraft((value) => ({ ...value, ...Object.fromEntries(Object.keys(value).filter((key) => key in defaults).map((key) => [key, defaults[key]])) }));
    } else if (scope === 'group') {
      const defaults = editableSettings(groupDefaults, Object.keys(currentArchitect));
      setAdvancedGroup(JSON.stringify(resetSettings(record(JSON.parse(advancedGroup)), defaults)));
      setGroupDraft((value) => ({ ...value, ...Object.fromEntries(Object.keys(value).filter((key) => key in defaults).map((key) => [key, defaults[key]])) }));
    } else if (scope === 'engineer') setAdvancedEngineer(JSON.stringify(resetSettings(record(JSON.parse(advancedEngineer)), engineerDefaults)));
    else setAdvancedArchitect(JSON.stringify(resetSettings(record(JSON.parse(advancedArchitect)), architectDefaults)));
    setDirty(true); setSaved(false);
  };
  const settingsDispatch = useAppDispatch();
  useEffect(() => () => saveController.current?.abort(), []);

  const change = <T extends object>(setter: React.Dispatch<React.SetStateAction<T>>, patch: Partial<T>) => {
    setter((value) => ({ ...value, ...patch })); setDirty(true); setSaved(false);
  };
  const save = async (confirmEmbeddingRebuild = false) => {
    if (form.current && !form.current.checkValidity()) {
      const invalid = form.current.querySelector<HTMLInputElement>('input:invalid, select:invalid, textarea:invalid');
      let ancestor = invalid?.parentElement;
      while (ancestor) { if (ancestor instanceof HTMLDetailsElement) ancestor.open = true; ancestor = ancestor.parentElement; }
      setJsonError('Correct the highlighted setting before saving.');
      invalid?.focus(); invalid?.reportValidity(); return;
    }
    let fullGlobal: UnknownRecord; let fullGroup: UnknownRecord; let fullEngineer: UnknownRecord; let fullArchitect: UnknownRecord;
    try { fullGlobal = record(JSON.parse(advancedGlobal)); fullGroup = record(JSON.parse(advancedGroup)); fullEngineer = record(JSON.parse(advancedEngineer)); fullArchitect = record(JSON.parse(advancedArchitect)); } catch { setJsonError('Advanced settings must be valid JSON.'); return; }
    if (saveController.current) return;
    onSavingChange(true); setSaving(true); setJsonError(''); setSaved(false);
    const controller = new AbortController(); saveController.current = controller;
    const next = { global: { ...fullGlobal, ...globalDraft }, group: { ...fullGroup, ...groupDraft }, engineer: fullEngineer, architect: fullArchitect, ai: { ...aiDraft } };
    const delta = (scope: keyof typeof next) => resetScopes.has(scope) ? editableSettings(next[scope]) : changedSettings(baseline[scope], next[scope]);
    const globalChanges = editableSettings(delta('global'), [...Object.keys(aiDraft), ...Object.keys(relayDraft)]);
    Object.assign(globalChanges, Object.fromEntries(Object.entries(relayDraft).filter(([key]) => relayTouched.includes(key))));
    const groupChanges = editableSettings(delta('group'), Object.keys(currentArchitect));
    const engineerChanges = delta('engineer');
    const architectChanges = delta('architect');
    const aiChanges = changedSettings(baseline.ai, next.ai);
    const commands: { scope: keyof typeof next; command: TorqueCommand }[] = [];
    if (Object.keys(globalChanges).length) commands.push({ scope: 'global', command: { cmd: 'update_global_settings', settings: globalChanges } });
    if (Object.keys(groupChanges).length) commands.push({ scope: 'group', command: { cmd: 'update_group_settings', group, settings: groupChanges } });
    if (Object.keys(engineerChanges).length) commands.push({ scope: 'engineer', command: { cmd: 'engineer_update_settings', group, ...engineerChanges } });
    if (Object.keys(architectChanges).length) commands.push({ scope: 'architect', command: { cmd: 'update_architect_settings', group, settings: architectChanges } });
    if (Object.keys(aiChanges).length || Object.values(aiSecrets).some((value) => value.trim()) || clearAiSecrets.length) commands.push({ scope: 'ai', command: { cmd: 'update_ai_settings', settings: aiChanges, secrets: Object.fromEntries(Object.entries(aiSecrets).filter(([, value]) => value.trim())), clear_secrets: clearAiSecrets, ...(confirmEmbeddingRebuild ? { confirm_embedding_rebuild: true } : {}) } });
    try {
      for (const { scope, command } of commands) {
        const response = await readCommand(command, controller.signal);
        if (controller.signal.aborted) return;
        if (response.type === 'error') throw new Error(text(response.message, 'Settings save failed.'));
        if (response.type !== 'state') settingsDispatch(projectionActions.auxiliaryResourceReceived(response));
        if (response.type === 'ai_settings_requires_confirmation') throw new Error('AI settings require embedding rebuild confirmation.');
        setBaseline((current) => ({ ...current, [scope]: next[scope] })); setResetScopes((current) => { const pending = new Set(current); pending.delete(scope); return pending; });
        if (scope === 'global') setRelayTouched([]);
        if (scope === 'ai') { setAiSecrets({ anthropic: '', openai_compatible: '' }); setClearAiSecrets([]); }
      }
      setBaseline(next); setResetScopes(new Set()); setRelayTouched([]);
      setDirty(false); setSaved(true); setAiSecrets({ anthropic: '', openai_compatible: '' }); setClearAiSecrets([]);
    } catch (cause) {
      if (!controller.signal.aborted) setJsonError(`Some settings may already be saved. ${cause instanceof Error ? cause.message : 'Save failed'}. Your draft is retained; retry when ready.`);
    } finally { saveController.current = null; if (!controller.signal.aborted) { setSaving(false); onSavingChange(false); } }

  };
  const relayTest = record(responses['relay_test_result:_'] ?? responses['relay_test_result:latest']);
  const deviceLink = record(responses['relay_device_link:_'] ?? responses['relay_device_link:latest']);
  const daemonCredential = record(responses['daemon_credential:_'] ?? responses['daemon_credential:latest']);
  const aiConfirmation = record(responses['ai_settings_requires_confirmation:_'] ?? responses['ai_settings_requires_confirmation:latest']);
  const promptPreview = record(responses[`system_prompt_preview:${group}`] ?? responses['system_prompt_preview:latest']);

  return <form ref={form} onFocusCapture={(event) => { if (event.target instanceof HTMLElement && event.target.matches('input, select, textarea')) lastEditor.current = event.target; }} noValidate className={styles.settings} onSubmit={(event) => { event.preventDefault(); void save(); }}>
    <header><div><h2>Workspace settings</h2><p>Global, group, and AI changes save as one coordinated operation.</p></div><span>{saving ? 'Saving…' : dirty ? 'Unsaved changes' : saved ? 'Saved' : 'Up to date'}</span><Button tone="primary" type="submit" isDisabled={!dirty || saving}>Save changes</Button></header>
    <SettingsSearch form={form} />
    {jsonError ? <p role="alert" className={styles.validation}>{jsonError}</p> : null}
    <fieldset disabled={saving} className={styles.settingsFields}><section><h3>Global runtime</h3><Button tone="quiet" isDisabled={!Object.keys(globalDefaults).length} onPress={() => resetSection('global')}>Reset global defaults</Button><p className={styles.note}>Resets runtime, shortcuts and status bar in this draft. AI, credentials and appearance keep their own controls.</p><div className={styles.formGrid}>
      <Field label="Terminal scrollback"><NumericSettingInput fieldKey="xterm_scrollback" value={globalDraft.xterm_scrollback} onChange={(value) => change(setGlobalDraft, { xterm_scrollback: value })} /></Field>
      <Field label="Pipeline depth"><NumericSettingInput fieldKey="max_pipeline_depth" value={globalDraft.max_pipeline_depth} onChange={(value) => change(setGlobalDraft, { max_pipeline_depth: value })} /></Field>
      <Field label="Event retention"><NumericSettingInput fieldKey="max_event_log" value={globalDraft.max_event_log} onChange={(value) => change(setGlobalDraft, { max_event_log: value })} /></Field>
      <Field label="Metrics"><select value={globalDraft.metrics_enabled ? 'on' : 'off'} onChange={(event) => change(setGlobalDraft, { metrics_enabled: event.target.value === 'on' })}><option value="on">Enabled</option><option value="off">Disabled</option></select></Field>
    </div></section>
    <AppearancePreferencesPanel />
    <ShortcutPreferencesPanel settings={globalDraft} onChange={(keybindings) => change(setGlobalDraft, { keybindings })} />
    <section><h3>Status bar</h3><div className={styles.statusVisibility}>{Object.entries(globalDraft.status_bar_visibility).map(([key, enabled]) => <label key={key}><input type="checkbox" checked={enabled === true} onChange={(event) => change(setGlobalDraft, { status_bar_visibility: { ...globalDraft.status_bar_visibility, [key]: event.target.checked } })} />{key.replaceAll('_', ' ')}</label>)}</div><p className={styles.note}>Choose which live daemon, usage, deployment, health, workload, task, and attention signals remain visible across workspaces.</p></section>
    <section><h3>{group} defaults</h3><Button tone="quiet" isDisabled={!Object.keys(groupDefaults).length} onPress={() => resetSection('group')}>Reset group defaults</Button><p className={styles.note}>Restores launch, worktree, notification and sync defaults. Empty launch overrides inherit their configured fallback. Save changes to apply.</p><div className={styles.formGrid}>
      <Field label="Default directory"><input value={groupDraft.default_directory} onChange={(event) => change(setGroupDraft, { default_directory: event.target.value })} /></Field>
      <Field label="Maximum agents"><NumericSettingInput fieldKey="max_agents" value={groupDraft.max_agents} onChange={(value) => change(setGroupDraft, { max_agents: value })} /></Field>
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
      <Field label="Boot summary minimum interval"><NumericSettingInput fieldKey="ai_boot_summary_min_interval_seconds" value={aiDraft.ai_boot_summary_min_interval_seconds} onChange={(value) => change(setAiDraft, { ai_boot_summary_min_interval_seconds: value })} /></Field>
      <Field label="Boot summary hourly limit"><NumericSettingInput fieldKey="ai_boot_summary_max_refreshes_per_hour" value={aiDraft.ai_boot_summary_max_refreshes_per_hour} onChange={(value) => change(setAiDraft, { ai_boot_summary_max_refreshes_per_hour: value })} /></Field>
    </div><div className={styles.aiCorpus}><strong>Index corpus</strong>{Object.entries(aiDraft.ai_index_corpus).map(([key, enabled]) => <label key={key}><input type="checkbox" checked={enabled === true} onChange={(event) => change(setAiDraft, { ai_index_corpus: { ...aiDraft.ai_index_corpus, [key]: event.target.checked } })} />{key.replaceAll('_', ' ')}</label>)}</div>{Object.keys(aiConfirmation).length ? <div className={styles.secretResult}><strong>Embedding index rebuild required</strong><p>{text(aiConfirmation.message)}</p><Button tone="primary" onPress={() => { void save(true); }}>Confirm settings and rebuild</Button></div> : null}<div className={styles.settingsActions}>{record(aiAnthropic.key).configured === true ? <Button tone="danger" onPress={() => { setClearAiSecrets((value) => value.includes('anthropic') ? value.filter((item) => item !== 'anthropic') : [...value, 'anthropic']); setDirty(true); }}>{clearAiSecrets.includes('anthropic') ? 'Keep Anthropic key' : 'Clear Anthropic key'}</Button> : null}{record(aiOpenAi.key).configured === true ? <Button tone="danger" onPress={() => { setClearAiSecrets((value) => value.includes('openai_compatible') ? value.filter((item) => item !== 'openai_compatible') : [...value, 'openai_compatible']); setDirty(true); }}>{clearAiSecrets.includes('openai_compatible') ? 'Keep OpenAI key' : 'Clear OpenAI key'}</Button> : null}<Button tone="quiet" onPress={() => send({ cmd: 'ai_index_start', mode: Number(record(aiIndex.counts).indexed ?? 0) > 0 ? 'rebuild' : 'incremental', confirm: true })}>Build / rebuild index</Button></div><p className={styles.note}>Raw provider keys are write-only and never returned in snapshots or logs. Index: {text(aiIndex.status, 'disabled')} · {text(record(aiIndex.counts).indexed, '0')} indexed.</p></section>
    <section><h3>Relay connector</h3><RelayConfigurationFields view={relayView} draft={relayDraft} touched={relayTouched} onChange={changeRelay} /><div className={styles.settingsActions}><Button tone="quiet" onPress={() => send({ cmd: 'test_relay_connection' })}>Test connection</Button><Button tone="quiet" isDisabled={!pairingToken.trim()} onPress={() => { send({ cmd: 'generate_daemon_credential', pairing_token: pairingToken }); setPairingToken(''); }}>Pair daemon credential</Button><Button tone="primary" isDisabled={!relayDraft.relay_enabled} onPress={() => send({ cmd: 'generate_relay_device_link', confirm: true })}>Generate one-time device link</Button></div>{Object.keys(relayTest).length ? <p className={styles.note}>{text(relayTest.status)} · {text(relayTest.message)}</p> : null}{Object.keys(daemonCredential).length ? <p className={styles.note}>{text(daemonCredential.message, text(daemonCredential.error))}</p> : null}{deviceLink.ok === true ? <div className={styles.secretResult}><strong>Display once</strong><p>{text(deviceLink.establish_url, text(deviceLink.url))}</p><code>{text(deviceLink.code)}</code><small>Expires {text(deviceLink.expires_at, 'soon')}</small></div> : null}</section>
    <section><h3>Runtime and behavior settings</h3><p className={styles.note}>Fields retain their daemon defaults and inheritance. Group-wide defaults apply to future launches; per-agent overrides remain in Agents.</p><details><summary>Global defaults</summary><StructuredSettings providers={providers} value={record(JSON.parse(advancedGlobal))} defaults={globalDefaults} omit={[...Object.keys(globalDraft), ...Object.keys(relayDraft), ...Object.keys(aiDraft), 'default_lanes']} onChange={(next) => { setAdvancedGlobal(JSON.stringify(next)); setDirty(true); setSaved(false); }} /></details><details><summary>{group} execution, worktrees, notifications and sync</summary><StructuredSettings launchContext={launchContext} group={group} providers={providers} value={record(JSON.parse(advancedGroup))} defaults={groupDefaults} omit={Object.keys(groupDraft)} onChange={(next) => { setAdvancedGroup(JSON.stringify(next)); setDirty(true); setSaved(false); }} /><BoardSyncSettings group={group} disabled={saving} settings={{ ...record(JSON.parse(advancedGroup)), ...groupDraft }} onChange={updateGithubDraft} /></details><details><summary>Engineer behavior defaults</summary><Button tone="quiet" isDisabled={!Object.keys(engineerDefaults).length} onPress={() => resetSection('engineer')}>Reset Engineer defaults</Button><EngineerNotificationPreset value={record(JSON.parse(advancedEngineer))} disabled={saving} onApply={(preset) => { setAdvancedEngineer((previous) => JSON.stringify({ ...record(JSON.parse(previous)), ...preset })); setDirty(true); setSaved(false); }} /><StructuredSettings launchContext={launchContext} providers={providers} value={record(JSON.parse(advancedEngineer))} defaults={engineerDefaults} onChange={(next) => { setAdvancedEngineer(JSON.stringify(next)); setDirty(true); setSaved(false); }} /></details><details><summary>Architect behavior defaults</summary><Button tone="quiet" isDisabled={!Object.keys(architectDefaults).length} onPress={() => resetSection('architect')}>Reset Architect defaults</Button><StructuredSettings launchContext={launchContext} providers={providers} value={record(JSON.parse(advancedArchitect))} defaults={architectDefaults} onChange={(next) => { setAdvancedArchitect(JSON.stringify(next)); setDirty(true); setSaved(false); }} /></details><div className={styles.settingsActions}><Button tone="quiet" onPress={() => { try { send({ cmd: 'preview_system_prompt', request_id: `react-engineer-${Date.now()}`, group, kind: 'engineer', group_settings: { ...record(JSON.parse(advancedGroup)), ...groupDraft }, settings: record(JSON.parse(advancedEngineer)) }); setJsonError(''); } catch { setJsonError('Advanced settings must be valid JSON.'); } }}>Preview Engineer system prompt</Button><Button tone="quiet" onPress={() => { try { send({ cmd: 'preview_system_prompt', request_id: `react-architect-${Date.now()}`, group, kind: 'architect', group_settings: { ...record(JSON.parse(advancedGroup)), ...groupDraft }, settings: record(JSON.parse(advancedArchitect)) }); setJsonError(''); } catch { setJsonError('Advanced settings must be valid JSON.'); } }}>Preview Architect system prompt</Button></div>{text(promptPreview.prompt) ? <details className={styles.promptPreview}><summary>{text(promptPreview.kind)} system prompt · {text(record(promptPreview.metadata).provider, 'inherited provider')}</summary><pre>{text(promptPreview.prompt)}</pre></details> : null}</section>
  </fieldset></form>;
}

function SettingsWorkspace({ group, responses, send }: { group: string; responses: Record<string, unknown>; send: (command: TorqueCommand) => void }) {
  const dispatch = useAppDispatch(); const connection = useAppSelector(selectConnection);
  const [generation, setGeneration] = useState(0);
  const discard = useCallback(() => setGeneration((value) => value + 1), []);
  const [snapshot, setSnapshot] = useState<SettingsSnapshot | null>(null);
  const [error, setError] = useState(''); const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false); const busyRef = useRef(false);
  const savingChanged = useCallback((value: boolean) => { busyRef.current = value; setBusy(value); }, []);
  useEffect(() => {
    if (busy || connection.status !== 'connected') return;
    const controller = new AbortController();
    void Promise.all([{ cmd: 'get_global_settings' }, { cmd: 'get_group_settings', group }, { cmd: 'get_ai_settings' }].map((command) => readCommand(command, controller.signal))).then((frames) => {
      if (controller.signal.aborted || busyRef.current) return;
      const [global, groupFrame, ai] = frames;
      if (global?.type !== 'global_settings' || groupFrame?.type !== 'group_settings' || ai?.type !== 'ai_settings' || groupFrame.group !== group) throw new Error('Could not load matching settings. Retry the refresh.');
      frames.forEach((frame) => dispatch(projectionActions.auxiliaryResourceReceived(frame)));
      setSnapshot({ global, group: groupFrame, ai }); setError('');
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Settings unavailable'); });
    return () => controller.abort();
  }, [group, dispatch, retry, connection.status, connection.reconnectCount, busy]);
  const retryButton = <Button onPress={() => { setError(''); setRetry((value) => value + 1); }}>Retry settings</Button>;
  return snapshot ? <>{error ? <div role="alert">Settings refresh failed. Your draft is retained. {error} {retryButton}</div> : null}<SettingsPanel key={generation} onDiscard={discard} group={group} responses={responses} send={send} snapshot={snapshot} onSavingChange={savingChanged} /></> : error ? <StateSurface title="Settings unavailable" description={error} action={retryButton} /> : <StateSurface title="Loading settings" description="Loading global, group and AI defaults before editing." />;
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
  const settingsNavigation = useSettingsNavigation();
  const setTab = (next: ControlTab) => { if (next !== tab) settingsNavigation.request(() => dispatch(workspaceUiActions.setControlTab(next))); };
  const [selectedAction, setSelectedAction] = useState('');
  const [helpRefreshVersion, setHelpRefreshVersion] = useState(0);
  const [actionRefreshVersion, setActionRefreshVersion] = useState(0);
  const requestKey = useRef('');
  const [classRefreshVersion, setClassRefreshVersion] = useState(0);
  const lastFrame = connection.lastAuxiliaryFrame;
  const baseDir = text(record(groupState.settings[group]).default_directory);

  const send = useCallback((command: TorqueCommand) => { if (!sendCommand(command)) onCommandUnavailable(); }, [onCommandUnavailable, sendCommand]);
  const refresh = useCallback(() => {
    if (tab === 'help') setHelpRefreshVersion((value) => value + 1);
    if (tab === 'actions') setActionRefreshVersion((value) => value + 1);
    if (tab === 'catalog') setClassRefreshVersion((value) => value + 1);
    const requests: Partial<Record<ControlTab, TorqueCommand[]>> = {
      mission: [{ cmd: 'get_mission_control', group }],
      activity: [{ cmd: 'get_events', limit: 100 }],
    };
    (requests[tab] || []).forEach(send);
  }, [group, send, tab]);

  useEffect(() => {
    if (!group || connection.status !== 'connected') return;
    const key = `${group}:${tab}:${connection.reconnectCount}`;
    if (requestKey.current === key) return;
    requestKey.current = key;
    if (tab === 'mission') send({ cmd: 'get_mission_control', group });
    if (tab === 'activity') send({ cmd: 'get_events', limit: 100 });
  }, [group, tab, connection.status, connection.reconnectCount, send]);

  const agentItems = useMemo(() => records(agents.records).filter((item) => !group || item.group === group), [agents.records, group]);
  const agentCount = agentItems.filter((item) => item.cell_type !== 'terminal').length;
  const terminalCount = agentItems.filter((item) => item.cell_type === 'terminal').length;
  const eventItems = useMemo(() => [...operations.events].reverse().map(record), [operations.events]);
  return <section className={styles.root} aria-label="Control Center">
    <header className={styles.header}><div><p>Workspace / {group || 'No group'}</p><h1>Control Center</h1></div><span>{agentCount} {agentCount === 1 ? 'agent' : 'agents'}{terminalCount ? ` · ${terminalCount} ${terminalCount === 1 ? 'terminal' : 'terminals'}` : ''} · {eventItems.length} events</span>{['mission', 'activity', 'actions', 'catalog', 'help'].includes(tab) ? <Button tone="quiet" onPress={refresh}>Refresh section</Button> : null}</header>
    <nav className={styles.tabs} aria-label="Control Center sections">{tabs.map((item) => <button key={item.id} aria-current={tab === item.id ? 'page' : undefined} onClick={() => setTab(item.id)}>{item.label}</button>)}</nav>
    {lastFrame?.type === 'error' ? <div className={styles.error} role="alert">{text(lastFrame.message, 'Request failed.')}</div> : null}
    <div className={styles.content}>
      {tab === 'mission' ? <MissionPanel group={group} agentCount={agentCount} mission={operations.missionControl} health={operations.health} supervisor={operations.supervisor} relay={operations.relayConnection} responses={auxiliaryResponses} send={send} onOpenTask={(id) => { dispatch(workspaceUiActions.setActivePanel('board')); dispatch(workspaceUiActions.setDetailTask(id)); }} onOpenAgent={(id) => { dispatch(workspaceUiActions.setActivePanel('agents')); dispatch(workspaceUiActions.setSelectedAgent(id)); }} /> : null}
      {tab === 'chat' ? <PeerChat threads={messages.peerThreads} agents={agents.records} /> : null}
      {tab === 'pipelines' ? <PipelineExplorer group={group} onEdit={(name) => { setSelectedAction(name); setTab('actions'); }} /> : null}
      {tab === 'logs' ? <LogViewer host={host} /> : null}
      {tab === 'activity' ? <ActivityPanel events={eventItems} send={send} group={group} /> : null}
      {tab === 'history' ? <HistoryPanel key={group} group={group} send={send} /> : null}
      {tab === 'context' ? <ContextPanel key={group} group={group} agents={agentItems} onOpenTarget={(target) => { if (target.group && target.group !== group) send({ cmd: 'ui_select_group', group: target.group }); if (target.kind === 'agent') { dispatch(workspaceUiActions.setActivePanel('agents')); dispatch(workspaceUiActions.setSelectedAgent(target.id)); } else { dispatch(workspaceUiActions.setActivePanel('board')); dispatch(workspaceUiActions.setDetailTask(target.id)); } }} /> : null}
      {tab === 'actions' ? <ActionsWorkspace key={`${group}:${baseDir}`} group={group} initialName={selectedAction} refreshVersion={actionRefreshVersion} onPipelines={() => setTab('pipelines')} /> : null}
      {tab === 'catalog' ? <div className={styles.catalog}>
        <AgentClassLibrary key={baseDir || group} baseDir={baseDir} refreshVersion={classRefreshVersion} />
        <CatalogEditor key={`role:${group}:${baseDir}`} title="Roles" kind="role" group={group} refreshVersion={classRefreshVersion} onMutation={() => setClassRefreshVersion((value) => value + 1)} />
        <CatalogEditor key={`template:${group}:${baseDir}`} title="Templates" kind="template" group={group} refreshVersion={classRefreshVersion} onMutation={() => setClassRefreshVersion((value) => value + 1)} />
        <CatalogEditor key={`specialization:${group}:${baseDir}`} title="Specializations" kind="specialization" group={group} refreshVersion={classRefreshVersion} onMutation={() => setClassRefreshVersion((value) => value + 1)} />
        <BehaviorOverlayEditor group={group} active={catalog.behaviorOverlays} proposals={operations.behaviorOverlayProposals} responses={auxiliaryResponses} agents={agentItems} send={send} />
      </div> : null}
      {tab === 'settings' ? <SettingsWorkspace key={group} group={group} responses={auxiliaryResponses} send={send} /> : null}
      {tab === 'help' ? <HelpPanel refreshVersion={helpRefreshVersion} /> : null}
    </div>
  </section>;
}
