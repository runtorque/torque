export interface KeybindingDescriptor {
  key: string;
  ctrl: boolean;
  meta: boolean;
  alt: boolean;
  shift: boolean;
}

export interface AppearancePreferences {
  contrast: 'balanced' | 'high';
  accent: 'blue' | 'violet' | 'teal' | 'amber';
  scale: number;
  terminalFont: number;
  density: 'compact' | 'comfortable';
  reduceMotion: boolean;
}

export const appearanceDefaults: AppearancePreferences = {
  contrast: 'balanced',
  accent: 'blue',
  scale: 100,
  terminalFont: 12,
  density: 'compact',
  reduceMotion: false,
};

export const keybindingDefaults: Record<string, KeybindingDescriptor> = {
  'navigator.open': { key: 'k', ctrl: false, meta: true, alt: false, shift: false },
  'task.create': { key: 'n', ctrl: false, meta: false, alt: false, shift: false },
  'composer.focus': { key: 'c', ctrl: false, meta: false, alt: false, shift: false },
  'react.panel.board': { key: 'b', ctrl: false, meta: false, alt: false, shift: false },
  'react.panel.agents': { key: 'a', ctrl: false, meta: false, alt: false, shift: false },
  'react.panel.planning': { key: 'p', ctrl: false, meta: false, alt: false, shift: false },
  'react.panel.control': { key: 'o', ctrl: false, meta: false, alt: false, shift: false },
};

const appearanceKey = 'torque.appearance.v1';

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function readAppearance(storage: Pick<Storage, 'getItem'> | null = typeof localStorage === 'undefined' ? null : localStorage): AppearancePreferences {
  try {
    const stored = record(JSON.parse(storage?.getItem(appearanceKey) ?? '{}'));
    return {
      contrast: stored.contrast === 'high' ? 'high' : 'balanced',
      accent: ['blue', 'violet', 'teal', 'amber'].includes(String(stored.accent)) ? stored.accent as AppearancePreferences['accent'] : 'blue',
      scale: Math.max(85, Math.min(125, Number(stored.scale ?? 100))),
      terminalFont: Math.max(10, Math.min(20, Number(stored.terminalFont ?? 12))),
      density: stored.density === 'comfortable' ? 'comfortable' : 'compact',
      reduceMotion: stored.reduceMotion === true,
    };
  } catch {
    return { ...appearanceDefaults };
  }
}

export function applyAppearance(value: AppearancePreferences, root: HTMLElement | null = typeof document === 'undefined' ? null : document.documentElement): void {
  if (!root) return;
  const accents = { blue: '#8da2fb', violet: '#a78bfa', teal: '#2dd4bf', amber: '#f0ad39' };
  root.dataset.torqueContrast = value.contrast;
  root.dataset.torqueDensity = value.density;
  root.dataset.torqueReduceMotion = value.reduceMotion ? 'true' : 'false';
  root.style.setProperty('--accent', accents[value.accent]);
  root.style.setProperty('--ui-scale', String(value.scale / 100));
  root.style.setProperty('--ui-font-size', `${13 * value.scale / 100}px`);
  root.style.setProperty('--terminal-font-size', `${value.terminalFont}px`);
}

export function saveAppearance(value: AppearancePreferences, storage: Pick<Storage, 'setItem'> | null = typeof localStorage === 'undefined' ? null : localStorage): void {
  try { storage?.setItem(appearanceKey, JSON.stringify(value)); } catch { /* client-local preference is best effort */ }
  applyAppearance(value);
}

export function descriptorFromKeyboardEvent(event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>): KeybindingDescriptor | null {
  if (['Meta', 'Control', 'Alt', 'Shift'].includes(event.key)) return null;
  return {
    key: event.key === ' ' ? 'Space' : event.key.length === 1 ? event.key.toLocaleLowerCase() : event.key,
    ctrl: event.ctrlKey,
    meta: event.metaKey,
    alt: event.altKey,
    shift: event.shiftKey,
  };
}

export function effectiveBinding(settings: unknown, action: string): KeybindingDescriptor | null {
  const candidate = record(record(settings).keybindings)[action];
  const item = record(candidate);
  if (typeof item.key === 'string' && item.key) {
    return { key: item.key, ctrl: item.ctrl === true, meta: item.meta === true, alt: item.alt === true, shift: item.shift === true };
  }
  return keybindingDefaults[action] ?? null;
}

export function eventMatchesBinding(event: KeyboardEvent, binding: KeybindingDescriptor | null, allowPlatformModifier = false): boolean {
  if (!binding) return false;
  const key = event.key === ' ' ? 'Space' : event.key;
  const modifierMatches = allowPlatformModifier && binding.meta
    ? (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey
    : event.ctrlKey === binding.ctrl && event.metaKey === binding.meta && event.altKey === binding.alt && event.shiftKey === binding.shift;
  return modifierMatches && key.toLocaleLowerCase() === binding.key.toLocaleLowerCase();
}

export function formatBinding(binding: KeybindingDescriptor | null): string {
  if (!binding) return 'Unassigned';
  return [binding.ctrl ? 'Ctrl' : '', binding.meta ? '⌘' : '', binding.alt ? '⌥' : '', binding.shift ? '⇧' : '', binding.key.length === 1 ? binding.key.toUpperCase() : binding.key]
    .filter(Boolean).join('+').replace('⌘+', '⌘').replace('⌥+', '⌥').replace('⇧+', '⇧');
}
