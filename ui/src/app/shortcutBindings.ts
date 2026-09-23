import { effectiveBinding, keybindingDefaults, type KeybindingDescriptor } from './preferences';

export const shortcutLabels: Record<string, string> = {
  'navigator.open': 'Open command palette', 'task.create': 'Create task', 'composer.focus': 'Focus agent composer',
  'panel.toggle': 'Open Board (alternate)', 'react.panel.board': 'Open Board', 'react.panel.agents': 'Open Agents', 'react.panel.planning': 'Open Planning', 'react.panel.control': 'Open Control Center',
};
export type NavigatorScope = 'all' | 'groups' | 'panels';
export const fixedNavigators = [
  { scope: 'groups', key: 'g', label: 'Open group navigator' },
  { scope: 'panels', key: 'p', label: 'Open panel navigator' },
] as const;
export function fixedNavigatorScope(event: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey'>): NavigatorScope | null {
  if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return null;
  return fixedNavigators.find((item) => item.key === event.key.toLocaleLowerCase())?.scope ?? null;
}
function fingerprint(binding: KeybindingDescriptor): string {
  return JSON.stringify([binding.key.toLocaleLowerCase(), binding.ctrl, binding.meta, binding.alt, binding.shift]);
}
function variants(action: string, binding: KeybindingDescriptor): string[] {
  return (action === 'navigator.open' && binding.meta && !binding.ctrl
    ? [binding, { ...binding, meta: false, ctrl: true }, { ...binding, ctrl: true }]
    : [binding]).map(fingerprint);
}
function reserved(binding: KeybindingDescriptor): string {
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter', 'Escape', 'Delete', 'Backspace', 'Home', 'End', 'Tab', 'Space'].includes(binding.key)) return 'Keyboard focus and item navigation';
  if (binding.key === '/') return 'Search Board';
  const scope = fixedNavigatorScope({ key: binding.key, ctrlKey: binding.ctrl, metaKey: binding.meta, altKey: binding.alt, shiftKey: binding.shift });
  return fixedNavigators.find((item) => item.scope === scope)?.label ?? '';
}
function collisions(overrides: Record<string, unknown>, action: string, binding: KeybindingDescriptor): string[] {
  const requested = variants(action, binding);
  return Object.keys(keybindingDefaults).filter((other) => {
    if (other === action) return false;
    const effective = effectiveBinding({ keybindings: overrides }, other);
    return effective && variants(other, effective).some((value) => requested.includes(value));
  });
}
function assign(overrides: Record<string, unknown>, action: string, binding: KeybindingDescriptor) {
  if (keybindingDefaults[action] && fingerprint(binding) === fingerprint(keybindingDefaults[action])) delete overrides[action];
  else overrides[action] = binding;
}
export function reviewShortcutChange(overrides: Record<string, unknown>, action: string, requested: KeybindingDescriptor | null) {
  const binding = requested ?? keybindingDefaults[action]!;
  const previous = effectiveBinding({ keybindings: overrides }, action)!;
  const conflicts = collisions(overrides, action, binding);
  const fixed = reserved(binding);
  const next = { ...overrides }; assign(next, action, binding);
  const other = conflicts[0];
  if (other && conflicts.length === 1) assign(next, other, previous);
  const canReassign = !fixed && conflicts.length === 1 && !reserved(previous) && Boolean(other) && collisions(next, other!, previous).length === 0;
  return { action, binding, previous, conflicts, fixed, canReassign, next, signature: JSON.stringify([action, binding, previous, conflicts, fixed, next]) };
}
