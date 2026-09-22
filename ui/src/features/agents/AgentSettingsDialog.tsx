import { useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type ReactNode } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { projectionActions, selectAgentsState, selectAgentSettingsDefaults, selectConnection, selectProviders } from '../../app/store';
import { Button, ModalDialog } from '../../design/primitives';
import type { TorqueCommand } from '../../protocol';
import { readCommand } from '../../protocol/http';
import { providerChoices } from '../control/providerChoices';
import { toAgentViewModel, type AgentViewModel } from './model';
import { acceptSettings, createSettingsEditor, digestSettingFields, editSetting, engineerSettingFields, identityValues, inheritSetting, principalSettingFields, record as asRecord, refreshSettings, settingText as text, settingsCommands, settingsDirty, settingsRefreshValues, settingsValues, validateSettingsFrame } from './agentSettingsModel';
import styles from './AgentWorkspace.module.css';

export function AgentSettingsDialog({ target, onClose }: { target: AgentViewModel; onClose: () => void }) {
  const dispatch = useAppDispatch();
  const sources = useAppSelector(selectAgentsState);
  const defaults = useAppSelector(selectAgentSettingsDefaults);
  const connection = useAppSelector(selectConnection);
  const providers = useAppSelector(selectProviders);
  const agent = useMemo(() => sources.records[target.id] ? toAgentViewModel(target.id, sources.records[target.id]) : target, [sources.records, target]);
  const principal = ['engineer', 'architect'].includes(agent.kind);
  const [editor, setEditor] = useState(() => createSettingsEditor(agent, sources.settings[agent.id], sources.digestSettings[agent.id], sources.resolvedSettings[agent.id]));
  const [saving, setSaving] = useState(false); const savingRef = useRef(false);
  const [hydrated, setHydrated] = useState(!principal);
  const [readStatus, setReadStatus] = useState({ key: '', error: '' }); const [retry, setRetry] = useState(0);
  const [saveError, setSaveError] = useState(''); const saveErrorElement = useRef<HTMLParagraphElement>(null);
  const nameInput = useRef<HTMLInputElement>(null); const [discard, setDiscard] = useState(false);
  const instance = useId();
  const identityKey = JSON.stringify(identityValues(agent));
  const defaultsKey = JSON.stringify([agent.group, defaults.groups[agent.group], defaults.engineers[agent.group], defaults.architects[agent.group]].map((value) => typeof value === 'string' ? value : settingsRefreshValues(value)));
  const sourceKey = JSON.stringify([sources.settings[agent.id], sources.digestSettings[agent.id], sources.resolvedSettings[agent.id]].map(settingsRefreshValues));
  const [previousIdentity, setPreviousIdentity] = useState(identityKey);
  if (previousIdentity !== identityKey) {
    setPreviousIdentity(identityKey);
    setEditor((current) => refreshSettings(current, JSON.parse(identityKey) as Record<string, string>));
  }
  const requestKey = JSON.stringify([agent.id, connection.status, connection.reconnectCount, defaultsKey, sourceKey, retry, saving]);
  const loading = principal && !saving && connection.status === 'connected' && readStatus.key !== requestKey;
  const loadError = readStatus.key === requestKey ? readStatus.error : '';
  useEffect(() => {
    if (!principal || saving || connection.status !== 'connected') return;
    const controller = new AbortController();
    void readCommand({ cmd: 'get_agent_settings', agent_id: agent.id }, controller.signal).then((frame) => {
      if (controller.signal.aborted || savingRef.current) return;
      validateSettingsFrame(frame, agent.id);
      const metadata = asRecord(frame.resolved);
      setEditor((current) => refreshSettings(current, settingsValues(frame.settings, {}, metadata), metadata));
      setHydrated(true); setReadStatus({ key: requestKey, error: '' });
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setReadStatus({ key: requestKey, error: cause instanceof Error ? cause.message : 'Could not refresh agent settings.' });
    });
    return () => controller.abort();
  }, [agent.id, principal, connection.status, requestKey, saving]);
  useEffect(() => { if (saveError && saveError !== 'Name is required.' && !saving) saveErrorElement.current?.focus(); }, [saveError, saving]);
  const dirty = settingsDirty(editor);
  const requestClose = () => { if (savingRef.current) return; if (dirty) setDiscard(true); else onClose(); };
  const values = editor.draft; const digestValues = editor.draft;
  const name = values.name ?? ''; const icon = values.icon ?? ''; const tabColor = values.tab_color ?? '';
  const specializations = values.engineer_specializations ?? ''; const relaunch = editor.relaunch;
  const updateValue = (key: string, value: string) => setEditor((current) => editSetting(current, key, value));
  const setName = (value: string) => updateValue('name', value); const setIcon = (value: string) => updateValue('icon', value); const setTabColor = (value: string) => updateValue('tab_color', value);
  const setSpecializations = (value: string) => updateValue('engineer_specializations', value);
  const setRelaunch = (value: boolean) => setEditor((current) => ({ ...current, relaunch: value }));
  const resetToInherited = (key: string) => setEditor((current) => inheritSetting(current, key));
  const origin = (key: string) => text(asRecord(editor.resolved[key]).origin) || 'inherit';
  const originControl = (key: string) => <span className={styles.settingOrigin}><span>{editor.intents[key] === 'inherit' ? 'inherited (unsaved)' : editor.intents[key] ? 'per-agent (unsaved)' : origin(key)}</span>{(origin(key) === 'per-agent' || editor.intents[key] === 'set') && editor.intents[key] !== 'inherit' ? <button type="button" onClick={() => resetToInherited(key)}>Use inherited</button> : null}</span>;
  const control = (key: string, label: string, type: string, digest = false) => {
    const value = (digest ? digestValues : values)[key] ?? '';
    const common = { id: `${instance}-${key}`, 'aria-label': label, value, onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => updateValue(key, event.target.value) };
    const suggestions = providerChoices(providers, key, values);
    const choicesId = `${instance}-${key}-choices`;
    const options = key === 'autonomy_mode' ? (agent.kind === 'architect' ? ['dispatch_freely', 'dispatch_after_confirm', 'ask_always'] : ['suggest_only', 'dispatch_when_clear', 'aggressive_auto_continue']) : key === 'wave_size_preference' ? ['small', 'balanced', 'large'] : key === 'same_agent_follow_up_preference' ? ['balanced', 'prefer_same_agent', 'prefer_fresh_agent'] : key === 'escalation_style' ? ['ask_early', 'note_then_ask', 'keep_moving'] : key === 'digest_verbosity' ? (agent.kind === 'architect' ? ['terse', 'balanced', 'verbose'] : ['compact', 'balanced', 'detailed']) : null;
    let input: ReactNode;
    if (type === 'textarea' || type === 'list') input = <textarea {...common} rows={type === 'list' ? 3 : 7} />;
    else if (type === 'number') input = <input {...common} type="number" placeholder="inherit" />;
    else if (type === 'boolean') input = <select {...common}><option value="">Inherited</option><option value="true">Allowed</option><option value="false">Not allowed</option></select>;
    else if (type === 'paused') input = <select {...common}><option value="">Inherited</option><option value="false">Enabled</option><option value="true">Paused</option></select>;
    else if (type === 'fast') input = <select {...common}><option value="">Inherited</option><option value="on">Fast on</option><option value="off">Fast off</option></select>;
    else if (options) input = <select {...common}><option value="">Inherited</option>{[...new Set([...options, ...(value ? [value] : [])])].map((option) => <option key={option} value={option}>{option.replaceAll('_', ' ')}</option>)}</select>;
    else input = <input {...common} list={suggestions.length ? choicesId : undefined} placeholder="inherit" />;
    return <div className={styles.settingField} key={key}><label htmlFor={`${instance}-${key}`}>{label}</label>{originControl(key)}{input}{suggestions.length ? <datalist id={choicesId}>{suggestions.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}</datalist> : null}</div>;
  };

  const save = () => {
    if (savingRef.current || loading || !hydrated || !dirty) return;
    if (!name.trim()) { setSaveError('Name is required.'); nameInput.current?.focus(); return; }
    let commands: TorqueCommand[];
    try { commands = settingsCommands(editor, agent); }
    catch (cause) { setSaveError(cause instanceof Error ? cause.message : 'Check the settings values.'); return; }
    if (!commands.length) return;
    savingRef.current = true; setSaving(true); setSaveError('');
    void (async () => {
      let saved = false;
      try {
        for (const command of commands) {
          const frame = await readCommand(command, new AbortController().signal);
          if (frame.type === 'error') throw new Error(text(frame.message) || 'Could not save settings.');
          if (command.cmd === 'rename_engineer') {
            if (frame.id !== agent.id || frame.name !== command.new_name || frame.kind !== 'engineer') throw new Error('Could not confirm the Engineer rename. Your changes are retained.');
            setEditor((current) => acceptSettings(current, { name: text(frame.name) }, ['name']));
          } else if (command.cmd === 'update_agent') {
            const identity = Object.fromEntries(['name', 'icon', 'tab_color'].filter((key) => key in command).map((key) => [key, text(command[key])]));
            setEditor((current) => acceptSettings(current, identity, Object.keys(identity)));
          } else if (command.cmd === 'update_agent_settings' || command.cmd === 'update_agent_digest_settings') {
            validateSettingsFrame(frame, agent.id); const metadata = asRecord(frame.resolved);
            setEditor((current) => acceptSettings(current, settingsValues(frame.settings, {}, metadata), Object.keys(asRecord(command.settings)), metadata));
          } else if (command.cmd === 'set_engineer_specializations') {
            if (frame.type !== 'engineer_specializations' || frame.engineer_id !== agent.id || !Array.isArray(frame.specializations)) throw new Error('Could not confirm the specialization change.');
            setEditor((current) => acceptSettings(current, { engineer_specializations: text(frame.specializations) }, ['engineer_specializations']));
          } else if (command.cmd === 'relaunch_agent') setEditor((current) => ({ ...current, relaunch: false }));
          saved = true;
          if (frame.type !== 'state') dispatch(projectionActions.auxiliaryResourceReceived(frame));
        }
        onClose();
      } catch (cause) { setSaveError(`${saved ? 'Some changes were saved. ' : ''}${cause instanceof Error ? cause.message : 'Could not save settings. Your changes are retained.'}`); }
      finally { savingRef.current = false; setSaving(false); }
    })();
  };
  return <>
    <ModalDialog title="Agent settings" description={`${agent.kind} · ${agent.id}`} size="large" isOpen onOpenChange={(open) => { if (!open) requestClose(); }}>
    <div className={styles.settingsStatus}><span aria-live="polite">{loading ? 'Refreshing agent settings…' : dirty ? 'Unsaved changes' : 'No unsaved changes'}</span>{principal ? <Button tone="quiet" isDisabled={saving || loading} onPress={() => setRetry((value) => value + 1)}>Refresh settings</Button> : null}</div>
    {loadError ? <div role="alert">Settings refresh failed: {loadError} <Button tone="quiet" isDisabled={saving || loading} onPress={() => setRetry((value) => value + 1)}>Retry settings</Button></div> : null}
    <form className={styles.settingsForm} onSubmit={(event) => { event.preventDefault(); save(); }}>
      {saveError ? <p role="alert" ref={saveErrorElement} tabIndex={-1}>{saveError}</p> : null}
      <fieldset disabled={saving} style={{ border: 0, margin: 0, padding: 0 }}>
      <section className={styles.settingsSection}><h3>Identity</h3><div className={styles.formGrid}><label>Name<input ref={nameInput} aria-invalid={saveError === 'Name is required.'} value={name} onChange={(event) => setName(event.target.value)} required /></label><label>Icon<input value={icon} onChange={(event) => setIcon(event.target.value)} placeholder="optional icon" /></label><label>Tab color<input value={tabColor} onChange={(event) => setTabColor(event.target.value)} placeholder="#6172f3" /></label></div></section>
      {['architect', 'engineer'].includes(agent.kind) ? <>
        <section className={styles.settingsSection}><h3>Launch and behavior</h3><div className={styles.formGrid}>{principalSettingFields.map(([key, label, type]) => control(key, label, type))}{agent.kind === 'engineer' ? engineerSettingFields.map(([key, label, type]) => control(key, label, type)) : null}</div></section>
        {agent.kind === 'engineer' ? <section className={styles.settingsSection}><h3>Specializations</h3><label>Ordered specialization slugs<textarea value={specializations} onChange={(event) => setSpecializations(event.target.value)} rows={3} placeholder="ui-ux, frontend" /></label></section> : null}
        <section className={styles.settingsSection}><h3>Digest delivery</h3><div className={styles.formGrid}>{digestSettingFields.map(([key, label, type]) => control(key, label, type, true))}</div></section>
        <label className={styles.inlineCheck}><input type="checkbox" checked={relaunch} onChange={(event) => setRelaunch(event.target.checked)} />Relaunch after saving launch-bound changes</label>
      </> : <p className={styles.formHint}>Worker provider and launch settings are inherited from its role, Agent Class, and group defaults.</p>}
      <footer><Button tone="quiet" type="button" isDisabled={saving} onPress={requestClose}>Cancel</Button><Button tone="primary" type="submit" isDisabled={saving || loading || !hydrated || !dirty}>{saving ? 'Saving settings…' : 'Save settings'}</Button></footer>
      </fieldset>
    </form>
    </ModalDialog>
    <ModalDialog title="Discard agent settings changes?" isOpen={discard} onOpenChange={setDiscard} size="small"><p>Discard the unsaved edits in this agent's settings?</p><footer><Button tone="quiet" onPress={() => setDiscard(false)}>Keep editing</Button><Button tone="danger" onPress={onClose}>Discard changes</Button></footer></ModalDialog>
  </>;
}
