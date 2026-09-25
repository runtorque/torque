import { useRef, useState } from 'react';

import {
  appearanceDefaults,
  descriptorFromKeyboardEvent,
  effectiveBinding,
  formatBinding,
  keybindingDefaults,
  type AppearancePreferences,
  type KeybindingDescriptor,
} from '../../app/preferences';
import { reviewShortcutChange, shortcutLabels, fixedNavigators } from '../../app/shortcutBindings';
import { Button } from '../../design/primitives';
import styles from './ControlCenter.module.css';

export function AppearancePreferencesPanel({ value, onChange }: { value: AppearancePreferences; onChange: (value: AppearancePreferences) => void }) {
  const update = (patch: Partial<AppearancePreferences>) => {
    const next = { ...value, ...patch };
    onChange(next);
  };
  return <section><h3>Appearance</h3><div className={styles.formGrid}>
    <label className={styles.field}><span>Contrast</span><select value={value.contrast} onChange={(event) => update({ contrast: event.target.value as AppearancePreferences['contrast'] })}><option value="balanced">Balanced</option><option value="high">High contrast</option></select></label>
    <label className={styles.field}><span>Density</span><select value={value.density} onChange={(event) => update({ density: event.target.value as AppearancePreferences['density'] })}><option value="compact">Compact</option><option value="comfortable">Comfortable</option></select></label>
    <label className={styles.field}><span>UI scale · {value.scale}%</span><input type="range" min="85" max="125" step="5" value={value.scale} onChange={(event) => update({ scale: Number(event.target.value) })} /></label>
    <label className={styles.field}><span>Terminal font · {value.terminalFont}px</span><input type="range" min="10" max="20" value={value.terminalFont} onChange={(event) => update({ terminalFont: Number(event.target.value) })} /></label>
  </div><div className={styles.appearanceRow}><span>Accent</span>{(['blue', 'violet', 'teal', 'amber'] as const).map((accent) => <button type="button" key={accent} data-accent={accent} aria-label={`${accent} accent`} aria-pressed={value.accent === accent} onClick={() => update({ accent })} />)}<label><input type="checkbox" checked={value.reduceMotion} onChange={(event) => update({ reduceMotion: event.target.checked })} />Reduce motion</label><Button tone="quiet" onPress={() => onChange({ ...appearanceDefaults })}>Reset</Button></div><p className={styles.note}>Preview changes here, then Save changes to keep them. Appearance is local to this device and shared with the classic UI.</p></section>;
}

export function ShortcutPreferencesPanel({ settings, onChange }: { settings: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  const overrides = settings.keybindings && typeof settings.keybindings === 'object' && !Array.isArray(settings.keybindings) ? settings.keybindings as Record<string, unknown> : {};
  const [pending, setPending] = useState<ReturnType<typeof reviewShortcutChange> | null>(null);
  const [changedReview, setChangedReview] = useState(false);
  const editors = useRef<Record<string, HTMLInputElement | null>>({});
  const restore = (action: string) => { editors.current[action]?.focus(); };
  const setBinding = (action: string, binding: KeybindingDescriptor | null) => {
    const review = reviewShortcutChange(overrides, action, binding); setChangedReview(false);
    if (review.fixed || review.conflicts.length) setPending(review);
    else { setPending(null); onChange(review.next); }
  };
  const cancel = () => { if (pending) restore(pending.action); setPending(null); setChangedReview(false); };
  const confirm = () => {
    if (!pending) return;
    const latest = reviewShortcutChange(overrides, pending.action, pending.binding);
    if (latest.signature !== pending.signature) { setPending(latest); setChangedReview(true); return; }
    if (!latest.canReassign) return;
    onChange(latest.next); restore(latest.action); setPending(null); setChangedReview(false);
  };
  return <section><h3>Keyboard shortcuts</h3>{pending ? <div role="alert" className={styles.shortcutConflict}>
    {changedReview ? <p>Shortcuts changed while this review was open. Review the current assignments before continuing.</p> : null}
    <p><strong>{formatBinding(pending.binding)}</strong> {pending.fixed ? `is reserved for ${pending.fixed}, a fixed shortcut.` : pending.conflicts.length ? `is already assigned to ${pending.conflicts.map((action) => shortcutLabels[action]).join(', ')}.` : 'is now available. Cancel this review and enter it again to apply.'}</p>
    {pending.canReassign ? <><p>Assign it to {shortcutLabels[pending.action]} and move {shortcutLabels[pending.conflicts[0]!]} to {formatBinding(pending.previous)}?</p><Button tone="primary" onPress={confirm}>Reassign shortcut</Button></> : <p>Choose another shortcut.</p>}
    <Button tone="quiet" onPress={cancel}>Cancel reassignment</Button>
  </div> : null}<div className={styles.shortcutGrid}>{Object.keys(keybindingDefaults).map((action) => {
    const binding = effectiveBinding({ keybindings: overrides }, action);
    return <article key={action}><span><strong>{shortcutLabels[action]}</strong><small>{action}</small></span><input ref={(element) => { editors.current[action] = element; }} readOnly value={formatBinding(binding)} aria-label={`${shortcutLabels[action]} shortcut`} onKeyDown={(event) => {
      if (event.key === 'Tab') return;
      event.preventDefault(); event.stopPropagation();
      if (event.key === 'Escape') { cancel(); return; }
      if (event.nativeEvent.isComposing || event.repeat) return;
      const descriptor = descriptorFromKeyboardEvent(event.nativeEvent); if (descriptor) setBinding(action, descriptor);
    }} onFocus={(event) => event.currentTarget.select()} /><Button tone="quiet" onPress={() => setBinding(action, null)} isDisabled={!overrides[action]}>Reset</Button></article>;
  })}</div><p className={styles.note}>Focus a shortcut field, then press the new key combination. Tab moves to the next control; Escape cancels a conflict review. Ordinary shortcuts are ignored while editing text.</p><p className={styles.note}>{fixedNavigators.map((item) => `${item.label}: ⌘${item.key.toUpperCase()} / Ctrl+${item.key.toUpperCase()}`).join(' · ')}. Item navigation keys and / for Board search are fixed.</p></section>;
}
