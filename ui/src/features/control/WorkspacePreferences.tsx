import { useState } from 'react';

import {
  appearanceDefaults,
  descriptorFromKeyboardEvent,
  effectiveBinding,
  formatBinding,
  keybindingDefaults,
  readAppearance,
  saveAppearance,
  type AppearancePreferences,
  type KeybindingDescriptor,
} from '../../app/preferences';
import { Button } from '../../design/primitives';
import styles from './ControlCenter.module.css';

const shortcutLabels: Record<string, string> = {
  'navigator.open': 'Open command palette',
  'task.create': 'Create task',
  'composer.focus': 'Focus agent composer',
  'react.panel.board': 'Open Board',
  'react.panel.agents': 'Open Agents',
  'react.panel.planning': 'Open Planning',
  'react.panel.control': 'Open Control Center',
};

export function AppearancePreferencesPanel() {
  const [value, setValue] = useState(readAppearance);
  const update = (patch: Partial<AppearancePreferences>) => {
    const next = { ...value, ...patch };
    setValue(next);
    saveAppearance(next);
  };
  return <section><h3>Appearance</h3><div className={styles.formGrid}>
    <label className={styles.field}><span>Contrast</span><select value={value.contrast} onChange={(event) => update({ contrast: event.target.value as AppearancePreferences['contrast'] })}><option value="balanced">Balanced</option><option value="high">High contrast</option></select></label>
    <label className={styles.field}><span>Density</span><select value={value.density} onChange={(event) => update({ density: event.target.value as AppearancePreferences['density'] })}><option value="compact">Compact</option><option value="comfortable">Comfortable</option></select></label>
    <label className={styles.field}><span>UI scale · {value.scale}%</span><input type="range" min="85" max="125" step="5" value={value.scale} onChange={(event) => update({ scale: Number(event.target.value) })} /></label>
    <label className={styles.field}><span>Terminal font · {value.terminalFont}px</span><input type="range" min="10" max="20" value={value.terminalFont} onChange={(event) => update({ terminalFont: Number(event.target.value) })} /></label>
  </div><div className={styles.appearanceRow}><span>Accent</span>{(['blue', 'violet', 'teal', 'amber'] as const).map((accent) => <button key={accent} data-accent={accent} aria-label={`${accent} accent`} aria-pressed={value.accent === accent} onClick={() => update({ accent })} />)}<label><input type="checkbox" checked={value.reduceMotion} onChange={(event) => update({ reduceMotion: event.target.checked })} />Reduce motion</label><Button tone="quiet" onPress={() => { setValue(appearanceDefaults); saveAppearance(appearanceDefaults); }}>Reset</Button></div><p className={styles.note}>Appearance is local to this device and is shared with the classic UI.</p></section>;
}

export function ShortcutPreferencesPanel({ settings, onChange }: { settings: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  const overrides = settings.keybindings && typeof settings.keybindings === 'object' && !Array.isArray(settings.keybindings) ? settings.keybindings as Record<string, unknown> : {};
  const setBinding = (action: string, binding: KeybindingDescriptor | null) => {
    const next = { ...overrides };
    if (binding) next[action] = binding; else delete next[action];
    onChange(next);
  };
  return <section><h3>Keyboard shortcuts</h3><div className={styles.shortcutGrid}>{Object.keys(keybindingDefaults).map((action) => {
    const binding = effectiveBinding({ keybindings: overrides }, action);
    return <article key={action}><span><strong>{shortcutLabels[action]}</strong><small>{action}</small></span><input readOnly value={formatBinding(binding)} aria-label={`${shortcutLabels[action]} shortcut`} onKeyDown={(event) => { event.preventDefault(); const descriptor = descriptorFromKeyboardEvent(event.nativeEvent); if (descriptor) setBinding(action, descriptor); }} onFocus={(event) => event.currentTarget.select()} /><Button tone="quiet" onPress={() => setBinding(action, null)} isDisabled={!overrides[action]}>Reset</Button></article>;
  })}</div><p className={styles.note}>Focus a shortcut field, then press the new key combination. Global shortcuts are ignored while editing text.</p></section>;
}
