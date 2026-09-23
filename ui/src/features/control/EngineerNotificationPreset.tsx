import type { UnknownRecord } from '../../protocol';
import { engineerNotificationPresets, matchNotificationPreset, notificationPresetValues, type EngineerNotificationPresetName, type EngineerNotificationValues } from './engineerNotificationPresets';
import styles from './ControlCenter.module.css';
export function EngineerNotificationPreset({ value, onApply, disabled = false }: { value: UnknownRecord; onApply: (value: EngineerNotificationValues) => void; disabled?: boolean }) {
  const selected = matchNotificationPreset(value);
  return <div className={styles.notificationPreset}><label>Engineer notification preset<select disabled={disabled} value={selected} onChange={(event) => { if (event.target.value !== 'custom') onApply(notificationPresetValues(event.target.value as EngineerNotificationPresetName)); }}><option value="custom">Custom</option>{Object.entries(engineerNotificationPresets).map(([name, preset]) => <option key={name} value={name}>{preset.label}</option>)}</select></label><p>{selected === 'custom' ? 'Custom notification settings.' : engineerNotificationPresets[selected].description} Choosing a preset replaces verbosity, delivery intervals, heartbeat and enabled events. Manual changes are retained; unmatched settings show Custom.</p></div>;
}
